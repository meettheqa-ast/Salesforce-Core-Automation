"""Parse LLM output into the shape the persistence layer expects.

Dispatched by ``output_format`` from the resolved template:

  * ``json_array``      -> existing JSON array of GeneratedTestCase.
                           Reuses the same lenient parser the legacy
                           drafter path used (fenced-code fallback,
                           trailing-text scrubber).
  * ``markdown_table``  -> detect the first GFM table; map columns to
                           GeneratedTestCase fields using a synonym
                           dictionary that covers both shipped seed
                           schemas plus arbitrary user-edited columns.
                           Splits multi-row Pre-conditions / Test Steps
                           / Expected Results cells (numbered lists).
  * ``robot_script``    -> pass through unchanged (the script builders
                           emit raw ``.robot`` text, not test cases).
  * ``freeform``        -> pass through unchanged.

The Zephyr seed appends "Confidence Level: ..." after the table; we
ignore everything after the table closes. Same for the Salesforce
Structured seed's "OUTPUT COMPLETENESS CHECK" footer.

Why a separate module: the old drafter path lived inside
``TestCaseGenerator`` as inline JSON parsing. Moving it here lets the
test_case_generator service stay focused on orchestration and lets us
add new output formats (e.g. ``yaml`` for a future Cucumber-style
template) without touching the generation flow.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

from ai_qa_portal.backend.models.generation import GeneratedTestCase

logger = logging.getLogger("ai_qa_portal.prompt_output_parser")


# ---------- public types -----------------------------------------


@dataclass
class ParsedOutput:
    """Common return shape across every format. Generators iterate
    ``test_cases`` (empty for non-TC formats) and use ``raw_text``
    when the format is ``robot_script`` / ``freeform``."""

    test_cases: list[GeneratedTestCase] = field(default_factory=list)
    raw_text: str = ""
    output_format: str = "json_array"
    # Per-row warnings for the audit log / UI: e.g. "row 3 had no title,
    # skipped". Never fatal; the caller decides whether to bail.
    warnings: list[str] = field(default_factory=list)


class OutputParseError(Exception):
    """Raised when output is unparseable in a way the caller cannot
    recover from (e.g. ``json_array`` but the response is not JSON
    AND has no parseable table)."""


# ---------- top-level dispatch -----------------------------------


def parse(raw: str, *, output_format: str) -> ParsedOutput:
    """Single public entry point. Dispatches on ``output_format``."""
    fmt = (output_format or "json_array").lower()
    if fmt == "json_array":
        return _parse_json_array(raw)
    if fmt == "markdown_table":
        return _parse_markdown_table(raw)
    if fmt in ("robot_script", "freeform"):
        return ParsedOutput(raw_text=raw or "", output_format=fmt)
    raise OutputParseError(f"Unknown output_format: {output_format!r}")


# ---------- json_array -------------------------------------------


_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>[\s\S]*?)```", re.IGNORECASE)


def _parse_json_array(raw: str) -> ParsedOutput:
    """Tolerant JSON-array parser. Handles:
      * fenced ```json blocks (LLMs love wrapping)
      * leading/trailing prose
      * trailing commas (best effort)
    Raises ``OutputParseError`` only when no valid array survives.
    """
    text = (raw or "").strip()
    if not text:
        raise OutputParseError("LLM returned empty body.")

    candidates: list[str] = []
    # 1. Look for fenced ```json blocks first.
    for m in _CODE_FENCE_RE.finditer(text):
        candidates.append(m.group("body").strip())
    # 2. Fall back to the largest [...] block.
    bracket = _largest_bracket(text)
    if bracket:
        candidates.append(bracket)
    # 3. Finally try the raw text.
    candidates.append(text)

    last_err: Exception | None = None
    for chunk in candidates:
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError as exc:
            last_err = exc
            continue
        if isinstance(data, dict) and "test_cases" in data:
            data = data["test_cases"]
        if not isinstance(data, list):
            continue
        items: list[GeneratedTestCase] = []
        warnings: list[str] = []
        for i, raw_item in enumerate(data):
            try:
                items.append(_coerce_generated_tc(raw_item, row_index=i))
            except ValueError as exc:
                warnings.append(f"row {i}: {exc}")
        if items:
            return ParsedOutput(
                test_cases=items,
                output_format="json_array",
                warnings=warnings,
            )
    raise OutputParseError(
        f"Could not parse JSON array from LLM output: {last_err}",
    )


