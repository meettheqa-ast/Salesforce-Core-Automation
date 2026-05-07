from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from ..models.org import SalesforceOrg
from ..models.persona import Persona
from ..models.test_case import TestCase
from ..prompts import assembler

logger = logging.getLogger("ai_qa_portal.test_case_script_builder")


class TestCaseScriptBuilder:
    """Builder: approved test case -> complete `.robot` file.

    Powered by the assembler, so this path now sees the full Salesforce
    playbook (recipes, anti-patterns, few-shots, suite skeleton) and the
    full live keyword catalog. Previously the system prompt was a single
    sentence ("You are a Robot Framework expert") with no library
    context, which is why it kept inventing keywords like raw
    `Click Element` for picklists.

    Persona and org are accepted but optional. When omitted (the
    pre-flight build-scripts flow), the prompt instructs the LLM to use
    Robot variables for credentials -- the runner injects real values at
    run time. When supplied (the legacy inline path in runs.py), the
    Org's login URL is mentioned as additional context so the LLM can
    pick org-shape-specific helpers if it has them.

    Generation is wrapped in the validate-fix-validate loop from
    ``script_validation_loop`` so the script returned to callers is
    AST-clean and (optionally) passes ``robot --dryrun``. Callers that
    want the legacy single-shot behaviour can pass
    ``validate=False``.
    """

    def build_robot_script(
        self,
        tc: TestCase,
        persona: Optional[Persona] = None,
        org: Optional[SalesforceOrg] = None,
        *,
        validate: bool = True,
        skip_dryrun: bool = False,
    ) -> str:
        from ai_bridge import call_llm, extract_robot_code

        steps_block = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(tc.steps))
        org_hint = (
            f"Org login URL: {org.login_url} (do NOT embed; use ${{globalSandboxTestUrl}})"
            if org is not None
            else "Org: not pinned -- use ${globalSandboxTestUrl}."
        )

        user_body = (
            f"Test case title: {tc.title}\n"
            f"Preconditions: {tc.preconditions or 'None'}\n"
            f"{org_hint}\n\n"
            f"Steps:\n{steps_block}\n\n"
            f"Expected result: {tc.expected_result}\n"
        )
        if tc.tags:
            user_body += f"\nTags to include: {', '.join(tc.tags)}\n"

        # Persona's default_app is the only metadata we currently surface
        # to the LLM. Future role-profile specialisation ("as a Sales
        # Manager...") would extend assembler.render_persona_context.
        persona_default_app = (
            getattr(persona, "default_app", None) if persona is not None else None
        )

        system_prompt = assembler.build_system_prompt("builder")
        user_prompt = assembler.build_user_prompt_with_catalog(
            user_body,
            include_full_catalog=True,
            default_app=persona_default_app,
        )

        if not validate:
            return call_llm(system_prompt, user_prompt)

        # Validation loop. The TestCase builder writes through a tempfile
        # because callers store the returned text wherever they want;
        # this scratch path is just so the validator + dryrun have a real
        # on-disk file to work against. Each retry overwrites it.
        from .script_validation_loop import run_with_validation

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".robot", delete=False, encoding="utf-8",
        ) as scratch:
            scratch_path = Path(scratch.name)

        try:
            def _llm(fix_prompt: Optional[str]) -> str:
                content = user_prompt
                if fix_prompt:
                    content = (
                        user_prompt
                        + "\n\n## Validator feedback (attempt failed)\n\n"
                        + fix_prompt
                    )
                return call_llm(system_prompt, content)

            def _post(robot_source: str) -> str:
                from ai_bridge import (
                    fix_misplaced_setup_teardown,
                    strip_credential_variable_overrides,
                    strip_empty_variable_overrides,
                    strip_llm_robot_garbage,
                    strip_hallucinated_csv_variables_from_suite,
                )
                cleaned = strip_credential_variable_overrides(robot_source)
                cleaned = strip_empty_variable_overrides(cleaned)
                cleaned = strip_llm_robot_garbage(cleaned)
                cleaned = strip_hallucinated_csv_variables_from_suite(cleaned)
                cleaned = fix_misplaced_setup_teardown(cleaned)
                return cleaned

            result = run_with_validation(
                llm_call=_llm,
                post_process=_post,
                extract_robot=extract_robot_code,
                suite_path=scratch_path,
                skip_dryrun=skip_dryrun,
            )
            if not result.converged:
                # Log but don't raise -- callers (RunsService etc.) need
                # SOMETHING to ship; degraded scripts go through with
                # validation errors annotated for downstream UI.
                logger.warning(
                    "TestCaseScriptBuilder: validation did not converge "
                    "after %d attempts; returning last attempt with %d errors",
                    len(result.attempts), len(result.final_report.errors),
                )
            return result.final_script
        finally:
            try:
                scratch_path.unlink(missing_ok=True)
            except OSError:
                pass
