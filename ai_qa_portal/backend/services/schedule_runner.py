"""Execute one ``Schedule`` row.

Two paths:

* ``runner=local``        -- resolve target -> build/lookup ``.robot`` ->
  invoke ``robot`` via ``run_test.build_robot_run`` -> record a row in
  the existing ``runs`` table -> mirror status into ``schedule_runs``.
* ``runner=github_actions`` -- look up the connected repo, decrypt the
  GitHub credentials, fire ``workflow_dispatch`` with the right inputs.
  The webhook handler later updates ``schedule_runs`` when the run
  finishes.

This module is deliberately kept narrow: it relies on the existing
``TestCaseScriptBuilder``, ``run_test.build_robot_run``, and the
``runs`` table so we don't fork the execution pipeline.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.services.credential_service import CredentialService
from ai_qa_portal.backend.services.db import RunRecord, SessionLocal
from ai_qa_portal.backend.services.db_models.github import GitHubConnection, GitHubRepo
from ai_qa_portal.backend.services.db_models.schedules import (
    Schedule,
    ScheduleRun,
    ScheduleRunner,
    ScheduleStatus,
    ScheduleTargetKind,
)

logger = logging.getLogger("ai_qa_portal.schedule_runner")


def execute(schedule_id: str) -> ScheduleRun:
    """Run one schedule end-to-end. Opens its own DB session so it can be
    invoked from APScheduler's executor (which runs outside of any
    request scope)."""
    db: Session = SessionLocal()
    try:
        schedule = db.query(Schedule).filter(Schedule.id == schedule_id).one_or_none()
        if schedule is None:
            raise RuntimeError(f"schedule {schedule_id} not found")
        if not schedule.enabled:
            logger.info("Skipping disabled schedule %s", schedule_id)
            return ScheduleRun(
                schedule_id=schedule_id,
                runner=schedule.runner,
                status=ScheduleStatus.cancelled.value,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )

        run_row = ScheduleRun(
            schedule_id=schedule.id,
            runner=schedule.runner,
            status=ScheduleStatus.running.value,
        )
        db.add(run_row)
        db.commit()
        db.refresh(run_row)

        try:
            if schedule.runner == ScheduleRunner.github_actions.value:
                _execute_github_actions(db, schedule=schedule, run_row=run_row)
            else:
                _execute_local(db, schedule=schedule, run_row=run_row)
        except Exception as exc:  # noqa: BLE001 -- we capture and surface
            logger.exception("Schedule %s execution failed", schedule.id)
            run_row.status = ScheduleStatus.error.value
            run_row.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            run_row.finished_at = datetime.now(UTC)
            db.commit()

        schedule.last_run_at = datetime.now(UTC)
        db.commit()
        db.refresh(run_row)
        return run_row
    finally:
        db.close()


# ---- local --------------------------------------------------------------

def _execute_local(db: Session, *, schedule: Schedule, run_row: ScheduleRun) -> None:
    """Resolve target -> robot suites -> spawn ``robot`` -> record."""
    from ai_qa_portal.backend.services.test_case_script_builder import TestCaseScriptBuilder
    from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend

    store = JsonFileBackend(settings.data_dir)
    builder = TestCaseScriptBuilder()

    # Build the list of .robot suites to run.
    suite_paths = _collect_suite_paths(store, builder, schedule=schedule, db=db)
    if not suite_paths:
        run_row.status = ScheduleStatus.failed.value
        run_row.error_message = "No runnable test cases found for target"
        run_row.finished_at = datetime.now(UTC)
        db.commit()
        return

    # Resolve persona + org for credentials. If not pinned on the schedule,
    # the run fails early -- scheduled runs need deterministic auth.
    persona, password, login_url = _resolve_credentials(store, schedule)
    if persona is None:
        run_row.status = ScheduleStatus.failed.value
        run_row.error_message = "Schedule has no persona / org configured"
        run_row.finished_at = datetime.now(UTC)
        db.commit()
        return

    # Create a RunRecord so the existing /runs UI surfaces this execution.
    run_record_id = str(uuid4())
    run_folder = f"schedule_{schedule.id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    db.add(
        RunRecord(
            id=run_record_id,
            project_slug=schedule.project_slug,
            run_folder=run_folder,
            triggered_by_user_id=schedule.created_by_user_id,
            persona_used_id=schedule.persona_id,
            persona_owner_at_time=schedule.persona_id,
            status="started",
        )
    )
    run_row.local_run_id = run_record_id
    db.commit()

    # Build & dispatch the robot CLI invocation. We use the project-level
    # run_test helper so behaviour matches the manual run path.
    sys.path.insert(0, str(Path(settings.data_dir).parent.parent))
    try:
        import run_test  # type: ignore[import-not-found]
    except ImportError as exc:
        run_row.status = ScheduleStatus.error.value
        run_row.error_message = f"run_test module unavailable: {exc}"
        run_row.finished_at = datetime.now(UTC)
        db.commit()
        return

    output_dir = Path(settings.results_dir) / run_folder
    output_dir.mkdir(parents=True, exist_ok=True)

    # Robot accepts either a single suite or a directory. Materialise a
    # transient parent dir if we have >1 suite.
    if len(suite_paths) == 1:
        test_target = suite_paths[0]
    else:
        test_target = output_dir / "suites"
        test_target.mkdir(parents=True, exist_ok=True)
        for i, src in enumerate(suite_paths):
            (test_target / f"suite_{i:03d}.robot").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    cmd, _ = run_test.build_robot_run(
        sandbox_url=login_url,
        username=persona.get("username", ""),
        password=password,
        test_path=test_target,
        output_dir=output_dir,
        headless=True,
    )

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        run_row.status = ScheduleStatus.passed.value
    else:
        run_row.status = ScheduleStatus.failed.value
        run_row.error_message = (proc.stderr or proc.stdout or "")[:2000]

    run_row.finished_at = datetime.now(UTC)
    run_row.result_summary = {
        "return_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-1500:],
        "output_dir": str(output_dir),
    }
    rec = db.query(RunRecord).filter(RunRecord.id == run_record_id).one_or_none()
    if rec is not None:
        rec.finished_at = datetime.now(UTC)
        rec.status = "passed" if proc.returncode == 0 else "failed"
    db.commit()


# ---- GitHub Actions -----------------------------------------------------

def _execute_github_actions(db: Session, *, schedule: Schedule, run_row: ScheduleRun) -> None:
    if not schedule.github_repo_id:
        raise ValueError("Schedule has runner='github_actions' but no github_repo_id")
    repo = db.query(GitHubRepo).filter(GitHubRepo.id == schedule.github_repo_id).one_or_none()
    if repo is None:
        raise ValueError(f"github_repo {schedule.github_repo_id} not found")
    connection = db.query(GitHubConnection).filter(GitHubConnection.id == repo.connection_id).one_or_none()
    if connection is None:
        raise ValueError("GitHub connection missing for linked repo")

    suite_path = _suite_path_for_github(schedule, repo)
    persona_username = _persona_username_for_schedule(schedule) or ""

    from ai_qa_portal.backend.services.github_sync import build_provider
    provider = build_provider(connection)
    workflow_filename = Path(repo.workflow_path).name
    with provider:
        provider.trigger_workflow_dispatch(
            owner=repo.owner,
            repo=repo.name,
            workflow_id=workflow_filename,
            ref=repo.default_branch,
            inputs={
                "suite_path": suite_path,
                "persona_username": persona_username,
                "schedule_run_id": run_row.id,
            },
        )
    # The workflow_run webhook will close out the row when the job
    # finishes. We leave status='running' until then.


# ---- helpers ------------------------------------------------------------

def _collect_suite_paths(store, builder, *, schedule: Schedule, db: Session) -> list[Path]:
    """Resolve the schedule's target into a list of on-disk .robot files,
    building any case that doesn't already have a materialised script."""
    paths: list[Path] = []
    target_id = schedule.target_id

    def _ensure_built(tc_row: dict) -> Path | None:
        from ai_qa_portal.backend.models.test_case import TestCase
        tc = TestCase.model_validate(tc_row)
        if tc.script_path:
            candidate = Path(settings.data_dir).parent.parent / tc.script_path
            if candidate.is_file():
                return candidate
        try:
            source = builder.build_robot_script(tc, persona=None, org=None, db=db, project_slug=schedule.project_slug)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping test case %s during schedule build: %s", tc.id, exc)
            return None
        target = Path(settings.output_dir) / "scheduled_suites"
        target.mkdir(parents=True, exist_ok=True)
        out = target / f"case_{tc.id.hex[:12]}.robot"
        out.write_text(source, encoding="utf-8")
        return out

    if schedule.target_kind == ScheduleTargetKind.test_case.value:
        try:
            tc_row = store.get_test_case(UUID(target_id))
        except KeyError:
            return []
        path = _ensure_built(tc_row)
        if path:
            paths.append(path)
    elif schedule.target_kind == ScheduleTargetKind.story.value:
        for tc_row in store.get_test_cases_by_story(UUID(target_id)):
            path = _ensure_built(tc_row)
            if path:
                paths.append(path)
    elif schedule.target_kind == ScheduleTargetKind.sprint.value:
        for story_row in store.get_user_stories_by_sprint(UUID(target_id)):
            for tc_row in store.get_test_cases_by_story(UUID(story_row["id"])):
                path = _ensure_built(tc_row)
                if path:
                    paths.append(path)
    elif schedule.target_kind == ScheduleTargetKind.tag.value:
        # Tag lookup requires the project id; we read from the schedule's
        # project_slug -> uuid registry.
        from ai_qa_portal.backend.project_registry import ensure_project_uuid
        pid = ensure_project_uuid(schedule.project_slug)
        for tc_row in store.get_test_cases_by_tag(pid, target_id):
            path = _ensure_built(tc_row)
            if path:
                paths.append(path)
    return paths


