"""Unit tests for CLI → frontdoor URL helper (no live Salesforce required)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Libraries.SalesforceSessionLibrary import SalesforceSessionLibrary  # noqa: E402


@pytest.fixture()
def lib() -> SalesforceSessionLibrary:
    return SalesforceSessionLibrary()


def test_get_frontdoor_url_builds_expected_query(lib: SalesforceSessionLibrary):
    payload = {
        "status": 0,
        "result": {
            "accessToken": "TOKEN/with+special=chars",
            "instanceUrl": "https://example.my.salesforce.com",
        },
    }
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps(payload)
    completed.stderr = ""

    with patch("Libraries.SalesforceSessionLibrary.shutil.which", return_value="sf"), patch(
        "Libraries.SalesforceSessionLibrary.subprocess.run", return_value=completed
    ):
        url = lib.get_frontdoor_url(org_alias="my-alias")

    assert url.startswith("https://example.my.salesforce.com/secur/frontdoor.jsp?")
    assert "sid=TOKEN%2Fwith%2Bspecial%3Dchars" in url
    assert "retURL=/lightning/page/home" in url


def test_cli_org_is_authenticated_false_when_sf_missing(lib: SalesforceSessionLibrary):
    with patch("Libraries.SalesforceSessionLibrary.shutil.which", return_value=None):
        assert lib.cli_org_is_authenticated() is False


def test_resolve_alias_prefers_env(lib: SalesforceSessionLibrary, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SF_DX_ORG_ALIAS", "from-env")
    with patch.object(lib, "resolve_sf_org_alias", wraps=lib.resolve_sf_org_alias):
        # Explicit arg wins
        assert lib.resolve_sf_org_alias("explicit") == "explicit"
    assert lib.resolve_sf_org_alias("") == "from-env"
