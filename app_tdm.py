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


def _connect(
    sandbox_url: str,
    username: str,
    password: str,
    security_token: str,
):
    """Return an authenticated ``simple_salesforce.Salesforce`` client."""
    from simple_salesforce import Salesforce

    parsed = urlparse(sandbox_url.strip().rstrip("/"))
    domain = parsed.hostname or ""
    is_sandbox = "sandbox" in domain or "--" in domain
    return Salesforce(
        username=username,
        password=password,
        security_token=security_token or "",
        domain="test" if is_sandbox else domain.split(".")[0],
    )


def seed_salesforce_data(
    sandbox_url: str,
    username: str,
    password: str,
    security_token: str,
    template_json_str: str,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Create records defined in *template_json_str*.

    Returns ``(variables_dict, teardown_list)``:
      - *variables_dict* — ``{var_name: record_id}`` for Robot ``-v`` injection.
      - *teardown_list*  — ``[{"object": "Account", "id": "001..."}]`` for
        post-run cleanup (should be deleted in **reverse** order).

    Raises on authentication failure.  Individual record-creation errors are
    logged but do not abort the remaining items.
    """
    sf = _connect(sandbox_url, username, password, security_token)

    items = json.loads(template_json_str)
    if not isinstance(items, list):
        raise ValueError("TDM template must be a JSON array.")

    results: dict[str, str] = {}
    teardown: list[dict[str, str]] = []

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
            teardown.append({"object": obj_name, "id": record_id})
            logger.info("Created %s → %s = %s", obj_name, var_name, record_id)
        except Exception:
            logger.warning("Failed to create %s for var %s.", obj_name, var_name, exc_info=True)

    return results, teardown


def teardown_salesforce_data(
    sandbox_url: str,
    username: str,
    password: str,
    security_token: str,
    teardown_list: list[dict[str, str]],
) -> int:
    """Delete previously seeded records in reverse order (child-first).

    Returns the number of records successfully deleted.
    """
    if not teardown_list:
        return 0

    sf = _connect(sandbox_url, username, password, security_token)
    deleted = 0
    for item in reversed(teardown_list):
        obj_name = item.get("object", "")
        record_id = item.get("id", "")
        if not obj_name or not record_id:
            continue
        try:
            getattr(sf, obj_name).delete(record_id)
            deleted += 1
            logger.info("Deleted %s %s", obj_name, record_id)
        except Exception:
            logger.warning("Failed to delete %s %s.", obj_name, record_id, exc_info=True)
    return deleted
