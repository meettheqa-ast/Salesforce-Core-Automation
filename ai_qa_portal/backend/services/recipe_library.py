"""Deterministic Robot Framework recipes -- the first tier of the
generation pipeline.

A **Recipe** is a parameterized Robot Framework script template plus the
metadata needed to (a) match a free-text user prompt to it via simple
rules, and (b) extract parameters from that prompt. When the matcher
returns a ``Recipe`` + ``params`` pair at high confidence, the recipe
renders directly without any LLM call.

This module hosts the registry. The matching/extraction logic lives in
``recipe_matcher.py`` to keep concerns separate (each recipe registers
its patterns + extractor here, but the matcher dispatches across all
recipes).

Pipeline shape::

    user_prompt
        -> recipe_matcher.match(prompt)            # this module's PATTERNS
        -> RecipeMatch(recipe, params, confidence)
        -> recipe_library.render(match)            # Jinja2
        -> validator (existing)
        -> done -- no LLM was called

If the matcher returns ``None`` or a low-confidence match, the caller
falls through to the LLM tier (local Ollama, then cloud).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

logger = logging.getLogger("ai_qa_portal.recipe_library")

_RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"
_TEMPLATES_DIR = _RECIPES_DIR / "templates"

# StrictUndefined turns ``{{ missing_var }}`` into a render-time error
# instead of silently emitting an empty string. That's the right default
# for code generation: we'd rather fall through to the LLM than ship a
# malformed .robot file. ``trim_blocks`` + ``lstrip_blocks`` mean
# ``{% if %}`` lines don't leave behind blank lines that Robot would
# parse as test separators.
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


@dataclass
class IntentSignal:
    """One pattern bundle inside a recipe's intent_patterns list. ALL
    ``required`` patterns must match (at least one occurrence each) and
    none of the ``forbidden`` patterns may match for the signal to fire.
    A recipe matches when ANY of its IntentSignals fire.

    Patterns are case-insensitive by default.
    """
    required: list[Pattern[str]] = field(default_factory=list)
    forbidden: list[Pattern[str]] = field(default_factory=list)
    confidence: str = "high"  # "high" | "medium" | "low"

    def fires(self, prompt: str) -> bool:
        for p in self.required:
            if not p.search(prompt):
                return False
        for p in self.forbidden:
            if p.search(prompt):
                return False
        return True


@dataclass
class Recipe:
    """A deterministic generation recipe.

    Attributes:
        name: stable identifier used in logs / API responses
            (e.g. ``"lead_routing_by_state"``).
        display_name: human-readable label rendered on the
            ``/generate`` page cards and on the post-generation banner
            (e.g. ``"Lead routing by state"``). When empty, the UI
            falls back to ``name`` so older code paths keep working.
        description: human-readable summary, surfaced in any UI.
        template: Jinja2 template filename inside ``recipes/templates/``.
        intent_signals: list of ``IntentSignal`` -- the recipe matches
            when ANY signal fires. Multiple signals let one recipe
            handle several phrasings.
        extractor: callable(prompt: str) -> dict[str, Any] | None.
            Returns the parameter dict for the template, or ``None``
            if extraction fails (in which case the matcher returns
            ``None`` for this recipe so the caller falls through to
            LLM).
        required_params: parameter keys the extractor MUST return. Used
            as a sanity check after ``extractor`` runs.
        sample_prompt: optional -- a worked example prompt that the
            UI can use to pre-fill the textarea when the user clicks
            "Use this recipe" in the recipes panel. Should produce a
            high-confidence match against this very recipe.
    """
    name: str
    description: str
    template: str
    intent_signals: list[IntentSignal]
    extractor: Callable[[str], dict[str, Any] | None]
    required_params: list[str] = field(default_factory=list)
    sample_prompt: str = ""
    display_name: str = ""

    def confidence_for(self, prompt: str) -> str | None:
        """Return the highest matching signal's confidence, or None."""
        order = {"high": 3, "medium": 2, "low": 1}
        best: str | None = None
        for sig in self.intent_signals:
            if sig.fires(prompt):
                if best is None or order[sig.confidence] > order[best]:
                    best = sig.confidence
        return best


_REGISTRY: dict[str, Recipe] = {}


def register(recipe: Recipe) -> None:
    """Add a recipe to the global registry. Last writer wins; the
    matcher iterates over the registry in insertion order."""
    if recipe.name in _REGISTRY:
        logger.info("recipe_library: replacing recipe %s", recipe.name)
    _REGISTRY[recipe.name] = recipe


def all_recipes() -> list[Recipe]:
    """Return registered recipes in insertion order."""
    return list(_REGISTRY.values())


