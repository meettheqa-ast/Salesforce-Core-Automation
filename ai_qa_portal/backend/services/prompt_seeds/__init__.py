"""System default prompt bodies.

Each ``.md`` file in this directory is loaded at startup by the
``prompt_registry.seed_system_templates`` boot hook. The filename
(stem) maps to a ``(category, name, output_format)`` triple declared
in ``SEED_MANIFEST`` below.

Why files-on-disk instead of inline Python constants:
  * Designers and PMs can read / propose edits in plain markdown.
  * `git blame` on the seed file tells a clean story (the previous
    inline ``_TAIL_*`` constants gave us 462-line diffs even for
    one-word tweaks).
  * The seeder hashes each file's contents; when a release ships an
    updated seed, every existing deployment automatically gets a new
    *system* version appended without touching user overrides.

Adding a seed:
  1. Drop a new ``.md`` file in this folder.
  2. Add an entry to ``SEED_MANIFEST`` with its category + display name
     + output_format + declared placeholders.
  3. Restart the backend; the seeder takes care of the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SeedSpec:
    """One row in the seed manifest. The seeder uses these to create /
    update one ``prompt_templates`` row per file."""

    filename: str  # without directory; e.g. "test_case_drafter.md"
    category: str
    name: str
    description: str
    output_format: str  # json_array | markdown_table | robot_script | freeform
    placeholders_declared: tuple[str, ...]
    # When true, the resolver composes this seed with the shared
    # salesforce_playbook.md base layer. When false, the seed is the
    # entire system prompt on its own (the two new Zephyr-style prompts
    # bring their own context and don't need the Robot playbook).
    compose_with_playbook: bool = True


SEED_MANIFEST: tuple[SeedSpec, ...] = (
    # ----- Original role tails (extracted from assembler.py) ---------
    SeedSpec(
        filename="test_case_drafter.md",
        category="test_case_drafter",
        name="Default JSON drafter",
        description=(
            "Original story-to-test-case drafter. Emits a JSON array "
            "matching GeneratedTestCase exactly. This is the out-of-the-"
            "box default for test_case_drafter -- the Salesforce "
            "Structured and Zephyr Enterprise seeds ship alongside it "
            "and admins flip the org default by editing the org-level "
            "override."
        ),
        output_format="json_array",
        placeholders_declared=(
            "story", "project", "persona", "qa_mode", "rag_context",
            "keyword_catalog",
        ),
    ),
    SeedSpec(
        filename="test_case_drafter_structured_sf.md",
        category="test_case_drafter",
        name="Salesforce Structured QA",
        description=(
            "Comprehensive Salesforce-aware test case generator emitting "
            "a structured Markdown table. Covers positive/negative/"
            "boundary/governor-limit/sharing scenarios."
        ),
        output_format="markdown_table",
        placeholders_declared=(
            "story", "project", "persona", "qa_mode",
        ),
        compose_with_playbook=False,
    ),
    SeedSpec(
        filename="test_case_drafter_zephyr_enterprise.md",
        category="test_case_drafter",
        name="Enterprise Zephyr QA",
        description=(
            "Astound enterprise Zephyr-aligned generator. Markdown-only "
            "output, qa_mode-aware (salesforce vs general), produces "
            "Zephyr-importable test cases."
        ),
        output_format="markdown_table",
        placeholders_declared=(
            "story", "project", "persona", "qa_mode", "input_json",
            "linked_output",
        ),
        compose_with_playbook=False,
    ),
    SeedSpec(
        filename="script_builder.md",
        category="script_builder",
        name="Approved TC to Robot script",
        description=(
            "Receives one approved test case and emits a complete "
            "Robot Framework .robot file."
        ),
        output_format="robot_script",
        placeholders_declared=(
            "test_case", "project", "persona", "keyword_catalog",
        ),
    ),
    SeedSpec(
        filename="quick_robot.md",
        category="quick_robot",
        name="Quick Generate from prompt",
        description="Free-form natural-language prompt to a complete Robot suite.",
        output_format="robot_script",
        placeholders_declared=(
            "user_prompt", "project", "persona", "keyword_catalog",
        ),
    ),
    SeedSpec(
        filename="stepwise_planner.md",
        category="stepwise_planner",
        name="Stepwise MCP planner",
        description=(
            "Emits an ordered JSON array of keyword calls the RF-MCP "
            "runtime executes one step at a time in a live browser."
        ),
        output_format="json_array",
        placeholders_declared=(
            "user_prompt", "project", "persona", "keyword_catalog",
            "rag_context",
        ),
    ),
    SeedSpec(
        filename="healer.md",
        category="healer",
        name="Script healer",
        description=(
            "Diagnoses a failed Robot run and emits a complete corrected "
            ".robot file."
        ),
        output_format="robot_script",
        placeholders_declared=(
            "test_case", "current_script", "failing_keyword",
            "error_message", "screenshot_available",
        ),
    ),
    SeedSpec(
        filename="recording_translator.md",
        category="recording_translator",
        name="Recording translator",
        description=(
            "Translates a captured browser action log into a Robot suite."
        ),
        output_format="robot_script",
        placeholders_declared=("action_log", "project", "persona"),
    ),
)


def seeds_dir() -> Path:
    return Path(__file__).resolve().parent


def read_seed_body(filename: str) -> str:
    """Read a seed body from disk. The seeder catches FileNotFoundError
    and skips that entry so a partially-deployed release never crashes
    on boot."""
    path = seeds_dir() / filename
    return path.read_text(encoding="utf-8")
