from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor  # pylint: disable=no-name-in-module
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ai_qa_portal.backend.config import REPO_ROOT, settings

from ..models.org import SalesforceOrg
from ..models.persona import RunRequest, RunResponse
from ..models.test_case import TestCase, TestCaseStatus
from ..models.user_story import UserStory
from ..prompts import assembler as _assembler
from ..routers.personas import load_personas_for_user
from ..services.audit import log_action
from ..services.auth import get_current_user
from ..services.credential_service import CredentialService
from ..services.db import (
    RunRecord,
    User,
    get_db,
    list_memberships_for_user,
)
from ..services.failure_diagnoser import (
    diagnose_run,
)
from ..services.failure_diagnoser import (
    format_for_prompt as format_diag_for_prompt,
)
from ..services.persona_resolver import PersonaResolver
from ..services.robot_results import (
    parse_output_xml as _shared_parse_output_xml,
)
from ..services.robot_results import (
    parse_robot_ts as _shared_parse_robot_ts,
)
from ..services.script_runner import ScriptRunner
from ..services.test_case_script_builder import TestCaseScriptBuilder
from ..storage.json_file_backend import JsonFileBackend

RESULTS_ROOT = Path(settings.results_dir)
RESULTS_ROOT.mkdir(parents=True, exist_ok=True)

# Phase 1 isolation note: runs are stored as filesystem folders keyed only by
# timestamp. We require auth on every endpoint but do NOT filter run history by
# user yet -- every logged-in user sees all runs. Per-user run filtering will
# be added in Phase 2 alongside project memberships (runs inherit project
# membership). Tracked at the top of routers/runs.py.
router = APIRouter(
    prefix="/run",
    tags=["runs"],
    dependencies=[Depends(get_current_user)],
)
exec_router = APIRouter(
    prefix="/api/runs",
    tags=["runs"],
    dependencies=[Depends(get_current_user)],
)
_resolver = PersonaResolver()
_runner = ScriptRunner(settings.output_dir)
_store = JsonFileBackend(settings.data_dir)
_script_builder = TestCaseScriptBuilder()
logger = logging.getLogger("ai_qa_portal.runs")


def _get_org(org_id):
    orgs = _store.read("orgs").get("items", [])
    return next((o for o in orgs if str(o["id"]) == str(org_id)), None)


def _resolve_or_build_script(
    tc: TestCase,
    persona,
    org_model: SalesforceOrg,
    fallback_dir: Path,
) -> Path:
    """Pick a `.robot` file to run for this test case.

    Order of preference:
      1. `tc.script_path` materialised by /user-stories/{id}/build-scripts --
         user has already inspected it, no LLM cost on every run.
      2. Inline build via `TestCaseScriptBuilder` (legacy behaviour) written
         under `fallback_dir`.

    The saved script is always preferred when present and inside the repo.
    """
    if tc.script_path:
        candidate = (REPO_ROOT / tc.script_path).resolve()
        try:
            candidate.relative_to(REPO_ROOT.resolve())
        except ValueError:
            candidate = None
        if candidate and candidate.is_file():
            return candidate
    robot = _script_builder.build_robot_script(tc, persona, org_model)
    path = fallback_dir / f"story_{tc.id.hex[:12]}.robot"
    path.write_text(robot, encoding="utf-8")
    return path