def _coerce_generated_tc(item: object, *, row_index: int) -> GeneratedTestCase:
    if not isinstance(item, dict):
        raise ValueError(f"item is not a JSON object ({type(item).__name__})")
    title = str(item.get("title") or "").strip()
    if not title:
        raise ValueError("missing title")
    steps = item.get("steps") or []
    if not isinstance(steps, list):
        steps = [str(steps)]
    expected = str(item.get("expected_result") or "").strip()
    pre = item.get("preconditions")
    if pre is not None:
        pre = str(pre).strip() or None
    tags = item.get("suggested_tags") or item.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    return GeneratedTestCase(
        title=title,
        steps=[str(s).strip() for s in steps if str(s).strip()],
        expected_result=expected,
        preconditions=pre,
        suggested_tags=[str(t).strip() for t in tags if str(t).strip()],
    )


def _largest_bracket(text: str) -> str | None:
    """Return the largest balanced ``[...]`` chunk in ``text`` or
    ``None`` when none found. Used as a fallback when the LLM wraps
    JSON in prose."""
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ---------- markdown_table ---------------------------------------


# Canonical TC field -> set of header synonyms (lowercased, stripped).
# Covers both shipped seeds (Salesforce Structured + Zephyr Enterprise)
# plus the common variations users will introduce when they edit
# templates. New synonyms can be added without touching anything else.
_HEADER_SYNONYMS: dict[str, set[str]] = {
    "title": {
        "title", "summary", "test case", "test case name", "case",
        "scenario", "scenario name", "tc title", "name",
    },
    "objective": {
        "objective", "purpose", "goal", "description",
    },
    "preconditions": {
        "preconditions", "pre-conditions", "pre conditions",
        "prerequisites", "pre-requisites", "setup",
    },
    "steps": {
        "steps", "test steps", "test step", "step", "actions",
        "procedure", "test procedure",
    },
    "expected_result": {
        "expected", "expected result", "expected results",
        "expected outcome", "expected outcomes", "expected behaviour",
        "expected behavior", "result", "expected output",
    },
    "priority": {
        "priority", "severity", "importance",
    },
    "components": {
        "components", "component", "modules", "module",
    },
    "labels": {
        "labels", "tags", "categories",
    },
    "test_type": {
        "test type", "type", "behaviour", "behavior",
    },
    "test_case_id": {
        "test case id", "tc id", "id", "case id", "test id",
    },
    # Salesforce-Structured-specific columns we capture for tags / payload.
    "salesforce_assertions": {"salesforce assertions", "assertions"},
    "test_data": {"test data", "data"},
    "automation_feasibility": {
        "automation feasibility", "automation", "automatable",
    },
    "related_config": {"related config", "related configuration"},
}


def _normalise_header(header: str) -> str:
    """Lowercase + collapse internal whitespace so 'Pre-conditions' and
    'PRE  CONDITIONS' match."""
    h = re.sub(r"\s+", " ", (header or "").strip().lower())
    # Strip trailing/leading non-alphanumerics that aren't spaces / hyphens.
    return h.strip().strip("|")


def _canonical_field(header: str) -> str | None:
    norm = _normalise_header(header)
    if not norm:
        return None
    for canonical, syns in _HEADER_SYNONYMS.items():
        if norm in syns:
            return canonical
    # Soft fuzzy: hyphen/underscore tolerant
    flat = norm.replace("-", " ").replace("_", " ")
    if flat == norm:
        return None
    for canonical, syns in _HEADER_SYNONYMS.items():
        if flat in syns:
            return canonical
    return None


