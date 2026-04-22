"""Analytics and run history endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.models.schemas import AnalyticsSummary, RunHistoryEntry

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/projects/{name}/summary", response_model=AnalyticsSummary)
def get_analytics_summary(name: str):
    try:
        from app_analytics import load_project_run_history

        history = load_project_run_history(name)
        entries = [
            RunHistoryEntry(
                run_name=r.run_name,
                timestamp=r.timestamp,
                passed=r.passed,
                failed=r.failed,
                total=r.total,
                duration_s=r.elapsed_s,
            )
            for r in history
        ]
        total_passed = sum(e.passed for e in entries)
        total_failed = sum(e.failed for e in entries)
        total_runs = len(entries)
        total_tests = total_passed + total_failed
        pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0.0
        avg_duration = sum(e.duration_s for e in entries) / total_runs if total_runs > 0 else 0.0

        return AnalyticsSummary(
            total_runs=total_runs,
            total_passed=total_passed,
            total_failed=total_failed,
            pass_rate=round(pass_rate, 1),
            avg_duration_s=round(avg_duration, 1),
            history=entries,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