@router.post("", response_model=RunResponse)
async def trigger_run(
    body: RunRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    # Restrict the resolver to personas this user is allowed to use, so
    # nobody can trigger a run with somebody else's encrypted SF credentials.
    all_personas = load_personas_for_user(current_user)

    try:
        persona, method = _resolver.resolve(
            project_id=body.project_id,
            org_id=body.org_id,
            prompt=body.prompt,
            ui_persona_id=body.persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    org = _get_org(body.org_id)
    if not org:
        raise HTTPException(404, f"Org {body.org_id} not found")

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)

    run_id = uuid4()

    background_tasks.add_task(
        _execute_in_background,
        run_id=run_id,
        login_url=org["login_url"],
        username=persona.username,
        password=password,
        prompt=body.prompt,
    )

    return RunResponse(
        run_id=run_id,
        resolved_persona=persona.name,
        resolution_method=method,
        status="started",
        log_url=f"/outputs/run_{run_id.hex[:8]}",
    )


class RunUserStoryBody(BaseModel):
    org_id: UUID
    persona_id: UUID | None = None


class RunTagBody(BaseModel):
    project_id: UUID
    org_id: UUID
    persona_id: UUID | None = None


@router.post("/user-story/{story_id}", response_model=list[RunResponse])
def run_tests_for_user_story(
    story_id: UUID,
    body: RunUserStoryBody,
    current_user: User = Depends(get_current_user),
):
    sid = story_id
    org_id = body.org_id
    persona_id = body.persona_id

    try:
        row = _store.get_user_story(sid)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    story = UserStory.model_validate(row)
    project_id = story.project_id

    tcs_raw = _store.get_test_cases_by_story(sid)
    tcs = [TestCase.model_validate(r) for r in tcs_raw]
    tcs = [t for t in tcs if t.status == TestCaseStatus.approved and not t.stale]
    if not tcs:
        raise HTTPException(400, "No approved non-stale test cases for this story")

    org = _get_org(org_id)
    if not org:
        raise HTTPException(404, f"Org {org_id} not found")
    org_model = SalesforceOrg(**org)

    all_personas = load_personas_for_user(current_user)
    prompt = f"Execute automated tests for user story: {story.title}"
    try:
        persona, method = _resolver.resolve(
            project_id=project_id,
            org_id=org_id,
            prompt=prompt,
            ui_persona_id=persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)

    out: list[RunResponse] = []
    gen_dir = REPO_ROOT / "Tests" / "Generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    for tc in tcs:
        path = _resolve_or_build_script(tc, persona, org_model, gen_dir)
        run_id, _log = _runner.run(
            str(path.resolve()),
            persona.username,
            password,
            org_model.login_url,
        )
        out.append(
            RunResponse(
                run_id=run_id,
                resolved_persona=persona.name,
                resolution_method=method,
                status="started",
                log_url=f"/outputs/run_{run_id.hex[:8]}",
            )
        )
    return out


@router.post("/tag/{tag_name}", response_model=list[RunResponse])
def run_tests_for_tag(
    tag_name: str,
    body: RunTagBody,
    current_user: User = Depends(get_current_user),
):
    project_id = body.project_id
    org_id = body.org_id
    persona_id = body.persona_id

    tcs_raw = _store.get_test_cases_by_tag(project_id, tag_name)
    tcs = [TestCase.model_validate(r) for r in tcs_raw if not r.get("stale")]
    if not tcs:
        raise HTTPException(400, f"No approved non-stale test cases tagged {tag_name!r}")

    org = _get_org(org_id)
    if not org:
        raise HTTPException(404, f"Org {org_id} not found")
    org_model = SalesforceOrg(**org)

    all_personas = load_personas_for_user(current_user)
    prompt = f"Run tests tagged {tag_name}"
    try:
        persona, method = _resolver.resolve(
            project_id=project_id,
            org_id=org_id,
            prompt=prompt,
            ui_persona_id=persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)

    out: list[RunResponse] = []
    gen_dir = REPO_ROOT / "Tests" / "Generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    for tc in tcs:
        path = _resolve_or_build_script(tc, persona, org_model, gen_dir)
        run_id, _log = _runner.run(
            str(path.resolve()),
            persona.username,
            password,
            org_model.login_url,
        )
        out.append(
            RunResponse(
                run_id=run_id,
                resolved_persona=persona.name,
                resolution_method=method,
                status="started",
                log_url=f"/outputs/run_{run_id.hex[:8]}",
            )
        )
    return out


# --- Parallel bulk execution with live SSE -----------------------------------
#
# Why custom executor instead of pabot:
#   pabot manages its own parallelism and produces a consolidated output, but
#   it's awkward to interleave per-suite stdout into a tagged SSE stream that
#   the UI can use to drive per-test cards + a live progress bar. A
#   ThreadPoolExecutor + per-test subprocess gives us exactly that, with the
#   cost of merging summaries ourselves at the end.
#
# Concurrency cap:
#   BULK_RUN_CONCURRENCY env var, default 3. UI Selenium runs are heavy
#   (each spawns Chromium); bumping this past machine memory will thrash.

_BULK_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _bulk_run_concurrency() -> int:
    raw = (os.getenv("BULK_RUN_CONCURRENCY") or "").strip()
    if not raw:
        return 3
    try:
        n = int(raw)
    except ValueError:
        return 3
    return max(1, min(n, 16))


def _bulk_slug(text: str, max_len: int = 32) -> str:
    s = _BULK_SLUG_RE.sub("_", text.lower()).strip("_")
    return (s or "case")[:max_len]


def _build_bulk_robot_cmd(
    test_path: Path,
    out_dir: Path,
    sandbox_url: str,
    username: str,
    password: str,
    default_app: str = "",
) -> list[str]:
    """Same shape as `_build_robot_cmd` but tuned for bulk: always uses the
    container/headless override path, no tag include/exclude (bulk runs
    intentionally take the test list verbatim from the caller).

    `default_app` (when non-empty) is injected as `${salesAutomationAppName}`
    so PO keywords pick the right Salesforce app for the persona's license.
    """
    cmd = [
        sys.executable, "-m", "robot",
        "--outputdir", str(out_dir),
        "--variable", f"globalSandboxTestUrl:{sandbox_url}",
        "--variable", f"sandboxUserNameInput:{username}",
        "--variable", f"sandboxPasswordInput:{password}",
    ]
    if _effective_headless(True):
        cmd.extend(["--variable", "headless:true"])
    cmd.extend(_container_browser_overrides())
    cmd.extend(_persona_robot_overrides(default_app))
    cmd.append(str(test_path))
    return cmd


def _bulk_event_stream(
    *,
    label: str,
    tcs: list[TestCase],
    persona,
    org_model: SalesforceOrg,
    password: str,
    auto_heal: bool = False,
    max_heal_attempts: int = 1,
):
    """Engine generator. Drives parallel Robot subprocesses, streams SSE
    events tagged with each test case's id.

    Auto-heal: when a test fails its first attempt and ``auto_heal=True``,
    the worker thread feeds the failure back to the LLM (same path as the
    /test-cases/{id}/heal endpoint), rewrites the saved script, and re-runs
    the test. Up to ``max_heal_attempts`` retries per test. Each attempt
    gets its own ``_attempt<n>`` subfolder so the UI can link to whichever
    attempt is the canonical result.
    """
    concurrency = _bulk_run_concurrency()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bulk_token = uuid4().hex[:6]
    # Per-test dirs live as SIBLINGS under RESULTS_ROOT (not nested) so the
    # existing /api/runs/{run_folder}/file endpoint can serve their log.html
    # and report.html unchanged. They're correlated by the shared prefix
    # `bulk_<ts>_<token>__` for grouping in the UI / on disk.
    bulk_prefix = f"bulk_{ts}_{bulk_token}"
    gen_fallback = REPO_ROOT / "Tests" / "Generated"
    gen_fallback.mkdir(parents=True, exist_ok=True)

    yield _sse("start", {
        "label": label,
        "total": len(tcs),
        "concurrency": concurrency,
        "bulk_prefix": bulk_prefix,
        "persona": persona.name,
        "org": org_model.name,
        "auto_heal": auto_heal,
        "max_heal_attempts": max_heal_attempts if auto_heal else 0,
    })
    for tc in tcs:
        yield _sse("queued", {
            "tc_id": str(tc.id),
            "tc_title": tc.title,
            "tags": list(tc.tags or []),
        })

    queue: Queue[dict | None] = Queue()
    results: dict[str, dict] = {}
    completed = 0
    sandbox_url = org_model.login_url
    username = persona.username

    def _attempt_dir(tc: TestCase, attempt: int) -> Path:
        suffix = "" if attempt == 1 else f"_attempt{attempt}"
        tc_folder = f"{bulk_prefix}__{_bulk_slug(tc.title)}_{tc.id.hex[:8]}{suffix}"
        d = RESULTS_ROOT / tc_folder
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_attempt(tc: TestCase, attempt: int) -> dict:
        """Run Robot once for this test case. Emits running/log events to
        the queue. Returns a dict with the attempt's result -- the caller
        decides whether to publish it as `done` or to heal+retry."""
        tc_dir = _attempt_dir(tc, attempt)
        try:
            script_path = _resolve_or_build_script(tc, persona, org_model, gen_fallback)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return {
                "tc_id": str(tc.id),
                "tc_title": tc.title,
                "status": "FAIL",
                "passed": 0, "failed": 1, "skipped": 0,
                "duration_s": 0.0,
                "exit_code": -1,
                "output_dir": str(tc_dir),
                "log_html": None,
                "report_html": None,
                "error": f"script resolution failed: {exc}",
                "attempt": attempt,
            }

        cmd = _build_bulk_robot_cmd(
            script_path, tc_dir, sandbox_url, username, password,
            default_app=getattr(persona, "default_app", None) or "",
        )
        started_at = datetime.now()
        queue.put({
            "event": "running",
            "tc_id": str(tc.id),
            "tc_title": tc.title,
            "test_path": str(script_path),
            "output_dir": str(tc_dir),
            "attempt": attempt,
        })

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            return {
                "tc_id": str(tc.id),
                "tc_title": tc.title,
                "status": "FAIL",
                "passed": 0, "failed": 1, "skipped": 0,
                "duration_s": 0.0,
                "exit_code": -1,
                "output_dir": str(tc_dir),
                "log_html": None,
                "report_html": None,
                "error": f"robot not on PATH: {exc}",
                "attempt": attempt,
            }

        assert proc.stdout is not None
        for raw in proc.stdout:
            queue.put({
                "event": "log",
                "tc_id": str(tc.id),
                "line": raw.rstrip("\n"),
            })
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

        duration = round((datetime.now() - started_at).total_seconds(), 2)
        passed, failed, skipped = _parse_output_xml(tc_dir / "output.xml")
        status = "PASS" if (proc.returncode == 0 and failed == 0) else "FAIL"
        log_html = tc_dir / "log.html"
        report_html = tc_dir / "report.html"
        return {
            "tc_id": str(tc.id),
            "tc_title": tc.title,
            "status": status,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "duration_s": duration,
            "exit_code": proc.returncode,
            "output_dir": str(tc_dir),
            "log_html": str(log_html) if log_html.exists() else None,
            "report_html": str(report_html) if report_html.exists() else None,
            "attempt": attempt,
        }

    def _heal_after_failure(tc: TestCase, last_attempt_result: dict) -> bool:
        """Run the heal pipeline on a freshly-failed test. Returns True
        when the script was rewritten and we should retry, False to give
        up. Emits healing / healed / heal_failed events for the UI."""
        run_dir = Path(last_attempt_result.get("output_dir") or "")
        queue.put({
            "event": "healing",
            "tc_id": str(tc.id),
            "tc_title": tc.title,
            "attempt": last_attempt_result.get("attempt", 1),
        })

        diag = diagnose_run(run_dir) if run_dir.is_dir() else None
        if diag is None or not diag.first_failure:
            queue.put({
                "event": "heal_failed",
                "tc_id": str(tc.id),
                "reason": "no actionable diagnosis from output.xml",
            })
            return False
        if not tc.script_path:
            queue.put({
                "event": "heal_failed",
                "tc_id": str(tc.id),
                "reason": "test case has no saved script_path to rewrite",
            })
            return False

        script_abs = (REPO_ROOT / tc.script_path).resolve()
        if not script_abs.is_file():
            queue.put({
                "event": "heal_failed",
                "tc_id": str(tc.id),
                "reason": f"saved script missing: {tc.script_path}",
            })
            return False

        try:
            current_script = script_abs.read_text(encoding="utf-8")
            steps_block = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(tc.steps))
            user_body = (
                f"## Original test case\n\n"
                f"Title: {tc.title}\n"
                f"Preconditions: {tc.preconditions or 'None'}\n\n"
                f"Steps:\n{steps_block}\n\n"
                f"Expected result: {tc.expected_result}\n"
                f"Tags: {', '.join(tc.tags) if tc.tags else 'none'}\n\n"
                f"## Current saved script (which just failed)\n\n"
                f"```robot\n{current_script}\n```\n\n"
                f"## Diagnosis from the failed run\n\n"
                f"{format_diag_for_prompt(diag)}\n"
            )
            user_prompt = _assembler.build_user_prompt_with_catalog(user_body, include_full_catalog=True)
            system_prompt = _assembler.build_system_prompt("healer")

            screenshot_bytes: bytes | None = None
            if diag.screenshot_path:
                p = Path(diag.screenshot_path)
                try:
                    if p.is_file() and p.stat().st_size <= 4 * 1024 * 1024:
                        screenshot_bytes = p.read_bytes()
                except OSError:
                    screenshot_bytes = None

            from ai_bridge import call_llm, extract_robot_code  # late import
            raw = call_llm(system_prompt, user_prompt, image_bytes=screenshot_bytes)
            new_script = extract_robot_code(raw).rstrip()
            if not new_script:
                queue.put({
                    "event": "heal_failed",
                    "tc_id": str(tc.id),
                    "reason": "healer LLM returned no usable Robot source",
                })
                return False

            tmp_path = script_abs.with_suffix(script_abs.suffix + ".heal.tmp")
            tmp_path.write_text(new_script + "\n", encoding="utf-8")
            tmp_path.replace(script_abs)

            now = datetime.now(UTC)
            row = _store.get_test_case(tc.id)
            row["script_built_at"] = now.isoformat()
            row["heal_attempts"] = int(row.get("heal_attempts", 0) or 0) + 1
            row["last_healed_at"] = now.isoformat()
            _store.save_test_case(row)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            queue.put({
                "event": "heal_failed",
                "tc_id": str(tc.id),
                "reason": f"heal pipeline crashed: {exc}",
            })
            return False

        queue.put({
            "event": "healed",
            "tc_id": str(tc.id),
            "tc_title": tc.title,
            "fixed_keyword": diag.first_failure.keyword_name,
        })
        return True

    def _run_with_optional_heal(tc: TestCase) -> None:
        """Outer worker loop: one attempt + up to N heal-and-retry rounds.
        Always emits exactly one final `done` event for this tc."""
        attempt = 1
        result = _run_attempt(tc, attempt)
        while (
            auto_heal
            and result["status"] == "FAIL"
            and attempt <= max_heal_attempts
        ):
            if not _heal_after_failure(tc, result):
                break
            attempt += 1
            result = _run_attempt(tc, attempt)
        queue.put({"event": "done", **result})

    executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="bulk-run")
    for tc in tcs:
        executor.submit(_run_with_optional_heal, tc)
    # Don't wait on the executor here -- we drain `queue` instead and shut
    # down once we've seen `done` for every test.

    summary_started = datetime.now()
    try:
        while completed < len(tcs):
            try:
                msg = queue.get(timeout=15)
            except Empty:
                yield ": keepalive\n\n"
                continue
            if msg is None:
                continue
            ev = msg.pop("event")
            if ev == "done":
                results[msg["tc_id"]] = msg
                completed += 1
            yield _sse(ev, msg)
    finally:
        executor.shutdown(wait=True)

    total_passed = sum(r.get("passed", 0) for r in results.values())
    total_failed = sum(r.get("failed", 0) for r in results.values())
    total_skipped = sum(r.get("skipped", 0) for r in results.values())
    pass_count = sum(1 for r in results.values() if r.get("status") == "PASS")
    fail_count = len(results) - pass_count
    duration = round((datetime.now() - summary_started).total_seconds(), 2)
    yield _sse("summary", {
        "label": label,
        "total": len(tcs),
        "tests_passed": pass_count,
        "tests_failed": fail_count,
        "assertions_passed": total_passed,
        "assertions_failed": total_failed,
        "assertions_skipped": total_skipped,
        "duration_s": duration,
        "bulk_prefix": bulk_prefix,
        "results": list(results.values()),
    })


