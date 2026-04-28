"""Single source of truth for LLM system prompts.

Four roles are supported, each tuned for one of the four LLM entry points
in the system. The assembler combines:
  - The Salesforce playbook (markdown sibling file).
  - A role-specific tail that pins the output format.
  - Optionally, the legacy `system_prompt.txt` for the Quick path so all
    the carefully-tuned guidance there keeps shipping with the new
    structured playbook on top.

Why one assembler, not four prompts: keeping every entry point in sync
matters more than tweaking each in isolation. A library change should
reach all callers via the playbook; only role-specific output rules
diverge.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

from ai_qa_portal.backend.config import REPO_ROOT
from ai_qa_portal.backend.services import keyword_catalog

logger = logging.getLogger("ai_qa_portal.prompts.assembler")

_PLAYBOOK_PATH = Path(__file__).resolve().parent / "salesforce_playbook.md"
_LEGACY_SYSTEM_PROMPT_PATH = REPO_ROOT / "system_prompt.txt"

Role = Literal["drafter", "builder", "quick", "stepwise", "healer"]


# --- Role tails ------------------------------------------------------------
#
# Each tail is appended after the playbook. They restate the playbook's
# output rules in role-specific terms so the LLM doesn't drift.

_TAIL_DRAFTER = """\
---

## Your task in this role: TEST CASE DRAFTER

You are not generating Robot code right now. You're drafting structured
**test case definitions** from a user story description. Each draft will
later be approved by a human, then a separate path turns it into Robot
Framework code (using the recipes above).

Output rules:
- Return ONLY a JSON array. No markdown, no preamble, no fences.
- Each element must have exactly these keys:
    title: string
    steps: array of strings (each step is a concrete action)
    expected_result: string
    preconditions: string or null
    suggested_tags: array, choose from ["Smoke","Regression","Sanity","E2E"] or empty
- Do not include any other keys.

