"""User stories, generated test-case review, and tag listing (additive routes)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ai_qa_portal.backend.config import REPO_ROOT, settings
from ai_qa_portal.backend.project_registry import slug_for_project_id
from ai_qa_portal.backend.prompts import assembler
from ai_qa_portal.backend.services.auth import (
    assert_user_owns_project,
    get_current_user,
)
from ai_qa_portal.backend.services.db import User
from ..models.generation import GenerationResponse
from ..models.tag import Tag, TagScope
from ..models.test_case import BatchApproveRequest, TestCase, TestCaseStatus
from ..models.user_story import UserStory, UserStoryCreate, UserStoryStatus, UserStoryUpdate
from ..services.failure_diagnoser import diagnose_run, format_for_prompt
from ..services.story_versioner import StoryVersioner
from ..services.test_case_generator import TestCaseGenerator
from ..services.test_case_script_builder import TestCaseScriptBuilder
from ..storage.json_file_backend import JsonFileBackend

_store = JsonFileBackend(settings.data_dir)
_versioner = StoryVersioner()
_generator = TestCaseGenerator()
_builder = TestCaseScriptBuilder()
_RESULTS_ROOT = Path(settings.results_dir)

router = APIRouter(
    prefix="/user-stories",
    tags=["user-stories"],
    dependencies=[Depends(get_current_user)],
)
test_cases_router = APIRouter(
    prefix="/test-cases",
    tags=["test-cases"],
    dependencies=[Depends(get_current_user)],
)
tags_router = APIRouter(
    prefix="/tags",
    tags=["tags"],
    dependencies=[Depends(get_current_user)],
)


def _story_model(row: dict) -> UserStory:
    return UserStory.model_validate(row)


def _user_can_see_story(s: UserStory, user: User) -> bool:
    """Phase 1 isolation: each user sees only their own stories. Admin sees all.
    Legacy unowned stories (empty owner_user_id) are admin-only."""
    if user.is_admin:
        return True
    return bool(s.owner_user_id) and s.owner_user_id == user.id


def _load_story_or_403(story_id: UUID, user: User) -> UserStory:
    try:
        row = _store.get_user_story(story_id)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    story = _story_model(row)
    if not _user_can_see_story(story, user):
        raise HTTPException(404, "User story not found")
    return story


@router.post("", response_model=UserStory, status_code=201)
async def create_user_story(
    body: UserStoryCreate,
    current_user: User = Depends(get_current_user),
):
    # Block creating a story under someone else's project.
    assert_user_owns_project(current_user, body.project_id)
    _store.seed_static_tags(body.project_id)
    now = datetime.now(timezone.utc)
    story = UserStory(
        id=uuid4(),
        project_id=body.project_id,
        title=body.title.strip(),
        description=body.description.strip(),
        status=UserStoryStatus.active,
        version=1,
        prev_version_id=None,
        created_at=now,
        updated_at=now,
        owner_user_id=current_user.id,
    )
    _store.save_user_story(story.model_dump(mode="json"))
    return story


@router.get("", response_model=list[UserStory])
def list_user_stories(
    project_id: UUID = Query(..., description="Project UUID"),
    current_user: User = Depends(get_current_user),
):
    rows = _store.list_user_stories(project_id)
    items = [_story_model(r) for r in rows if r.get("status") == UserStoryStatus.active.value]
    items = [s for s in items if _user_can_see_story(s, current_user)]
    items.sort(key=lambda s: s.created_at, reverse=True)
    return items


@router.get("/{story_id}", response_model=UserStory)
def get_user_story_by_id(story_id: UUID, current_user: User = Depends(get_current_user)):
    return _load_story_or_403(story_id, current_user)


@router.put("/{story_id}")
def update_user_story(
    story_id: UUID,
    body: UserStoryUpdate,
    current_user: User = Depends(get_current_user),
):
    existing = _load_story_or_403(story_id, current_user)
    if existing.status != UserStoryStatus.active:
        raise HTTPException(400, "Only active stories can be updated this way")
    new_story, stale_count = _versioner.update_story(existing, body, _store)
    return {
        "new_story": new_story.model_dump(mode="json"),
        "stale_count": stale_count,
        "message": f"Story updated to v{new_story.version}. {stale_count} test cases marked stale.",
    }


def _ensure_tag(project_id: UUID, name: str) -> None:
    key = f"tags:{project_id}:{name}"
    if _store.read(key):
        return
    tag = Tag(
        id=uuid4(),
        project_id=project_id,
        name=name,
        color="#8B5CF6",
        scope=TagScope.custom,
    )
    _store.save_tag(tag.model_dump(mode="json"))


@router.post("/{story_id}/generate", response_model=GenerationResponse)
async def generate_test_cases(story_id: UUID, current_user: User = Depends(get_current_user)):
    """Generate AI test-case drafts AND persist them with status=draft.

    Why persist immediately: the previous flow returned drafts as in-memory
    cards, and only the ones the user explicitly toggled approved + clicked
    "Submit approved" ever hit the backend. Result: the bulk run panel saw
    zero approved cases and the user thought nothing was saved. Now drafts
    show up in /test-cases?user_story_id=... right away; approval is a
    per-case PATCH.
    """
    story = _load_story_or_403(story_id, current_user)
    generated = await _generator.generate(story)
    project_id = story.project_id
    _store.seed_static_tags(project_id)

    persisted: list[TestCase] = []
    for g in generated:
        tags = [t.strip() for t in (g.suggested_tags or []) if t.strip()]
        for tname in tags:
            _ensure_tag(project_id, tname)
        now = datetime.now(timezone.utc)
        tc = TestCase(
            id=uuid4(),
            user_story_id=story.id,
            project_id=project_id,
            title=g.title.strip() or "Untitled",
            steps=list(g.steps),
            expected_result=(g.expected_result or "").strip(),
            preconditions=(g.preconditions or "").strip() or None,
            status=TestCaseStatus.draft,
            stale=False,
            tags=tags,
            created_at=now,
        )
        _store.save_test_case(tc.model_dump(mode="json"))
        persisted.append(tc)

    # Stuff persisted ids into the response so the frontend doesn't need a
    # second round-trip to know the saved drafts. Reuse GeneratedTestCase
    # shape for the existing response model -- the frontend will refetch
    # /test-cases anyway to get the canonical rows.
    return GenerationResponse(user_story_id=story.id, generated=generated)


class TestCaseCreate(BaseModel):
    """Manual create of a test case. Distinct from the AI-driven
    `/user-stories/{id}/generate` path -- this is what the "Add case
    manually" button on the story detail page hits."""
    user_story_id: UUID
    title: str
    steps: list[str] = Field(default_factory=list)
    expected_result: str = ""
    preconditions: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    status: TestCaseStatus = TestCaseStatus.draft


