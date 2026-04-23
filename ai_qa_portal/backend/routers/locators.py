"""Locator health scanning endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.models.schemas import LocatorResult, LocatorScanRequest, LocatorScanResponse
from ai_qa_portal.backend.services.auth import get_current_user

router = APIRouter(
    prefix="/api/locators",
    tags=["locators"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/scan", response_model=LocatorScanResponse)
def scan_locators(body: LocatorScanRequest):
    try:
        import locator_validator

        report = locator_validator.run_scan(body.sandbox_url, body.username, body.password)

        results = [
            LocatorResult(
                name=r["name"],
                locator=r.get("locator", ""),
                status=r["status"],
                error=r.get("error"),
            )
            for r in report
        ]
        healthy = sum(1 for r in results if r.status == "FOUND")
        stale = sum(1 for r in results if r.status == "NOT_FOUND")
        skipped = sum(1 for r in results if r.status == "ERROR")

        return LocatorScanResponse(healthy=healthy, stale=stale, skipped=skipped, results=results)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/list")
def list_locators():
    try:
        import locator_validator
        from ai_qa_portal.backend.config import REPO_ROOT

        locators_file = REPO_ROOT / "Resources" / "Common" / "GlobalLocators.robot"
        parsed = locator_validator.parse_locators_file(str(locators_file))
        return {"locators": parsed, "count": len(parsed)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
