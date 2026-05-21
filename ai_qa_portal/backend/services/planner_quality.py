"""Planner-side quality controls: signatures, validation, sequence linting.

This module is the "brain" gatekeeper between the LLM planner and RF-MCP
execution. It exists because the planner -- even with a great playbook --
will hallucinate keyword names, miss required args, and produce
sequences that violate keyword side-effect contracts (e.g. asking the
runtime to "verify redirection" right after a PO `Create A New X` keyword
that already redirects internally).

The three public surfaces:

1. ``signature_index()`` -- structured per-keyword metadata derived from
   the live catalog. Includes argument names, default flags, named-only
   vs positional, and a one-line side-effect hint extracted from the
   keyword's docstring. Used by:
   - the planner prompt (``signatures_for_prompt``)
   - the validator (``validate_steps``)

2. ``validate_steps(steps)`` -- pre-execution validator. Accepts the
   LLM's raw plan, returns a list of ``StepIssue``s with closest-match
   suggestions for any unresolvable keyword and a structured fix prompt
   the caller can feed back to the LLM for retry.

3. ``lint_sequence(steps)`` -- rule-based linter that catches common
   sequencing mistakes (verify-after-self-saving-keyword, related-list
   ops without a record context, repeated logins, etc.).

All three are pure functions over the plan; nothing here touches the
network or the RF-MCP runtime. That keeps the validator cheap (~10 ms
on the Pentair catalog) so the replan loop can run it three times
without measurable user-visible latency.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from ai_qa_portal.backend.services import keyword_catalog

logger = logging.getLogger("ai_qa_portal.planner_quality")


# --- Signature index ------------------------------------------------------


@dataclass(frozen=True)
class KeywordSignature:
    """Structured view of a single catalog keyword's calling shape."""

    qualified: str  # "SalesPO.Create A New Lead"
    bare: str  # "Create A New Lead"
    library: str  # "SalesPO"
    positional: tuple[str, ...]  # arg names without defaults
    named: tuple[str, ...]  # arg names with defaults (named-arg-friendly)
    var_args: bool  # accepts @{args}
    var_named: bool  # accepts &{kwargs}
    doc_summary: str  # one-line description
    side_effects: tuple[str, ...]  # extracted intent hints (see _extract_side_effects)

    @property
    def min_args(self) -> int:
        return len(self.positional)

    @property
    def max_args(self) -> int | None:
        if self.var_args:
            return None
        return len(self.positional) + len(self.named)

    def signature_hint(self) -> str:
        """Human-readable single-line signature for inclusion in the LLM prompt."""
        parts: list[str] = []
        if self.positional:
            parts.append("positional: " + ", ".join(self.positional))
        if self.named:
            parts.append("named (optional): " + ", ".join(f"{n}=" for n in self.named))
        if self.var_args:
            parts.append("@args")
        if self.var_named:
            parts.append("&kwargs")
        if not parts:
            parts.append("no args")
        return "; ".join(parts)


_DEFAULT_RE = re.compile(r"^(?P<name>[A-Za-z_][\w]*)\s*=\s*(?P<default>.+?)\s*$")


def _parse_arg_token(token: str) -> tuple[str, bool, bool, bool]:
    """Return (canonical_name, has_default, is_var_args, is_var_named).

    Tokens come in three shapes from the catalog:
      - ``"name"``                   plain positional
      - ``"name=default"``           named with default (named-arg-friendly)
      - ``"@{args}"`` / ``"@args"``  ``@`` prefix == var_args (Robot/Python *args)
      - ``"&{kwargs}"`` / ``"&kw"``  ``&`` prefix == var_named (Robot/Python **kwargs)

    The Robot ``${name}`` wrapping is stripped so we get pure parameter names.
    """
    raw = token.strip()
    if not raw:
        return "", False, False, False
    is_var_args = raw.startswith("@")
    is_var_named = raw.startswith("&")
    cleaned = raw.lstrip("@&").strip()
    # Strip Robot ${...} wrapping if present.
    inner_match = re.match(r"^\$?\{?([A-Za-z_][\w]*)\}?\s*(?:=\s*(.+))?$", cleaned)
    if inner_match:
        name = inner_match.group(1)
        has_default = inner_match.group(2) is not None
        return name, has_default, is_var_args, is_var_named
    # Fall back to ``name=default`` plain form (Python-style libdoc tokens).
    m = _DEFAULT_RE.match(cleaned)
    if m:
        return m.group("name"), True, is_var_args, is_var_named
    return cleaned, False, is_var_args, is_var_named


