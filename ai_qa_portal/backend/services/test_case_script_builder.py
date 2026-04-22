from __future__ import annotations

from ..models.org import SalesforceOrg
from ..models.persona import Persona
from ..models.test_case import TestCase


class TestCaseScriptBuilder:
    def build_robot_script(self, tc: TestCase, persona: Persona, org: SalesforceOrg) -> str:
        from ai_bridge import call_llm

        steps_block = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(tc.steps))
        prompt = f"""
Convert this test case into a Robot Framework test script for Salesforce.
Login URL: {org.login_url}
Username variable: ${{sandboxUserNameInput}}
Password variable: ${{sandboxPasswordInput}}

Test case title: {tc.title}
Preconditions: {tc.preconditions or "None"}
Steps:
{steps_block}
Expected result: {tc.expected_result}

Return ONLY valid Robot Framework syntax. No explanation.
Use GlobalKeywords and SalesPO resources as in existing suites.
""".strip()
        system = "You are a Robot Framework expert for Salesforce UI tests."
        return call_llm(system, prompt)
