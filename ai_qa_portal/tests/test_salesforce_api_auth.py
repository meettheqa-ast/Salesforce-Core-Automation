"""Regression: ``SalesforceApiLibrary._connect`` must try auth methods
in the documented priority order:

  1. Reuse the active SeleniumLibrary browser session (sid cookie).
  2. OAuth 2.0 password flow when a Connected App's credentials are set.
  3. Legacy SOAP login (only viable on orgs that haven't disabled it).

Bug history: a Pentair sandbox returned ``INVALID_OPERATION: SOAP API
login() is disabled by default in this org`` because we only attempted
SOAP login. Path 1 in particular needs explicit coverage because it's
the silent default the user gets when they just call ``Login To
Sandbox`` then start seeding.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from Libraries.SalesforceApiLibrary import SalesforceApiLibrary  # noqa: E402


def _fake_selenium_with_session(
    *,
    current_url: str,
    sid: str = "FAKE_SID_123",
    domain: str = "",
    api_sid: str = "",
    api_domain: str = "",
) -> MagicMock:
    """Build a SeleniumLibrary mock whose ``.driver`` mimics the cookie
    surface our session-reuse code touches.

    The library uses ``driver.execute_cdp_cmd("Network.getAllCookies",
    {})`` to enumerate cookies across every domain. Tests can pass an
    ``api_sid`` + ``api_domain`` to also include a ``my.salesforce.com``
    cookie -- that's the realistic shape after Lightning has issued
    XHR to the My Domain. Default behaviour (no api_sid) returns only
    the Lightning cookie, exercising the frontdoor.jsp fallback path.
    """
    driver = MagicMock()
    driver.current_url = current_url

    cookies: list[dict] = []
    if sid:
        c = {"name": "sid", "value": sid}
        if domain:
            c["domain"] = domain
        cookies.append(c)
    if api_sid:
        c = {"name": "sid", "value": api_sid, "domain": api_domain or "acme--qa.sandbox.my.salesforce.com"}
        cookies.append(c)

    driver.execute_cdp_cmd.return_value = {"cookies": cookies}
    driver.get_cookies.return_value = cookies
    sl = MagicMock()
    sl.driver = driver
    return sl


def test_session_reuse_takes_priority_over_oauth() -> None:
    """When a Selenium browser is on Salesforce AND OAuth creds are set,
    session reuse wins -- it skips the second login round-trip. The
    library prefers the API-eligible ``my.salesforce.com`` cookie when
    one is present."""
    lib = SalesforceApiLibrary(
        username="u", password="p", sandbox_url="https://acme--qa.sandbox.lightning.force.com",
        consumer_key="ck", consumer_secret="cs",
    )
    sl = _fake_selenium_with_session(
        current_url="https://acme--qa.sandbox.lightning.force.com/lightning/page",
        sid="LIGHTNING_SID",
        domain="acme--qa.sandbox.lightning.force.com",
        api_sid="API_SID",
        api_domain="acme--qa.sandbox.my.salesforce.com",
    )

    with patch("Libraries.SalesforceApiLibrary.Salesforce") as fake_sf:
        with patch("robot.libraries.BuiltIn.BuiltIn") as fake_builtin_cls:
            fake_builtin_cls.return_value.get_library_instance.return_value = sl
            lib._connect()

        fake_sf.assert_called_once()
        kwargs = fake_sf.call_args.kwargs
        # Hallmark of session reuse: session_id + instance_url, NO username/password.
        assert "session_id" in kwargs
        # And specifically the API-eligible sid -- not the Lightning one.
        assert kwargs["session_id"] == "API_SID"
        assert kwargs["instance_url"] == "https://acme--qa.sandbox.my.salesforce.com"
        assert "username" not in kwargs
        assert "password" not in kwargs


def test_oauth_used_when_no_browser_session() -> None:
    """No browser open + Connected-App creds set -> OAuth password flow,
    NOT a SOAP attempt that would fail on locked-down orgs."""
    lib = SalesforceApiLibrary(
        username="u", password="p", sandbox_url="https://acme--qa.sandbox.lightning.force.com",
        consumer_key="my_consumer_key", consumer_secret="my_consumer_secret",
    )

    with patch("Libraries.SalesforceApiLibrary.Salesforce") as fake_sf:
        # No SeleniumLibrary registered -> get_library_instance raises.
        with patch("robot.libraries.BuiltIn.BuiltIn") as fake_builtin_cls:
            fake_builtin_cls.return_value.get_library_instance.side_effect = RuntimeError("no SL")
            lib._connect()

        fake_sf.assert_called_once()
        kwargs = fake_sf.call_args.kwargs
        assert kwargs["consumer_key"] == "my_consumer_key"
        assert kwargs["consumer_secret"] == "my_consumer_secret"
        assert kwargs["username"] == "u"
        assert kwargs["password"] == "p"
        assert kwargs["domain"] == "test"  # sandbox URL


def test_soap_login_disabled_error_is_actionable() -> None:
    """The classic 'SOAP API login() is disabled' error must surface a
    RuntimeError that names BOTH alternative auth paths so the operator
    has a concrete next step instead of a stack trace."""
    from simple_salesforce import SalesforceAuthenticationFailed

    lib = SalesforceApiLibrary(
        username="u", password="p", sandbox_url="https://acme--qa.sandbox.lightning.force.com",
    )

    with patch("Libraries.SalesforceApiLibrary.Salesforce") as fake_sf:
        fake_sf.side_effect = SalesforceAuthenticationFailed(
            "INVALID_OPERATION", "SOAP API login() is disabled by default in this org. Contact admin."
        )
        with patch("robot.libraries.BuiltIn.BuiltIn") as fake_builtin_cls:
            fake_builtin_cls.return_value.get_library_instance.side_effect = RuntimeError("no SL")
            with pytest.raises(RuntimeError) as exc_info:
                lib._connect()

    msg = str(exc_info.value)
    assert "SOAP API login is disabled" in msg
    # Must guide the operator to the two viable alternatives.
    assert "Login To Sandbox" in msg, "error must point at session-reuse path"
    assert "SF_CONSUMER_KEY" in msg, "error must point at OAuth path"


def test_browser_not_on_salesforce_falls_through_to_other_paths() -> None:
    """A live browser parked on a non-Salesforce URL must NOT be treated
    as a session source -- otherwise we'd hand simple-salesforce a
    cookie from some other site."""
    lib = SalesforceApiLibrary(
        username="u", password="p", sandbox_url="https://acme--qa.sandbox.lightning.force.com",
        consumer_key="ck", consumer_secret="cs",
    )
    sl = _fake_selenium_with_session(current_url="https://www.google.com/", sid="WHO_KNOWS")

    with patch("Libraries.SalesforceApiLibrary.Salesforce") as fake_sf:
        with patch("robot.libraries.BuiltIn.BuiltIn") as fake_builtin_cls:
            fake_builtin_cls.return_value.get_library_instance.return_value = sl
            lib._connect()

    # Because the browser wasn't on Salesforce, we fell through to OAuth.
    kwargs = fake_sf.call_args.kwargs
    assert "consumer_key" in kwargs
    assert "session_id" not in kwargs


def test_consumer_key_picked_up_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SF_CONSUMER_KEY`` / ``SF_CONSUMER_SECRET`` env vars are the
    operator-friendly way to enable OAuth without editing imports."""
    monkeypatch.setenv("SF_CONSUMER_KEY", "env_ck")
    monkeypatch.setenv("SF_CONSUMER_SECRET", "env_cs")

    lib = SalesforceApiLibrary(
        username="u", password="p", sandbox_url="https://acme--qa.sandbox.lightning.force.com",
    )
    assert lib._consumer_key == "env_ck"
    assert lib._consumer_secret == "env_cs"