def _parse_markdown_table(raw: str) -> ParsedOutput:
    """Parse the LLM response by extracting every GFM-style table and
    interpreting each one according to its shape:

      * **Wide table** (Zephyr Enterprise seed) -- many columns, each
        row is one test case. Identified by having a Title/Summary
        column in the header.

      * **Vertical table** (Salesforce Structured seed) -- two columns
        named "Field" and "Value"; each table is ONE test case and its
        rows are the field values. Identified by exactly 2 columns
        with first header being Field / Property / Attribute.

    We collect TCs from every table found in the response. Prose
    between tables and the Zephyr "Confidence Level: ..." footer are
    ignored.
    """
    text = raw or ""
    tables = _extract_all_tables(text)
    if not tables:
        raise OutputParseError(
            "No Markdown table found in LLM output. The template uses "
            "output_format='markdown_table' but the response was prose.",
        )

    items: list[GeneratedTestCase] = []
    warnings: list[str] = []
    wide_seen = False
    vertical_seen = False

    for table_idx, table in enumerate(tables):
        header_row, _sep_row, body_rows = table
        headers = [c.strip() for c in _split_row(header_row)]
        if not headers:
            continue

        if _is_vertical_schema(headers):
            tc, vwarn = _parse_vertical_table(headers, body_rows)
            if tc is not None:
                items.append(tc)
                vertical_seen = True
            else:
                warnings.append(f"table {table_idx} (vertical): {vwarn}")
            continue

        canonical = [_canonical_field(h) for h in headers]
        if "title" not in canonical:
            warnings.append(
                f"table {table_idx}: no title/summary column, skipped",
            )
            continue
        wide_seen = True
        for row_idx, raw_row in enumerate(body_rows):
            tc, rwarn = _parse_wide_row(headers, canonical, raw_row)
            if tc is not None:
                items.append(tc)
            else:
                warnings.append(f"table {table_idx} row {row_idx}: {rwarn}")

    if not items:
        raise OutputParseError(
            "Found Markdown table(s) but extracted zero test cases "
            f"({len(warnings)} issues). Saw "
            f"wide={wide_seen} vertical={vertical_seen}.",
        )
    return ParsedOutput(
        test_cases=items,
        output_format="markdown_table",
        warnings=warnings,
    )


def _is_vertical_schema(headers: list[str]) -> bool:
    """A vertical (per-TC) table has exactly 2 columns where the first
    is one of {field, property, attribute}. The Salesforce Structured
    seed emits this shape, one table per test case."""
    if len(headers) != 2:
        return False
    first = _normalise_header(headers[0])
    return first in {"field", "property", "attribute", "key"}


def _parse_vertical_table(
    headers: list[str], body_rows: list[str],
) -> tuple[GeneratedTestCase | None, str]:
    """Collapse a Field|Value table into one GeneratedTestCase. Returns
    (tc, "") on success or (None, reason) on a skip."""
    fields: dict[str, str] = {}
    for raw_row in body_rows:
        cells = _split_row(raw_row)
        if len(cells) < 2:
            continue
        key = _canonical_field(cells[0])
        value = cells[1].strip()
        if key:
            fields.setdefault(key, value)
    title = fields.get("title", "").strip()
    if not title:
        return None, "missing title field"
    steps = _split_numbered(fields.get("steps", ""))
    expected = fields.get("expected_result", "").strip()
    pre = fields.get("preconditions") or None
    if pre:
        pre = pre.strip() or None
    extras: dict[str, str] = {}
    tags = _collect_tags(fields, extras)
    return (
        GeneratedTestCase(
            title=title,
            steps=steps,
            expected_result=expected,
            preconditions=pre,
            suggested_tags=tags,
        ),
        "",
    )


def _parse_wide_row(
    headers: list[str], canonical: list[str | None], raw_row: str,
) -> tuple[GeneratedTestCase | None, str]:
    """One row of a wide table -> one GeneratedTestCase. Returns
    (tc, "") on success or (None, reason) on a skip."""
    cells = _split_row(raw_row)
    while len(cells) < len(headers):
        cells.append("")
    row: dict[str, str] = {}
    extras: dict[str, str] = {}
    for h, canonical_name, cell in zip(headers, canonical, cells):
        cleaned = cell.strip()
        if canonical_name:
            row.setdefault(canonical_name, cleaned)
        else:
            extras[h.strip()] = cleaned
    title = row.get("title", "").strip()
    if not title:
        return None, "missing title"
    steps = _split_numbered(row.get("steps", ""))
    expected = row.get("expected_result", "").strip()
    if not expected and row.get("expected_results"):
        expected = row["expected_results"].strip()
    pre = row.get("preconditions") or None
    if pre:
        pre = pre.strip() or None
    tags = _collect_tags(row, extras)
    return (
        GeneratedTestCase(
            title=title,
            steps=steps,
            expected_result=expected,
            preconditions=pre,
            suggested_tags=tags,
        ),
        "",
    )