def get(name: str) -> Recipe | None:
    return _REGISTRY.get(name)


def render(recipe: Recipe, params: dict[str, Any]) -> str:
    """Render a recipe's Jinja2 template with the given parameter dict.

    Raises ``jinja2.UndefinedError`` if a required template variable is
    missing (StrictUndefined). Callers should treat that as "fall
    through to LLM".
    """
    template = _jinja_env.get_template(recipe.template)
    rendered = template.render(**params)
    # Robot is whitespace-sensitive at the column level. Jinja's
    # trim_blocks helps but doesn't always leave a single trailing
    # newline; normalize that here.
    return rendered.rstrip() + "\n"


# ---------------------------------------------------------------------------
# Initial recipe definitions. Phase 2 ships three; Phase 4 expands.
# Keep extractors small and obvious -- if regex isn't enough, that's a
# signal the prompt is novel and should fall through to the LLM tier.
# ---------------------------------------------------------------------------


# State -> (full name, default address) lookup. Used by the routing
# extractor to translate prompt mentions like "CA", "California", or
# "CA, AZ, NV" into the parameter dict. Mirrors the playbook table so
# updates land in one place.
_STATE_TABLE: dict[str, tuple[str, str]] = {
    # West Coast -> Pool-NA-ISR-West
    "CA": ("California", "18 King Street, San Francisco, California"),
    "WA": ("Washington", "400 Broad Street, Seattle, Washington"),
    "OR": ("Oregon", "1 SW Columbia Street, Portland, Oregon"),
    "NV": ("Nevada", "1 N Las Vegas Boulevard, Las Vegas, Nevada"),
    "AZ": ("Arizona", "1 E Washington Street, Phoenix, Arizona"),
    # Central -> Pool-NA-ISR-Central
    "TX": ("Texas", "901 Main Street, Dallas, Texas"),
    "IL": ("Illinois", "233 S Wacker Drive, Chicago, Illinois"),
    "MN": ("Minnesota", "1 Main Street SE, Minneapolis, Minnesota"),
    "LA": ("Louisiana", "701 Poydras Street, New Orleans, Louisiana"),
    # East -> Pool-NA-ISR-East
    "NY": ("New York", "350 5th Avenue, New York, New York"),
    "FL": ("Florida", "701 Brickell Avenue, Miami, Florida"),
    "PA": ("Pennsylvania", "1 Logan Square, Philadelphia, Pennsylvania"),
    "ME": ("Maine", "1 Monument Square, Portland, Maine"),
}

_WEST_STATES = {"CA", "WA", "OR", "NV", "AZ"}
_CENTRAL_STATES = {"TX", "IL", "MN", "LA"}
_EAST_STATES = {"NY", "FL", "PA", "ME"}


def _queue_for(state_code: str) -> str:
    if state_code in _WEST_STATES:
        return "Pool-NA-ISR-West"
    if state_code in _CENTRAL_STATES:
        return "Pool-NA-ISR-Central"
    if state_code in _EAST_STATES:
        return "Pool-NA-ISR-East"
    return "Unassigned Lead Queue"


def _scenario_tag_for(state_code: str) -> str:
    queue = _queue_for(state_code)
    return queue.split("-")[-1] + "Route" if queue.startswith("Pool-NA-ISR-") else "UnassignedQueue"


_STATE_NAME_PATTERNS: dict[str, Pattern[str]] = {
    code: re.compile(
        r"\b" + re.escape(code) + r"\b|\b" + re.escape(name) + r"\b",
        re.IGNORECASE,
    )
    for code, (name, _addr) in _STATE_TABLE.items()
}


def _build_args_line(parts: dict[str, str]) -> str:
    """Render a Robot keyword-args line from {arg_name: value}.

    Drops empty values entirely so the call uses the keyword's default.
    Robot's cell separator is 4 spaces. Returns "" when no parts have
    a value (caller renders the keyword call without args).
    """
    rendered: list[str] = []
    for key, val in parts.items():
        if val:
            rendered.append(f"{key}={val}")
    return "    ".join(rendered)


