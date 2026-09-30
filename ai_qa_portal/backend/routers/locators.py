"""Locator health scanning endpoints."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.models.schemas import (
    LocatorResult,
    LocatorScanRequest,
    LocatorScanResponse,
)
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import User, get_db

logger = logging.getLogger("ai_qa_portal.locators")

router = APIRouter(
    prefix="/api/locators",
    tags=["locators"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/scan", response_model=LocatorScanResponse)
def scan_locators(
    body: LocatorScanRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Scan GlobalLocators.robot against a live Salesforce org.

    On scan completion (success OR failure) we emit a
    ``locator.scanned`` event into the unified events table so the
    Settings -> Infrastructure -> Locator Health card can render
    "last scan" telemetry without each viewer re-running the scan.
    The emit is best-effort; an audit failure never blocks the
    primary response. See ``GET /api/locators/status`` for the read
    path the Settings card uses.
    """
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

        # Emit a persisted telemetry row so the Settings card can show
        # "last scan: 2h ago" without forcing a re-scan on every load.
        # Sandbox URL is recorded; password / username are NOT, by
        # design -- this row is visible in the audit feed.
        _emit_scan_event(
            db=db,
            actor_user_id=str(current_user.id),
            sandbox_url=body.sandbox_url,
            healthy=healthy,
            stale=stale,
            failed=skipped,
            error_message=None,
        )

        return LocatorScanResponse(healthy=healthy, stale=stale, skipped=skipped, results=results)
    except HTTPException:
        raise
    except Exception as exc:
        # Emit a failure event too so the Settings card can render the
        # FAIL state instead of pretending the last successful scan
        # is current. Best-effort.
        try:
            _emit_scan_event(
                db=db,
                actor_user_id=str(current_user.id),
                sandbox_url=body.sandbox_url,
                healthy=0,
                stale=0,
                failed=0,
                error_message=str(exc)[:500],
            )
        except Exception:
            pass
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


# ---------- Locator scan telemetry -------------------------------


class LocatorStatusResponse(BaseModel):
    """Settings -> Locator Health card payload. Returned by
    ``GET /api/locators/status`` and described in the IA refactor
    Phase 5 plan.

    ``last_scan_status`` rules:
      * ``NEVER_RUN`` when no ``locator.scanned`` event exists yet.
      * ``FAIL`` when the last event recorded a ``scan_error``.
      * ``STALE`` when the last successful scan reported any stale
        locators (``stale_count > 0``).
      * ``PASS`` otherwise (the scan completed AND every locator
        resolved).
    """

    last_scan_at: str | None = None
    last_scan_status: Literal["PASS", "STALE", "FAIL", "NEVER_RUN"]
    healthy_count: int = 0
    stale_count: int = 0
    failed_count: int = 0
    sandbox_url: str | None = None
    actor_user_id: str | None = None
    scan_error: str | None = None


@router.get("/status", response_model=LocatorStatusResponse)
def get_locator_status(
    current_user: User = Depends(get_current_user),  # noqa: ARG001 -- gate only
    db: Session = Depends(get_db),
):
    """Return the latest ``locator.scanned`` event for the Settings
    card. Returns ``NEVER_RUN`` shape when no scan has run.

    Reads from the unified events table (Alembic 0008) rather than
    introducing a new locator_scans table -- locator scans run
    manually a few times a year, so the events row is plenty of
    durable history without a dedicated schema."""
    # Lazy import keeps this router cheap to load on cold start.
    from ai_qa_portal.backend.services.db_models.events import Event

    latest = (
        db.query(Event)
        .filter(Event.action == "locator.scanned")
        .order_by(Event.created_at.desc())
        .first()
    )
    if latest is None:
        return LocatorStatusResponse(last_scan_status="NEVER_RUN")

    meta = latest.metadata_json or {}
    scan_error = meta.get("scan_error")
    healthy = int(meta.get("healthy_count", 0) or 0)
    stale = int(meta.get("stale_count", 0) or 0)
    failed = int(meta.get("failed_count", 0) or 0)
    if scan_error:
        status = "FAIL"
    elif stale > 0:
        status = "STALE"
    else:
        status = "PASS"
    return LocatorStatusResponse(
        last_scan_at=latest.created_at.isoformat() if latest.created_at else None,
        last_scan_status=status,
        healthy_count=healthy,
        stale_count=stale,
        failed_count=failed,
        sandbox_url=meta.get("sandbox_url"),
        actor_user_id=latest.actor_user_id,
        scan_error=scan_error,
    )


# ---------- internals --------------------------------------------


def _emit_scan_event(
    *,
    db: Session,
    actor_user_id: str | None,
    sandbox_url: str,
    healthy: int,
    stale: int,
    failed: int,
    error_message: str | None,
) -> None:
    """Best-effort writer for the locator.scanned event row.

    Wrapper exists so the success + failure paths in ``scan_locators``
    don't duplicate the emit_event payload construction. Failure to
    persist the event NEVER blocks the scan response -- the
    underlying emit_event already swallows SQL errors.
    """
    try:
        from ai_qa_portal.backend.services.event_stream import emit_event

        summary = (
            f"scan failed: {error_message[:80]}"
            if error_message
            else f"{healthy} healthy, {stale} stale, {failed} failed"
        )
        metadata: dict = {
            "healthy_count": healthy,
            "stale_count": stale,
            "failed_count": failed,
            # Sandbox URL is identifying but not secret; helps disambiguate
            # multiple sandboxes when a team scans more than one.
            "sandbox_url": sandbox_url or None,
            "scanned_at_utc": datetime.now(UTC).isoformat(),
        }
        if error_message:
            metadata["scan_error"] = error_message
        emit_event(
            db,
            kind="infra",
            action="locator.scanned",
            actor_user_id=actor_user_id,
            target_type="locator_library",
            target_id="GlobalLocators.robot",
            summary=summary,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001 -- never block the response
        logger.warning("locator scan event emit failed: %s", exc)
