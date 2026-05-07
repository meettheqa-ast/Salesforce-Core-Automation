"""Test generation endpoints — quick generate and MCP stepwise."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.config import GENERATED_SUITE
from ai_qa_portal.backend.models.schemas import (
    GenerateRequest,
    GenerateResponse,
    GenerationAttempt,
    ProviderSwitchPayload,
    ValidationErrorPayload,
)
from ai_qa_portal.backend.services.auth import get_current_user

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
    errors = [
        ValidationErrorPayload(
            line=e.line, column=e.column, kind=str(e.kind),
            symbol=e.symbol, message=e.message,
            closest_matches=list(e.closest_matches), snippet=e.snippet,
        )
        for e in result.final_report.errors
    ]
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


@router.post("/robot-suite", response_model=GenerateResponse)
def generate_robot_suite(body: GenerateRequest):
    """Quick-generate: prompt -> validated .robot file.

    Wraps the LLM call in the validate-fix-validate loop so any script
    that ships back has been parsed by ``robot.api`` AND ``robot --dryrun``.
    The on-disk file is the FINAL attempt (best-effort even when the
    loop didn't converge) so the user can still inspect what the LLM
    produced and the UI can render the validator errors inline."""
    try:
        from ai_bridge import (
            generate_test_from_prompt_validated,
            validate_generated_robot,
        )

        result = generate_test_from_prompt_validated(
            body.prompt, default_app=body.default_app,
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
        )
    except Exception as exc:
        logger.exception("quick-generate failed")
        raise HTTPException(status_code=500, detail=_format_generation_error(exc)) from exc


_MCP_STEP_TIMEOUT_S = 25
# Cold init does: manage_session(init) + 2 * import_resource (Robot keyword
# table loads, sequential per the comment in mcp_bridge.init_session) + a
# Selenium browser launch the first time RF-MCP touches a session +
# Salesforce login (network-bound). 30 s was tight enough to fall back even
# on healthy runs; bump to 60 so we only fall back when it's actually wedged.
# Override via MCP_INIT_TIMEOUT_S env var.
_MCP_INIT_TIMEOUT_S = int(os.environ.get("MCP_INIT_TIMEOUT_S", "60"))
_MCP_BUILD_TIMEOUT_S = 25
# Planning is one LLM call (break_prompt_into_steps). The default OpenAI /
# Gemini SDK timeouts are 5-10 minutes which is unacceptable on the user's
# critical path -- if the LLM hangs we'd rather fail fast and fall back to
# Quick Generate (which is also one LLM call but a different prompt and
# often a different provider). 45 s is generous enough for slow networks
# but tight enough that the user isn't staring at "Planning steps" forever.
_MCP_PLAN_TIMEOUT_S = int(os.environ.get("MCP_PLAN_TIMEOUT_S", "45"))


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


def _quick_generate_fallback(prompt: str, *, default_app: str = "") -> GenerateResponse:
    """Stepwise fallback path. Routes through the SAME validate-fix-validate
    loop as the primary Quick Generate endpoint -- without this parity, a
    wedged RF-MCP would silently bypass the new safety net.
    """
    from ai_bridge import generate_test_from_prompt_validated, validate_generated_robot

    result = generate_test_from_prompt_validated(prompt, default_app=default_app)
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


@router.post("/mcp-stepwise")
def generate_mcp_stepwise(body: GenerateRequest):
    """MCP Stepwise: decompose prompt into steps, execute each, build suite.

    Falls back to quick-generate if MCP is unreachable, errors out, or any
    individual step exceeds the per-step timeout.
    """
    from ai_bridge import break_prompt_into_steps
    import mcp_bridge

    notes: list[str] = []

    try:
        if not mcp_bridge.is_server_running():
            mcp_bridge.start_mcp_server()
    except Exception as exc:
        logger.warning("RF-MCP unavailable, falling back to quick-generate: %s", exc)
        resp = _quick_generate_fallback(body.prompt, default_app=body.default_app)
        resp.lint_errors = list(resp.lint_errors) + [
            f"MCP unavailable: {exc}. Used Quick Generate instead."
        ]
        return resp

    try:
        steps = _run_with_timeout(
            break_prompt_into_steps,
            body.prompt,
            None,  # catalog_json -- let the planner build the lean one
            None,  # scenario_analysis -- only used by the legacy stepwise pipeline
            body.default_app,
            timeout=_MCP_PLAN_TIMEOUT_S,
            label="LLM step planning",
        )
    except TimeoutError as exc:
        logger.warning("LLM planning timed out, falling back to quick-generate: %s", exc)
        resp = _quick_generate_fallback(body.prompt, default_app=body.default_app)
        resp.lint_errors = list(resp.lint_errors) + [
            f"{exc}. Used Quick Generate instead."
        ]
        return resp
    except Exception as exc:
        logger.exception("step planning failed")
        raise HTTPException(status_code=500, detail=_format_generation_error(exc)) from exc

    suite_name = body.test_name or "MCP Stepwise Test"

    try:
        session_id, cache_hit = _run_with_timeout(
            mcp_bridge.get_or_init_session,
            body.sandbox_url,
            body.username,
            body.password,
            timeout=_MCP_INIT_TIMEOUT_S,
            label="MCP init_session",
        )
        if cache_hit:
            notes.append("Reused warm Salesforce session (skipped re-login).")
    except Exception as exc:
        logger.warning("MCP init failed, falling back to quick-generate: %s", exc)
        resp = _quick_generate_fallback(body.prompt, default_app=body.default_app)
        resp.lint_errors = list(resp.lint_errors) + [
            f"MCP init failed: {exc}. Used Quick Generate instead."
        ]
        return resp

    step_results: list[dict] = []
    session_invalidated = False
    for step in steps:
        kw = step.get("keyword", "")
        args = step.get("args", [])
        try:
            result = _run_with_timeout(
                mcp_bridge.execute_step,
                session_id,
                kw,
                args,
                timeout=_MCP_STEP_TIMEOUT_S,
                label=f"MCP step {kw}",
            )
            step_results.append(
                {"keyword": kw, "args": args, "status": "pass", "output": str(result)}
            )
        except Exception as e:
            note = f"step '{kw}' failed: {type(e).__name__}: {e}"
            notes.append(note)
            step_results.append(
                {"keyword": kw, "args": args, "status": "fail", "error": str(e)}
            )
            # If the cached session went stale (e.g. SF kicked us out), drop
            # it once so the next generation builds a fresh one. We only do
            # this for cache-hit sessions -- a freshly-created session that
            # fails on its first step is more likely a planning/keyword
            # issue than a session-staleness issue.
            if cache_hit and not session_invalidated and _looks_like_session_error(e):
                mcp_bridge.invalidate_cached_session(body.sandbox_url, body.username)
                session_invalidated = True

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
        notes.append("MCP returned empty suite; used Quick Generate")
        resp = _quick_generate_fallback(body.prompt, default_app=body.default_app)
        resp.lint_errors = list(resp.lint_errors) + notes
        return resp

    from ai_bridge import (
        fix_misplaced_setup_teardown,
        strip_credential_variable_overrides,
        strip_empty_variable_overrides,
        strip_llm_robot_garbage,
        strip_hallucinated_csv_variables_from_suite,
        format_robot_code,
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

    # Final-suite validation. The Stepwise execution path verifies each
    # step at runtime against RF-MCP, but ``build_suite`` can still emit
    # surprising shapes (extra Resource imports, mis-quoted args). Running
    # the AST + dryrun gate here gives the user the same structured
    # feedback Quick Generate now provides.
    validation_ok, validation_errors, validation_attempts = _validate_existing_suite(
        Path(GENERATED_SUITE),
    )
    return GenerateResponse(
        robot_code=GENERATED_SUITE.read_text(encoding="utf-8"),
        test_path=str(GENERATED_SUITE),
        lint_errors=notes,
        validation_ok=validation_ok,
        validation_errors=validation_errors,
        validation_attempts=validation_attempts,
        provider_switches=_drain_provider_switches(),
    )


def _validate_existing_suite(
    path: Path,
) -> tuple[bool, list[ValidationErrorPayload], list[GenerationAttempt]]:
    """Run the validator + dryrun against an already-on-disk suite (e.g.
    one built by ``mcp_bridge.build_suite``). No retry loop -- the LLM
    isn't in scope for the Stepwise builder, so we just surface the
    findings."""
    from ai_qa_portal.backend.services import script_validator as sv
    from ai_qa_portal.backend.services import script_dryrun as sd

    if not path.is_file():
        return True, [], []

    ast_report = sv.validate(path)
    dry_report = sd.dryrun(path) if ast_report.ok else sv.ValidationReport(ok=True, errors=[])

    combined = sv.ValidationReport(
        ok=ast_report.ok and dry_report.ok,
        errors=list(ast_report.errors) + list(dry_report.errors),
    )
    payloads = [
        ValidationErrorPayload(
            line=e.line, column=e.column, kind=str(e.kind),
            symbol=e.symbol, message=e.message,
            closest_matches=list(e.closest_matches), snippet=e.snippet,
        )
        for e in combined.errors
    ]
    attempts = [GenerationAttempt(
        attempt=1, ok=combined.ok, error_count=len(combined.errors),
    )]
    return combined.ok, payloads, attempts


def _sse(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    safe = payload.replace("\r", "")
    return f"event: {event}\ndata: {safe}\n\n"


@router.post("/mcp-stepwise/stream")
def generate_mcp_stepwise_stream(body: GenerateRequest):
    """Stream MCP stepwise progress as Server-Sent Events.

    Events: phase, step, note, result, error.
    Falls back to Quick Generate just like the non-streaming endpoint, but
    surfaces every transition so the UI can show a live pipeline.
    """
    from ai_bridge import break_prompt_into_steps
    import mcp_bridge

    def stream():
        try:
            yield from _stream_inner(body)
        except Exception as exc:  # noqa: BLE001 -- outer safety net
            logger.exception("mcp-stepwise stream crashed unexpectedly")
            yield _sse("error", {"message": _format_generation_error(exc)})

    def _stream_inner(body: GenerateRequest):
        notes: list[str] = []
        logger.info("mcp-stepwise stream start; prompt_chars=%d", len(body.prompt))

        # Per-phase timing. Each phase event carries the elapsed_ms since the
        # previous transition so the UI can render "+12.3 s" badges and make
        # "is this hung?" answerable at a glance.
        phase_t0 = time.monotonic()
        stream_t0 = phase_t0

        def _emit_phase(name: str) -> str:
            nonlocal phase_t0
            now = time.monotonic()
            payload = {
                "name": name,
                "elapsed_ms_phase": int((now - phase_t0) * 1000),
                "elapsed_ms_total": int((now - stream_t0) * 1000),
            }
            phase_t0 = now
            return _sse("phase", payload)

        # Phase: mcp-init (start RF-MCP if needed)
        yield _emit_phase("mcp-init")
        try:
            if not mcp_bridge.is_server_running():
                mcp_bridge.start_mcp_server()
        except Exception as exc:
            logger.warning("RF-MCP unavailable: %s -- falling back to Quick Generate", exc)
            yield _sse("note", {"message": f"RF-MCP unavailable: {exc}. Falling back to Quick Generate."})
            yield _emit_phase("fallback")
            try:
                resp = _quick_generate_fallback(body.prompt, default_app=body.default_app)
            except Exception as exc2:
                logger.exception("quick-generate fallback failed (after RF-MCP unavailable)")
                yield _sse("error", {"message": _format_generation_error(exc2)})
                return
            yield _sse("result", resp.model_dump(mode="json"))
            return

        # Phase: planning (LLM step decomposition)
        yield _emit_phase("planning")
        try:
            steps = _run_with_timeout(
                break_prompt_into_steps,
                body.prompt,
                None,  # catalog_json -- planner builds the lean one
                None,  # scenario_analysis -- legacy parameter
                body.default_app,
                timeout=_MCP_PLAN_TIMEOUT_S,
                label="LLM step planning",
            )
        except TimeoutError as exc:
            # LLM hung -- fall back to Quick Generate so the user gets a
            # usable script instead of an indefinite "Planning steps" pill.
            logger.warning("LLM planning timed out: %s -- falling back to Quick Generate", exc)
            yield _sse("note", {"message": f"{exc}. Falling back to Quick Generate."})
            yield _emit_phase("fallback")
            try:
                resp = _quick_generate_fallback(body.prompt, default_app=body.default_app)
            except Exception as exc2:
                logger.exception("quick-generate fallback failed (after planning timeout)")
                yield _sse("error", {"message": _format_generation_error(exc2)})
                return
            yield _sse("result", resp.model_dump(mode="json"))
            return
        except Exception as exc:
            logger.exception("step planning failed")
            yield _sse("error", {"message": _format_generation_error(exc)})
            return
        logger.info("planned %d step(s)", len(steps))
        yield _sse("note", {"message": f"Planned {len(steps)} step(s)"})

        # Phase: session init (cache-aware -- a cache hit skips browser launch
        # + Salesforce login, the single biggest wall-clock cost)
        yield _emit_phase("session")
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
            if cache_hit:
                yield _sse("note", {"message": "Reused warm Salesforce session (skipped re-login)."})
        except Exception as exc:
            logger.warning("MCP init_session failed: %s -- falling back to Quick Generate", exc)
            yield _sse("note", {"message": f"MCP init failed: {exc}. Falling back to Quick Generate."})
            yield _emit_phase("fallback")
            try:
                resp = _quick_generate_fallback(body.prompt, default_app=body.default_app)
            except Exception as exc2:
                logger.exception("quick-generate fallback failed (after MCP init)")
                yield _sse("error", {"message": _format_generation_error(exc2)})
                return
            yield _sse("result", resp.model_dump(mode="json"))
            return

        # Phase: executing
        yield _emit_phase("executing")
        step_results: list[dict] = []
        total = len(steps)
        session_invalidated = False
        for idx, step in enumerate(steps, start=1):
            kw = step.get("keyword", "")
            args = step.get("args", [])
            step_t0 = time.monotonic()
            try:
                result = _run_with_timeout(
                    mcp_bridge.execute_step,
                    session_id,
                    kw,
                    args,
                    timeout=_MCP_STEP_TIMEOUT_S,
                    label=f"MCP step {kw}",
                )
                step_results.append(
                    {"keyword": kw, "args": args, "status": "pass", "output": str(result)}
                )
                yield _sse("step", {
                    "index": idx, "total": total, "keyword": kw, "args": args,
                    "status": "pass",
                    "elapsed_ms": int((time.monotonic() - step_t0) * 1000),
                })
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                notes.append(f"step '{kw}' failed: {msg}")
                step_results.append(
                    {"keyword": kw, "args": args, "status": "fail", "error": msg}
                )
                yield _sse("step", {
                    "index": idx, "total": total, "keyword": kw, "args": args,
                    "status": "fail", "error": msg,
                    "elapsed_ms": int((time.monotonic() - step_t0) * 1000),
                })
                # Drop a stale cached session once so the next request rebuilds.
                if cache_hit and not session_invalidated and _looks_like_session_error(e):
                    mcp_bridge.invalidate_cached_session(body.sandbox_url, body.username)
                    session_invalidated = True
                    notes.append("Cached session looked stale; will rebuild on next run.")

        # Phase: build
        yield _emit_phase("build")
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
            logger.warning("MCP build_suite returned empty -- falling back to Quick Generate")
            yield _sse("note", {"message": "MCP returned empty suite. Falling back to Quick Generate."})
            yield _emit_phase("fallback")
            try:
                resp = _quick_generate_fallback(body.prompt, default_app=body.default_app)
            except Exception as exc2:
                logger.exception("quick-generate fallback failed (after empty MCP build)")
                yield _sse("error", {"message": _format_generation_error(exc2)})
                return
            resp.lint_errors = list(resp.lint_errors) + notes
            yield _sse("result", resp.model_dump(mode="json"))
            return

        # Post-processing pipeline. Any failure here used to silently exit the
        # generator -- now surfaced as a clear error event so the UI doesn't
        # claim "Generation complete" with no script attached.
        try:
            from ai_bridge import (
                fix_misplaced_setup_teardown,
                strip_credential_variable_overrides,
                strip_empty_variable_overrides,
                strip_llm_robot_garbage,
                strip_hallucinated_csv_variables_from_suite,
                format_robot_code,
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

            # Same validation gate as the non-streaming path. Surfaced as
            # a `phase: validate` event so the UI can show a "Validating"
            # pill in the live pipeline before "Done".
            validation_ok = True
            validation_errors: list[ValidationErrorPayload] = []
            validation_attempts: list[GenerationAttempt] = []
            with contextlib.suppress(Exception):
                validation_ok, validation_errors, validation_attempts = (
                    _validate_existing_suite(Path(GENERATED_SUITE))
                )

            resp = GenerateResponse(
                robot_code=GENERATED_SUITE.read_text(encoding="utf-8"),
                test_path=str(GENERATED_SUITE),
                lint_errors=notes,
                validation_ok=validation_ok,
                validation_errors=validation_errors,
                validation_attempts=validation_attempts,
                provider_switches=_drain_provider_switches(),
            )
        except Exception as exc:
            logger.exception("post-processing pipeline failed (suite path=%s)", GENERATED_SUITE)
            yield _sse("error", {"message": f"Post-processing failed: {_format_generation_error(exc)}"})
            return

        logger.info("mcp-stepwise stream done; robot_chars=%d", len(resp.robot_code or ""))
        yield _emit_phase("done")
        yield _sse("result", resp.model_dump(mode="json"))

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


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
