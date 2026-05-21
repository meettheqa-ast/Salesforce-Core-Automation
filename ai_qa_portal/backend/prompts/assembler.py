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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
  references).
- Named arguments use the EXACT shape `name=value` (e.g.
  `"status=Sales Lead"`). The arg name MUST match a parameter in the
  keyword's signature exactly. NEVER wrap the parameter name in
  `${...}` -- `"${status}=Sales Lead"` is wrong; `"status=Sales Lead"`
  is right.
- Prefer qualified names (`GlobalKeywords.Launch App`,
  `SalesPO.Create A New Lead`) so the MCP runtime resolves them
  unambiguously.

Workflow rules:
1. Always start with
   `GlobalKeywords.Login To Sandbox` with three args:
   `["${globalSandboxTestUrl}", "${sandboxUserNameInput}", "${sandboxPasswordInput}"]`.
2. Use the recipes for every Salesforce operation.
3. End with verification keywords when the prompt mentions success.

## CRITICAL: keyword side effects you MUST respect

The RF-MCP runtime executes ONE step at a time. After each step the
browser state changes -- a keyword that opens a modal leaves the modal
open; a PO `Create A New X` keyword saves the form, closes the modal,
AND redirects to the new record's detail page. Planning the wrong
follow-up step wastes minutes of browser time on a guaranteed failure.

**The following PO keywords are "self-saving":** they fill the form,
click Save, handle missing-required-field auto-heal, AND redirect to
the new record's detail page. After they return, the modal is GONE and
the success toast has already been consumed.

| Self-saving keyword | What it leaves you on |
|---|---|
| `SalesPO.Create A New Lead` | Lead detail page |
| `SalesPO.Create A New Account` | Account detail page |
| `SalesPO.Create A New Opportunity` | Opportunity detail page |
| `SalesPO.Create A New Contact` | Contact detail page |
| `ContactPO.Create A New Contact` | Contact detail page |
| `Create A New Campaign` | Campaign detail page |

**After a self-saving keyword, you MUST NOT plan:**
- `GlobalKeywords.Verify Redirection to Record Details Page` -- redirection
  already happened; this keyword waits 60 s for an event that already fired.
- `GlobalKeywords.Get Success Toast Message Related Record Creation ID` --
  the toast was consumed inside the PO keyword; this returns nothing.
- A second `Open Dropdown` / `Select Dropdown Option` for a field that
  was supposed to be set during the create -- the modal is gone.

**Do this instead:**
- For field assertions: `GlobalKeywords.Verify Field Value On Detail Page    <Label>    <Expected>`.
- For "verify the record was created": `SalesPO.Verify <Object> Created Successfully`.
- For related records (Contacts on the Account, etc.): use
  `GlobalKeywords.Open Related Record Dropdown    <RelatedListLabel>    New`
  to open a NEW related-record modal, THEN call the right
  `Create A New X` keyword.

## CRITICAL: pass field values as named args, not extra steps

When the prompt names specific values (Lead Source, Status, Address,
Account Name on a Contact, etc.), pass them as **named args** to the
`Create A New X` keyword. Do NOT plan separate `Open Dropdown` /
`Select Dropdown Option` steps after the create -- the modal is gone
by then.

Example: prompt says *"Create a Lead with Lead Source = Web, Status =
Sales Lead, address in California"*

CORRECT:
```
[
  {"keyword": "GlobalKeywords.Login To Sandbox",
   "args": ["${globalSandboxTestUrl}", "${sandboxUserNameInput}", "${sandboxPasswordInput}"]},
  {"keyword": "SalesPO.Open New Lead From Sales App", "args": []},
  {"keyword": "SalesPO.Create A New Lead",
   "args": ["source=Web", "status=Sales Lead", "address=18 King Street, San Francisco, California"]},
  {"keyword": "SalesPO.Verify Lead Created Successfully", "args": []}
]
```

WRONG (modal closes after Create, the next 3 steps time out):
```
[
  {"keyword": "GlobalKeywords.Login To Sandbox", "args": [...]},
  {"keyword": "SalesPO.Open New Lead From Sales App", "args": []},
  {"keyword": "SalesPO.Create A New Lead", "args": []},
  {"keyword": "GlobalKeywords.Open Dropdown", "args": ["Lead Source"]},
  {"keyword": "GlobalKeywords.Select Dropdown Option", "args": ["Lead Source", "Web"]},
  {"keyword": "GlobalKeywords.Verify Redirection to Record Details Page", "args": []}
]
```

## CRITICAL: multi-record flows (Account + Contact + Opportunity)

When the prompt says *"create an Account, then a Contact under that
Account, then an Opportunity linked to that Account"*, the right shape
is:

