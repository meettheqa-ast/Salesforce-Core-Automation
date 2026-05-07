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
from typing import Iterable, Optional

from ai_qa_portal.backend.config import REPO_ROOT

logger = logging.getLogger("ai_qa_portal.keyword_catalog")

# Directories scanned. Order matters only for the resulting "scanned_paths"
# list in the catalog header; keyword ordering doesn't depend on it.
_SCAN_DIRS = ("Resources/Common", "Resources/PO")

# Where to fall back if the scanner produces nothing (legitimately empty
# Resources/ tree, or an unexpected parse failure). The static snapshot is
# better than no catalog at all.
_FALLBACK_JSON = REPO_ROOT / "keyword_catalog.json"

# Robot-Framework Settings line that imports a Python or Robot library.
# Captures both ``Library  ../../Libraries/SalesforceApiLibrary.py`` and the
# bare-name form ``Library  SeleniumLibrary``. We only follow path-style
# imports for the live scanner (the bare-name form ships with the framework
# and is covered by the validator's allow-list instead -- duplicating those
# keywords in the prompt would just inflate the token budget).
_LIBRARY_IMPORT_RE = re.compile(
    r"^\s*Library\s+(?P<target>\S+)(?P<rest>.*)$",
    re.IGNORECASE | re.MULTILINE,
)


# --- Robot file parsing -----------------------------------------------------


@dataclass
class ParsedKeyword:
    keyword_name: str
    arguments: list[str] = field(default_factory=list)
    documentation: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    source_file: str = ""
    # When the keyword originates from a Python ``Library`` import rather than
    # a Robot resource file, this carries the library's class/module name (as
    # reported by libdoc). Used by ``compact_for_prompt`` to qualify the
    # keyword as ``LibraryName.Keyword Name`` so the LLM emits a unique call.
    source_library: Optional[str] = None

    def to_dict(self) -> dict:
        """Match the schema of the existing keyword_catalog.json so the LLM
        prompts that already consume that schema work unchanged."""
        doc = self.documentation
        summary = (
            doc.splitlines()[0].strip()
            if doc
            else f"Robot Framework keyword: {self.keyword_name}."
        )
        out: dict = {
            "keyword_name": self.keyword_name,
            "arguments": list(self.arguments),
            "documentation": doc,
            "documentation_source": "robot_doc" if doc else "inferred_only",
            "natural_language_summary": summary,
            "inferred_description": summary,
            "tags": list(self.tags),
            "source_file": self.source_file.replace("\\", "/"),
        }
        if self.source_library:
            out["source_library"] = self.source_library
        return out


