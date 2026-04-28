from __future__ import annotations

from typing import Optional

from ..models.org import SalesforceOrg
from ..models.persona import Persona
from ..models.test_case import TestCase
from ..prompts import assembler


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
    """

    def build_robot_script(
        self,
        tc: TestCase,
        persona: Optional[Persona] = None,
        org: Optional[SalesforceOrg] = None,
    ) -> str:
        from ai_bridge import call_llm

        # Persona role profile is hinted but not required; future prompt
        # tuning may specialise behaviour ("as a Sales Manager...") --
        # for now we just pass the persona name in context.
        _ = persona

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

        system_prompt = assembler.build_system_prompt("builder")
        user_prompt = assembler.build_user_prompt_with_catalog(
            user_body,
            include_full_catalog=True,
        )
        return call_llm(system_prompt, user_prompt)
