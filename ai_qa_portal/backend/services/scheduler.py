"""APScheduler wrapper used by the portal for ``runner='local'`` schedules.

Jobs are persisted to Postgres via ``SQLAlchemyJobStore`` so the scheduler
survives restarts. We register one in-process scheduler at FastAPI
startup; routers call :func:`add_or_replace`, :func:`pause`,
:func:`resume`, and :func:`delete` to manage jobs.

``runner='github_actions'`` schedules are NOT registered here -- their
cron lives in the connected repo's workflow YAML. We track them in the
DB for visibility and "Run now" still works via
:func:`schedule_runner.execute`.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.db import _is_postgres, _is_sqlite, engine
from ai_qa_portal.backend.services.db_models.schedules import (
    Schedule,
    ScheduleRunner,
)

logger = logging.getLogger("ai_qa_portal.scheduler")


# APScheduler is an optional dependency: the rest of the portal must
# import cleanly even when it isn't installed (e.g. CLI-only deploys, or
# the dev environment before a fresh `pip install -r requirements.txt`).
# We import lazily so the failure surfaces only on the first scheduler
# call, with a clear message pointing at the install step.
try:
    from apscheduler.executors.pool import ThreadPoolExecutor as APThreadPoolExecutor
    from apscheduler.jobstores.memory import MemoryJobStore
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _APSCHEDULER_AVAILABLE = True
    _APSCHEDULER_IMPORT_ERROR: str | None = None
except ImportError as _exc:  # pragma: no cover -- exercised on dep-missing hosts
    _APSCHEDULER_AVAILABLE = False
    _APSCHEDULER_IMPORT_ERROR = str(_exc)
    BackgroundScheduler = Any  # type: ignore[assignment,misc]


_scheduler: Optional[Any] = None
_lock = threading.Lock()


def _require_apscheduler() -> None:
    if not _APSCHEDULER_AVAILABLE:
        raise RuntimeError(
            "APScheduler is not installed. Run `pip install -r ai_qa_portal/requirements.txt` "
            f"or set SCHEDULER_ENABLED=false to disable in-process scheduling. "
            f"(Import error: {_APSCHEDULER_IMPORT_ERROR})"
        )


def _build_scheduler() -> Any:
    _require_apscheduler()
    # Persisted jobstore so a deploy doesn't lose schedules. APScheduler
    # creates its own ``apscheduler_jobs`` table; nothing for the portal
    # to migrate by hand.
    if _is_postgres(str(engine.url)):
        jobstore = SQLAlchemyJobStore(engine=engine, tablename="apscheduler_jobs")
    elif _is_sqlite(str(engine.url)):
        # SQLite + APScheduler is fine for dev. The same ``users.db`` file
        # carries the jobs table.
        jobstore = SQLAlchemyJobStore(engine=engine, tablename="apscheduler_jobs")
    else:
        # Unknown driver -> in-memory fallback. Schedules WILL be lost on
        # restart in this mode; loud warning so the operator notices.
        logger.warning(
            "Unknown DB dialect %s; using in-memory APScheduler jobstore. "
            "Schedules will not survive restarts.",
            engine.dialect.name,
        )
        jobstore = MemoryJobStore()

    return BackgroundScheduler(
        jobstores={"default": jobstore},
        executors={"default": APThreadPoolExecutor(max_workers=max(1, settings.scheduler_max_workers))},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
        timezone="UTC",
    )


def start() -> None:
    """Idempotent start. Called from FastAPI's startup hook."""
    global _scheduler  # noqa: PLW0603 -- singleton at module scope
    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=false")
        return
    if not _APSCHEDULER_AVAILABLE:
        logger.warning(
            "Scheduler requested but APScheduler is not installed; skipping. "
            "Install requirements or set SCHEDULER_ENABLED=false to silence."
        )
        return
    with _lock:
        if _scheduler is not None and _scheduler.running:
            return
        _scheduler = _build_scheduler()
        _scheduler.start()
        logger.info("APScheduler started (jobstore=%s)", type(_scheduler._jobstores["default"]).__name__)


def shutdown() -> None:
    global _scheduler  # noqa: PLW0603
    with _lock:
        if _scheduler is not None and getattr(_scheduler, "running", False):
            _scheduler.shutdown(wait=False)
        _scheduler = None


def _get() -> Any:
    _require_apscheduler()
    if _scheduler is None or not _scheduler.running:
        start()
    assert _scheduler is not None
    return _scheduler


def _job_id(schedule_id: str) -> str:
    return f"schedule:{schedule_id}"


def add_or_replace(schedule: Schedule) -> None:
    """Register / re-register a schedule. ``github_actions`` schedules
    are no-ops here -- their cron lives in the connected repo's workflow
    YAML."""
    if schedule.runner == ScheduleRunner.github_actions.value:
        return
    if not _APSCHEDULER_AVAILABLE or not settings.scheduler_enabled:
        return
    if not schedule.enabled:
        delete(schedule.id)
        return
    sched = _get()
    try:
        trigger = CronTrigger.from_crontab(schedule.cron, timezone=schedule.timezone or "UTC")
    except ValueError as exc:
        logger.warning("Invalid cron %r for schedule %s: %s", schedule.cron, schedule.id, exc)
        return
    sched.add_job(
        func="ai_qa_portal.backend.services.schedule_runner:execute",
        args=[schedule.id],
        trigger=trigger,
        id=_job_id(schedule.id),
        replace_existing=True,
        name=schedule.name,
    )


def pause(schedule_id: str) -> None:
    if not _APSCHEDULER_AVAILABLE:
        return
    sched = _get()
    try:
        sched.pause_job(_job_id(schedule_id))
    except Exception:  # noqa: BLE001 -- pausing a non-existent job is fine
        pass


def resume(schedule_id: str) -> None:
    if not _APSCHEDULER_AVAILABLE:
        return
    sched = _get()
    try:
        sched.resume_job(_job_id(schedule_id))
    except Exception:  # noqa: BLE001
        pass


def delete(schedule_id: str) -> None:
    if not _APSCHEDULER_AVAILABLE:
        return
    sched = _get()
    try:
        sched.remove_job(_job_id(schedule_id))
    except Exception:  # noqa: BLE001 -- already absent
        pass


def reload_all(db: Session) -> int:
    """Re-register every enabled local schedule from the DB. Called on
    startup and from the admin 're-sync schedules' endpoint."""
    if not settings.scheduler_enabled:
        return 0
    rows = (
        db.query(Schedule)
        .filter(Schedule.runner == ScheduleRunner.local.value, Schedule.enabled.is_(True))
        .all()
    )
    for s in rows:
        add_or_replace(s)
    return len(rows)
