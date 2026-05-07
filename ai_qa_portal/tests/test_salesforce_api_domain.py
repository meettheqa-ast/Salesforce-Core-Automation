"""Regression: ``SalesforceApiLibrary._derive_domain`` must produce a
value ``simple-salesforce`` can use to build a valid OAuth/SOAP URL.

Bug history: a Lightning sandbox URL like
``pentairpool--qa.sandbox.lightning.force.com`` used to fall through to
the "custom domain" branch and be returned verbatim. ``simple-
salesforce`` then constructed
``https://<that-host>.salesforce.com/...`` -- an NXDOMAIN -- and every
``API Seed *`` keyword failed with ``getaddrinfo failed`` before the
test even reached the UI.

Tests cover all real Salesforce URL shapes the project encounters:
modern + legacy sandbox, modern + legacy production, Lightning vs
``my.salesforce.com``, the bare ``test/login`` endpoints, and the
empty / unknown fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from Libraries.SalesforceApiLibrary import SalesforceApiLibrary  # noqa: E402


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Lightning sandbox (the URL that triggered this regression).
        ("https://pentairpool--qa.sandbox.lightning.force.com", "test"),
        ("https://pentairpool--qa.sandbox.lightning.force.com/", "test"),
        # Modern + legacy sandbox shapes.
        ("https://acme--dev.sandbox.my.salesforce.com", "test"),
        ("https://acme--dev.sandbox.lightning.force.com", "test"),
        ("https://acme--dev.my.salesforce.com", "test"),
        ("https://acme--dev.lightning.force.com", "test"),
        # Production hosts.
        ("https://pentairpool.my.salesforce.com", "login"),
        ("https://pentairpool.lightning.force.com", "login"),
        # Bare OAuth endpoints.
        ("https://test.salesforce.com", "test"),
        ("https://login.salesforce.com", "login"),
        # Defensive fallbacks.
        ("", "test"),
        ("https://something.weird.example.com", "test"),
    ],
)
def test_derive_domain(url: str, expected: str) -> None:
    """Every Salesforce URL shape we ship support for must resolve to
    a ``simple-salesforce``-safe ``domain`` value (``"test"`` / ``"login"``
    or a usable My-Domain prefix). A regression here breaks every
    ``API Seed *`` keyword silently with NXDOMAIN."""
    lib = SalesforceApiLibrary(sandbox_url=url)
    assert lib._derive_domain() == expected
