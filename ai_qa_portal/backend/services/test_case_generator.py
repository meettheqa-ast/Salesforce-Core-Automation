from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..models.generation import GeneratedTestCase
from ..models.user_story import UserStory

if TYPE_CHECKING:
    pass


class TestCaseGenerator:
    SYSTEM_PROMPT = """
You are a QA engineer. Given a user story, generate structured test cases.
Return ONLY a JSON array. No markdown, no explanation, no preamble.
Each element must have exactly these keys:
  - title: string
  - steps: array of strings (each step is an action)
  - expected_result: string
  - preconditions: string or null
  - suggested_tags: array of strings from ["Smoke","Regression","Sanity","E2E"] or empty
Do not include any other keys.
""".strip()

    async def generate(self, user_story: UserStory) -> list[GeneratedTestCase]:
        from ai_bridge import call_llm

        prompt = (
            f"User story title: {user_story.title}\n\nDescription:\n{user_story.description}"
        )
        response = await asyncio.to_thread(
            call_llm,
            self.SYSTEM_PROMPT,
            prompt,
        )
        raw = response.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```\s*$", "", raw)
        raw = raw.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM returned invalid JSON: {e}. Raw: {raw[:200]}") from e
        if not isinstance(data, list):
            raise ValueError(f"LLM must return a JSON array. Raw: {raw[:200]}")
        out: list[GeneratedTestCase] = []
        for item in data:
            try:
                out.append(GeneratedTestCase(**item))
            except ValidationError as e:
                raise ValueError(f"Invalid test case object: {e}. Item: {item!r}") from e
        return out
