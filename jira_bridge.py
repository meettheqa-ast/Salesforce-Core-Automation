"""
Jira Cloud / Server bridge — browse projects, search issues, and pull full
user-story context (summary, description, comments, attachments) into the
Test Intelligence Platform.

Ported from the "Browse user stories" feature of the Test-artifact-agent
portal (src/lib/jira*.ts) onto this app's Python/Streamlit stack. The
browser-side proxy that project used to dodge Atlassian's CORS policy isn't
needed here — Streamlit calls Jira's REST API directly, server-side.

Auth:
  * Jira Cloud (recommended) — HTTP Basic with Atlassian account email +
    API token (https://id.atlassian.com/manage-profile/security/api-tokens).
  * Jira Server / Data Center — leave "email" blank and put a Personal
    Access Token in "api_token"; requests then use ``Authorization: Bearer``,
    matching the existing ``publish_results_to_zephyr`` convention in
    app_reporting.py.

Pure Python — no Streamlit dependency, so it's easy to unit test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_TIMEOUT_S = 20
_RECENT_WINDOW = "updated >= -90d"


@dataclass
class JiraSettings:
    site_url: str = ""
    email: str = ""
    api_token: str = ""
    project_key: str = ""

    @property
    def base(self) -> str:
        return (self.site_url or "").rstrip("/")


# ---------------------------------------------------------------------------
# Validation & auth
# ---------------------------------------------------------------------------

def validate_settings(settings: JiraSettings) -> str | None:
    """Return a human-readable error, or None if settings look usable."""
    if not settings.api_token.strip():
        return "Add a Jira API token (Cloud) or Personal Access Token (Server) in Project Settings."
    if not settings.base:
        return "Add a Jira Site URL in Project Settings (e.g. https://yourcompany.atlassian.net)."
    if not settings.base.startswith("https://") and not settings.base.startswith("http://"):
        return "Site URL must start with https:// (or http:// for an on-prem Jira)."
    return None


def _auth_headers(settings: JiraSettings) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if settings.email.strip():
        # Jira Cloud: HTTP Basic (email:api_token) — requests handles the
        # base64 encoding when we pass `auth=`, so this header path is only
        # used by callers that build the request manually.
        return headers
    headers["Authorization"] = f"Bearer {settings.api_token.strip()}"
    return headers


def _request(
    settings: JiraSettings,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[bool, Any, int]:
    """Low-level authenticated call. Returns (ok, data_or_error_string, status)."""
    err = validate_settings(settings)
    if err:
        return False, err, 0

    url = f"{settings.base}{path if path.startswith('/') else '/' + path}"
    auth = (settings.email.strip(), settings.api_token.strip()) if settings.email.strip() else None
    headers = _auth_headers(settings)

    try:
        resp = requests.request(
            method,
            url,
            headers=headers,
            auth=auth,
            params=params,
            json=json_body,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        return False, f"Could not reach Jira ({exc}). Check the Site URL and network connection.", 0

    if not resp.ok:
        return False, parse_error_body(resp.status_code, resp.text), resp.status_code

    if not resp.text.strip():
        return True, {}, resp.status_code
    try:
        return True, resp.json(), resp.status_code
    except ValueError:
        return False, "Invalid JSON from Jira. Check the Site URL and permissions.", resp.status_code


def parse_error_body(status: int, raw: str) -> str:
    """Normalise Jira's ``{errorMessages, errors, message}`` (or plain-text) error bodies."""
    text = (raw or "").strip()
    if not text:
        return f"HTTP {status}"
    try:
        import json

        body = json.loads(text)
    except ValueError:
        return f"{status}: {text[:400]}"

    parts: list[str] = []
    for msg in body.get("errorMessages") or []:
        if isinstance(msg, str) and msg.strip():
            parts.append(msg.strip())
    errors = body.get("errors")
    if isinstance(errors, dict):
        for field, msg in errors.items():
            if isinstance(msg, str) and msg.strip():
                parts.append(f"{field}: {msg.strip()}")
    message = body.get("message")
    if isinstance(message, str) and message.strip():
        parts.append(message.strip())
    if parts:
        return f"{status}: " + "; ".join(parts)[:500]
    return f"{status}: {text[:400]}"


def test_connection(settings: JiraSettings) -> tuple[bool, str]:
    """Quick auth check via ``/rest/api/3/myself``. Returns (ok, message)."""
    ok, data, status = _request(settings, "GET", "/rest/api/3/myself")
    if not ok:
        return False, str(data)
    name = (data or {}).get("displayName") or (data or {}).get("emailAddress") or "Jira"
    return True, f"Connected as {name}."


# ---------------------------------------------------------------------------
# ADF (Atlassian Document Format) → plain text
# ---------------------------------------------------------------------------

