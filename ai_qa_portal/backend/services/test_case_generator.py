from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..models.generation import GeneratedTestCase
from ..models.user_story import UserStory
from ..prompts import assembler


@dataclass
class GenerationProvenance:
    """Returned alongside the test cases when the caller wants to stamp
    provenance on the persisted rows + write a prompt_usage_audit row.

    Every field is optional so legacy callers that just want the
    drafted test cases can keep using ``.generate(...)`` -- the new
    ``.generate_with_provenance(...)`` returns both."""

    template_version_id: str | None = None
    template_id: str | None = None
    category: str | None = None
    output_format: str | None = None
    source_scope: str | None = None
    model: str | None = None
    provider: str | None = None
    qa_mode: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    warnings: list[str] = field(default_factory=list)


class TestCaseGenerator:
    """Drafter: user story -> structured test case definitions.

    The system prompt is resolved via the prompt registry when
    ``PROMPT_REGISTRY_ENABLED=true`` (user override -> project ->
    org -> system seed), otherwise the legacy assembler path is used.
    Output is parsed by ``prompt_output_parser.parse`` so a user who
    activates a markdown-table template (Salesforce Structured /
    Enterprise Zephyr) gets the same ``GeneratedTestCase`` shape back
    -- the parser handles both shapes transparently.
    """

    async def generate(
        self,
        user_story: UserStory,
        *,
        db=None,
        project_slug: str | None = None,
        user_id: str | None = None,
        qa_mode: str = "salesforce",
    ) -> list[GeneratedTestCase]:
        """Draft test cases. Back-compat wrapper that discards the
        provenance fields. New call sites should prefer
        :meth:`generate_with_provenance`."""
        cases, _ = await self.generate_with_provenance(
            user_story,
            db=db,
            project_slug=project_slug,
            user_id=user_id,
            qa_mode=qa_mode,
        )
        return cases

    async def generate_with_provenance(
        self,
        user_story: UserStory,
        *,
        db=None,
        project_slug: str | None = None,
        user_id: str | None = None,
        qa_mode: str = "salesforce",
    ) -> tuple[list[GeneratedTestCase], GenerationProvenance]:
        """Draft test cases AND return prompt + LLM provenance so the
        router can stamp ``prompt_version_id`` / ``model_name`` etc.
        on each persisted TestCase and write one ``prompt_usage_audit``
        row per generation call.
        """
        from ai_bridge import call_llm_with_metadata

        from .prompt_output_parser import OutputParseError, parse

        # Resolve via the registry when the flag is on, falling back to
        # the legacy assembler text via the AssembledPrompt wrapper.
        assembled = assembler.build_system_prompt_resolved(
            "drafter",
            user_id=user_id,
            project_id=str(user_story.project_id) if getattr(user_story, "project_id", None) else None,
        )
        system_prompt = assembled.text
        output_format = assembled.output_format or "json_array"

        user_body = (
            f"User story title: {user_story.title}\n\n"
            f"Description:\n{user_story.description}\n\n"
            f"QA Mode: {qa_mode}\n"
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

        llm_result = await asyncio.to_thread(
            call_llm_with_metadata, system_prompt, user_prompt,
        )

        try:
            parsed = parse(llm_result.text, output_format=output_format)
        except OutputParseError as exc:
            raise ValueError(
                f"LLM output could not be parsed as {output_format}: {exc}",
            ) from exc

        provenance = GenerationProvenance(
            template_version_id=assembled.version_id,
            template_id=assembled.template_id,
            category=assembled.category,
            output_format=assembled.output_format,
            source_scope=assembled.source_scope,
            model=llm_result.model,
            provider=llm_result.provider,
            qa_mode=qa_mode,
            input_tokens=llm_result.input_tokens,
            output_tokens=llm_result.output_tokens,
            latency_ms=llm_result.latency_ms,
            warnings=parsed.warnings,
        )
        return parsed.test_cases, provenance
