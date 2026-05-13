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
        return list(
            self._paginate(
                f"/rest/agile/1.0/board/{board_id}/sprint",
                items_key="values",
            )
        )

    def list_sprints_for_project(self, project_key: str) -> list[dict[str, Any]]:
        """Convenience: flatten all sprints across every board the project
        is on, deduplicated by sprint id."""
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for board in self.list_boards_for_project(project_key):
            board_id = board.get("id")
            if board_id is None:
                continue
            for sprint in self.list_sprints_for_board(board_id):
                sid = str(sprint.get("id"))
                if sid and sid not in seen:
                    seen.add(sid)
                    out.append(sprint)
        return out

    # ---- issues -------------------------------------------------------

    def search_issues(
        self,
        jql: str,
        *,
        fields: list[str] | None = None,
        expand: list[str] | None = None,
        page_size: int = 100,
    ) -> Iterator[dict[str, Any]]:
        """Yield every issue matching ``jql``. ``fields=['*all']`` returns
        the full body (custom fields included); pass a narrow list when
        you only need summary/status for a list view."""
        params: dict[str, Any] = {
            "jql": jql,
            "fields": ",".join(fields or ["*all"]),
            "maxResults": page_size,
        }
        if expand:
            params["expand"] = ",".join(expand)
        # The new ``/search/jql`` endpoint paginates with a cursor; the
        # legacy ``/search`` still uses startAt and is what most tenants
        # support without opting into the new API. We use the legacy
        # endpoint for broad compatibility.
        start = 0
        while True:
            params["startAt"] = start
            payload = self._get_json("/rest/api/3/search", params=params)
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
