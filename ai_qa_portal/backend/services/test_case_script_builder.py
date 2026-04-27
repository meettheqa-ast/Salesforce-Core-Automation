from __future__ import annotations

from typing import Optional

from ..models.org import SalesforceOrg
from ..models.persona import Persona
from ..models.test_case import TestCase


class TestCaseScriptBuilder:
    """Turn a `TestCase` into Robot Framework code via the configured LLM.

    Persona and org are optional. When omitted (the build-scripts pre-flight
    flow), the generated script uses Robot variables (`${globalSandboxTestUrl}`,
    `${sandboxUserNameInput}`, `${sandboxPasswordInput}`) which the runner
    injects via `robot --variable` at run time. This keeps a single saved
    script reusable across orgs and personas; only the inline run path in
    `routers/runs.py` ever passes a concrete persona+org, and even there the
    generated text still leans on those same Robot variables for credentials.
    """

    def build_robot_script(
        self,
        tc: TestCase,
        persona: Optional[Persona] = None,
        org: Optional[SalesforceOrg] = None,
    ) -> str:
        from ai_bridge import call_llm

        # Persona is not yet woven into the prompt -- accepted for API
        # compatibility with existing callers and so a future revision can
        # specialise the prompt by role profile without touching call sites.
        _ = persona
        steps_block = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(tc.steps))
        login_url = org.login_url if org is not None else "${globalSandboxTestUrl}"
        prompt = f"""
Convert this test case into a Robot Framework test script for Salesforce.
Login URL: {login_url}
Username variable: ${{sandboxUserNameInput}}
Password variable: ${{sandboxPasswordInput}}

Test case title: {tc.title}
Preconditions: {tc.preconditions or "None"}
Steps:
{steps_block}
Expected result: {tc.expected_result}

Return ONLY valid Robot Framework syntax. No explanation.
Use GlobalKeywords and SalesPO resources as in existing suites.
The script MUST resolve credentials from ${{sandboxUserNameInput}} and
${{sandboxPasswordInput}} only -- never hard-code values. Do not embed any
real URL, username, or password literal in the output.
""".strip()
        system = "You are a Robot Framework expert for Salesforce UI tests."
        return call_llm(system, prompt)
