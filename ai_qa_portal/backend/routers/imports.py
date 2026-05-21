"""CSV / Excel test-case import router.

Two-step wizard:

  1. ``POST /api/imports/test-cases/parse``  (multipart)
     Persists the upload to ``{data_dir}/imports/{batch_id}{ext}``,
     creates an ``ImportBatch`` row in ``pending`` status, returns a
     preview (first 50 rows) + suggested column mapping the wizard
     can pre-fill its <select> dropdowns from.

  2. ``POST /api/imports/test-cases/commit``
     Re-reads the file using the user-confirmed mapping, validates
     each row, applies the chosen duplicate strategy, and creates
     TestCases under the target story (or per-row stories when the
     source has a 'story_id' column). Reports per-row outcomes.

Plus three management endpoints:

  - ``GET  /api/imports?project_slug=...``      list past batches
  - ``GET  /api/imports/{batch_id}``            single batch + errors
  - ``POST /api/imports/{batch_id}/rollback``   admin / project lead

This module reuses the existing primitives intentionally: project
access checks via ``assert_project_role_at_least``, audit logging via
``log_action``, JSON-store TC persistence via ``_store.save_test_case``
so per-story + per-project + per-tag indexes stay consistent, and the
hard-delete primitive ``_store.hard_delete_test_case`` for rollback.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.audit import log_action
from ai_qa_portal.backend.services.auth import (
    assert_project_role_at_least,
    get_current_user,
)
from ai_qa_portal.backend.services.db import ProjectRole, User, get_db
from ai_qa_portal.backend.services.db_models.imports import ImportBatch
from ai_qa_portal.backend.services.import_dedupe import (
    DuplicateStrategy,
    find_duplicate,
)
from ai_qa_portal.backend.services.import_mapping import (
    CANONICAL_FIELD_NAMES,
    MappedRow,
    normalise_row,
    suggest_mapping,
)
from ai_qa_portal.backend.services.import_parser import (
    ImportParseError,
    ImportTooLargeError,
    detect_kind,
    parse_bytes,
)
from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

from ..models.test_case import TestCase, TestCaseStatus
from ..models.user_story import UserStory

logger = logging.getLogger("ai_qa_portal.imports")

_store = JsonFileBackend(settings.data_dir)

router = APIRouter(
    prefix="/api/imports",
    tags=["imports"],
    dependencies=[Depends(get_current_user)],
)


# Hard caps -- match the parser module so the user gets the same number
# everywhere. The commit cap is tighter than parse because committing
# 10k rows in one HTTP request is asking for timeouts and partial
# writes; large files should be split client-side.
COMMIT_MAX_ROWS = int(os.environ.get("IMPORT_COMMIT_MAX_ROWS", "1000"))
COMMIT_CHUNK_SIZE = int(os.environ.get("IMPORT_COMMIT_CHUNK", "100"))


# ---------- Pydantic IO shapes -----------------------------------


class _ParseResponse(BaseModel):
    batch_id: str
    source_filename: str
    source_kind: str
    columns: list[str]
    row_count: int
    sheet_name: str | None = None
    encoding: str | None = None
    suggested_mapping: dict[str, str | None]
    preview: list[dict[str, str]]
    canonical_fields: list[str]


class _CommitTarget(BaseModel):
    project_id: str
    sprint_id: str | None = None
    # When story_id is null, the commit handler MUST find a 'story_id'
    # column in the user mapping. Otherwise it 422s before doing any
    # writes -- we never silently drop rows without a parent story.
    story_id: str | None = None


class _CommitRequest(BaseModel):
    batch_id: str
    mapping: dict[str, str | None]
    target: _CommitTarget
    duplicate_strategy: DuplicateStrategy = "skip"
    default_status: Literal["draft", "approved"] = "draft"


class _CommitFailedRow(BaseModel):
    row_index: int
    title: str | None = None
    message: str


class _CommitResponse(BaseModel):
    batch_id: str
    status: str  # committed | partial | failed
    imported_count: int
    skipped_count: int
    failed_count: int
    created_test_case_ids: list[str]
    failed_rows: list[_CommitFailedRow]
    duration_ms: int


# ---------- Helpers ----------------------------------------------


def _imports_dir() -> Path:
    """Where temp uploads live. Created on first use."""
    p = Path(settings.data_dir) / "imports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_batch_or_404(db: Session, batch_id: str, *, user: User) -> ImportBatch:
    row = db.query(ImportBatch).filter(ImportBatch.id == batch_id).one_or_none()
    if row is None:
        raise HTTPException(404, f"Import batch {batch_id} not found")
    # Project access is the canonical gate -- a user with member role
    # on the project can see every import batch under it, regardless of
    # who created it. We mirror this in /list below.
    assert_project_role_at_least(db, user, row.project_slug, ProjectRole.member)
    return row


def _ensure_story_in_project(story_id: UUID, project_id: UUID) -> None:
    """Reused by both single-target and per-row paths."""
    try:
        story_row = _store.get_user_story(story_id)
    except KeyError as exc:
        raise HTTPException(404, f"Story {story_id} not found") from exc
    story = UserStory.model_validate(story_row)
    if str(story.project_id) != str(project_id):
        raise HTTPException(
            400,
            f"Story {story_id} belongs to a different project than the import target.",
        )


# ---------- /parse -----------------------------------------------


@router.post("/test-cases/parse", response_model=_ParseResponse)
async def parse_test_case_import(
    project_slug: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 1 of the import wizard. Reads the file in memory, persists
    it to disk under {data_dir}/imports/{batch_id}{ext}, creates an
    ImportBatch row in 'pending', and returns a preview + suggested
    column mapping.

    The wizard then lets the user override the mapping, pick a target
    story, choose a duplicate strategy, and call /commit with the
    same batch_id.
    """
    assert_project_role_at_least(db, current_user, project_slug, ProjectRole.member)

    blob = await file.read()
    filename = file.filename or "upload"

    try:
        parsed = parse_bytes(filename, blob)
    except ImportTooLargeError as exc:
        raise HTTPException(413, str(exc)) from exc
    except ImportParseError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Persist the upload so /commit can re-read it without the client
    # having to re-upload. Use the batch_id as the filename root so
    # collisions are impossible.
    batch_id = str(uuid4())
    suffix = Path(filename).suffix or ".csv"
    on_disk = _imports_dir() / f"{batch_id}{suffix}"
    try:
        on_disk.write_bytes(blob)
    except OSError as exc:
        raise HTTPException(
            500,
            f"Could not persist upload to {on_disk}: {exc}. Check data dir permissions.",
        ) from exc

    suggested = suggest_mapping(parsed.columns)
    preview = parsed.rows[:50]

    row = ImportBatch(
        id=batch_id,
        project_slug=project_slug,
        user_id=current_user.id,
        source_filename=filename,
        source_kind=detect_kind(filename),
        source_path=str(on_disk),
        target_project_id="",  # filled by /commit
        target_sprint_id=None,
        target_story_id=None,
        mapping_json=dict(suggested),
        duplicate_strategy="skip",
        total_rows=parsed.row_count,
        status="pending",
    )
    db.add(row)
    db.commit()

    log_action(
        db,
        user=current_user,
        action="tc_import_parsed",
        target_type="import_batch",
        target_id=batch_id,
        metadata={
            "project_slug": project_slug,
            "filename": filename,
            "row_count": parsed.row_count,
            "kind": row.source_kind,
        },
    )

    return _ParseResponse(
        batch_id=batch_id,
        source_filename=filename,
        source_kind=row.source_kind,
        columns=parsed.columns,
        row_count=parsed.row_count,
        sheet_name=parsed.sheet_name,
        encoding=parsed.encoding,
        suggested_mapping=suggested,
        preview=preview,
        canonical_fields=list(CANONICAL_FIELD_NAMES),
    )