# Side-effect heuristics: the planner needs to know e.g. that "Create A
# New Lead" both saves the form AND closes the modal AND lands on the
# detail page, so it doesn't plan a follow-up "Verify Redirection".
# Each rule pairs a regex against the docstring with a short tag the
# linter can match against.
_SIDE_EFFECT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(saves?|clicks?\s+save|submits?)\b", re.I), "saves_form"),
    (re.compile(r"\b(closes?\s+(the\s+)?modal|modal\s+closes?|dialog\s+closes?)\b", re.I), "closes_modal"),
    (re.compile(r"\bredirect(?:s|ed|ion)?\b", re.I), "redirects"),
    (re.compile(r"\b(record\s+detail|detail\s+page)\b", re.I), "lands_on_detail"),
    (re.compile(r"\b(opens?\s+(the\s+)?modal|opens?\s+(the\s+)?dialog|opens?\s+new)\b", re.I), "opens_modal"),
    (re.compile(r"\b(opens?\s+(the\s+)?app|launch(?:es)?\s+(the\s+)?app|app\s+launcher)\b", re.I), "opens_app"),
    (re.compile(r"\b(login|signs?\s+in|authenticate)\b", re.I), "logs_in"),
    (re.compile(r"\b(verify|verifies|verifies?\s+that|asserts?|should\s+be|expects?)\b", re.I), "is_verification"),
    (re.compile(r"\bdelete(?:s|d)?\b", re.I), "deletes_record"),
    (re.compile(r"\b(navigates?|goes?\s+to|opens?\s+record)\b", re.I), "navigates"),
    (re.compile(r"\b(toast|success\s+message)\b", re.I), "consumes_toast"),
)


def _extract_side_effects(doc: str) -> tuple[str, ...]:
    if not doc:
        return ()
    found: set[str] = set()
    for rx, tag in _SIDE_EFFECT_RULES:
        if rx.search(doc):
            found.add(tag)
    return tuple(sorted(found))


_signature_cache: dict[str, KeywordSignature] | None = None
_signature_cache_sig: tuple | None = None


def _qualified_name(kw: dict) -> tuple[str, str, str]:
    """Return (qualified, bare, library) for a catalog entry."""
    name = (kw.get("keyword_name") or "").strip()
    src_lib = (kw.get("source_library") or "").strip()
    src = kw.get("source_file") or ""
    lib = src_lib or (Path(src).stem if src else "")
    if "." in name or not lib:
        return name, name.split(".", 1)[-1], (name.split(".", 1)[0] if "." in name else lib)
    return f"{lib}.{name}", name, lib


def signature_index(force: bool = False) -> dict[str, KeywordSignature]:
    """Map ``qualified_name_lower`` → ``KeywordSignature`` for the live catalog.

    Cached against ``keyword_catalog._scan_signature`` so this stays in
    sync with hot reloads of Resource files / library imports.
    """
    global _signature_cache, _signature_cache_sig  # noqa: PLW0603

    catalog = keyword_catalog.build_catalog(force=force)
    sig = catalog.get("generated_at")  # cheap stable signature for our cache
    if (
        not force
        and _signature_cache is not None
        and _signature_cache_sig == sig
    ):
        return _signature_cache

    out: dict[str, KeywordSignature] = {}
    for kw in catalog.get("keywords", []):
        qualified, bare, library = _qualified_name(kw)
        if not qualified:
            continue
        positional: list[str] = []
        named: list[str] = []
        var_args = False
        var_named = False
        for token in kw.get("arguments") or []:
            name, has_default, is_var_args, is_var_named = _parse_arg_token(str(token))
            if is_var_args:
                var_args = True
                continue
            if is_var_named:
                var_named = True
                continue
            if not name:
                continue
            if has_default:
                named.append(name)
            else:
                positional.append(name)

        doc = (kw.get("documentation") or kw.get("natural_language_summary") or "").strip()
        # Skip the auto-generated "Robot Framework keyword: X." stub.
        if doc.startswith("Robot Framework keyword:"):
            doc = ""
        # Reduce to a single line for prompt + side-effect extraction.
        doc_one_line = doc.splitlines()[0].strip() if doc else ""
        side_effects = _extract_side_effects(doc)

        out[qualified.lower()] = KeywordSignature(
            qualified=qualified,
            bare=bare,
            library=library,
            positional=tuple(positional),
            named=tuple(named),
            var_args=var_args,
            var_named=var_named,
            doc_summary=doc_one_line,
            side_effects=side_effects,
        )

    _signature_cache = out
    _signature_cache_sig = sig
    logger.debug("signature_index: cached %s keywords", len(out))
    return out