def _resolve_story_bulk_inputs(
    story_id: UUID,
    org_id: UUID,
    persona_id: UUID | None,
    current_user: User,
) -> tuple[list[TestCase], object, SalesforceOrg, str, str]:
    """Shared resolver for the user-story bulk endpoints. Returns
    (tcs, persona, org_model, password, label) or raises HTTPException.

    Pulled out so the SSE handler and the existing POST endpoint can share
    exactly the same access checks and resolution path."""
    try:
        row = _store.get_user_story(story_id)
    except KeyError:
        raise HTTPException(404, "User story not found") from None
    story = UserStory.model_validate(row)

    tcs_raw = _store.get_test_cases_by_story(story_id)
    tcs = [TestCase.model_validate(r) for r in tcs_raw]
    tcs = [t for t in tcs if t.status == TestCaseStatus.approved and not t.stale]
    if not tcs:
        raise HTTPException(400, "No approved non-stale test cases for this story")

    org = _get_org(org_id)
    if not org:
        raise HTTPException(404, f"Org {org_id} not found")
    org_model = SalesforceOrg(**org)

    all_personas = load_personas_for_user(current_user)
    prompt = f"Execute automated tests for user story: {story.title}"
    try:
        persona, _method = _resolver.resolve(
            project_id=story.project_id,
            org_id=org_id,
            prompt=prompt,
            ui_persona_id=persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)
    label = f"Story: {story.title}"
    return tcs, persona, org_model, password, label


