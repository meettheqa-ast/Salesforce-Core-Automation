"""
Plan Dhurandhar — Locator Health Scanner.

Parses GlobalLocators.robot, navigates through a Salesforce Lead flow
using RF-MCP, and tests each locator against the live DOM. Produces a
report of healthy, stale, and skipped locators.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
LOCATORS_FILE = ROOT / "Resources" / "Common" / "GlobalLocators.robot"

# Locators that need a specific page context to be testable
_LOGIN_PAGE_LOCATORS = {"sandboxUserName", "sandboxPassword", "sandboxLoginButton"}
_POST_LOGIN_LOCATORS = {
    "appLauncher", "searchAppLauncher", "sandboxLaunch360Logo", "activeAppLocator",
}
_LIST_VIEW_LOCATORS = {
    "tabInAppLocator", "activeTabLocator", "newRecordTier1", "newRecordTier2",
    "newRecord", "listViewButton", "listViewButtonFlexible", "intelligentListButton",
    "sfRecordTypeOverlay", "listViewDropdownLocator", "listViewSearchSpinner",
}
_DIALOG_LOCATORS = {
    "newRecordDialogTitleLocator", "dialogAction", "inputFieldDialogLocator",
    "customInputFieldDialogLocator", "dropdownDialogLocator",
    "dropdownDialogAriaLabel", "dropdownOptionsDialogLocator",
    "dropdownOptionByDataValue", "comboboxOption",
    "dateFieldDialogLocator", "timeFieldDialogLocator",
    "checkboxDialogLocator", "dialogLocator",
    "searchInputFieldDialogLocator", "searchSuggestionTermDialogLocator",
    "multiselectScopeDialogLocator", "leadConvertFieldDialogLocator",
    "salesforceModalValidationErrorLocator", "snagErrorFieldLinksLocator",
}
_RECORD_DETAIL_LOCATORS = {
    "recordDataLocator", "recordFieldBlockLocator",
    "actionRecordTypeLocator", "headerQuickActionDropdownLocator",
    "successToastMessageOnRecordDetailsPageLocator",
    "relatedRecordDropdownNameLocator", "relatedRecordDropdownLocator",
    "relatedRecordDropdownOptionLocator", "relatedRecordsViewAllLocator",
    "realtedRecordListViewTitleLocator", "tableCellLocator",
    "relatedRecordParentBreadcrumbLocator", "successToastMessageLocator",
}
# Locators that cannot be tested without very specific runtime context
_SKIP_LOCATORS = {
    "searchSuggestionTermDialogLocator", "searchSuggestionTermLocator",
    "dropdownOptionsDialogLocator", "dropdownOptionsLocator",
    "dropdownOptionByDataValue", "tableCellLocator",
    "relatedRecordDropdownOptionLocator", "closeStageSelectDialog",
    "pathOption", "activePathOption", "submitPathStep",
    "entityNameLocator", "emptyContainerListViewLocator",
    "listViewDropdownOptionLocator", "dynamicFormInformationSectionLocator",
    "leadConvertFieldDialogLocator",
}

# Placeholder substitutions for testing (use real Salesforce values)
_PLACEHOLDER_SUBS = {
    "<tab-name>": "Leads",
    "<app-name>": "Sales",
    "<record-name>": "Lead",
    "<record-type>": "Lead",
    "<field-name>": "Last Name",
    "<dropdown-field>": "Lead Status",
    "<dropdown-value>": "Open",
    "<btn-action>": "Save",
    "<date-field-name>": "Close Date",
    "<time-field-name>": "Time",
    "<checkbox-field>": "Do Not Call",
    "<search-input-field>": "Accounts",
    "<search-term>": "Test",
    "<pos>": "1",
    "<snag-field-name>": "Last Name",
    "<field-label>": "Industry",
    "<record-action>": "Edit",
    "<actual-data>": "Test",
    "<record-id>": "Test",
    "<account-record-type>": "BC Commercial",
    "<title-name>": "Information",
}


def parse_locators_file(path: Path | None = None) -> list[dict[str, str]]:
    """Parse GlobalLocators.robot and extract variable name + locator value pairs."""
    path = path or LOCATORS_FILE
    if not path.is_file():
        raise FileNotFoundError(f"Locators file not found: {path}")

    text = path.read_text(encoding="utf-8")
    locators = []
    pattern = re.compile(r"^\$\{(\w+)\}\s*=\s*(.+)$", re.MULTILINE)

    for match in pattern.finditer(text):
        name = match.group(1).strip()
        value = match.group(2).strip()
        if value and not value.startswith("#"):
            locators.append({"name": name, "locator": value})

    return locators


def _substitute_placeholders(locator: str) -> str:
    """Replace template placeholders with test values."""
    result = locator
    for placeholder, value in _PLACEHOLDER_SUBS.items():
        result = result.replace(placeholder, value)
    return result


def _determine_context(name: str) -> str:
    """Determine which page context a locator needs."""
    if name in _SKIP_LOCATORS:
        return "skip"
    if name in _LOGIN_PAGE_LOCATORS:
        return "login"
    if name in _POST_LOGIN_LOCATORS:
        return "post_login"
    if name in _LIST_VIEW_LOCATORS:
        return "list_view"
    if name in _DIALOG_LOCATORS:
        return "dialog"
    if name in _RECORD_DETAIL_LOCATORS:
        return "record_detail"
    # General locators (not in dialog context) — test on list view
    if "Dialog" not in name and "dialog" not in name:
        return "list_view"
    return "dialog"


def run_scan(
    sandbox_url: str,
    username: str,
    password: str,
) -> list[dict[str, Any]]:
    """Run the full locator scan against the live Salesforce org.

    Returns a list of dicts: [{"name": ..., "locator": ..., "status": "FOUND"|"NOT_FOUND"|"ERROR", "error": ...}, ...]
    """
    import mcp_bridge

    locators = parse_locators_file()
    results: list[dict[str, Any]] = []

    # Start MCP server and create a session
    mcp_bridge.start_mcp_server()
    session_id = mcp_bridge.init_session(sandbox_url, username, password)

    # Track which page contexts we've navigated to
    contexts_reached: set[str] = set()

    def _navigate_to(context: str) -> bool:
        """Navigate the MCP session to the required page context."""
        if context in contexts_reached:
            return True
        try:
            if context == "login":
                # Login page locators are only on the login page (before auth)
                # We can't test these after logging in; skip them
                return False

            if context == "post_login":
                mcp_bridge.execute_step(session_id, "GlobalKeywords.Login To Sandbox", [
                    "${globalSandboxTestUrl}", "${sandboxUserNameInput}", "${sandboxPasswordInput}",
                ])
                contexts_reached.add("post_login")
                return True

            if context == "list_view":
                if "post_login" not in contexts_reached:
                    _navigate_to("post_login")
                mcp_bridge.execute_step(session_id, "GlobalKeywords.Launch App", ["Sales"])
                mcp_bridge.execute_step(session_id, "GlobalKeywords.Select App Tab", ["Leads"])
                contexts_reached.add("list_view")
                return True

            if context == "dialog":
                if "list_view" not in contexts_reached:
                    _navigate_to("list_view")
                mcp_bridge.execute_step(session_id, "GlobalKeywords.Open New Dialog", ["Lead"])
                contexts_reached.add("dialog")
                return True

            if context == "record_detail":
                # We'd need to create a record first — skip for now
                return False

        except Exception as exc:
            _logger.warning("Failed to navigate to %s: %s", context, exc)
            return False
        return False

    # Group locators by context and test them
    for loc in locators:
        name = loc["name"]
        raw_locator = loc["locator"]
        context = _determine_context(name)

        if context == "skip":
            results.append({
                "name": name,
                "locator": raw_locator[:100],
                "status": "ERROR",
                "error": "Requires specific runtime context",
            })
            continue

        test_locator = _substitute_placeholders(raw_locator)

        if not _navigate_to(context):
            results.append({
                "name": name,
                "locator": raw_locator[:100],
                "status": "ERROR",
                "error": f"Could not navigate to {context} context",
            })
            continue

        # Test the locator
        try:
            result = mcp_bridge.execute_step(
                session_id,
                "Wait Until Page Contains Element",
                [test_locator, "timeout=3s"],
            )
            status = result.get("status", "")
            if status == "FAIL":
                results.append({
                    "name": name,
                    "locator": raw_locator[:100],
                    "status": "NOT_FOUND",
                    "error": result.get("error", "Element not present"),
                })
            else:
                results.append({
                    "name": name,
                    "locator": raw_locator[:100],
                    "status": "FOUND",
                    "error": "",
                })
        except Exception as exc:
            results.append({
                "name": name,
                "locator": raw_locator[:100],
                "status": "NOT_FOUND",
                "error": str(exc)[:200],
            })

    return results
