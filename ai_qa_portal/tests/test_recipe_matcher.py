"""Tests for the recipe matcher and library.

Covers:
  * Intent matching for the three initial recipes (lead_routing_by_state,
    create_lead_basic, create_campaign_basic) — both positive and
    negative cases (prompts that look adjacent must NOT misroute).
  * Parameter extraction: states, status, source, address, names map
    correctly from natural-language prompts to template parameters.
  * Confidence levels: explicit "create lead" patterns are high, the
    softer "new lead" mention is medium.
  * Template rendering: every recipe produces validator-clean Robot
    Framework when written into ``Tests/Generated/`` (the canonical
    location, where the relative ``Resources/`` imports resolve).
  * Recipe registry stability: insertion order is preserved (so the
    routing recipe wins the tiebreaker against create_lead_basic).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_qa_portal.backend.config import REPO_ROOT
from ai_qa_portal.backend.services import (
    recipe_library,
    recipe_matcher,
)

# ── intent matching ──────────────────────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "Verify Sales Leads with West Coast states (CA, AZ, NV, WA) route to Pool-NA-West",
    "Lead routing rule: California sales leads should be owned by Pool-NA-ISR-West",
    "Test that a Lead created with State=NY and Status=Sales Lead routes to the East queue",
    "non-Sales Leads should route to the Unassigned Lead Queue",
])
def test_routing_recipe_matches(prompt: str):
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None, f"routing prompt should match: {prompt!r}"
    assert m.recipe.name == "lead_routing_by_state"
    assert m.confidence == "high"


@pytest.mark.parametrize("prompt", [
    "Create a new Lead with First Name John, Last Name Doe, Lead Source Web",
    "Create a Lead and verify it was saved",
    "Make a new Lead in the Sales app",
])
def test_create_lead_recipe_matches(prompt: str):
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None, f"create-lead prompt should match: {prompt!r}"
    assert m.recipe.name == "create_lead_basic"


@pytest.mark.parametrize("prompt", [
    "Create a new Campaign with name Q1 Outreach, type Webinar",
    "Create a Campaign and verify it was saved",
    "Make a new Campaign called Spring Promo",
])
def test_create_campaign_recipe_matches(prompt: str):
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None, f"create-campaign prompt should match: {prompt!r}"
    assert m.recipe.name == "create_campaign_basic"


@pytest.mark.parametrize("prompt", [
    # Routing wins over create_lead when both signals fire
    "Create a Lead and verify it routes to Pool-NA-ISR-West when State is California",
])
def test_routing_wins_tiebreak_against_create_lead(prompt: str):
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None
    assert m.recipe.name == "lead_routing_by_state", (
        "routing recipe must register first so it wins on prompts that "
        "mention both 'create a Lead' AND a routing destination"
    )


@pytest.mark.parametrize("prompt", [
    "Show me a list of all open Cases",
    "Run the regression suite for billing",
    "Make a custom Bug Report record",
    "",
    "   ",
])
def test_unmatched_prompts_return_none(prompt: str):
    assert recipe_matcher.match_prompt(prompt) is None, (
        f"prompt should NOT match any recipe (forces LLM fallback): {prompt!r}"
    )


# ── Phase 4 expansion recipes ────────────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "Create a new Account with name Acme Corp",
    "Make a new Account",
    "Create an Account called 'Globex Industries'",
])
def test_create_account_recipe_matches(prompt: str):
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None, f"create-account prompt should match: {prompt!r}"
    assert m.recipe.name == "create_account_basic"


@pytest.mark.parametrize("prompt", [
    "Add a new Lead to a Campaign as CampaignMember",
    "Link a Lead to an existing Campaign",
    "Associate a Lead with a Campaign",
])
def test_campaign_member_add_recipe_matches(prompt: str):
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None, f"campaign-member-add prompt should match: {prompt!r}"
    assert m.recipe.name == "campaign_member_add"


@pytest.mark.parametrize("prompt", [
    "Convert a Lead into an Opportunity",
    "Create a Lead and convert it to Account/Contact",
    "Lead conversion test",
])
def test_lead_convert_recipe_matches(prompt: str):
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None, f"lead-convert prompt should match: {prompt!r}"
    assert m.recipe.name == "lead_convert"


# ── Phase 5: opportunity + contact recipes ────────────────────────────


@pytest.mark.parametrize("prompt", [
    "Create a new Opportunity with name Acme Q2 Deal",
    "Make a new Opportunity",
    "Create an Opportunity at Stage Closed Won and verify",
    # The "update Stage" phrasing was a static template before the recipe
    # existed; route it to the create-with-stage flow now that it's real.
    "Create an Opportunity, set its Stage to Closed Won, and verify",
])
def test_create_opportunity_recipe_matches(prompt: str):
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None, f"create-opportunity prompt should match: {prompt!r}"
    assert m.recipe.name == "create_opportunity_basic"


@pytest.mark.parametrize("prompt", [
    "Create a Contact and verify First Name, Last Name, and Email",
    "Create a new Contact",
    "Make a new Contact named Jane Smith",
    "Verify Contact creation with Title and Email",
])
def test_create_contact_recipe_matches(prompt: str):
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None, f"create-contact prompt should match: {prompt!r}"
    assert m.recipe.name == "create_contact_basic"


@pytest.mark.parametrize("prompt,expected", [
    ("Convert a Lead into an Opportunity", "lead_convert"),
    ("Create a Lead and convert it to a Contact", "lead_convert"),
])
def test_lead_flows_win_against_opportunity_and_contact(prompt: str, expected: str):
    """Lead-convert prompts must NOT misroute to the new opportunity /
    contact recipes -- the forbidden patterns on those recipes plus the
    earlier registration of the lead pipeline make this hold."""
    m = recipe_matcher.match_prompt(prompt)
    assert m is not None, f"prompt should still match a recipe: {prompt!r}"
    assert m.recipe.name == expected, (
        f"{prompt!r} should route to {expected!r}, got {m.recipe.name!r}"
    )


def test_opportunity_extracts_stage_when_prompted():
    m = recipe_matcher.match_prompt(
        "Create a new Opportunity at Stage Closed Won and verify"
    )
    assert m is not None
    assert m.params["stage"] == "Closed Won"
    # Title should reflect the stage so the test name reads naturally
    # in the run report.
    assert "ClosedWon" in m.params["test_title"].replace(" ", "")


def test_opportunity_no_stage_when_not_prompted():
    """When the prompt doesn't name a stage, the recipe must NOT inject
    a Stage suite-variable override. The template's ``{% if stage %}``
    guard relies on this being falsy."""
    m = recipe_matcher.match_prompt("Create a new Opportunity")
    assert m is not None
    assert not m.params.get("stage", "")


def test_contact_extracts_named_fields():
    m = recipe_matcher.match_prompt(
        "Create a Contact with First Name Jane, Last Name Smith, Email jane@example.com"
    )
    assert m is not None
    call = m.params["create_contact_call"]
    assert "first_name=Jane" in call
    assert "last_name=Smith" in call
    assert "email=jane@example.com" in call


@pytest.mark.parametrize("prompt", [
    "Create a new Account with name Acme Corp",
    "Add a Lead to a Campaign",
    "Convert a Lead into an Opportunity",
    # New phase-5 recipes also need to render cleanly.
    "Create a new Opportunity with name Acme Q2 Deal and Stage Closed Won",
    "Create a new Opportunity",
    "Create a Contact with First Name Jane, Last Name Smith, Email jane@example.com",
    "Create a new Contact",
])
def test_phase4_recipes_render_validator_clean(prompt: str):
    """All non-routing recipes must produce validator-clean Robot."""
    from ai_qa_portal.backend.services.script_validator import validate

    m = recipe_matcher.match_prompt(prompt)
    assert m is not None
    rendered = recipe_matcher.render_match(m)

    dest = Path(REPO_ROOT) / "Tests" / "Generated" / "_recipe_phase4_test.robot"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(rendered, encoding="utf-8")
    try:
        report = validate(dest)
        assert report.ok, (
            f"recipe {m.recipe.name!r} failed validation:\n"
            + "\n".join(f"  {e.kind}: {e.symbol} -- {e.message}" for e in report.errors)
        )
    finally:
        dest.unlink(missing_ok=True)


def test_registry_contains_all_eight_recipes():
    names = [r.name for r in recipe_library.all_recipes()]
    expected = {
        "lead_routing_by_state",
        "create_lead_basic",
        "create_campaign_basic",
        "create_account_basic",
        "campaign_member_add",
        "create_opportunity_basic",
        "create_contact_basic",
        "lead_convert",
    }
    assert expected.issubset(set(names)), f"missing recipes: {expected - set(names)}"


def test_every_recipe_has_a_display_name():
    """Every registered recipe must carry a friendly display_name --
    the /generate UI renders it on cards and on the post-gen banner.
    Empty display_name would surface the snake_case internal id to
    end users."""
    missing = [r.name for r in recipe_library.all_recipes() if not r.display_name]
    assert not missing, f"recipes missing display_name: {missing}"


# ── parameter extraction ─────────────────────────────────────────────


def test_routing_extracts_one_test_per_state():
    m = recipe_matcher.match_prompt(
        "Verify routing for CA, TX, NY -- each should route to its regional queue"
    )
    assert m is not None
    tests = m.params["tests"]
    titles = [t["title"] for t in tests]
    # One test per state mentioned, in iteration order (West, Central, East)
    assert any("California" in t for t in titles)
    assert any("Texas" in t for t in titles)
    assert any("New York" in t for t in titles)


def test_routing_full_state_name_in_address_not_code():
    """Critical: address strings must use FULL state names, never the
    2-letter code -- the State picklist prefix-matches 'Canada' before
    'California' and silently sets the wrong state.
    """
    m = recipe_matcher.match_prompt("West Coast (CA) sales leads to Pool-NA-West")
    assert m is not None
    args = m.params["tests"][0]["create_lead_args"]
    assert "California" in args, f"expected full state name in address; got: {args!r}"
    assert ", CA" not in args, (
        f"address must NOT end in 2-letter state code -- prefix-matches 'Canada' "
        f"in the State picklist. got: {args!r}"
    )


def test_routing_assigns_correct_queue_per_state():
    m = recipe_matcher.match_prompt("CA + TX + NY routing")
    assert m is not None
    by_owner = {t["expected_owner"]: t for t in m.params["tests"]}
    assert "Pool-NA-ISR-West" in by_owner
    assert "Pool-NA-ISR-Central" in by_owner
    assert "Pool-NA-ISR-East" in by_owner


def test_routing_assigns_scenario_tag_per_state():
    m = recipe_matcher.match_prompt("CA + TX routing")
    assert m is not None
    tags = {t["scenario_tag"] for t in m.params["tests"]}
    assert "WestRoute" in tags
    assert "CentralRoute" in tags


def test_routing_includes_unassigned_queue_when_named():
    """Prompts that mention the non-Sales-Lead path should add a test
    that asserts routing to the Unassigned Lead Queue."""
    m = recipe_matcher.match_prompt(
        "Verify CA sales leads route to West, and non-Sales leads route to Unassigned Lead Queue"
    )
    assert m is not None
    owners = [t["expected_owner"] for t in m.params["tests"]]
    assert "Unassigned Lead Queue" in owners


def test_create_lead_extracts_named_fields():
    m = recipe_matcher.match_prompt(
        "Create a new Lead with First Name John, Last Name Doe, Lead Source Web"
    )
    assert m is not None
    call = m.params["create_lead_call"]
    assert "first_name=John" in call
    assert "last_name=Doe" in call
    assert "source=Web" in call


def test_create_lead_with_no_explicit_fields_emits_bare_call():
    m = recipe_matcher.match_prompt("Create a Lead and verify it was saved")
    assert m is not None
    # No named args -> the keyword runs with SalesData.robot defaults.
    assert m.params["create_lead_call"] == "SalesPO.Create A New Lead"


def test_create_campaign_extracts_name_and_type():
    m = recipe_matcher.match_prompt(
        "Create a new Campaign with name Q1 Outreach, type Webinar"
    )
    assert m is not None
    call = m.params["create_campaign_call"]
    assert "name=Q1 Outreach" in call
    assert "type=Webinar" in call


# ── template rendering ───────────────────────────────────────────────


@pytest.mark.parametrize("prompt", [
    "West Coast (CA) and East Coast (NY) lead routing, plus non-Sales -> Unassigned Lead Queue",
    "Create a new Lead with First Name John, Last Name Doe, Lead Source Web",
    "Create a new Campaign with name Q1 Outreach, type Webinar",
])
def test_rendered_recipe_passes_ast_validator(prompt: str):
    """Every rendered recipe MUST be validator-clean against the real
    catalog. If a template references a renamed/removed keyword or
    leaves a Jinja placeholder unresolved, this test catches it before
    it reaches a user."""
    from ai_qa_portal.backend.services.script_validator import validate

    m = recipe_matcher.match_prompt(prompt)
    assert m is not None

    rendered = recipe_matcher.render_match(m)
    # Write into Tests/Generated where the relative resource imports
    # in the templates resolve correctly. The validator is path-based.
    dest = Path(REPO_ROOT) / "Tests" / "Generated" / "_recipe_test_render.robot"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(rendered, encoding="utf-8")
    try:
        report = validate(dest)
        assert report.ok, (
            f"recipe {m.recipe.name!r} rendered output failed validation:\n"
            + "\n".join(f"  {e.kind}: {e.symbol} -- {e.message}" for e in report.errors)
        )
    finally:
        dest.unlink(missing_ok=True)


# ── registry behavior ────────────────────────────────────────────────


def test_registry_contains_initial_three_recipes():
    names = [r.name for r in recipe_library.all_recipes()]
    assert "lead_routing_by_state" in names
    assert "create_lead_basic" in names
    assert "create_campaign_basic" in names


def test_routing_recipe_registered_before_create_lead():
    """Order is load-bearing: routing must register first so it wins
    the tiebreaker on prompts that say 'create a Lead' AND mention
    a routing destination."""
    names = [r.name for r in recipe_library.all_recipes()]
    routing_idx = names.index("lead_routing_by_state")
    create_idx = names.index("create_lead_basic")
    assert routing_idx < create_idx