def _resolve_tag_bulk_inputs(
    tag_name: str,
    project_id: UUID,
    org_id: UUID,
    persona_id: UUID | None,
    current_user: User,
) -> tuple[list[TestCase], object, SalesforceOrg, str, str]:
    tcs_raw = _store.get_test_cases_by_tag(project_id, tag_name)
    tcs = [TestCase.model_validate(r) for r in tcs_raw if not r.get("stale")]
    if not tcs:
        raise HTTPException(400, f"No approved non-stale test cases tagged {tag_name!r}")

    org = _get_org(org_id)
    if not org:
        raise HTTPException(404, f"Org {org_id} not found")
    org_model = SalesforceOrg(**org)

    all_personas = load_personas_for_user(current_user)
    prompt = f"Run tests tagged {tag_name}"
    try:
        persona, _method = _resolver.resolve(
            project_id=project_id,
            org_id=org_id,
            prompt=prompt,
            ui_persona_id=persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)
    label = f"Tag: {tag_name}"
    return tcs, persona, org_model, password, label


def _resolve_sprint_bulk_inputs(
    sprint_id: UUID,
    org_id: UUID,
    persona_id: UUID | None,
    current_user: User,
) -> tuple[list[TestCase], object, SalesforceOrg, str, str]:
    """Sprint-scoped resolver. Walks every story under the sprint, then
    every approved non-stale test case under each story, and returns the
    flat list. Same return shape as the story / tag resolvers so the
    SSE engine consumes it unchanged.
    """
    try:
        sprint_row = _store.get_sprint(sprint_id)
    except KeyError:
        raise HTTPException(404, "Sprint not found") from None

    project_id = UUID(str(sprint_row["project_id"]))
    sprint_name = sprint_row.get("name") or f"Sprint {sprint_id.hex[:8]}"

    story_rows = _store.get_user_stories_by_sprint(sprint_id)
    if not story_rows:
        raise HTTPException(400, "Sprint has no user stories assigned")

    tcs: list[TestCase] = []
    for srow in story_rows:
        # Skip archived stories so a stale leftover doesn't run.
        if srow.get("status") == "archived":
            continue
        for tc_row in _store.get_test_cases_by_story(UUID(str(srow["id"]))):
            tc = TestCase.model_validate(tc_row)
            if tc.status == TestCaseStatus.approved and not tc.stale:
                tcs.append(tc)

    if not tcs:
        raise HTTPException(
            400,
            "Sprint has no approved non-stale test cases across its stories",
        )

    org = _get_org(org_id)
    if not org:
        raise HTTPException(404, f"Org {org_id} not found")
    org_model = SalesforceOrg(**org)

    all_personas = load_personas_for_user(current_user)
    prompt = f"Execute automated tests for sprint: {sprint_name}"
    try:
        persona, _method = _resolver.resolve(
            project_id=project_id,
            org_id=org_id,
            prompt=prompt,
            ui_persona_id=persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)
    label = f"Sprint: {sprint_name}"
    return tcs, persona, org_model, password, label


@router.get("/user-story/{story_id}/stream")
def run_user_story_stream(
    story_id: UUID,
    org_id: UUID = Query(...),
    persona_id: UUID | None = Query(None),
    auto_heal: bool = Query(False, description="When true, failed tests are healed and re-run once."),
    current_user: User = Depends(get_current_user),
):
    """SSE stream of a parallel bulk run for a user story.

    Auth: get_current_user already runs (router-level dependency); the JWT
    is provided by the EventSource as a query token (see frontend/lib/api.ts
    `withAuthQuery`).
    """
    tcs, persona, org_model, password, label = _resolve_story_bulk_inputs(
        story_id, org_id, persona_id, current_user,
    )
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        _bulk_event_stream(
            label=label, tcs=tcs, persona=persona, org_model=org_model, password=password,
            auto_heal=auto_heal,
        ),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get("/tag/{tag_name}/stream")
def run_tag_stream(
    tag_name: str,
    project_id: UUID = Query(...),
    org_id: UUID = Query(...),
    persona_id: UUID | None = Query(None),
    auto_heal: bool = Query(False, description="When true, failed tests are healed and re-run once."),
    current_user: User = Depends(get_current_user),
):
    """SSE stream of a parallel bulk run for a tag."""
    tcs, persona, org_model, password, label = _resolve_tag_bulk_inputs(
        tag_name, project_id, org_id, persona_id, current_user,
    )
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        _bulk_event_stream(
            label=label, tcs=tcs, persona=persona, org_model=org_model, password=password,
            auto_heal=auto_heal,
        ),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get("/sprint/{sprint_id}/stream")
