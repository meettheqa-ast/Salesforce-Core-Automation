"""Jira Cloud REST client.

Talks to:

* ``/rest/api/3/myself`` for connection tests.
* ``/rest/api/3/project/search`` (paginated) to enumerate projects.
* ``/rest/agile/1.0/board?projectKeyOrId=`` -> ``/sprint`` to enumerate sprints.
* ``/rest/api/3/search`` (JQL) to enumerate issues with ``expand=renderedFields,
  changelog,names`` so we keep both the formatted ADF body and a normalised
  text rendering.
* ``/rest/api/3/issue/{key}/comment`` for per-issue comments.

Auth is HTTP Basic with ``email:api_token``. Generate tokens at
https://id.atlassian.com/manage-profile/security/api-tokens. The provider
is intentionally synchronous and thread-safe so the Jira sync orchestrator
(see :mod:`ai_qa_portal.backend.services.jira_sync`) can call it from a
background thread without pulling in asyncio.

The class wraps a single ``httpx.Client`` per instance so connections are
reused across the dozens of calls a single sprint sync makes.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import httpx

logger = logging.getLogger("ai_qa_portal.integrations.jira")

# Conservative defaults; Atlassian's documented quota is 500 req / 5min /
# IP, plus per-endpoint caps. We stay well under by serialising calls and
# obeying ``Retry-After`` on 429.
_DEFAULT_TIMEOUT_S = 30.0
_MAX_RETRIES = 5
_BACKOFF_BASE_S = 1.0


class JiraAuthError(RuntimeError):
    """401/403 from Jira -- token wrong, expired, or insufficient scope."""


class JiraRateLimitError(RuntimeError):
    """Repeated 429s exhausted our retry budget."""


class JiraProvider:
    """Pull-side Jira integration.

    The protocol allows push methods too (``push_test_case_result``,
    ``push_story_status``); we leave those raising ``NotImplementedError``
    for v1 since the connected scope is read-only.
    """

    source_name: str = "jira"

    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        api_token: str,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not base_url or not email or not api_token:
            raise ValueError("JiraProvider requires base_url, email, and api_token")
        self.base_url = base_url.rstrip("/")
        self._auth = (email, api_token)
        self._client = httpx.Client(
            base_url=self.base_url,
            auth=self._auth,
            timeout=timeout_s,
            headers={"Accept": "application/json"},
        )

    # ---- lifecycle ----------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JiraProvider":
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()

    # ---- low-level HTTP ----------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Single request with 429-aware retry. Raises on persistent
        failure; auth errors become :class:`JiraAuthError` so the router
        can surface them as 401 to the UI.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt + 1 >= _MAX_RETRIES:
                    raise
                time.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                continue
            if resp.status_code in (401, 403):
                raise JiraAuthError(
                    f"Jira {resp.status_code} on {method} {path}: {resp.text[:200]}"
                )
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", _BACKOFF_BASE_S * (2 ** attempt)))
                logger.warning("Jira 429 on %s %s -- sleeping %.1fs", method, path, retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                if attempt + 1 >= _MAX_RETRIES:
                    resp.raise_for_status()
                logger.warning("Jira %d on %s %s -- backing off", resp.status_code, method, path)
                time.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                continue
            resp.raise_for_status()
            return resp
        raise JiraRateLimitError(f"Exhausted retries on {method} {path}: {last_exc}")

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params).json()

    def _paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        items_key: str = "values",
        page_size: int = 50,
    ) -> Iterator[dict[str, Any]]:
        """Generic ``startAt`` / ``maxResults`` pagination shared by the
        Agile, project, and board endpoints. Yields one item at a time so
        callers can stream into the DB without buffering the full result."""
        params = dict(params or {})
        params.setdefault("maxResults", page_size)
        start = 0
        while True:
            params["startAt"] = start
            payload = self._get_json(path, params=params)
            items = payload.get(items_key, [])
            if not items:
                return
            for item in items:
                yield item
            if payload.get("isLast"):
                return
            received = len(items)
            if received < params["maxResults"]:
                return
            start += received

    # ---- connection ---------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        """Cheap auth probe. Returns the authenticated user's profile."""
        return self._get_json("/rest/api/3/myself")

    # ---- projects -----------------------------------------------------

    def list_projects(self) -> list[dict[str, Any]]:
        """All projects visible to the authenticated user.

        Uses ``/rest/api/3/project/search`` which is the supported
        replacement for the legacy ``/project`` endpoint.
        """
        return list(self._paginate("/rest/api/3/project/search", items_key="values"))

    def get_project(self, key: str) -> dict[str, Any]:
        return self._get_json(f"/rest/api/3/project/{key}")

    # ---- sprints ------------------------------------------------------

    def list_boards_for_project(self, project_key: str) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                "/rest/agile/1.0/board",
                params={"projectKeyOrId": project_key},
                items_key="values",
            )
        )

    def list_sprints_for_board(self, board_id: int | str) -> list[dict[str, Any]]:
        """Return every sprint on a board. Kanban / Service Management
        boards have no sprints and Atlassian returns ``400 Bad Request``
        with body ``The board does not support sprints`` for those. We
        treat that 400 as "no sprints" and return ``[]`` rather than
        letting it abort the whole sync -- callers like
        :meth:`list_sprints_for_project` iterate over many boards and
        one Kanban board in the mix shouldn't kill the run."""
        try:
            return list(
                self._paginate(
                    f"/rest/agile/1.0/board/{board_id}/sprint",
                    items_key="values",
                )
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                logger.info(
                    "Board %s does not support sprints (Kanban / SM / WM); skipping.",
                    board_id,
                )
                return []
            raise

    def list_sprints_for_project(self, project_key: str) -> list[dict[str, Any]]:
        """Convenience: flatten all sprints across every Scrum board the
        project is on, deduplicated by sprint id.

        Boards whose ``type`` is not ``scrum`` (e.g. Kanban, Service
        Management queues, Jira Work Management business projects) have
        no sprints, so we skip them up-front rather than firing a doomed
        ``/sprint`` request. :meth:`list_sprints_for_board` still has a
        defensive 400 catch for the edge case where a board reports
        ``type=scrum`` but Atlassian still rejects ``/sprint``."""
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for board in self.list_boards_for_project(project_key):
            board_id = board.get("id")
            board_type = str(board.get("type") or "").strip().lower()
            if board_id is None:
                continue
            if board_type and board_type != "scrum":
                logger.info(
                    "Skipping non-Scrum board %s (type=%s) for project %s.",
                    board_id, board_type, project_key,
                )
                continue
            for sprint in self.list_sprints_for_board(board_id):
                sid = str(sprint.get("id"))
                if sid and sid not in seen:
                    seen.add(sid)
                    out.append(sprint)
        return out

    # ---- issues -------------------------------------------------------

    # Fields requested by the sync layer when the caller does not pass
    # an explicit list. The new ``/rest/api/3/search/jql`` endpoint does
    # NOT honour ``fields=['*all']`` -- it returns a 400 -- so we need
    # an explicit allow-list. This set is the minimum
    # :mod:`ai_qa_portal.backend.services.jira_sync` reads from each
    # issue; customfield_10020 is the standard Sprint custom field, and
    # ``_extract_sprint_jira_id`` already handles tenants that use a
    # different ``customfield_*`` id by scanning every custom field.
    _DEFAULT_ISSUE_FIELDS: tuple[str, ...] = (
        "summary",
        "description",
        "status",
        "issuetype",
        "assignee",
        "reporter",
        "priority",
        "labels",
        "created",
        "updated",
        "parent",
        "customfield_10020",
    )

    def search_issues(
        self,
        jql: str,
        *,
        fields: list[str] | None = None,
        expand: list[str] | None = None,
        page_size: int = 100,
    ) -> Iterator[dict[str, Any]]:
        """Yield every issue matching ``jql``.

        Atlassian sunset the legacy ``/rest/api/3/search`` (both GET and
        POST) on 1 May 2025 and replaced it with
        ``/rest/api/3/search/jql``, which uses **cursor pagination**
        (``nextPageToken``) instead of ``startAt``/``total``. We hit the
        new endpoint first; on 404/405 (Data Center / self-hosted
        tenants that still expose only the legacy path) we fall back to
        the old endpoint with its original startAt/POST-on-410 chain.

        ``fields=['*all']`` is only forwarded to the legacy fallback --
        the new endpoint requires an explicit list, so we use
        :data:`_DEFAULT_ISSUE_FIELDS` when the caller did not supply one.
        """
        fields_list = list(fields) if fields else list(self._DEFAULT_ISSUE_FIELDS)
        fields_csv = ",".join(fields_list)
        expand_list = list(expand) if expand else []
        expand_csv = ",".join(expand_list)

        # Track how many issues we have already yielded from the cursor
        # endpoint so we don't double-emit via the legacy fallback if the
        # new endpoint dies mid-stream. The only legitimate fallback
        # trigger is "endpoint does not exist on this tenant", which
        # surfaces on the very first request (yielded == 0). A 404/405
        # AFTER we've already streamed data is a real error and we
        # propagate it.
        yielded = 0
        try:
            for issue in self._search_issues_via_jql_cursor(
                jql=jql,
                fields_csv=fields_csv,
                expand_csv=expand_csv,
                page_size=page_size,
            ):
                yielded += 1
                yield issue
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (404, 405) or yielded > 0:
                raise
            logger.info(
                "Jira /search/jql returned %d on first call; falling back to legacy /search",
                exc.response.status_code,
            )

        yield from self._search_issues_via_legacy(
            jql=jql,
            fields_list=fields_list,
            fields_csv=fields_csv,
            expand_list=expand_list,
            expand_csv=expand_csv,
            page_size=page_size,
        )

    def _search_issues_via_jql_cursor(
        self,
        *,
        jql: str,
        fields_csv: str,
        expand_csv: str,
        page_size: int,
    ) -> Iterator[dict[str, Any]]:
        """Cursor-paginated search against the new ``/rest/api/3/search/jql``."""
        next_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "jql": jql,
                "fields": fields_csv,
                "maxResults": page_size,
            }
            if expand_csv:
                params["expand"] = expand_csv
            if next_token:
                params["nextPageToken"] = next_token
            payload = self._get_json("/rest/api/3/search/jql", params=params)
            issues = payload.get("issues", [])
            if not issues:
                return
            for issue in issues:
                yield issue
            if payload.get("isLast"):
                return
            next_token = payload.get("nextPageToken")
            if not next_token:
                return

    def _search_issues_via_legacy(
        self,
        *,
        jql: str,
        fields_list: list[str],
        fields_csv: str,
        expand_list: list[str],
        expand_csv: str,
        page_size: int,
    ) -> Iterator[dict[str, Any]]:
        """Legacy ``startAt``/``maxResults`` path against ``/rest/api/3/search``,
        with the original GET-then-POST-on-410 fallback for older tenants
        that rejected GET. Kept for self-hosted Data Center installs that
        haven't shipped the cursor endpoint."""
        start = 0
        while True:
            params: dict[str, Any] = {
                "jql": jql,
                "fields": fields_csv,
                "maxResults": page_size,
                "startAt": start,
            }
            if expand_csv:
                params["expand"] = expand_csv
            try:
                payload = self._get_json("/rest/api/3/search", params=params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 410:
                    raise
                logger.info(
                    "Jira GET /search returned 410; retrying via POST /search"
                )
                body: dict[str, Any] = {
                    "jql": jql,
                    "fields": fields_list,
                    "maxResults": page_size,
                    "startAt": start,
                }
                if expand_list:
                    body["expand"] = expand_list
                payload = self._request("POST", "/rest/api/3/search", json=body).json()
            issues = payload.get("issues", [])
            if not issues:
                return
            for issue in issues:
                yield issue
            received = len(issues)
            total = int(payload.get("total", 0))
            start += received
            if start >= total or received < page_size:
                return

    def list_comments(self, issue_key_or_id: str) -> list[dict[str, Any]]:
        """All comments on a single issue. Paginated under ``comments``."""
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            payload = self._get_json(
                f"/rest/api/3/issue/{issue_key_or_id}/comment",
                params={"startAt": start, "maxResults": 100, "expand": "renderedBody"},
            )
            items = payload.get("comments", [])
            if not items:
                break
            out.extend(items)
            start += len(items)
            if start >= int(payload.get("total", 0)):
                break
        return out

    # ---- ExternalIssueProvider protocol -------------------------------
    # The protocol expects shape-aligned dicts; concrete normalisation
    # to portal models lives in ``services.jira_sync``. These wrappers
    # exist so the registry's typing stays satisfied.

    def fetch_sprints(self, project_external_id: str) -> list[dict[str, Any]]:
        return self.list_sprints_for_project(project_external_id)

    def fetch_stories_for_sprint(self, sprint_external_id: str) -> list[dict[str, Any]]:
        return list(
            self.search_issues(
                jql=f"sprint = {sprint_external_id} AND issuetype in (Story, Task, Bug)"
            )
        )

    def fetch_test_cases_for_story(self, story_external_id: str) -> list[dict[str, Any]]:
        return list(
            self.search_issues(jql=f"parent = {story_external_id} AND issuetype = Sub-task")
        )

    def push_test_case_result(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("Jira write-back is not enabled in v1.")

    def push_story_status(self, *_args: Any, **_kwargs: Any) -> None:
        raise NotImplementedError("Jira write-back is not enabled in v1.")