def _extract_routing_params(prompt: str) -> dict[str, Any] | None:
    """Pull state list + expected status from a routing prompt.

    Returns None when no state is mentioned -- the matcher should not
    fire on prompts that don't actually name states.
    """
    states_hit: list[str] = []
    seen: set[str] = set()
    # Iterate dict in insertion order -> deterministic state ordering
    # (West first, then Central, then East) which matches the canonical
    # routing test layout.
    for code, pat in _STATE_NAME_PATTERNS.items():
        if pat.search(prompt) and code not in seen:
            states_hit.append(code)
            seen.add(code)

    # Also handle the explicit "Unassigned Lead Queue" / "non-Sales"
    # variant, which doesn't name a state but is a valid routing test.
    include_unassigned = bool(re.search(
        r"\bnon[-\s]?Sales\b|\bUnassigned\s+Lead\s+Queue\b|\bMarketing\s+(?:or\s+)?Service\b",
        prompt, re.IGNORECASE,
    ))

    if not states_hit and not include_unassigned:
        return None

    tests = []
    for code in states_hit:
        full_name, address = _STATE_TABLE[code]
        expected_owner = _queue_for(code)
        scenario_tag = _scenario_tag_for(code)
        # Robot test names allow spaces; keep the full state name
        # (e.g. "New York") so the title reads naturally in reports.
        title = f"Verify Sales Lead From {full_name} Routes To {expected_owner.replace('-', ' ')}"
        create_args = _build_args_line({
            "status": "Sales Lead",
            "address": address,
        })
        tests.append({
            "title": title,
            "documentation": (
                f"Lead with State={full_name} + Status=Sales Lead must route to {expected_owner}."
            ),
            "tags": ["critical", "lead", "routing"],
            "scenario_tag": scenario_tag,
            "create_lead_args": create_args,
            "expected_owner": expected_owner,
        })

    if include_unassigned:
        # Non-Sales-Lead status -> default Unassigned Lead Queue. Address
        # is a generic CA fallback (the rule fires on Status, not State,
        # for non-Sales-Lead leads -- but we still need a real address
        # for the form to save).
        tests.append({
            "title": "Verify Non Sales Lead Routes To Unassigned Lead Queue",
            "documentation": (
                "Lead saved with a non-Sales-Lead Status must route to the "
                "default Unassigned Lead Queue regardless of State."
            ),
            "tags": ["critical", "lead", "routing"],
            "scenario_tag": "UnassignedQueue",
            # NO status arg -> the modal's default Status (often
            # "Working - Contacted" or "Open - Not Contacted") fires
            # the non-Sales-Lead branch of the routing rule.
            "create_lead_args": _build_args_line({
                "address": "18 King Street, San Francisco, California",
            }),
            "expected_owner": "Unassigned Lead Queue",
        })

    return {
        "suite_doc": "Lead routing rule verification by State -- one test per state.",
        "tests": tests,
    }


def _extract_create_lead_params(prompt: str) -> dict[str, Any] | None:
    """Pull explicit fields out of a 'create a lead' prompt. Anything
    not mentioned is left blank so the SalesData.robot Faker default
    fills in. Returns the param dict (always succeeds for prompts
    that reach this extractor)."""
    p = prompt
    fields: dict[str, str] = {
        "first_name": "",
        "last_name": "",
        "company": "",
        "status": "",
        "source": "",
        "address": "",
    }
    m = re.search(r"first[\s-]*name\s*[:=]?\s*([A-Z][A-Za-z'-]+)", p, re.IGNORECASE)
    if m:
        fields["first_name"] = m.group(1)
    m = re.search(r"last[\s-]*name\s*[:=]?\s*([A-Z][A-Za-z'-]+)", p, re.IGNORECASE)
    if m:
        fields["last_name"] = m.group(1)
    m = re.search(r"\bcompany\s*[:=]?\s*([A-Z][\w& -]{1,40}?)(?:[.,]|$|\s+(?:and|with|in|on))", p, re.IGNORECASE)
    if m:
        fields["company"] = m.group(1).strip()
    m = re.search(r"status\s*[:=]?\s*([A-Z][\w \-]{2,40}?)(?:[.,]|$|\s+(?:and|with|on))", p, re.IGNORECASE)
    if m:
        fields["status"] = m.group(1).strip()
    m = re.search(r"(?:lead\s+)?source\s*[:=]?\s*([A-Z][\w \-]{2,40}?)(?:[.,]|$|\s+(?:and|with|on))", p, re.IGNORECASE)
    if m:
        fields["source"] = m.group(1).strip()
    m = re.search(r"address\s*[:=]?\s*\"?([^\"\n]+?)\"?(?:\.|$|\n)", p, re.IGNORECASE)
    if m:
        fields["address"] = m.group(1).strip()
    elif not fields["address"]:
        for code, pat in _STATE_NAME_PATTERNS.items():
            if pat.search(p):
                fields["address"] = _STATE_TABLE[code][1]
                break

    args = _build_args_line(fields)
    create_call = "SalesPO.Create A New Lead"
    if args:
        create_call = f"SalesPO.Create A New Lead    {args}"
    return {
        "test_title": "Create A New Lead",
        "documentation": "Create a Lead and verify it was saved.",
        "tags": ["lead", "create", "smoke"],
        "create_lead_call": create_call,
    }