def _extract_library_paths(text: str, robot_file: Path) -> list[Path]:
    """Resolve any path-style ``Library`` imports in a parsed Robot file.

    Bare-name imports (``Library  SeleniumLibrary``) are skipped here -- the
    validator picks those up via ``framework_allow_list()``. Path-style
    imports point at project-local Python modules whose @keyword functions
    we want the LLM to see by name.
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for m in _LIBRARY_IMPORT_RE.finditer(text):
        target = m.group("target").strip()
        # Robot's WITH NAME / arg-as-target syntax can put non-path tokens
        # here. Treat anything ending in .py (case-insensitive) as a path.
        if not target.lower().endswith(".py"):
            continue
        try:
            resolved = (robot_file.parent / target).resolve(strict=False)
        except OSError:
            continue
        if not resolved.is_file():
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _libdoc_for(target: str) -> Optional[object]:
    """Build a libdoc document for ``target`` (a file path OR a bare library
    name like ``"BuiltIn"``). Returns ``None`` if the lookup fails.

    Robot 7's public entry point is ``robot.libdocpkg.LibraryDocumentation``
    (a function). Wrapped in a broad ``try/except`` because libdoc
    instantiates the library class to introspect it -- a constructor that
    raises would otherwise abort the whole catalog build.
    """
    try:
        from robot.libdocpkg import LibraryDocumentation
    except ImportError as exc:  # pragma: no cover -- robotframework is a dep
        logger.warning("keyword_catalog: robot.libdocpkg unavailable: %s", exc)
        return None

    try:
        return LibraryDocumentation(target)
    except Exception as exc:  # noqa: BLE001 -- libdoc raises a wide variety
        logger.warning(
            "keyword_catalog: libdoc failed for %s: %s (skipping its keywords)",
            target, exc,
        )
        return None


def _libdoc_keywords(library_path: Path, repo_root: Path) -> list[ParsedKeyword]:
    """Use Robot's libdoc machinery to enumerate ``@keyword`` functions in a
    Python library file."""
    libdoc = _libdoc_for(str(library_path))
    if libdoc is None:
        return []

    try:
        rel = library_path.relative_to(repo_root).as_posix()
    except ValueError:
        rel = library_path.as_posix()

    out: list[ParsedKeyword] = []
    for kw in libdoc.keywords:
        # libdoc's ``args`` is a list of ArgInfo objects with .name (and
        # other signature info). For the catalog schema we only need the
        # names -- the LLM prompts emit them positionally or as
        # ``name=value``. Skip ``**kwargs``-style entries because Robot's
        # callers pass them inline as ``key=value`` and a bare name like
        # "fields" in the signature would just confuse the model.
        try:
            arg_names: list[str] = []
            for a in (kw.args or []):
                kind = getattr(a, "kind", "")
                name = str(getattr(a, "name", a)).strip()
                if not name:
                    continue
                if str(kind).upper().endswith("VAR_NAMED"):
                    arg_names.append(f"&{{{name}}}")
                elif str(kind).upper().endswith("VAR_POSITIONAL"):
                    arg_names.append(f"@{{{name}}}")
                else:
                    arg_names.append(name)
        except Exception:  # noqa: BLE001 -- defensive
            arg_names = []
        doc = (kw.doc or "").strip() or None
        tags = []
        try:
            tags = [str(t).strip() for t in (kw.tags or []) if str(t).strip()]
        except Exception:  # noqa: BLE001
            tags = []
        out.append(ParsedKeyword(
            keyword_name=kw.name,
            arguments=arg_names,
            documentation=doc,
            tags=tags,
            source_file=rel,
            source_library=getattr(libdoc, "name", None) or library_path.stem,
        ))
    return out


_SECTION_RE = re.compile(r"^\*\*\*\s*([A-Za-z ]+?)\s*\*\*\*", re.MULTILINE)
_KEYWORD_HEADER_RE = re.compile(r"^([A-Za-z][^\s].*?)\s*$")
_DOC_RE = re.compile(r"^\s*\[Documentation\]\s*(.+)$", re.IGNORECASE)
_ARGS_RE = re.compile(r"^\s*\[Arguments\]\s*(.+)$", re.IGNORECASE)
_TAGS_RE = re.compile(r"^\s*\[Tags\]\s*(.+)$", re.IGNORECASE)
_CONTINUATION_RE = re.compile(r"^\s*\.\.\.\s*(.*)$")


def _parse_robot_file(
    path: Path,
    repo_root: Path,
    *,
    library_sink: Optional[set[Path]] = None,
) -> list[ParsedKeyword]:
    """Extract keyword definitions from a single .robot file.

    The Robot file format isn't context-free in the strict sense but
    `*** Keywords ***` sections follow a predictable shape: a non-indented
    line names a keyword, indented lines beneath it (until the next
    non-indented line or section) belong to that keyword. `[Documentation]`
    / `[Arguments]` / `[Tags]` settings can span multiple lines via `...`.

    When ``library_sink`` is provided, every path-style ``Library`` import
    discovered in this file is added to it so the caller can later run
    libdoc against those modules. The sink is shared (mutated) across all
    Robot files in a single catalog build, so each library is scanned once
    even if multiple resources import it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("keyword_catalog: cannot read %s: %s", path, exc)
        return []

    if library_sink is not None:
        for lib_path in _extract_library_paths(text, path):
            library_sink.add(lib_path)

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
# Cache for the framework allow-list. BuiltIn + SeleniumLibrary keyword sets
# don't change at runtime so they're cached forever (per-process). Computed
# lazily on first request because importing the libdoc machinery isn't free.
_framework_allow_list: frozenset[str] | None = None