# ---------------------------------------------------------------------------
# Cookie-domain prioritisation for session reuse
# ---------------------------------------------------------------------------
# Bug history: when the browser is parked on a ``.lightning.force.com`` page,
# Selenium's ``get_cookie('sid')`` returns the Lightning session cookie --
# which Salesforce's REST API rejects with ``INVALID_SESSION_ID`` because
# the API endpoints live on ``.my.salesforce.com``. We now use CDP's
# ``Network.getAllCookies`` to enumerate cookies across every domain and
# prefer the ``.my.salesforce.com`` sid. These tests pin the priority logic
# without needing a live browser.


def test_pick_api_session_prefers_my_salesforce_domain() -> None:
    """When BOTH a Lightning sid and a my.salesforce.com sid exist,
    pick the my.salesforce.com one -- that's the API-eligible session."""
    cookies = [
        {"name": "sid", "value": "LIGHTNING", "domain": ".acme.lightning.force.com"},
        {"name": "sid", "value": "API", "domain": ".acme.my.salesforce.com"},
        {"name": "OptanonConsent", "value": "x", "domain": ".salesforce.com"},
    ]
    sid, host = SalesforceApiLibrary._pick_api_session(cookies)
    assert sid == "API"
    assert host == "acme.my.salesforce.com"


