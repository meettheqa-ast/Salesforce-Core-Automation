"""AI prompt management router.

Endpoints under ``/api/prompts``:

  * ``GET    /categories``                  -- known categories +
                                                placeholder allow-lists
  * ``GET    /``                             -- list templates visible
                                                to the user
  * ``GET    /{template_id}``                -- template + last N versions
                                                + active overrides
  * ``GET    /{template_id}/versions/{n}``   -- single version body
  * ``POST   /``                             -- create a user / project
                                                / org template (clone or
                                                blank)
  * ``POST   /{template_id}/versions``       -- append immutable version
  * ``POST   /{template_id}/activate``       -- upsert active override
                                                row for a scope
  * ``POST   /{template_id}/reset``          -- delete a scope's
                                                override (fall back to
                                                next layer)
  * ``POST   /{template_id}/preview``        -- render template against
                                                mock context (no LLM
                                                call)
  * ``DELETE /{template_id}``                -- soft delete user-owned;
                                                ``?permanent=true``
                                                admin-only hard delete
  * ``GET    /audit``                        -- usage audit

RBAC:
  * Any authenticated user can READ system seeds + their own templates.
  * Mutations on ``scope='user'`` require ``current_user.id ==
    target.scope_id``.
  * Mutations on ``scope='project'`` require ``ProjectRole.lead`` on
    the project.
  * Mutations on ``scope='org'`` or any system template require
    ``is_admin``.

Every mutation calls ``log_action`` so the existing audit history view
shows prompt edits inline with everything else.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ai_qa_portal.backend.services.audit import log_action
from ai_qa_portal.backend.services.auth import (
    assert_project_role_at_least,
    get_current_user,
)
from ai_qa_portal.backend.services.db import ProjectRole, User, get_db
from ai_qa_portal.backend.services.db_models.prompts import (
    PromptOverride,
    PromptTemplate,
    PromptUsageAudit,
    PromptVersion,
)
from ai_qa_portal.backend.services.prompt_compiler import (
    PromptCompileError,
    compile,
    validate_body,
)
from ai_qa_portal.backend.services.prompt_seeds import SEED_MANIFEST

from ..services import prompt_registry

logger = logging.getLogger("ai_qa_portal.prompts_router")

router = APIRouter(
    prefix="/api/prompts",
    tags=["prompts"],
    dependencies=[Depends(get_current_user)],
)


PROMPT_MAX_BODY_BYTES = int(os.environ.get("PROMPT_MAX_BODY_BYTES", str(200 * 1024)))


# ---------- Pydantic IO shapes -----------------------------------


class _CategoryInfo(BaseModel):
    category: str
    default_name: str
    description: str
    output_format: str
    placeholders: list[str]
    compose_with_playbook: bool


class _VersionSummary(BaseModel):
    id: str
    version_number: int
    change_note: str | None
    body_bytes: int
    created_by_user_id: str | None
    created_at: str | None


class _TemplateSummary(BaseModel):
    id: str
    category: str
    name: str
    description: str | None
    is_system: bool
    is_active: bool
    output_format: str
    model_hint: str | None
    placeholders_declared: list[str]
    owner_user_id: str | None
    source_template_id: str | None
    created_at: str | None
    updated_at: str | None
    deleted_at: str | None


class _ActiveOverride(BaseModel):
    scope: str
    scope_id: str | None
    template_id: str
    template_name: str
    active_version_id: str
    active_version_number: int


class _TemplateDetail(_TemplateSummary):
    versions: list[_VersionSummary]
    overrides: list[_ActiveOverride]


class _CreateTemplate(BaseModel):
    category: str
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    body: str
    output_format: str = "json_array"
    model_hint: str | None = None
    placeholders_declared: list[str] | None = None
    source_template_id: str | None = None
    scope: str = Field(default="user", pattern="^(user|project|org)$")
    scope_id: str | None = None
    change_note: str | None = "initial"


class _AppendVersion(BaseModel):
    body: str
    change_note: str | None = None


class _Activate(BaseModel):
    version_number: int = Field(ge=1)
    scope: str = Field(pattern="^(user|project|org)$")
    scope_id: str | None = None


class _Reset(BaseModel):
    scope: str = Field(pattern="^(user|project|org)$")
    scope_id: str | None = None


class _Preview(BaseModel):
    context: dict = Field(default_factory=dict)
    strict: bool = False


# ---------- helpers ----------------------------------------------


def _load_or_404(db: Session, template_id: str) -> PromptTemplate:
    row = db.get(PromptTemplate, template_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, f"Prompt template {template_id} not found")
    return row


def _category_spec(category: str):
    """Return the SeedSpec for a category (or None for user-only
    categories). Used to surface placeholder allow-lists in the API."""
    for spec in SEED_MANIFEST:
        if spec.category == category:
            return spec
    return None


def _enforce_scope_perms(
    *, db: Session, user: User, scope: str, scope_id: str | None, target_is_system: bool,
) -> None:
    """Raise 403 on any unauthorised mutation. Centralised so every
    write endpoint applies the same rules."""
    if target_is_system and not user.is_admin:
        raise HTTPException(403, "System templates can only be modified by admins.")
    if scope == "user":
        if not scope_id or scope_id != str(user.id):
            raise HTTPException(
                403,
                "User-scope writes must match the authenticated user.",
            )
        return
    if scope == "project":
        if not scope_id:
            raise HTTPException(422, "project-scope writes require scope_id.")
        assert_project_role_at_least(db, user, scope_id, ProjectRole.lead)
        return
    if scope == "org":
        if not user.is_admin:
            raise HTTPException(403, "Org-scope writes require admin.")
        return
    raise HTTPException(422, f"Unknown scope: {scope!r}")


def _version_summary(v: PromptVersion) -> _VersionSummary:
    return _VersionSummary(
        id=v.id,
        version_number=int(v.version_number),
        change_note=v.change_note,
        body_bytes=int(v.body_bytes or 0),
        created_by_user_id=v.created_by_user_id,
        created_at=v.created_at.isoformat() if v.created_at else None,
    )


def _template_summary(t: PromptTemplate) -> _TemplateSummary:
    return _TemplateSummary(**t.to_dict())


# ---------- routes -----------------------------------------------


@router.get("/categories", response_model=list[_CategoryInfo])
def list_categories() -> list[_CategoryInfo]:
    """The set of categories the registry knows about, with each
    category's declared placeholder allow-list. The editor right-rail
    renders these as the variable reference, and the create form
    populates its category <select> from this response."""
    out: list[_CategoryInfo] = []
    for spec in SEED_MANIFEST:
        out.append(
            _CategoryInfo(
                category=spec.category,
                default_name=spec.name,
                description=spec.description,
                output_format=spec.output_format,
                placeholders=list(spec.placeholders_declared),
                compose_with_playbook=spec.compose_with_playbook,
            )
        )
    return out


@router.get("", response_model=list[_TemplateSummary])
def list_templates(
    category: str | None = Query(None),
    include_deleted: bool = Query(False),
    mine_only: bool = Query(False, description="Only this user's templates."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = prompt_registry.list_templates(
        db,
        category=category,
        include_deleted=include_deleted,
        owner_user_id=current_user.id if mine_only else None,
    )
    return [_template_summary(r) for r in rows]


@router.get("/{template_id}", response_model=_TemplateDetail)
def get_template(
    template_id: str,
    versions_limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tpl = _load_or_404(db, template_id)
    versions = [
        _version_summary(v)
        for v in prompt_registry.list_versions(db, template_id, limit=versions_limit)
    ]
    override_rows = (
        db.query(PromptOverride)
        .filter(PromptOverride.template_id == template_id)
        .all()
    )
    active = []
    for o in override_rows:
        v = db.get(PromptVersion, o.active_version_id)
        active.append(
            _ActiveOverride(
                scope=o.scope,
                scope_id=o.scope_id,
                template_id=o.template_id,
                template_name=tpl.name,
                active_version_id=o.active_version_id,
                active_version_number=int(v.version_number) if v else 0,
            )
        )
    return _TemplateDetail(
        **tpl.to_dict(),
        versions=versions,
        overrides=active,
    )


class _VersionBody(BaseModel):
    id: str
    template_id: str
    version_number: int
    body: str
    change_note: str | None
    body_bytes: int
    created_at: str | None
    created_by_user_id: str | None


@router.get("/{template_id}/versions/{version_number}", response_model=_VersionBody)
def get_version(
    template_id: str,
    version_number: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tpl = _load_or_404(db, template_id)
    v = prompt_registry.get_version_by_number(db, template_id, version_number)
    if v is None:
        raise HTTPException(404, f"Version {version_number} not found")
    return _VersionBody(
        id=v.id,
        template_id=tpl.id,
        version_number=int(v.version_number),
        body=v.body,
        change_note=v.change_note,
        body_bytes=int(v.body_bytes or 0),
        created_at=v.created_at.isoformat() if v.created_at else None,
        created_by_user_id=v.created_by_user_id,
    )


@router.post("", response_model=_TemplateDetail, status_code=201)
def create_template(
    body: _CreateTemplate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _enforce_scope_perms(
        db=db, user=current_user, scope=body.scope, scope_id=body.scope_id,
        target_is_system=False,
    )

    # Decide placeholder allow-list: caller-supplied wins, else fall
    # back to the category seed spec (which always knows the right list).
    declared = list(body.placeholders_declared or [])
    if not declared:
        spec = _category_spec(body.category)
        if spec is not None:
            declared = list(spec.placeholders_declared)

    try:
        validate_body(
            body.body, declared_placeholders=declared, max_bytes=PROMPT_MAX_BODY_BYTES,
        )
    except PromptCompileError as exc:
        raise HTTPException(400, str(exc)) from exc

    tw = prompt_registry.create_user_template(
        db,
        category=body.category,
        name=body.name,
        body=body.body,
        description=body.description,
        output_format=body.output_format,
        placeholders_declared=declared,
        # owner_user_id semantics: user clones own them; project / org
        # templates have null owner (their access is gated by RBAC, not
        # ownership) so any project lead can edit a project template.
        owner_user_id=current_user.id if body.scope == "user" else None,
        source_template_id=body.source_template_id,
        model_hint=body.model_hint,
        change_note=body.change_note or "initial",
    )
    log_action(
        db, user=current_user, action="prompt_template_created",
        target_type="prompt_template", target_id=tw.template.id,
        metadata={
            "category": body.category, "scope": body.scope,
            "scope_id": body.scope_id, "source_template_id": body.source_template_id,
        },
    )
    return _TemplateDetail(
        **tw.template.to_dict(),
        versions=[_version_summary(tw.version)],
        overrides=[],
    )


@router.post("/{template_id}/versions", response_model=_VersionSummary, status_code=201)
def append_version(
    template_id: str,
    body: _AppendVersion,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tpl = _load_or_404(db, template_id)
    if tpl.is_system:
        raise HTTPException(
            403,
            "System templates are immutable. Clone first via POST /api/prompts.",
        )
    # Versioning is a write -- enforce the same scope rules as creation.
    if tpl.owner_user_id and tpl.owner_user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(403, "Only the template owner (or admin) may add versions.")

    try:
        validate_body(
            body.body,
            declared_placeholders=tpl.placeholders_declared or [],
            max_bytes=PROMPT_MAX_BODY_BYTES,
        )
    except PromptCompileError as exc:
        raise HTTPException(400, str(exc)) from exc

    v = prompt_registry.append_version(
        db, template=tpl, body=body.body, change_note=body.change_note,
        created_by_user_id=current_user.id,
    )
    log_action(
        db, user=current_user, action="prompt_version_added",
        target_type="prompt_template", target_id=tpl.id,
        metadata={"version_number": v.version_number, "change_note": body.change_note},
    )
    return _version_summary(v)


@router.post("/{template_id}/activate", response_model=_ActiveOverride)
def activate_version(
    template_id: str,
    body: _Activate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tpl = _load_or_404(db, template_id)
    _enforce_scope_perms(
        db=db, user=current_user, scope=body.scope, scope_id=body.scope_id,
        target_is_system=False,
    )
    v = prompt_registry.get_version_by_number(db, template_id, body.version_number)
    if v is None:
        raise HTTPException(404, f"Version {body.version_number} not found on this template")
    override = prompt_registry.set_override(
        db,
        scope=body.scope,
        scope_id=body.scope_id,
        category=tpl.category,
        template_id=tpl.id,
        active_version_id=v.id,
        updated_by_user_id=current_user.id,
    )
    log_action(
        db, user=current_user, action="prompt_activated",
        target_type="prompt_template", target_id=tpl.id,
        metadata={
            "version_number": v.version_number, "scope": body.scope,
            "scope_id": body.scope_id, "category": tpl.category,
        },
    )
    return _ActiveOverride(
        scope=override.scope,
        scope_id=override.scope_id,
        template_id=tpl.id,
        template_name=tpl.name,
        active_version_id=override.active_version_id,
        active_version_number=int(v.version_number),
    )


@router.post("/{template_id}/reset")
def reset_override(
    template_id: str,
    body: _Reset,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tpl = _load_or_404(db, template_id)
    _enforce_scope_perms(
        db=db, user=current_user, scope=body.scope, scope_id=body.scope_id,
        target_is_system=False,
    )
    removed = prompt_registry.clear_override(
        db, scope=body.scope, scope_id=body.scope_id, category=tpl.category,
    )
    log_action(
        db, user=current_user, action="prompt_reset",
        target_type="prompt_template", target_id=tpl.id,
        metadata={
            "scope": body.scope, "scope_id": body.scope_id,
            "category": tpl.category, "removed_override": removed,
        },
    )
    return {"removed_override": removed, "category": tpl.category}


class _PreviewResponse(BaseModel):
    text: str
    bytes: int
    placeholders_used: list[str]
    output_format: str
    template_id: str
    template_name: str


class _DryRunRequest(BaseModel):
    """Body for ``/dry-run``. ``user_message`` is sent as the user
    content; ``context`` (optional) is rendered into the template body
    via the Jinja sandbox. Strict mode is FALSE so a missing field
    renders empty rather than blowing up before the LLM is even called."""

    context: dict = Field(default_factory=dict)
    user_message: str = ""
    qa_mode: str = "salesforce"


class _DryRunResponse(BaseModel):
    """What the editor's dry-run button renders. ``test_cases`` is
    populated for JSON / markdown-table outputs; ``raw`` is always
    present so the user can sanity-check the LLM didn't go off the
    rails."""

    template_id: str
    template_name: str
    output_format: str
    model: str | None
    provider: str | None
    latency_ms: int
    raw: str
    test_cases: list[dict]
    warnings: list[str]


@router.post("/{template_id}/preview", response_model=_PreviewResponse)
def preview_template(
    template_id: str,
    body: _Preview,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tpl = _load_or_404(db, template_id)
    latest = prompt_registry.get_latest_version(db, tpl.id)
    if latest is None:
        raise HTTPException(409, "Template has no versions yet.")
    try:
        rendered = compile(latest.body, body.context or {}, strict=body.strict)
    except PromptCompileError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _PreviewResponse(
        text=rendered.text,
        bytes=rendered.bytes,
        placeholders_used=rendered.placeholders_used,
        output_format=tpl.output_format,
        template_id=tpl.id,
        template_name=tpl.name,
    )


@router.post("/{template_id}/dry-run", response_model=_DryRunResponse)
def dry_run_template(
    template_id: str,
    body: _DryRunRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send the latest version of a template to the LLM and parse the
    response, WITHOUT persisting anything. Used by the editor's
    Preview tab to validate that an edited prompt actually still
    produces parseable output before the user activates it.

    Never raises a successful generation -- parse failures surface as
    a warning + empty ``test_cases`` so the user sees what came back.
    """
    from ai_bridge import call_llm_with_metadata

    from ..services.prompt_output_parser import OutputParseError, parse

    tpl = _load_or_404(db, template_id)
    latest = prompt_registry.get_latest_version(db, tpl.id)
    if latest is None:
        raise HTTPException(409, "Template has no versions yet.")

    try:
        rendered = compile(latest.body, body.context or {}, strict=False)
    except PromptCompileError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Build a minimal user message. If the caller passed one verbatim,
    # use it; otherwise synthesize from the context to give the LLM
    # something to chew on.
    user_msg = body.user_message.strip()
    if not user_msg:
        user_msg = (
            f"Generate based on the provided context.\nQA mode: {body.qa_mode}"
        )

    llm = call_llm_with_metadata(rendered.text, user_msg)
    warnings: list[str] = []
    cases_out: list[dict] = []
    try:
        parsed = parse(llm.text, output_format=tpl.output_format)
        cases_out = [c.model_dump() for c in parsed.test_cases]
        warnings = list(parsed.warnings)
    except OutputParseError as exc:
        warnings = [f"output parse failed ({tpl.output_format}): {exc}"]

    # Record the dry-run in the audit feed so token / latency tracking
    # still counts -- target_type=None signals "no persisted artefact".
    try:
        prompt_registry.record_usage(
            db,
            template_version_id=latest.id,
            category=tpl.category,
            user_id=current_user.id,
            model=llm.model,
            provider=llm.provider,
            qa_mode=body.qa_mode,
            input_tokens=llm.input_tokens,
            output_tokens=llm.output_tokens,
            latency_ms=llm.latency_ms,
            target_type="dry_run",
            target_id=tpl.id,
        )
    except Exception:
        pass

    return _DryRunResponse(
        template_id=tpl.id,
        template_name=tpl.name,
        output_format=tpl.output_format,
        model=llm.model,
        provider=llm.provider,
        latency_ms=llm.latency_ms,
        raw=llm.text,
        test_cases=cases_out,
        warnings=warnings,
    )