def _scan_signature() -> tuple:
    """Tuple of (path, mtime_ns) for every file the scanner reads. Used as
    the cache key so callers don't re-walk the tree on every invocation.

    Includes both Robot resources AND any path-style ``Library`` imports
    those resources declare, so editing a Python @keyword library
    invalidates the cache the same way editing a .robot file does.
    """
    parts: list[tuple[str, int]] = []
    library_paths: set[Path] = set()

    for sub in _SCAN_DIRS:
        root = REPO_ROOT / sub
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.robot")):
            try:
                parts.append((str(p), p.stat().st_mtime_ns))
            except OSError:
                continue
            # Cheap second pass to discover library imports without parsing
            # the Keywords block. Reads each .robot once, but the result is
            # then served from the mtime cache.
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lib in _extract_library_paths(text, p):
                library_paths.add(lib)

    for lib in sorted(library_paths):
        try:
            parts.append((str(lib), lib.stat().st_mtime_ns))
        except OSError:
            continue
    return tuple(parts)


def build_catalog(force: bool = False) -> dict:
    """Return the full keyword catalog. Cached by file mtimes.

    Falls back to the on-disk `keyword_catalog.json` if scanning produces
    no keywords (e.g. someone moved Resources/ around) so the LLM never
    sees a totally empty catalog.

    Includes both Robot ``*** Keywords ***`` definitions AND ``@keyword``
    functions discovered in any path-style ``Library`` import, so the
    LLM can also see e.g. ``API Create Record`` /
    ``API Delete Record`` defined in ``Libraries/SalesforceApiLibrary.py``.
    """
    # pylint: disable-next=global-statement
    global _cache, _cache_signature  # intentional module-level mtime cache

    sig = _scan_signature()
    with _cache_lock:
        if not force and _cache is not None and _cache_signature == sig:
            return _cache

        keywords: list[dict] = []
        scanned_paths: list[str] = []
        library_paths: set[Path] = set()

        for sub in _SCAN_DIRS:
            root = REPO_ROOT / sub
            if not root.is_dir():
                continue
            scanned_paths.append(sub)
            for p in sorted(root.rglob("*.robot")):
                for kw in _parse_robot_file(p, REPO_ROOT, library_sink=library_paths):
                    keywords.append(kw.to_dict())

        # Each unique library path gets a single libdoc pass even if multiple
        # resources import it.
        for lib in sorted(library_paths):
            for kw in _libdoc_keywords(lib, REPO_ROOT):
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
            "scanned_libraries": sorted(p.relative_to(REPO_ROOT).as_posix() if _is_under(p, REPO_ROOT) else p.as_posix() for p in library_paths),
            "keyword_count": len(keywords),
            "keywords": keywords,
        }
        _cache = result
        _cache_signature = sig
        return result


