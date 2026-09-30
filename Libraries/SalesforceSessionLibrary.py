"""
Robot Framework library: Salesforce CLI session → UI frontdoor jump URL.

CumulusCI-style login bypass for Robot/Selenium runs. After the operator
authenticates once with ``sf org login web`` (fingerprint / MFA in that
browser), this library reads the stored access token and builds a
``/secur/frontdoor.jsp?sid=...`` URL so automated Chrome never touches
the multi-step login form.

Alias resolution (same convention as ``sf_dx_bridge`` / Org Inspector):

1. Explicit ``org_alias`` keyword argument
2. Robot variable ``${SF_DX_ORG_ALIAS}`` (when set and non-empty)
3. Environment ``SF_DX_ORG_ALIAS``
4. Fallback ``DEFAULT_TARGET_ORG``
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any
from urllib.parse import quote, urlparse

from robot.api import logger
from robot.api.deco import keyword
from robot.libraries.BuiltIn import BuiltIn

_DEFAULT_ORG_ALIAS = "DEFAULT_TARGET_ORG"
_SF_DISPLAY_TIMEOUT_S = 45


class SalesforceSessionLibrary:
    """Expose CLI org session data for Robot UI login via frontdoor.jsp."""

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    @keyword("Resolve Sf Org Alias")
    def resolve_sf_org_alias(self, org_alias: str = "") -> str:
        """Return the effective Salesforce CLI org alias."""
        explicit = (org_alias or "").strip()
        if explicit:
            return explicit
        try:
            from_robot = str(BuiltIn().get_variable_value("${SF_DX_ORG_ALIAS}", "") or "").strip()
        except Exception:  # noqa: BLE001 — BuiltIn unavailable outside Robot
            from_robot = ""
        if from_robot:
            return from_robot
        return os.environ.get("SF_DX_ORG_ALIAS", _DEFAULT_ORG_ALIAS).strip() or _DEFAULT_ORG_ALIAS

    @keyword("Cli Org Is Authenticated")
    def cli_org_is_authenticated(self, org_alias: str = "") -> bool:
        """Return True when ``sf org display`` yields a usable access token."""
        try:
            self._org_display(org_alias)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.info(f"CLI org not authenticated for frontdoor: {exc}")
            return False

    @keyword("Get Frontdoor Url")
    def get_frontdoor_url(self, org_alias: str = "", ret_url: str = "/lightning/page/home") -> str:
        """Build ``{instanceUrl}/secur/frontdoor.jsp?sid={accessToken}&retURL=...``.

        Raises ``RuntimeError`` when the CLI is missing, the org alias is
        not authenticated, or the display payload lacks token/URL fields.
        """
        result = self._org_display(org_alias)
        access_token = (result.get("accessToken") or result.get("access_token") or "").strip()
        instance_url = (result.get("instanceUrl") or result.get("instance_url") or "").strip().rstrip("/")
        if not access_token or not instance_url:
            raise RuntimeError(
                "sf org display did not return accessToken/instanceUrl. "
                "Run: sf org login web --alias <alias>"
            )
        # Prefer my.salesforce.com host when present (Lightning-friendly).
        parsed = urlparse(instance_url)
        if not parsed.scheme or not parsed.netloc:
            raise RuntimeError(f"Invalid instanceUrl from sf org display: {instance_url!r}")
        base = f"{parsed.scheme}://{parsed.netloc}"
        ret = (ret_url or "/lightning/page/home").strip() or "/lightning/page/home"
        if not ret.startswith("/"):
            ret = "/" + ret
        url = (
            f"{base}/secur/frontdoor.jsp"
            f"?sid={quote(access_token, safe='')}"
            f"&retURL={quote(ret, safe='/')}"
        )
        logger.info(f"Built frontdoor URL for host {parsed.netloc} (token redacted)")
        return url

    def _org_display(self, org_alias: str = "") -> dict[str, Any]:
        alias = self.resolve_sf_org_alias(org_alias)
        sf = shutil.which("sf")
        if not sf:
            raise RuntimeError(
                "Salesforce CLI (`sf`) not found on PATH. "
                "Install SF CLI and run: sf org login web --alias " + alias
            )
        cmd = [sf, "org", "display", "--target-org", alias, "--json"]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_SF_DISPLAY_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"sf org display timed out for alias {alias!r}") from exc

        raw = (completed.stdout or "").strip() or (completed.stderr or "").strip()
        if not raw:
            raise RuntimeError(
                f"sf org display returned empty output for alias {alias!r} "
                f"(exit {completed.returncode}). Run: sf org login web --alias {alias}"
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"sf org display returned non-JSON for alias {alias!r}: {raw[:200]}"
            ) from exc

        status = payload.get("status", 0)
        result = payload.get("result") if isinstance(payload.get("result"), dict) else None
        if status not in (0, "0") or not result:
            message = payload.get("message") or payload.get("name") or raw[:300]
            raise RuntimeError(
                f"sf org display failed for alias {alias!r}: {message}. "
                f"Run: sf org login web --alias {alias}"
            )
        return result
