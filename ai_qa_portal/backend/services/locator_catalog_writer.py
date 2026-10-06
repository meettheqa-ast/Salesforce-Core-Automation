"""Read/match/write helpers for widening shared locators in
Resources/Common/GlobalLocators.robot after a bulk-run heal attempt
actually passes.

Why: the bulk auto-heal pipeline (``_heal_after_failure`` in
``routers/runs.py``) fixes a failing generated test by asking an LLM to
rewrite the whole .robot script, usually inlining a ``Resolve Tiered
Locator`` call with guessed xpath fallbacks. That only helps the one test
case -- the shared locator library never learns the fix, so the next
generated test that hits the same UI quirk has to be healed all over
again. This module lets the healer propose a widened shared-locator value
alongside its script fix, and -- only once the retry has empirically
passed -- append that alternative to the shared variable in
GlobalLocators.robot. Never replaces or removes existing tiers; only
appends, and only to a variable that already exists in the file.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

_VAR_LINE_RE = re.compile(r"^\$\{(\w+)\}\s*=\s*(.+)$", re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"<[^>]+>")
_VAR_NAME_RE = re.compile(r"^[A-Za-z]\w*$")
_LOCATOR_FIX_RE = re.compile(
    r"---LOCATOR-FIX---\s*\n(.*?)\n(.*?)\n---END-LOCATOR-FIX---",
    re.DOTALL,
)

_DEFAULT_LOCATORS_FILE = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "Resources" / "Common" / "GlobalLocators.robot"
)

_SIMILARITY_THRESHOLD = 0.45


def _locators_path(path: Path | None) -> Path:
    return path or _DEFAULT_LOCATORS_FILE


def read_global_locators(path: Path | None = None) -> dict[str, dict]:
    """Parse GlobalLocators.robot's ``*** Variables ***`` table.

    Returns ``{name: {"value": str, "line_no": int}}``. ``line_no`` is
    1-based, for safe in-place rewriting later.
    """
    p = _locators_path(path)
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8")
    out: dict[str, dict] = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        m = _VAR_LINE_RE.match(line)
        if not m:
            continue
        name, value = m.group(1).strip(), m.group(2).strip()
        if value and not value.startswith("#"):
            out[name] = {"value": value, "line_no": line_no}
    return out


def _normalize(locator_value: str) -> str:
    """Strip ``<placeholder>`` tokens so templated catalog values compare
    reasonably against a literal failing locator."""
    return _PLACEHOLDER_RE.sub("", locator_value).strip()


def find_related_locator(failing_literal: str, catalog: dict[str, dict] | None = None) -> str | None:
    """Best-effort fuzzy match of a failing locator literal against the
    shared catalog. Returns the variable name of the closest match above
    a similarity threshold, else None.

    This is only a *hint* fed to the healer LLM -- the LLM makes the real
    judgment call about whether its fix generalizes, so approximate
    matching here is fine.
    """
    if not failing_literal:
        return None
    catalog = catalog if catalog is not None else read_global_locators()
    needle = _normalize(failing_literal)
    if not needle:
        return None
    best_name: str | None = None
    best_ratio = 0.0
    for name, entry in catalog.items():
        hay = _normalize(entry["value"])
        if not hay:
            continue
        ratio = difflib.SequenceMatcher(None, needle, hay).ratio()
        if ratio > best_ratio:
            best_ratio, best_name = ratio, name
    return best_name if best_ratio >= _SIMILARITY_THRESHOLD else None


def extract_locator_fix(raw: str) -> tuple[str, str] | None:
    """Parse an optional ``---LOCATOR-FIX---`` block out of a healer LLM
    reply. Shape:

        ---LOCATOR-FIX---
        <variable name>
        <full new value, all alternatives | -joined>
        ---END-LOCATOR-FIX---

    Returns ``(var_name, new_value)`` or None if absent/malformed.
    """
    m = _LOCATOR_FIX_RE.search(raw or "")
    if not m:
        return None
    var_name = m.group(1).strip().strip("${}")
    new_value = m.group(2).strip()
    if not _VAR_NAME_RE.match(var_name) or not new_value:
        return None
    return var_name, new_value


def apply_locator_promotion(var_name: str, new_alternative: str, path: Path | None = None) -> bool:
    """Append ``new_alternative`` as an additional ``|``-joined option on
    ``${var_name}``'s line in GlobalLocators.robot.

    Returns False (no-op) when: the name is invalid, the variable doesn't
    already exist in the file (MVP only widens known shared locators,
    never auto-creates new ones), or the alternative is already present.
    Returns True when the file was actually rewritten.

    Only ever appends -- existing tiers are never replaced or removed.
    Write is atomic (tmp file + replace), mirroring the heal-script writer
    in routers/runs.py.
    """
    if not _VAR_NAME_RE.match(var_name or ""):
        return False
    new_alternative = (new_alternative or "").strip()
    if not new_alternative:
        return False

    p = _locators_path(path)
    catalog = read_global_locators(p)
    entry = catalog.get(var_name)
    if entry is None:
        return False

    current_value = entry["value"]
    if new_alternative in current_value:
        return False

    updated_value = f"{current_value} | {new_alternative}"
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    idx = entry["line_no"] - 1
    old_line = lines[idx]
    newline = "\n" if old_line.endswith("\n") else ""
    m = _VAR_LINE_RE.match(old_line.rstrip("\n"))
    if not m:
        return False
    prefix = old_line[: m.start(2)]
    lines[idx] = f"{prefix}{updated_value}{newline}"

    tmp_path = p.with_suffix(p.suffix + ".promote.tmp")
    tmp_path.write_text("".join(lines), encoding="utf-8")
    tmp_path.replace(p)
    return True