# --- Prompt helpers -------------------------------------------------------


def signatures_for_prompt(*, max_doc_chars: int = 100) -> str:
    """Compact catalog projection optimised for planner accuracy.

    Each line: ``QualifiedName :: signature_hint :: doc``. The format
    intentionally differs from ``compact_for_prompt``'s JSON because the
    planner consistently reads "name :: hint :: doc" lines correctly while
    the JSON form encourages it to ignore the args field on long catalogs.
    """
    lines: list[str] = []
    for sig in sorted(signature_index().values(), key=lambda s: s.qualified.lower()):
        doc = sig.doc_summary
        if doc and len(doc) > max_doc_chars:
            doc = doc[: max_doc_chars - 1].rstrip() + "…"
        line = f"{sig.qualified}  ::  {sig.signature_hint()}"
        if doc:
            line += f"  ::  {doc}"
        lines.append(line)
    return "\n".join(lines)


# --- Validation -----------------------------------------------------------


@dataclass
class StepIssue:
    """One validator finding against a planned step."""

    index: int  # 1-based step index
    severity: str  # "error" | "warning"
    code: str  # short machine-readable identifier
    message: str  # human-readable
    keyword: str
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "step_index": self.index,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "keyword": self.keyword,
            "suggestions": list(self.suggestions),
        }


_KEYWORD_LOOKUP_CACHE: dict[str, str] | None = None
_KEYWORD_LOOKUP_SIG: tuple | None = None


def _keyword_lookup() -> dict[str, str]:
    """Map of every recognised name (qualified + bare, lowercased) →
    canonical qualified name. Bare-name collisions resolve to the alphabetically
    first library so the planner can still call ``Login To Sandbox`` without
    a prefix; downstream RF-MCP resolves both forms.
    """
    global _KEYWORD_LOOKUP_CACHE, _KEYWORD_LOOKUP_SIG  # noqa: PLW0603

    sig = keyword_catalog.build_catalog().get("generated_at")
    if _KEYWORD_LOOKUP_CACHE is not None and _KEYWORD_LOOKUP_SIG == sig:
        return _KEYWORD_LOOKUP_CACHE

    out: dict[str, str] = {}
    for canon in signature_index().values():
        out[canon.qualified.lower()] = canon.qualified
        # Only add the bare form if we don't already have one (first-write wins
        # → stable and deterministic).
        out.setdefault(canon.bare.lower(), canon.qualified)
    _KEYWORD_LOOKUP_CACHE = out
    _KEYWORD_LOOKUP_SIG = sig
    return out


def resolve_keyword(name: str) -> str | None:
    """Return the canonical qualified name for an LLM-emitted keyword.

    Accepts ``"SalesPO.Create A New Lead"``, ``"Create A New Lead"``,
    ``"salespo.create a new lead"``, etc. Returns ``None`` when no match.
    """
    if not name:
        return None
    return _keyword_lookup().get(name.strip().lower())


def closest_keywords(name: str, *, n: int = 3) -> list[str]:
    """Top-N closest known keyword names (qualified) for an unrecognised emit."""
    if not name:
        return []
    lookup = _keyword_lookup()
    matches = get_close_matches(name.strip().lower(), list(lookup.keys()), n=n, cutoff=0.55)
    seen: set[str] = set()
    out: list[str] = []
    for m in matches:
        canon = lookup[m]
        if canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
    return out


_NAMED_ARG_RE = re.compile(r"^([A-Za-z_][\w]*)\s*=\s*(.*)$", re.DOTALL)