@test_cases_router.post("", response_model=TestCase, status_code=201)
def create_test_case(
    body: TestCaseCreate,
    current_user: User = Depends(get_current_user),
):
    """Create one test case manually. Ownership inherited from the parent
    story; new tags are seeded into the project's tag registry so they
    show up in the bulk-run tag dropdown immediately."""
    story = _load_story_or_403(body.user_story_id, current_user)
    _store.seed_static_tags(story.project_id)

    cleaned_tags = [t.strip() for t in body.tags if t.strip()]
    for tname in cleaned_tags:
        _ensure_tag(story.project_id, tname)

    tc = TestCase(
        id=uuid4(),
        user_story_id=story.id,
        project_id=story.project_id,
        title=body.title.strip() or "Untitled",
        steps=[s for s in body.steps if s is not None],
        expected_result=(body.expected_result or "").strip(),
        preconditions=(body.preconditions or "").strip() or None,
        status=body.status,
        stale=False,
        tags=cleaned_tags,
        created_at=datetime.now(timezone.utc),
    )
    _store.save_test_case(tc.model_dump(mode="json"))
    return tc


class TestCasePatch(BaseModel):
    """Per-case patch covering both status flips and content edits.

    Every field is optional; only the keys the caller actually sends are
    applied (`model_dump(exclude_unset=True)`). When ANY content field
    changes (title / steps / expected / preconditions), `script_path` is
    cleared so the bulk runner can't ship a stale Robot script -- the
    user can hit "Generate scripts" again to re-materialise.
    """
    status: Optional[TestCaseStatus] = None
    title: Optional[str] = None
    steps: Optional[list[str]] = None
    expected_result: Optional[str] = None
    preconditions: Optional[str] = None
    tags: Optional[list[str]] = None