def _suite_path_for_github(schedule: Schedule, repo: GitHubRepo) -> str:
    """Translate the schedule target into the repo-relative suite path
    the workflow's ``suite_path`` input expects. Test-case targets map
    to the deterministic ``case_<hex12>.robot`` filename produced by
    ``github_sync.push_project_suites``. Story / sprint / tag targets
    point at the parent directory so Robot runs every suite in it."""
    root = repo.suites_root_path.rstrip("/")
    if schedule.target_kind == ScheduleTargetKind.test_case.value:
        return f"{root}/case_{schedule.target_id[:12]}.robot"
    if schedule.target_kind == ScheduleTargetKind.story.value:
        return f"{root}/story_{schedule.target_id[:12]}"
    if schedule.target_kind == ScheduleTargetKind.sprint.value:
        return f"{root}/sprint_{schedule.target_id[:12]}"
    return root


def _resolve_credentials(store, schedule: Schedule) -> tuple[dict | None, str, str]:
    """Look up persona + org for a local run. Returns (persona_row,
    plaintext_password, login_url). ``persona_row`` is None when the
    schedule has no persona pinned."""
    if not schedule.persona_id:
        return None, "", ""
    items = store.read("personas").get("items", [])
    persona = next((p for p in items if str(p.get("id")) == str(schedule.persona_id)), None)
    if persona is None:
        return None, "", ""
    orgs = store.read("orgs").get("items", [])
    org = (
        next((o for o in orgs if str(o.get("id")) == str(schedule.org_id)), None)
        if schedule.org_id
        else None
    )
    login_url = (org or {}).get("login_url", "") if org else ""
    cred_svc = CredentialService(settings.fernet_key or None)
    encrypted = persona.get("encrypted_password") or ""
    password = ""
    if encrypted:
        try:
            password = cred_svc.decrypt(encrypted)
        except Exception:  # noqa: BLE001 -- empty password caught by runner
            password = ""
    return persona, password, login_url


def _persona_username_for_schedule(schedule: Schedule) -> str | None:
    from ai_qa_portal.backend.storage.json_file_backend import JsonFileBackend
    store = JsonFileBackend(settings.data_dir)
    if not schedule.persona_id:
        return None
    items = store.read("personas").get("items", [])
    p = next((p for p in items if str(p.get("id")) == str(schedule.persona_id)), None)
    return (p or {}).get("username")