```
[
  {"keyword": "GlobalKeywords.Login To Sandbox",
   "args": ["${globalSandboxTestUrl}", "${sandboxUserNameInput}", "${sandboxPasswordInput}"]},
  {"keyword": "SalesPO.Open New Account From Sales App", "args": []},
  {"keyword": "SalesPO.Create A New Account", "args": ["account_name=${accountName}"]},
  {"keyword": "SalesPO.Verify Account Creation", "args": []},
  {"keyword": "SalesPO.Open New Contact From Sales App", "args": []},
  {"keyword": "SalesPO.Create A New Contact",
   "args": ["account_name=${accountName}"]},
  {"keyword": "SalesPO.Verify Contact Created Successfully", "args": []},
  {"keyword": "SalesPO.Open New Opportunity From Sales App", "args": []},
  {"keyword": "SalesPO.Create A New Opportunity",
   "args": ["account_name=${accountName}"]},
  {"keyword": "SalesPO.Verify Opportunity", "args": []}
]
```

Notes:
- `${accountName}` is a Faker default declared in `SalesData.robot`,
  unique per suite import. Reusing the same variable across the three
  Create steps is what links Contact / Opportunity back to the Account.
- **Verification keywords are the PO `Verify <Object> Created Successfully`
  family.** Do NOT use `GlobalKeywords.Verify Field Value On Detail Page
  Account Name ${accountName}` -- the record's primary name is the page
  title, not a record-field cell, and that keyword will fail with
  "Could not locate ... output cell" after a 10 s timeout.
- The PO openers (`Open New Contact From Sales App`,
  `Open New Opportunity From Sales App`) navigate from ANY starting page
  -- you do NOT need to insert `Launch App` / `Select App Tab` between
  them, even if the previous step left you on a different record's
  detail page.
- Do NOT plan `Verify Redirection to Record Details Page` after each
  Create -- the redirect already happened inside the PO Create keyword.

## What you'll see on retry

The validator runs against your plan BEFORE the runtime executes
anything. If it rejects your plan, you'll get a fix-prompt with:
- The exact keyword name we don't recognise + 3 closest matches.
- Required-arg violations + the keyword's signature.
- Sequence-linter findings with one-line fix hints.

Pick a suggestion, fix the order, resubmit the FULL array. Don't push
back, don't comment, don't return a diff.
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


_playbook_cache: str | None = None
_playbook_mtime: float | None = None
_legacy_cache: str | None = None
_legacy_mtime: float | None = None


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


# --- Role <-> registry category mapping -----------------------------------
#
# When the prompt registry is enabled we resolve via the SQL-backed
# template tree (system seeds + sparse user/project/org overrides). Each
# legacy ``Role`` maps to one registry category. The category names are
# stable across the project (frontend Settings list, audit logs, etc.).
_ROLE_TO_CATEGORY: dict[Role, str] = {
    "drafter": "test_case_drafter",
    "builder": "script_builder",
    "quick": "quick_robot",
    "stepwise": "stepwise_planner",
    "healer": "healer",
}


def _registry_enabled() -> bool:
    """Feature flag. ``PROMPT_REGISTRY_ENABLED`` is OFF by default until
    Phase 2 wires call sites + audit; flip via env to opt in early."""
    import os  # local import keeps assembler import-cheap
    return os.environ.get("PROMPT_REGISTRY_ENABLED", "").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AssembledPrompt:
    """Returned by ``build_system_prompt_resolved`` for callers that
    want provenance (Phase 2 stamps these onto every TestCase + audit
    row). The legacy ``build_system_prompt`` returns the same text but
    discards the metadata so existing call sites don't need to change."""

    text: str
    template_id: str | None
    version_id: str | None
    category: str | None
    output_format: str | None
    source_scope: str | None


def _compose_with_playbook(category: str, body: str, output_format: str) -> str:
    """Prepend the Salesforce/Robot playbook for templates that produce
    Robot-shaped output (``json_array`` keyword plans, ``robot_script``
    files). Templates with ``markdown_table`` output (the Salesforce
    Structured + Enterprise Zephyr drafters) emit standalone manual /
    Zephyr-importable test cases -- the playbook is irrelevant noise
    for them, so we ship the body verbatim.

    Templates outside the Robot family entirely (future categories
    like ``defect_analysis``) also skip the playbook.
    """
    robot_categories = {
        "test_case_drafter", "script_builder", "quick_robot",
        "stepwise_planner", "healer", "recording_translator",
    }
    if category not in robot_categories:
        return body
    if (output_format or "").lower() == "markdown_table":
        # Standalone drafter -- ship the user-edited body unchanged.
        return body
    playbook = _read_playbook()
    if not playbook:
        return body
    return f"{playbook}\n\n{body.rstrip()}\n"