def run_sprint_stream(
    sprint_id: UUID,
    org_id: UUID = Query(...),
    persona_id: UUID | None = Query(None),
    auto_heal: bool = Query(False, description="When true, failed tests are healed and re-run once."),
    current_user: User = Depends(get_current_user),
):
    """SSE stream of a parallel bulk run across every story in a sprint.

    Resolves to all approved non-stale test cases under the sprint's
    stories, then delegates to the same `_bulk_event_stream` engine that
    powers /run/user-story/{id}/stream and /run/tag/{name}/stream. The
    UI's BulkExecutionStream component handles its events unchanged.
    """
    tcs, persona, org_model, password, label = _resolve_sprint_bulk_inputs(
        sprint_id, org_id, persona_id, current_user,
    )
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        _bulk_event_stream(
            label=label, tcs=tcs, persona=persona, org_model=org_model, password=password,
            auto_heal=auto_heal,
        ),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get("/test-case/{test_case_id}/stream")
def run_single_test_case_stream(
    test_case_id: UUID,
    org_id: UUID = Query(...),
    persona_id: UUID | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    """Re-run a single test case as SSE.

    Used by the "Heal & retry" button in the bulk-execution UI: after
    `POST /test-cases/{id}/heal` rewrites the script, the frontend
    opens this stream to verify the rewrite. Reuses the same engine as
    the bulk endpoint with a one-element list, so the UI's per-test
    card sees the same event shape and can replace its prior result.

    Ownership: inherited via the test case's parent story, same as
    other /test-cases routes.
    """
    try:
        row = _store.get_test_case(test_case_id)
    except KeyError:
        raise HTTPException(404, "Test case not found") from None
    story_id = UUID(str(row["user_story_id"]))
    try:
        story_row = _store.get_user_story(story_id)
    except KeyError:
        raise HTTPException(404, "Parent story not found") from None
    story = UserStory.model_validate(story_row)
    # Owner check (matches _user_can_see_story in user_stories.py).
    if not current_user.is_admin and (
        not story.owner_user_id or story.owner_user_id != current_user.id
    ):
        raise HTTPException(404, "Test case not found")

    tc = TestCase.model_validate(row)
    # Allow re-running even if status flipped to draft / rejected -- the
    # caller explicitly chose this case. Stale we still skip, since the
    # parent story moved on and the test no longer matches it.
    if tc.stale:
        raise HTTPException(400, "Test case is stale (parent story changed); regenerate first.")

    org = _get_org(org_id)
    if not org:
        raise HTTPException(404, f"Org {org_id} not found")
    org_model = SalesforceOrg(**org)

    all_personas = load_personas_for_user(current_user)
    prompt = f"Re-run test case: {tc.title}"
    try:
        persona, _method = _resolver.resolve(
            project_id=story.project_id,
            org_id=org_id,
            prompt=prompt,
            ui_persona_id=persona_id,
            all_personas=all_personas,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cred_svc = CredentialService(settings.fernet_key or None)
    password = cred_svc.decrypt(persona.encrypted_password)

    label = f"Re-run: {tc.title}"
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        _bulk_event_stream(
            label=label, tcs=[tc], persona=persona, org_model=org_model, password=password,
        ),
        media_type="text/event-stream",
        headers=headers,
    )


class ExecuteRequest(BaseModel):
    test_path: str
    sandbox_url: str
    username: str
    password: str
    headless: bool = True
    use_pabot: bool = False
    include_tags: str = ""
    exclude_tags: str = ""
    # Optional Salesforce app to land in (mapped to ${salesAutomationAppName}).
    default_app: str = ""


class ExecuteResponse(BaseModel):
    status: str
    duration_s: float = 0.0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    output_dir: str = ""
    log_html: str | None = None
    report_html: str | None = None
    error_message: str | None = None


def _safe_within_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT.resolve())
        return True
    except ValueError:
        return False


def _parse_output_xml(output_xml: Path) -> tuple[int, int, int]:
    """Backwards-compatible wrapper around the shared parser."""
    passed, failed, skipped, _duration = _shared_parse_output_xml(output_xml)
    return passed, failed, skipped


def _parse_output_xml_with_duration(output_xml: Path) -> tuple[int, int, int, float]:
    return _shared_parse_output_xml(output_xml)


def _extract_project_slug_from_test_path(test_path: Path) -> str | None:
    """Best-effort: pull the project slug out of a Saved_Projects/<slug>/Tests/... path."""
    parts = test_path.resolve().parts
    if "Saved_Projects" in parts:
        i = parts.index("Saved_Projects")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