Quality bar for steps:
- Each step describes one observable action ("Open the Sales app", "Set
  Lead Source to Web", "Click Save"). Don't lump multiple clicks into one.
- Mention specific picklist values, lookup names, and custom button
  labels when the user story implies them. The Robot author will use
  these to pick the right recipe (Set Lead Source to Web -> recipe #5).
- Do NOT write Robot syntax in the steps. Plain English only.
"""

_TAIL_BUILDER = """\
---

## Your task in this role: TEST CASE -> ROBOT SCRIPT BUILDER

You receive a single approved test case (title, preconditions, ordered
steps, expected result) and emit a complete Robot Framework `.robot` file.

Output rules:
- Return ONLY valid Robot Framework syntax. No markdown fences, no
  explanation. Start with `*** Settings ***`.
- Use the suite skeleton in the playbook above.
- Map every step to the matching recipe -- if a step says "Set Lead
  Source to Web", you emit `Open Dropdown` + `Select Dropdown Option`
  per recipe #5.
- Use catalog keyword names exactly. The catalog is appended in the
  user message as JSON.
- Resolve credentials from the suite variables only:
  `${globalSandboxTestUrl}`, `${sandboxUserNameInput}`,
  `${sandboxPasswordInput}`. NEVER embed real URLs or credentials.
- Add `Resource` lines for every PO module you call (`SalesPO`,
  `ContactPO`, etc.).

If a step is ambiguous (e.g. "fill the form"), pick a sensible default
and use the highest-level keyword available (`SalesPO.Create A New Lead`
reads from suite variables and handles required fields).
"""

_TAIL_QUICK = """\
---

## Your task in this role: QUICK GENERATE FROM PROMPT

The user provides a free-form prompt; emit a complete Robot Framework
`.robot` file that fulfills it.

Apply the recipes above for any Salesforce-specific operation. Use the
suite skeleton. Catalog keyword names are authoritative; full catalog
JSON appears in the user message.

If the user prompt is ambiguous about field values, prefer the random
helpers (`Open Dropdown And Select First Option`) over hard-coding
guesses -- the test still proves the form works.

If `---ROBOT---` separator was requested by the calling path, place it
on its own line before `*** Settings ***`. Otherwise, output starts at
`*** Settings ***`.
"""

_TAIL_STEPWISE = """\
---

## Your task in this role: STEPWISE PLANNER

You're not emitting a `.robot` file. You're emitting an ordered JSON
array where each element is a single keyword call the RF-MCP runtime
will execute and verify in a live browser, one step at a time.

Output rules:
- Return ONLY a JSON array. No markdown, no fences, no prose.
- Each element: `{"keyword": "<KeywordName>", "args": ["...","..."]}`.
- `keyword` is ONLY the name (e.g. `"GlobalKeywords.Login To Sandbox"`).
  Never include arguments inside the `keyword` string.
- `args` is a separate array of plain string values (or `${var}`
  references). Don't use named-args syntax like `field=value`.
- Prefer qualified names (`GlobalKeywords.Launch App`,
  `SalesPO.Create A New Lead`) so the MCP runtime resolves them
  unambiguously.

Workflow rules:
1. Always start with
   `GlobalKeywords.Login To Sandbox` with three args:
   `["${globalSandboxTestUrl}", "${sandboxUserNameInput}", "${sandboxPasswordInput}"]`.
2. Use the recipes for every Salesforce operation.
3. End with verification keywords when the prompt mentions success.

Example:
```
[
  {"keyword": "GlobalKeywords.Login To Sandbox",
   "args": ["${globalSandboxTestUrl}", "${sandboxUserNameInput}", "${sandboxPasswordInput}"]},
  {"keyword": "GlobalKeywords.Launch App", "args": ["Sales"]},
  {"keyword": "GlobalKeywords.Select App Tab", "args": ["Leads"]},
  {"keyword": "GlobalKeywords.Open New Dialog", "args": ["Lead"]},
  {"keyword": "SalesPO.Create A New Lead", "args": []},
  {"keyword": "SalesPO.Verify Lead Created Successfully", "args": []}
]
```
"""

_TAIL_HEALER = """\
---

## Your task in this role: SCRIPT HEALER

A previously generated Robot script ran and FAILED. You are given:
  - The original test case definition.
  - The current `.robot` script that just failed.
  - The failing keyword name + Robot's error message.
  - Optionally, a screenshot of the page at the moment of failure.

Diagnose the cause from the error message and screenshot, then emit a
**complete corrected `.robot` file**. Do not emit a diff -- emit the
whole file.

Common failure modes and fixes:
- "Element not visible" / "no such element" on a picklist -> swap raw
  `Click Element` for `Open Dropdown` + `Select Dropdown Option`
  (recipe #5).
- "Element not visible" on a lookup field -> use
  `Enter Into Search Field` (recipe #6).
- Login failure -> check the credential variables are referenced as
  `${globalSandboxTestUrl}` etc., not hard-coded.
- "App not found" -> the user's app name might be misspelled; pass a
  shorter distinctive substring to `Launch App` (it does fuzzy match).
- Required-field validation toast -> swap `Select Dialog Button    Save`
  for `Attempt Save And Auto-Heal Missing Fields`.

Output rules: same as BUILDER role. Raw `.robot`, no markdown.
"""

_TAILS: dict[Role, str] = {
    "drafter": _TAIL_DRAFTER,
    "builder": _TAIL_BUILDER,
    "quick": _TAIL_QUICK,
    "stepwise": _TAIL_STEPWISE,
    "healer": _TAIL_HEALER,
}


# --- Caching --------------------------------------------------------------


_playbook_cache: Optional[str] = None
_playbook_mtime: Optional[float] = None
_legacy_cache: Optional[str] = None
_legacy_mtime: Optional[float] = None


def _read_playbook() -> str:
    # pylint: disable-next=global-statement
    global _playbook_cache, _playbook_mtime

    try:
        mtime = _PLAYBOOK_PATH.stat().st_mtime
    except OSError as exc:
        logger.warning("assembler: cannot stat playbook %s: %s", _PLAYBOOK_PATH, exc)
        return ""

    if _playbook_cache is not None and _playbook_mtime == mtime:
        return _playbook_cache

    try:
        text = _PLAYBOOK_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("assembler: cannot read playbook %s: %s", _PLAYBOOK_PATH, exc)
        return _playbook_cache or ""

    _playbook_cache = text
    _playbook_mtime = mtime
    return text


def _read_legacy_system_prompt() -> str:
    """Optionally returns the legacy system_prompt.txt content. Used only
    for the Quick role so the carefully-tuned guidance there keeps
    shipping alongside the new structured playbook."""
    # pylint: disable-next=global-statement
    global _legacy_cache, _legacy_mtime

    if not _LEGACY_SYSTEM_PROMPT_PATH.is_file():
        return ""
    try:
        mtime = _LEGACY_SYSTEM_PROMPT_PATH.stat().st_mtime
    except OSError:
        return _legacy_cache or ""

    if _legacy_cache is not None and _legacy_mtime == mtime:
        return _legacy_cache

    try:
        _legacy_cache = _LEGACY_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        _legacy_mtime = mtime
    except OSError as exc:
        logger.warning("assembler: cannot read legacy system prompt: %s", exc)
        return _legacy_cache or ""
    return _legacy_cache


# --- Public API -----------------------------------------------------------


def build_system_prompt(role: Role, *, include_legacy_quick_guidance: bool = True) -> str:
    """Compose the system prompt for a given LLM entry point role.

    The playbook plus a role-specific tail is the baseline. For the
    "quick" role, we ALSO append the legacy `system_prompt.txt` content
    -- it has months of tuning around picklists, app-name fuzzy matching,
    Account record types, etc. that we don't want to lose. Other roles
    don't get the legacy prompt (it's shaped for prompt-driven generation,
    not test-case translation or stepwise planning).
    """
    if role not in _TAILS:
        raise ValueError(f"Unknown role: {role!r}. Valid: {sorted(_TAILS)}")

    parts: list[str] = [_read_playbook(), _TAILS[role].rstrip()]
    if role == "quick" and include_legacy_quick_guidance:
        legacy = _read_legacy_system_prompt()
        if legacy.strip():
            parts.append("---\n\n## Additional guidance (legacy system_prompt.txt)\n\n" + legacy.strip())

    return "\n\n".join(p for p in parts if p).strip() + "\n"


def build_user_prompt_with_catalog(
    body: str,
    *,
    include_full_catalog: bool = True,
    catalog_section_title: str = "Keyword Catalog",
) -> str:
    """Wrap a caller-provided user prompt with the keyword catalog.

    `include_full_catalog=False` returns just the keyword name list --
    cheaper for the drafter role where the LLM doesn't need full args/docs.
    """
    if include_full_catalog:
        catalog_text = keyword_catalog.compact_json()
    else:
        names = keyword_catalog.keyword_names()
        catalog_text = "\n".join(f"- {n}" for n in names)

    return (
        f"## {catalog_section_title}\n\n"
        f"{catalog_text}\n\n"
        f"## User request\n\n"
        f"{body.strip()}\n"
    )