def _extract_create_campaign_params(prompt: str) -> dict[str, Any] | None:
    """Pull explicit fields out of a 'create a campaign' prompt."""
    p = prompt
    fields: dict[str, str] = {
        "name": "",
        "type": "",
        "status": "",
    }
    m = re.search(
        r"(?:campaign\s+)?name\s*[:=]?\s*\"?([A-Z][\w '\-]{2,60}?)\"?(?:[.,]|$|\s+(?:and|with|in|on|of))",
        p, re.IGNORECASE,
    )
    if m:
        fields["name"] = m.group(1).strip()
    m = re.search(
        r"(?:campaign\s+)?type\s*[:=]?\s*\"?([A-Z][\w \-]{2,40}?)\"?(?:[.,]|$|\s+(?:and|with|on))",
        p, re.IGNORECASE,
    )
    if m:
        fields["type"] = m.group(1).strip()
    m = re.search(
        r"(?:campaign\s+)?status\s*[:=]?\s*\"?([A-Z][\w \-]{2,40}?)\"?(?:[.,]|$|\s+(?:and|with|on))",
        p, re.IGNORECASE,
    )
    if m:
        fields["status"] = m.group(1).strip()
    args = _build_args_line(fields)
    create_call = "CampaignPO.Create A New Campaign"
    if args:
        create_call = f"CampaignPO.Create A New Campaign    {args}"
    return {
        "test_title": "Create A New Campaign",
        "documentation": "Create a Campaign and verify it was saved.",
        "tags": ["campaign", "create", "smoke"],
        "create_campaign_call": create_call,
    }


# Match verb-conjugations of "route" (route/routes/routed/routing) and
# "queue" / "owner" (singular + plural). Used by both the routing
# recipe's signals AND the create_lead recipe's forbidden-list, so we
# define it once and reference it everywhere.
_ROUTING_VERB_RE = re.compile(
    r"\b(routes?|routed|routing|queues?|owner|owned)\b",
    re.IGNORECASE,
)
_LEAD_NOUN_RE = re.compile(r"\b(lead|leads)\b", re.IGNORECASE)
_STATE_OR_QUEUE_RE = re.compile(
    r"\b(CA|California|TX|Texas|NY|New York|WA|Washington|OR|Oregon|"
    r"NV|Nevada|AZ|Arizona|IL|Illinois|MN|Minnesota|LA|Louisiana|"
    r"FL|Florida|PA|Pennsylvania|ME|Maine|Pool-NA|ISR|"
    r"Unassigned\s+Lead\s+Queue)\b",
    re.IGNORECASE,
)
_NON_SALES_RE = re.compile(
    r"\bnon[-\s]?Sales\b|\bUnassigned\s+Lead\s+Queue\b|"
    r"\bMarketing\s+(?:or\s+)?Service\b",
    re.IGNORECASE,
)

# Register the initial recipes. Order matters for ties: more-specific
# patterns (routing) MUST register before broader ones (create_lead) so
# the matcher prefers them.
register(Recipe(
    name="lead_routing_by_state",
    display_name="Lead routing by state",
    description=(
        "Verify Salesforce Lead routing rules: one test per State that "
        "asserts the saved Lead's Owner is the expected queue (Pool-NA-ISR-"
        "West/Central/East or Unassigned Lead Queue)."
    ),
    template="lead_routing_by_state.robot.j2",
    intent_signals=[
        # Signal A: explicit "Lead routing/owner" + a state or queue
        # name. Highest specificity, fires on the canonical PM phrasing.
        IntentSignal(
            required=[
                _ROUTING_VERB_RE,
                _LEAD_NOUN_RE,
                _STATE_OR_QUEUE_RE,
            ],
            confidence="high",
        ),
        # Signal B: routing language + state-or-queue WITHOUT the
        # "lead" noun. Catches terse phrasings like "CA + TX + NY
        # routing -> regional queues" where the lead-ness is implicit.
        IntentSignal(
            required=[
                _ROUTING_VERB_RE,
                _STATE_OR_QUEUE_RE,
            ],
            confidence="high",
        ),
        # Signal C: the explicit "non-Sales -> Unassigned Lead Queue"
        # variant, which doesn't need a state. Caught here so the
        # extractor can build the unassigned-only test list.
        IntentSignal(
            required=[
                _NON_SALES_RE,
                _LEAD_NOUN_RE,
            ],
            confidence="high",
        ),
        # Signal D: a Pentair queue name (Pool-NA-* / ISR) appears
        # with "lead/leads" but WITHOUT an explicit routing verb.
        # Catches phrasings like "West Coast (CA) sales leads to
        # Pool-NA-West" where the "to" preposition implies routing.
        # Pool-NA / ISR / Unassigned Lead Queue are unambiguous queue
        # names in this domain, so seeing one alongside "lead" is a
        # strong routing-test signal.
        IntentSignal(
            required=[
                re.compile(
                    r"\b(Pool-NA|ISR|Unassigned\s+Lead\s+Queue)\b",
                    re.IGNORECASE,
                ),
                _LEAD_NOUN_RE,
            ],
            confidence="high",
        ),
    ],
    extractor=_extract_routing_params,
    required_params=["tests"],
    sample_prompt=(
        "Verify Salesforce Lead routing rules: West Coast (CA) sales "
        "leads should route to Pool-NA-ISR-West, Central (TX) to "
        "Pool-NA-ISR-Central, East Coast (NY) to Pool-NA-ISR-East, "
        "and non-Sales leads to the Unassigned Lead Queue."
    ),
))