def build_system_prompt(role: Role, *, include_legacy_quick_guidance: bool = True) -> str:
    """Compose the system prompt for a given LLM entry point role.

    Legacy path (default): playbook + role-specific tail + (for ``quick``
    only) the legacy ``system_prompt.txt`` body.

    Registry path (when ``PROMPT_REGISTRY_ENABLED=true``): resolve via
    ``prompt_resolver`` to honour user / project / org overrides.
    Resolver failures (no system seed, DB unavailable) fall back to the
    legacy path so the generation pipeline never crashes on a registry
    issue.
    """
    if role not in _TAILS:
        raise ValueError(f"Unknown role: {role!r}. Valid: {sorted(_TAILS)}")

    if _registry_enabled():
        try:
            return build_system_prompt_resolved(role).text
        except Exception as exc:  # noqa: BLE001
            # Never let the registry block generation. We log + fall
            # through to the legacy assembler.
            logger.warning("registry resolve failed for role=%s: %s", role, exc)

    parts: list[str] = [_read_playbook(), _TAILS[role].rstrip()]
    if role == "quick" and include_legacy_quick_guidance:
        legacy = _read_legacy_system_prompt()
        if legacy.strip():
            parts.append("---\n\n## Additional guidance (legacy system_prompt.txt)\n\n" + legacy.strip())

    return "\n\n".join(p for p in parts if p).strip() + "\n"


def build_system_prompt_resolved(
    role: Role,
    *,
    user_id: str | None = None,
    project_id: str | None = None,
    org_id: str | None = None,
) -> AssembledPrompt:
    """Registry-aware variant that returns provenance + text. Phase 2
    call sites use this so they can stamp the resolved version id on
    persisted artefacts + audit rows.

    Always returns an ``AssembledPrompt``. Resolver miss falls back to
    the legacy text (template_id / version_id / category will be None
    in that case)."""
    from ai_qa_portal.backend.services.db import SessionLocal  # lazy
    from ai_qa_portal.backend.services import prompt_resolver  # lazy

    if role not in _TAILS:
        raise ValueError(f"Unknown role: {role!r}. Valid: {sorted(_TAILS)}")

    category = _ROLE_TO_CATEGORY.get(role)
    if category is None:
        text = build_system_prompt(role)
        return AssembledPrompt(text=text, template_id=None, version_id=None,
                               category=None, output_format=None, source_scope=None)

    db = SessionLocal()
    try:
        resolved = prompt_resolver.resolve(
            db, category=category,
            user_id=user_id, project_id=project_id, org_id=org_id,
        )
    finally:
        db.close()

    if resolved is None:
        # No system seed -- fall back to inline tails so nothing breaks.
        text = build_system_prompt(role)
        return AssembledPrompt(text=text, template_id=None, version_id=None,
                               category=category, output_format=None, source_scope=None)

    composed = _compose_with_playbook(
        resolved.category, resolved.body, resolved.output_format,
    )
    return AssembledPrompt(
        text=composed if composed.endswith("\n") else composed + "\n",
        template_id=resolved.template_id,
        version_id=resolved.version_id,
        category=resolved.category,
        output_format=resolved.output_format,
        source_scope=resolved.source_scope,
    )


def render_persona_context(default_app: str | None = None) -> str:
    """Format an optional 'Persona context' block for inclusion in a user
    prompt. Returns empty string when nothing relevant is set, so callers
    can unconditionally concatenate.

    Today's only field is `default_app`; the helper exists so the next
    persona-derived hint (role profile, default list view, etc.) lands in
    one place rather than five separate prompt builders.
    """
    app = (default_app or "").strip()
    if not app:
        return ""
    return (
        "## Persona context\n\n"
        f"Persona default app: {app}\n"
        f"   (Already injected as ${{salesAutomationAppName}} at runtime. "
        "Prefer PO keywords like `Open New Lead From Sales App` without "
        "specifying app_name; they will pick this up automatically. Only "
        "pass an explicit app name if the test deliberately needs a "
        "different one.)\n"
    )


def build_user_prompt_with_catalog(
    body: str,
    *,
    include_full_catalog: bool = True,
    catalog_section_title: str = "Keyword Catalog",
    default_app: str | None = None,
    rag_context: str = "",
) -> str:
    """Wrap a caller-provided user prompt with the keyword catalog.

    `include_full_catalog=False` returns just the keyword name list --
    cheaper for the drafter role where the LLM doesn't need full args/docs.

    `default_app`, when set, prepends a one-section "Persona context"
    block so the LLM frames generated steps around the right Salesforce
    app for this user license.

    `rag_context`, when non-empty, prepends a "Project context" block --
    produced by ``services.rag_retrieval.format_passages_block`` -- so
    the LLM sees the most relevant Jira issues, comments, uploaded docs,
    and test-data rows for this generation.
    """
    if include_full_catalog:
        catalog_text = keyword_catalog.compact_json()
    else:
        names = keyword_catalog.keyword_names()
        catalog_text = "\n".join(f"- {n}" for n in names)

    persona_block = render_persona_context(default_app)
    context_block = rag_context.strip()
    context_section = f"{context_block}\n\n" if context_block else ""

    return (
        f"{context_section}"
        f"{persona_block}"
        f"## {catalog_section_title}\n\n"
        f"{catalog_text}\n\n"
        f"## User request\n\n"
        f"{body.strip()}\n"
    )