def _is_under(path: Path, parent: Path) -> bool:
    """Best-effort ``is_relative_to`` polyfill that doesn't raise on
    Windows when paths live on different drives."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def framework_allow_list() -> frozenset[str]:
    """Return the lower-cased set of every keyword name shipped by the
    standard libraries the project actually imports (``BuiltIn``,
    ``SeleniumLibrary``, ``String``, ``Collections``, ``DateTime``,
    ``OperatingSystem``, ``Process``, ``FakerLibrary``).

    Used by ``script_validator`` to decide whether an unresolved
    ``KeywordCall`` is a real framework keyword or a hallucination.
    Deliberately NOT mixed into ``compact_for_prompt`` -- the LLM
    already knows BuiltIn / SeleniumLibrary by training, and inflating
    the prompt with their ~500 keyword names would burn tokens for no
    accuracy gain.
    """
    # pylint: disable-next=global-statement
    global _framework_allow_list

    if _framework_allow_list is not None:
        return _framework_allow_list

    names: set[str] = set()
    for lib_name in (
        "BuiltIn",
        "Collections",
        "DateTime",
        "OperatingSystem",
        "Process",
        "String",
        "SeleniumLibrary",
        "FakerLibrary",
        # XML, Screenshot, Telnet etc. exist but the project doesn't import them
        # in any current resource. Keep the list tight so a typo in a project
        # keyword doesn't accidentally collide with an obscure stdlib keyword.
    ):
        libdoc = _libdoc_for(lib_name)
        if libdoc is None:
            continue
        for kw in libdoc.keywords:
            n = (kw.name or "").strip().lower()
            if n:
                names.add(n)

    _framework_allow_list = frozenset(names)
    logger.info("framework_allow_list: cached %s keywords", len(_framework_allow_list))
    return _framework_allow_list


def all_known_keywords() -> dict[str, list[str]]:
    """Map of lower-cased keyword name -> list of source labels (resource
    file stem, library class name, or ``"framework"``). Used by the
    validator's closest-match suggestions and for unambiguous resolution
    of qualified ``Library.Keyword`` references.
    """
    out: dict[str, list[str]] = {}
    catalog = build_catalog()
    for kw in catalog.get("keywords", []):
        name = (kw.get("keyword_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        src = (kw.get("source_library") or Path(kw.get("source_file") or "").stem or "").strip()
        out.setdefault(key, []).append(src or "robot")
    for n in framework_allow_list():
        out.setdefault(n, []).append("framework")
    return out


def compact_json(catalog: Optional[dict] = None) -> str:
    """Full pretty-printed catalog JSON. Kept for non-prompt callers
    (debug pages, exports). DO NOT use this for LLM prompts -- it dumps
    every keyword's full documentation, tags, source path and metadata,
    which on this codebase comes out to ~36k tokens. Use
    ``compact_for_prompt`` instead, which strips that to ~3-5k tokens
    while keeping the fields the LLM actually planner needs (qualified
    name, args, one-line summary). Token bloat here was the single
    biggest cause of slow / failing planning calls -- Groq free tier
    413s at 12k TPM, Gemini takes 30-100 s to chew it.
    """
    data = catalog if catalog is not None else build_catalog()
    return json.dumps(data, ensure_ascii=False, indent=2)


def compact_for_prompt(catalog: Optional[dict] = None, *, max_doc_chars: int = 140) -> str:
    """Lean catalog JSON for LLM prompts.

    Each entry has just three fields:
      - ``name`` -- qualified keyword (e.g. ``"SalesPO.Create A New Lead"``).
        Library prefix is derived from the source file stem so the LLM can
        pick the right prefix without seeing the full path.
      - ``args`` -- argument signature list, unchanged.
      - ``doc``  -- one-line summary (first 140 chars), only when present
        and not the auto-generated "Robot Framework keyword: ..." stub.

    All metadata the planner doesn't read (tags, source_file, doc source,
    inferred descriptions, generation timestamps) is dropped, and the JSON
    is emitted with the most compact separators. On this repo's catalog
    (171 keywords) this brings the prompt payload from ~36k tokens down
    to ~3-5k.
    """
    data = catalog if catalog is not None else build_catalog()
    out: list[dict] = []
    for kw in data.get("keywords", []):
        name = (kw.get("keyword_name") or "").strip()
        if not name:
            continue
        # Prefer the explicit ``source_library`` (set by libdoc-discovered
        # @keyword functions: "API Create Record" -> "SalesforceApiLibrary.
        # API Create Record"). Otherwise derive from the source file stem
        # ("Resources/PO/Platform/SalesPO.robot" -> "SalesPO"). Skip when
        # the keyword is already qualified.
        src_lib = (kw.get("source_library") or "").strip()
        src = kw.get("source_file") or ""
        lib = src_lib or (Path(src).stem if src else "")
        qualified = name if "." in name or not lib else f"{lib}.{name}"

        entry: dict = {"name": qualified}
        args = kw.get("arguments") or []
        if args:
            entry["args"] = list(args)

        summary = (kw.get("natural_language_summary") or "").strip()
        # Skip the auto-generated stub from to_dict() -- it's pure noise.
        if summary and not summary.startswith("Robot Framework keyword:"):
            # Single line, capped, so a stray multi-paragraph docstring can't
            # blow the budget.
            summary = summary.splitlines()[0].strip()
            if len(summary) > max_doc_chars:
                summary = summary[: max_doc_chars - 1].rstrip() + "…"
            entry["doc"] = summary

        out.append(entry)
    # Compact separators -- saves ~15% over the default ", " / ": " pair on
    # a payload this size.
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def keyword_names(catalog: Optional[dict] = None) -> list[str]:
    """Just the keyword names, alphabetised. Cheap summary for the
    test-case drafter where full arg/doc payload is noise."""
    data = catalog if catalog is not None else build_catalog()
    names = sorted({kw["keyword_name"] for kw in data.get("keywords", [])}, key=str.lower)
    return names
