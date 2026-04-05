"""
Smoke Test Prompt Templates.

Provides rich, step-by-step LLM prompts for full-lifecycle smoke tests on
standard Salesforce objects (Lead, Account, Contact, Opportunity).

Each template:
  - Uses the configurable app_name in Launch App calls
  - Injects discovered picklist values (field_context) so the AI uses EXACT values
  - Falls back to Open Dropdown And Select First Option when values not discovered
  - References Enter Text With Fallback for resilient field filling
  - Uses Attempt Save And Auto-Heal Missing Fields on every Save
"""

from __future__ import annotations

# Map user-facing names → canonical SF API object names for OrgInspector
SMOKE_OBJECT_MAP: dict[str, str] = {
    "lead": "Lead",
    "account": "Account",
    "contact": "Contact",
    "opportunity": "Opportunity",
    "opp": "Opportunity",
}

# Keywords recognized as smoke intents
_SMOKE_SIGNALS = [
    "smoke on",
    "smoke test",
    "smoke for",
    "run smoke",
    "full smoke",
    "lifecycle test",
    "lifecycle smoke",
    "smoke lifecycle",
    "standard smoke",
    "e2e smoke",
    "end-to-end smoke",
    "end to end smoke",
]

_OBJECT_HINTS = {
    "Lead": ["lead", "leads"],
    "Account": ["account", "accounts"],
    "Contact": ["contact", "contacts"],
    "Opportunity": ["opportunity", "opportunities", "opp", "opps"],
}


def detect_smoke_intent(prompt: str) -> dict | None:
    """
    Detect smoke-test intent from a natural-language prompt.

    Returns {"object": "Lead"|"Account"|"Contact"|"Opportunity"} or None.

    Examples that match:
      "smoke on leads"  →  {"object": "Lead"}
      "run smoke for accounts"  →  {"object": "Account"}
      "full opportunity lifecycle smoke"  →  {"object": "Opportunity"}
    """
    lower = prompt.strip().lower()

    has_smoke_signal = any(sig in lower for sig in _SMOKE_SIGNALS)
    # Also match bare "<object> smoke" or "smoke <object>"
    for sf_obj, hints in _OBJECT_HINTS.items():
        for hint in hints:
            if f"{hint} smoke" in lower or f"smoke {hint}" in lower:
                has_smoke_signal = True

    if not has_smoke_signal:
        return None

    for sf_obj, hints in _OBJECT_HINTS.items():
        if any(hint in lower for hint in hints):
            return {"object": sf_obj}

    return None


# ---------------------------------------------------------------------------
# Template builders
# ---------------------------------------------------------------------------

def _field_ctx_block(field_context: str) -> str:
    if not field_context or not field_context.strip():
        return (
            "No live field data was retrieved. Use `Open Dropdown And Select First Option` "
            "for every picklist field.\n"
        )
    return field_context.strip() + "\n"


def get_smoke_prompt(
    sf_object: str,
    app_name: str = "Sales",
    field_context: str = "",
) -> str:
    """
    Return a detailed lifecycle prompt for the given Salesforce object.

    sf_object  — "Lead", "Account", "Contact", or "Opportunity"
    app_name   — Salesforce App Launcher name (configurable by user)
    field_context — pre-formatted string from OrgInspector.get_smoke_field_context()
    """
    builders = {
        "Lead": _lead_prompt,
        "Account": _account_prompt,
        "Contact": _contact_prompt,
        "Opportunity": _opportunity_prompt,
    }
    builder = builders.get(sf_object, _lead_prompt)
    return builder(app_name, field_context)


def _lead_prompt(app_name: str, field_context: str) -> str:
    return f"""Generate a complete Robot Framework smoke test suite for the FULL Lead lifecycle.
The suite must cover every step below in a single test case.

APP NAME: Use "{app_name}" for all Launch App calls.

DISCOVERED FIELD VALUES (inject these into Select Dropdown Option calls):
{_field_ctx_block(field_context)}

STEP-BY-STEP LIFECYCLE:
1. Login: GlobalKeywords.Login To Sandbox with runtime credentials.
2. Navigate: Launch App "{app_name}" → Select Leads tab → Open New Dialog Lead.
3. Create Lead: Fill ALL fields using SalesPO.Create A New Lead (which uses SalesData variables).
   - For every picklist, use the FIRST non-None value from the discovered list above,
     OR use Open Dropdown And Select First Option if that field is not listed.
   - Use Attempt Save And Auto-Heal Missing Fields for Save.
4. Verify Creation: SalesPO.Verify Lead Created Successfully (toast + record page).
5. Edit Lead Status: On the record page, call Perform Action On Record Details Page Header  Lead  Edit,
   then Open Dropdown for Lead Status and select a DIFFERENT value than what was used at creation
   (use the second non-None option from the discovered list, or Open Dropdown And Select First Option).
   Update Title using Enter Text With Fallback. Click Save via Select Dialog Button  Save.
6. Convert Lead: Call SalesPO.Convert Lead To Opportunity.
   For Converted Status, use the first discovered value or Open Dropdown And Select First Option.
7. Verify Converted: After "Go to Leads", verify the page has returned to the Leads tab.

CRITICAL RULES:
- Use Attempt Save And Auto-Heal Missing Fields on EVERY Save step.
- Use Enter Text With Fallback for any text field that may be a custom field.
- Use exact keyword names from the catalog. No raw SeleniumLibrary calls in the test body.
- Import: SeleniumLibrary, GlobalKeywords.robot, SalesPO.robot.
- Test Setup: Begin Web Test  |  Test Teardown: End Web Test.
- Do NOT define sandbox credentials in *** Variables ***.
- Tags: smoke  lead  lifecycle
"""


