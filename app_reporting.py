"""
In-app Robot run summaries for PM-friendly review (parses output.xml; surfaces screenshots).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

from app_config import ROOT


@dataclass
class TestCaseSummary:
    """One executed test case from output.xml."""

    name: str
    longname: str
    status: str
    elapsed_ms: int
    message: str
    screenshot_paths: list[Path] = field(default_factory=list)
    ai_analysis: str = ""


def _walk_tests(suite):
    """Yield TestCase objects from a Robot result suite (nested suites)."""
    for t in suite.tests:
        yield t
    for child in suite.suites:
        yield from _walk_tests(child)


def clean_failure_message(msg: str) -> str:
    """Make Selenium/Robot failure text readable (shorten long XPath / locators)."""
    if not msg or not msg.strip():
        return "(no details)"
    s = msg.strip()
    # Selenium-style: long locator inside quotes before "not visible"
    if "not visible" in s.lower() and "Element" in s:
        tail = re.search(r"not visible\s+(.+)$", s, re.IGNORECASE | re.DOTALL)
        hint = (tail.group(1).strip().rstrip(".") if tail else "") or "see screenshot"
        return f"The expected element was not found or not visible in time ({hint})."
    if len(s) <= 220:
        s = re.sub(
            r"xpath://[^\s)]+(?:\([^)]*\))*[^\s)]*",
            "[XPath]",
            s,
            flags=re.IGNORECASE,
        )
        return s
    tail = re.search(
        r"(AssertionError:[^.]*\.?|TimeoutException[^.]*\.?|Error:[^.]{0,200}\.?)\s*$",
        s,
        re.IGNORECASE | re.DOTALL,
    )
    if tail:
        return "Failure: " + tail.group(0).strip()
    return s[:180] + "\n…\n" + s[-160:]


def parse_test_cases_from_output_xml(output_xml: Path) -> list[TestCaseSummary]:
    """Load output.xml via Robot's ExecutionResult and collect per-test results."""
    try:
        from robot.api import ExecutionResult
    except ImportError:
        return []

    if not output_xml.is_file():
        return []

    try:
        result = ExecutionResult(str(output_xml))
    except Exception:  # noqa: BLE001
        return []

    _analyze = None
    try:
        from ai_bridge import analyze_test_failure as _analyze  # type: ignore[assignment]
    except ImportError:
        pass

    rows: list[TestCaseSummary] = []
    for test in _walk_tests(result.suite):
        status = str(getattr(test, "status", "") or "")
        msg = str(getattr(test, "message", "") or "")
        elapsed = int(getattr(test, "elapsedtime", 0) or 0)
        name = str(getattr(test, "name", "") or "Test")
        longname = str(getattr(test, "longname", "") or name)

        ai_rca = ""
        if status.upper() == "FAIL" and msg.strip() and _analyze is not None:
            ai_rca = _analyze(name, msg)

        rows.append(
            TestCaseSummary(
                name=name,
                longname=longname,
                status=status.upper(),
                elapsed_ms=elapsed,
                message=msg,
                ai_analysis=ai_rca,
            )
        )
    return rows


_SCREENSHOT_NAME = re.compile(
    r"(selenium-screenshot-\d+\.png)",
    re.IGNORECASE,
)


def _collect_pngs(out_dir: Path) -> list[Path]:
    """All PNG artifacts under the run folder (flat + pabot subfolders)."""
    if not out_dir.is_dir():
        return []
    found: list[Path] = []
    for p in out_dir.rglob("*.png"):
        if p.is_file():
            found.append(p)
    # Prefer selenium screenshots first, then name order
    found.sort(key=lambda x: (0 if "selenium-screenshot" in x.name.lower() else 1, x.name))
    return found


def _assign_screenshots(
    failed: list[TestCaseSummary],
    pngs: list[Path],
    out_dir: Path,
) -> None:
    """Attach screenshot paths to failed tests (from message filenames, else order)."""
    remaining = list(pngs)
    for tc in failed:
        paths: list[Path] = []
        for m in _SCREENSHOT_NAME.finditer(tc.message or ""):
            name = m.group(1)
            for p in out_dir.rglob(name):
                if p.is_file() and p not in paths:
                    paths.append(p)
                    try:
                        remaining.remove(p)
                    except ValueError:
                        pass
        if not paths and remaining:
            paths.append(remaining.pop(0))
        tc.screenshot_paths = paths


