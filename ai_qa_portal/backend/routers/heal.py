"""Form-healing API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import SessionLocal, User
from ai_qa_portal.backend.services.form_healer import FormHealer

router = APIRouter(
    prefix="/api/heal",
    tags=["heal"],
    dependencies=[Depends(get_current_user)],
)


class HealSaveRequest(BaseModel):
    session_id: str
    sobject: str = ""
    save_action: str = "Save"
    duplicate_strategy: str = "regenerate"
    project_slug: str | None = None
    run_id: str | None = None
    job_id: str | None = None
    step_index: int | None = None
    sandbox_url: str = ""
    username: str = ""
    password: str = ""
    security_token: str = ""


class HealSaveResponse(BaseModel):
    outcome: str
    reason: str = ""
    attempts: int = 0
    healed_fields: list[str] = []
    details: list[dict] = []


@router.post("/save", response_model=HealSaveResponse)
def heal_save(body: HealSaveRequest):
    try:
        engine = FormHealer()
        result = engine.heal_save(
            session_id=body.session_id,
            sobject=(body.sobject or "").strip() or "Unknown",
            save_action=body.save_action or "Save",
            project_slug=body.project_slug,
            run_id=body.run_id,
            job_id=body.job_id,
            step_index=body.step_index,
            duplicate_strategy=body.duplicate_strategy or "regenerate",
            sandbox_url=body.sandbox_url,
            username=body.username,
            password=body.password,
            security_token=body.security_token,
        )
        return HealSaveResponse(
            outcome=result.outcome,
            reason=result.reason,
            attempts=len(result.attempts),
            healed_fields=list(result.healed_fields),
            details=[
                {
                    "attempt_n": a.attempt_n,
                    "save_outcome": a.save_outcome,
                    "latency_ms": a.latency_ms,
                    "errors": [e.raw_text for e in a.raw_errors],
                    "strategies": [d.strategy for d in a.decisions],
                }
                for a in result.attempts
            ],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/events")
def get_heal_events(
    current_user: User = Depends(get_current_user),
    run_id: str | None = Query(None),
    job_id: str | None = Query(None),
    project_slug: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    _ = current_user
    try:
        from ai_qa_portal.backend.services.db_models.heal import HealEvent

        with SessionLocal() as session:
            q = session.query(HealEvent)
            if run_id:
                q = q.filter(HealEvent.run_id == run_id)
            if job_id:
                q = q.filter(HealEvent.generation_job_id == job_id)
            if project_slug:
                q = q.filter(HealEvent.project_slug == project_slug)
            rows = q.order_by(HealEvent.created_at.desc()).limit(limit).all()
            return {
                "events": [
                    {
                        "id": row.id,
                        "project_slug": row.project_slug,
                        "run_id": row.run_id,
                        "generation_job_id": row.generation_job_id,
                        "sobject": row.sobject,
                        "step_index": row.step_index,
                        "attempt_number": row.attempt_number,
                        "error_type": row.error_type,
                        "field_label": row.field_label,
                        "strategy": row.strategy,
                        "outcome": row.outcome,
                        "latency_ms": row.latency_ms,
                        "payload": row.payload,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in rows
                ]
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/aggregate")
def aggregate_heal_feedback(
    current_user: User = Depends(get_current_user),
    project_slug: str | None = Query(None),
    lookback_days: int = Query(90, ge=1, le=365),
):
    is_admin = current_user.is_admin or current_user.global_role == "admin"
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    try:
        from ai_qa_portal.backend.services.heal_feedback import aggregate_org_field_learnings

        return aggregate_org_field_learnings(
            project_slug=project_slug,
            lookback_days=lookback_days,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