def adf_to_plain(adf: Any) -> str:
    """Minimal ADF → plain-text walker (mirrors adfToPlain in jiraIssue.ts)."""
    if adf is None:
        return ""
    if isinstance(adf, str):
        return adf

    def walk(node: Any) -> str:
        if not isinstance(node, dict):
            return ""
        node_type = node.get("type")
        if node_type == "text":
            return str(node.get("text") or "")
        if node_type == "hardBreak":
            return "\n"
        content = node.get("content")
        if isinstance(content, list):
            joiner = "" if node_type != "paragraph" else ""
            return joiner.join(walk(child) for child in content)
        return ""

    if isinstance(adf, dict) and adf.get("type") == "doc" and isinstance(adf.get("content"), list):
        return "\n".join(walk(child) for child in adf["content"]).strip()
    return walk(adf)


# ---------------------------------------------------------------------------
# Browse / search
# ---------------------------------------------------------------------------

def _escape_jql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


_FULL_JQL_RE = re.compile(
    # Leading \b anchors each clause to a word start; no trailing \b on the
    # operator-suffixed clauses since "=", "~", "<", ">", "!" are non-word
    # chars and \b can never match between two non-word characters (that
    # bug is why the upstream TS heuristic silently only matched "order by"
    # / "issuetype" in practice — fixed here so "project = X" etc. also count).
    r"(\border\s+by\b|\bproject\s*=|\bissuetype\b|\bupdated\s*[<>=!]|\bstatus\s*=|\bassignee\s*=|\bsummary\s*~)",
    re.IGNORECASE,
)


def _looks_like_full_jql(q: str) -> bool:
    return bool(_FULL_JQL_RE.search(q))


def _project_scoped_base(project_key: str) -> str:
    key = project_key.strip().upper()
    return f"project = {key} AND {_RECENT_WINDOW} AND issuetype != Epic"


BROWSE_STORIES_NEED_PROJECT_HINT = (
    "Enter a project key (e.g. QA) to list recent stories, or load a Jira issue "
    "first so we know your project."
)


def resolve_issue_key(query: str, default_project: str) -> str | None:
    """Turn a search box value into a Jira issue key when the user clearly meant one.

    Examples (default project SCRUM):
      ``SCRUM-5`` → ``SCRUM-5``
      ``5``       → ``SCRUM-5``   (bare number + project key)
      ``login``   → None          (keyword search, not a key)
    """
    q = (query or "").strip()
    if not q:
        return None
    full = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)-(\d+)", q)
    if full:
        return f"{full.group(1).upper()}-{full.group(2)}"
    if re.fullmatch(r"\d+", q) and default_project.strip():
        return f"{default_project.strip().upper()}-{q}"
    return None


def build_browse_jql(query: str, default_project: str) -> str | None:
    """Build a bounded JQL string for the story browser. None = don't call Jira yet."""
    q = (query or "").strip()

    if q and _looks_like_full_jql(q):
        return q

    if not q:
        if not default_project.strip():
            return None
        return f"{_project_scoped_base(default_project)} ORDER BY updated DESC"

    # Issue key (PROJ-123) or bare number ("5" → SCRUM-5 when project is set).
    # Key lookups skip the 90-day window — older stories must still be findable.
    issue_key = resolve_issue_key(q, default_project)
    if issue_key:
        return f'key = {issue_key} ORDER BY key'

    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", q):
        return f"{_project_scoped_base(q)} ORDER BY updated DESC"

    project = default_project.strip().upper()
    if not project:
        return None
    return f'{_project_scoped_base(project)} AND summary ~ "{_escape_jql_string(q)}" ORDER BY updated DESC'


def search_issues(
    settings: JiraSettings, jql: str, max_results: int = 50
) -> tuple[list[dict[str, str]], str | None]:
    """Run a JQL search. Returns (rows, error). Each row has key/summary/issue_type/status/updated."""
    fields = ["summary", "issuetype", "status", "updated"]

    # Jira Cloud's newer POST /rest/api/3/search/jql endpoint replaced the
    # GET /rest/api/3/search endpoint (which now 410s on some sites).
    ok, data, status = _request(
        settings,
        "POST",
        "/rest/api/3/search/jql",
        json_body={"jql": jql, "fields": fields, "maxResults": max_results},
    )
    if not ok and status in (404, 410):
        ok, data, status = _request(
            settings,
            "GET",
            "/rest/api/3/search",
            params={"jql": jql, "fields": ",".join(fields), "maxResults": max_results},
        )
    if not ok:
        return [], str(data)

    rows: list[dict[str, str]] = []
    for issue in (data or {}).get("issues", []):
        f = issue.get("fields") or {}
        key = str(issue.get("key") or "").upper()
        if not key:
            continue
        rows.append(
            {
                "key": key,
                "summary": f.get("summary") or "",
                "issue_type": (f.get("issuetype") or {}).get("name") or "",
                "status": (f.get("status") or {}).get("name") or "",
                "updated": f.get("updated") or "",
            }
        )
    return rows, None


# ---------------------------------------------------------------------------
# Full issue fetch
# ---------------------------------------------------------------------------

