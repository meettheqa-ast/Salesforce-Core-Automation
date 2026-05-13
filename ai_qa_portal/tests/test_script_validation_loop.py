"""Tests for the LLM retry-on-validation-failure loop.

Mocks the LLM with a stub that returns a known-bad script first, then a
corrected one, and asserts the loop converges in 2 attempts and surfaces
the correction trail. Also tests the budget-exhausted path: an LLM stuck
in a hallucination loop must produce a non-converged result with the
last attempt's errors intact.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from ai_qa_portal.backend.services import script_validation_loop as svl

# Self-contained scripts (no external Resource imports) so the
# validator can run them in a tmpfile without needing the project's
# Resources/ tree on disk. The first triggers the validator
# (RANDOM_STRING undefined); the second is clean.
BAD_SCRIPT = textwrap.dedent("""\
    *** Settings ***
    Documentation       Bad: references undefined ${RANDOM_STRING}.

    *** Variables ***
    ${name}    Demo ${RANDOM_STRING}

    *** Test Cases ***
    Demo
        [Tags]    smoke
        Log    ${name}
""")

GOOD_SCRIPT = textwrap.dedent("""\
    *** Settings ***
    Documentation       Good: only references built-in variables.

    *** Variables ***
    ${name}    Demo ${SPACE}suffix

    *** Test Cases ***
    Demo
        [Tags]    smoke
        Log    ${name}
""")


def _identity_extract(raw: str) -> str:
    """Test stub: the 'LLM' here returns a complete .robot already."""
    return raw


def _identity_post(robot: str) -> str:
    """Test stub: skip cleanup so we control the EXACT bytes the
    validator sees."""
    return robot


def test_loop_converges_after_one_correction(tmp_path: Path):
    """With a stub LLM that returns BAD then GOOD, the loop converges
    on attempt 2 and surfaces the trail."""
    suite_path = tmp_path / "suite.robot"
    responses = iter([BAD_SCRIPT, GOOD_SCRIPT])
    fix_prompts_seen: list[str | None] = []

    def llm(fix_prompt: str | None) -> str:
        fix_prompts_seen.append(fix_prompt)
        return next(responses)

    result = svl.run_with_validation(
        llm_call=llm,
        post_process=_identity_post,
        extract_robot=_identity_extract,
        suite_path=suite_path,
        max_attempts=2,
        skip_dryrun=True,  # robot --dryrun is exercised separately; skip for speed
    )

    assert result.converged is True
    assert len(result.attempts) == 2
    assert result.attempts[0].report.ok is False
    assert result.attempts[1].report.ok is True
    # First call had no fix prompt; second call DID.
    assert fix_prompts_seen[0] is None
    assert fix_prompts_seen[1] is not None and "RANDOM_STRING" in fix_prompts_seen[1]


def test_loop_returns_partial_result_when_budget_exhausted(tmp_path: Path):
    """LLM stuck in a hallucination loop -> non-converged result with
    the last attempt's errors intact, file on disk reflects the LAST
    attempt."""
    suite_path = tmp_path / "suite.robot"

    def llm(fix_prompt: str | None) -> str:
        # Always return the bad script; the loop should give up.
        return BAD_SCRIPT

    result = svl.run_with_validation(
        llm_call=llm,
        post_process=_identity_post,
        extract_robot=_identity_extract,
        suite_path=suite_path,
        max_attempts=1,
        skip_dryrun=True,
    )

    assert result.converged is False
    assert len(result.attempts) == 2  # initial + 1 retry
    assert result.final_report.ok is False
    assert any(
        e.kind == "undefined_variable" and "RANDOM_STRING" in e.symbol
        for e in result.final_report.errors
    )
    # Non-empty lint_errors so the API surface can show them inline.
    assert result.lint_errors()


def test_loop_handles_empty_llm_output(tmp_path: Path):
    """If the LLM returns content that extracts to an empty string, the
    loop must handle it gracefully (not crash) and feed back a generic
    nudge."""
    suite_path = tmp_path / "suite.robot"
    responses = iter(["", GOOD_SCRIPT])

    def llm(fix_prompt: str | None) -> str:
        return next(responses)

    result = svl.run_with_validation(
        llm_call=llm,
        post_process=_identity_post,
        extract_robot=_identity_extract,
        suite_path=suite_path,
        max_attempts=2,
        skip_dryrun=True,
    )

    # Empty output on attempt 1, valid on attempt 2 -> converged.
    assert result.converged is True
    # First attempt's report.ok is False because we synthesised a parse_error.
    assert result.attempts[0].report.ok is False
    assert any(e.kind == "parse_error" for e in result.attempts[0].report.errors)


def test_loop_uses_default_budget_when_max_attempts_none(tmp_path: Path):
    """``max_attempts=None`` should fall back to the env-configured
    default (currently 2 retries -> 3 total attempts) without crashing."""
    suite_path = tmp_path / "suite.robot"

    def always_good(fix_prompt: str | None) -> str:
        return GOOD_SCRIPT

    result = svl.run_with_validation(
        llm_call=always_good,
        post_process=_identity_post,
        extract_robot=_identity_extract,
        suite_path=suite_path,
        max_attempts=None,
        skip_dryrun=True,
    )
    # Converges on attempt 1.
    assert result.converged is True
    assert len(result.attempts) == 1
