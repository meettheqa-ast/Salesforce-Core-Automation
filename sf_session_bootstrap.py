"""
Salesforce CLI OAuth session bootstrap: turns a one-time interactive
``sf org login web`` into a ready-to-use browser session for automation,
via Salesforce's ``frontdoor.jsp`` mechanism.

Why this exists
----------------
Both Robot/Selenium (``GlobalKeywords.Login To Sandbox``) and Playwright
(``pw_mcp_bridge._do_salesforce_login``) log in by filling the standard
username/password form. That form-fill can never get past an org's
MFA/OTP challenge or SSO redirect on its own — there's no code anywhere
that submits a verification code or drives an identity provider.

The Salesforce CLI (`sf`) already solves this: `sf org login web` opens a
real browser, a human logs in and completes MFA/SSO themselves ONCE, and
the CLI stores an OAuth refresh token locally. From then on, `sf org
display --json` hands back a fresh access token without any human
interaction (the CLI silently uses the stored refresh token).

Given a valid access token + instance URL, navigating an automated
browser straight to::

    <instanceUrl>/secur/frontdoor.jsp?sid=<accessToken>

logs that browser into an authenticated Lightning session immediately —
no login form, no MFA/OTP prompt, no SSO redirect — because Salesforce
already trusts the token. This module is the shared, framework-agnostic
piece that fetches that token and builds that URL for both the Robot/
Selenium and Playwright login paths.

Security notes
---------------
* Access tokens are bearer credentials. Nothing in this module logs a
  token or a fully-built frontdoor URL — only the org alias and instance
  host are logged.
* The local `sf` CLI auth store (``~/.sf`` / OS keychain) is itself a
  credential store equivalent to a saved password; protect the machine
  it runs on the same way (see docs/sfdx-login-setup.md).
* Subprocess calls never use ``shell=True``; alias/instance-url are
  passed as separate argv entries.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from urllib.parse import urlsplit

_logger = logging.getLogger(__name__)

# Substrings the `sf` CLI is known to emit (in stderr or the JSON error
# message) when an org alias has no usable auth — either never
# authenticated, or the stored refresh token was expired/revoked.
_NO_AUTH_HINTS = (
    "no authorization information found",
    "no auth info found",
    "not authenticated",
    "no default environment",
    "invalid_grant",
    "expired access/refresh token",
    "session_expired_or_invalid",
    "the org cannot be found",
)


class SfCliAuthError(RuntimeError):
    """Raised when the local `sf` CLI has no valid, usable auth for an org alias.

    The message is always actionable (names the exact ``sf org login web``
    command to run) and never contains a token or other secret.
    """


def _sf_path() -> str:
    sf = shutil.which("sf")
    if not sf:
        raise FileNotFoundError(
            "Salesforce CLI ('sf') not found on PATH. Install it from "
            "https://developer.salesforce.com/tools/salesforcecli to use "
            "CLI OAuth session login."
        )
    return sf


def _login_hint(alias: str, instance_url: str | None) -> str:
    cmd = f"sf org login web --alias {alias}"
    if instance_url:
        cmd += f" --instance-url {instance_url}"
    return (
        f"Salesforce CLI has no valid session for org alias '{alias}'. "
        f"Run this once, interactively, to authenticate (this satisfies "
        f"MFA/SSO one time):\n  {cmd}\n"
        f"Then verify with: sf org display --target-org {alias}"
    )


def is_configured(alias: str | None) -> bool:
    """Cheap, no-subprocess check: is a non-empty alias set and is `sf` on PATH?

    Callers should check this before attempting the CLI path at all, so a
    project that hasn't opted into CLI OAuth login never pays the cost of
    a subprocess call.
    """
    return bool(alias and alias.strip()) and shutil.which("sf") is not None


def get_org_session(alias: str, *, instance_url: str | None = None, timeout: float = 20.0) -> dict:
    """Return ``{"accessToken": ..., "instanceUrl": ...}`` for ``alias``.

    Runs ``sf org display --json --target-org <alias>``. Raises
    ``SfCliAuthError`` (with an actionable, secret-free message) when the
    CLI reports no usable auth for this alias, and ``FileNotFoundError``
    when the `sf` CLI itself isn't installed.
    """
    alias = (alias or "").strip()
    if not alias:
        raise SfCliAuthError("No Salesforce CLI org alias configured.")

    sf = _sf_path()
    # On `sf` CLI >= 2.1xx, `org display --json` (even with --verbose)
    # always redacts accessToken as the literal string "[REDACTED] Use
    # 'sf org auth show-access-token' to view" -- that command is now the
    # only supported way to get the real token back out. `org display`
    # is still used here for its own sake: it validates the alias has
    # usable auth and gives us instanceUrl with the same error-message
    # shape callers already expect.
    cmd = [sf, "org", "display", "--json", "--target-org", alias]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SfCliAuthError(
            f"Salesforce CLI did not respond within {timeout}s for org alias '{alias}'."
        ) from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined_lc = f"{stdout}\n{stderr}".lower()

    payload: dict | None = None
    try:
        payload = json.loads(stdout) if stdout.strip() else None
    except ValueError:
        payload = None

    if completed.returncode != 0 or payload is None or payload.get("status") not in (0, "0"):
        if any(hint in combined_lc for hint in _NO_AUTH_HINTS):
            raise SfCliAuthError(_login_hint(alias, instance_url))
        # Some other CLI failure -- surface stderr but never the raw stdout
        # (which, on some sf CLI versions, echoes back partial token data
        # inside error payloads for other commands; play it safe here too).
        detail = stderr.strip() or "sf CLI returned a non-zero exit code with no error detail."
        raise SfCliAuthError(
            f"Salesforce CLI could not resolve org alias '{alias}': {detail}"
        )

    result = payload.get("result") or {}
    access_token = result.get("accessToken")
    resolved_instance_url = result.get("instanceUrl") or instance_url
    if not access_token or not resolved_instance_url:
        raise SfCliAuthError(_login_hint(alias, instance_url))
    if "REDACTED" in access_token:
        access_token = _fetch_access_token(sf, alias, timeout=timeout)

    host = urlsplit(resolved_instance_url).netloc or "?"
    _logger.info("sf-session-bootstrap resolved org alias '%s' (host=%s)", alias, host)

    return {"accessToken": access_token, "instanceUrl": resolved_instance_url}


def _fetch_access_token(sf: str, alias: str, *, timeout: float) -> str:
    """Fetch the real access token via the dedicated command required on
    `sf` CLI versions that always redact it from ``org display`` output.
    ``--json`` skips the interactive confirmation prompt (per `sf`'s own
    docs), so this is safe to run non-interactively from a subprocess."""
    cmd = [sf, "org", "auth", "show-access-token", "--target-org", alias, "--json"]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SfCliAuthError(
            f"Salesforce CLI did not respond within {timeout}s fetching the "
            f"access token for org alias '{alias}'."
        ) from exc

    try:
        payload = json.loads(completed.stdout) if completed.stdout.strip() else None
    except ValueError:
        payload = None

    token = (payload or {}).get("result", {}).get("accessToken") if payload else None
    if completed.returncode != 0 or not token or "REDACTED" in token:
        raise SfCliAuthError(
            f"Salesforce CLI could not return an access token for org alias "
            f"'{alias}'. Try running `sf org auth show-access-token "
            f"--target-org {alias}` yourself to see the underlying error."
        )
    return token


def build_frontdoor_url(instance_url: str, access_token: str) -> str:
    """Build the ``frontdoor.jsp`` URL that bootstraps an authenticated session."""
    return f"{instance_url.rstrip('/')}/secur/frontdoor.jsp?sid={access_token}"


def get_frontdoor_url(alias: str, *, instance_url: str | None = None) -> str:
    """Convenience wrapper: ``get_org_session`` + ``build_frontdoor_url``."""
    session = get_org_session(alias, instance_url=instance_url)
    return build_frontdoor_url(session["instanceUrl"], session["accessToken"])
