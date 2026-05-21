"""Shared Salesforce field-label alias map.

This is the Python source of truth used by runtime healing and planner paths.
Robot keywords keep a local fallback map for backward compatibility.
"""

from __future__ import annotations

import re

FIELD_LABEL_TO_API_NAME: dict[str, str] = {
    "Lead Owner": "OwnerId",
    "Owner": "OwnerId",
    "Account Owner": "OwnerId",
    "Case Owner": "OwnerId",
    "Opportunity Owner": "OwnerId",
    "Contact Owner": "OwnerId",
    "Lead Status": "Status",
    "Status": "Status",
    "Lead Source": "LeadSource",
    "Source": "LeadSource",
    "Company": "Company",
    "First Name": "FirstName",
    "Last Name": "LastName",
    "Phone": "Phone",
    "Email": "Email",
    "Title": "Title",
    "Website": "Website",
    "Stage": "StageName",
    "Stage Name": "StageName",
    "Amount": "Amount",
    "Close Date": "CloseDate",
    "Account Name": "Name",
    "Industry": "Industry",
    "Type": "Type",
    "Rating": "Rating",
}

_WS_RE = re.compile(r"\s+")


def normalize_field_label(label: str) -> str:
    return _WS_RE.sub(" ", (label or "").strip()).lower()


def api_name_for_label(label: str) -> str | None:
    if not label:
        return None
    wanted = normalize_field_label(label)
    for key, val in FIELD_LABEL_TO_API_NAME.items():
        if normalize_field_label(key) == wanted:
            return val
    return None

