"""Test Data Management (TDM) engine.

Reads a JSON template of prerequisite Salesforce records, seeds them via the
REST API, and returns a ``{var_name: record_id}`` mapping that can be injected
as Robot Framework ``-v`` variables.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_EXAMPLE_TEMPLATE = """\
[
  {
    "object": "Account",
    "var_name": "SeededAccountId",
    "fields": {
      "Name": "TDM Test Account"
    }
  },
  {
    "object": "Contact",
    "var_name": "SeededContactId",
    "fields": {
      "FirstName": "Jane",
      "LastName": "Doe",
      "Email": "jane.doe@example.com"
    }
  }
]
"""

EXAMPLE_TEMPLATE: str = _EXAMPLE_TEMPLATE.strip()


def seed_salesforce_data(
    sandbox_url: str,
    username: str,
    password: str,
    security_token: str,
    template_json_str: str,
) -> dict[str, str]:
    """Create records defined in *template_json_str* and return ``{var_name: id}``.

    Each entry in the JSON array must have:
      - ``object``   — Salesforce API name (e.g. ``Account``, ``Lead``).
      - ``var_name`` — Robot variable name the record ID will be mapped to.
      - ``fields``   — dict of field→value pairs for the create call.

    Raises on authentication failure.  Individual record-creation errors are
    logged but do not abort the remaining items.
    """
    from simple_salesforce import Salesforce

    parsed = urlparse(sandbox_url.strip().rstrip("/"))
    domain = parsed.hostname or ""
    is_sandbox = "sandbox" in domain or "--" in domain

    sf = Salesforce(
        username=username,
        password=password,
        security_token=security_token or "",
        domain="test" if is_sandbox else domain.split(".")[0],
    )

    items = json.loads(template_json_str)
    if not isinstance(items, list):
        raise ValueError("TDM template must be a JSON array.")

    results: dict[str, str] = {}
    for item in items:
        obj_name = item.get("object", "")
        var_name = item.get("var_name", "")
        fields = item.get("fields", {})
        if not obj_name or not var_name:
            logger.warning("Skipping TDM entry missing 'object' or 'var_name': %s", item)
            continue
        try:
            resp = getattr(sf, obj_name).create(fields)
            record_id = resp.get("id") or resp.get("Id") or ""
            results[var_name] = record_id
            logger.info("Created %s → %s = %s", obj_name, var_name, record_id)
        except Exception:
            logger.warning("Failed to create %s for var %s.", obj_name, var_name, exc_info=True)

    return results