def _classify_arg(arg: str) -> tuple[str, str | None, str]:
    """Return (kind, name, value) where kind is ``"positional"`` or ``"named"``.

    Recognises Robot's ``name=value`` syntax. ``${var}`` or ``${var}=...``
    forms are treated as positional (Sanitize step already strips
    ``${var}=value`` patterns to bare values).
    """
    if not isinstance(arg, str):
        return "positional", None, str(arg)
    a = arg.strip()
    if not a:
        return "positional", None, ""
    # Variable references like ${foo} are always positional.
    if a.startswith("${"):
        return "positional", None, a
    m = _NAMED_ARG_RE.match(a)
    if m and not m.group(1).startswith("$"):
        return "named", m.group(1), m.group(2)
    return "positional", None, a


def validate_steps(steps: list[dict]) -> list[StepIssue]:
    """Validate a planned step list against the live catalog.

    Returns an empty list when everything is fine. Each issue carries
    structured metadata so the caller can render either a UI panel or a
    fix-prompt for the LLM.
    """
    issues: list[StepIssue] = []
    sigs = signature_index()
    for i, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            issues.append(
                StepIssue(
                    index=i,
                    severity="error",
                    code="bad_shape",
                    message=f"Step #{i} is not an object: {raw!r}",
                    keyword="",
                )
            )
            continue
        kw = str(raw.get("keyword") or "").strip()
        args = list(raw.get("args") or [])
        if not kw:
            issues.append(
                StepIssue(
                    index=i,
                    severity="error",
                    code="missing_keyword",
                    message=f"Step #{i} has no keyword name.",
                    keyword="",
                )
            )
            continue

        canonical = resolve_keyword(kw)
        if canonical is None:
            issues.append(
                StepIssue(
                    index=i,
                    severity="error",
                    code="unknown_keyword",
                    message=(
                        f"Step #{i}: keyword '{kw}' is not in the catalog."
                    ),
                    keyword=kw,
                    suggestions=closest_keywords(kw),
                )
            )
            continue

        sig = sigs.get(canonical.lower())
        if sig is None:
            # Catalog/signature index race -- skip arg validation.
            continue

        # --- Argument validation --------------------------------------
        positional_args: list[Any] = []
        named_args: dict[str, Any] = {}
        for arg in args:
            kind, name, value = _classify_arg(str(arg))
            if kind == "named" and name:
                named_args[name] = value
            else:
                positional_args.append(value)

        # Too many positional args.
        if not sig.var_args and len(positional_args) > sig.min_args + len(sig.named):
            issues.append(
                StepIssue(
                    index=i,
                    severity="error",
                    code="too_many_args",
                    message=(
                        f"Step #{i}: '{sig.qualified}' accepts at most "
                        f"{sig.min_args + len(sig.named)} args, got "
                        f"{len(positional_args)} positional + "
                        f"{len(named_args)} named."
                    ),
                    keyword=sig.qualified,
                    suggestions=[sig.signature_hint()],
                )
            )

        # Required positional args missing (only when there are no named args
        # being passed for them, since Robot allows mixing).
        if len(positional_args) < sig.min_args and not sig.var_args:
            named_overlap = set(named_args).intersection(sig.positional)
            if (len(positional_args) + len(named_overlap)) < sig.min_args:
                missing = [
                    p for p in sig.positional[len(positional_args):]
                    if p not in named_args
                ]
                issues.append(
                    StepIssue(
                        index=i,
                        severity="error",
                        code="missing_args",
                        message=(
                            f"Step #{i}: '{sig.qualified}' requires "
                            f"{sig.min_args} positional arg(s); missing: "
                            f"{', '.join(missing) or '(unknown)'}."
                        ),
                        keyword=sig.qualified,
                        suggestions=[sig.signature_hint()],
                    )
                )

        # Unknown named args.
        if not sig.var_named:
            allowed = set(sig.positional) | set(sig.named)
            for n in named_args:
                if n not in allowed:
                    issues.append(
                        StepIssue(
                            index=i,
                            severity="error",
                            code="unknown_named_arg",
                            message=(
                                f"Step #{i}: '{sig.qualified}' has no named "
                                f"argument '{n}'. Allowed: "
                                f"{', '.join(sorted(allowed)) or '(none)'}."
                            ),
                            keyword=sig.qualified,
                            suggestions=[sig.signature_hint()],
                        )
                    )

    return issues