def test_pick_api_session_returns_none_when_only_lightning_present() -> None:
    """If no my.salesforce.com cookie has been issued yet (e.g. user
    just signed in and Lightning hasn't made any XHR to the My Domain),
    the API picker returns None so the caller can try frontdoor.jsp."""
    cookies = [
        {"name": "sid", "value": "LIGHTNING", "domain": ".acme.lightning.force.com"},
    ]
    sid, host = SalesforceApiLibrary._pick_api_session(cookies)
    assert sid is None
    assert host is None


def test_pick_lightning_session_handles_dotted_and_undotted_domains() -> None:
    """Salesforce sometimes emits cookie ``domain`` with and without a
    leading dot. The picker must accept both."""
    a = SalesforceApiLibrary._pick_lightning_session(
        [{"name": "sid", "value": "LITE", "domain": ".acme.lightning.force.com"}]
    )
    b = SalesforceApiLibrary._pick_lightning_session(
        [{"name": "sid", "value": "LITE", "domain": "acme.lightning.force.com"}]
    )
    assert a == ("LITE", "acme.lightning.force.com")
    assert b == ("LITE", "acme.lightning.force.com")


def test_pick_session_ignores_third_party_sid_cookies() -> None:
    """Random sites also use a cookie named ``sid``. The picker must
    only accept ones on Salesforce-shaped hosts, otherwise we'd hand
    simple-salesforce someone else's session token."""
    cookies = [
        {"name": "sid", "value": "RANDOM", "domain": ".example.com"},
        {"name": "sid", "value": "ANALYTICS", "domain": ".trk.somecdn.io"},
    ]
    api_sid, _ = SalesforceApiLibrary._pick_api_session(cookies)
    lite_sid, _ = SalesforceApiLibrary._pick_lightning_session(cookies)
    assert api_sid is None
    assert lite_sid is None


def test_derive_my_domain_lightning_to_my_salesforce() -> None:
    """Lightning host -> My Domain mapping for the canonical sandbox /
    production patterns."""
    cases = [
        ("acme--qa.sandbox.lightning.force.com", "acme--qa.sandbox.my.salesforce.com"),
        ("acme.lightning.force.com",             "acme.my.salesforce.com"),
        # Already a My Domain host -> unchanged.
        ("acme.my.salesforce.com",               "acme.my.salesforce.com"),
        # No recognised suffix -> returned as-is so the caller can decide.
        ("test.salesforce.com",                  "test.salesforce.com"),
        ("",                                     ""),
    ]
    for inp, expected in cases:
        assert SalesforceApiLibrary._derive_my_domain(inp) == expected, (
            f"derive failed for {inp!r}"
        )


def test_collect_all_cookies_uses_cdp_when_available() -> None:
    """``Network.getAllCookies`` is the only way to read cookies on
    domains other than the page's current origin. The library must
    prefer CDP and fall back to ``get_cookies`` only when CDP errors."""
    lib = SalesforceApiLibrary(sandbox_url="https://acme.lightning.force.com")
    driver = MagicMock()
    driver.execute_cdp_cmd.return_value = {
        "cookies": [
            {"name": "sid", "value": "API_SID", "domain": ".acme.my.salesforce.com"},
        ]
    }
    cookies = lib._collect_all_cookies(driver)
    assert cookies and cookies[0]["value"] == "API_SID"
    driver.execute_cdp_cmd.assert_called_once_with("Network.getAllCookies", {})
    # Fallback was not needed.
    driver.get_cookies.assert_not_called()


def test_collect_all_cookies_falls_back_when_cdp_unavailable() -> None:
    """Firefox / Safari drivers don't speak CDP. The fallback must
    return whatever the standard cookies API provides instead of
    crashing."""
    lib = SalesforceApiLibrary(sandbox_url="https://acme.lightning.force.com")
    driver = MagicMock()
    driver.execute_cdp_cmd.side_effect = AttributeError("no CDP here")
    driver.get_cookies.return_value = [
        {"name": "sid", "value": "FB", "domain": ".acme.lightning.force.com"},
    ]
    cookies = lib._collect_all_cookies(driver)
    assert cookies and cookies[0]["value"] == "FB"
    driver.get_cookies.assert_called_once()
