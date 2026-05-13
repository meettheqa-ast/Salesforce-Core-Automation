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
        consumer_key: str = "",
        consumer_secret: str = "",
    ) -> None:
        import os

        self._username = username
        self._password = password
        self._security_token = security_token or ""
        self._sandbox_url = sandbox_url.rstrip("/")
        # Connected-App credentials for OAuth 2.0 username-password flow.
        # Fall back to env vars so operators can configure this once
        # without changing every Library import line in every suite.
        self._consumer_key = consumer_key or os.environ.get("SF_CONSUMER_KEY", "")
        self._consumer_secret = consumer_secret or os.environ.get("SF_CONSUMER_SECRET", "")
        self._sf: Salesforce | None = None

    def _connect(self) -> Salesforce:
        """Lazy-connect on first use so import never blocks if creds are empty.

        Auth strategy (in priority order):

        1. **Reuse the active Selenium browser session.** When a SeleniumLibrary
           browser is open and pointed at Salesforce, we grab the ``sid``
           cookie and hand it to ``simple-salesforce`` as
           ``(session_id, instance_url)``. No admin setup, no extra
           credentials, no second login. This is the path that "just
           works" for tests that follow the project's standard pattern
           ``Login To Sandbox`` -> ``API Seed *``.

        2. **OAuth 2.0 password flow** when ``consumer_key`` +
           ``consumer_secret`` are configured (Connected App credentials,
           supplied via ``Library`` args or ``SF_CONSUMER_KEY`` /
           ``SF_CONSUMER_SECRET`` env vars). Required when the org has
           disabled SOAP API login -- which is the modern Salesforce
           default -- and you need to seed BEFORE any UI login.

        3. **SOAP login** (legacy default) when neither of the above
           apply. Works on orgs that haven't disabled SOAP. Will raise
           a clear, actionable RuntimeError when the org rejects SOAP
           login, naming both alternative paths above.
        """
        if self._sf is not None:
            return self._sf

        # Path 1: hijack the live Selenium session if one exists.
        reused = self._try_reuse_selenium_session()
        if reused is not None:
            session_id, instance_url = reused
            logger.info(f"Salesforce API: reusing Selenium session at {instance_url}")
            self._sf = Salesforce(session_id=session_id, instance_url=instance_url)
            return self._sf

        # Path 2: OAuth password flow when a Connected App is configured.
        if self._consumer_key and self._consumer_secret:
            if not self._username or not self._password:
                raise RuntimeError(
                    "SalesforceApiLibrary: OAuth password flow requires "
                    "username and password alongside consumer_key / consumer_secret."
                )
            domain = self._derive_domain()
            logger.info(
                f"Salesforce API: OAuth password flow as {self._username} "
                f"(domain={domain}, consumer_key=...{self._consumer_key[-6:]})"
            )
            try:
                self._sf = Salesforce(
                    username=self._username,
                    password=self._password,
                    consumer_key=self._consumer_key,
                    consumer_secret=self._consumer_secret,
                    domain=domain,
                )
                return self._sf
            except SalesforceAuthenticationFailed as exc:
                raise RuntimeError(
                    f"Salesforce OAuth password flow failed for {self._username}: {exc}. "
                    "Verify the Connected App allows the user, the IP range is permitted, "
                    "and that the consumer_key/consumer_secret are current."
                ) from exc

        # Path 3: SOAP login. The classic default; many newer orgs
        # disable this for security. The error message names the two
        # other paths so the operator has a concrete next step.
        if not self._username or not self._password:
            raise RuntimeError(
                "SalesforceApiLibrary: no auth method available. Either "
                "(a) call ``Login To Sandbox`` BEFORE any API keyword so the "
                "library can reuse the browser session, (b) set ``SF_CONSUMER_KEY`` "
                "and ``SF_CONSUMER_SECRET`` env vars with a Connected App's "
                "credentials, or (c) provide username + password and ensure SOAP "
                "API login is enabled on the org."
            )

        domain = self._derive_domain()
        logger.info(f"Salesforce API: SOAP login as {self._username} (domain={domain})")
        try:
            self._sf = Salesforce(
                username=self._username,
                password=self._password,
                security_token=self._security_token,
                domain=domain,
            )
        except SalesforceAuthenticationFailed as exc:
            msg = str(exc)
            if "SOAP API login() is disabled" in msg:
                raise RuntimeError(
                    "Salesforce SOAP API login is disabled in this org. "
                    "Two ways forward without bothering an admin: "
                    "(1) call ``Login To Sandbox`` BEFORE any API keyword -- "
                    "the library will then reuse the browser's session "
                    "automatically; or (2) set ``SF_CONSUMER_KEY`` + "
                    "``SF_CONSUMER_SECRET`` env vars with a Connected App's "
                    "credentials so the library can use OAuth 2.0 password flow."
                ) from exc
            raise RuntimeError(
                f"Salesforce API login failed for {self._username}: {exc}"
            ) from exc

        logger.info("Salesforce API session established.")
        return self._sf

    def _try_reuse_selenium_session(self) -> tuple[str, str] | None:
        """Inspect the live SeleniumLibrary session for a Salesforce
        session cookie that is valid for ``services/data/`` REST calls.
        Returns ``(session_id, instance_url)`` or ``None``.

        Subtle and important: Salesforce sets ``sid`` on TWO different
        cookie domains after a Lightning login:

          * ``.lightning.force.com``  -- Lightning UI session.
            ``Selenium.get_cookie('sid')`` returns this when the browser
            is parked on a Lightning page. **It is NOT valid for the
            REST API**; passing it to ``simple-salesforce`` produces
            ``INVALID_SESSION_ID`` on the first call.
          * ``.my.salesforce.com``    -- API-eligible session that
            ``services/data/`` accepts. Lightning's own XHR traffic to
            the My Domain causes Salesforce to ``Set-Cookie`` here, so
            usually within a few seconds of login the cookie exists --
            but it lives on a domain Selenium's per-page cookie API
            can't see.

        Strategy:

          1. Use CDP ``Network.getAllCookies`` to enumerate cookies
             across every domain the browser knows about.
          2. Prefer a ``sid`` whose domain matches ``my.salesforce.com``
             -- that's the one ``services/data/`` accepts.
          3. If only a Lightning ``sid`` exists, perform the documented
             ``/secur/frontdoor.jsp?sid=<lightning_sid>`` exchange to
             upgrade it into an API-eligible session, then re-scan.
          4. As a last-ditch, return the Lightning ``sid`` paired with
             the derived My Domain host so the caller still has
             SOMETHING to try (it'll fail loudly with InvalidSessionId,
             which is no worse than today's behaviour).

        Failure paths return ``None`` so ``_connect`` falls through to
        OAuth / SOAP. We never raise -- the entire method is
        best-effort detection.
        """
        try:
            from robot.libraries.BuiltIn import BuiltIn
        except Exception:  # pragma: no cover -- robot is a dep
            return None

        try:
            sl = BuiltIn().get_library_instance("SeleniumLibrary")
        except Exception:
            # SeleniumLibrary not imported in this suite, or BuiltIn
            # called outside a running Robot context.
            return None
        if sl is None:
            return None

        # SeleniumLibrary exposes the active driver via .driver in
        # modern versions; older versions used ._current_browser. Try
        # the public path first.
        driver = None
        try:
            driver = sl.driver
        except Exception:
            try:
                driver = sl._current_browser()  # type: ignore[attr-defined]
            except Exception:
                driver = None
        if driver is None:
            return None

        try:
            current_url = driver.current_url
        except Exception:
            return None
        parsed = urlparse(current_url or "")
        current_host = (parsed.hostname or "").lower()
        if not current_host or (
            "salesforce" not in current_host and "force.com" not in current_host
        ):
            # Browser isn't on Salesforce. Don't try to grab a sid; it
            # would either be missing or belong to some other site.
            return None

        all_cookies = self._collect_all_cookies(driver)
        if not all_cookies:
            return None

        # Step 1: prefer the API-eligible cookie outright.
        api_sid, api_host = self._pick_api_session(all_cookies)

        # Step 2: if only a Lightning sid is visible, upgrade it via
        # frontdoor.jsp. Salesforce returns Set-Cookie on the
        # ``.my.salesforce.com`` domain in that response.
        lightning_sid = None
        lightning_host = None
        if not api_sid:
            lightning_sid, lightning_host = self._pick_lightning_session(all_cookies)
            if lightning_sid:
                my_domain_host = self._derive_my_domain(
                    lightning_host or current_host,
                )
                if my_domain_host:
                    upgraded = self._exchange_via_frontdoor(
                        lightning_sid=lightning_sid,
                        my_domain_host=my_domain_host,
                    )
                    if upgraded:
                        api_sid, api_host = upgraded[0], upgraded[1]

        # Step 3: still nothing useful -> last-ditch with the Lightning
        # sid + derived My Domain host.
        if not api_sid and lightning_sid:
            api_sid = lightning_sid
            api_host = self._derive_my_domain(
                lightning_host or current_host,
            ) or current_host

        if not api_sid:
            return None

        instance_url = f"https://{api_host or current_host}"
        return api_sid, instance_url

    def _collect_all_cookies(self, driver) -> list[dict]:
        """Read cookies across every domain the browser knows about.

        ``Network.getAllCookies`` is a Chrome DevTools call exposed via
        Selenium's ``execute_cdp_cmd``. It returns cookies regardless of
        the page's current origin -- the only way to see
        ``.my.salesforce.com`` cookies while the user is parked on a
        ``.lightning.force.com`` page. Falls back to the standard
        ``get_cookies`` (current-domain only) if CDP is not supported
        (e.g. Firefox / Safari driver).
        """
        try:
            result = driver.execute_cdp_cmd("Network.getAllCookies", {})
            cookies = result.get("cookies") if isinstance(result, dict) else None
            if isinstance(cookies, list) and cookies:
                return cookies
        except Exception:
            pass
        try:
            return list(driver.get_cookies() or [])
        except Exception:
            return []

    @staticmethod
    def _pick_api_session(cookies: list[dict]) -> tuple[str | None, str | None]:
        """Pick the ``sid`` cookie whose domain is the canonical API
        host (``*.my.salesforce.com``). Returns ``(sid, domain)`` or
        ``(None, None)``."""
        for c in cookies:
            if c.get("name") != "sid":
                continue
            dom = (c.get("domain") or "").lstrip(".").lower()
            if "my.salesforce.com" in dom:
                value = c.get("value") or ""
                if value:
                    return value, dom
        return None, None

    @staticmethod
    def _pick_lightning_session(
        cookies: list[dict],
    ) -> tuple[str | None, str | None]:
        """Pick a ``sid`` on a ``.lightning.force.com`` (or any other
        Salesforce-shaped) host. Used as the input to the
        ``frontdoor.jsp`` upgrade."""
        # Prefer Lightning explicitly so we don't mistakenly grab some
        # third-party sid cookie that happens to share the name.
        for c in cookies:
            if c.get("name") != "sid":
                continue
            dom = (c.get("domain") or "").lstrip(".").lower()
            if "lightning.force.com" in dom:
                value = c.get("value") or ""
                if value:
                    return value, dom
        for c in cookies:
            if c.get("name") != "sid":
                continue
            dom = (c.get("domain") or "").lstrip(".").lower()
            if "salesforce.com" in dom or "force.com" in dom:
                value = c.get("value") or ""
                if value:
                    return value, dom
        return None, None

    @staticmethod
    def _derive_my_domain(host: str) -> str:
        """Map a Lightning host to its My Domain peer:

        * ``acme--qa.sandbox.lightning.force.com``
            -> ``acme--qa.sandbox.my.salesforce.com``
        * ``acme.lightning.force.com``
            -> ``acme.my.salesforce.com``

        Returns the input host unchanged when no Lightning suffix is
        present (we may already be on the My Domain) -- the caller is
        responsible for handling the failure case.
        """
        host = (host or "").lower().strip().strip(".")
        if not host:
            return ""
        if host.endswith(".lightning.force.com"):
            return host[: -len(".lightning.force.com")] + ".my.salesforce.com"
        return host

    def _exchange_via_frontdoor(
        self,
        *,
        lightning_sid: str,
        my_domain_host: str,
    ) -> tuple[str, str] | None:
        """Use Salesforce's documented session-bridging endpoint to
        convert a Lightning session into an API-eligible one.

        The flow: GET
        ``https://<my_domain>/secur/frontdoor.jsp?sid=<lightning_sid>&retURL=/``
        with the Lightning ``sid`` already in the request cookies.
        Salesforce validates the Lightning session, then ``Set-Cookie``s
        a fresh ``sid`` on the ``.my.salesforce.com`` domain. Reading
        that cookie from ``response.cookies`` yields the API sid.

        We follow redirects so any subsequent SAML / oauth hops that
        Salesforce throws at us still complete -- but the ``sid`` we
        want is set on the very first response in normal cases.
        Returns ``(api_sid, my_domain_host)`` or ``None`` on failure.
        """
        try:
            import requests
        except ImportError:  # pragma: no cover -- shipped via simple-salesforce
            return None

        url = f"https://{my_domain_host}/secur/frontdoor.jsp"
        session = requests.Session()
        # The cookie domain must NOT include the leading dot for
        # ``requests`` to send it on the request, but Salesforce's
        # cookies store with the dot. Using both forms is harmless.
        session.cookies.set("sid", lightning_sid, domain=my_domain_host, path="/")
        try:
            resp = session.get(
                url,
                params={"sid": lightning_sid, "retURL": "/"},
                timeout=10,
                allow_redirects=True,
            )
        except Exception as exc:  # noqa: BLE001 -- best effort
            logger.debug(f"frontdoor.jsp exchange failed: {exc}")
            return None

        # The session jar is the cleanest way to grab whatever cookies
        # Salesforce ultimately set. We don't care about the response
        # body -- only the new sid.
        candidates = []
        for c in session.cookies:
            if c.name != "sid":
                continue
            dom = (c.domain or "").lstrip(".").lower()
            if "my.salesforce.com" not in dom:
                continue
            candidates.append((c.value, dom))
        if not candidates:
            logger.debug(
                "frontdoor.jsp: no my.salesforce.com sid cookie in response "
                f"(status={resp.status_code}, final_url={resp.url})"
            )
            return None
        api_sid, dom = candidates[0]
        if api_sid and api_sid != lightning_sid:
            logger.info(f"Salesforce API: upgraded Lightning session via frontdoor.jsp at {dom}")
        return api_sid, dom

    def _derive_domain(self) -> str:
        """
        Determine the ``simple-salesforce`` *domain* parameter from the sandbox URL.

        ``simple-salesforce`` builds its OAuth/SOAP endpoint as
        ``https://{domain}.salesforce.com/...`` -- so ``domain`` must
        be either the literal token ``"test"`` / ``"login"`` (it then
        uses the standard endpoints) OR the **prefix** of a custom
        ``my.salesforce.com`` URL. **Never** a full hostname that
        already contains ``.salesforce.com`` or ``.lightning.force.com``,
        because then the resolver tacks ``.salesforce.com`` onto the end
        and you get NXDOMAIN.

        Recognised inputs:

        * Sandboxes (any of these clues -> ``"test"``):
          - ``*.sandbox.my.salesforce.com``
          - ``*.sandbox.lightning.force.com``           (Lightning)
          - any hostname containing ``.sandbox.``      (defensive)
          - ``test.salesforce.com``
        * Production (``"login"``):
          - ``*.my.salesforce.com`` (non-sandbox)
          - ``*.lightning.force.com`` (non-sandbox)
          - ``login.salesforce.com``
        * Empty / unknown -> ``"test"`` (safer default; QA work always
          targets sandboxes).
        """
        if not self._sandbox_url:
            return "test"

        host = urlparse(self._sandbox_url).hostname or self._sandbox_url
        host = host.lower().rstrip(".")

        # Direct OAuth endpoints -- pass through.
        if host in ("test.salesforce.com", "login.salesforce.com"):
            return host.split(".", 1)[0]  # "test" or "login"

        # Sandbox detection. The ``.sandbox.`` substring is the universal
        # tell across ``my.salesforce.com`` and ``lightning.force.com``
        # variants; we also accept the legacy ``--<name>`` prefix shape.
        if ".sandbox." in host:
            return "test"
        if "--" in host.split(".", 1)[0]:
            # ``orgname--sandboxname.lightning.force.com`` is a sandbox
            # even when ``.sandbox.`` is absent (older URL shapes).
            return "test"

        # Production hosts. Both ``my.salesforce.com`` and the Lightning
        # ``lightning.force.com`` route auth through ``login.salesforce.com``.
        if host.endswith(".my.salesforce.com") or host.endswith(".lightning.force.com"):
            return "login"

        # Genuinely custom / unrecognised host. Returning ``"test"``
        # rather than the raw hostname avoids the
        # ``<host>.salesforce.com`` mis-concatenation simple-salesforce
        # would otherwise produce. Operators with bespoke OAuth
        # endpoints can override by exporting ``SF_DOMAIN`` (handled
        # upstream in the runner) or by passing a sandbox URL of the
        # ``*.sandbox.*`` shape.
        return "test"

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