def format_issues_as_fix_prompt(issues: list[StepIssue]) -> str:
    """Render the validator's issues into a fix-prompt the LLM can act on.

    The format mirrors what ``script_validation_loop`` sends to the LLM
    for Quick Generate: numbered errors with closest-match suggestions.
    """
    if not issues:
        return ""
    lines = [
        "The previous plan failed validation. Fix EACH problem and resubmit "
        "the entire JSON array (no diff). Use the suggestions verbatim when "
        "a closest match is offered.",
        "",
    ]
    for issue in issues:
        suggestions = ""
        if issue.suggestions:
            suggestions = "  Suggestions: " + " | ".join(issue.suggestions)
        lines.append(f"- {issue.message}{suggestions}")
    return "\n".join(lines)


# --- Sequence linter ------------------------------------------------------


@dataclass
class SequenceIssue:
    """One linter finding against the planned sequence."""

    index: int  # 1-based step index of the offending step
    code: str
    message: str
    fix_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "step_index": self.index,
            "code": self.code,
            "message": self.message,
            "fix_hint": self.fix_hint,
        }


_VERIFY_AFTER_SAVE_PATTERNS = (
    re.compile(r"verify\s+redirection", re.I),
    re.compile(r"get\s+success\s+toast", re.I),
    re.compile(r"verify\s+success\s+toast", re.I),
)


def _kw_signature_for(step: dict) -> KeywordSignature | None:
    canonical = resolve_keyword(str(step.get("keyword") or ""))
    if not canonical:
        return None
    return signature_index().get(canonical.lower())


