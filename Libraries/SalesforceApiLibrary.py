"""
Robot Framework library for Salesforce REST API data seeding.

Uses ``simple-salesforce`` to authenticate against a Salesforce org and exposes
Robot keywords for creating / deleting records so that test data can be seeded
instantly via the API rather than through slow UI interactions.

Usage in Robot (via GlobalApi.robot):
    Library    ../Libraries/SalesforceApiLibrary.py
    ...        username=${sandboxUserNameInput}
    ...        password=${sandboxPasswordInput}
    ...        security_token=${sandboxSecurityToken}
    ...        sandbox_url=${globalSandboxTestUrl}
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from robot.api import logger
from robot.api.deco import keyword

try:
    from simple_salesforce import Salesforce, SalesforceAuthenticationFailed
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "simple-salesforce is required for SalesforceApiLibrary. "
        "Install it with: pip install simple-salesforce"
    ) from _exc


class SalesforceApiLibrary:
    """Authenticate to Salesforce and provide CRUD keywords for data seeding."""

    ROBOT_LIBRARY_SCOPE = "SUITE"

    def __init__(
        self,
        username: str = "",
        password: str = "",
        security_token: str = "",
        sandbox_url: str = "",
    ) -> None:
        self._username = username
        self._password = password
        self._security_token = security_token or ""
        self._sandbox_url = sandbox_url.rstrip("/")
        self._sf: Salesforce | None = None

    def _connect(self) -> Salesforce:
        """Lazy-connect on first use so import never blocks if creds are empty."""
        if self._sf is not None:
            return self._sf

        if not self._username or not self._password:
            raise RuntimeError(
                "SalesforceApiLibrary: username and password are required for API authentication."
            )

        domain = self._derive_domain()
        logger.info(f"Connecting to Salesforce API as {self._username} (domain={domain})")

        try:
            self._sf = Salesforce(
                username=self._username,
                password=self._password,
                security_token=self._security_token,
                domain=domain,
            )
        except SalesforceAuthenticationFailed as exc:
            raise RuntimeError(
                f"Salesforce API login failed for {self._username}: {exc}"
            ) from exc

        logger.info("Salesforce API session established.")
        return self._sf

    def _derive_domain(self) -> str:
        """
        Determine the ``simple-salesforce`` *domain* parameter from the sandbox URL.

        - Standard sandboxes (``*.sandbox.my.salesforce.com``) → ``"test"``
        - Production (``*.my.salesforce.com``) → ``"login"``
        - Custom domains → the full hostname is passed as the domain
        """
        if not self._sandbox_url:
            return "test"

        host = urlparse(self._sandbox_url).hostname or self._sandbox_url
        host = host.lower().rstrip(".")

        if re.search(r"\.sandbox\.my\.salesforce\.com$", host):
            return "test"
        if host.endswith(".my.salesforce.com"):
            return "login"
        return host

    @keyword("API Create Record")
    def api_create_record(self, object_name: str, **fields) -> str:
        """
        Create a single Salesforce record via the REST API.

        Arguments:
            - ``object_name``: SObject API name (e.g. ``Lead``, ``Account``, ``Contact``).
            - ``**fields``: Keyword arguments mapping field API names to values.

        Returns the new record's 18-character Salesforce ID.

        Example (Robot):
            ${id}=    API Create Record    Lead    LastName=AutoBot    Company=Acme
        """
        sf = self._connect()
        sobject = getattr(sf, object_name)
        logger.info(f"API Create Record: {object_name} with {fields}")
        result = sobject.create(fields)

        if not result.get("success"):
            errors = result.get("errors", [])
            raise RuntimeError(
                f"API Create Record failed for {object_name}: {errors}"
            )

        record_id = result["id"]
        logger.info(f"Created {object_name} record: {record_id}")
        return record_id

    @keyword("API Delete Record")
    def api_delete_record(self, object_name: str, record_id: str) -> None:
        """
        Delete a Salesforce record via the REST API.

        Arguments:
            - ``object_name``: SObject API name (e.g. ``Lead``, ``Account``).
            - ``record_id``: The 15- or 18-character Salesforce record ID.

        Example (Robot):
            API Delete Record    Lead    ${lead_id}
        """
        sf = self._connect()
        sobject = getattr(sf, object_name)
        logger.info(f"API Delete Record: {object_name} / {record_id}")
        sobject.delete(record_id)
        logger.info(f"Deleted {object_name} record: {record_id}")

    @keyword("API Query Records")
    def api_query_records(self, soql: str) -> list[dict]:
        """
        Execute a SOQL query and return a list of record dicts.

        Arguments:
            - ``soql``: A valid SOQL query string.

        Returns a list of dictionaries (one per record). Each dict contains the
        queried fields plus ``Id`` and ``attributes``.

        Example (Robot):
            ${rows}=    API Query Records    SELECT Id, Name FROM Account LIMIT 5
        """
        sf = self._connect()
        logger.info(f"API Query: {soql}")
        result = sf.query_all(soql)
        records = result.get("records", [])
        logger.info(f"Query returned {len(records)} record(s).")
        return records
