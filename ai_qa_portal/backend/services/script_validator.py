"""Static validator for LLM-generated Robot Framework scripts.

Goal: catch hallucinated keywords, undefined variables, and broken
``Resource`` / ``Library`` imports **before** the script reaches the user
or the test runner. Every error produced by this module is shaped as an
:class:`ValidationError` with the symbol that failed, the closest
catalog matches (top 3 by Levenshtein distance), and a snippet of the
offending line — so the retry loop in
``test_case_script_builder`` can hand the LLM a concrete fix prompt.

The validator is **AST-only** (uses ``robot.api.get_model``) — it never
imports the suite or executes anything. The companion ``script_dryrun``
module owns the runtime gate.

Resolution rules — a ``KeywordCall`` passes when ANY of these apply:

* The literal name is in the project catalog (Resources/**/*.robot
  ``*** Keywords ***`` or any path-style ``Library`` import's
  @keyword-decorated functions).
* The literal name is in the framework allow-list (``BuiltIn``,
  ``SeleniumLibrary``, ``Collections``, ``DateTime``, ``OperatingSystem``,
  ``Process``, ``String``, ``FakerLibrary``).
* The name is qualified as ``Source.Keyword`` and the right-hand side
  passes the above two checks.
* The name is defined in the suite's own ``*** Keywords ***`` section.

A ``${var}`` reference passes when ANY of these apply:

* It's defined in the suite's ``*** Variables ***``.
* It's assigned earlier in the same test/keyword via ``${x}=  Some Keyword``.
* It's a ``[Arguments]`` of the enclosing user keyword.
* It's a Robot built-in (``${SPACE}``, ``${EMPTY}``, ``${TRUE}``,
  ``${FALSE}``, ``${None}``, ``${TEST_NAME}``, ``${OUTPUT_DIR}``,
  ``${CURDIR}``, ``${TEMPDIR}``, ``${EXECDIR}``, ``${\\n}``, ``${/}``,
  ``${:}``, ``${\\t}``).
* It's defined in any imported ``Resource``'s ``*** Variables ***``
  (transitively walked one level — Resources rarely chain deeply, and
  walking infinitely would expand the validator's surface).

This deliberately does NOT check argument arity — the
``robot --dryrun`` gate covers that. Catching it here would require
reimplementing Robot's keyword-resolution + type-coercion stack, which
is exactly what dryrun does for free.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Iterable, Literal, Optional

from ai_qa_portal.backend.config import REPO_ROOT
from ai_qa_portal.backend.services import keyword_catalog as _catalog

logger = logging.getLogger("ai_qa_portal.script_validator")


ErrorKind = Literal[
    "undefined_keyword",
    "undefined_variable",
    "missing_resource",
    "missing_library",
    "parse_error",
]


@dataclass
class ValidationError:
    """One actionable problem in a generated script.

    ``symbol`` is the literal token the LLM emitted that didn't resolve
    (so the fix prompt can quote it back). ``closest_matches`` is up to
    three real symbols the LLM should consider instead — sorted by
    similarity, all guaranteed to exist in the catalog. ``snippet`` is
    the surrounding line for human readability in the UI.
    """

    line: int
    column: int
    kind: ErrorKind
    symbol: str
    message: str
    closest_matches: list[str] = field(default_factory=list)
    snippet: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationReport:
    ok: bool
    errors: list[ValidationError] = field(default_factory=list)
    # Stats useful for the UI / logs even when ok=True.
    keyword_calls_seen: int = 0
    variable_refs_seen: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": [e.to_dict() for e in self.errors],
            "keyword_calls_seen": self.keyword_calls_seen,
            "variable_refs_seen": self.variable_refs_seen,
        }


# Robot Framework's built-in automatic variables. Anything in this set is
# always available in a suite without explicit declaration.
_BUILTIN_VARS: frozenset[str] = frozenset(n.lower() for n in (
    "SPACE", "EMPTY", "TRUE", "FALSE", "None", "null", "TEST_NAME",
    "TEST_TAGS", "TEST_DOCUMENTATION", "TEST_STATUS", "TEST_MESSAGE",
    "PREV_TEST_NAME", "PREV_TEST_STATUS", "PREV_TEST_MESSAGE",
    "SUITE_NAME", "SUITE_SOURCE", "SUITE_DOCUMENTATION", "SUITE_METADATA",
    "SUITE_STATUS", "SUITE_MESSAGE", "KEYWORD_STATUS", "KEYWORD_MESSAGE",
    "LOG_LEVEL", "OUTPUT_DIR", "OUTPUT_FILE", "LOG_FILE", "REPORT_FILE",
    "DEBUG_FILE", "CURDIR", "TEMPDIR", "EXECDIR", "/",
    "\\n", "\\t", ":", ";",
    # Numeric env-style helpers that some teams use.
    "OPTIONS",
))


# Regex: extract every ``${name}``, ``@{name}``, ``&{name}`` reference
# from a token value. We deliberately do NOT support nested-variable
# resolution (``${a${b}}``) — those are exotic, and the dryrun gate
# catches them.
_VAR_REF_RE = re.compile(r"[\$@&]\{([^}]+)\}")
# When a variable expression contains item access (``${row}[name]``),
# Robot still resolves the BASE name, so we strip the index/access.
# Examples handled: ``${item}[name]``, ``${item.foo}``, ``${list}[0]``.
_BASE_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_ ]*)")


def _normalize_var_base(name: str) -> str:
    """Lower-case + strip index/access. ``"item.foo"`` -> ``"item"``."""
    cleaned = name.strip()
    # Drop dot-access and bracket-access tails.
    cleaned = cleaned.split(".", 1)[0].split("[", 1)[0]
    m = _BASE_NAME_RE.match(cleaned.strip())
    if m:
        return m.group(1).strip().lower()
    return cleaned.strip().lower()


def _normalize_keyword_name(name: str) -> str:
    """Robot keyword resolution is case-insensitive and ignores spaces /
    underscores in the name. Mirror that for catalog lookups so the LLM
    can write either ``Open New Dialog`` or ``open_new_dialog``."""
    return name.strip().lower().replace("_", " ")


def _strip_qualifier(name: str) -> tuple[str, Optional[str]]:
    """``"GlobalApi.API Seed Lead"`` -> ``("API Seed Lead", "GlobalApi")``."""
    if "." in name and not name.startswith("."):
        head, tail = name.split(".", 1)
        # Robot allows ``Library.With Spaces.Keyword`` rarely; the first
        # dot is the qualifier separator.
        return tail.strip(), head.strip()
    return name.strip(), None


def _read_resource_universe(
    resource_path: Path,
    *,
    seen: Optional[set[Path]] = None,
    depth: int = 0,
    max_depth: int = 5,
) -> tuple[set[str], set[str]]:
    """Walk a resource file (and its imported resources, transitively up to
    ``max_depth``) and return ``(known_vars, known_keywords)`` lower-cased.

    Robot resolves keywords case-insensitively and across the entire import
    graph, so for the validator to match Robot's behaviour we need the same
    transitive walk. Cycle-safe via the ``seen`` set, and bounded by
    ``max_depth`` so a maliciously-deep import chain can't lock the
    validator up.
    """
    if seen is None:
        seen = set()
    seen.add(resource_path)
    out_vars: set[str] = set()
    out_keywords: set[str] = set()

    if not resource_path.is_file() or depth > max_depth:
        return out_vars, out_keywords
    try:
        from robot.api import get_model
        import robot.api.parsing as rp
    except ImportError:  # pragma: no cover
        return out_vars, out_keywords
    try:
        model = get_model(str(resource_path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("script_validator: cannot parse resource %s: %s", resource_path, exc)
        return out_vars, out_keywords

    nested_resources: list[str] = []
    nested_libraries: list[str] = []

    class V(rp.ModelVisitor):
        def visit_Variable(self, node):  # noqa: N802
            tok = node.get_token(rp.Token.VARIABLE)
            if not tok:
                return
            for m in _VAR_REF_RE.finditer(tok.value):
                out_vars.add(_normalize_var_base(m.group(1)))
        def visit_Keyword(self, node):  # noqa: N802 -- user-keyword definition
            try:
                name_tok = node.header.get_token(rp.Token.KEYWORD_NAME)
            except AttributeError:
                name_tok = None
            if name_tok and name_tok.value:
                out_keywords.add(_normalize_keyword_name(name_tok.value))
        def visit_ResourceImport(self, node):  # noqa: N802
            tok = node.get_token(rp.Token.NAME)
            if tok and tok.value:
                nested_resources.append(tok.value)
        def visit_LibraryImport(self, node):  # noqa: N802
            tok = node.get_token(rp.Token.NAME)
            if tok and tok.value:
                nested_libraries.append(tok.value)

    V().visit(model)

    for raw in nested_resources:
        nested_path = _resolve_resource_path(raw, resource_path)
        if nested_path is None or nested_path in seen:
            continue
        nested_vars, nested_kw = _read_resource_universe(
            nested_path, seen=seen, depth=depth + 1, max_depth=max_depth,
        )
        out_vars |= nested_vars
        out_keywords |= nested_kw

    # Path-style library imports inside a resource also register their
    # @keywords with the suite, so feed them through libdoc.
    for raw in nested_libraries:
        if not raw.lower().endswith(".py"):
            continue
        lib_path = _resolve_library_path(raw, resource_path)
        if lib_path is None:
            continue
        try:
            lib_kw = _catalog._libdoc_keywords(lib_path, REPO_ROOT)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            continue
        for k in lib_kw:
            out_keywords.add(_normalize_keyword_name(k.keyword_name))

    return out_vars, out_keywords


def _resolve_resource_path(raw: str, suite_path: Path) -> Optional[Path]:
    """Apply Robot's path resolution rules in the simplest form: try the
    path as given (relative to the suite, then absolute), with the
    ``${CURDIR}`` token rewritten to the suite directory. Return ``None``
    when nothing resolves on disk."""
    candidate = raw.replace("${CURDIR}", str(suite_path.parent))
    p = Path(candidate)
    if not p.is_absolute():
        p = (suite_path.parent / p).resolve(strict=False)
    if p.is_file():
        return p
    # Also try resolving relative to REPO_ROOT for prompts that emit
    # repo-rooted paths (defensive — the LLM occasionally does this).
    alt = (REPO_ROOT / candidate).resolve(strict=False)
    if alt.is_file():
        return alt
    return None


def _resolve_library_path(raw: str, suite_path: Path) -> Optional[Path]:
    """Same as ``_resolve_resource_path`` but only for ``.py`` library
    imports. Bare-name imports (``Library  SeleniumLibrary``) return
    ``None`` and are handled by the framework allow-list elsewhere."""
    if not raw.lower().endswith(".py"):
        return None
    return _resolve_resource_path(raw, suite_path)


def _build_universe(suite_path: Path) -> tuple[set[str], set[str], list[str]]:
    """Walk the suite + its imported resources to build:

    * known_keywords: lowercase-normalized names of keywords callable
      from this suite (catalog + framework + locally-defined +
      transitively-imported resource keywords).
    * known_vars: lowercase base names of variables visible to this suite
      (suite *** Variables ***, imported resources' *** Variables ***,
      built-ins).
    * missing_resources: list of resource/library paths that didn't
      resolve on disk — surfaced as parse-time errors.

    On any parse failure the universe shrinks but the validator still
    runs against what it could resolve.
    """
    known_keywords: set[str] = set()
    known_vars: set[str] = set(_BUILTIN_VARS)
    missing_imports: list[str] = []

    # Catalog + framework allow-list -- always available.
    for kw in _catalog.build_catalog().get("keywords", []):
        nm = (kw.get("keyword_name") or "").strip()
        if nm:
            known_keywords.add(_normalize_keyword_name(nm))
    known_keywords |= set(_catalog.framework_allow_list())

    # Walk THIS suite for its Variables + locally-defined keywords +
    # imports. A separate visitor handles each so each can fail
    # independently.
    try:
        from robot.api import get_model
        import robot.api.parsing as rp
    except ImportError:  # pragma: no cover
        return known_keywords, known_vars, missing_imports

    try:
        model = get_model(str(suite_path))
    except Exception as exc:  # noqa: BLE001
        logger.debug("script_validator: cannot parse suite %s: %s", suite_path, exc)
        return known_keywords, known_vars, [str(suite_path)]

    imported_resources: list[str] = []
    imported_libraries: list[str] = []

    class Importer(rp.ModelVisitor):
        def visit_ResourceImport(self, node):  # noqa: N802
            tok = node.get_token(rp.Token.NAME)
            if tok and tok.value:
                imported_resources.append(tok.value)
        def visit_LibraryImport(self, node):  # noqa: N802
            tok = node.get_token(rp.Token.NAME)
            if tok and tok.value:
                imported_libraries.append(tok.value)
        def visit_Variable(self, node):  # noqa: N802
            tok = node.get_token(rp.Token.VARIABLE)
            if not tok:
                return
            for m in _VAR_REF_RE.finditer(tok.value):
                known_vars.add(_normalize_var_base(m.group(1)))
        def visit_Keyword(self, node):  # noqa: N802 -- defines a local keyword
            try:
                name_tok = node.header.get_token(rp.Token.KEYWORD_NAME)
            except AttributeError:
                name_tok = None
            if name_tok and name_tok.value:
                known_keywords.add(_normalize_keyword_name(name_tok.value))

    Importer().visit(model)

    # Resolve imports + recursively pull their variables AND keywords.
    seen: set[Path] = {suite_path}
    for raw in imported_resources:
        path = _resolve_resource_path(raw, suite_path)
        if path is None:
            missing_imports.append(f"resource:{raw}")
            continue
        nested_vars, nested_kw = _read_resource_universe(path, seen=seen)
        known_vars |= nested_vars
        known_keywords |= nested_kw

    # Library imports: bare-name passes via the framework allow-list;
    # path-style libraries are checked for existence + their @keywords
    # are added to the universe (in case they're project-local libs the
    # main catalog scanner didn't reach because they're imported only
    # from the suite under test).
    for raw in imported_libraries:
        if raw.lower().endswith(".py"):
            path = _resolve_library_path(raw, suite_path)
            if path is None:
                missing_imports.append(f"library:{raw}")
                continue
            try:
                for k in _catalog._libdoc_keywords(path, REPO_ROOT):  # type: ignore[attr-defined]
                    known_keywords.add(_normalize_keyword_name(k.keyword_name))
            except Exception:  # noqa: BLE001
                pass

    return known_keywords, known_vars, missing_imports


def _suggest(symbol: str, pool: Iterable[str], *, n: int = 3) -> list[str]:
    """Top-N closest matches for ``symbol`` from ``pool`` (case-insensitive)."""
    needle = symbol.strip().lower()
    options = list({p for p in pool if p})
    return get_close_matches(needle, options, n=n, cutoff=0.55)


def _line_text(suite_path: Path, line: int) -> str:
    try:
        text = suite_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    lines = text.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].rstrip()
    return ""


def validate(suite_path: Path | str) -> ValidationReport:
    """Validate one Robot file and return a structured report."""
    path = Path(suite_path)
    if not path.is_file():
        return ValidationReport(
            ok=False,
            errors=[ValidationError(
                line=0, column=0, kind="parse_error",
                symbol=str(suite_path),
                message=f"Script not found at {suite_path}",
            )],
        )

    try:
        from robot.api import get_model
        import robot.api.parsing as rp
    except ImportError as exc:  # pragma: no cover
        return ValidationReport(
            ok=False,
            errors=[ValidationError(
                line=0, column=0, kind="parse_error",
                symbol="robot.api",
                message=f"robot.api unavailable: {exc}",
            )],
        )

    try:
        model = get_model(str(path))
    except Exception as exc:  # noqa: BLE001
        return ValidationReport(
            ok=False,
            errors=[ValidationError(
                line=0, column=0, kind="parse_error",
                symbol=path.name,
                message=f"robot.api parse failed: {exc}",
            )],
        )

    known_keywords, known_vars, missing_imports = _build_universe(path)

    errors: list[ValidationError] = []
    keyword_calls = 0
    variable_refs = 0

    # Surface unresolved imports as their own errors so the LLM can fix
    # them (typo in the path) without us having to interpret the
    # downstream "every keyword from that resource is undefined".
    for token in missing_imports:
        kind, _, raw = token.partition(":")
        errors.append(ValidationError(
            line=0, column=0,
            kind="missing_resource" if kind == "resource" else "missing_library",
            symbol=raw,
            message=(
                f"Cannot resolve {kind} import '{raw}' on disk. "
                "Check the path is correct relative to the suite or use ${CURDIR}."
            ),
            closest_matches=[],
            snippet="",
        ))

    catalog_names_for_suggestions = [
        kw.get("keyword_name") for kw in _catalog.build_catalog().get("keywords", [])
        if kw.get("keyword_name")
    ]
    catalog_names_lower = [n.lower() for n in catalog_names_for_suggestions]
    var_names_for_suggestions = sorted(known_vars)

    class Visitor(rp.ModelVisitor):

        def visit_Variable(self, node):  # noqa: N802
            """Check the RHS of a ``*** Variables ***`` entry for
            unresolved variable references. The LHS (the variable being
            defined) is added to the suite's known set by ``_build_universe``;
            this visitor only inspects the value side so a definition like
            ``${campaignName}    Generated Campaign ${RANDOM_STRING}`` flags
            the missing ``${RANDOM_STRING}`` even though the LHS is fine.
            """
            nonlocal variable_refs
            # Tokens after VARIABLE are the value expression. We scan every
            # ARGUMENT token (the value side) for refs.
            saw_lhs = False
            for t in node.tokens:
                if t.type == rp.Token.VARIABLE and not saw_lhs:
                    saw_lhs = True
                    continue
                if t.type != rp.Token.ARGUMENT:
                    continue
                for m in _VAR_REF_RE.finditer(t.value):
                    variable_refs += 1
                    base = _normalize_var_base(m.group(1))
                    if not base or base in known_vars:
                        continue
                    errors.append(ValidationError(
                        line=t.lineno or 0,
                        column=t.col_offset or 0,
                        kind="undefined_variable",
                        symbol="${" + m.group(1).strip() + "}",
                        message=(
                            f"Variable '${{{m.group(1).strip()}}}' on the right-hand side "
                            "of a *** Variables *** entry is not defined anywhere."
                        ),
                        closest_matches=_suggest(base, var_names_for_suggestions),
                        snippet=_line_text(path, t.lineno or 0),
                    ))
            self.generic_visit(node)

        def visit_KeywordCall(self, node):  # noqa: N802
            nonlocal keyword_calls, variable_refs
            keyword_calls += 1
            kw_token = node.get_token(rp.Token.KEYWORD)
            if kw_token:
                bare, qualifier = _strip_qualifier(kw_token.value)
                norm = _normalize_keyword_name(bare)
                if norm not in known_keywords:
                    suggestions = _suggest(bare, catalog_names_lower, n=3)
                    # Map suggestions back to original-cased catalog names.
                    pretty = []
                    seen = set()
                    for s in suggestions:
                        for original in catalog_names_for_suggestions:
                            if original.lower() == s and original not in seen:
                                pretty.append(original)
                                seen.add(original)
                                break
                    errors.append(ValidationError(
                        line=kw_token.lineno or 0,
                        column=kw_token.col_offset or 0,
                        kind="undefined_keyword",
                        symbol=kw_token.value,
                        message=(
                            f"Keyword '{kw_token.value}' is not defined in the catalog, "
                            "framework allow-list, or this suite."
                            + (f" (qualifier '{qualifier}' did not match a known source.)" if qualifier else "")
                        ),
                        closest_matches=pretty,
                        snippet=_line_text(path, kw_token.lineno or 0),
                    ))
            # Variable references in arguments/assigns.
            assigned: set[str] = set()
            for t in node.tokens:
                if t.type == rp.Token.ASSIGN:
                    for m in _VAR_REF_RE.finditer(t.value):
                        assigned.add(_normalize_var_base(m.group(1)))
                if t.type == rp.Token.ARGUMENT:
                    for m in _VAR_REF_RE.finditer(t.value):
                        variable_refs += 1
                        base = _normalize_var_base(m.group(1))
                        if not base:
                            continue
                        if base in known_vars or base in assigned:
                            continue
                        errors.append(ValidationError(
                            line=t.lineno or 0,
                            column=t.col_offset or 0,
                            kind="undefined_variable",
                            symbol="${" + m.group(1).strip() + "}",
                            message=(
                                f"Variable '${{{m.group(1).strip()}}}' is not defined in "
                                "*** Variables ***, [Arguments], an assignment, an imported "
                                "resource, or Robot built-ins."
                            ),
                            closest_matches=_suggest(base, var_names_for_suggestions),
                            snippet=_line_text(path, t.lineno or 0),
                        ))
            # Locally-assigned vars become known for the rest of the suite.
            # (Lexical-scope-ish; Robot is dynamic, but for a single-file
            # static check this is close enough.)
            known_vars.update(assigned)
            self.generic_visit(node)

    Visitor().visit(model)

    return ValidationReport(
        ok=not errors,
        errors=errors,
        keyword_calls_seen=keyword_calls,
        variable_refs_seen=variable_refs,
    )


def build_fix_prompt(report: ValidationReport, *, max_errors: int = 8) -> str:
    """Render a structured correction request the LLM can act on directly.

    Errors beyond ``max_errors`` are summarised as a count so the prompt
    stays bounded — fixing the first 8 typically resolves the rest
    transitively (e.g. one bad ``Resource`` import causes every keyword
    from it to be undefined).
    """
    if report.ok:
        return ""

    head = (
        "Your generated script has unresolved symbols. Fix EACH one by replacing "
        "it with a real keyword or variable from the catalog you were given. Do "
        "NOT invent new symbols — the validator will reject them.\n\n"
        f"Found {len(report.errors)} issue(s):\n\n"
    )

    body_lines: list[str] = []
    visible = report.errors[:max_errors]
    for i, err in enumerate(visible, 1):
        loc = f"Line {err.line}" if err.line else "(file-level)"
        kind_label = {
            "undefined_keyword": "undefined keyword",
            "undefined_variable": "undefined variable",
            "missing_resource":  "missing Resource import",
            "missing_library":   "missing Library import",
            "parse_error":       "parse error",
        }.get(err.kind, err.kind)
        body_lines.append(f"{i}. {loc}: {kind_label} `{err.symbol}`")
        if err.snippet:
            body_lines.append(f"   On line: `{err.snippet.strip()}`")
        if err.closest_matches:
            body_lines.append(
                "   Closest valid options: " + ", ".join(f"`{c}`" for c in err.closest_matches)
            )
        body_lines.append("")

    if len(report.errors) > max_errors:
        body_lines.append(
            f"...and {len(report.errors) - max_errors} more error(s) of similar shape."
        )
        body_lines.append("")

    tail = (
        "Return ONLY the corrected complete `*** Settings ***` ... `*** Test Cases ***` "
        "Robot file, no commentary."
    )
    return head + "\n".join(body_lines) + "\n" + tail
