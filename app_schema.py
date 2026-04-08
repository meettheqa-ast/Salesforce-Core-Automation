"""Salesforce org schema introspection for AI-assisted test generation.

Uses ``simple-salesforce`` to call ``.describe()`` on a Salesforce object and
extract required fields and active picklist values so the LLM can generate
scripts with org-accurate data.
"""

from __future__ import annotations

import re
from typing import Any


_SF_OBJECTS_PATTERN = re.compile(
    r"\b(Lead|Account|Contact|Opportunity|Case|Work\s*Order|Campaign|Task|Event)\b",
    re.IGNORECASE,
)

_API_NAME_MAP: dict[str, str] = {
    "lead": "Lead",
    "account": "Account",
    "contact": "Contact",
    "opportunity": "Opportunity",
    "case": "Case",
    "workorder": "WorkOrder",
    "work order": "WorkOrder",
    "campaign": "Campaign",
    "task": "Task",
    "event": "Event",
}


def detect_salesforce_objects(prompt: str) -> list[str]:
    """Return de-duplicated list of Salesforce API object names found in *prompt*."""
    hits = _SF_OBJECTS_PATTERN.findall(prompt)
    seen: set[str] = set()
    result: list[str] = []
    for h in hits:
        api = _API_NAME_MAP.get(h.lower().replace(" ", ""), h)
        if api not in seen:
            seen.add(api)
            result.append(api)
    return result


def _connect(
    sandbox_url: str,
    username: str,
    password: str,
    security_token: str,
) -> Any:
    from simple_salesforce import Salesforce

    domain = "test"
    url = sandbox_url.strip().rstrip("/")
    if ".sandbox.my.salesforce.com" in url or ".sandbox.lightning.force.com" in url:
        domain = url.split("//")[-1].split(".sandbox")[0]
        if "--" in domain:
            domain = domain.split("--")[0]
        domain = "test"

    return Salesforce(
        username=username.strip(),
        password=password.strip(),
        security_token=security_token or "",
        domain=domain,
    )


def get_object_schema(
    sandbox_url: str,
    username: str,
    password: str,
    security_token: str,
    object_name: str,
) -> str:
    """Return a compact text summary of required fields and picklist values.

    On any failure (bad creds, invalid object, missing library) returns ``""``.
    """
    try:
        sf = _connect(sandbox_url, username, password, security_token)
        desc = getattr(sf, object_name).describe()
    except Exception:  # noqa: BLE001
        return ""

    fields: list[dict[str, Any]] = desc.get("fields", [])

    required: list[str] = []
    picklists: dict[str, list[str]] = {}

    for f in fields:
        name = f.get("name", "")
        label = f.get("label", name)
        createable = f.get("createable", False)
        if not createable:
            continue

        nillable = f.get("nillable", True)
        defaulted = f.get("defaultedOnCreate", True)
        if not nillable and not defaulted:
            required.append(label)

        ftype = f.get("type", "")
        if ftype in ("picklist", "multipicklist"):
            active_vals = [
                pv.get("label", pv.get("value", ""))
                for pv in f.get("picklistValues", [])
                if pv.get("active", False)
            ]
            if active_vals:
                picklists[label] = active_vals

    lines: list[str] = [f"{object_name} Schema"]
    if required:
        lines.append(f"  Required fields (must be filled on create): {', '.join(required)}")
    if picklists:
        for label, vals in picklists.items():
            display = vals[:15]
            suffix = f" ... (+{len(vals) - 15} more)" if len(vals) > 15 else ""
            lines.append(f"  {label} picklist options: [{', '.join(display)}{suffix}]")

    return "\n".join(lines)


def get_schema_context(
    prompt: str,
    sandbox_url: str,
    username: str,
    password: str,
    security_token: str,
) -> str:
    """Detect objects in *prompt*, fetch their schemas, return combined context string."""
    objects = detect_salesforce_objects(prompt)
    if not objects:
        return ""
    if not sandbox_url.strip() or not username.strip() or not password.strip():
        return ""

    blocks: list[str] = []
    for obj in objects:
        schema = get_object_schema(sandbox_url, username, password, security_token, obj)
        if schema:
            blocks.append(schema)

    if not blocks:
        return ""

    return (
        "\n\nCRITICAL ORG SCHEMA INFO\n"
        "------------------------\n"
        "Below are the ACTUAL required fields and picklist values for this org. "
        "You MUST use these exact picklist values in Select Dropdown Option calls "
        "if you are interacting with these fields. For picklist fields NOT listed "
        "below, use Open Dropdown And Select First Option.\n\n"
        + "\n\n".join(blocks)
    )