def fetch_issue(settings: JiraSettings, issue_key: str) -> dict[str, Any]:
    """Fetch summary/description/comments/attachments for one issue.

    Returns a dict with key/summary/description_text/comments_text/
    attachment_names/project_key/project_name/issue_type/status, or
    ``{"error": "..."}``.
    """
    key = (issue_key or "").strip().upper()
    if not key:
        return {"error": "Enter a story key like PROJ-123."}

    ok, data, _status = _request(
        settings,
        "GET",
        f"/rest/api/3/issue/{key}",
        params={"fields": "summary,description,project,comment,attachment,issuetype,status"},
    )
    if not ok:
        return {"error": str(data)}

    fields = (data or {}).get("fields") or {}
    comments = ((fields.get("comment") or {}).get("comments")) or []
    comment_blocks = []
    for i, c in enumerate(comments):
        author = ((c.get("author") or {}).get("displayName")) or ""
        created = c.get("created") or ""
        header = f"Comment {i + 1}" + (f" by {author}" if author else "") + (f" ({created})" if created else "")
        body = adf_to_plain(c.get("body"))
        if body.strip():
            comment_blocks.append(f"{header}\n{body}")

    attachments = fields.get("attachment") or []
    attachment_names = [a.get("filename") for a in attachments if a.get("filename")]

    resolved_key = data.get("key") or key
    project = fields.get("project") or {}
    project_key = project.get("key") or (resolved_key.split("-")[0] if "-" in resolved_key else "")

    return {
        "key": resolved_key,
        "summary": fields.get("summary") or "",
        "description_text": adf_to_plain(fields.get("description")),
        "comments_text": "\n\n".join(comment_blocks),
        "attachment_names": attachment_names,
        "project_key": project_key,
        "project_name": project.get("name") or project_key,
        "issue_type": (fields.get("issuetype") or {}).get("name") or "",
        "status": (fields.get("status") or {}).get("name") or "",
    }


def build_story_context(issue: dict[str, Any]) -> str:
    """Format a fetched issue as Markdown-ish text suitable for an LLM prompt."""
    parts = [f"# {issue.get('key', '')}: {issue.get('summary', '')}", ""]
    if issue.get("description_text"):
        parts += ["## Description", issue["description_text"], ""]
    if issue.get("comments_text"):
        parts += ["## Jira comments", issue["comments_text"], ""]
    if issue.get("attachment_names"):
        parts += ["## Jira attachments", *[f"- {n}" for n in issue["attachment_names"]], ""]
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Epics
# ---------------------------------------------------------------------------

def is_epic_issue_type(name: str | None) -> bool:
    return bool(name) and name.strip().lower() == "epic"


def fetch_epic_children(
    settings: JiraSettings, epic_key: str, max_children: int = 50
) -> dict[str, Any]:
    """Fetch an Epic's child stories via four strategies (merged, de-duplicated).

    Jira has no single reliable "get epic children" call across project
    types, so — like the source feature — we try: the modern ``parent =``
    field, the ``childIssuesOf()`` JQL function, the Agile REST API, and the
    legacy ``"Epic Link"`` custom field, then merge unique results.
    """
    key = (epic_key or "").strip().upper()
    if not key:
        return {"error": "Enter an Epic key like PROJ-50."}
    max_n = max(1, min(100, max_children))

    ok, data, _status = _request(
        settings, "GET", f"/rest/api/3/issue/{key}", params={"fields": "summary,description,issuetype"}
    )
    if not ok:
        return {"error": str(data)}
    epic_fields = (data or {}).get("fields") or {}

    def parse_issues(raw: Any) -> list[dict[str, str]] | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("issues"), list):
            return None
        out = []
        for i in raw["issues"]:
            k = str(i.get("key") or "").upper()
            if not k:
                continue
            f = i.get("fields") or {}
            out.append(
                {
                    "key": k,
                    "summary": f.get("summary") or "",
                    "issue_type": (f.get("issuetype") or {}).get("name") or "",
                }
            )
        return out

    def try_jql(jql: str) -> list[dict[str, str]]:
        ok_j, data_j, _s = _request(
            settings,
            "POST",
            "/rest/api/3/search/jql",
            json_body={"jql": jql, "fields": ["summary", "issuetype"], "maxResults": max_n},
        )
        return parse_issues(data_j) or [] if ok_j else []

    def try_agile() -> list[dict[str, str]]:
        ok_a, data_a, _s = _request(
            settings,
            "GET",
            f"/rest/agile/1.0/epic/{key}/issue",
            params={"maxResults": max_n, "fields": "summary,issuetype"},
        )
        return parse_issues(data_a) or [] if ok_a else []

    strategies = [
        try_jql(f"parent = {key} ORDER BY rank"),
        try_jql(f'issue in childIssuesOf("{key}") ORDER BY rank'),
        try_agile(),
        try_jql(f'"Epic Link" = {key} ORDER BY rank'),
    ]

    seen: set[str] = set()
    merged: list[dict[str, str]] = []
    for rows in strategies:
        for row in rows:
            if row["key"] in seen:
                continue
            seen.add(row["key"])
            merged.append(row)

    return {
        "key": data.get("key") or key,
        "summary": epic_fields.get("summary") or "",
        "children": merged,
    }
