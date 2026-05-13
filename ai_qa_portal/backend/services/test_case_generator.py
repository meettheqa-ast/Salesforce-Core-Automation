from __future__ import annotations

import asyncio
import json
import re

from pydantic import ValidationError

from ..models.generation import GeneratedTestCase
from ..models.user_story import UserStory
from ..prompts import assembler


class TestCaseGenerator:
    """Drafter: user story -> structured test case definitions.

    The system prompt is now assembled from the Salesforce playbook (so
    drafted steps reference real recipes and field names) plus the
    drafter-specific output rules. The user content includes the keyword
    name list (not full args/docs -- the drafter doesn't need to know
    signatures, only what the library is capable of) so the steps it
    invents are framed in terms of actually-implementable actions.
    """

    async def generate(
        self,
        user_story: UserStory,
        *,
        db=None,
        project_slug: str | None = None,
    ) -> list[GeneratedTestCase]:
        """Draft test cases for one user story.

        ``db`` + ``project_slug`` are optional. When both are present, we
        fetch retrieval-augmented context for the story (Jira backlog
        text, related comments, uploaded docs) and pass it through to the
        LLM so drafted steps match the team's terminology.
        """
        from ai_bridge import call_llm

        system_prompt = assembler.build_system_prompt("drafter")
        user_body = (
            f"User story title: {user_story.title}\n\n"
            f"Description:\n{user_story.description}"
        )
        rag_block = ""
        if db is not None and project_slug:
            try:
                from .rag_retrieval import format_passages_block, retrieve
                passages = retrieve(
                    db,
                    project_slug=project_slug,
                    query=f"{user_story.title}\n{user_story.description}",
                    sprint_id=str(user_story.sprint_id) if getattr(user_story, "sprint_id", None) else None,
                    story_id=str(user_story.id),
                    limit=6,
                )
                rag_block = format_passages_block(passages)
            except Exception:
                # Drafter should never block on a retrieval miss.
                rag_block = ""

        # Drafter gets just the keyword names -- enough to anchor steps
        # to real capabilities without spending tokens on full signatures.
        user_prompt = assembler.build_user_prompt_with_catalog(
            user_body,
            include_full_catalog=False,
            catalog_section_title="Available keyword names (for reference)",
            rag_context=rag_block,
        )

        response = await asyncio.to_thread(call_llm, system_prompt, user_prompt)
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