def lint_sequence(steps: list[dict]) -> list[SequenceIssue]:
    """Apply rule-based sequence sanity checks.

    Each rule below is derived from a real Stepwise failure mode we
    observed in the Pentair runs, encoded so the planner can be told to
    fix the order BEFORE we burn 60 seconds of browser time hitting a
    timeout for an out-of-context verification keyword.
    """
    issues: list[SequenceIssue] = []
    if not steps:
        return issues

    sigs = [(_kw_signature_for(s), s) for s in steps]

    login_indices: list[int] = []
    has_app_open = False
    on_detail_page = False
    last_self_saving_step: tuple[int, KeywordSignature] | None = None

    for i, (sig, step) in enumerate(sigs, start=1):
        kw = str(step.get("keyword") or "")
        kw_lower = kw.lower()

        # Rule 1: track logins; multiple is almost always a planner error.
        if sig and "logs_in" in sig.side_effects:
            login_indices.append(i)
            if len(login_indices) > 1:
                issues.append(
                    SequenceIssue(
                        index=i,
                        code="duplicate_login",
                        message=(
                            f"Step #{i} ({kw}) attempts a second login. "
                            "RF-MCP keeps the browser session warm; one Login "
                            "To Sandbox at the start is enough."
                        ),
                        fix_hint="Remove this step.",
                    )
                )

        # Rule 2: app-launch must precede object-tab navigation. We approximate
        # this via side-effect tags.
        if sig and "opens_app" in sig.side_effects:
            has_app_open = True

        # Rule 3: detail-page state tracking. PO `Create A New X` keywords
        # save+redirect; the next step landing on a detail page gives us a
        # window where verify-on-detail keywords are valid.
        if sig and ("saves_form" in sig.side_effects or "redirects" in sig.side_effects
                    or "lands_on_detail" in sig.side_effects):
            on_detail_page = True
            if "saves_form" in sig.side_effects or "redirects" in sig.side_effects:
                last_self_saving_step = (i, sig)
        elif sig and ("opens_modal" in sig.side_effects or "navigates" in sig.side_effects):
            # Opening a new dialog / navigating away exits the detail view.
            on_detail_page = False

        # Rule 4: "Verify Redirection" / "Get Success Toast" right after a
        # self-saving PO keyword is a near-guaranteed timeout. The PO
        # already consumed the toast and redirected.
        if last_self_saving_step is not None and i == last_self_saving_step[0] + 1:
            for rx in _VERIFY_AFTER_SAVE_PATTERNS:
                if rx.search(kw):
                    saving_kw = last_self_saving_step[1].qualified
                    issues.append(
                        SequenceIssue(
                            index=i,
                            code="verify_after_self_saving",
                            message=(
                                f"Step #{i} ({kw}) runs immediately after "
                                f"'{saving_kw}', which already saves+redirects "
                                "and consumes the success toast. This will "
                                "time out waiting for an event that already "
                                "fired."
                            ),
                            fix_hint=(
                                "Drop this step or replace it with "
                                "'GlobalKeywords.Verify Field Value On "
                                "Detail Page' for a specific field."
                            ),
                        )
                    )

        # Rule 5: related-record ops require us to be on a detail page.
        if "related" in kw_lower and not on_detail_page:
            issues.append(
                SequenceIssue(
                    index=i,
                    code="related_without_context",
                    message=(
                        f"Step #{i} ({kw}) operates on a related list, but "
                        "no preceding step has navigated to a record's "
                        "detail page."
                    ),
                    fix_hint=(
                        "Open or navigate to a record (e.g. via "
                        "'Open Record From Table View') before this step."
                    ),
                )
            )

        # Rule 6: detail-page assertions (Verify Field Value On Detail Page)
        # require us to actually be on a detail page.
        if "verify field value on detail page" in kw_lower and not on_detail_page:
            issues.append(
                SequenceIssue(
                    index=i,
                    code="detail_assert_without_context",
                    message=(
                        f"Step #{i} ({kw}) asserts a detail-page field, but "
                        "no prior step has landed on a record detail page."
                    ),
                    fix_hint=(
                        "Run a 'SalesPO.Create A New X' or 'Open Record From "
                        "Table View' step first."
                    ),
                )
            )

        # Rule 7: tab/list view operations before app launcher.
        if not has_app_open and (
            "select app tab" in kw_lower
            or "convert view from intelligent to list" in kw_lower
        ):
            issues.append(
                SequenceIssue(
                    index=i,
                    code="tab_before_app_open",
                    message=(
                        f"Step #{i} ({kw}) navigates a tab before any app "
                        "has been opened. Lightning navigation depends on the "
                        "currently-open app."
                    ),
                    fix_hint="Add 'GlobalKeywords.Launch App' (or a PO opener) before this step.",
                )
            )

        # Rule 8: Login keywords pre-condition. The very first non-meta step
        # should typically be the login keyword.
        if i == 1 and sig and "logs_in" not in sig.side_effects:
            kw_norm = kw_lower
            if kw_norm and "login" not in kw_norm and "sandbox" not in kw_norm:
                issues.append(
                    SequenceIssue(
                        index=i,
                        code="missing_login_first",
                        message=(
                            f"Step #{i} ({kw}) is the first step but doesn't "
                            "log in. Salesforce automation requires "
                            "GlobalKeywords.Login To Sandbox before any UI op."
                        ),
                        fix_hint=(
                            "Insert GlobalKeywords.Login To Sandbox with the "
                            "three credential variables as the first step."
                        ),
                    )
                )

        # Rule 9: chaining two verification keywords back-to-back is
        # almost always the planner being lazy. Allow it but warn.
        if (
            i > 1 and sig and "is_verification" in sig.side_effects
            and sigs[i - 2][0] is not None
            and "is_verification" in sigs[i - 2][0].side_effects
        ):
            issues.append(
                SequenceIssue(
                    index=i,
                    code="back_to_back_verifications",
                    message=(
                        f"Step #{i} ({kw}) follows another verification step. "
                        "If both verifications target the same record, fold them "
                        "into a single 'Verify Field Value On Detail Page' call."
                    ),
                    fix_hint="Consolidate verifications.",
                )
            )

        # Rule 10: open-new-dialog without a preceding tab selection.
        if (
            sig and "opens_modal" in sig.side_effects
            and "open new" in kw_lower
            and not has_app_open
        ):
            issues.append(
                SequenceIssue(
                    index=i,
                    code="dialog_before_app",
                    message=(
                        f"Step #{i} ({kw}) opens a creation modal before any "
                        "app has been launched."
                    ),
                    fix_hint="Add 'GlobalKeywords.Launch App' before this step.",
                )
            )

    return issues


def format_sequence_issues_as_fix_prompt(issues: list[SequenceIssue]) -> str:
    """Render sequence issues into a planner-replan prompt fragment."""
    if not issues:
        return ""
    lines = [
        "The previous plan has out-of-order or contextually-invalid keyword calls. "
        "Apply the fix hints below and resubmit the entire JSON array.",
        "",
    ]
    for issue in issues:
        lines.append(f"- {issue.message}")
        if issue.fix_hint:
            lines.append(f"  Fix: {issue.fix_hint}")
    return "\n".join(lines)
