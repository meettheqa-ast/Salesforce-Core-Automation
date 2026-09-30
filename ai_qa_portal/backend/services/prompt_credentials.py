"""Extract test-user credentials embedded in a free-text generate prompt.

When a prompt names both a username and a password, those values should
override the WorkspaceBar / persona login for that generate or run.
Either half alone is ignored (workspace login stays authoritative).
"""

from __future__ import annotations

import re

_PLACEHOLDER_RE = re.compile(
    r"^(\*+|\$\{.*\}|<[^>]+>|your[_-]?password|changeme|xxx+|password)$",
    re.IGNORECASE,
)

# Labeled fields: "Username: a@b.com" / "test account = foo" / "password: secret"
_LABELED_USER_RE = re.compile(
    r"(?i)\b(?:user\s*name|username|login|email|test\s*user|test\s*account)\s*[:=]\s*"
    r"[\"']?([^\s\"',;]+@?[^\s\"',;]*)[\"']?"
)
_LABELED_PASS_RE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|pass)\s*[:=]\s*"
    r"[\"']?([^\s\"',;]+)[\"']?"
)

# Combined phrases: "as user a@b.com with password secret"
_COMBINED_RE = re.compile(
    r"(?i)\b(?:as|with)\s+(?:user(?:name)?|login|account)?\s*"
    r"[\"']?([^\s\"']+@[^\s\"']+)[\"']?\s+"
    r"(?:and\s+)?(?:with\s+)?(?:password|passwd|pwd|pass)\s+"
    r"[\"']?([^\s\"',;]+)[\"']?"
)

# "user a@b.com ... password secret" (looser)
_USER_THEN_PASS_RE = re.compile(
    r"(?i)\b(?:user(?:name)?|login|account)\s+[\"']?([^\s\"']+@[^\s\"']+)[\"']?"
    r"[\s\S]{0,80}?\b(?:password|passwd|pwd|pass)\s+[\"']?([^\s\"',;]+)[\"']?"
)


def _clean(value: str) -> str:
    return (value or "").strip().strip("\"'").strip()


def _is_placeholder(value: str) -> bool:
    v = _clean(value)
    if not v or len(v) < 2:
        return True
    return bool(_PLACEHOLDER_RE.match(v))


def extract_credentials_from_prompt(prompt: str) -> tuple[str, str] | None:
    """Return ``(username, password)`` when both are present in ``prompt``.

    Returns ``None`` when either half is missing or looks like a placeholder.
    """
    text = prompt or ""
    if not text.strip():
        return None

    username = ""
    password = ""

    for pattern in (_COMBINED_RE, _USER_THEN_PASS_RE):
        m = pattern.search(text)
        if m:
            username = _clean(m.group(1))
            password = _clean(m.group(2))
            break

    if not username:
        um = _LABELED_USER_RE.search(text)
        if um:
            username = _clean(um.group(1))
    if not password:
        pm = _LABELED_PASS_RE.search(text)
        if pm:
            password = _clean(pm.group(1))

    if _is_placeholder(username) or _is_placeholder(password):
        return None
    return username, password