# Forbidden patterns for create_lead: anything that smells like routing
# (so the routing recipe wins), campaign, or conversion. The
# _ROUTING_VERB_RE handles route/routes/routed/routing/queue/owner.
_CREATE_LEAD_FORBIDDEN = [
    _ROUTING_VERB_RE,
    re.compile(r"\bcampaign\b", re.IGNORECASE),
    re.compile(r"\bconvert\b", re.IGNORECASE),
]

register(Recipe(
    name="create_lead_basic",
    display_name="Create a Lead",
    description="Create a single Lead with optional named fields and verify it was saved.",
    template="create_lead_basic.robot.j2",
    intent_signals=[
        IntentSignal(
            required=[
                re.compile(r"\b(create|make|add)\b.*\blead\b", re.IGNORECASE | re.DOTALL),
            ],
            forbidden=_CREATE_LEAD_FORBIDDEN,
            confidence="high",
        ),
        IntentSignal(
            required=[
                re.compile(r"\bnew\s+lead\b", re.IGNORECASE),
            ],
            forbidden=_CREATE_LEAD_FORBIDDEN,
            confidence="medium",
        ),
    ],
    extractor=_extract_create_lead_params,
    required_params=[],
    sample_prompt="Create a new Lead with First Name John, Last Name Doe, Lead Source Web.",
))


_CREATE_CAMPAIGN_FORBIDDEN = [
    # CampaignMember and lead-linking flows are handled by their own
    # recipes (registered before this one). This list is the LAST line
    # of defense: we use a permissive ".*" match for "lead" so a prompt
    # like "Create a Campaign and add Leads to it" routes to
    # campaign_member_add even if the registration order changes.
    re.compile(r"\b(member|convert)\b", re.IGNORECASE),
    re.compile(r"\bcampaign\b.*\blead", re.IGNORECASE | re.DOTALL),
    re.compile(r"\blead.*\bcampaign\b", re.IGNORECASE | re.DOTALL),
]

register(Recipe(
    name="create_campaign_basic",
    display_name="Create a Campaign",
    description="Create a single Campaign with optional named fields and verify it was saved.",
    template="create_campaign_basic.robot.j2",
    intent_signals=[
        IntentSignal(
            required=[
                re.compile(r"\b(create|make|add)\b.*\bcampaign\b", re.IGNORECASE | re.DOTALL),
            ],
            forbidden=_CREATE_CAMPAIGN_FORBIDDEN,
            confidence="high",
        ),
        IntentSignal(
            required=[
                re.compile(r"\bnew\s+campaign\b", re.IGNORECASE),
            ],
            forbidden=_CREATE_CAMPAIGN_FORBIDDEN,
            confidence="medium",
        ),
    ],
    extractor=_extract_create_campaign_params,
    required_params=[],
    sample_prompt="Create a new Campaign with name Q1 Outreach, type Webinar, status Planned.",
))


# ---------------------------------------------------------------------------
# Phase 4 expansion recipes -- account create, campaign+lead linkage,
# lead conversion. Same pattern as the Phase 2 recipes.
# ---------------------------------------------------------------------------


def _extract_create_account_params(prompt: str) -> dict[str, Any] | None:
    """Pull explicit fields out of a 'create an account' prompt."""
    p = prompt
    fields: dict[str, str] = {
        "account_name": "",
        "phone": "",
        "website": "",
    }
    m = re.search(
        r"(?:account\s+)?name\s*[:=]?\s*\"?([A-Z][\w '\-&,.]{2,60}?)\"?(?:[.,]|$|\s+(?:and|with|in|on))",
        p, re.IGNORECASE,
    )
    if m:
        fields["account_name"] = m.group(1).strip()
    m = re.search(r"phone\s*[:=]?\s*\"?([+\d \-()]{7,25})\"?", p, re.IGNORECASE)
    if m:
        fields["phone"] = m.group(1).strip()
    m = re.search(r"website\s*[:=]?\s*\"?([\w.\-]+\.[a-z]{2,8})\"?", p, re.IGNORECASE)
    if m:
        fields["website"] = m.group(1).strip()
    args = _build_args_line(fields)
    create_call = "SalesPO.Create A New Account"
    if args:
        create_call = f"SalesPO.Create A New Account    {args}"
    return {
        "test_title": "Create A New Account",
        "documentation": "Create an Account and verify it on the detail page.",
        "tags": ["account", "create", "smoke"],
        "create_account_call": create_call,
    }


