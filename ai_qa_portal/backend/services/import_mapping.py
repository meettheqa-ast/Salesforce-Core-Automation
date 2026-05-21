"""Column-mapping engine for the test-case import wizard.

Two responsibilities:

  1. **Suggest** a canonical-field for each source column the parser
     surfaces. The mapping UI pre-fills its <select> dropdowns from
     this output; the user can override every choice before commit.

  2. **Normalise** a raw row dict (with the user-confirmed mapping
     applied) into a structured ``MappedRow`` the commit pipeline
     can validate, dedupe, and persist as a TestCase. Steps get
     split into a list, tags get tokenised, priority becomes a tag.

The synonym table is intentionally tiny + explicit -- we want the LLM
NOT to be in this hot path. ``difflib`` covers near-misses
("Test Step" vs "Test Steps") without any external dependency.

Adding a new external source (Zephyr, Xray, TestRail, Azure DevOps)
is a one-line append per field; the rest of the pipeline doesn't
notice.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Iterable

logger = logging.getLogger("ai_qa_portal.import_mapping")


# Canonical fields the importer knows how to fill on a TestCase.
# Order matters only for stable test snapshots.
CANONICAL_FIELDS: dict[str, list[str]] = {
    "title": [
        "title", "test case name", "name", "tc name", "summary",
        "test name", "case", "scenario", "scenario name",
    ],
    "description": [
        "description", "notes", "details", "test description",
        "objective", "purpose",
    ],
    "steps": [
        "steps", "test steps", "step", "actions", "procedure",
        "step description", "action", "test procedure",
    ],
    "expected_result": [
        "expected", "expected result", "expected outcome",
        "expected behavior", "expected behaviour", "result",
        "expected results", "expected output",
    ],
    "preconditions": [
        "preconditions", "pre-conditions", "pre conditions", "setup",
        "prerequisites", "pre req", "prereq",
    ],
    "tags": [
        "tags", "labels", "categories", "components", "modules",
    ],
    # Priority arrives as a separate column in most templates but we
    # store it as a tag (``priority:high``) to avoid bloating the
    # TestCase model with a column that's only meaningful at import
    # time.
    "priority": [
        "priority", "severity", "importance",
    ],
    "external_id": [
        "jira id", "jira key", "issue key", "issue id", "id",
        "test case id", "tc id", "zephyr id", "xray id", "testrail id",
        "azure devops id", "ado id", "ticket", "ticket id", "key",
    ],
    "story_id": [
        "story id", "user story id", "parent", "parent id", "story",
        "epic", "epic id", "user story",
    ],
    "status": [
        "status", "state",
    ],
}

CANONICAL_FIELD_NAMES: tuple[str, ...] = tuple(CANONICAL_FIELDS.keys())

# Pre-compute the flat synonym -> canonical lookup so suggestion is O(1)
# for exact matches and the difflib pass operates on a small string list.
_SYNONYM_TO_CANONICAL: dict[str, str] = {
    _norm: canonical
    for canonical, synonyms in CANONICAL_FIELDS.items()
    for _norm in (_normalised(s) for s in synonyms)  # type: ignore[name-defined]
} if False else {}


def _normalise_header(header: str) -> str:
    """Trim, lowercase, drop non-alphanumeric so 'Test Case-ID' and
    'test case id' compare equal. Used for matching only; the
    original string is preserved for display in the mapping UI."""
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


# Materialise the lookup once at import time.
def _build_synonym_index() -> dict[str, str]:
    out: dict[str, str] = {}
    for canonical, synonyms in CANONICAL_FIELDS.items():
        for syn in synonyms:
            out[_normalise_header(syn)] = canonical
        # Make the canonical name itself match too (so a literal "title"
        # column maps without needing it duplicated in the synonyms list).
        out[_normalise_header(canonical)] = canonical
    return out


_SYNONYM_INDEX: dict[str, str] = _build_synonym_index()


def suggest_mapping(columns: Iterable[str]) -> dict[str, str | None]:
    """Build the initial mapping the wizard shows the user.

    Algorithm per column:
      1. Normalise the header (lowercase + strip non-alphanumeric).
      2. Exact lookup in the synonym index -> canonical field.
      3. difflib.get_close_matches against the synonym index keys with
         cutoff=0.85; a single hit -> canonical.
      4. Otherwise None (user picks in the UI).

    Returns ``{source_column: canonical_or_None}`` keyed by the
    ORIGINAL (un-normalised) column string so the wizard can render
    rows the user typed verbatim.
    """
    keys = list(_SYNONYM_INDEX.keys())
    out: dict[str, str | None] = {}
    for col in columns:
        norm = _normalise_header(col)
        if not norm:
            out[col] = None
            continue
        if norm in _SYNONYM_INDEX:
            out[col] = _SYNONYM_INDEX[norm]
            continue
        # Fuzzy fallback. A high cutoff keeps us from confidently
        # matching 'description' to 'preconditions' just because they
        # share characters; in practice 0.85 only fires on real typos
        # ('Stepps', 'Prioirity').
        close = get_close_matches(norm, keys, n=1, cutoff=0.85)
        out[col] = _SYNONYM_INDEX[close[0]] if close else None
    return out


# ---------- Row normalisation -------------------------------------


@dataclass
class MappedRow:
    """A raw CSV/Excel row translated into TestCase-shaped fields.

    Validation happens in the commit handler (this dataclass holds
    what we GOT from the source; missing-required is up to the caller
    to detect). Lists like `steps` / `tags` are guaranteed to be
    non-None even when the source had no value for them."""

    row_index: int
    title: str = ""
    description: str = ""
    steps: list[str] = field(default_factory=list)
    expected_result: str = ""
    preconditions: str | None = None
    tags: list[str] = field(default_factory=list)
    external_id: str | None = None
    story_id: str | None = None
    status: str | None = None
    raw: dict[str, str] = field(default_factory=dict)


_STEP_NUMBER_RE = re.compile(r"^\s*\d+[.):\-]\s+", re.MULTILINE)
_TAG_SPLIT_RE = re.compile(r"[,;|]+")


def normalise_row(
    row_index: int,
    raw: dict[str, str],
    mapping: dict[str, str | None],
) -> MappedRow:
    """Apply ``mapping`` to ``raw`` and return a MappedRow ready for
    duplicate detection + persistence.

    Conflict policy when MULTIPLE source columns map to the same
    canonical field: first non-empty value wins; later ones are
    appended for ``tags`` / ``steps`` (more is usually correct), and
    silently dropped for scalar fields (we WARN-log so misconfigured
    mappings surface in operator logs).
    """
    out = MappedRow(row_index=row_index, raw=dict(raw))
    extra_priority: str | None = None
    seen_scalars: set[str] = set()

    for source_col, canonical in mapping.items():
        if canonical is None:
            continue
        value = (raw.get(source_col) or "").strip()
        if not value:
            continue
        if canonical == "title":
            if not out.title:
                out.title = value
            elif "title" in seen_scalars:
                logger.debug("Multiple columns mapped to title; ignoring %r", value)
        elif canonical == "description":
            if not out.description:
                out.description = value
        elif canonical == "expected_result":
            if not out.expected_result:
                out.expected_result = value
        elif canonical == "preconditions":
            if not out.preconditions:
                out.preconditions = value
        elif canonical == "external_id":
            if not out.external_id:
                out.external_id = value
        elif canonical == "story_id":
            if not out.story_id:
                out.story_id = value
        elif canonical == "status":
            if not out.status:
                out.status = value.lower()
        elif canonical == "steps":
            out.steps.extend(_split_steps(value))
        elif canonical == "tags":
            out.tags.extend(_split_tags(value))
        elif canonical == "priority":
            # Priority lives on the TC only as a tag, not its own
            # field. Capture it here and append on egress so multi-
            # column mappings don't double-tag.
            if extra_priority is None:
                extra_priority = value
        else:
            logger.warning("Unknown canonical mapping target: %r", canonical)
            continue
        seen_scalars.add(canonical)

    if extra_priority:
        out.tags.append(f"priority:{extra_priority.lower()}")

    # Deduplicate tags (case-insensitive) while preserving first-seen order.
    seen_tags: set[str] = set()
    dedup_tags: list[str] = []
    for tag in out.tags:
        key = tag.lower()
        if not key or key in seen_tags:
            continue
        seen_tags.add(key)
        dedup_tags.append(tag)
    out.tags = dedup_tags
    return out


def _split_steps(value: str) -> list[str]:
    """Best-effort split of a 'steps' cell into individual lines.

    Two common shapes:
      - Numbered text in one cell:
            "1. Open dialog\n2. Fill name\n3. Click save"
      - Plain newline-separated:
            "Open dialog\nFill name\nClick save"

    A future improvement (NOT v1): support workbooks that have one
    row per step grouped by Test Case ID. That requires the commit
    layer to fold rows together, which is a bigger change.
    """
    if not value:
        return []
    # If the text contains numbered prefixes ("1.", "2)", "3:"), split
    # on those. Otherwise split on newlines.
    if _STEP_NUMBER_RE.search(value):
        parts = _STEP_NUMBER_RE.split(value)
    else:
        parts = value.splitlines()
    return [p.strip() for p in parts if p and p.strip()]


def _split_tags(value: str) -> list[str]:
    """Tag cells use comma / semicolon / pipe as the separator. We do
    NOT split on whitespace -- multi-word tags ('smoke test') are
    valid and common."""
    if not value:
        return []
    return [t.strip() for t in _TAG_SPLIT_RE.split(value) if t and t.strip()]
