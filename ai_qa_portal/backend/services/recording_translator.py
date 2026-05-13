"""Translate a Playwright codegen action log into a Robot Framework
script using this project's existing keyword catalog.

This is the second-stage processor in the recording pipeline:

  pw_mcp_bridge.record_start  -> user clicks through SF (capturing actions)
  pw_mcp_bridge.record_stop   -> action log returned
  recording_translator.translate(action_log)  <-- THIS MODULE
  script_validation_loop      -> validate the output against AST + dryrun
                                 + (optional) Playwright locator gate

The translator routes through the same ``ai_bridge.call_llm`` function
the rest of the codebase uses, so it inherits provider failover,
catalog injection, and the system-prompt assembler. The only new
artifact is the recording-specific user prompt template at
``ai_qa_portal/backend/prompts/recording_translator.md``.

Output flows back through ``script_validation_loop.run_with_validation``
so a stale-locator translation gets the same self-correction loop a
prompt-driven generation does.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_qa_portal.backend.services.script_validation_loop import (
    ValidationLoopResult,
    run_with_validation,
)

logger = logging.getLogger("ai_qa_portal.recording_translator")

# The translator-specific user-prompt template lives next to other
# generation prompts. Loaded lazily so the import is cheap.
_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "recording_translator.md"
)


@dataclass
class TranslationResult:
    """What ``translate_actions`` returns. Mirrors ``ValidationLoopResult``
    so callers can plug it directly into the existing API surfaces. The
    raw action log is included so the frontend can show the
    "actions captured" + "robot generated" side-by-side."""
    raw_actions: list[dict]
    loop_result: ValidationLoopResult


def _load_translator_prompt() -> str:
    """Read the translator system prompt. Cached implicitly via Python's
    module-level immutability after first call. We don't memoise
    explicitly because the file is tiny and edits should be picked up
    without a restart in dev.
    """
    if not _PROMPT_PATH.is_file():
        # Fallback: a minimal inline prompt. Should never trigger in
        # production; serves as a safety net for unit tests that mock
        # the file system.
        return (
            "Translate the action log into a Robot Framework script using "
            "the project's keyword catalog. Output ONLY the .robot file."
        )
    return _PROMPT_PATH.read_text(encoding="utf-8")


def translate_actions(
    actions: list[dict],
    *,
    output_path: Path,
    test_name_hint: str = "",
    locator_validate: Any | None = None,
    locator_shadow_mode: bool = False,
) -> TranslationResult:
    """Run the LLM translation + validation loop on a captured action log.

    ``actions`` is the list returned by ``pw_mcp_bridge.record_stop`` --
    a sequence of click / fill / navigate / press_key / select dicts.
    The output ``.robot`` file is written to ``output_path``; the
    returned ``TranslationResult`` carries both the raw input (for UI
    side-by-side display) and the standard validation-loop result so
    callers can render validation pills + Run buttons unchanged.

    Soft-fail: empty action list -> a no-op result with a parse-error
    report. We never raise on bad input because the recording flow is
    user-initiated and exceptions would be surface-level UX failures.
    """
    if not actions:
        from ai_qa_portal.backend.services.script_validator import (
            ValidationError,
            ValidationReport,
        )
        empty_report = ValidationReport(
            ok=False,
            errors=[
                ValidationError(
                    line=0, column=0, kind="parse_error",
                    symbol="empty_recording",
                    message="No actions were captured during recording.",
                )
            ],
        )
        return TranslationResult(
            raw_actions=[],
            loop_result=ValidationLoopResult(
                final_script="",
                final_report=empty_report,
                attempts=[],
                converged=False,
            ),
        )

    # Lazy imports so the module is loadable on systems without ai_bridge
    # (e.g. unit-test runners that mock the LLM).
    from ai_bridge import (
        _post_process_robot_source,  # type: ignore[attr-defined]
        call_llm,
        extract_robot_code,
        format_robot_code,
        hydrate_llm_env,
    )
    from ai_qa_portal.backend.prompts import assembler

    hydrate_llm_env()

    # Build the user content: translator instructions + JSON action log
    # + project catalog (delegates to the same assembler that drives
    # quick generate). This keeps the LLM grounded in the same keyword
    # catalog every other path uses.
    translator_instructions = _load_translator_prompt()
    action_json = json.dumps(actions, indent=2)
    user_body = (
        f"{translator_instructions}\n\n"
        f"## Action log (chronological)\n\n"
        f"```json\n{action_json}\n```\n\n"
        + (f"## Suggested test name\n\n{test_name_hint}\n\n" if test_name_hint else "")
    )
    user_prompt = assembler.build_user_prompt_with_catalog(
        user_body, include_full_catalog=True,
    )
    system_prompt = assembler.build_system_prompt("drafter")

    def _llm(fix_prompt: str | None) -> str:
        content = user_prompt
        if fix_prompt:
            content = (
                user_prompt
                + "\n\n## Validator feedback (attempt failed)\n\n"
                + fix_prompt
            )
        return call_llm(system_prompt, content)

    def _post(robot_source: str) -> str:
        return _post_process_robot_source(robot_source)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_with_validation(
        llm_call=_llm,
        post_process=_post,
        extract_robot=extract_robot_code,
        suite_path=output_path,
        locator_validate=locator_validate,
        locator_shadow_mode=locator_shadow_mode,
    )

    # Format the on-disk file with robotidy, same as the prompt-driven
    # paths. Best-effort: a formatter failure shouldn't fail translation.
    if output_path.is_file():
        try:
            format_robot_code(output_path)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    return TranslationResult(
        raw_actions=actions,
        loop_result=result,
    )
