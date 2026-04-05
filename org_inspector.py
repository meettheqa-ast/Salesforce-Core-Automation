"""
OrgInspector — Live Salesforce field discovery via Selenium + REST API.

Authentication strategy (zero extra config required):
  1. Open a short-lived headless Chrome session using existing sandbox credentials.
  2. Log in and wait for the Lightning shell to load.
  3. Navigate to the Salesforce DescribeSObject REST endpoint on the same
     my.salesforce.com domain — the browser carries the session cookie automatically.
  4. Read the JSON body from the page and parse picklist values.
  5. Quit Chrome.

No Connected App / OAuth client credentials needed.
Results are cached per SF object within the inspector instance.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SF_API_VERSION = "v59.0"


class OrgInspectorError(Exception):
    """Raised when the org inspector cannot authenticate or retrieve field data."""


class OrgInspector:
    """Inspect live Salesforce field metadata (picklist values, etc.)."""

    def __init__(self) -> None:
        self._describe_cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_credentials(
        cls,
        sandbox_url: str,
        username: str,
        password: str,
        *,
        progress_cb: Any = None,
    ) -> "OrgInspector":
        """
        Open a headless Chrome session, log in to the Salesforce sandbox,
        and return an OrgInspector with a live authenticated browser session
        ready to query the REST API.

        progress_cb: optional callable(msg: str) — called with status updates
                     (useful for displaying spinners in Streamlit).
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
        except ImportError as exc:
            raise OrgInspectorError(
                f"Selenium is required for live org inspection: {exc}"
            ) from exc

        def _progress(msg: str) -> None:
            if progress_cb:
                progress_cb(msg)

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        driver = webdriver.Chrome(options=options)
        inspector = cls()

        try:
            _progress("Opening sandbox login page…")
            base_url = sandbox_url.rstrip("/")
            driver.get(base_url)

            wait = WebDriverWait(driver, 30)
            wait.until(EC.presence_of_element_located(("id", "username")))

            _progress("Entering credentials…")
            driver.find_element("id", "username").send_keys(username)
            driver.find_element("id", "password").send_keys(password)
            driver.find_element("id", "Login").click()

            _progress("Waiting for Salesforce to load…")
            wait.until(
                EC.presence_of_element_located(
                    ("css selector", "div.slds-global-header__item, div.slds-global-header__logo")
                )
            )

            # Store the driver on the inspector so describe_object() can use it.
            inspector._driver = driver      # type: ignore[attr-defined]
            inspector._base_url = base_url  # type: ignore[attr-defined]
            _progress("Authenticated. Ready to inspect fields.")
            return inspector

        except OrgInspectorError:
            driver.quit()
            raise
        except Exception as exc:
            driver.quit()
            raise OrgInspectorError(
                f"Failed to authenticate with Salesforce for field inspection.\n"
                f"Check credentials and ensure MFA is disabled for this user.\n"
                f"Detail: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Field discovery
    # ------------------------------------------------------------------

    def describe_object(self, sf_object: str) -> dict[str, Any]:
        """
        Fetch /services/data/{version}/sobjects/{sf_object}/describe/ by
        navigating the in-session browser to the REST endpoint and reading
        the JSON body from the page.  Results are cached per object.
        """
        if sf_object in self._describe_cache:
            return self._describe_cache[sf_object]

        driver = getattr(self, "_driver", None)
        base_url = getattr(self, "_base_url", "")
        if driver is None:
            raise OrgInspectorError(
                "OrgInspector was not created via from_credentials(); no browser session."
            )

        describe_url = (
            f"{base_url}/services/data/{SF_API_VERSION}/sobjects/{sf_object}/describe/"
        )
        driver.get(describe_url)
        time.sleep(1.5)  # allow the browser to finish rendering the JSON

        try:
            # Salesforce REST API returns raw JSON in the browser body.
            body_text = driver.find_element("tag name", "body").text
            data = json.loads(body_text)
        except Exception as exc:
            raise OrgInspectorError(
                f"Could not parse Salesforce describe response for {sf_object}.\n"
                f"The session may have expired or the object name may be incorrect.\n"
                f"Detail: {exc}"
            ) from exc

        if "errorCode" in data:
            raise OrgInspectorError(
                f"Salesforce API error for {sf_object}: "
                f"{data.get('errorCode')} — {data.get('message')}"
            )

        self._describe_cache[sf_object] = data
        return data

    def get_picklist_values(
        self,
        sf_object: str,
        field_label_hints: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """
        Return {field_label: [active_value_label, ...]} for all picklist / multipicklist
        fields on the object.  If field_label_hints is provided, only fields whose
        label contains one of the hint strings (case-insensitive) are returned.
        """
        describe = self.describe_object(sf_object)
        picklist_types = {"picklist", "multipicklist"}
        result: dict[str, list[str]] = {}

        for field in describe.get("fields", []):
            if field.get("type") not in picklist_types:
                continue
            label: str = field.get("label", "")
            if field_label_hints:
                if not any(h.lower() in label.lower() for h in field_label_hints):
                    continue
            values = [
                pv["label"]
                for pv in field.get("picklistValues", [])
                if pv.get("active") and pv.get("label")
            ]
            if values:
                result[label] = values

        return result

    def get_smoke_field_context(self, sf_object: str) -> str:
        """
        Return a markdown-formatted string of all active picklist values for the
        given object, ready to be injected into the LLM smoke-test prompt.
        Returns a descriptive error string (not an exception) if inspection fails.
        """
        try:
            picklists = self.get_picklist_values(sf_object)
        except OrgInspectorError as exc:
            return f"_(Field discovery failed: {exc}. Using Open Dropdown And Select First Option as fallback.)_"

        if not picklists:
            return "_(No active picklist fields discovered for this object.)_"

        lines = [
            f"## Live picklist values from your org ({sf_object})",
            "Use these EXACT label strings when calling `Select Dropdown Option`.",
            "For every picklist field not listed here, use `Open Dropdown And Select First Option`.",
            "",
        ]
        for field_label, values in picklists.items():
            display = ", ".join(f'"{v}"' for v in values[:12])
            suffix = f" … ({len(values)} total)" if len(values) > 12 else ""
            lines.append(f"- **{field_label}**: {display}{suffix}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Quit the underlying browser session."""
        driver = getattr(self, "_driver", None)
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
            self._driver = None  # type: ignore[attr-defined]

    def __enter__(self) -> "OrgInspector":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
