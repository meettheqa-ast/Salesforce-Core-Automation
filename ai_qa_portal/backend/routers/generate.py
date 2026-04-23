"""Test generation endpoints — quick generate and MCP stepwise."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.config import GENERATED_SUITE
from ai_qa_portal.backend.models.schemas import GenerateRequest, GenerateResponse

router = APIRouter(prefix="/api/generate", tags=["generate"])
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


@router.post("/robot-suite", response_model=GenerateResponse)
def generate_robot_suite(body: GenerateRequest):
    """Quick-generate: prompt -> .robot file in one LLM call."""
    try:
        from ai_bridge import (
            generate_test_from_prompt,
            validate_generated_robot,
        )

        output = generate_test_from_prompt(body.prompt)
        robot_code = output.read_text(encoding="utf-8")

        lint_errors = []
        with contextlib.suppress(Exception):
            lint_errors = validate_generated_robot(robot_code)

        return GenerateResponse(
            robot_code=robot_code,
            test_path=str(output),
            lint_errors=lint_errors,
        )
    except Exception as exc:
        logger.exception("quick-generate failed")
        raise HTTPException(status_code=500, detail=_format_generation_error(exc)) from exc


_MCP_STEP_TIMEOUT_S = 25
_MCP_INIT_TIMEOUT_S = 30
_MCP_BUILD_TIMEOUT_S = 25


def _run_with_timeout(fn, *args, timeout: float, label: str):
    """Run a blocking callable in a thread with a hard timeout."""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, *args)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as e:
            raise TimeoutError(f"{label} did not finish within {timeout}s") from e


def _quick_generate_fallback(prompt: str) -> GenerateResponse:
    from ai_bridge import generate_test_from_prompt, validate_generated_robot

    output = generate_test_from_prompt(prompt)
    code = output.read_text(encoding="utf-8")
    lints: list[str] = []
    with contextlib.suppress(Exception):
        lints = validate_generated_robot(code)
    return GenerateResponse(robot_code=code, test_path=str(output), lint_errors=lints)


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
        resp = _quick_generate_fallback(body.prompt)
        resp.lint_errors = list(resp.lint_errors) + [
            f"MCP unavailable: {exc}. Used Quick Generate instead."
        ]
        return resp

    try:
        steps = break_prompt_into_steps(body.prompt)
    except Exception as exc:
        logger.exception("step planning failed")
        raise HTTPException(status_code=500, detail=_format_generation_error(exc)) from exc

    suite_name = body.test_name or "MCP Stepwise Test"

    try:
        session_id = _run_with_timeout(
            mcp_bridge.init_session,
            body.sandbox_url,
            body.username,
            body.password,
            timeout=_MCP_INIT_TIMEOUT_S,
            label="MCP init_session",
        )
    except Exception as exc:
        logger.warning("MCP init failed, falling back to quick-generate: %s", exc)
        resp = _quick_generate_fallback(body.prompt)
        resp.lint_errors = list(resp.lint_errors) + [
            f"MCP init failed: {exc}. Used Quick Generate instead."
        ]
        return resp

    step_results: list[dict] = []
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
        resp = _quick_generate_fallback(body.prompt)
        resp.lint_errors = list(resp.lint_errors) + notes
        return resp

    from ai_bridge import (
        fix_misplaced_setup_teardown,
        strip_credential_variable_overrides,
        strip_llm_robot_garbage,
        strip_hallucinated_csv_variables_from_suite,
        format_robot_code,
    )
    robot_code = strip_credential_variable_overrides(robot_code)
    robot_code = strip_llm_robot_garbage(robot_code)
    robot_code = strip_hallucinated_csv_variables_from_suite(robot_code)
    robot_code = fix_misplaced_setup_teardown(robot_code)

    final = robot_code.rstrip() + "\n"
    GENERATED_SUITE.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_SUITE.write_text(final, encoding="utf-8")
    with contextlib.suppress(Exception):
        format_robot_code(GENERATED_SUITE)

    return GenerateResponse(
        robot_code=GENERATED_SUITE.read_text(encoding="utf-8"),
        test_path=str(GENERATED_SUITE),
        lint_errors=notes,
    )


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
        notes: list[str] = []

        # Phase: mcp-init (start RF-MCP if needed)
        yield _sse("phase", {"name": "mcp-init"})
        try:
            if not mcp_bridge.is_server_running():
                mcp_bridge.start_mcp_server()
        except Exception as exc:
            yield _sse("note", {"message": f"RF-MCP unavailable: {exc}. Falling back to Quick Generate."})
            yield _sse("phase", {"name": "fallback"})
            try:
                resp = _quick_generate_fallback(body.prompt)
            except Exception as exc2:
                yield _sse("error", {"message": _format_generation_error(exc2)})
                return
            yield _sse("result", resp.model_dump(mode="json"))
            return

        # Phase: planning (LLM step decomposition)
        yield _sse("phase", {"name": "planning"})
        try:
            steps = break_prompt_into_steps(body.prompt)
        except Exception as exc:
            yield _sse("error", {"message": _format_generation_error(exc)})
            return
        yield _sse("note", {"message": f"Planned {len(steps)} step(s)"})

        # Phase: session init
        yield _sse("phase", {"name": "session"})
        try:
            session_id = _run_with_timeout(
                mcp_bridge.init_session,
                body.sandbox_url,
                body.username,
                body.password,
                timeout=_MCP_INIT_TIMEOUT_S,
                label="MCP init_session",
            )
        except Exception as exc:
            yield _sse("note", {"message": f"MCP init failed: {exc}. Falling back to Quick Generate."})
            yield _sse("phase", {"name": "fallback"})
            try:
                resp = _quick_generate_fallback(body.prompt)
            except Exception as exc2:
                yield _sse("error", {"message": _format_generation_error(exc2)})
                return
            yield _sse("result", resp.model_dump(mode="json"))
            return

        # Phase: executing
        yield _sse("phase", {"name": "executing"})
        step_results: list[dict] = []
        total = len(steps)
        for idx, step in enumerate(steps, start=1):
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
                yield _sse("step", {
                    "index": idx, "total": total, "keyword": kw, "args": args,
                    "status": "pass",
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
                })

        # Phase: build
        yield _sse("phase", {"name": "build"})
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
            yield _sse("note", {"message": "MCP returned empty suite. Falling back to Quick Generate."})
            yield _sse("phase", {"name": "fallback"})
            try:
                resp = _quick_generate_fallback(body.prompt)
            except Exception as exc2:
                yield _sse("error", {"message": _format_generation_error(exc2)})
                return
            resp.lint_errors = list(resp.lint_errors) + notes
            yield _sse("result", resp.model_dump(mode="json"))
            return

        from ai_bridge import (
            fix_misplaced_setup_teardown,
            strip_credential_variable_overrides,
            strip_llm_robot_garbage,
            strip_hallucinated_csv_variables_from_suite,
            format_robot_code,
        )
        robot_code = strip_credential_variable_overrides(robot_code)
        robot_code = strip_llm_robot_garbage(robot_code)
        robot_code = strip_hallucinated_csv_variables_from_suite(robot_code)
        robot_code = fix_misplaced_setup_teardown(robot_code)

        final = robot_code.rstrip() + "\n"
        GENERATED_SUITE.parent.mkdir(parents=True, exist_ok=True)
        GENERATED_SUITE.write_text(final, encoding="utf-8")
        with contextlib.suppress(Exception):
            format_robot_code(GENERATED_SUITE)

        resp = GenerateResponse(
            robot_code=GENERATED_SUITE.read_text(encoding="utf-8"),
            test_path=str(GENERATED_SUITE),
            lint_errors=notes,
        )
        yield _sse("phase", {"name": "done"})
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
