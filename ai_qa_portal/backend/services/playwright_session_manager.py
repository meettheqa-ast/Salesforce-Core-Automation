"""Salesforce session manager for Playwright.

The existing ``Libraries/SalesforceApiLibrary.py`` reuses Selenium driver
sessions for REST API calls -- but its CDP cookie-capture path is wired to
``SeleniumLibrary``, not Playwright. There is no analogous bridge between
a live Playwright BrowserContext and the SF API.

This module owns Playwright's own auth lifecycle:

  1. First request for a (sandbox_url, username, persona_id):
     - Launch Playwright Chromium (idempotent via ``pw_mcp_bridge``)
     - Open a fresh context, navigate to sandbox login
     - Submit credentials, wait for Lightning shell
     - Persist storageState (cookies + localStorage) to disk

  2. Subsequent requests within the cache TTL:
     - Reuse the live BrowserContext (skips even page navigation)

  3. Subsequent requests after TTL expiry but within storage lifetime:
     - Build a new context with the persisted storageState
     - Skip the SF login form entirely; cookies prove identity

  4. After storage lifetime expires (or SF kicks us out):
     - Fall through to a full fresh login

Public surface is ``acquire_session`` -- callers in the validator,
recording, visual regression services use it instead of touching
``pw_mcp_bridge.get_or_init_session`` directly. Centralising means we
can later add per-org login overrides (e.g. SSO, MFA tokens) without
touching every caller.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("ai_qa_portal.playwright")


# Treat these substrings in a thrown exception as "Salesforce kicked us
# out" -- we drop the cached state and force a fresh login on next
# acquire. Conservative list; missing a hint just means an extra round
# trip on the next call, never a wrong answer.
_SF_LOGOUT_HINTS = (
    "redirected to login",
    "login.salesforce",
    "session expired",
    "invalid_session",
    "sso required",
)


@dataclass
class SessionHandle:
    """What ``acquire_session`` returns. Holds the live BrowserContext
    plus enough metadata for callers to decide whether to navigate or
    just use the existing page.
    """
    context: Any                       # playwright.async_api.BrowserContext
    sandbox_url: str
    username: str
    persona_id: str | None
    cache_hit: bool                    # True if reused warm context
    acquired_at: float


def acquire_session(
    sandbox_url: str,
    username: str,
    password: str,
    persona_id: str | None = None,
) -> SessionHandle:
    """Synchronous facade over ``pw_mcp_bridge.get_or_init_session``.

    Adds:
    * Friendly log line so we can correlate "Playwright session create"
      events with bulk-run requests in production logs.
    * Defensive credential validation -- empty creds short-circuit so
      we don't spin up a browser just to fail the login form.
    * Returns a typed ``SessionHandle`` instead of a bare 2-tuple.
    """
    if not sandbox_url or not username or not password:
        raise ValueError(
            "Cannot acquire Playwright SF session: sandbox_url, username "
            "and password are all required (got empty values)."
        )

    # Late import keeps this module loadable on systems without
    # Playwright installed; only the actual call needs the package.
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    import pw_mcp_bridge

    t0 = time.monotonic()
    context, cache_hit = pw_mcp_bridge.get_or_init_session(
        sandbox_url=sandbox_url,
        username=username,
        password=password,
        persona_id=persona_id,
    )
    dt_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "pw-mcp acquire_session %s sandbox=%s user=%s persona=%s elapsed_ms=%d",
        "warm" if cache_hit else "cold",
        sandbox_url,
        username,
        persona_id or "-",
        dt_ms,
    )
    return SessionHandle(
        context=context,
        sandbox_url=sandbox_url,
        username=username,
        persona_id=persona_id,
        cache_hit=cache_hit,
        acquired_at=time.time(),
    )


def release_on_logout_hint(
    sandbox_url: str,
    username: str,
    persona_id: str | None,
    error_text: str,
) -> bool:
    """When a downstream caller sees a Playwright error that smells like
    "SF kicked us out", call this to drop the cached session AND its
    on-disk storageState. Next acquire_session will run a full login.

    Returns True iff the error matched a logout hint and we evicted
    cached state.
    """
    msg = (error_text or "").lower()
    if not any(h in msg for h in _SF_LOGOUT_HINTS):
        return False

    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    import pw_mcp_bridge

    pw_mcp_bridge.invalidate_cached_session(
        sandbox_url=sandbox_url,
        username=username,
        persona_id=persona_id,
        drop_storage=True,
    )
    logger.info(
        "pw-mcp evicted session+storage on logout hint: %s (matched in error)",
        sandbox_url,
    )
    return True
