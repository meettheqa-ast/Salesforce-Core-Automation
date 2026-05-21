"""Prompt registry -- seeds + low-level CRUD.

Boot hook: ``seed_system_templates(db)`` upserts every entry in
``prompt_seeds.SEED_MANIFEST`` as a system template + version. Idempotent
and content-aware -- if a .md seed changes between releases, we append a
new system version (so users on the old version aren't silently flipped)
and update the template's ``seed_content_sha`` pointer.

CRUD helpers wrap SQL chatter into clean operations the router + resolver
both use. Every mutation calls ``bump_cache_epoch`` so multi-worker
resolvers invalidate their LRUs.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_qa_portal.backend.services.db_models.prompts import (
    PromptMeta,
    PromptOverride,
    PromptTemplate,
    PromptVersion,
)

from .prompt_seeds import SEED_MANIFEST, SeedSpec, read_seed_body

logger = logging.getLogger("ai_qa_portal.prompt_registry")


# ---------- cache epoch ------------------------------------------


def get_cache_epoch(db: Session) -> int:
    """Read the singleton cache_epoch row. Returns 1 (and creates the
    row) when missing; that only happens on a fresh DB that skipped the
    Alembic insert (e.g. tests using ``Base.metadata.create_all``)."""
    row = db.get(PromptMeta, 1)
    if row is None:
        row = PromptMeta(id=1, cache_epoch=1)
        db.add(row)
        db.commit()
    return int(row.cache_epoch or 1)


def bump_cache_epoch(db: Session) -> int:
    """Atomically increment the cache_epoch. Called by every mutation
    in this module + the router. The resolver checks this value on each
    resolve and discards its LRU when it advances."""
    row = db.get(PromptMeta, 1)
    if row is None:
        row = PromptMeta(id=1, cache_epoch=1)
        db.add(row)
    row.cache_epoch = int(row.cache_epoch or 0) + 1
    row.updated_at = datetime.now(UTC)
    db.commit()
    return int(row.cache_epoch)


# ---------- seed loader ------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _system_template_for_seed(db: Session, spec: SeedSpec) -> PromptTemplate | None:
    """System templates are uniquely identified by (category, name,
    is_system=True). We never have more than one row per seed."""
    stmt = (
        select(PromptTemplate)
        .where(PromptTemplate.is_system.is_(True))
        .where(PromptTemplate.category == spec.category)
        .where(PromptTemplate.name == spec.name)
    )
    return db.execute(stmt).scalar_one_or_none()


def _next_version_number(db: Session, template_id: str) -> int:
    last = (
        db.query(PromptVersion)
        .filter(PromptVersion.template_id == template_id)
        .order_by(PromptVersion.version_number.desc())
        .first()
    )
    return int(last.version_number) + 1 if last else 1


def seed_system_templates(db: Session) -> dict:
    """Idempotent boot seeder. Returns a small summary dict for logging.

    Algorithm:
      1. For each SeedSpec, read the .md body from disk.
      2. Compute sha256(body); compare to PromptTemplate.seed_content_sha.
      3. Insert a brand-new template + version 1 when the template
         doesn't exist yet.
      4. Append a new version + advance seed_content_sha when the file
         changed since last boot.
      5. Skip when nothing changed (cheap fast path).

    User overrides are NEVER touched -- they live in prompt_overrides
    and resolve against whatever template_id + version_id they last
    pinned.
    """
    created = 0
    appended = 0
    unchanged = 0
    missing: list[str] = []

    for spec in SEED_MANIFEST:
        try:
            body = read_seed_body(spec.filename)
        except FileNotFoundError:
            missing.append(spec.filename)
            logger.warning("prompt seed file missing: %s", spec.filename)
            continue

        body_sha = _sha256(body)
        existing = _system_template_for_seed(db, spec)

        if existing is None:
            template = PromptTemplate(
                category=spec.category,
                name=spec.name,
                description=spec.description,
                is_system=True,
                is_active=True,
                output_format=spec.output_format,
                placeholders_declared=list(spec.placeholders_declared),
                seed_content_sha=body_sha,
            )
            db.add(template)
            db.flush()
            db.add(
                PromptVersion(
                    template_id=template.id,
                    version_number=1,
                    body=body,
                    body_sha256=body_sha,
                    body_bytes=len(body.encode("utf-8")),
                    change_note="initial seed",
                )
            )
            created += 1
            continue

        # Keep template-level metadata aligned with the manifest in case
        # description / output_format / placeholders changed in a release.
        existing.description = spec.description
        existing.output_format = spec.output_format
        existing.placeholders_declared = list(spec.placeholders_declared)

        if existing.seed_content_sha == body_sha:
            unchanged += 1
            continue

        # Seed body changed since last boot -> append a new system
        # version. We do NOT auto-activate it for overrides -- users
        # who pinned an earlier version stay where they were.
        next_v = _next_version_number(db, existing.id)
        db.add(
            PromptVersion(
                template_id=existing.id,
                version_number=next_v,
                body=body,
                body_sha256=body_sha,
                body_bytes=len(body.encode("utf-8")),
                change_note=f"seed update (release upgrade) -> v{next_v}",
            )
        )
        existing.seed_content_sha = body_sha
        existing.updated_at = datetime.now(UTC)
        appended += 1

    db.commit()
    if created or appended:
        bump_cache_epoch(db)
    summary = {
        "created": created,
        "version_appended": appended,
        "unchanged": unchanged,
        "missing_files": missing,
    }
    logger.info("prompt seeds: %s", summary)
    return summary


# ---------- CRUD helpers used by router + resolver ---------------


@dataclass(frozen=True)
class TemplateWithVersion:
    """Convenience tuple returned by ``get_active_version_for_template``
    and by the seed-fallback path in the resolver."""

    template: PromptTemplate
    version: PromptVersion


def get_template(db: Session, template_id: str) -> PromptTemplate | None:
    return db.get(PromptTemplate, template_id)


def list_templates(
    db: Session,
    *,
    category: str | None = None,
    include_deleted: bool = False,
    owner_user_id: str | None = None,
) -> list[PromptTemplate]:
    q = db.query(PromptTemplate)
    if category:
        q = q.filter(PromptTemplate.category == category)
    if not include_deleted:
        q = q.filter(PromptTemplate.deleted_at.is_(None))
    if owner_user_id is not None:
        # `None` selects system + project + org rows too; passing a real
        # user id narrows to that user's clones.
        q = q.filter(PromptTemplate.owner_user_id == owner_user_id)
    return q.order_by(PromptTemplate.is_system.desc(), PromptTemplate.name).all()


def list_versions(db: Session, template_id: str, *, limit: int = 50) -> list[PromptVersion]:
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.template_id == template_id)
        .order_by(PromptVersion.version_number.desc())
        .limit(limit)
        .all()
    )


def get_version(db: Session, version_id: str) -> PromptVersion | None:
    return db.get(PromptVersion, version_id)


def get_latest_version(db: Session, template_id: str) -> PromptVersion | None:
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.template_id == template_id)
        .order_by(PromptVersion.version_number.desc())
        .first()
    )


def get_version_by_number(
    db: Session, template_id: str, version_number: int,
) -> PromptVersion | None:
    return (
        db.query(PromptVersion)
        .filter(PromptVersion.template_id == template_id)
        .filter(PromptVersion.version_number == int(version_number))
        .one_or_none()
    )


def create_user_template(
    db: Session,
    *,
    category: str,
    name: str,
    body: str,
    description: str | None,
    output_format: str,
    placeholders_declared: Iterable[str],
    owner_user_id: str | None,
    source_template_id: str | None,
    model_hint: str | None = None,
    change_note: str = "initial",
) -> TemplateWithVersion:
    """Create a non-system template + its initial version. Use this for
    user clones, project templates, org templates."""
    template = PromptTemplate(
        category=category,
        name=name,
        description=description,
        is_system=False,
        is_active=True,
        output_format=output_format,
        placeholders_declared=list(placeholders_declared),
        owner_user_id=owner_user_id,
        source_template_id=source_template_id,
        model_hint=model_hint,
    )
    db.add(template)
    db.flush()
    body_sha = _sha256(body)
    version = PromptVersion(
        template_id=template.id,
        version_number=1,
        body=body,
        body_sha256=body_sha,
        body_bytes=len(body.encode("utf-8")),
        change_note=change_note,
        created_by_user_id=owner_user_id,
    )
    db.add(version)
    db.commit()
    bump_cache_epoch(db)
    return TemplateWithVersion(template=template, version=version)


def append_version(
    db: Session,
    *,
    template: PromptTemplate,
    body: str,
    change_note: str | None,
    created_by_user_id: str | None,
) -> PromptVersion:
    """Append a new version row to an existing template. Bumps the
    template's updated_at + the cache_epoch so resolvers re-read."""
    next_v = _next_version_number(db, template.id)
    body_sha = _sha256(body)
    version = PromptVersion(
        template_id=template.id,
        version_number=next_v,
        body=body,
        body_sha256=body_sha,
        body_bytes=len(body.encode("utf-8")),
        change_note=change_note,
        created_by_user_id=created_by_user_id,
    )
    db.add(version)
    template.updated_at = datetime.now(UTC)
    db.commit()
    bump_cache_epoch(db)
    return version