# ---------- /commit ----------------------------------------------


def _materialise_test_case(
    *,
    mapped: MappedRow,
    project_id: UUID,
    story_id: UUID,
    batch_id: str,
    default_status: str,
    duplicate_strategy: DuplicateStrategy,
) -> tuple[TestCase | None, str | None, str | None]:
    """Persist a single MappedRow. Returns (test_case, action, error)
    where:
      - test_case: the saved TestCase row (None when skipped / failed)
      - action: 'created' | 'overwritten' | 'skipped' | 'failed'
      - error:   message when action == 'failed'
    """
    if not mapped.title:
        return None, "failed", "Missing required field: title."

    verdict = find_duplicate(
        external_id=mapped.external_id,
        title=mapped.title,
        target_story_id=str(story_id),
        target_project_id=str(project_id),
    )

    # Resolve final status. The default lives on the request body so
    # operators can choose whether imports land as draft (review
    # required) or approved (skip review for trusted sources).
    status_str = (mapped.status or default_status or "draft").lower()
    if status_str not in {s.value for s in TestCaseStatus}:
        status_str = default_status

    if verdict.is_duplicate:
        if duplicate_strategy == "skip":
            return None, "skipped", None
        if duplicate_strategy == "overwrite" and verdict.existing_id:
            try:
                row = _store.get_test_case(UUID(verdict.existing_id))
            except KeyError:
                return None, "failed", "Duplicate target vanished mid-commit."
            row.update(
                title=mapped.title,
                description=mapped.description,
                steps=mapped.steps or row.get("steps") or [],
                expected_result=mapped.expected_result or row.get("expected_result", ""),
                preconditions=(
                    mapped.preconditions if mapped.preconditions is not None
                    else row.get("preconditions")
                ),
                tags=mapped.tags or row.get("tags") or [],
                status=status_str,
                stale=False,
                # Clear any built script so the user re-builds against the
                # new content. Skipping this would let stale .robot files
                # silently linger.
                script_path=None,
                script_built_at=None,
                external_id=mapped.external_id or row.get("external_id"),
                external_source=row.get("external_source") or _external_source_for(batch_id),
                external_payload=mapped.raw,
                last_synced_at=datetime.now(UTC).isoformat(),
            )
            _store.save_test_case(row)
            return TestCase.model_validate(row), "overwritten", None
        # create_new: fall through, append a disambiguator
        title = f"{mapped.title} (import {batch_id[:8]})"
    else:
        title = mapped.title

    now = datetime.now(UTC)
    tc = TestCase(
        id=uuid4(),
        user_story_id=story_id,
        project_id=project_id,
        title=title,
        steps=mapped.steps,
        expected_result=mapped.expected_result,
        preconditions=mapped.preconditions,
        status=TestCaseStatus(status_str),
        stale=False,
        tags=mapped.tags,
        created_at=now,
        # External tracking: use the row's external_id when present
        # (Jira/Zephyr/etc.); otherwise tag with batch + row index so
        # rollback can find every row this import created.
        external_id=mapped.external_id or f"{batch_id}:{mapped.row_index}",
        external_source=_external_source_for(batch_id, mapped.external_id),
        external_payload=mapped.raw,
        last_synced_at=now,
    )
    _store.save_test_case(tc.model_dump(mode="json"))
    return tc, "created", None