@router.delete("/{template_id}")
def delete_template(
    template_id: str,
    permanent: bool = Query(False, description="Admin-only hard delete."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tpl = _load_or_404(db, template_id)
    if tpl.is_system:
        raise HTTPException(403, "System templates cannot be deleted.")
    if tpl.owner_user_id and tpl.owner_user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(403, "Only the owner (or admin) may delete this template.")
    if permanent:
        if not current_user.is_admin:
            raise HTTPException(403, "Permanent delete requires admin.")
        # Hard delete cascades to versions; overrides are RESTRICT so we
        # clear them first.
        db.query(PromptOverride).filter(PromptOverride.template_id == tpl.id).delete()
        db.delete(tpl)
        db.commit()
        prompt_registry.bump_cache_epoch(db)
        action = "prompt_deleted_permanent"
    else:
        prompt_registry.soft_delete_template(db, template=tpl)
        action = "prompt_deleted"
    log_action(
        db, user=current_user, action=action,
        target_type="prompt_template", target_id=tpl.id,
        metadata={"category": tpl.category, "name": tpl.name, "permanent": permanent},
    )
    return {"id": tpl.id, "status": "permanent" if permanent else "soft"}


class _AuditRow(BaseModel):
    id: str
    template_version_id: str | None
    category: str
    user_id: str | None
    project_id: str | None
    model: str | None
    provider: str | None
    qa_mode: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    target_type: str | None
    target_id: str | None
    created_at: str | None


@router.get("/audit", response_model=list[_AuditRow])
def list_audit(
    category: str | None = Query(None),
    user_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read the prompt_usage_audit feed. Non-admins see only their own
    rows; admins can pass ?user_id= to filter."""
    q = db.query(PromptUsageAudit).order_by(PromptUsageAudit.created_at.desc())
    if category:
        q = q.filter(PromptUsageAudit.category == category)
    if not current_user.is_admin:
        q = q.filter(PromptUsageAudit.user_id == current_user.id)
    elif user_id:
        q = q.filter(PromptUsageAudit.user_id == user_id)
    rows = q.limit(limit).all()
    return [_AuditRow(**r.to_dict()) for r in rows]