def _account_prompt(app_name: str, field_context: str) -> str:
    return f"""Generate a complete Robot Framework smoke test suite for the FULL Account lifecycle.
The suite must cover every step below in a single test case.

APP NAME: Use "{app_name}" for all Launch App calls.

DISCOVERED FIELD VALUES:
{_field_ctx_block(field_context)}

STEP-BY-STEP LIFECYCLE:
1. Login: GlobalKeywords.Login To Sandbox with runtime credentials.
2. Navigate: Launch App "{app_name}" → Select Accounts tab → Convert View From Intelligent To List → Open New Dialog Account.
3. Create Account: Fill using SalesPO.Create A New Account (uses SalesData variables for Name, Type, Phone, Industry).
   - For every picklist, use the discovered value or Open Dropdown And Select First Option.
   - Use Attempt Save And Auto-Heal Missing Fields for Save.
4. Verify Creation: SalesPO.Verify Account Creation.
5. Edit Account — update Phone:
   Call Perform Action On Record Details Page Header  Account  Edit.
   Use Enter Text With Fallback  Phone  <new_fake_phone> (generate with Evaluate random).
   Select Dialog Button  Save. Verify success toast appears and disappears.
6. Delete: SalesPO.Delete Account.
7. Verify deletion: Confirm redirect to Accounts tab (activeTabLocator for Accounts visible).

CRITICAL RULES:
- Use Attempt Save And Auto-Heal Missing Fields on every Save.
- Use Enter Text With Fallback for any non-guaranteed-standard field.
- Import: SeleniumLibrary, GlobalKeywords.robot, SalesPO.robot.
- Test Setup: Begin Web Test  |  Test Teardown: End Web Test.
- Do NOT define sandbox credentials in *** Variables ***.
- Tags: smoke  account  lifecycle
"""


def _contact_prompt(app_name: str, field_context: str) -> str:
    return f"""Generate a complete Robot Framework smoke test suite for the FULL Contact lifecycle.
The suite must cover every step below in a single test case.

APP NAME: Use "{app_name}" for all Launch App calls.

DISCOVERED FIELD VALUES:
{_field_ctx_block(field_context)}

STEP-BY-STEP LIFECYCLE:
1. Login: GlobalKeywords.Login To Sandbox with runtime credentials.
2. Navigate: Launch App "{app_name}" → Select Contacts tab → Open New Dialog Contact.
3. Create Contact: Fill using ContactPO.Create A New Contact (uses SalesData contact variables).
   - First Name, Last Name, Title, Email, Phone: use Enter Text With Fallback.
   - Account Name: use Enter Into Search Field  Account Name  ${{contactAccountName}} if the variable is set.
   - For every picklist, use the discovered value or Open Dropdown And Select First Option.
   - Use Attempt Save And Auto-Heal Missing Fields for Save.
4. Verify Creation: ContactPO.Verify Contact Created Successfully.
5. Edit Contact — update Title:
   Call Perform Action On Record Details Page Header  Contact  Edit.
   Use Enter Text With Fallback  Title  "Updated Smoke Title".
   Select Dialog Button  Save. Verify toast appears.
6. Delete: ContactPO.Delete Contact.

CRITICAL RULES:
- Use Attempt Save And Auto-Heal Missing Fields on every Save.
- Use Enter Text With Fallback for all text fields.
- Import: SeleniumLibrary, GlobalKeywords.robot, SalesPO.robot, ContactPO.robot.
- Test Setup: Begin Web Test  |  Test Teardown: End Web Test.
- Do NOT define sandbox credentials in *** Variables ***.
- Tags: smoke  contact  lifecycle
"""


def _opportunity_prompt(app_name: str, field_context: str) -> str:
    return f"""Generate a complete Robot Framework smoke test suite for the FULL Opportunity lifecycle.
The suite must cover every step below in a single test case.

APP NAME: Use "{app_name}" for all Launch App calls.

DISCOVERED FIELD VALUES:
{_field_ctx_block(field_context)}

STEP-BY-STEP LIFECYCLE:
1. Login: GlobalKeywords.Login To Sandbox with runtime credentials.
2. Navigate to Opportunities: Launch App "{app_name}" → Select Opportunities tab → Open New Dialog Opportunity.
3. Create Opportunity: Fill using SalesPO.Create A New Opportunity (uses SalesData opportunity variables).
   - Account Name lookup: Enter Into Search Field  Accounts  ${{opportunityAccountName}}.
   - Stage: use first discovered Stage value, or Open Dropdown And Select First Option.
   - Close Date: use Enter Date  Close Date  ${{opportunityCloseDate}}.
   - For every other picklist, use discovered values or Open Dropdown And Select First Option.
   - Use Attempt Save And Auto-Heal Missing Fields for Save.
4. Verify Creation: SalesPO.Verify Opportunity.
5. Update Stage — change Stage to a DIFFERENT value:
   Call Perform Action On Record Details Page Header  Opportunity  Edit.
   Open Dropdown  Stage, then Select Dropdown Option with the SECOND discovered Stage value,
   OR use Open Dropdown And Select First Option as fallback.
   Select Dialog Button  Save. Verify success toast.
6. Delete: SalesPO.Delete Opportunity.

CRITICAL RULES:
- Use Attempt Save And Auto-Heal Missing Fields on every Save.
- Enter Text With Fallback for all text/number fields.
- Import: SeleniumLibrary, GlobalKeywords.robot, SalesPO.robot.
- Test Setup: Begin Web Test  |  Test Teardown: End Web Test.
- Do NOT define sandbox credentials in *** Variables ***.
- Tags: smoke  opportunity  lifecycle
"""
