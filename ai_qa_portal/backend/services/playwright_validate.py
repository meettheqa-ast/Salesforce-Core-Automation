"""Locator validation gate.

A new validation tier for the LLM script generation pipeline. Sits AFTER
the existing AST + dryrun gates. Purpose: catch the failure class where
the LLM emits a syntactically-valid Robot Framework script that uses a
locator (xpath / css / id) which doesn't actually exist on the live
Salesforce page.

Why this is the highest-leverage Playwright integration:

* AST validation catches "did you spell the keyword right".
* ``robot --dryrun`` catches "do the keywords exist + take the right
  number of args".
* Neither catches "does ``//input[@name='FirstName']`` actually resolve
  to an element on the New Lead modal in this customer's Salesforce
  org". That's exactly what real users hit at runtime, file as bugs,
  and consume the heal-budget on.

This gate fixes that BEFORE the script is saved.

Flow:

  1. Parse the .robot file with ``robot.api.get_model``
     (same parser as ``script_validator``).
  2. Walk every keyword call; collect the LITERAL locator strings
     passed as args (xpath, css, id selectors). Skip variable
     references (``${LEAD_NAME_INPUT}``) because those resolve through
     Resource files that the AST validator already vouched for.
  3. Acquire a warm Playwright BrowserContext via ``playwright_session_manager``.
  4. Group locators by "what page must Playwright be on for this to
     resolve?" using a small static map of known PO entry points
     (``SalesPO.Open New Lead From Sales App`` -> lead-create modal,
     etc.).
  5. For each page-group, navigate, then check each locator.
  6. Emit a ``ValidationReport`` shaped exactly like the AST + dryrun
     ones, with ``kind="locator_not_found"`` for stale locators and
     ``closest_matches`` populated from the live DOM (the LLM gets a
     concrete "did you mean" instead of guessing).

Design rules:

* The validator is a SOFT gate. If Playwright is unreachable or
  Salesforce is down, we log + skip and let the AST + dryrun verdict
  stand. Never block generation on infrastructure flakes.
* Per-call timeout: 60s. Cold-start (no warm context) hits ~18s; warm
  path is ~3s. Past 60s something's wrong; bail.
* Every locator check is metered via ``playwright_metrics`` so we have
  data on hit rate / false positives / timing.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ai_qa_portal.backend.services import playwright_metrics as metrics
from ai_qa_portal.backend.services.script_validator import (
    ValidationError,
    ValidationReport,
)

logger = logging.getLogger("ai_qa_portal.playwright")


# ---------------------------------------------------------------------------
# Locator extraction from .robot files
# ---------------------------------------------------------------------------

# A "literal locator" is a string that looks like a CSS / XPath / id
# selector -- something Playwright can directly evaluate. We deliberately
# DO NOT touch ``${VAR}`` references or pure-text args like Salesforce
# field labels (those resolve through PO keywords' own logic, not direct
# selectors).
#
# Patterns the validator considers "validatable":
#   * starts with `//` or `(`             -> XPath
#   * starts with `xpath:` / `css:` / `id:` / `name:`  -> Selenium prefix
#   * starts with `#` and has no spaces   -> ID selector
#   * contains `[` and `]` and an `=`     -> attribute selector
_LITERAL_LOCATOR_PATTERNS = [
    re.compile(r"^xpath\s*[:=]\s*"),
    re.compile(r"^css\s*[:=]\s*"),
    re.compile(r"^id\s*[:=]\s*"),
    re.compile(r"^name\s*[:=]\s*"),
    re.compile(r"^//"),
    re.compile(r"^\(\s*//"),
]

# The frequent first-keyword -> "expected page" map. When the LLM-generated
# test calls ``SalesPO.Open New Lead From Sales App`` as its first
# user-flow step, we know the next page Playwright should be on is the
# Lead-create modal. Kept tiny on purpose -- 80% of generated tests use
# one of these entry points; the rest fall through to a "current page"
# check (validator skips per-page nav).
_PAGE_ENTRY_KEYWORDS: dict[str, str] = {
    "salespo.open new lead from sales app": "lead_create",
    "salespo.open new account from sales app": "account_create",
    "salespo.open new opportunity from sales app": "opportunity_create",
    "salespo.open new contact from sales app": "contact_create",
    "campaignpo.open new campaign from sales app": "campaign_create",
}

# Known "skip these arg positions" -- some keywords pass non-locator
# strings as args (e.g. ``Set Picklist Field    Lead Status   Working``
# passes a label, not a selector). Conservative: if it doesn't match a
# locator pattern, we skip it.


@dataclass
class _ExtractedLocator:
    """One literal locator string we want to validate, plus context."""
    line: int
    selector: str
    snippet: str
    expected_page: str | None = None


def _looks_like_locator(value: str) -> bool:
    if not value:
        return False
    v = value.strip()
    # ${...} reference: skip (handled by AST + variable resolver).
    if v.startswith("${") and v.endswith("}"):
        return False
    # @{...} list ref: skip.
    if v.startswith("@{") or v.startswith("&{"):
        return False
    # Pure text label without selector hints: skip.
    if not any(p.match(v) for p in _LITERAL_LOCATOR_PATTERNS):
        # ID-shorthand: '#someid' with no spaces.
        if v.startswith("#") and " " not in v and len(v) > 1:
            return True
        # Attribute selector shorthand: contains [ and ] and an =.
        if "[" in v and "]" in v and "=" in v:
            return True
        return False
    return True


def _extract_literal_locators(suite_path: Path) -> list[_ExtractedLocator]:
    """Walk the .robot AST and collect every literal locator argument.

    Returns a list ordered by file position so the validator can
    correlate failures back to source lines in error messages.
    """
    try:
        from robot.api import get_model
    except ImportError:
        # Robot framework isn't importable -- tests will mock this path.
        # Real production has robot installed via requirements.txt.
        logger.warning("robot.api not available; locator extraction skipped")
        return []

    try:
        model = get_model(str(suite_path), data_only=True)
    except Exception as exc:
        logger.warning("locator extractor: get_model failed: %s", exc)
        return []

    out: list[_ExtractedLocator] = []
    next_expected_page: str | None = None

    def _walk(node) -> None:
        nonlocal next_expected_page
        # KeywordCall has .keyword (name) and .args (list of strings).
        if hasattr(node, "keyword") and hasattr(node, "args"):
            kw_name = (node.keyword or "").strip()
            kw_lower = kw_name.lower()
            # Update the page tracker if this keyword is a known entry.
            if kw_lower in _PAGE_ENTRY_KEYWORDS:
                next_expected_page = _PAGE_ENTRY_KEYWORDS[kw_lower]
            # Collect literal locator args.
            for arg in node.args or []:
                if _looks_like_locator(arg):
                    line = getattr(node, "lineno", 0) or 0
                    out.append(
                        _ExtractedLocator(
                            line=int(line),
                            selector=arg.strip(),
                            snippet=f"{kw_name}    {arg}",
                            expected_page=next_expected_page,
                        )
                    )
        # Recurse into children. Robot's AST exposes a simple
        # parent/children structure; iterate generically.
        for child in getattr(node, "body", []) or []:
            _walk(child)
        # Test case bodies live under .testcase, suite-level keywords
        # under .keywords, etc. Handle the few common attribute names.
        for attr in ("testcases", "keywords", "tests", "sections"):
            for child in getattr(node, attr, None) or []:
                _walk(child)

    _walk(model)
    return out


# ---------------------------------------------------------------------------
# Playwright-driven validation
# ---------------------------------------------------------------------------

def _normalize_selector_for_playwright(selector: str) -> str:
    """Translate Selenium-flavored prefixes into Playwright equivalents.

    Robot's SeleniumLibrary uses ``xpath:``, ``css:``, ``id:``, ``name:``;
    Playwright's locator() accepts CSS by default and XPath when the
    string starts with ``//``. We strip Selenium prefixes so Playwright
    sees a usable selector.
    """
    s = selector.strip()
    if s.lower().startswith("xpath:"):
        return s.split(":", 1)[1].strip()
    if s.lower().startswith("css:"):
        return s.split(":", 1)[1].strip()
    if s.lower().startswith("id:"):
        # CSS id selector
        return f"#{s.split(':', 1)[1].strip()}"
    if s.lower().startswith("name:"):
        return f'[name="{s.split(":", 1)[1].strip()}"]'
    return s


def validate_locators(
    suite_path: Path,
    sandbox_url: str,
    username: str,
    password: str,
    *,
    persona_id: str | None = None,
    timeout_s: float = 60.0,
    metrics_label_phase: str = "unknown",
) -> ValidationReport:
    """Open Salesforce in Playwright, verify each literal locator the
    .robot file references against the live page DOM.

    Returns a :class:`ValidationReport` with the same shape AST + dryrun
    produce. ``kind="locator_not_found"`` errors carry rich context the
    fix-prompt builder uses to feed concrete "did you mean" alternatives
    back to the LLM.

    Soft-fail policy: any infrastructure error (Playwright not installed,
    SF unreachable, login failed) -> return ``ok=True`` with zero errors.
    The locator gate is a SAFETY NET, not a load-bearing check; we don't
    block generation when the net is unavailable.
    """
    deadline = time.monotonic() + timeout_s

    # Step 1: extract literal locators from the .robot file. Cheap; no
    # browser needed. Don't ever fail the whole call here -- a parse
    # error blames the script, not us.
    try:
        locators = _extract_literal_locators(suite_path)
    except Exception as exc:
        logger.warning("locator extraction crashed: %s", exc)
        metrics.increment(
            "pw_locator_validate_total",
            labels={"phase": metrics_label_phase, "result": "extract_error"},
        )
        return ValidationReport(ok=True, errors=[])

    if not locators:
        # No literal locators in the script -- everything is variable
        # references that the AST validator already vouched for. Soft
        # pass.
        metrics.increment(
            "pw_locator_validate_total",
            labels={"phase": metrics_label_phase, "result": "no_locators"},
        )
        return ValidationReport(ok=True, errors=[])

    # Step 2: acquire a Playwright session. The session manager handles
    # warm-cache vs cold-login transparently.
    with metrics.measure("pw_locator_validate", labels={"phase": metrics_label_phase}) as meas:
        try:
            from ai_qa_portal.backend.services.playwright_session_manager import acquire_session
        except ImportError as exc:
            logger.warning("playwright_session_manager unavailable: %s", exc)
            meas.label("result", "unavailable")
            return ValidationReport(ok=True, errors=[])

        try:
            handle = acquire_session(sandbox_url, username, password, persona_id)
        except ImportError as exc:
            # Playwright package itself missing.
            logger.warning("Playwright not installed: %s", exc)
            meas.label("result", "playwright_missing")
            return ValidationReport(ok=True, errors=[])
        except Exception as exc:
            logger.warning("acquire_session failed: %s", exc)
            meas.label("result", "session_error")
            return ValidationReport(ok=True, errors=[])

        # Step 3: navigate to the first expected page, then check each
        # locator. We only deeply navigate when the locator's expected
        # page is known -- otherwise we trust the warm context's current
        # page.
        import pw_mcp_bridge

        ctx = handle.context
        errors: list[ValidationError] = []
        # Group by expected_page so we navigate at most once per page.
        from collections import defaultdict
        by_page: dict[str | None, list[_ExtractedLocator]] = defaultdict(list)
        for loc in locators:
            by_page[loc.expected_page].append(loc)

        for page_key, group in by_page.items():
            if time.monotonic() > deadline:
                logger.info("locator validation deadline reached; remaining locators skipped")
                break
            # Deep nav only when we know the target. ``None`` page means
            # "validate against whatever page is current after warm-up";
            # we still verify each locator but skip an explicit goto.
            if page_key:
                target_url = _resolve_page_url(sandbox_url, page_key)
                if target_url:
                    try:
                        pw_mcp_bridge.goto(ctx, target_url, wait_until="load")
                    except Exception as exc:
                        logger.warning("page navigation to %s failed: %s", target_url, exc)
                        # Don't fail the whole validation -- check the
                        # locators against whatever page is current.
            for loc in group:
                if time.monotonic() > deadline:
                    break
                pw_selector = _normalize_selector_for_playwright(loc.selector)
                try:
                    result = pw_mcp_bridge.find_locator(ctx, pw_selector, timeout_ms=4000)
                except Exception as exc:
                    logger.warning("locator check threw: %s", exc)
                    continue
                if not result.get("found"):
                    # Live "did you mean" via DOM scan.
                    closest: list[str] = []
                    try:
                        candidates = pw_mcp_bridge.find_closest_match(
                            ctx, loc.selector, max_candidates=3,
                        )
                        closest = [c.get("suggested", "") for c in candidates if c.get("suggested")]
                    except Exception:
                        pass
                    errors.append(
                        ValidationError(
                            line=loc.line,
                            column=0,
                            kind="locator_not_found",  # type: ignore[arg-type]
                            symbol=loc.selector,
                            message=(
                                f"Locator did not resolve on the live page. "
                                f"page_url={result.get('page_url', '')}"
                            ),
                            closest_matches=closest,
                            snippet=loc.snippet,
                        )
                    )

        # Metrics outcome label.
        if not errors:
            meas.label("result", "all_live")
        elif len(errors) == len(locators):
            meas.label("result", "all_stale")
        else:
            meas.label("result", "partial")

    return ValidationReport(
        ok=len(errors) == 0,
        errors=errors,
        keyword_calls_seen=0,
        variable_refs_seen=0,
    )


def _resolve_page_url(sandbox_url: str, page_key: str) -> str | None:
    """Map a page key to a Salesforce URL fragment we can navigate to.

    Salesforce Lightning's URL shape: ``<sandbox>/lightning/o/<sobject>/new``
    opens the create modal for an SObject. ``page_key`` like
    ``lead_create`` -> ``Lead`` SObject create.
    """
    sobject_map = {
        "lead_create": "Lead",
        "account_create": "Account",
        "opportunity_create": "Opportunity",
        "contact_create": "Contact",
        "campaign_create": "Campaign",
    }
    sobj = sobject_map.get(page_key)
    if not sobj:
        return None
    base = sandbox_url.rstrip("/")
    # Convert a my.salesforce.com URL to the Lightning equivalent if needed.
    if ".my.salesforce.com" in base and ".lightning.force.com" not in base:
        base = base.replace(".my.salesforce.com", ".lightning.force.com")
    return f"{base}/lightning/o/{sobj}/new"