_TC_CONTENT_FIELDS = frozenset({"title", "steps", "expected_result", "preconditions"})


@test_cases_router.patch("/{test_case_id}", response_model=TestCase)
def patch_test_case(
    test_case_id: UUID,
    body: TestCasePatch,
    current_user: User = Depends(get_current_user),
):
    """Patch any subset of a test case's fields. Used by both the per-row
    Approve / Reject / Re-draft buttons (status-only) and the new bulk
    Edit modal (full content edits)."""
    try:
        row = _store.get_test_case(test_case_id)
    except KeyError:
        raise HTTPException(404, "Test case not found") from None
    _load_story_or_403(UUID(str(row["user_story_id"])), current_user)

    payload = body.model_dump(exclude_unset=True)
    if not payload:
        return TestCase.model_validate(row)

    project_id = UUID(str(row["project_id"]))
    content_changed = any(k in payload for k in _TC_CONTENT_FIELDS)

    for k, v in payload.items():
        if k == "status":
            row[k] = v.value if hasattr(v, "value") else v
            continue
        if k == "title":
            row[k] = (v or "").strip() or "Untitled"
            continue
        if k == "expected_result":
            row[k] = (v or "").strip()
            continue
        if k == "preconditions":
            row[k] = (v or "").strip() or None
            continue
        if k == "steps":
            row[k] = [s for s in (v or []) if s is not None]
            continue
        if k == "tags":
            cleaned = [t.strip() for t in (v or []) if t.strip()]
            for tname in cleaned:
                _ensure_tag(project_id, tname)
            row[k] = cleaned
            continue

    if content_changed:
        # Saved Robot script is no longer guaranteed to match the new
        # steps/expected. Force re-materialisation on the next
        # "Generate scripts" / bulk run.
        row["script_path"] = None
        row["script_built_at"] = None

    _store.save_test_case(row)
    return TestCase.model_validate(row)


@test_cases_router.post("/batch-approve")
def batch_approve(body: BatchApproveRequest, current_user: User = Depends(get_current_user)):
    """Legacy batch-approve. Kept so older clients keep working; new flow
    persists drafts on /generate and approves per-case via PATCH."""
    story = _load_story_or_403(body.user_story_id, current_user)
    project_id = story.project_id
    _store.seed_static_tags(project_id)

    saved_ids: list[UUID] = []
    for ap in body.approved:
        for tname in ap.tags:
            if tname.strip():
                _ensure_tag(project_id, tname.strip())
        now = datetime.now(timezone.utc)
        tc = TestCase(
            id=uuid4(),
            user_story_id=body.user_story_id,
            project_id=project_id,
            title=ap.title.strip(),
            steps=list(ap.steps),
            expected_result=ap.expected_result.strip(),
            preconditions=(ap.preconditions or "").strip() or None,
            status=TestCaseStatus.approved,
            stale=False,
            tags=[t.strip() for t in ap.tags if t.strip()],
            created_at=now,
        )
        _store.save_test_case(tc.model_dump(mode="json"))
        saved_ids.append(tc.id)

    return {"saved": len(saved_ids), "test_case_ids": saved_ids}


@test_cases_router.get("", response_model=list[TestCase])
def list_test_cases(
    user_story_id: UUID = Query(...),
    current_user: User = Depends(get_current_user),
):
    # Inherit ownership from parent story.
    _load_story_or_403(user_story_id, current_user)
    rows = _store.get_test_cases_by_story(user_story_id)
    return [TestCase.model_validate(r) for r in rows]