def _extract_campaign_member_add_params(prompt: str) -> dict[str, Any] | None:
    """Pull params for a 'seed Campaign + link Lead' flow. The prompt
    may name the Lead's company / last name; defaults fall through to
    SalesData.robot Faker variables, which the test references."""
    return {
        "test_title": "Add Lead To Campaign Via API",
        "documentation": (
            "Seed a Campaign via REST, then link a new Lead to it as a "
            "CampaignMember. Both records are torn down on teardown."
        ),
        "tags": ["campaign", "lead", "api", "smoke"],
    }


def _extract_create_opportunity_params(prompt: str) -> dict[str, Any] | None:
    """Pull explicit fields out of a 'create an opportunity' prompt.

    Salesforce's standard ``Stage`` picklist values vary by org but
    ``Closed Won``, ``Closed Lost``, ``Proposal``, ``Negotiation``, and
    ``Qualification`` are out-of-the-box on every implementation. We
    extract any of those that appear in the prompt verbatim and let the
    template wire the value through ``Set Suite Variable`` before the
    create call so ``SalesPO.Create A New Opportunity`` picks it up via
    the existing ``${opportunityStageOption}`` suite variable -- no new
    Robot keyword required.
    """
    p = prompt
    fields: dict[str, str] = {
        "opp_name": "",
        "amount": "",
        "account_name": "",
    }
    m = re.search(
        r"(?:opportunity\s+)?name\s*[:=]?\s*\"?([A-Z][\w '\-&.,]{2,60}?)\"?(?:[.,]|$|\s+(?:and|with|in|on|of))",
        p, re.IGNORECASE,
    )
    if m:
        fields["opp_name"] = m.group(1).strip()
    m = re.search(r"amount\s*[:=]?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", p, re.IGNORECASE)
    if m:
        fields["amount"] = m.group(1).strip()
    m = re.search(
        r"account(?:\s+name)?\s*[:=]?\s*\"?([A-Z][\w '\-&.,]{2,60}?)\"?(?:[.,]|$|\s+(?:and|with|on))",
        p, re.IGNORECASE,
    )
    if m:
        fields["account_name"] = m.group(1).strip()

    # Stage extraction. Match the canonical out-of-the-box stage labels
    # (case-insensitive); we re-emit the title-cased form so the value
    # matches what Salesforce shows on the detail page exactly.
    stage = ""
    stage_match = re.search(
        r"stage\s*[:=]?\s*\"?(closed\s+won|closed\s+lost|prospecting|qualification|"
        r"needs\s+analysis|value\s+proposition|id\.\s*decision\s+makers|"
        r"perception\s+analysis|proposal(?:/price\s+quote)?|negotiation(?:/review)?)\"?",
        p, re.IGNORECASE,
    )
    if stage_match:
        # Normalise whitespace + title-case (preserving the / separator
        # used by SF's native stages like "Proposal/Price Quote").
        raw = re.sub(r"\s+", " ", stage_match.group(1).strip())
        stage = " ".join(w.capitalize() for w in raw.split(" "))

    args = _build_args_line(fields)
    create_call = "SalesPO.Create A New Opportunity"
    if args:
        create_call = f"SalesPO.Create A New Opportunity    {args}"

    tags = ["opportunity", "create", "smoke"]
    title = "Create A New Opportunity"
    if stage:
        title = f"Create A New Opportunity At Stage {stage.replace(' ', '')}"

    return {
        "test_title": title,
        "documentation": (
            f"Create an Opportunity at Stage {stage} and verify on the detail page."
            if stage else
            "Create an Opportunity and verify on the detail page."
        ),
        "tags": tags,
        "create_opportunity_call": create_call,
        "stage": stage,
    }


