"""Live keyword-catalog scanner for the Robot Framework Salesforce library.

Why this exists: the static `keyword_catalog.json` shipped at the repo root
captures a snapshot of the library and drifts whenever someone adds a
keyword. The LLM paths that consume that catalog were missing newly-added
keywords (App Launcher, picklist helpers, etc.). This module walks
`Resources/Common/*.robot` and `Resources/PO/**/*.robot` on demand, parses
the `*** Keywords ***` blocks, and returns a structured catalog identical
in shape to the static JSON so callers can swap one for the other.

Public API:
  - `build_catalog()` -> dict matching keyword_catalog.json's schema
  - `compact_json()` -> JSON string ready to inject into an LLM prompt
  - `keyword_names()` -> list of just the keyword names (cheap summary
    suitable for the test-case drafter where full args/docs are noise)

The output is mtime-cached so consecutive calls within a request are free.
The cache invalidates when any scanned file's mtime changes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

from ai_qa_portal.backend.config import REPO_ROOT

logger = logging.getLogger("ai_qa_portal.keyword_catalog")

# Directories scanned. Order matters only for the resulting "scanned_paths"
# list in the catalog header; keyword ordering doesn't depend on it.
_SCAN_DIRS = ("Resources/Common", "Resources/PO")

# Where to fall back if the scanner produces nothing (legitimately empty
# Resources/ tree, or an unexpected parse failure). The static snapshot is
# better than no catalog at all.
_FALLBACK_JSON = REPO_ROOT / "keyword_catalog.json"


# --- Robot file parsing -----------------------------------------------------


@dataclass
class ParsedKeyword:
    keyword_name: str
    arguments: list[str] = field(default_factory=list)
    documentation: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    source_file: str = ""

    def to_dict(self) -> dict:
        """Match the schema of the existing keyword_catalog.json so the LLM
        prompts that already consume that schema work unchanged."""
        doc = self.documentation
        summary = (
            doc.splitlines()[0].strip()
            if doc
            else f"Robot Framework keyword: {self.keyword_name}."
        )
        return {
            "keyword_name": self.keyword_name,
            "arguments": list(self.arguments),
            "documentation": doc,
            "documentation_source": "robot_doc" if doc else "inferred_only",
            "natural_language_summary": summary,
            "inferred_description": summary,
            "tags": list(self.tags),
            "source_file": self.source_file.replace("\\", "/"),
        }


_SECTION_RE = re.compile(r"^\*\*\*\s*([A-Za-z ]+?)\s*\*\*\*", re.MULTILINE)
_KEYWORD_HEADER_RE = re.compile(r"^([A-Za-z][^\s].*?)\s*$")
_DOC_RE = re.compile(r"^\s*\[Documentation\]\s*(.+)$", re.IGNORECASE)
_ARGS_RE = re.compile(r"^\s*\[Arguments\]\s*(.+)$", re.IGNORECASE)
_TAGS_RE = re.compile(r"^\s*\[Tags\]\s*(.+)$", re.IGNORECASE)
_CONTINUATION_RE = re.compile(r"^\s*\.\.\.\s*(.*)$")


def _parse_robot_file(path: Path, repo_root: Path) -> list[ParsedKeyword]:
    """Extract keyword definitions from a single .robot file.

    The Robot file format isn't context-free in the strict sense but
    `*** Keywords ***` sections follow a predictable shape: a non-indented
    line names a keyword, indented lines beneath it (until the next
    non-indented line or section) belong to that keyword. `[Documentation]`
    / `[Arguments]` / `[Tags]` settings can span multiple lines via `...`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("keyword_catalog: cannot read %s: %s", path, exc)
        return []

    # Find the *** Keywords *** section span. Robot allows multiple but in
    # practice each file has at most one.
    sections = list(_SECTION_RE.finditer(text))
    if not sections:
        return []
    # Build a list of (section_name, body_text) tuples.
    bodies: list[tuple[str, str]] = []
    for i, m in enumerate(sections):
        name = m.group(1).strip().lower()
        start = m.end()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        bodies.append((name, text[start:end]))

    keyword_bodies = [b for n, b in bodies if n in ("keyword", "keywords")]
    if not keyword_bodies:
        return []

    rel = path.relative_to(repo_root).as_posix()
    out: list[ParsedKeyword] = []
    for body in keyword_bodies:
        out.extend(_parse_keywords_block(body, rel))
    return out


