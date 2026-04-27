from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ai_qa_portal.backend.config import REPO_ROOT, settings
from ..models.org import SalesforceOrg
from ..models.persona import RunRequest, RunResponse
from ..models.test_case import TestCase, TestCaseStatus
from ..models.user_story import UserStory
from ..routers.personas import load_personas_for_user
from ..services.audit import log_action
from ..services.auth import get_current_user
from ..services.db import (
    RunRecord,
    User,
    get_db,
    list_memberships_for_user,
)
from ..services.credential_service import CredentialService
from ..services.persona_resolver import PersonaResolver
from ..services.robot_results import (
    parse_output_xml as _shared_parse_output_xml,
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
    persona_id: Optional[UUID] = None


class RunTagBody(BaseModel):
    project_id: UUID
    org_id: UUID
    persona_id: Optional[UUID] = None


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


class ExecuteRequest(BaseModel):
    test_path: str
    sandbox_url: str
    username: str
    password: str
    headless: bool = True
    use_pabot: bool = False
    include_tags: str = ""
    exclude_tags: str = ""


class ExecuteResponse(BaseModel):
    status: str
    duration_s: float = 0.0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    output_dir: str = ""
    log_html: Optional[str] = None
    report_html: Optional[str] = None
    error_message: Optional[str] = None


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
        tp, out_dir, sandbox_url, username, password, headless, include_tags, exclude_tags
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

    artefacts = {
        "log_html": "log.html" if log_html.is_file() else None,
        "report_html": "report.html" if report_html.is_file() else None,
        "output_xml": "output.xml" if output_xml.is_file() else None,
        "screenshots": _list_screenshots(run_dir),
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
    """Stream a ZIP of log.html + report.html + output.xml + screenshots."""
    import io
    import zipfile

    run_dir = _resolve_run_dir(run_folder)
    candidates = ["log.html", "report.html", "output.xml"] + _list_screenshots(run_dir)

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
    """Background task: generate .robot from prompt then run it."""
    import sys
    import os
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    sys.path.insert(0, repo_root)

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
        import logging
        logging.getLogger(__name__).error("Background run %s failed: %s", run_id, exc)
