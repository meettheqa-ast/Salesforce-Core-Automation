"""Test generation endpoints — quick generate and MCP stepwise."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.config import GENERATED_SUITE
from ai_qa_portal.backend.models.schemas import GenerateRequest, GenerateResponse

router = APIRouter(prefix="/api/generate", tags=["generate"])


def _format_generation_error(exc: BaseException) -> str:
    """Readable detail for API clients (unwrap ExceptionGroup / TaskGroup noise)."""
    import builtins as _bi

    parts: list[str] = [str(exc)]
    _eg = getattr(_bi, "BaseExceptionGroup", None)
    if _eg is not None and isinstance(exc, _eg):
        parts.append("; ".join(_format_generation_error(e) for e in exc.exceptions))
    cause = getattr(exc, "__cause__", None)
    if cause and str(cause) not in parts[0]:
        parts.append(f"cause: {cause}")
    return " | ".join(p for p in parts if p)


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
        raise HTTPException(status_code=500, detail=_format_generation_error(exc)) from exc


@router.post("/mcp-stepwise")
def generate_mcp_stepwise(body: GenerateRequest):
    """MCP Stepwise: decompose prompt into steps, execute each, build suite.

    Returns the final .robot code. For real-time progress,
    use the SSE endpoint /api/generate/mcp-stepwise/stream.
    """
    try:
        from ai_bridge import break_prompt_into_steps
        import mcp_bridge

        if not mcp_bridge.is_server_running():
            mcp_bridge.start_mcp_server()

        steps = break_prompt_into_steps(body.prompt)

        session_id = mcp_bridge.init_session(
            body.sandbox_url,
            body.username,
            body.password,
        )

        step_results = []
        for step in steps:
            kw = step.get("keyword", "")
            args = step.get("args", [])
            try:
                result = mcp_bridge.execute_step(session_id, kw, args)
                step_results.append({"keyword": kw, "args": args, "status": "pass", "output": str(result)})
            except Exception as e:
                step_results.append({"keyword": kw, "args": args, "status": "fail", "error": str(e)})

        suite_name = body.test_name or "MCP Stepwise Test"
        try:
            robot_code = mcp_bridge.build_suite(session_id, suite_name)
        except Exception:
            robot_code = _build_fallback_suite(step_results, suite_name)

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
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_format_generation_error(exc)) from exc


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
