"""Visual regression API endpoints used by frontend panels."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.visual_regression import list_pending_baselines, promote_current_to_baseline

router = APIRouter(
    prefix="/api/visual-regression",
    tags=["visual_regression"],
    dependencies=[Depends(get_current_user)],
)


def _ensure_enabled() -> None:
    if not getattr(settings, "pw_visual_regression", False):
        raise HTTPException(403, "Visual regression feature is not enabled")


@router.get("/{project_slug}/pending-baselines")
def pending_baselines(project_slug: str):
    _ensure_enabled()
    return list_pending_baselines(project_slug)


@router.post("/{project_slug}/promote-baseline")
def promote_baseline(project_slug: str, body: dict):
    _ensure_enabled()
    test_case_id = str(body.get("test_case_id") or "").strip()
    if not test_case_id:
        raise HTTPException(422, "test_case_id is required")
    step_label = str(body.get("step_label") or "default").strip() or "default"
    ok = promote_current_to_baseline(project_slug, test_case_id, step_label)
    if not ok:
        raise HTTPException(404, "No current screenshot found to promote")
    return {"ok": True, "test_case_id": test_case_id, "step_label": step_label}


@router.get("/{project_slug}/screenshot")
def screenshot(
    project_slug: str,
    kind: str = Query(..., pattern="^(baseline|current|diff)$"),
    filename: str = Query(...),
    run_folder: str | None = Query(None),
):
    _ensure_enabled()
    root = Path(settings.saved_projects_dir) / project_slug / "screenshots"
    if kind == "diff":
        if not run_folder:
            raise HTTPException(422, "run_folder is required when kind=diff")
        path = root / "diffs" / run_folder / filename
    elif kind == "baseline":
        path = root / "baselines" / filename
    else:
        path = root / "current" / filename
    if not path.is_file():
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(path=str(path), media_type="image/png")