def _extract_create_contact_params(prompt: str) -> dict[str, Any] | None:
    """Pull explicit fields out of a 'create a contact' prompt. ``Last
    Name`` is the only Salesforce-required field on Contact, so the
    template can run with zero extracted fields and still produce a
    saving test (``SalesData.robot`` Faker defaults fill the rest)."""
    p = prompt
    fields: dict[str, str] = {
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "title": "",
        "account_name": "",
    }
    m = re.search(r"first[\s-]*name\s*[:=]?\s*([A-Z][A-Za-z'-]+)", p, re.IGNORECASE)
    if m:
        fields["first_name"] = m.group(1)
    m = re.search(r"last[\s-]*name\s*[:=]?\s*([A-Z][A-Za-z'-]+)", p, re.IGNORECASE)
    if m:
        fields["last_name"] = m.group(1)
    m = re.search(r"email\s*[:=]?\s*([\w.+\-]+@[\w.\-]+\.[a-z]{2,8})", p, re.IGNORECASE)
    if m:
        fields["email"] = m.group(1).strip()
    m = re.search(r"phone\s*[:=]?\s*\"?([+\d \-()]{7,25})\"?", p, re.IGNORECASE)
    if m:
        fields["phone"] = m.group(1).strip()
    m = re.search(
        r"title\s*[:=]?\s*\"?([A-Z][\w &/\-]{2,40}?)\"?(?:[.,]|$|\s+(?:and|with|on))",
        p, re.IGNORECASE,
    )
    if m:
        fields["title"] = m.group(1).strip()
    m = re.search(
        r"account(?:\s+name)?\s*[:=]?\s*\"?([A-Z][\w '\-&.,]{2,60}?)\"?(?:[.,]|$|\s+(?:and|with|on))",
        p, re.IGNORECASE,
    )
    if m:
        fields["account_name"] = m.group(1).strip()

    args = _build_args_line(fields)
    create_call = "SalesPO.Create A New Contact"
    if args:
        create_call = f"SalesPO.Create A New Contact    {args}"
    return {
        "test_title": "Create A New Contact",
        "documentation": "Create a Contact and verify First Name, Last Name, Title, and Email on the detail page.",
        "tags": ["contact", "create", "smoke"],
        "create_contact_call": create_call,
    }


def _extract_lead_convert_params(prompt: str) -> dict[str, Any] | None:
    """Reuse the create-lead extractor to harvest any explicit lead
    fields from the prompt, then wrap them in the convert flow."""
    base = _extract_create_lead_params(prompt) or {}
    return {
        "test_title": "Convert A Lead Into An Opportunity",
        "documentation": (
            "Create a Lead, then convert it -- Salesforce auto-creates "
            "the Account, Contact, and Opportunity from the Lead's data."
        ),
        "tags": ["lead", "convert", "smoke"],
        "create_lead_call": base.get("create_lead_call", "SalesPO.Create A New Lead"),
    }


register(Recipe(
    name="create_account_basic",
    display_name="Create an Account",
    description="Create a single Account with optional named fields and verify it on the detail page.",
    template="create_account_basic.robot.j2",
    intent_signals=[
        IntentSignal(
            required=[
                re.compile(r"\b(create|make|add)\b.*\baccount\b", re.IGNORECASE | re.DOTALL),
            ],
            forbidden=[
                re.compile(r"\b(lead|contact|opportunity|campaign|convert)\b", re.IGNORECASE),
            ],
            confidence="high",
        ),
        IntentSignal(
            required=[
                re.compile(r"\bnew\s+account\b", re.IGNORECASE),
            ],
            forbidden=[
                re.compile(r"\b(lead|contact|opportunity|campaign|convert)\b", re.IGNORECASE),
            ],
            confidence="medium",
        ),
    ],
    extractor=_extract_create_account_params,
    required_params=[],
    sample_prompt="Create a new Account with name Acme Corporation.",
))


register(Recipe(
    name="campaign_member_add",
    display_name="Add Lead to Campaign",
    description=(
        "Seed a Campaign via REST and link a Lead to it as a "
        "CampaignMember. End-to-end test of the Campaign + Lead "
        "junction with API-driven setup and teardown."
    ),
    template="campaign_member_add.robot.j2",
    intent_signals=[
        # Specific phrasing: "add a Lead to a Campaign" / "link a Lead
        # to a Campaign as CampaignMember". Higher specificity than
        # the generic create-campaign recipe.
        IntentSignal(
            required=[
                re.compile(r"\b(add|link|associate)\b.*\b(lead|leads)\b", re.IGNORECASE | re.DOTALL),
                re.compile(r"\bcampaign\b", re.IGNORECASE),
            ],
            forbidden=[
                re.compile(r"\bconvert\b", re.IGNORECASE),
            ],
            confidence="high",
        ),
        IntentSignal(
            required=[
                re.compile(r"\bcampaign\s*member\b", re.IGNORECASE),
            ],
            forbidden=[
                re.compile(r"\bconvert\b", re.IGNORECASE),
            ],
            confidence="high",
        ),
    ],
    extractor=_extract_campaign_member_add_params,
    required_params=[],
    sample_prompt="Add a new Lead to a Campaign as a CampaignMember via API.",
))


