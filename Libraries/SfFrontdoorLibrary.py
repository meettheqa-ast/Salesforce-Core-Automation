"""Robot bridge library for resolving a fresh CLI OAuth frontdoor.jsp URL
at the moment each test case actually logs in, instead of once for an
entire (potentially long, multi-test-case) run.

Why: sf_session_bootstrap.get_frontdoor_url() mints/reads a Salesforce
access token and builds a frontdoor.jsp?sid=<token> URL from it. Both
run_test.py (direct CLI runs) and runs.py (portal bulk/stream runs) were
resolving this exactly once, before Robot even starts, and baking the
result into a single ${sandboxFrontdoorUrl} variable shared by every test
case in the run. For a bulk run executing many test cases sequentially,
that token can legitimately go stale (connected-app / session timeout) by
the time a later test case's Login To Sandbox actually runs -- Salesforce
then bounces the frontdoor navigation straight back to the login form,
logged as "access token expired; falling back to username/password login"
even though the CLI's own underlying OAuth grant is still perfectly valid
and a fresh token would work fine.

This keyword re-resolves the token/URL right before each login by calling
back into sf_session_bootstrap directly from Robot.
"""

from __future__ import annotations

import sys
from pathlib import Path

from robot.api import logger
from robot.api.deco import keyword

_REPO_ROOT = Path(__file__).resolve().parent.parent


class SfFrontdoorLibrary:
    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    @keyword("Get Fresh Frontdoor Url")
    def get_fresh_frontdoor_url(self, org_alias: str) -> str:
        """Fetch a brand-new frontdoor.jsp URL for org_alias right now.

        Returns an empty string (never raises) if the alias is blank or the
        CLI/auth lookup fails -- callers should fall back to whatever
        pre-resolved URL (or the username/password path) they already had,
        same as if this keyword didn't exist.
        """
        alias = (org_alias or "").strip()
        if not alias:
            return ""
        if str(_REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(_REPO_ROOT))
        try:
            from sf_session_bootstrap import get_frontdoor_url

            return get_frontdoor_url(alias)
        except Exception as exc:  # noqa: BLE001 -- never fatal, caller falls back
            logger.warn(f"Fresh frontdoor fetch failed for alias '{alias}': {exc}")
            return ""