def render_in_app_run_summary(out_dir: Path, *, key_prefix: str = "summary") -> None:
    """
    Parse ``out_dir / output.xml`` and show one expander per test (pass/fail, time, message, images).
    """
    out_dir = out_dir.resolve()
    try:
        out_dir.relative_to(ROOT.resolve())
    except ValueError:
        st.warning("Run output folder is outside the project; summary skipped.")
        return

    xml_path = out_dir / "output.xml"
    tests = parse_test_cases_from_output_xml(xml_path)
    if not tests:
        if xml_path.is_file():
            st.info("No test cases were found in **output.xml** for this run.")
        else:
            st.info("**output.xml** was not found for this run — detailed summary unavailable.")
        return

    failed = [t for t in tests if t.status == "FAIL"]
    pngs = _collect_pngs(out_dir)
    if failed and pngs:
        _assign_screenshots(failed, pngs, out_dir)

    passed_n = sum(1 for t in tests if t.status == "PASS")
    failed_n = sum(1 for t in tests if t.status == "FAIL")
    skipped_n = sum(1 for t in tests if t.status == "SKIP")

    st.subheader("Run summary")
    m1, m2, m3 = st.columns(3)
    m1.metric("Passed", str(passed_n))
    m2.metric("Failed", str(failed_n))
    m3.metric("Skipped", str(skipped_n))
    st.caption(
        "Results are read from **output.xml** in this run folder. Open the HTML report below only if you need full Robot logs."
    )

    for tc in tests:
        ok = tc.status == "PASS"
        sk = tc.status == "SKIP"
        label = f"[{'✅ PASS' if ok else '⏭️ SKIP' if sk else '❌ FAIL'}] {tc.longname}"
        sec = tc.elapsed_ms / 1000.0

        with st.expander(label, expanded=(not ok and not sk)):
            st.markdown(f"**Status:** `{tc.status}`")
            st.markdown(f"**Duration:** {sec:.2f}s")
            if ok or sk:
                if sk and tc.message.strip():
                    st.markdown(clean_failure_message(tc.message))
            else:
                st.markdown("**What went wrong**")
                st.info(clean_failure_message(tc.message))
                if tc.ai_analysis and tc.ai_analysis != "AI Analysis unavailable.":
                    st.info(f"🤖 **AI Root Cause Analysis:**\n{tc.ai_analysis}")
                for j, img_path in enumerate(tc.screenshot_paths):
                    if img_path.is_file():
                        st.caption(f"Screenshot: `{img_path.name}`")
                        st.image(str(img_path), use_container_width=True)
                if not tc.screenshot_paths:
                    st.caption(
                        "No **.png** screenshots were matched for this failure in the run folder."
                    )


_TICKET_TAG = re.compile(r"^(US-|TC-|SFDC-|QA-)", re.IGNORECASE)


def publish_results_to_zephyr(
    output_xml_path: str | Path,
    jira_url: str,
    token: str,
    project_key: str,
    environment: str = "Dev",
) -> int:
    """Push per-test Pass/Fail verdicts to Zephyr Scale (or Jira comment fallback).

    Matches tests whose Robot tags start with ``US-``, ``TC-``, ``SFDC-``, or
    ``QA-`` and POSTs a result payload for each.  Returns the number of tests
    successfully synced.  Never raises — logs warnings on failure.
    """
    import logging
    import requests

    logger = logging.getLogger(__name__)
    output_xml_path = Path(output_xml_path)

    try:
        from robot.api import ExecutionResult
    except ImportError:
        logger.warning("robot.api not available — cannot publish to Zephyr.")
        return 0

    if not output_xml_path.is_file():
        return 0

    try:
        result = ExecutionResult(str(output_xml_path))
    except Exception:
        logger.warning("Could not parse %s for Zephyr sync.", output_xml_path, exc_info=True)
        return 0

    base = jira_url.rstrip("/")
    endpoint = f"{base}/rest/atm/1.0/testresult"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    _rca_fn = None
    try:
        from ai_bridge import analyze_test_failure as _rca_fn  # type: ignore[assignment]
    except ImportError:
        pass

    synced = 0
    for test in _walk_tests(result.suite):
        tags = [str(t) for t in getattr(test, "tags", [])]
        target_tags = [t for t in tags if _TICKET_TAG.match(t)]
        if not target_tags:
            continue
        status = "PASS" if str(getattr(test, "status", "")).upper() == "PASS" else "FAIL"
        msg = str(getattr(test, "message", "") or "")

        comment = f"Automated result from Test Intelligence Platform — {test.name}"
        if status == "FAIL" and msg.strip() and _rca_fn is not None:
            rca = _rca_fn(str(test.name), msg)
            if rca and rca != "AI Analysis unavailable.":
                comment += f"\n\nAI Root Cause Analysis:\n{rca}"

        for tag in target_tags:
            payload = {
                "projectKey": project_key,
                "testCaseKey": tag,
                "status": "Pass" if status == "PASS" else "Fail",
                "environment": environment,
                "comment": comment,
            }
            try:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=15)
                if resp.ok:
                    synced += 1
                else:
                    logger.warning(
                        "Zephyr POST for %s returned %s: %s",
                        tag, resp.status_code, resp.text[:200],
                    )
            except Exception:
                logger.warning("Zephyr POST failed for %s.", tag, exc_info=True)
    return synced


def render_run_summary_for_last_run(key_prefix: str = "persisted_summary") -> None:
    """Re-show summary for ``st.session_state['last_run']['out_dir']`` after navigation/rerun."""
    lr = st.session_state.get("last_run") or {}
    rel = lr.get("out_dir")
    if not rel:
        return
    out_dir = ROOT / rel
    if not (out_dir / "output.xml").is_file():
        return
    render_in_app_run_summary(out_dir, key_prefix=key_prefix)
