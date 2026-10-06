"""Pull candidate navigation-target names out of story text.

v1 is deliberately simple: a regex pass over quoted strings and capitalized
phrases following common navigation verbs. False negatives are acceptable --
Phase A's in-browser fallback ladder and Phase C's fail-fast diagnosis remain
the safety net for anything this misses. This is not NLP and isn't meant to be.
"""

from __future__ import annotations

import re

_QUOTED_RE = re.compile(r'["“]([A-Za-z0-9][A-Za-z0-9 /&\'\-]{1,48})["”]')
_VERB_PHRASE_RE = re.compile(
    r"\b(?i:navigate to|go to|open|click(?: on)?|select)\s+(?:(?i:the)\s+)?"
    r"((?:[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,4}))",
)
_STOPWORDS = {
    "The", "This", "That", "It", "A", "An", "Then", "And", "But", "Verify",
    "Ensure", "Check", "Confirm",
}


def extract_candidate_target_names(title: str, steps: list[str] | str | None) -> list[str]:
    """Extract likely tab/object/quick-action/flow/report names referenced
    in a test case's title and steps, for pre-resolution against org
    metadata. Order-preserving, de-duplicated (case-insensitive)."""
    if isinstance(steps, str):
        step_lines = [steps]
    else:
        step_lines = list(steps or [])
    text_blocks = [title or "", *step_lines]

    seen: set[str] = set()
    out: list[str] = []
    for text in text_blocks:
        if not text:
            continue
        for match in _QUOTED_RE.finditer(text):
            _add(out, seen, match.group(1).strip())
        for match in _VERB_PHRASE_RE.finditer(text):
            candidate = match.group(1).strip()
            if candidate and candidate not in _STOPWORDS:
                _add(out, seen, candidate)
    return out


def _add(out: list[str], seen: set[str], candidate: str) -> None:
    key = candidate.lower()
    if not candidate or key in seen:
        return
    seen.add(key)
    out.append(candidate)
