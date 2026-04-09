"""Predefined smoke and regression test plans for Salesforce core objects.

Each plan is a list of plain-English scenario descriptions that the LLM will
expand into independent ``*** Test Cases ***`` inside a single ``.robot`` file.

Usage::

    from test_plans import get_plan, detect_plan_intent

    level, obj = detect_plan_intent("Run a smoke test for Leads")
    scenarios = get_plan(obj, level)
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

SMOKE: dict[str, list[str]] = {
    "Lead": [
        "Create a new Lead with auto-generated data (First Name, Last Name, Company, Phone, Email) and verify it was created successfully using SalesPO.Verify Lead Created Successfully",
        "Open the Lead record, click Edit, update the Phone field to a new number, save, and verify the updated Phone value is displayed on the record page",
        "Open the Lead record, change the Lead Status to a different value via the Status dropdown, save, and verify the new status is reflected",
        "Convert the Lead to an Opportunity using SalesPO.Convert Lead To Opportunity and verify the conversion completed",
        "Delete the Lead record and verify it no longer appears in the list view",
    ],
    "Account": [
        "Create a new Account with auto-generated data (Name, Phone, Website) and verify it was created successfully using SalesPO.Verify Account Creation",
        "Open the Account record, click Edit, update the Phone field, save, and verify the change persisted",
        "Open the Account record, click Edit, update the Website field, save, and verify the change persisted",
        "Delete the Account record and verify it no longer appears",
    ],
    "Contact": [
        "Create a new Contact with auto-generated data (First Name, Last Name, Title, Email, Phone) and verify it was created successfully using SalesPO.Verify Contact Created Successfully",
        "Open the Contact record, click Edit, update the Title field, save, and verify the change persisted",
        "Open the Contact record, click Edit, update the Email field, save, and verify the change persisted",
        "Delete the Contact record and verify it no longer appears",
    ],
    "Opportunity": [
        "Create a new Opportunity with auto-generated data (Name, Amount, Close Date, Stage) and verify it was created using SalesPO.Verify Opportunity",
        "Open the Opportunity record, click Edit, update the Amount field, save, and verify the change persisted",
        "Open the Opportunity record, change the Stage to a different value via dropdown, save, and verify the new Stage is reflected",
        "Delete the Opportunity record and verify it no longer appears",
    ],
}

REGRESSION: dict[str, list[str]] = {
    "Lead": [
        *SMOKE["Lead"],
        "Attempt to create a Lead without a Last Name and verify the required-field validation error appears",
        "Attempt to create a Lead without a Company and verify the required-field validation error appears",
        "Create a Lead, navigate away to the Leads list, search for the Lead by name, and verify it appears in the search results",
        "Create a Lead with maximum-length text (255 characters) in the Last Name and Company fields and verify it saves successfully",
        "Create a Lead, click Edit, make changes, then click Cancel — verify the original values remain unchanged",
        "Create a Lead and verify all picklist fields (Salutation, Lead Source, Lead Status) accept their first valid option without errors",
        "Create two Leads with different data and verify both appear correctly in the Leads list view",
    ],
    "Account": [
        *SMOKE["Account"],
        "Attempt to create an Account without a Name and verify the required-field validation error appears",
        "Create an Account with maximum-length text (255 characters) in the Account Name field and verify it saves",
        "Create an Account, click Edit, make changes, click Cancel, and verify the original values remain unchanged",
        "Create an Account and verify the Type and Industry picklists accept their first valid option",
    ],
    "Contact": [
        *SMOKE["Contact"],
        "Attempt to create a Contact without a Last Name and verify the required-field validation error appears",
        "Create a Contact linked to an existing Account and verify the Account Name appears on the Contact record page",
        "Create a Contact with maximum-length text in the Last Name field and verify it saves",
        "Create a Contact, click Edit, make changes, click Cancel, and verify the original values remain unchanged",
    ],
    "Opportunity": [
        *SMOKE["Opportunity"],
        "Attempt to create an Opportunity without a Close Date and verify the required-field validation error appears",
        "Attempt to create an Opportunity without a Stage and verify the required-field validation error appears",
        "Create an Opportunity, click Edit, change the Close Date to a future date, save, and verify the new date is displayed",
        "Create an Opportunity and verify all picklist fields (Stage, Forecast Category, Type, Lead Source) accept their first valid option",
    ],
}

_LEVELS = {"smoke": SMOKE, "regression": REGRESSION}

# All supported objects (case-insensitive matching)
_OBJECTS = list(SMOKE.keys())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_plan(sf_object: str, level: str = "smoke") -> list[str]:
    """Return scenario list for *sf_object* at *level* ('smoke' or 'regression')."""
    registry = _LEVELS.get(level.lower(), SMOKE)
    for key in registry:
        if key.lower() == sf_object.lower():
            return registry[key]
    return []


def list_objects() -> list[str]:
    return list(_OBJECTS)


_SMOKE_RE = re.compile(r"\b(smoke)\b", re.IGNORECASE)
_REGRESSION_RE = re.compile(r"\b(regression)\b", re.IGNORECASE)
_OBJECT_RE = re.compile(
    r"\b(lead|account|contact|opportunity)\b", re.IGNORECASE,
)


def detect_plan_intent(prompt: str) -> tuple[str, list[str]] | None:
    """Detect if *prompt* requests a smoke or regression suite for core objects.

    Returns ``(level, [object_names])`` or ``None`` if not a plan-style request.
    """
    level = ""
    if _REGRESSION_RE.search(prompt):
        level = "regression"
    elif _SMOKE_RE.search(prompt):
        level = "smoke"
    if not level:
        return None

    obj_matches = _OBJECT_RE.findall(prompt)
    if not obj_matches:
        return None

    objects = []
    seen: set[str] = set()
    for m in obj_matches:
        cap = m.capitalize()
        if cap in _OBJECTS and cap not in seen:
            objects.append(cap)
            seen.add(cap)
    if not objects:
        return None
    return level, objects


def build_expanded_prompt(sf_objects: list[str], level: str = "smoke") -> str:
    """Build the full LLM prompt that instructs multi-test-case generation."""
    all_scenarios: list[str] = []
    for obj in sf_objects:
        scenarios = get_plan(obj, level)
        for s in scenarios:
            all_scenarios.append(f"[{obj}] {s}")

    if not all_scenarios:
        return ""

    label = level.capitalize()
    numbered = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(all_scenarios))
    obj_list = ", ".join(sf_objects)

    return (
        f"Generate a SINGLE .robot file containing MULTIPLE *** Test Cases *** "
        f"for a {label} Test Suite targeting Salesforce **{obj_list}** records.\n\n"
        f"Here are the exact scenarios to implement as SEPARATE, INDEPENDENT test cases:\n\n"
        f"{numbered}\n\n"
        f"CRITICAL RULES FOR THIS MULTI-TEST SUITE:\n"
        f"- Each test case MUST be fully independent (do NOT rely on another test having run first).\n"
        f"- Use Test Setup  Begin Web Test  and  Test Teardown  End Web Test  at the *** Settings *** level.\n"
        f"- Each test case MUST call GlobalKeywords.Login To Sandbox as its first step.\n"
        f"- For tests that need an existing record (update, delete, convert), use API data seeding "
        f"(API Seed Lead / API Seed Account / etc. from GlobalApi.robot) in the test's first step "
        f"to create the prerequisite, then clean it up with API Cleanup Record in the test's "
        f"[Teardown] if the test didn't already delete it.\n"
        f"- Name each test case descriptively: '{label} - Create Lead', '{label} - Update Account Phone', etc.\n"
        f"- Tag every test case with: {level}    <object_name_lowercase>\n"
    )