@exec_router.post("/execute", response_model=ExecuteResponse)
def execute_robot(
    body: ExecuteRequest,
    current_user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """Run a single Robot suite synchronously and return parsed results."""
    test_path = Path(body.test_path)
    if not test_path.is_file():
        raise HTTPException(404, f"Test file not found: {test_path}")
    if not _safe_within_repo(test_path):
        raise HTTPException(400, "Refusing to execute file outside the repo")

    sys.path.insert(0, str(REPO_ROOT))
    from run_test import write_envdata

    write_envdata(body.sandbox_url, body.username, body.password)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS_ROOT / f"ui_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Phase 2e: stamp a RunRecord before launching so the run shows up in the
    # project's filtered run history. project_slug derived from test_path.
    project_slug = _extract_project_slug_from_test_path(test_path) or ""
    run_record = RunRecord(
        project_slug=project_slug,
        run_folder=out_dir.name,
        triggered_by_user_id=current_user.id,
        status="started",
    )
    try:
        db.add(run_record)
        db.commit()
        db.refresh(run_record)
    except Exception:
        db.rollback()
        run_record = None

    log_action(
        db, user=current_user,
        action="run_started", target_type="run", target_id=out_dir.name,
        metadata={"project_slug": project_slug, "test": test_path.name},
    )

    cmd = [
        sys.executable, "-m", "robot",
        "--outputdir", str(out_dir),
        "--variable", f"globalSandboxTestUrl:{body.sandbox_url}",
        "--variable", f"sandboxUserNameInput:{body.username}",
        "--variable", f"sandboxPasswordInput:{body.password}",
    ]
    if _effective_headless(body.headless):
        cmd.extend(["--variable", "headless:true"])
    cmd.extend(_container_browser_overrides())
    cmd.extend(_persona_robot_overrides(body.default_app))
    if body.include_tags:
        for tag in [t.strip() for t in body.include_tags.split(",") if t.strip()]:
            cmd.extend(["--include", tag])
    if body.exclude_tags:
        for tag in [t.strip() for t in body.exclude_tags.split(",") if t.strip()]:
            cmd.extend(["--exclude", tag])
    cmd.append(str(test_path))

    started = datetime.now()
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        return ExecuteResponse(
            status="TIMEOUT",
            output_dir=str(out_dir),
            error_message="Robot run exceeded 900s and was killed.",
        )
    except FileNotFoundError as exc:
        raise HTTPException(500, f"robot not on PATH for {sys.executable}: {exc}") from exc

    duration = (datetime.now() - started).total_seconds()
    output_xml = out_dir / "output.xml"
    log_html = out_dir / "log.html"
    report_html = out_dir / "report.html"
    passed, failed, skipped = _parse_output_xml(output_xml)
    status = "PASS" if completed.returncode == 0 and failed == 0 else "FAIL"

    # Update the RunRecord with terminal status + finished_at.
    if run_record is not None:
        try:
            run_record.status = status
            run_record.finished_at = datetime.now()
            db.commit()
        except Exception:
            db.rollback()
    log_action(
        db, user=current_user,
        action="run_finished", target_type="run", target_id=out_dir.name,
        metadata={"status": status, "passed": passed, "failed": failed, "duration_s": duration},
    )

    # Fire an in-app notification so users see run outcomes from the
    # bell (not just the inline log). One row per terminal run --
    # bulk runs emit one "run_finished" per case which keeps the
    # noise low; users can mute by marking-all-read.
    # Notification type matches audit_actions.RUN_FAILED / RUN_PASSED
    # so the activity feed and inbox classify them consistently.
    try:
        from ..services.audit_actions import RUN_FAILED, RUN_PASSED
        from ..services.db import push_notification
        ev_type = RUN_FAILED if status == "FAIL" else RUN_PASSED
        push_notification(
            db,
            user_id=str(current_user.id),
            type=ev_type,
            title=(
                f"Run failed ({failed} test{'' if failed == 1 else 's'})"
                if status == "FAIL"
                else f"Run passed ({passed} test{'' if passed == 1 else 's'})"
            ),
            body=f"{duration:.1f}s wall clock · {out_dir.name}",
            action_url=f"/runs/{out_dir.name}",
        )
    except Exception:
        # Notification failure must never break a run response.
        pass

    error_message = None
    if completed.returncode != 0 and not output_xml.exists():
        # Robot didn't even start writing output - capture stderr/stdout snippet.
        snippet = (completed.stderr or completed.stdout or "").strip().splitlines()
        error_message = " | ".join(snippet[-5:]) if snippet else f"exit {completed.returncode}"

    return ExecuteResponse(
        status=status,
        duration_s=round(duration, 2),
        passed=passed,
        failed=failed,
        skipped=skipped,
        output_dir=str(out_dir),
        log_html=str(log_html) if log_html.exists() else None,
        report_html=str(report_html) if report_html.exists() else None,
        error_message=error_message,
    )


def _sse(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    safe = payload.replace("\r", "")
    return f"event: {event}\ndata: {safe}\n\n"


def _is_in_container() -> bool:
    """True when the backend is running inside our Docker image.

    Set by the Dockerfile. Used to override caller-supplied headless=False
    (Chrome can't open a window with no display) and to point Selenium at the
    system chromium binary."""
    return os.getenv("BACKEND_IN_CONTAINER", "").strip() not in ("", "0", "false", "False")


def _container_browser_overrides() -> list[str]:
    """Extra `--variable` args that GlobalKeywords.robot uses to reach the
    in-container chromium binary. Returns [] when not in a container."""
    if not _is_in_container():
        return []
    binary = os.getenv("CONTAINER_BROWSER_BINARY", "/usr/bin/chromium").strip()
    return ["--variable", f"CONTAINER_BROWSER_BINARY:{binary}"]


def _persona_robot_overrides(default_app: str | None) -> list[str]:
    """Extra `--variable` args derived from persona metadata.

    Today: maps `persona.default_app` -> `${salesAutomationAppName}` so
    PO keywords (Open New Lead From Sales App, Go To Accounts Tab For App,
    etc.) automatically land in the right Salesforce app for this user's
    license. Without this override the global default in
    `Resources/TestData/Platform/SalesData.robot` (= "Sales") wins, which
    is what existing tests fall back to when no persona is bound.

    Single source of truth: any future per-persona Robot variable
    (e.g. `defaultListView`, `defaultRecordType`) lands in this helper so
    we don't have to thread new kwargs through every command builder.
    """
    extras: list[str] = []
    app = (default_app or "").strip()
    if app:
        extras.extend(["--variable", f"salesAutomationAppName:{app}"])
    return extras


def _effective_headless(requested: bool) -> bool:
    """Containerized backend has no display, so headed Chrome dies on launch.
    Force headless regardless of what the caller asked for."""
    return True if _is_in_container() else requested


def _build_robot_cmd(
    test_path: Path,
    out_dir: Path,
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
    include_tags: str = "",
    exclude_tags: str = "",
    default_app: str = "",
) -> list[str]:
    cmd = [
        sys.executable, "-m", "robot",
        "--outputdir", str(out_dir),
        "--variable", f"globalSandboxTestUrl:{sandbox_url}",
        "--variable", f"sandboxUserNameInput:{username}",
        "--variable", f"sandboxPasswordInput:{password}",
    ]
    if _effective_headless(headless):
        cmd.extend(["--variable", "headless:true"])
    cmd.extend(_container_browser_overrides())
    cmd.extend(_persona_robot_overrides(default_app))
    for tag in [t.strip() for t in include_tags.split(",") if t.strip()]:
        cmd.extend(["--include", tag])
    for tag in [t.strip() for t in exclude_tags.split(",") if t.strip()]:
        cmd.extend(["--exclude", tag])
    cmd.append(str(test_path))
    return cmd


@exec_router.get("/execute/stream")
def execute_robot_stream(
    test_path: str = Query(...),
    sandbox_url: str = Query(""),
    username: str = Query(""),
    password: str = Query(""),
    headless: bool = Query(True),
    include_tags: str = Query(""),
    exclude_tags: str = Query(""),
    default_app: str = Query("", description="Optional ${salesAutomationAppName} override."),
):
    """Stream Robot stdout line-by-line as Server-Sent Events.

    Auth note: the password is passed as a query string because EventSource
    cannot send a body. Same trust boundary as the JSON POST variant; use only
    on a trusted host (default localhost in dev).
    """
    tp = Path(test_path)
    if not tp.is_file():
        raise HTTPException(404, f"Test file not found: {tp}")
    if not _safe_within_repo(tp):
        raise HTTPException(400, "Refusing to execute file outside the repo")

    sys.path.insert(0, str(REPO_ROOT))
    from run_test import write_envdata

    write_envdata(sandbox_url, username, password)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS_ROOT / f"ui_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = _build_robot_cmd(
        tp, out_dir, sandbox_url, username, password, headless, include_tags, exclude_tags,
        default_app=default_app,
    )

    def event_stream():
        yield _sse("start", {"output_dir": str(out_dir), "headless": headless})
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            yield _sse("error", {"message": f"robot not on PATH: {exc}"})
            return

        q: Queue[str | None] = Queue()

        def _reader():
            assert proc.stdout is not None
            for line in proc.stdout:
                q.put(line.rstrip("\n"))
            q.put(None)

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        started = datetime.now()
        try:
            while True:
                try:
                    item = q.get(timeout=15)
                except Empty:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield _sse("log", {"line": item})
        finally:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

        duration = round((datetime.now() - started).total_seconds(), 2)
        output_xml = out_dir / "output.xml"
        log_html = out_dir / "log.html"
        report_html = out_dir / "report.html"
        passed, failed, skipped = _parse_output_xml(output_xml)
        status = "PASS" if (proc.returncode == 0 and failed == 0) else "FAIL"
        yield _sse("done", {
            "status": status,
            "duration_s": duration,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "output_dir": str(out_dir),
            "log_html": str(log_html) if log_html.exists() else None,
            "report_html": str(report_html) if report_html.exists() else None,
            "exit_code": proc.returncode,
        })

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@exec_router.get("/latest")
def latest_runs(
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return recent UI run summaries from the configured RESULTS_DIR.

    Phase 2e: scoped by project membership.
      - Admin sees all runs (RunRecord-tagged or legacy on-disk-only).
      - Project members see runs in projects they're a member of.
      - Legacy on-disk runs without a RunRecord row are admin-only (they
        were created before the SQL run table existed; nothing in their
        on-disk metadata identifies which project they belong to).
    """
    results_root = RESULTS_ROOT
    if not results_root.is_dir():
        return {"runs": []}

    is_admin = current_user.is_admin or current_user.global_role == "admin"
    member_slugs = (
        None  # placeholder; only computed for non-admins
        if is_admin
        else {m.project_slug for m in list_memberships_for_user(db, current_user.id)}
    )

    # Index of run_folder -> RunRecord (only the slugs we care about).
    record_index: dict[str, RunRecord] = {
        r.run_folder: r
        for r in db.query(RunRecord)
        .order_by(RunRecord.started_at.desc())
        .limit(2000)
        .all()
    }

    rows = []
    for run_dir in sorted(
        (p for p in results_root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        if len(rows) >= limit:
            break

        rec = record_index.get(run_dir.name)
        if not is_admin:
            # Membership-scoped filter for non-admins.
            if rec is None:
                continue
            if rec.project_slug not in (member_slugs or set()):
                continue

        passed, failed, skipped, duration_s = _shared_parse_output_xml(run_dir / "output.xml")
        total = passed + failed + skipped
        status = "PASS" if total > 0 and failed == 0 else ("FAIL" if failed > 0 else "EMPTY")
        rows.append({
            "run_name": run_dir.name,
            "timestamp": datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat(),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": total,
            "duration_s": duration_s,
            "status": status,
            "log_html": str(run_dir / "log.html") if (run_dir / "log.html").is_file() else None,
            "report_html": str(run_dir / "report.html") if (run_dir / "report.html").is_file() else None,
            "project_slug": rec.project_slug if rec else None,
            "triggered_by_user_id": rec.triggered_by_user_id if rec else None,
        })
    return {"runs": rows}


# ---- Per-run summary, file, and bundle endpoints ----------------------

# Use the shared timestamp parser to keep behaviour consistent with analytics.
_parse_robot_ts = _shared_parse_robot_ts


def _resolve_run_dir(run_folder: str) -> Path:
    """Return the absolute <RESULTS_ROOT>/<run_folder> path or raise 404."""
    if "/" in run_folder or "\\" in run_folder or ".." in run_folder:
        raise HTTPException(400, "Invalid run folder")
    run_dir = (RESULTS_ROOT / run_folder).resolve()
    results_root = RESULTS_ROOT.resolve()
    try:
        run_dir.relative_to(results_root)
    except ValueError as exc:
        raise HTTPException(400, "Refusing to access path outside the results directory") from exc
    if not run_dir.is_dir():
        raise HTTPException(404, f"Run '{run_folder}' not found")
    return run_dir


def _status_dt_and_elapsed(st):
    """Compatible with Robot 6 (starttime/endtime) and Robot 7 (start/elapsed)."""
    if st is None:
        return None, None, 0.0
    s = _shared_parse_robot_ts(st.attrib.get("starttime") or st.attrib.get("start"))
    e = _shared_parse_robot_ts(st.attrib.get("endtime") or st.attrib.get("end"))
    elapsed = 0.0
    raw = st.attrib.get("elapsed")
    if raw:
        try:
            elapsed = float(raw)
        except (TypeError, ValueError):
            elapsed = 0.0
    if elapsed <= 0 and s and e:
        elapsed = (e - s).total_seconds()
    return s, e, elapsed


def _walk_tests(suite_el, parent_path: str = "") -> list[dict]:
    """Depth-first walk of <suite>...<test> from a Robot output.xml suite node."""
    out: list[dict] = []
    suite_name = suite_el.attrib.get("name", "")
    here = f"{parent_path}.{suite_name}" if parent_path else suite_name
    for test in suite_el.findall("test"):
        st = test.find("status")
        status = (st.attrib.get("status") if st is not None else "FAIL") or "FAIL"
        _s, _e, elapsed = _status_dt_and_elapsed(st)
        message = (st.text or "").strip() if st is not None else ""
        tags = [t.text or "" for t in test.findall("tags/tag") if (t.text or "").strip()]
        out.append({
            "name": test.attrib.get("name", ""),
            "suite": here,
            "status": status.upper(),
            "duration_s": round(elapsed, 2),
            "message": message[:500] if message else None,
            "tags": tags,
        })
    for child in suite_el.findall("suite"):
        out.extend(_walk_tests(child, here))
    return out


def _gather_stats(node) -> list[dict]:
    out: list[dict] = []
    for stat in node.findall("stat"):
        name = (stat.text or "").strip()
        if not name:
            continue
        passed = int(stat.attrib.get("pass", 0))
        failed = int(stat.attrib.get("fail", 0))
        skipped = int(stat.attrib.get("skip", 0))
        out.append({
            "name": name,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": passed + failed + skipped,
        })
    return out


def _list_screenshots(run_dir: Path) -> list[str]:
    return sorted(
        p.name
        for p in run_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".png", ".jpg", ".jpeg"}
        and p.name.lower().startswith("selenium-screenshot")
    )


@exec_router.get("/{run_folder}/summary")
def run_summary(run_folder: str):
    """Detailed run summary: KPIs, by-tag/by-suite stats, per-test rows, artefacts."""
    run_dir = _resolve_run_dir(run_folder)
    output_xml = run_dir / "output.xml"
    log_html = run_dir / "log.html"
    report_html = run_dir / "report.html"

    started_at = None
    finished_at = None
    duration_s = 0.0
    by_tag: list[dict] = []
    by_suite: list[dict] = []
    tests: list[dict] = []

    if output_xml.is_file():
        try:
            import xml.etree.ElementTree as ET

            root = ET.parse(output_xml).getroot()
            generated = root.attrib.get("generated") or root.attrib.get("generated_time")
            finished_at = _parse_robot_ts(generated)

            for top_suite in root.findall("suite"):
                tests.extend(_walk_tests(top_suite))
                st = top_suite.find("status")
                s, e, elapsed = _status_dt_and_elapsed(st)
                if s and (started_at is None or s < started_at):
                    started_at = s
                if e and (finished_at is None or e > finished_at):
                    finished_at = e
                if elapsed > 0:
                    duration_s += elapsed

            stats = root.find("statistics")
            if stats is not None:
                tag_node = stats.find("tag")
                suite_node = stats.find("suite")
                if tag_node is not None:
                    by_tag = _gather_stats(tag_node)
                if suite_node is not None:
                    by_suite = _gather_stats(suite_node)
        except (ET.ParseError, OSError):
            pass

    # Derive totals from the per-test walk so we are robust to Robot version
    # differences in the <statistics> block.
    passed = sum(1 for t in tests if t["status"] == "PASS")
    failed = sum(1 for t in tests if t["status"] == "FAIL")
    skipped = sum(1 for t in tests if t["status"] == "SKIP")
    total = passed + failed + skipped
    status = "PASS" if total > 0 and failed == 0 else ("FAIL" if failed > 0 else "EMPTY")
    pass_rate = round((passed / total * 100), 1) if total > 0 else 0.0

    # Phase 4: Playwright trace files. Surfaced as a list of filenames
    # so the frontend can build "Open trace viewer" links per file
    # (suites that capture multiple traces -- one per test case --
    # produce e.g. ``smoke.trace.zip`` + ``regression.trace.zip``).
    # The standard PlaywrightDebug.robot wrapper writes a single
    # ``trace.zip``; this glob handles both cases additively.
    trace_files = sorted(
        p.name for p in run_dir.glob("*trace.zip") if p.is_file()
    )

    artefacts = {
        "log_html": "log.html" if log_html.is_file() else None,
        "report_html": "report.html" if report_html.is_file() else None,
        "output_xml": "output.xml" if output_xml.is_file() else None,
        "screenshots": _list_screenshots(run_dir),
        "playwright_traces": trace_files,
    }

    return {
        "run_folder": run_folder,
        "started_at": started_at.isoformat() if started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else None,
        "duration_s": round(duration_s, 2),
        "status": status,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "pass_rate": pass_rate,
        "by_tag": by_tag,
        "by_suite": by_suite,
        "tests": tests,
        "artefacts": artefacts,
    }


_INLINE_MIME = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".xml": "application/xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".log": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".robot": "text/plain; charset=utf-8",
}


@exec_router.get("/{run_folder}/file/{filename}")
def run_file(run_folder: str, filename: str, download: bool = Query(False)):
    """Stream a single file from a run folder. Use ?download=1 to force download."""
    run_dir = _resolve_run_dir(run_folder)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    target = (run_dir / filename).resolve()
    try:
        target.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise HTTPException(400, "Refusing to serve path outside the run folder") from exc
    if not target.is_file():
        raise HTTPException(404, f"File '{filename}' not found in run")

    mime = _INLINE_MIME.get(target.suffix.lower(), "application/octet-stream")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{target.name}"'
    else:
        headers["Content-Disposition"] = f'inline; filename="{target.name}"'

    def _iter():
        with target.open("rb") as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(_iter(), media_type=mime, headers=headers)


@exec_router.get("/{run_folder}/bundle.zip")
def run_bundle(run_folder: str):
    """Stream a ZIP of log.html + report.html + output.xml + screenshots
    + (Phase 4) Playwright trace.zip when present.

    The trace file is included additively -- bundles for runs that
    didn't capture a Playwright trace contain exactly the same files
    they always did. Only opt-in suites that import
    ``Resources/Common/PlaywrightDebug.robot`` and were launched with
    ``--variable USE_PLAYWRIGHT_TRACE:1`` produce a trace.zip.
    """
    import io
    import zipfile

    run_dir = _resolve_run_dir(run_folder)
    candidates = ["log.html", "report.html", "output.xml"] + _list_screenshots(run_dir)
    # Phase 4: Playwright trace. Conventionally named "trace.zip" by
    # ``PlaywrightDebug.robot`` -- we glob for any *.trace.zip too so
    # custom Suite Setup steps that name traces after the test case
    # also surface in the bundle.
    trace_files = [p.name for p in run_dir.glob("*trace.zip") if p.is_file()]
    candidates.extend(trace_files)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in candidates:
            p = run_dir / name
            if p.is_file():
                zf.write(p, arcname=f"{run_folder}/{name}")
    buf.seek(0)

    headers = {
        "Content-Disposition": f'attachment; filename="{run_folder}.zip"',
        "Content-Length": str(buf.getbuffer().nbytes),
    }
    return StreamingResponse(iter([buf.getvalue()]), media_type="application/zip", headers=headers)


def _execute_in_background(
    run_id,
    login_url: str,
    username: str,
    password: str,
    prompt: str,
) -> None:
    """Background task: generate .robot from prompt then run it.

    Repo root is already on sys.path at module import time (see top of file
    -- REPO_ROOT injection happens once when this module is loaded). No need
    to re-inject per call, and re-importing logging/os/sys inside the body
    just shadowed the module-level names.
    """
    try:
        from ai_bridge import generate_test_from_prompt
        output_path = generate_test_from_prompt(prompt)
        _runner.run(
            str(output_path),
            username=username,
            password=password,
            login_url=login_url,
        )
    except Exception as exc:
        logger.error("Background run %s failed: %s", run_id, exc)