# --- Script materialisation -------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _filename_slug(title: str, max_len: int = 40) -> str:
    """Filesystem-safe slug from a test case title. Empty or all-symbol titles
    fall back to "case"."""
    s = _SLUG_RE.sub("_", title.lower()).strip("_")
    if not s:
        s = "case"
    return s[:max_len]


class BuildScriptsResult(BaseModel):
    test_case_id: UUID
    script_path: str
    bytes_written: int


class BuildScriptsResponse(BaseModel):
    story_id: UUID
    project_slug: Optional[str]
    output_dir: str
    built: list[BuildScriptsResult]
    skipped: list[dict]


@router.post("/{story_id}/build-scripts", response_model=BuildScriptsResponse)
def build_scripts_for_story(
    story_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Materialise a Robot script per approved non-stale test case.

    Scripts are credential-agnostic: persona/org are not baked in. The runner
    injects ``globalSandboxTestUrl`` / ``sandboxUserNameInput`` /
    ``sandboxPasswordInput`` via ``robot --variable`` at run time, the same
    way the prompt-driven Generate flow already does. This means the same
    saved script can run against any org+persona the caller picks later.
    """
    story = _load_story_or_403(story_id, current_user)
    project_id = story.project_id

    rows = _store.get_test_cases_by_story(story_id)
    tcs = [TestCase.model_validate(r) for r in rows]
    runnable = [
        t for t in tcs if t.status == TestCaseStatus.approved and not t.stale
    ]

    skipped: list[dict] = []
    for t in tcs:
        if t in runnable:
            continue
        reason = "stale" if t.stale else (
            "not-approved" if t.status != TestCaseStatus.approved else "unknown"
        )
        skipped.append({"test_case_id": str(t.id), "title": t.title, "reason": reason})

    if not runnable:
        raise HTTPException(
            400,
            "No approved non-stale test cases to build. Approve some cases first.",
        )

    slug = slug_for_project_id(project_id)
    if slug:
        out_dir = REPO_ROOT / "Saved_Projects" / slug / "Tests" / "Generated" / f"story_{story.id.hex[:12]}"
    else:
        # No registry entry (legacy / never-saved project): fall back to the
        # repo-level Tests/Generated dir so we still produce a valid path.
        out_dir = REPO_ROOT / "Tests" / "Generated" / f"story_{story.id.hex[:12]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    built: list[BuildScriptsResult] = []
    now = datetime.now(timezone.utc)
    for tc in runnable:
        try:
            robot = _builder.build_robot_script(tc, persona=None, org=None)
        except Exception as exc:  # noqa: BLE001 -- LLM/IO; surface in response
            skipped.append(
                {"test_case_id": str(tc.id), "title": tc.title, "reason": f"build-failed: {exc}"}
            )
            continue
        fname = f"{_filename_slug(tc.title)}_{tc.id.hex[:8]}.robot"
        path = out_dir / fname
        path.write_text(robot.rstrip() + "\n", encoding="utf-8")
        # Persist the relative path on the TestCase so /run/user-story/{id}
        # can prefer it later.
        try:
            rel = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = path.as_posix()
        row = _store.get_test_case(tc.id)
        row["script_path"] = rel
        row["script_built_at"] = now.isoformat()
        _store.save_test_case(row)
        built.append(
            BuildScriptsResult(
                test_case_id=tc.id,
                script_path=rel,
                bytes_written=len(robot.encode("utf-8")),
            )
        )

    return BuildScriptsResponse(
        story_id=story.id,
        project_slug=slug,
        output_dir=str(out_dir),
        built=built,
        skipped=skipped,
    )


@test_cases_router.get("/{test_case_id}/script")
def get_test_case_script(
    test_case_id: UUID,
    current_user: User = Depends(get_current_user),
):
    """Return the saved Robot script content for inline preview in the UI."""
    try:
        row = _store.get_test_case(test_case_id)
    except KeyError:
        raise HTTPException(404, "Test case not found") from None
    _load_story_or_403(UUID(str(row["user_story_id"])), current_user)

    rel = row.get("script_path")
    if not rel:
        raise HTTPException(404, "No script has been built for this test case yet")
    abs_path = (REPO_ROOT / rel).resolve()
    # Defence in depth: never serve a path that escaped the repo via a saved
    # absolute or symlinked path.
    try:
        abs_path.relative_to(REPO_ROOT.resolve())
    except ValueError:
        raise HTTPException(403, "Refusing to read file outside the repo") from None
    if not abs_path.is_file():
        raise HTTPException(410, f"Script file is missing on disk: {rel}")
    return {
        "test_case_id": str(test_case_id),
        "path": rel,
        "content": abs_path.read_text(encoding="utf-8"),
        "built_at": row.get("script_built_at"),
    }


@tags_router.get("", response_model=list[Tag])
def list_tags(
    project_id: UUID = Query(...),
    current_user: User = Depends(get_current_user),
):
    """Tags are project-scoped metadata. Phase 1: return tags for any project_id
    the user has at least one story in (cheap proxy for "owns the project").
    Admin sees all. Static tags are always visible to anyone with project access.
    """
    rows = _store.list_tags(project_id)
    tags = [Tag.model_validate(r) for r in rows]
    if not current_user.is_admin:
        # Cheap ownership probe: do we have any story in this project?
        my_stories = [
            s for s in (_story_model(r) for r in _store.list_user_stories(project_id))
            if s.owner_user_id == current_user.id
        ]
        if not my_stories:
            return []
    static = sorted([t for t in tags if t.scope == TagScope.static], key=lambda t: t.name.lower())
    custom = sorted([t for t in tags if t.scope == TagScope.custom], key=lambda t: t.name.lower())
    return static + custom


# --- Self-healing -----------------------------------------------------------

_HEAL_CAP_PER_HOUR = 2


class HealRequest(BaseModel):
    run_folder: str  # which failed run to learn from


class HealResponse(BaseModel):
    ok: bool
    test_case_id: UUID
    script_path: Optional[str]
    diagnosis: dict
    attempts: int
    message: str


def _resolve_run_dir_safe(run_folder: str) -> Path:
    """Same path-safety rules as routers/runs.py uses for file serving."""
    if "/" in run_folder or "\\" in run_folder or ".." in run_folder:
        raise HTTPException(400, "Invalid run folder")
    run_dir = (_RESULTS_ROOT / run_folder).resolve()
    try:
        run_dir.relative_to(_RESULTS_ROOT.resolve())
    except ValueError:
        raise HTTPException(400, "Refusing to access path outside the results directory") from None
    if not run_dir.is_dir():
        raise HTTPException(404, f"Run '{run_folder}' not found")
    return run_dir


def _read_screenshot_bytes(path: Optional[str], limit: int = 4 * 1024 * 1024) -> Optional[bytes]:
    """Read a screenshot file from disk, capped at ``limit`` bytes so we
    don't pump huge images into the LLM. Returns None on any error."""
    if not path:
        return None
    p = Path(path)
    try:
        if not p.is_file():
            return None
        if p.stat().st_size > limit:
            return None
        return p.read_bytes()
    except OSError:
        return None


@test_cases_router.post("/{test_case_id}/heal", response_model=HealResponse)
def heal_test_case(
    test_case_id: UUID,
    body: HealRequest,
    current_user: User = Depends(get_current_user),
):
    """Read a failed run, ask the LLM (with playbook + catalog + screenshot)
    to rewrite the saved Robot script, and persist the rewrite.

    The endpoint:
      1. Loads the failing test case (ownership inherited from parent story).
      2. Diagnoses ``output.xml`` to extract the failing keyword + error
         + last screenshot.
      3. Calls the LLM with the "healer" role prompt (playbook + catalog +
         original test case + current script + diagnosis + screenshot bytes
         when available + small).
      4. Writes the rewrite to the same ``script_path`` so the next run
         picks it up automatically.
      5. Bumps ``heal_attempts`` and ``last_healed_at`` on the test case
         and refuses to act past ``_HEAL_CAP_PER_HOUR`` attempts within
         the trailing hour.

    The endpoint is sync because the heal is one LLM call; the bulk
    runner already runs heals concurrently via its ThreadPoolExecutor.
    """
    try:
        row = _store.get_test_case(test_case_id)
    except KeyError:
        raise HTTPException(404, "Test case not found") from None
    story_id = UUID(str(row["user_story_id"]))
    _load_story_or_403(story_id, current_user)

    tc = TestCase.model_validate(row)
    if not tc.script_path:
        raise HTTPException(409, "Test case has no saved script to heal. Build scripts first.")

    # Per-hour cap. Cheap to compute -- one timestamp comparison.
    now = datetime.now(timezone.utc)
    if tc.last_healed_at is not None:
        last = tc.last_healed_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (now - last).total_seconds()
        if elapsed < 3600 and tc.heal_attempts >= _HEAL_CAP_PER_HOUR:
            raise HTTPException(
                429,
                f"Heal cap reached: {tc.heal_attempts} attempts in the last hour. "
                "Investigate manually or wait an hour for the cap to reset.",
            )

    run_dir = _resolve_run_dir_safe(body.run_folder)
    diag = diagnose_run(run_dir)
    if diag is None:
        raise HTTPException(
            400,
            "No actionable diagnosis: the run has no output.xml or did not fail at the test level.",
        )

    script_abs = (REPO_ROOT / tc.script_path).resolve()
    if not script_abs.is_file():
        raise HTTPException(410, f"Saved script missing on disk: {tc.script_path}")
    current_script = script_abs.read_text(encoding="utf-8")

    failure_block = format_for_prompt(diag)
    steps_block = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(tc.steps))
    user_body = (
        f"## Original test case\n\n"
        f"Title: {tc.title}\n"
        f"Preconditions: {tc.preconditions or 'None'}\n\n"
        f"Steps:\n{steps_block}\n\n"
        f"Expected result: {tc.expected_result}\n"
        f"Tags: {', '.join(tc.tags) if tc.tags else 'none'}\n\n"
        f"## Current saved script (which just failed)\n\n"
        f"```robot\n{current_script}\n```\n\n"
        f"## Diagnosis from the failed run\n\n"
        f"{failure_block}\n"
    )
    user_prompt = assembler.build_user_prompt_with_catalog(user_body, include_full_catalog=True)
    system_prompt = assembler.build_system_prompt("healer")

    screenshot_bytes = _read_screenshot_bytes(diag.screenshot_path)

    # Late import to avoid pulling ai_bridge at module load time (it
    # eagerly imports LLM SDK packages which are heavy).
    from ai_bridge import call_llm, extract_robot_code

    raw = call_llm(system_prompt, user_prompt, image_bytes=screenshot_bytes)
    new_script = extract_robot_code(raw).rstrip()
    if not new_script:
        raise HTTPException(502, "Healer returned no usable Robot source.")

    # Atomic write -- write to a tempfile next to the target then rename.
    tmp_path = script_abs.with_suffix(script_abs.suffix + ".heal.tmp")
    tmp_path.write_text(new_script + "\n", encoding="utf-8")
    tmp_path.replace(script_abs)

    # Reset attempts when an hour has passed; otherwise increment.
    if tc.last_healed_at is not None:
        last = tc.last_healed_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last).total_seconds() >= 3600:
            tc.heal_attempts = 0
    tc.heal_attempts += 1
    tc.last_healed_at = now
    tc.script_built_at = now
    _store.save_test_case(tc.model_dump(mode="json"))

    return HealResponse(
        ok=True,
        test_case_id=tc.id,
        script_path=tc.script_path,
        diagnosis=diag.to_dict(),
        attempts=tc.heal_attempts,
        message=(
            f"Healed. Failing keyword '{diag.first_failure.keyword_name}' rewritten."
            if diag.first_failure
            else "Healed."
        ),
    )