def _parse_keywords_block(body: str, source_file: str) -> list[ParsedKeyword]:
    keywords: list[ParsedKeyword] = []
    current: Optional[ParsedKeyword] = None
    # Continuation accumulators -- a single setting can span multiple `...` lines.
    pending_setting: Optional[str] = None  # "doc" | "args" | "tags"
    pending_doc: list[str] = []
    pending_args: list[str] = []
    pending_tags: list[str] = []

    def _flush_pending():
        nonlocal pending_setting, pending_doc, pending_args, pending_tags
        if not current or not pending_setting:
            pending_setting = None
            return
        if pending_setting == "doc":
            current.documentation = " ".join(s.strip() for s in pending_doc if s.strip()).strip() or None
        elif pending_setting == "args":
            # Tokenise on Robot's separator (>=2 spaces or tab) joined across
            # any continuation lines.
            joined = " ".join(s for s in pending_args if s)
            current.arguments = [t.strip() for t in re.split(r"\s{2,}|\t", joined) if t.strip()]
        elif pending_setting == "tags":
            joined = " ".join(s for s in pending_tags if s)
            current.tags = [t.strip() for t in re.split(r"\s{2,}|\t|,", joined) if t.strip()]
        pending_setting = None
        pending_doc = []
        pending_args = []
        pending_tags = []

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            _flush_pending()
            continue
        if line.lstrip().startswith("#"):
            # Robot comment, ignore.
            continue

        # New keyword: line starts at column 0 (no leading whitespace) and
        # isn't a continuation marker.
        if not raw_line[:1].isspace():
            _flush_pending()
            if current is not None:
                keywords.append(current)
            name = _KEYWORD_HEADER_RE.match(line).group(1) if _KEYWORD_HEADER_RE.match(line) else line.strip()
            current = ParsedKeyword(keyword_name=name, source_file=source_file)
            continue

        if current is None:
            # Body line with no header in scope; skip.
            continue

        cont = _CONTINUATION_RE.match(line)
        if cont:
            tail = cont.group(1)
            if pending_setting == "doc":
                pending_doc.append(tail)
            elif pending_setting == "args":
                pending_args.append(tail)
            elif pending_setting == "tags":
                pending_tags.append(tail)
            continue

        # A non-continuation indented line ends any prior multi-line setting.
        _flush_pending()

        m_doc = _DOC_RE.match(line)
        if m_doc:
            pending_setting = "doc"
            pending_doc = [m_doc.group(1)]
            continue
        m_args = _ARGS_RE.match(line)
        if m_args:
            pending_setting = "args"
            pending_args = [m_args.group(1)]
            continue
        m_tags = _TAGS_RE.match(line)
        if m_tags:
            pending_setting = "tags"
            pending_tags = [m_tags.group(1)]
            continue
        # Body step -- not a setting we care about for the catalog. Skip.

    _flush_pending()
    if current is not None:
        keywords.append(current)
    return keywords


# --- Catalog assembly + caching --------------------------------------------


_cache_lock = Lock()
_cache: dict | None = None
_cache_signature: tuple | None = None


def _scan_signature() -> tuple:
    """Tuple of (path, mtime_ns) for every file the scanner reads. Used as
    the cache key so callers don't re-walk the tree on every invocation."""
    parts: list[tuple[str, int]] = []
    for sub in _SCAN_DIRS:
        root = REPO_ROOT / sub
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.robot")):
            try:
                parts.append((str(p), p.stat().st_mtime_ns))
            except OSError:
                continue
    return tuple(parts)


def build_catalog(force: bool = False) -> dict:
    """Return the full keyword catalog. Cached by file mtimes.

    Falls back to the on-disk `keyword_catalog.json` if scanning produces
    no keywords (e.g. someone moved Resources/ around) so the LLM never
    sees a totally empty catalog.
    """
    # pylint: disable-next=global-statement
    global _cache, _cache_signature  # intentional module-level mtime cache

    sig = _scan_signature()
    with _cache_lock:
        if not force and _cache is not None and _cache_signature == sig:
            return _cache

        keywords: list[dict] = []
        scanned_paths: list[str] = []
        for sub in _SCAN_DIRS:
            root = REPO_ROOT / sub
            if not root.is_dir():
                continue
            scanned_paths.append(sub)
            for p in sorted(root.rglob("*.robot")):
                for kw in _parse_robot_file(p, REPO_ROOT):
                    keywords.append(kw.to_dict())

        if not keywords and _FALLBACK_JSON.is_file():
            logger.warning("keyword_catalog: scan produced 0 keywords; using static %s", _FALLBACK_JSON)
            try:
                _cache = json.loads(_FALLBACK_JSON.read_text(encoding="utf-8"))
                _cache_signature = sig
                return _cache
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("keyword_catalog: fallback JSON unreadable: %s", exc)

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root_hint": REPO_ROOT.name,
            "scanned_paths": scanned_paths,
            "keyword_count": len(keywords),
            "keywords": keywords,
        }
        _cache = result
        _cache_signature = sig
        return result


def compact_json(catalog: Optional[dict] = None) -> str:
    """JSON string suitable for inlining into an LLM prompt."""
    data = catalog if catalog is not None else build_catalog()
    return json.dumps(data, ensure_ascii=False, indent=2)


def keyword_names(catalog: Optional[dict] = None) -> list[str]:
    """Just the keyword names, alphabetised. Cheap summary for the
    test-case drafter where full arg/doc payload is noise."""
    data = catalog if catalog is not None else build_catalog()
    names = sorted({kw["keyword_name"] for kw in data.get("keywords", [])}, key=str.lower)
    return names