register(Recipe(
    name="create_opportunity_basic",
    display_name="Create an Opportunity",
    description=(
        "Create a single Opportunity (optionally at a given Stage like "
        "Closed Won) and verify it on the detail page."
    ),
    template="create_opportunity_basic.robot.j2",
    intent_signals=[
        # High: explicit verb + opportunity. Forbid "lead" so prompts
        # like "Create a Lead and convert it to an Opportunity" route to
        # ``lead_convert`` instead. Forbid "convert" too in case the
        # user writes "convert into Opportunity" without "lead".
        IntentSignal(
            required=[
                re.compile(r"\b(create|make|add)\b.*\bopportunit(y|ies)\b", re.IGNORECASE | re.DOTALL),
            ],
            forbidden=[
                re.compile(r"\b(lead|leads|convert(?:s|ed|ing)?|conversion)\b", re.IGNORECASE),
            ],
            confidence="high",
        ),
        # Medium: "new opportunity" without an explicit verb.
        IntentSignal(
            required=[
                re.compile(r"\bnew\s+opportunit(y|ies)\b", re.IGNORECASE),
            ],
            forbidden=[
                re.compile(r"\b(lead|leads|convert(?:s|ed|ing)?|conversion)\b", re.IGNORECASE),
            ],
            confidence="medium",
        ),
        # High: "Update / set Opportunity Stage to <X>" style prompts.
        # We deliberately route these to the create-with-stage recipe
        # because there is no standalone keyword for editing an
        # existing Opportunity's Stage on the detail page -- the
        # deterministic path is "create AT the desired stage and verify".
        IntentSignal(
            required=[
                re.compile(r"\bopportunit(y|ies)\b", re.IGNORECASE),
                re.compile(
                    r"\bstage\s*(?:[:=]|to|=|is)\s*(?:closed\s+won|closed\s+lost|"
                    r"prospecting|qualification|negotiation|proposal)\b",
                    re.IGNORECASE,
                ),
            ],
            forbidden=[
                re.compile(r"\b(lead|leads|convert(?:s|ed|ing)?|conversion)\b", re.IGNORECASE),
            ],
            confidence="high",
        ),
    ],
    extractor=_extract_create_opportunity_params,
    required_params=[],
    sample_prompt="Create a new Opportunity with name Acme Q2 Deal and Stage Closed Won, then verify it.",
))


register(Recipe(
    name="create_contact_basic",
    display_name="Create a Contact",
    description=(
        "Create a single Contact with optional named fields and verify "
        "First Name, Last Name, Title, and Email on the detail page."
    ),
    template="create_contact_basic.robot.j2",
    intent_signals=[
        IntentSignal(
            required=[
                re.compile(r"\b(create|make|add|verify)\b.*\bcontact\b", re.IGNORECASE | re.DOTALL),
            ],
            forbidden=[
                # CampaignMember + lead-conversion flows reference
                # contacts but should route to their own recipes.
                re.compile(r"\b(lead|leads|campaign|convert(?:s|ed|ing)?|conversion|member)\b", re.IGNORECASE),
            ],
            confidence="high",
        ),
        IntentSignal(
            required=[
                re.compile(r"\bnew\s+contact\b", re.IGNORECASE),
            ],
            forbidden=[
                re.compile(r"\b(lead|leads|campaign|convert(?:s|ed|ing)?|conversion|member)\b", re.IGNORECASE),
            ],
            confidence="medium",
        ),
    ],
    extractor=_extract_create_contact_params,
    required_params=[],
    sample_prompt="Create a Contact with First Name Jane, Last Name Smith, Email jane@example.com, then verify the fields.",
))


register(Recipe(
    name="lead_convert",
    display_name="Convert a Lead",
    description=(
        "Create a Lead, then convert it -- Salesforce auto-creates the "
        "Account, Contact, and Opportunity from the Lead's data."
    ),
    template="lead_convert.robot.j2",
    intent_signals=[
        IntentSignal(
            required=[
                # Verb (convert/converted/converting) OR noun
                # (conversion). Both phrasings show up in PM specs.
                re.compile(r"\b(convert(?:s|ed|ing)?|conversion)\b", re.IGNORECASE),
                re.compile(r"\b(lead|leads)\b", re.IGNORECASE),
            ],
            confidence="high",
        ),
    ],
    extractor=_extract_lead_convert_params,
    required_params=[],
    sample_prompt="Create a Lead, then convert it into an Opportunity (Salesforce auto-creates the Account and Contact).",
))