def soft_delete_template(db: Session, *, template: PromptTemplate) -> None:
    if template.is_system:
        # Refuses silently; the router enforces this with a 403 first,
        # so reaching here means a programming bug not user input.
        raise ValueError("System templates cannot be soft-deleted")
    template.deleted_at = datetime.now(UTC)
    db.commit()
    bump_cache_epoch(db)


# ---------- override management ----------------------------------


def get_override(
    db: Session, *, scope: str, scope_id: str | None, category: str,
) -> PromptOverride | None:
    q = db.query(PromptOverride).filter(
        PromptOverride.scope == scope,
        PromptOverride.category == category,
    )
    if scope_id is None:
        q = q.filter(PromptOverride.scope_id.is_(None))
    else:
        q = q.filter(PromptOverride.scope_id == scope_id)
    return q.one_or_none()


def set_override(
    db: Session,
    *,
    scope: str,
    scope_id: str | None,
    category: str,
    template_id: str,
    active_version_id: str,
    updated_by_user_id: str | None,
) -> PromptOverride:
    existing = get_override(db, scope=scope, scope_id=scope_id, category=category)
    if existing is None:
        existing = PromptOverride(
            scope=scope,
            scope_id=scope_id,
            category=category,
            template_id=template_id,
            active_version_id=active_version_id,
            updated_by_user_id=updated_by_user_id,
        )
        db.add(existing)
    else:
        existing.template_id = template_id
        existing.active_version_id = active_version_id
        existing.updated_at = datetime.now(UTC)
        existing.updated_by_user_id = updated_by_user_id
    db.commit()
    bump_cache_epoch(db)
    return existing