def _external_source_for(batch_id: str, supplied_external_id: str | None = None) -> str:
    """The TC's external_source field. When the row carried its own
    external id we tag it as ``csv_import:<batch_id>`` so the source
    of truth is still identifiable; otherwise the row is purely a
    portal-native import."""
    return f"csv_import:{batch_id}" if supplied_external_id else f"csv_import:{batch_id}"


def _resolve_story_id_for_row(
    mapped: MappedRow,
    *,
    target: _CommitTarget,
) -> UUID | None:
    """Choose the story this row's TC will live under.

    Priority:
      1. target.story_id (single-story import).
      2. mapped.story_id (per-row import; column was mapped to story_id).
    Returns None when neither is set; the caller will mark the row
    failed with a clear message.
    """
    if target.story_id:
        try:
            return UUID(target.story_id)
        except (TypeError, ValueError):
            return None
    if mapped.story_id:
        try:
            return UUID(str(mapped.story_id))
        except (TypeError, ValueError):
            return None
    return None


@router.post("/test-cases/commit", response_model=_CommitResponse)
def commit_test_case_import(
    body: _CommitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 2: persist the parsed rows as TestCases."""
    t0 = time.monotonic()
    batch = _load_batch_or_404(db, body.batch_id, user=current_user)
    if batch.status not in ("pending", "partial", "failed"):
        # 'committed' batches cannot be re-committed (idempotency
        # would require revisiting the dedupe semantics). Reset by
        # re-parsing the file.
        raise HTTPException(
            409,
            f"Import batch {batch.id} status={batch.status}; cannot commit again.",
        )

    if not batch.source_path or not Path(batch.source_path).is_file():
        raise HTTPException(
            410,
            "The uploaded file is no longer on disk. Re-parse the file and retry.",
        )
    if batch.total_rows > COMMIT_MAX_ROWS:
        raise HTTPException(
            413,
            f"Commit is capped at {COMMIT_MAX_ROWS:,} rows; this batch has "
            f"{batch.total_rows:,}. Split the file and re-upload.",
        )

    try:
        project_id = UUID(body.target.project_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "target.project_id must be a UUID") from exc

    has_per_row_story_mapping = "story_id" in (body.mapping.values())
    if not body.target.story_id and not has_per_row_story_mapping:
        raise HTTPException(
            422,
            "Either target.story_id must be set OR the mapping must include "
            "a story_id column (per-row story import).",
        )

    if body.target.story_id:
        try:
            _ensure_story_in_project(UUID(body.target.story_id), project_id)
        except HTTPException:
            raise

    # Re-read the file. We use the same parser that ran in /parse so
    # encoding + row caps stay consistent.
    blob = Path(batch.source_path).read_bytes()
    try:
        parsed = parse_bytes(batch.source_filename, blob)
    except ImportParseError as exc:
        batch.status = "failed"
        batch.error_message = str(exc)
        db.commit()
        raise HTTPException(400, str(exc)) from exc

    created: list[str] = []
    skipped = 0
    failed_rows: list[_CommitFailedRow] = []
    # Track stories we touched so the response can include them.
    touched_story_ids: set[str] = set()

    # Process in chunks; each chunk is one JSON-store write loop. We
    # do NOT wrap commits in a single SQL transaction here because the
    # TC store is JSON-file backed -- commit cadence is per-row by
    # design. The ImportBatch row is updated once at the end.
    for chunk_start in range(0, len(parsed.rows), COMMIT_CHUNK_SIZE):
        chunk = parsed.rows[chunk_start : chunk_start + COMMIT_CHUNK_SIZE]
        for offset, raw in enumerate(chunk):
            idx = chunk_start + offset
            try:
                mapped = normalise_row(idx, raw, body.mapping)
            except Exception as exc:  # noqa: BLE001 -- per-row resilience
                failed_rows.append(
                    _CommitFailedRow(row_index=idx, message=f"map error: {exc}"),
                )
                continue

            story_id = _resolve_story_id_for_row(mapped, target=body.target)
            if story_id is None:
                failed_rows.append(
                    _CommitFailedRow(
                        row_index=idx,
                        title=mapped.title or None,
                        message="No target story (set target.story_id or map a story_id column).",
                    ),
                )
                continue

            try:
                _ensure_story_in_project(story_id, project_id)
            except HTTPException as exc:
                failed_rows.append(
                    _CommitFailedRow(
                        row_index=idx,
                        title=mapped.title or None,
                        message=str(exc.detail),
                    ),
                )
                continue

            try:
                tc, action, error = _materialise_test_case(
                    mapped=mapped,
                    project_id=project_id,
                    story_id=story_id,
                    batch_id=batch.id,
                    default_status=body.default_status,
                    duplicate_strategy=body.duplicate_strategy,
                )
            except Exception as exc:  # noqa: BLE001
                failed_rows.append(
                    _CommitFailedRow(
                        row_index=idx,
                        title=mapped.title or None,
                        message=f"persist error: {exc}",
                    ),
                )
                continue

            if action in ("created", "overwritten") and tc is not None:
                created.append(str(tc.id))
                touched_story_ids.add(str(story_id))
            elif action == "skipped":
                skipped += 1
            elif action == "failed":
                failed_rows.append(
                    _CommitFailedRow(
                        row_index=idx,
                        title=mapped.title or None,
                        message=error or "unknown failure",
                    ),
                )

    imported_count = len(created)
    failed_count = len(failed_rows)
    final_status = (
        "committed" if failed_count == 0 else "partial"
        if imported_count > 0 else "failed"
    )

    batch.status = final_status
    batch.imported_count = imported_count
    batch.skipped_count = skipped
    batch.failed_count = failed_count
    batch.mapping_json = dict(body.mapping)
    batch.duplicate_strategy = body.duplicate_strategy
    batch.target_project_id = str(project_id)
    batch.target_sprint_id = body.target.sprint_id
    batch.target_story_id = body.target.story_id
    batch.failed_rows_json = [r.model_dump() for r in failed_rows]
    batch.committed_at = datetime.now(UTC)
    # Once a batch is committed (even partially) we no longer need the
    # raw source file -- the per-TC external_payload preserves the
    # original row. Drop it so the imports/ directory doesn't grow.
    try:
        if Path(batch.source_path).is_file():
            Path(batch.source_path).unlink()
    except OSError:
        pass
    batch.source_path = ""
    db.commit()

    log_action(
        db,
        user=current_user,
        action="tc_import_committed",
        target_type="import_batch",
        target_id=batch.id,
        metadata={
            "imported": imported_count,
            "skipped": skipped,
            "failed": failed_count,
            "story_ids": sorted(touched_story_ids),
            "strategy": body.duplicate_strategy,
        },
    )

    duration_ms = int((time.monotonic() - t0) * 1000)
    return _CommitResponse(
        batch_id=batch.id,
        status=final_status,
        imported_count=imported_count,
        skipped_count=skipped,
        failed_count=failed_count,
        created_test_case_ids=created,
        failed_rows=failed_rows,
        duration_ms=duration_ms,
    )


# ---------- GET / list / rollback --------------------------------


@router.get("/{batch_id}")
def get_import_batch(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = _load_batch_or_404(db, batch_id, user=current_user)
    return batch.to_dict()


@router.get("")
def list_import_batches(
    project_slug: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_project_role_at_least(db, current_user, project_slug, ProjectRole.member)
    rows = (
        db.query(ImportBatch)
        .filter(ImportBatch.project_slug == project_slug)
        .order_by(ImportBatch.created_at.desc())
        .limit(limit)
        .all()
    )
    # Trim failed_rows on the list view -- it can be large per row and
    # the UI typically renders it only on the detail view.
    summaries = []
    for r in rows:
        d = r.to_dict()
        failed = d.pop("failed_rows", [])
        d["failed_rows_count"] = len(failed)
        summaries.append(d)
    return {"batches": summaries}


class _RollbackResponse(BaseModel):
    batch_id: str
    reverted_count: int
    status: str = "rolled_back"


@router.post("/{batch_id}/rollback", response_model=_RollbackResponse)
def rollback_import_batch(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hard-delete every TestCase this batch created.

    Detection: the commit handler stamps each TC with
    ``external_source = "csv_import:<batch_id>"`` (and the row index
    in external_id when none was supplied), so a project scan looking
    at external_source matches every row this batch produced. We do
    NOT touch TCs that an overwrite-mode commit modified; those still
    exist with their original ids and predate the batch.

    Admin / project lead only -- this is a destructive operation that
    can wipe hundreds of TCs in one click.
    """
    batch = _load_batch_or_404(db, batch_id, user=current_user)
    if batch.status == "rolled_back":
        return _RollbackResponse(batch_id=batch_id, reverted_count=0)
    # Tighten the gate from member -> lead for the destructive step.
    assert_project_role_at_least(db, current_user, batch.project_slug, ProjectRole.lead)

    if not batch.target_project_id:
        raise HTTPException(409, "Batch has no target_project_id; nothing to scan.")

    target_source = f"csv_import:{batch.id}"
    reverted = 0
    idx = _store.read(f"test_case_ids_project:{batch.target_project_id}")
    # Snapshot the id list so we can mutate the store as we iterate.
    for tid in list(idx.get("ids", [])):
        row = _store.read(f"test_case:{tid}")
        if not row:
            continue
        if str(row.get("external_source") or "") != target_source:
            continue
        try:
            _store.hard_delete_test_case(UUID(str(row.get("id"))))
            reverted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("rollback: failed to purge %s: %s", tid, exc)

    batch.status = "rolled_back"
    batch.committed_at = batch.committed_at or datetime.now(UTC)
    db.commit()

    log_action(
        db,
        user=current_user,
        action="tc_import_rolled_back",
        target_type="import_batch",
        target_id=batch.id,
        metadata={"reverted": reverted, "project_slug": batch.project_slug},
    )
    return _RollbackResponse(batch_id=batch_id, reverted_count=reverted)