# ---------- markdown table helpers -------------------------------


def _extract_all_tables(text: str) -> list[tuple[str, str, list[str]]]:
    """Return ``(header_row, separator_row, body_rows)`` for every
    GFM-style pipe table in ``text``. Empty list when no tables.

    Heuristic: a table starts at a line beginning with ``|`` and is
    immediately followed by a separator row matching
    ``|\\s*-+\\s*|...``. The table continues until the first blank
    line or non-pipe line. We then resume scanning after that table so
    multiple per-test-case vertical tables (Salesforce Structured) all
    get picked up.
    """
    lines = text.splitlines()
    n = len(lines)
    tables: list[tuple[str, str, list[str]]] = []
    i = 0
    while i < n - 1:
        line = lines[i].strip()
        next_line = lines[i + 1].strip()
        if (
            line.startswith("|")
            and next_line.startswith("|")
            and re.fullmatch(r"\|[\s\-:|]+\|", next_line)
        ):
            header = line
            sep = next_line
            j = i + 2
            body: list[str] = []
            while j < n:
                row = lines[j].strip()
                if not row.startswith("|"):
                    break
                if not row.strip("|").strip():
                    break
                body.append(row)
                j += 1
            tables.append((header, sep, body))
            i = j
            continue
        i += 1
    return tables


def _split_row(row: str) -> list[str]:
    """Split a single GFM table row into its cells, dropping the
    leading/trailing empty cells produced by the outer ``|``. Preserves
    any escaped pipes (``\\|`` -> ``|``)."""
    # Temporary placeholder for escaped pipes -- swap, split, swap back.
    placeholder = "\x00"
    safe = row.replace(r"\|", placeholder)
    parts = safe.split("|")
    # Drop the empty cells at the boundaries.
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.replace(placeholder, "|") for p in parts]


# Step-prefix matcher. Real LLM output is messy:
#   * Sometimes one step per line ("1. ...\n2. ...")
#   * Sometimes all on one line ("1. ... 2. ... 3. ...")
#   * Sometimes with embedded <br> the prompt says NOT to use but
#     models do anyway.
# This regex matches a step number prefix at the start, after a
# newline, OR after whitespace mid-string -- covering all three cases.
_NUMBERED_RE = re.compile(r"(?:^|\n|\r|\s)(\d+)\s*[.)\-]\s+")
_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)


def _split_numbered(cell: str) -> list[str]:
    """Split a multi-step cell into individual steps.

    Both Salesforce Structured and Zephyr seeds emit numbered lists
    inside cells. We split on numbered prefixes (anywhere in the
    string) when present; otherwise fall back to newline splitting.
    Stray ``<br>`` tags (despite the prompt forbidding them) are
    normalised to newlines first.
    """
    text = _BR_RE.sub("\n", (cell or "")).strip()
    if not text:
        return []
    # Try numbered-prefix split first.
    matches = list(_NUMBERED_RE.finditer(text))
    if matches:
        parts: list[str] = []
        for idx, m in enumerate(matches):
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            chunk = text[start:end].strip(" \t\r\n")
            if chunk:
                parts.append(chunk)
        if parts:
            return parts
    # No numbers -> split on newlines.
    return [p.strip() for p in text.split("\n") if p and p.strip()]


def _collect_tags(row: dict[str, str], extras: dict[str, str]) -> list[str]:
    """Compose suggested_tags from priority / components / labels /
    test_type. Empty pieces are dropped, duplicates de-duped case-
    insensitively. Format: ``priority:<value>`` etc.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _push(value: str) -> None:
        key = value.strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(value.strip())

    priority = row.get("priority", "").strip()
    if priority:
        _push(f"priority:{priority.lower()}")

    for raw in (
        row.get("labels", ""),
        row.get("components", ""),
        row.get("test_type", ""),
    ):
        if not raw:
            continue
        for piece in re.split(r"[,;|]+", raw):
            piece = piece.strip()
            if piece:
                _push(piece)

    # Capture a few interesting extras as tags so the audit trail
    # reflects what the LLM emitted, without bloating the TC model.
    feasibility = row.get("automation_feasibility", "").strip()
    if feasibility:
        _push(f"automation:{feasibility.lower().split()[0]}")

    return out