def clear_override(
    db: Session, *, scope: str, scope_id: str | None, category: str,
) -> bool:
    """Delete the override row (resolver falls back to the next layer).
    Returns True when a row was removed."""
    existing = get_override(db, scope=scope, scope_id=scope_id, category=category)
    if existing is None:
        return False
    db.delete(existing)
    db.commit()
    bump_cache_epoch(db)
    return True


def list_overrides_for_scope(
    db: Session, *, scope: str, scope_id: str | None,
) -> list[PromptOverride]:
    q = db.query(PromptOverride).filter(PromptOverride.scope == scope)
    if scope_id is None:
        q = q.filter(PromptOverride.scope_id.is_(None))
    else:
        q = q.filter(PromptOverride.scope_id == scope_id)
    return q.all()


# ---------- usage audit ------------------------------------------


def record_usage(
    db: Session,
    *,
    template_version_id: str | None,
    category: str,
    user_id: str | None = None,
    project_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    qa_mode: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> None:
    """Insert one ``prompt_usage_audit`` row. Best-effort -- audit
    failures must never break a generation request, so this swallows
    SQL errors after logging."""
    from ai_qa_portal.backend.services.db_models.prompts import PromptUsageAudit

    try:
        row = PromptUsageAudit(
            template_version_id=template_version_id,
            category=category,
            user_id=user_id,
            project_id=project_id,
            model=model,
            provider=provider,
            qa_mode=qa_mode,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            target_type=target_type,
            target_id=target_id,
        )
        db.add(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "prompt_usage_audit insert failed (category=%s target=%s/%s): %s",
            category, target_type, target_id, exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
