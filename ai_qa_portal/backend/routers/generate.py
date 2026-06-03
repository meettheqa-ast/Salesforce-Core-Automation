"""Test generation endpoints — quick generate and MCP stepwise."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.config import GENERATED_SUITE, REPO_ROOT
from ai_qa_portal.backend.config import settings
from ai_qa_portal.backend.models.schemas import (
    GenerateRequest,
    GenerateResponse,
    GenerationAttempt,
    ProviderSwitchPayload,
    ValidationErrorPayload,
)
from ai_qa_portal.backend.services.auth import get_current_user
from ai_qa_portal.backend.services.db import SessionLocal, User
from ai_qa_portal.backend.services.db_models.generation import (
    GenerationJob,
    GenerationMetric,
    GenerationStatus,
)
from ai_qa_portal.backend.services.generation_worker import JobCancelled, broker, submit_generation_job

router = APIRouter(
    prefix="/api/generate",
    tags=["generate"],
    dependencies=[Depends(get_current_user)],
)
logger = logging.getLogger("ai_qa_portal.generate")


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """Flatten BaseExceptionGroup trees into their non-group leaves."""
    import builtins as _bi

    _eg = getattr(_bi, "BaseExceptionGroup", None)
    if _eg is not None and isinstance(exc, _eg):
        out: list[BaseException] = []
        for sub in exc.exceptions:
            out.extend(_leaf_exceptions(sub))
        return out or [exc]
    return [exc]


def _format_generation_error(exc: BaseException) -> str:
    """Readable detail for API clients (unwrap ExceptionGroup / TaskGroup noise)."""
    leaves = _leaf_exceptions(exc)
    parts: list[str] = []
    seen: set[str] = set()
    for leaf in leaves:
        label = f"{type(leaf).__name__}: {leaf}".strip().rstrip(":")
        cause = getattr(leaf, "__cause__", None) or getattr(leaf, "__context__", None)
        if cause and str(cause) and str(cause) not in label:
            label = f"{label} (cause: {type(cause).__name__}: {cause})"
        if label not in seen:
            seen.add(label)
            parts.append(label)
    if not parts:
        parts.append(repr(exc))
    return " | ".join(parts)


def _validation_payload_from_loop(result) -> tuple[
    bool, list[ValidationErrorPayload], list[GenerationAttempt],
]:
    """Translate a ``ValidationLoopResult`` into the schema models the API
    surface uses. Kept in the router so the schemas module stays free of
    runtime service imports."""
    errors: list[ValidationErrorPayload] = []
    for e in result.final_report.errors:
        # Locator-not-found errors carry extra context (page_url +
        # suggested_locator) embedded in message + closest_matches; pull
        # them out into their own payload fields for the frontend.
        page_url = ""
        suggested_locator = ""
        if e.kind == "locator_not_found":
            # Message format from playwright_validate: "Locator did not
            # resolve on the live page. page_url=<url>"
            if "page_url=" in (e.message or ""):
                try:
                    page_url = e.message.split("page_url=", 1)[1].strip()
                except IndexError:
                    pass
            if e.closest_matches:
                suggested_locator = e.closest_matches[0]
        errors.append(
            ValidationErrorPayload(
                line=e.line, column=e.column, kind=str(e.kind),
                symbol=e.symbol, message=e.message,
                closest_matches=list(e.closest_matches), snippet=e.snippet,
                page_url=page_url,
                suggested_locator=suggested_locator,
            )
        )
    attempts = [
        GenerationAttempt(
            attempt=a.attempt,
            ok=a.report.ok,
            error_count=len(a.report.errors),
            # Cap excerpt fields so a many-attempt history can't bloat
            # the response payload.
            fix_prompt_excerpt=(a.fix_prompt or "")[:280],
            script_excerpt=(a.script_excerpt or "")[:600],
        )
        for a in result.attempts
    ]
    return result.converged, errors, attempts


def _build_locator_validate_callable(body) -> tuple[Any | None, bool]:
    """Build the optional locator-validation callable for the validation
    loop, gated by the global + per-project Playwright flags.

    Returns ``(callable_or_None, shadow_mode)``. The router passes both
    to ``generate_test_from_prompt_validated`` so the loop knows whether
    to run the gate AND whether failures should block.

    Soft-fails: any import / config error -> return ``(None, False)``
    so the existing two-tier validation pipeline carries on unchanged.
    """
    # Late imports to avoid circular dependencies on module load and to
    # keep the existing fallback path import-clean when Playwright isn't
    # installed.
    try:
        from ai_qa_portal.backend.config import settings
    except Exception:  # pylint: disable=broad-exception-caught
        return None, False

    # Master kill switch + feature flag.
    if not getattr(settings, "playwright_enabled", True):
        return None, False
    if not getattr(settings, "pw_locator_validation", False):
        return None, False
    # Need credentials to drive a Salesforce login. Without them the
    # gate would just hang; skip silently.
    if not body.sandbox_url or not body.username or not body.password:
        return None, False

    shadow = bool(getattr(settings, "pw_locator_validation_shadow", False))

    try:
        from ai_qa_portal.backend.services.playwright_validate import validate_locators
    except ImportError:
        return None, False

    def _gate(suite_path):
        return validate_locators(
            suite_path,
            sandbox_url=body.sandbox_url,
            username=body.username,
            password=body.password,
            timeout_s=60.0,
            metrics_label_phase="quick_gen",
        )

    return _gate, shadow


def _drain_provider_switches() -> list[ProviderSwitchPayload]:
    """Pull any LLM-provider failovers that happened on this request's
    thread and convert them into API-surface payloads. Returns an empty
    list in the common case (primary provider succeeded). Drained AFTER
    every LLM call site so multi-attempt validation loops still get
    their full switch history reported once.
    """
    try:
        from ai_bridge import drain_provider_notes
    except ImportError:  # pragma: no cover -- ai_bridge ships with the backend
        return []
    return [ProviderSwitchPayload(**n) for n in drain_provider_notes()]


@router.get("/recipes")
def list_recipes() -> list[dict]:
    """Return the deterministic recipe registry as a UI-ready list.

    Drives the "Recipes" panel in the /generate page: each entry has a
    name, description, and sample prompt the user can click to pre-fill
    the prompt textarea. Selecting a recipe and submitting the
    pre-filled prompt routes through the same matcher -> render path
    as a typed prompt would, so there's no separate code path or
    template-form complexity.
    """
    from ai_qa_portal.backend.services import recipe_library
    return [
        {
            "name": r.name,
            # Fall back to ``name`` when display_name is empty so older
            # recipes / older deploys keep rendering something useful in
            # the UI instead of a blank card.
            "display_name": r.display_name or r.name,
            "description": r.description,
            "sample_prompt": r.sample_prompt,
        }
        for r in recipe_library.all_recipes()
    ]


@router.post("/save")
def save_generated_script(body: dict):
    """Persist edited Robot code to disk (Save Script flow)."""
    robot_code = (body.get("robot_code") or "").replace("\r\n", "\n")
    if not robot_code.strip():
        raise HTTPException(422, "robot_code is required")

    raw_path = str(body.get("test_path") or "").strip()
    target = Path(raw_path) if raw_path else GENERATED_SUITE
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    else:
        target = target.resolve()

    repo_root = REPO_ROOT.resolve()
    try:
        target.relative_to(repo_root)
    except ValueError as exc:
        raise HTTPException(400, "Refusing to save outside repository root") from exc
    if target.suffix.lower() != ".robot":
        raise HTTPException(400, "Only .robot files can be saved")

    target.parent.mkdir(parents=True, exist_ok=True)
    final = robot_code.rstrip() + "\n"
    target.write_text(final, encoding="utf-8")
    return {
        "ok": True,
        "test_path": str(target),
        "bytes_written": len(final.encode("utf-8")),
    }


@router.post("/record/start")
def start_recording(body: dict):
    if not settings.playwright_enabled or not settings.pw_recording:
        raise HTTPException(403, "Recording mode is not enabled")
    sandbox_url = (body.get("sandbox_url") or "").strip()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    if not sandbox_url or not username or not password:
        raise HTTPException(422, "sandbox_url, username, and password are required")
    try:
        import pw_mcp_bridge

        session_id = pw_mcp_bridge.record_start(
            sandbox_url=sandbox_url,
            username=username,
            password=password,
            persona_id=body.get("persona_id"),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Could not start recording session: {exc}") from exc
    return {"session_id": session_id}


@router.get("/record/{session_id}/actions")
def get_recording_actions(session_id: str):
    if not settings.playwright_enabled or not settings.pw_recording:
        raise HTTPException(403, "Recording mode is not enabled")
    import pw_mcp_bridge

    actions = pw_mcp_bridge.record_actions(session_id)
    return {"session_id": session_id, "actions": actions, "count": len(actions)}


@router.post("/record/{session_id}/stop")
def stop_recording(session_id: str, body: dict | None = None):
    if not settings.playwright_enabled or not settings.pw_recording:
        raise HTTPException(403, "Recording mode is not enabled")
    import pw_mcp_bridge
    from ai_qa_portal.backend.services.recording_translator import translate_actions

    actions = pw_mcp_bridge.record_stop(session_id)
    output_path = GENERATED_SUITE
    test_name = ((body or {}).get("test_name") or "").strip()
    translated = translate_actions(
        actions,
        output_path=output_path,
        test_name_hint=test_name,
    )
    return {
        "session_id": session_id,
        "actions": translated.raw_actions,
        "robot_code": translated.loop_result.final_script,
        "validation_ok": translated.loop_result.converged,
        "validation_errors": [e.model_dump() for e in translated.loop_result.final_report.errors],
        "validation_attempts": [
            {"attempt": a.attempt, "ok": a.report.ok, "error_count": len(a.report.errors)}
            for a in translated.loop_result.attempts
        ],
        "test_path": str(output_path),
    }


@router.post("/robot-suite", response_model=GenerateResponse)
def generate_robot_suite(body: GenerateRequest):
    """Quick-generate: prompt -> validated .robot file.

    Wraps the LLM call in the validate-fix-validate loop so any script
    that ships back has been parsed by ``robot.api`` AND ``robot --dryrun``.
    The on-disk file is the FINAL attempt (best-effort even when the
    loop didn't converge) so the user can still inspect what the LLM
    produced and the UI can render the validator errors inline.

    Phase 1: when project + global flags allow it, ALSO runs the
    Playwright locator gate as a third validation tier. Failures in
    shadow mode log + surface in the response but do not block; in
    load-bearing mode they trigger a fix-prompt retry the same way AST
    or dryrun failures do."""
    try:
        from ai_bridge import (
            generate_test_from_prompt_validated,
            validate_generated_robot,
        )

        # Phase 1: build the optional locator-validation callable. None
        # when feature is off (current default in production) -- the
        # call below is byte-for-byte identical to the pre-Phase-1
        # behaviour in that case.
        locator_validate, shadow_mode = _build_locator_validate_callable(body)

        result = generate_test_from_prompt_validated(
            body.prompt,
            default_app=body.default_app,
            locator_validate=locator_validate,
            locator_shadow_mode=shadow_mode,
        )

        target = Path(GENERATED_SUITE)
        robot_code = (
            target.read_text(encoding="utf-8") if target.is_file() else result.final_script
        )

        # Existing forbidden-keyword lint runs alongside structured
        # validation so we don't lose any of the soft warnings the legacy
        # check produced (e.g. "no Sleep please").
        lint_errors: list[str] = list(result.lint_errors())
        with contextlib.suppress(Exception):
            lint_errors.extend(validate_generated_robot(robot_code))

        ok, validation_errors, attempts = _validation_payload_from_loop(result)
        return GenerateResponse(
            robot_code=robot_code,
            test_path=str(target),
            lint_errors=lint_errors,
            validation_ok=ok,
            validation_errors=validation_errors,
            validation_attempts=attempts,
            provider_switches=_drain_provider_switches(),
            used_recipe=result.used_recipe,
            used_recipe_confidence=result.used_recipe_confidence,
            locator_validation_ok=result.locator_validation_ok,
            locator_validation_count=result.locator_validation_count,
            locator_validation_failed=result.locator_validation_failed,
            locator_validation_shadow=result.locator_validation_shadow,
        )
    except Exception as exc:
        logger.exception("quick-generate failed")
        raise HTTPException(status_code=500, detail=_format_generation_error(exc)) from exc


_MCP_STEP_TIMEOUT_S = int(os.environ.get("MCP_STEP_TIMEOUT_S", "60"))
# Cold init does: manage_session(init) + 2 * import_resource (Robot keyword
# table loads, sequential per the comment in mcp_bridge.init_session) + a
# Selenium browser launch the first time RF-MCP touches a session +
# Salesforce login (network-bound). 30 s was tight enough to fall back even
# on healthy runs; bump to 60 so we only fall back when it's actually wedged.
# Override via MCP_INIT_TIMEOUT_S env var.
_MCP_INIT_TIMEOUT_S = int(os.environ.get("MCP_INIT_TIMEOUT_S", "180"))
_MCP_BUILD_TIMEOUT_S = int(os.environ.get("MCP_BUILD_TIMEOUT_S", "60"))
# Planning is one LLM call (break_prompt_into_steps). The default OpenAI /
# Gemini SDK timeouts are 5-10 minutes which is unacceptable on the user's
# critical path -- if the LLM hangs we'd rather fail fast and fall back to
# Quick Generate (which is also one LLM call but a different prompt and
# often a different provider). 45 s is generous enough for slow networks
# but tight enough that the user isn't staring at "Planning steps" forever.
_MCP_PLAN_TIMEOUT_S = int(os.environ.get("MCP_PLAN_TIMEOUT_S", "90"))
_MCP_STEPWISE_NO_FALLBACK = (
    os.environ.get("MCP_STEPWISE_NO_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on")
)


class _PhaseRecorder:
    """Track per-phase timings and stream lifecycle telemetry."""

    def __init__(self) -> None:
        self.stream_t0 = time.monotonic()
        self.phase_t0 = self.stream_t0
        self.phase_timings_ms: dict[str, int] = {}
        self.current_phase = "idle"
        self.final_status = "error"
        self.fallback_reason: str | None = None
        self.error_message: str | None = None
        self.step_count_planned = 0
        self.step_count_passed = 0
        self.step_count_failed = 0
        self.provider_used: str | None = None
        self.cache_hit = False
        self.robot_chars: int | None = None

    def open_phase(self, name: str) -> dict[str, int | str]:
        now = time.monotonic()
        elapsed = int((now - self.phase_t0) * 1000)
        if self.current_phase not in ("idle", "done", "failed"):
            self.phase_timings_ms[self.current_phase] = elapsed
        self.phase_t0 = now
        self.current_phase = name
        return {
            "name": name,
            "elapsed_ms_phase": elapsed,
            "elapsed_ms_total": int((now - self.stream_t0) * 1000),
        }

    def close_current_phase(self) -> None:
        now = time.monotonic()
        if self.current_phase not in ("idle", "done", "failed"):
            self.phase_timings_ms[self.current_phase] = int((now - self.phase_t0) * 1000)


def _metrics_payload_from_recorder(
    recorder: _PhaseRecorder,
    *,
    mode: str,
    prompt_chars: int,
    job_id: str | None = None,
) -> GenerationMetric:
    now = datetime.now(UTC)
    return GenerationMetric(
        job_id=job_id,
        started_at=now - timedelta(milliseconds=int((time.monotonic() - recorder.stream_t0) * 1000)),
        finished_at=now,
        mode=mode,
        prompt_chars=prompt_chars,
        phase_timings=dict(recorder.phase_timings_ms),
        final_status=recorder.final_status,
        fallback_reason=recorder.fallback_reason,
        error_message=recorder.error_message,
        robot_chars=recorder.robot_chars,
        step_count_planned=recorder.step_count_planned,
        step_count_passed=recorder.step_count_passed,
        step_count_failed=recorder.step_count_failed,
        provider_used=recorder.provider_used,
        cache_hit=recorder.cache_hit,
    )


def _record_metric(recorder: _PhaseRecorder, *, mode: str, prompt_chars: int, job_id: str | None = None) -> None:
    with SessionLocal() as session:
        metric = _metrics_payload_from_recorder(
            recorder,
            mode=mode,
            prompt_chars=prompt_chars,
            job_id=job_id,
        )
        session.add(metric)
        session.commit()


def _phase_payload(name: str, recorder: _PhaseRecorder) -> dict[str, int | str]:
    return recorder.open_phase(name)


def _event_record(seq: int, event: str, payload: dict | str) -> dict[str, Any]:
    return {
        "seq": seq,
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        "payload": payload,
    }


def _append_job_event(session, job: GenerationJob, event: str, payload: dict | str) -> dict[str, Any]:
    job.event_seq = (job.event_seq or 0) + 1
    record = _event_record(job.event_seq, event, payload)
    rows = list(job.phase_log or [])
    rows.append(record)
    job.phase_log = rows
    job.updated_at = datetime.now(UTC)
    session.add(job)
    session.commit()
    return record


def _publish_job_event(session, job: GenerationJob, event: str, payload: dict | str) -> dict[str, Any]:
    record = _append_job_event(session, job, event, payload)
    broker.publish(job.id, record)
    return record


def _job_is_cancelled(session, job_id: str) -> bool:
    row = session.query(GenerationJob).filter(GenerationJob.id == job_id).one_or_none()
    return bool(row and row.cancellation_requested_at is not None)


def _run_quick_fallback_or_fail(
    body: GenerateRequest,
    recorder: _PhaseRecorder,
    *,
    reason: str,
    user_message: str,
    no_fallback: bool,
) -> tuple[dict | None, str | None]:
    recorder.fallback_reason = reason
    if no_fallback:
        recorder.final_status = "failed"
        recorder.error_message = user_message
        return None, user_message
    resp = _quick_generate_fallback(body.prompt, default_app=body.default_app, body=body)
    recorder.final_status = "fallback"
    recorder.robot_chars = len(resp.robot_code or "")
    return resp.model_dump(mode="json"), None


def _run_with_timeout(fn, *args, timeout: float, label: str):
    """Run a blocking callable in a thread with a hard timeout.

    DO NOT use ``ThreadPoolExecutor`` as a context manager here. ``__exit__``
    calls ``shutdown(wait=True)``, which blocks until the worker thread
    completes -- even if we just raised TimeoutError. The hung MCP /
    Selenium / LLM call inside the worker would happily ignore Python's
    timeout and pin the executor's shutdown for minutes, defeating the
    entire point of having a timeout. Symptom: ``Opening session`` pill
    in the UI sat at 136 s with the backend SSE stream still open and no
    fallback events emitted, despite the orchestrator's 60 s budget.

    The fix: manage the pool manually and shutdown with
    ``wait=False, cancel_futures=True`` so on timeout we abandon the
    worker (it's a daemon thread; it'll die with the process) and the
    caller actually gets the TimeoutError it asked for.
    """
    import concurrent.futures

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn, *args)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as e:
            raise TimeoutError(f"{label} did not finish within {timeout}s") from e
    finally:
        # On success the worker is already done so this is a no-op; on
        # timeout we deliberately don't wait for the abandoned worker.
        pool.shutdown(wait=False, cancel_futures=True)


def _quick_generate_fallback(
    prompt: str,
    *,
    default_app: str = "",
    body: GenerateRequest | None = None,
) -> GenerateResponse:
    """Stepwise fallback path. Routes through the SAME validate-fix-validate
    loop as the primary Quick Generate endpoint -- without this parity, a
    wedged RF-MCP would silently bypass the new safety net.

    Phase 1: when ``body`` is provided AND the project flags allow it,
    the fallback also runs the Playwright locator gate. Stepwise users
    get the same dependability win as Quick Generate users when MCP is
    down for any reason.
    """
    from ai_bridge import generate_test_from_prompt_validated, validate_generated_robot

    locator_validate = None
    shadow_mode = False
    if body is not None:
        locator_validate, shadow_mode = _build_locator_validate_callable(body)

    result = generate_test_from_prompt_validated(
        prompt,
        default_app=default_app,
        locator_validate=locator_validate,
        locator_shadow_mode=shadow_mode,
    )
    target = Path(GENERATED_SUITE)
    code = target.read_text(encoding="utf-8") if target.is_file() else result.final_script

    lints: list[str] = list(result.lint_errors())
    with contextlib.suppress(Exception):
        lints.extend(validate_generated_robot(code))

    ok, validation_errors, attempts = _validation_payload_from_loop(result)
    return GenerateResponse(
        robot_code=code,
        test_path=str(target),
        lint_errors=lints,
        validation_ok=ok,
        validation_errors=validation_errors,
        validation_attempts=attempts,
        provider_switches=_drain_provider_switches(),
        used_recipe=result.used_recipe,
        used_recipe_confidence=result.used_recipe_confidence,
        locator_validation_ok=result.locator_validation_ok,
        locator_validation_count=result.locator_validation_count,
        locator_validation_failed=result.locator_validation_failed,
        locator_validation_shadow=result.locator_validation_shadow,
    )


# Substrings that indicate the cached session is no longer usable (browser
# closed, RF-MCP restarted, Salesforce timed us out). Heuristic only -- when
# in doubt we'd rather invalidate and re-login than serve stale failures.
_SESSION_ERROR_HINTS = (
    "session not found",
    "no such session",
    "invalid session id",
    "session expired",
    "browser is closed",
    "no browser",
    "webdriver",
)


def _looks_like_session_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(hint in msg for hint in _SESSION_ERROR_HINTS)


_SAVE_ACTION_ARGS = {"save", "save & new", "save & close"}
_SOBJECT_HINTS = ("lead", "account", "contact", "opportunity", "case", "campaign")


def _is_save_step(keyword: str, args: list[Any]) -> bool:
    kw = (keyword or "").strip().lower()
    first_arg = str(args[0]).strip().lower() if args else ""
    if kw.endswith("attempt save and auto-heal missing fields"):
        return True
    if kw.endswith("save and heal"):
        return True
    if kw.endswith("select dialog button") and first_arg in _SAVE_ACTION_ARGS:
        return True
    return False


def _save_action_for_step(keyword: str, args: list[Any]) -> str:
    kw = (keyword or "").strip().lower()
    if kw.endswith("select dialog button") and args:
        return str(args[0] or "Save")
    return "Save"


def _infer_sobject_for_heal(
    *,
    current_keyword: str,
    full_steps: list[dict],
    current_index: int,
    prompt: str,
) -> str:
    lookback = [current_keyword]
    for idx in range(max(0, current_index - 6), current_index):
        step = full_steps[idx] if idx < len(full_steps) else {}
        lookback.append(str(step.get("keyword") or ""))
    lookback.append(prompt or "")
    joined = " ".join(lookback).lower()
    for hint in _SOBJECT_HINTS:
        if hint in joined:
            return hint.capitalize()
    return "Lead"


@router.post("/mcp-stepwise")
def generate_mcp_stepwise(body: GenerateRequest):
    """MCP Stepwise: decompose prompt into steps, execute each, build suite.

    Non-streaming variant kept for external integrations and the legacy
    `/api/generate/stepwise` frontend call. Internally delegates to the
    same ``_iter_stepwise_events`` engine the streaming endpoint uses,
    so every brain-side improvement (verified-recipe replay, scenario
    analysis, semantic memory, project warnings, plan validator,
    sequence linter, per-step recovery, outcome capture) flows through
    here too. Previously this endpoint had a parallel hand-rolled
    implementation that bypassed every Tier 1+2+3 brain feature -- a
    silent contract drift that made the non-stream path silently
    unreliable for external integrations.
    """
    notes: list[str] = []
    final_payload: dict | None = None
    final_error: str | None = None
    fallback_used = False

    for ev, payload in _iter_stepwise_events(body, no_fallback=False):
        payload = payload or {}
        if ev == "note":
            msg = payload.get("message")
            if msg:
                notes.append(str(msg))
        elif ev == "phase":
            if payload.get("name") == "fallback":
                fallback_used = True
        elif ev == "result":
            final_payload = payload
        elif ev == "error":
            final_error = payload.get("message") or "Generation failed"

    if final_payload is None:
        # Either the job ended in the explicit-failure path (no_fallback
        # configured upstream) or quick fallback itself failed. Surface a
        # clean 500 with the captured error.
        raise HTTPException(
            status_code=500,
            detail=final_error or "Stepwise generation produced no result.",
        )

    # Repackage the JSON-serialised result back into a typed
    # GenerateResponse. The dict shape is already model_dump'd by
    # _iter_stepwise_events so this round-trip is cheap.
    try:
        resp = GenerateResponse.model_validate(final_payload)
    except Exception:
        # Should never happen unless GenerateResponse drifts; fall back
        # to a permissive read so the endpoint still returns useful
        # content rather than 500ing on a contract bug.
        resp = GenerateResponse(
            robot_code=final_payload.get("robot_code") or "",
            test_path=final_payload.get("test_path") or str(GENERATED_SUITE),
        )

    if notes:
        existing = list(resp.lint_errors or [])
        resp.lint_errors = existing + notes
    if fallback_used and not any("Quick Generate" in n for n in (resp.lint_errors or [])):
        resp.lint_errors = list(resp.lint_errors or []) + [
            "Used Quick Generate fallback (Stepwise pipeline unavailable)."
        ]
    return resp


def _validate_existing_suite(
    path: Path,
    body: GenerateRequest | None = None,
) -> tuple[bool, list[ValidationErrorPayload], list[GenerationAttempt], dict]:
    """Run the validator + dryrun against an already-on-disk suite (e.g.
    one built by ``mcp_bridge.build_suite``). No retry loop -- the LLM
    isn't in scope for the Stepwise builder, so we just surface the
    findings.

    Phase 1: when ``body`` is provided AND project flags allow it, also
    runs the Playwright locator gate. Stepwise users get the same
    dependability win as Quick Generate users on the post-build path.

    Returns ``(ok, payloads, attempts, locator_stats)``. ``locator_stats``
    is a dict with ``ok`` (Optional[bool]), ``count``, ``failed``,
    ``shadow``. Stays ``ok=None`` when the gate didn't run -- caller
    passes those straight through to the response.
    """
    from ai_qa_portal.backend.services import script_dryrun as sd
    from ai_qa_portal.backend.services import script_validator as sv

    locator_stats: dict[str, Any] = {
        "ok": None, "count": 0, "failed": 0, "shadow": False,
    }

    if not path.is_file():
        return True, [], [], locator_stats

    ast_report = sv.validate(path)
    dry_report = sd.dryrun(path) if ast_report.ok else sv.ValidationReport(ok=True, errors=[])

    # Phase 1: optional locator gate. Only run when AST + dryrun pass --
    # consistent with the Quick Generate loop's ordering.
    loc_errors: list = []
    if ast_report.ok and dry_report.ok and body is not None:
        locator_validate, shadow = _build_locator_validate_callable(body)
        if locator_validate is not None:
            try:
                loc_report = locator_validate(path)
                locator_stats["ok"] = loc_report.ok
                locator_stats["failed"] = len(loc_report.errors)
                locator_stats["count"] = max(locator_stats["count"], locator_stats["failed"])
                locator_stats["shadow"] = shadow
                if not loc_report.ok and not shadow:
                    loc_errors = list(loc_report.errors)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("stepwise locator gate crashed: %s", exc)

    combined = sv.ValidationReport(
        ok=ast_report.ok and dry_report.ok and (locator_stats["shadow"] or not loc_errors),
        errors=list(ast_report.errors) + list(dry_report.errors) + loc_errors,
    )
    payloads: list[ValidationErrorPayload] = []
    for e in combined.errors:
        page_url = ""
        suggested_locator = ""
        if e.kind == "locator_not_found":
            if "page_url=" in (e.message or ""):
                try:
                    page_url = e.message.split("page_url=", 1)[1].strip()
                except IndexError:
                    pass
            if e.closest_matches:
                suggested_locator = e.closest_matches[0]
        payloads.append(
            ValidationErrorPayload(
                line=e.line, column=e.column, kind=str(e.kind),
                symbol=e.symbol, message=e.message,
                closest_matches=list(e.closest_matches), snippet=e.snippet,
                page_url=page_url,
                suggested_locator=suggested_locator,
            )
        )
    attempts = [GenerationAttempt(
        attempt=1, ok=combined.ok, error_count=len(combined.errors),
    )]
    return combined.ok, payloads, attempts, locator_stats


def _sse(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    safe = payload.replace("\r", "")
    return f"event: {event}\ndata: {safe}\n\n"


def _iter_stepwise_events(
    body: GenerateRequest,
    *,
    no_fallback: bool,
    cancel_check: Callable[[], bool] | None = None,
    metrics_mode: str = "mcp_stepwise",
    metric_job_id: str | None = None,
):
    """Core stepwise engine yielding `(event, payload)` tuples."""
    import mcp_bridge
    from ai_qa_portal.backend.services import planner_brain
    from ai_qa_portal.backend.services.form_healer import FormHealer

    notes: list[str] = []
    recorder = _PhaseRecorder()
    project_slug = (body.project_name or "").strip() or None
    healer = FormHealer()
    logger.info(
        "mcp-stepwise stream start; project=%s; prompt_chars=%d",
        project_slug or "(none)", len(body.prompt),
    )

    try:
        yield "phase", _phase_payload("mcp-init", recorder)
        if cancel_check and cancel_check():
            raise JobCancelled("Generation cancelled by user.")
        try:
            if not mcp_bridge.is_server_running():
                mcp_bridge.start_mcp_server()
        except Exception as exc:
            msg = f"RF-MCP unavailable: {exc}."
            logger.warning("%s", msg)
            yield "note", {"message": f"{msg} Falling back to Quick Generate."}
            fallback_payload, fallback_err = _run_quick_fallback_or_fail(
                body, recorder,
                reason="rfmcp_unavailable",
                user_message=f"{msg} Quick fallback disabled.",
                no_fallback=no_fallback,
            )
            if fallback_err:
                yield "phase", _phase_payload("failed", recorder)
                yield "error", {"message": fallback_err}
                return
            yield "phase", _phase_payload("fallback", recorder)
            yield "result", fallback_payload
            return

        yield "phase", _phase_payload("planning", recorder)
        if cancel_check and cancel_check():
            raise JobCancelled("Generation cancelled by user.")
        try:
            # Wrap planner_brain.plan_steps in the same hard-timeout pattern
            # we use for every other phase, but use a lambda so we can pass
            # the keyword arguments through. The brain internally may make
            # multiple LLM calls (analyze + recall + plan + up to 3 replans)
            # so we give it a slightly more generous timeout than a single
            # planner call would need.
            def _plan():
                return planner_brain.plan_steps(
                    body.prompt,
                    default_app=body.default_app,
                    project_slug=project_slug,
                )

            steps, plan_meta = _run_with_timeout(
                _plan,
                timeout=_MCP_PLAN_TIMEOUT_S * 2,
                label="LLM step planning",
            )
        except TimeoutError as exc:
            msg = f"{exc}."
            logger.warning("LLM planning timed out: %s", exc)
            yield "note", {"message": f"{msg} Falling back to Quick Generate."}
            fallback_payload, fallback_err = _run_quick_fallback_or_fail(
                body, recorder,
                reason="planning_timeout",
                user_message=f"{msg} Quick fallback disabled.",
                no_fallback=no_fallback,
            )
            if fallback_err:
                yield "phase", _phase_payload("failed", recorder)
                yield "error", {"message": fallback_err}
                return
            yield "phase", _phase_payload("fallback", recorder)
            yield "result", fallback_payload
            return
        except Exception as exc:
            recorder.final_status = "error"
            recorder.error_message = _format_generation_error(exc)
            logger.exception("step planning failed")
            yield "phase", _phase_payload("failed", recorder)
            yield "error", {"message": recorder.error_message}
            return
        recorder.step_count_planned = len(steps)

        # Surface what the brain did for observability.
        if plan_meta.get("recipe_hit"):
            hit = plan_meta["recipe_hit"]
            yield "note", {
                "message": (
                    f"Replayed verified recipe (sim={hit['similarity']:.2f}, "
                    f"prior_successes={hit['success_count']})."
                ),
            }
        if plan_meta.get("scenario_analysis_used"):
            yield "note", {"message": "Used RF-MCP scenario analysis hints."}
        if plan_meta.get("recall_hits"):
            yield "note", {
                "message": f"Used {plan_meta['recall_hits']} recalled past sequence(s).",
            }
        if plan_meta.get("project_warnings"):
            yield "note", {
                "message": (
                    f"Applied {plan_meta['project_warnings']} project-specific "
                    "reliability warning(s)."
                ),
            }
        yield "note", {"message": f"Planned {len(steps)} step(s)"}

        yield "phase", _phase_payload("session", recorder)
        if cancel_check and cancel_check():
            raise JobCancelled("Generation cancelled by user.")
        cache_hit = False
        try:
            session_id, cache_hit = _run_with_timeout(
                mcp_bridge.get_or_init_session,
                body.sandbox_url,
                body.username,
                body.password,
                timeout=_MCP_INIT_TIMEOUT_S,
                label="MCP init_session",
            )
            recorder.cache_hit = bool(cache_hit)
            if cache_hit:
                yield "note", {"message": "Reused warm Salesforce session (skipped re-login)."}
        except Exception as exc:
            msg = f"MCP init failed: {exc}."
            logger.warning("MCP init_session failed: %s", exc)
            yield "note", {"message": f"{msg} Falling back to Quick Generate."}
            fallback_payload, fallback_err = _run_quick_fallback_or_fail(
                body, recorder,
                reason="init_timeout",
                user_message=f"{msg} Quick fallback disabled.",
                no_fallback=no_fallback,
            )
            if fallback_err:
                yield "phase", _phase_payload("failed", recorder)
                yield "error", {"message": fallback_err}
                return
            yield "phase", _phase_payload("fallback", recorder)
            yield "result", fallback_payload
            return

        yield "phase", _phase_payload("executing", recorder)
        step_results: list[dict] = []
        total = len(steps)
        session_invalidated = False
        # Recovery budget: caps the total number of LLM-driven replan
        # attempts in this run, AND tracks attempts per failed keyword
        # name so the model can't loop on the same keyword forever.
        # We saw runs where the model kept suggesting the same keyword
        # as its own "replacement", burning ~10 s of browser time per
        # cycle. The two-tier budget hard-stops that.
        max_recovery_total = int(os.environ.get("MCP_PLANNER_RECOVERY_BUDGET", "6"))
        max_recovery_per_keyword = int(
            os.environ.get("MCP_PLANNER_RECOVERY_PER_KEYWORD", "2")
        )
        recovery_total = 0
        recovery_by_keyword: dict[str, int] = {}
        idx = 0
        executed_steps: list[dict] = []
        while idx < len(steps):
            if cancel_check and cancel_check():
                raise JobCancelled("Generation cancelled by user.")
            step = steps[idx]
            display_index = idx + 1
            idx += 1
            kw = step.get("keyword", "")
            args = step.get("args", [])
            step_t0 = time.monotonic()
            try:
                if _is_save_step(kw, args):
                    heal_events: list[dict[str, Any]] = []

                    def _emit_heal(ev: dict[str, Any]) -> None:
                        heal_events.append(ev)

                    inferred_sobject = _infer_sobject_for_heal(
                        current_keyword=kw,
                        full_steps=steps,
                        current_index=display_index - 1,
                        prompt=body.prompt,
                    )
                    save_action = _save_action_for_step(kw, args)
                    heal_result = _run_with_timeout(
                        lambda: healer.heal_save(
                            session_id=session_id,
                            sobject=inferred_sobject,
                            save_action=save_action,
                            project_slug=project_slug,
                            run_id=metric_job_id,
                            job_id=metric_job_id,
                            step_index=display_index - 1,
                            duplicate_strategy="regenerate",
                            sandbox_url=body.sandbox_url,
                            username=body.username,
                            password=body.password,
                            event_cb=_emit_heal,
                        ),
                        timeout=_MCP_STEP_TIMEOUT_S,
                        label=f"MCP heal-save {kw}",
                    )
                    for he in heal_events:
                        yield "heal", he
                    if heal_result.outcome not in ("passed", "skipped"):
                        raise RuntimeError(
                            f"save auto-heal {heal_result.outcome}: {heal_result.reason or 'unknown reason'}"
                        )
                    result = {"status": heal_result.outcome, "reason": heal_result.reason}
                else:
                    result = _run_with_timeout(
                        mcp_bridge.execute_step,
                        session_id,
                        kw,
                        args,
                        timeout=_MCP_STEP_TIMEOUT_S,
                        label=f"MCP step {kw}",
                    )
                recorder.step_count_passed += 1
                step_results.append({"keyword": kw, "args": args, "status": "pass", "output": str(result)})
                executed_steps.append({"keyword": kw, "args": list(args)})
                yield "step", {
                    "index": display_index,
                    "total": max(total, idx),
                    "keyword": kw,
                    "args": args,
                    "status": "pass",
                    "elapsed_ms": int((time.monotonic() - step_t0) * 1000),
                }
                with contextlib.suppress(Exception):
                    planner_brain.record_keyword_outcome(
                        project_slug=project_slug, keyword=kw, success=True,
                    )
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                notes.append(f"step '{kw}' failed: {msg}")
                yield "step", {
                    "index": display_index,
                    "total": max(total, idx),
                    "keyword": kw,
                    "args": args,
                    "status": "fail",
                    "error": msg,
                    "elapsed_ms": int((time.monotonic() - step_t0) * 1000),
                }
                if cache_hit and not session_invalidated and _looks_like_session_error(exc):
                    mcp_bridge.invalidate_cached_session(body.sandbox_url, body.username)
                    session_invalidated = True
                    notes.append("Cached session looked stale; will rebuild on next run.")
                with contextlib.suppress(Exception):
                    planner_brain.record_keyword_outcome(
                        project_slug=project_slug,
                        keyword=kw,
                        success=False,
                        error=msg,
                    )

                step_results.append({"keyword": kw, "args": args, "status": "fail", "error": msg})
                recorder.step_count_failed += 1

                # Adaptive recovery: ask the LLM for a single replacement
                # step (or skip / abort) before continuing the cascade.
                # Two-tier budget: per-keyword (so the model can't loop on
                # the same suggestion) AND per-run (so a runaway recovery
                # session can't burn arbitrary browser time).
                if recovery_total >= max_recovery_total:
                    continue
                kw_norm = (kw or "").strip().lower()
                if recovery_by_keyword.get(kw_norm, 0) >= max_recovery_per_keyword:
                    continue
                recovery_total += 1
                recovery_by_keyword[kw_norm] = recovery_by_keyword.get(kw_norm, 0) + 1

                page_state = planner_brain.get_page_state_safe(session_id)
                decision = planner_brain.replan_failed_step(
                    failed_keyword=kw,
                    failed_args=list(args),
                    error_message=msg,
                    page_state=page_state,
                    full_plan=steps,
                    failed_index=display_index - 1,
                    project_slug=project_slug,
                )
                if not decision:
                    continue

                action = (decision.get("action") or "").lower()
                if action == "abort":
                    reason = decision.get("reason") or "planner aborted"
                    notes.append(f"Recovery aborted run: {reason}")
                    yield "note", {"message": f"Recovery aborted run: {reason}"}
                    break
                if action == "skip":
                    notes.append(f"Recovery skipped step '{kw}'.")
                    yield "note", {"message": f"Recovery skipped step '{kw}'."}
                    continue
                if action == "replace":
                    replacement_kw = str(decision.get("keyword") or "").strip()
                    if not replacement_kw:
                        continue
                    # Reject self-replacements -- the LLM sometimes panics
                    # and suggests the same keyword that just failed,
                    # which is what tipped the runaway loop we observed.
                    if replacement_kw.strip().lower() == kw_norm:
                        notes.append(
                            f"Recovery suggested re-running '{kw}' "
                            "verbatim; ignoring and skipping."
                        )
                        yield "note", {
                            "message": (
                                f"Recovery skipped: model offered the same "
                                f"keyword '{kw}' as a replacement."
                            ),
                        }
                        continue
                    replacement = {
                        "keyword": replacement_kw,
                        "args": list(decision.get("args") or []),
                    }
                    notes.append(
                        f"Recovery replaced step '{kw}' -> '{replacement_kw}'."
                    )
                    yield "note", {
                        "message": (
                            f"Recovery replaced step '{kw}' -> '{replacement_kw}'."
                        ),
                    }
                    # Rewind: insert the replacement at the current position
                    # so the loop runs it next iteration.
                    steps.insert(idx, replacement)

        yield "phase", _phase_payload("build", recorder)
        suite_name = body.test_name or "MCP Stepwise Test"
        try:
            robot_code = _run_with_timeout(
                mcp_bridge.build_suite,
                session_id,
                suite_name,
                timeout=_MCP_BUILD_TIMEOUT_S,
                label="MCP build_suite",
            )
        except Exception as exc:
            notes.append(f"build_suite failed ({exc}); used local fallback")
            robot_code = _build_fallback_suite(step_results, suite_name)

        if not robot_code or not robot_code.strip():
            yield "note", {"message": "MCP returned empty suite. Falling back to Quick Generate."}
            fallback_payload, fallback_err = _run_quick_fallback_or_fail(
                body, recorder,
                reason="empty_suite",
                user_message="MCP returned empty suite and Quick fallback is disabled.",
                no_fallback=no_fallback,
            )
            if fallback_err:
                yield "phase", _phase_payload("failed", recorder)
                yield "error", {"message": fallback_err}
                return
            if fallback_payload is not None:
                fallback_payload["lint_errors"] = list(fallback_payload.get("lint_errors") or []) + notes
            yield "phase", _phase_payload("fallback", recorder)
            yield "result", fallback_payload
            return

        try:
            from ai_bridge import (
                fix_misplaced_setup_teardown,
                format_robot_code,
                strip_credential_variable_overrides,
                strip_empty_variable_overrides,
                strip_hallucinated_csv_variables_from_suite,
                strip_llm_robot_garbage,
            )
            robot_code = strip_credential_variable_overrides(robot_code)
            robot_code = strip_empty_variable_overrides(robot_code)
            robot_code = strip_llm_robot_garbage(robot_code)
            robot_code = strip_hallucinated_csv_variables_from_suite(robot_code)
            robot_code = fix_misplaced_setup_teardown(robot_code)

            final = robot_code.rstrip() + "\n"
            GENERATED_SUITE.parent.mkdir(parents=True, exist_ok=True)
            GENERATED_SUITE.write_text(final, encoding="utf-8")
            with contextlib.suppress(Exception):
                format_robot_code(GENERATED_SUITE)

            validation_ok = True
            validation_errors: list[ValidationErrorPayload] = []
            validation_attempts: list[GenerationAttempt] = []
            locator_stats: dict[str, Any] = {"ok": None, "count": 0, "failed": 0, "shadow": False}
            with contextlib.suppress(Exception):
                validation_ok, validation_errors, validation_attempts, locator_stats = (
                    _validate_existing_suite(Path(GENERATED_SUITE), body=body)
                )

            if locator_stats["ok"] is not None:
                yield "phase", _phase_payload("locator_check", recorder)
                yield "note", {
                    "message": (
                        f"Locator validation: {locator_stats['count'] - locator_stats['failed']} "
                        f"of {locator_stats['count']} live"
                        + (" (shadow mode -- not blocking)" if locator_stats["shadow"] else "")
                    ),
                }

            provider_switches = _drain_provider_switches()
            if provider_switches:
                recorder.provider_used = provider_switches[-1].to_provider
            else:
                recorder.provider_used = (os.environ.get("LLM_PROVIDER") or "").strip().lower() or None
            resp = GenerateResponse(
                robot_code=GENERATED_SUITE.read_text(encoding="utf-8"),
                test_path=str(GENERATED_SUITE),
                lint_errors=notes,
                validation_ok=validation_ok,
                validation_errors=validation_errors,
                validation_attempts=validation_attempts,
                provider_switches=provider_switches,
                locator_validation_ok=locator_stats["ok"],
                locator_validation_count=locator_stats["count"],
                locator_validation_failed=locator_stats["failed"],
                locator_validation_shadow=locator_stats["shadow"],
            )
        except Exception as exc:
            recorder.final_status = "error"
            recorder.fallback_reason = "post_processing"
            recorder.error_message = f"Post-processing failed: {_format_generation_error(exc)}"
            logger.exception("post-processing pipeline failed (suite path=%s)", GENERATED_SUITE)
            yield "phase", _phase_payload("failed", recorder)
            yield "error", {"message": recorder.error_message}
            return

        recorder.final_status = "success"
        recorder.robot_chars = len(resp.robot_code or "")

        # Capture the successful plan into the verified-recipe store and
        # RF-MCP semantic memory so future similar prompts can short-circuit
        # the LLM. We only persist when the run was substantively
        # successful: at least 4 successfully-executed steps AND a
        # >=70% pass rate over the originally-planned step count. This
        # avoids polluting the recipe store with partial / minimal runs
        # (e.g. an early-exit that only completed Login + 1 keyword)
        # which would later short-circuit a richer prompt to a stub plan.
        planned_total = recorder.step_count_planned or len(executed_steps)
        passed = recorder.step_count_passed
        pass_ratio = (passed / planned_total) if planned_total else 0.0
        recipe_quality_ok = (
            len(executed_steps) >= 4
            and passed >= 4
            and pass_ratio >= 0.7
        )
        if executed_steps and recipe_quality_ok:
            with contextlib.suppress(Exception):
                planner_brain.record_successful_plan(
                    body.prompt,
                    executed_steps,
                    project_slug=project_slug,
                )
            with contextlib.suppress(Exception):
                planner_brain.store_recall_safe(body.prompt, executed_steps)
        elif executed_steps:
            logger.info(
                "stepwise: skipping verified-recipe capture "
                "(passed=%d/%d, ratio=%.2f, executed=%d)",
                passed, planned_total, pass_ratio, len(executed_steps),
            )

        yield "phase", _phase_payload("done", recorder)
        yield "result", resp.model_dump(mode="json")
    except JobCancelled as exc:
        recorder.final_status = "cancelled"
        recorder.error_message = str(exc)
        yield "phase", _phase_payload("failed", recorder)
        yield "error", {"message": recorder.error_message}
    except Exception as exc:  # noqa: BLE001
        recorder.final_status = "error"
        recorder.error_message = _format_generation_error(exc)
        logger.exception("mcp-stepwise stream crashed unexpectedly")
        yield "phase", _phase_payload("failed", recorder)
        yield "error", {"message": recorder.error_message}
    finally:
        recorder.close_current_phase()
        _record_metric(
            recorder,
            mode=metrics_mode,
            prompt_chars=len(body.prompt or ""),
            job_id=metric_job_id,
        )


def _create_generation_job(body: GenerateRequest, current_user: User) -> str:
    with SessionLocal() as session:
        row = GenerationJob(
            owner_user_id=current_user.id,
            project_slug=body.project_name or None,
            mode=body.generation_mode or "mcp_stepwise",
            prompt=body.prompt,
            request_payload=body.model_dump(mode="json"),
            status=GenerationStatus.queued.value,
            current_phase="queued",
            phase_log=[],
            event_seq=0,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def _run_generation_job(job_id: str) -> None:
    with SessionLocal() as session:
        job = session.query(GenerationJob).filter(GenerationJob.id == job_id).one_or_none()
        if job is None:
            return
        job.status = GenerationStatus.running.value
        job.current_phase = "queued"
        job.started_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        session.add(job)
        session.commit()
        payload = dict(job.request_payload or {})

    body = GenerateRequest(**payload)

    def _cancel_check() -> bool:
        with SessionLocal() as cancel_session:
            return _job_is_cancelled(cancel_session, job_id)

    terminal_status = GenerationStatus.failed.value
    terminal_error: str | None = None
    final_robot_code: str | None = None

    for event, payload in _iter_stepwise_events(
        body,
        no_fallback=_MCP_STEPWISE_NO_FALLBACK,
        cancel_check=_cancel_check,
        metrics_mode=("mcp_stepwise_no_fallback" if _MCP_STEPWISE_NO_FALLBACK else "mcp_stepwise"),
        metric_job_id=job_id,
    ):
        with SessionLocal() as session:
            job = session.query(GenerationJob).filter(GenerationJob.id == job_id).one_or_none()
            if job is None:
                return
            if event == "phase":
                phase_name = str((payload or {}).get("name") or "")
                if phase_name:
                    job.current_phase = phase_name
            if event == "result":
                final_robot_code = str((payload or {}).get("robot_code") or "")
                terminal_status = GenerationStatus.succeeded.value
            if event == "error":
                terminal_error = str((payload or {}).get("message") or "Generation failed")
                terminal_status = (
                    GenerationStatus.cancelled.value
                    if job.cancellation_requested_at is not None
                    else GenerationStatus.failed.value
                )
            _publish_job_event(session, job, event, payload)

    with SessionLocal() as session:
        job = session.query(GenerationJob).filter(GenerationJob.id == job_id).one_or_none()
        if job is None:
            return
        job.status = terminal_status
        job.finished_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        if terminal_error:
            job.error_message = terminal_error
        if final_robot_code:
            job.robot_code = final_robot_code
        session.add(job)
        session.commit()
        # Notify the job's submitter that their long-running stepwise
        # generation finished. The bell badge updates within ~60s
        # because of SWR polling on /api/me/notifications.
        try:
            if job.owner_user_id and terminal_status in (
                GenerationStatus.succeeded.value,
                GenerationStatus.failed.value,
            ):
                from ..services.audit_actions import GENERATION_COMPLETED
                from ..services.db import push_notification
                ok = terminal_status == GenerationStatus.succeeded.value
                push_notification(
                    session,
                    user_id=str(job.owner_user_id),
                    type=GENERATION_COMPLETED,
                    title=(
                        "Generation completed"
                        if ok
                        else f"Generation failed: {(terminal_error or 'unknown')[:80]}"
                    ),
                    body=f"Job {job.id[:8]} · {job.mode or 'stepwise'}",
                    action_url="/generate",
                )
        except Exception:
            pass


def _job_stream(job_id: str, *, from_seq: int = 0):
    # Replay persisted events first.
    with SessionLocal() as session:
        job = session.query(GenerationJob).filter(GenerationJob.id == job_id).one_or_none()
        if job is None:
            yield _sse("error", {"message": "Generation job not found"})
            return
        rows = [r for r in list(job.phase_log or []) if int(r.get("seq", 0)) > from_seq]
        status = job.status
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, dict):
            payload = {**payload, "seq": row.get("seq"), "ts": row.get("ts")}
        yield _sse(str(row.get("event") or "message"), payload)

    if status in (
        GenerationStatus.succeeded.value,
        GenerationStatus.failed.value,
        GenerationStatus.cancelled.value,
    ):
        return

    q = broker.subscribe(job_id)
    try:
        last = time.monotonic()
        while True:
            try:
                row = q.get(timeout=5)
            except Exception:
                yield ": keepalive\n\n"
                with SessionLocal() as session:
                    job = session.query(GenerationJob).filter(GenerationJob.id == job_id).one_or_none()
                    if job is None:
                        return
                    if job.status in (
                        GenerationStatus.succeeded.value,
                        GenerationStatus.failed.value,
                        GenerationStatus.cancelled.value,
                    ):
                        return
                continue
            payload = row.get("payload")
            if isinstance(payload, dict):
                payload = {**payload, "seq": row.get("seq"), "ts": row.get("ts")}
            yield _sse(str(row.get("event") or "message"), payload)
            last = time.monotonic()
            with SessionLocal() as session:
                job = session.query(GenerationJob).filter(GenerationJob.id == job_id).one_or_none()
                if job is None:
                    return
                if job.status in (
                    GenerationStatus.succeeded.value,
                    GenerationStatus.failed.value,
                    GenerationStatus.cancelled.value,
                ) and (row.get("event") in ("result", "error") or (time.monotonic() - last) > 1):
                    return
    finally:
        broker.unsubscribe(job_id, q)


def _load_generation_job_for_user(session, job_id: str, user: User) -> GenerationJob:
    job = session.query(GenerationJob).filter(GenerationJob.id == job_id).one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    is_admin = user.is_admin or user.global_role == "admin"
    if not is_admin and job.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="You do not have access to this generation job")
    return job


@router.post("/jobs")
def create_generation_job(body: GenerateRequest, current_user: User = Depends(get_current_user)):
    job_id = _create_generation_job(body, current_user)
    submit_generation_job(lambda: _run_generation_job(job_id))
    return {"job_id": job_id}


@router.get("/jobs/in-flight")
def generation_job_in_flight(current_user: User = Depends(get_current_user)):
    with SessionLocal() as session:
        row = (
            session.query(GenerationJob)
            .filter(
                GenerationJob.owner_user_id == current_user.id,
                GenerationJob.status.in_([GenerationStatus.queued.value, GenerationStatus.running.value]),
            )
            .order_by(GenerationJob.created_at.desc())
            .first()
        )
        if row is None:
            return {"job": None}
        return {
            "job": {
                "id": row.id,
                "status": row.status,
                "mode": row.mode,
                "current_phase": row.current_phase,
                "event_seq": row.event_seq,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "started_at": row.started_at.isoformat() if row.started_at else None,
            }
        }


@router.get("/jobs/{job_id}")
def generation_job_snapshot(job_id: str, current_user: User = Depends(get_current_user)):
    with SessionLocal() as session:
        job = _load_generation_job_for_user(session, job_id, current_user)
        events = list(job.phase_log or [])[-50:]
        return {
            "id": job.id,
            "status": job.status,
            "mode": job.mode,
            "current_phase": job.current_phase,
            "event_seq": job.event_seq,
            "events": events,
            "robot_code": job.robot_code,
            "error_message": job.error_message,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "cancellation_requested_at": (
                job.cancellation_requested_at.isoformat()
                if job.cancellation_requested_at
                else None
            ),
        }


@router.post("/jobs/{job_id}/cancel")
def cancel_generation_job(job_id: str, current_user: User = Depends(get_current_user)):
    with SessionLocal() as session:
        job = _load_generation_job_for_user(session, job_id, current_user)
        if job.status in (
            GenerationStatus.succeeded.value,
            GenerationStatus.failed.value,
            GenerationStatus.cancelled.value,
        ):
            return {"ok": True, "status": job.status}
        job.cancellation_requested_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        session.add(job)
        session.commit()
        _publish_job_event(session, job, "note", {"message": "Cancellation requested. Finishing current step..."})
        return {"ok": True, "status": job.status, "cancellation_requested_at": job.cancellation_requested_at.isoformat()}


@router.get("/jobs/{job_id}/events")
def generation_job_events(
    job_id: str,
    from_seq: int = Query(0, ge=0, alias="from"),
    current_user: User = Depends(get_current_user),
):
    with SessionLocal() as session:
        _load_generation_job_for_user(session, job_id, current_user)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        _job_stream(job_id, from_seq=from_seq),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/mcp-stepwise/stream")
def generate_mcp_stepwise_stream(
    body: GenerateRequest,
    current_user: User = Depends(get_current_user),
):
    """Legacy alias: create a persisted job and proxy its event stream."""
    job_id = _create_generation_job(body, current_user)
    submit_generation_job(lambda: _run_generation_job(job_id))
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        _job_stream(job_id, from_seq=0),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get("/metrics")
def generation_metrics(
    days: int = Query(7, ge=1, le=90),
    mode: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    is_admin = current_user.is_admin or current_user.global_role == "admin"
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    since = datetime.now(UTC) - timedelta(days=days)
    with SessionLocal() as session:
        q = session.query(GenerationMetric).filter(GenerationMetric.started_at >= since)
        if mode:
            q = q.filter(GenerationMetric.mode == mode)
        rows = q.order_by(GenerationMetric.started_at.desc()).all()

    fallback_hist: dict[str, int] = {}
    success = 0
    timings: dict[str, list[int]] = {}
    for row in rows:
        if row.final_status == "success":
            success += 1
        key = row.fallback_reason or "none"
        fallback_hist[key] = fallback_hist.get(key, 0) + 1
        for phase, ms in (row.phase_timings or {}).items():
            try:
                val = int(ms)
            except Exception:
                continue
            timings.setdefault(str(phase), []).append(val)

    def _pct(data: list[int], percentile: float) -> int | None:
        if not data:
            return None
        ordered = sorted(data)
        idx = max(0, min(len(ordered) - 1, int(round((percentile / 100.0) * (len(ordered) - 1)))))
        return ordered[idx]

    phase_summary = {
        phase: {
            "count": len(vals),
            "p50_ms": _pct(vals, 50),
            "p95_ms": _pct(vals, 95),
            "max_ms": max(vals) if vals else None,
        }
        for phase, vals in timings.items()
    }
    return {
        "window_days": days,
        "mode_filter": mode,
        "total_runs": len(rows),
        "success_rate": (success / len(rows)) if rows else 0.0,
        "fallback_reason_histogram": fallback_hist,
        "phase_timings": phase_summary,
        "recent_runs": [
            {
                "id": r.id,
                "job_id": r.job_id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "mode": r.mode,
                "final_status": r.final_status,
                "fallback_reason": r.fallback_reason,
                "error_message": r.error_message,
                "phase_timings": r.phase_timings,
                "step_count_planned": r.step_count_planned,
                "step_count_passed": r.step_count_passed,
                "step_count_failed": r.step_count_failed,
                "cache_hit": r.cache_hit,
            }
            for r in rows[:50]
        ],
    }


def _build_fallback_suite(step_results: list[dict], suite_name: str) -> str:
    """Construct a .robot file from step execution log when build_suite fails."""
    lines = [
        "*** Settings ***",
        "Library    SeleniumLibrary",
        "Resource   ../../Resources/Common/GlobalKeywords.robot",
        "Resource   ../../Resources/PO/Platform/SalesPO.robot",
        "",
        "Test Setup    Begin Web Test",
        "Test Teardown    End Web Test",
        "",
        "*** Test Cases ***",
        suite_name,
        "    [Tags]    generated    mcp-stepwise",
    ]
    for sr in step_results:
        kw = sr["keyword"]
        args = sr.get("args", [])
        arg_str = "    ".join(str(a) for a in args)
        prefix = "    " if sr["status"] == "pass" else "    # FAILED: "
        lines.append(f"{prefix}{kw}    {arg_str}".rstrip())
    return "\n".join(lines) + "\n"
