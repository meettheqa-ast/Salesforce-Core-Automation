"""Analytics and run history endpoints (project-scoped)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import project_manager
from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.models.schemas import AnalyticsSummary, RunHistoryEntry
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import User
from ai_qa_portal.backend.services.robot_results import (
    parse_output_xml,
    parse_run_times,
)

router = APIRouter(
    prefix="/api/analytics",
    tags=["analytics"],
    dependencies=[Depends(get_current_user)],
)


def _scan_results_dir(results_dir: Path) -> list[RunHistoryEntry]:
    if not results_dir.is_dir():
        return []
    rows: list[RunHistoryEntry] = []
    for d in sorted(results_dir.iterdir(), key=lambda p: p.stat().st_mtime):
        if not d.is_dir():
            continue
        xml = d / "output.xml"
        if not xml.is_file():
            continue
        passed, failed, skipped, duration_s = parse_output_xml(xml)
        # Prefer the real run start time; fall back to mtime when missing.
        started, _finished = parse_run_times(xml)
        timestamp = started or datetime.fromtimestamp(d.stat().st_mtime)
        rows.append(
            RunHistoryEntry(
                run_name=d.name,
                timestamp=timestamp,
                passed=passed,
                failed=failed,
                total=passed + failed + skipped,
                duration_s=duration_s,
            )
        )
    return rows


@router.get("/projects/{name}/summary", response_model=AnalyticsSummary)
def get_analytics_summary(name: str, current_user: User = Depends(get_current_user)):
    """Aggregate Robot Framework runs for a project.

    Looks under ``Saved_Projects/<name>/Results/`` first; if the project has
    no scoped results yet, falls back to the global ``Results/`` directory so
    new-app runs (which currently land there) still show up.
    """
    if not project_manager.project_exists(name):
        raise HTTPException(404, f"Project '{name}' not found")
    owner = project_manager.get_project_owner(name)
    if not current_user.is_admin and owner and owner != current_user.id:
        raise HTTPException(403, "You do not have access to this project")

    proj_dir = project_manager.get_project_path(name)
    entries = _scan_results_dir(proj_dir / "Results")
    if not entries:
        # Fallback: global results dir (new app writes here).
        entries = _scan_results_dir(Path(settings.results_dir))

    total_runs = len(entries)
    total_passed = sum(e.passed for e in entries)
    total_failed = sum(e.failed for e in entries)
    total_tests = total_passed + total_failed
    pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0.0
    # Avg duration over runs that actually have a duration recorded; otherwise
    # the historical zero-duration noise drags it to ~0s and gives the wrong
    # impression on the dashboard.
    timed = [e.duration_s for e in entries if e.duration_s and e.duration_s > 0]
    avg_duration = (sum(timed) / len(timed)) if timed else 0.0

    return AnalyticsSummary(
        total_runs=total_runs,
        total_passed=total_passed,
        total_failed=total_failed,
        pass_rate=round(pass_rate, 1),
        avg_duration_s=round(avg_duration, 1),
        history=entries,
    )
