"""Run an LLM script generator inside an AST-validate + dryrun feedback loop.

The pipeline shape is:

  llm_call() -> raw -> post_process -> write to suite_path -> AST validate
    -> if errors: build fix prompt -> llm_call(fix_prompt) -> repeat
    -> if AST clean: dryrun -> if errors: build fix prompt -> repeat
    -> on success or budget exhausted: return ValidationLoopResult

Caller responsibilities:
  * ``llm_call(extra_user_content)`` calls the LLM with an OPTIONAL extra
    fix prompt appended to the user message and returns RAW model output
    (string). When ``extra_user_content`` is empty/None, the caller does
    a fresh first-attempt call. The caller owns the system prompt,
    catalog injection, persona context, etc. so this module stays
    LLM-agnostic.
  * ``post_process`` runs the same cleanup the existing pipelines do
    (strip credential overrides, format with robotidy, ...). It runs
    BETWEEN the LLM response and the validator so we don't validate
    against unprintable garbage or stale credentials.
  * ``suite_path`` is the path Robot will eventually run. Each retry
    overwrites it. On a complete failure (budget exhausted) the file
    on disk is the LAST attempt -- callers that prefer the BEST
    attempt should keep their own snapshots.

Why a separate module: both Quick Generate (``routers/generate.py``)
and the legacy ``TestCaseScriptBuilder`` need this exact loop. Putting
it here keeps the LLM provider abstraction inside ``ai_bridge`` (one
place) and the validation surface inside ``script_validator`` /
``script_dryrun``, while the orchestration logic lives in one shared
spot.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ai_qa_portal.backend.services.script_dryrun import dryrun as dryrun_validate
from ai_qa_portal.backend.services.script_validator import (
    ValidationError,
    ValidationReport,
    build_fix_prompt,
)
from ai_qa_portal.backend.services.script_validator import (
    validate as ast_validate,
)

logger = logging.getLogger("ai_qa_portal.script_validation_loop")


# Default budget. The first attempt is always made; ``MAX_VALIDATION_RETRIES``
# *additional* attempts are spent fixing errors. So the LLM is called up to
# ``MAX_VALIDATION_RETRIES + 1`` times. Tuned at 2 -> 3 calls because (a)
# beyond 3 attempts the LLM rarely converges and just burns tokens, and (b)
# the user's wall-clock budget for "Quick Generate" is ~15 s; 3 calls at 3-5 s
# each fits inside that.
DEFAULT_MAX_RETRIES = int(os.environ.get("AI_QA_VALIDATION_RETRIES", "2"))
# How long ``robot --dryrun`` is allowed to take per attempt.
DEFAULT_DRYRUN_TIMEOUT_S = float(os.environ.get("AI_QA_DRYRUN_TIMEOUT_S", "30"))


@dataclass
class AttemptRecord:
    """One pass through the loop -- either the initial generation or a
    retry. Captures the report we produced and the fix prompt the loop
    will feed back to the LLM (empty for the final, successful attempt).
    """
    attempt: int                       # 1-indexed
    report: ValidationReport
    fix_prompt: str = ""
    # ``script_excerpt`` is just the first ~600 chars so the UI can show
    # a "diff trail" without storing the full multi-attempt history.
    script_excerpt: str = ""


@dataclass
class ValidationLoopResult:
    final_script: str
    final_report: ValidationReport
    attempts: list[AttemptRecord] = field(default_factory=list)
    converged: bool = False
    # When set, the deterministic recipe-first tier produced the
    # script and NO LLM was called. Carries the matched recipe name
    # (e.g. ``"lead_routing_by_state"``) so callers can render a
    # "Generated from recipe: X" badge in the UI. None for LLM-only
    # generations.
    used_recipe: str | None = None
    used_recipe_confidence: str | None = None
    # Phase 1 Playwright integration. ``locator_validation_ok`` is
    # None when the gate was disabled for this generation (most
    # current production traffic), True when all checked locators
    # resolved on the live page, False when at least one failed.
    # ``locator_validation_count`` and ``locator_validation_failed``
    # are populated whenever the gate ran (regardless of result).
    # ``locator_validation_shadow`` records whether shadow mode was on
    # so the API surface can render "ran in observation mode" vs
    # "blocked your save".
    locator_validation_ok: bool | None = None
    locator_validation_count: int = 0
    locator_validation_failed: int = 0
    locator_validation_shadow: bool = False

    # Convenience: the last AttemptRecord whose report is OK, or the
    # last one overall if the loop never converged. UI uses this to
    # decide whether to enable Run.
    @property
    def ok(self) -> bool:
        return self.converged

    def lint_errors(self) -> list[str]:
        """Render the final report's errors as user-facing strings, the
        same shape the existing API surface uses for ``lint_errors``."""
        if self.final_report.ok:
            return []
        out: list[str] = []
        for e in self.final_report.errors:
            loc = f"L{e.line}" if e.line else "—"
            out.append(f"[{loc}] {e.kind}: {e.symbol} — {e.message}")
        if not self.converged and self.attempts:
            out.append(
                f"Validation did not converge after {len(self.attempts)} attempt(s); "
                "see errors above."
            )
        return out


def run_with_validation(
    *,
    llm_call: Callable[[str | None], str],
    post_process: Callable[[str], str],
    extract_robot: Callable[[str], str],
    suite_path: Path,
    max_attempts: int | None = None,
    skip_dryrun: bool = False,
    dryrun_timeout_s: float | None = None,
    locator_validate: Callable[[Path], ValidationReport] | None = None,
    locator_shadow_mode: bool = False,
) -> ValidationLoopResult:
    """Drive the LLM through validate-fix-validate until success or budget.

    Each attempt:
      1. Calls ``llm_call(fix_prompt or None)`` for raw model output.
      2. Runs ``extract_robot(raw)`` to pull the .robot block.
      3. Runs ``post_process(robot)`` for the existing cleanup pipeline.
      4. Writes the result to ``suite_path``.
      5. Runs ``ast_validate``; if ok and ``skip_dryrun`` is False,
         also runs ``dryrun_validate``.
      6. **Optional** (Phase 1 Playwright integration): if
         ``locator_validate`` is provided AND AST + dryrun are clean,
         runs the live-page locator gate. ``locator_shadow_mode=True``
         means "log the result but DO NOT count failures against the
         loop" -- used to collect false-positive metrics for 1-2 weeks
         before flipping the gate to load-bearing.
      7. On success: returns immediately.
         On failure: builds a fix prompt and feeds it into the next
         attempt.

    The function NEVER raises on validation failures -- failures are
    captured in the returned ``ValidationLoopResult`` so callers can
    decide whether to surface them inline or fail the request. Real
    exceptions from the LLM, file I/O, or extract step DO propagate.
    """
    budget = (max_attempts if max_attempts is not None else DEFAULT_MAX_RETRIES) + 1
    if budget < 1:
        budget = 1
    timeout = dryrun_timeout_s if dryrun_timeout_s is not None else DEFAULT_DRYRUN_TIMEOUT_S

    attempts: list[AttemptRecord] = []
    fix_prompt: str | None = None
    final_script: str = ""
    final_report: ValidationReport = ValidationReport(ok=False, errors=[])
    # Locator-validation stats accumulate across attempts; we report
    # the LAST run's stats since each attempt overwrites the suite.
    last_locator_count: int = 0
    last_locator_failed: int = 0
    last_locator_ok: bool | None = None  # None = gate never ran

    for n in range(1, budget + 1):
        raw = llm_call(fix_prompt)
        robot_text = extract_robot(raw)
        if not robot_text or not robot_text.strip():
            err = ValidationError(
                line=0, column=0, kind="parse_error",
                symbol="empty",
                message="LLM returned no usable .robot content.",
            )
            final_report = ValidationReport(ok=False, errors=[err])
            attempts.append(AttemptRecord(
                attempt=n, report=final_report, fix_prompt=fix_prompt or "",
                script_excerpt="",
            ))
            # No content to feed back -- just retry with a generic nudge.
            fix_prompt = (
                "Your previous response did not contain a parsable Robot Framework "
                "test file. Return ONLY the *** Settings *** ... *** Test Cases *** "
                "Robot file with no commentary, no markdown fences, nothing else."
            )
            continue

        robot_text = post_process(robot_text)
        suite_path.parent.mkdir(parents=True, exist_ok=True)
        suite_path.write_text(robot_text.rstrip() + "\n", encoding="utf-8")

        # AST gate first (cheap), dryrun second (expensive).
        report = ast_validate(suite_path)
        if report.ok and not skip_dryrun:
            try:
                dr = dryrun_validate(suite_path, timeout=timeout)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # dryrun is best-effort: a tooling failure (subprocess,
                # OS, missing robot.exe) must not block generation. Log
                # + treat as clean so the AST verdict stands.
                logger.warning("script_validation_loop: dryrun crashed: %s", exc)
                dr = ValidationReport(ok=True, errors=[])
            if not dr.ok:
                report = ValidationReport(
                    ok=False,
                    errors=list(report.errors) + list(dr.errors),
                    keyword_calls_seen=report.keyword_calls_seen,
                    variable_refs_seen=report.variable_refs_seen,
                )

        # Phase 1: Playwright locator gate. Only run when AST + dryrun
        # passed -- no point checking selectors against a live page when
        # the script doesn't even parse. The gate is opt-in (caller
        # passes ``locator_validate``) AND can be flipped to shadow
        # mode where failures log but don't block the loop.
        if report.ok and locator_validate is not None:
            try:
                loc_report = locator_validate(suite_path)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # Same soft-fail policy as dryrun: infrastructure errors
                # (Playwright down, SF unreachable) must not block
                # generation.
                logger.warning("script_validation_loop: locator validation crashed: %s", exc)
                loc_report = ValidationReport(ok=True, errors=[])
            # Accumulate stats regardless of outcome -- callers want to
            # show "12/12 verified" even when nothing was wrong.
            last_locator_failed = len(loc_report.errors)
            # Total checked = pass + fail. The gate doesn't expose total
            # explicitly when ok; infer "0 fails == all passed". When
            # there are failures the gate could have skipped some due to
            # deadline, so treat fails as "at least N checked".
            last_locator_count = max(last_locator_count, last_locator_failed)
            last_locator_ok = loc_report.ok
            if not loc_report.ok:
                if locator_shadow_mode:
                    # Shadow mode: log the failure for false-positive
                    # metrics but treat as clean for the loop's purposes.
                    logger.info(
                        "locator-validate shadow: %d stale locator(s) detected (not blocking)",
                        len(loc_report.errors),
                    )
                else:
                    report = ValidationReport(
                        ok=False,
                        errors=list(report.errors) + list(loc_report.errors),
                        keyword_calls_seen=report.keyword_calls_seen,
                        variable_refs_seen=report.variable_refs_seen,
                    )

        excerpt = robot_text.strip()[:600]
        if report.ok:
            attempts.append(AttemptRecord(
                attempt=n, report=report, fix_prompt=fix_prompt or "",
                script_excerpt=excerpt,
            ))
            final_script = robot_text
            final_report = report
            return ValidationLoopResult(
                final_script=final_script,
                final_report=final_report,
                attempts=attempts,
                converged=True,
                locator_validation_ok=last_locator_ok,
                locator_validation_count=last_locator_count,
                locator_validation_failed=last_locator_failed,
                locator_validation_shadow=locator_shadow_mode,
            )

        # Failure: queue a fix prompt for the next round.
        fix_prompt = build_fix_prompt(report)
        attempts.append(AttemptRecord(
            attempt=n, report=report, fix_prompt=fix_prompt,
            script_excerpt=excerpt,
        ))
        final_script = robot_text
        final_report = report

    return ValidationLoopResult(
        final_script=final_script,
        final_report=final_report,
        attempts=attempts,
        converged=False,
        locator_validation_ok=last_locator_ok,
        locator_validation_count=last_locator_count,
        locator_validation_failed=last_locator_failed,
        locator_validation_shadow=locator_shadow_mode,
    )
