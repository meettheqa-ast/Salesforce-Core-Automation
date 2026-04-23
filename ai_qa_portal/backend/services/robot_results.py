"""Shared helpers for parsing Robot Framework output.xml.

Used by both the runs router (per-run summary, history) and the analytics
router (per-project aggregates). Centralised here so we have one parser to
maintain and one place to handle Robot 6 vs Robot 7 schema differences.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

_ROBOT_TS_FORMATS = (
    "%Y%m%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y%m%d %H:%M:%S",
)


def parse_robot_ts(value: str | None) -> datetime | None:
    """Parse Robot's various timestamp formats. Returns None on failure."""
    if not value:
        return None
    for fmt in _ROBOT_TS_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _status_times(st) -> tuple[datetime | None, datetime | None, float]:
    """Extract (start_dt, end_dt, elapsed_s) from a Robot <status .../> element.

    Supports both schemas:
      - Robot <= 6: ``starttime="20260422 22:20:12.016"`` + ``endtime=...``
      - Robot 7+:    ``start="2026-04-22T22:20:12.016"`` + ``elapsed="63.07"``
    """
    if st is None:
        return None, None, 0.0
    s = parse_robot_ts(st.attrib.get("starttime") or st.attrib.get("start"))
    e = parse_robot_ts(st.attrib.get("endtime") or st.attrib.get("end"))
    elapsed = 0.0
    raw_elapsed = st.attrib.get("elapsed")
    if raw_elapsed:
        try:
            elapsed = float(raw_elapsed)
        except (TypeError, ValueError):
            elapsed = 0.0
    if elapsed <= 0 and s and e:
        elapsed = (e - s).total_seconds()
    if e is None and s is not None and elapsed > 0:
        e = s + timedelta(seconds=elapsed)
    return s, e, elapsed


def _walk_test_statuses(suite_el):
    """Yield ('PASS'|'FAIL'|'SKIP', start_dt|None, end_dt|None, elapsed_s) for every <test>."""
    for test in suite_el.findall("test"):
        st = test.find("status")
        status = (st.attrib.get("status") if st is not None else "FAIL") or "FAIL"
        s, e, elapsed = _status_times(st)
        yield status.upper(), s, e, elapsed
    for child in suite_el.findall("suite"):
        yield from _walk_test_statuses(child)


def parse_output_xml(output_xml: Path) -> tuple[int, int, int, float]:
    """Return ``(passed, failed, skipped, duration_s)`` for a Robot output.xml.

    Strategy:
      1. Try the legacy ``<statistics>/<total>/<stat>`` block first.
      2. If that block is missing or zero, walk every ``<test><status>`` node
         (Robot 7+ sometimes omits the statistics block when run with certain
         options; this fallback keeps counts honest).
      3. Duration is summed from top-level ``<suite><status starttime endtime/>``
         deltas, falling back to per-test deltas when suite times are absent.
    """
    if not output_xml.is_file():
        return 0, 0, 0, 0.0

    try:
        root = ET.parse(output_xml).getroot()
    except (ET.ParseError, OSError):
        return 0, 0, 0, 0.0

    passed = failed = skipped = 0

    stat = root.find("statistics/total/stat") or root.find("statistics/total")
    if stat is not None:
        try:
            passed = int(stat.attrib.get("pass", 0))
            failed = int(stat.attrib.get("fail", 0))
            skipped = int(stat.attrib.get("skip", 0))
        except (TypeError, ValueError):
            passed = failed = skipped = 0

    duration_s = 0.0
    test_duration_s = 0.0

    if passed + failed + skipped == 0:
        # Fallback: walk every <test> and tally counts + duration.
        for top in root.findall("suite"):
            for status, _s, _e, elapsed in _walk_test_statuses(top):
                if status == "PASS":
                    passed += 1
                elif status == "FAIL":
                    failed += 1
                elif status == "SKIP":
                    skipped += 1
                test_duration_s += elapsed
    else:
        # Even when stats are present, gather per-test times for the fallback duration.
        for top in root.findall("suite"):
            for _, _s, _e, elapsed in _walk_test_statuses(top):
                test_duration_s += elapsed

    # Prefer the suite-level duration when available.
    for top in root.findall("suite"):
        _s, _e, elapsed = _status_times(top.find("status"))
        if elapsed > 0:
            duration_s += elapsed

    if duration_s <= 0:
        duration_s = test_duration_s

    return passed, failed, skipped, round(duration_s, 2)


def parse_run_times(output_xml: Path) -> tuple[datetime | None, datetime | None]:
    """Return (started_at, finished_at) walking suite-level statuses."""
    if not output_xml.is_file():
        return None, None
    try:
        root = ET.parse(output_xml).getroot()
    except (ET.ParseError, OSError):
        return None, None

    started: datetime | None = None
    finished: datetime | None = None
    generated = root.attrib.get("generated") or root.attrib.get("generated_time")
    finished = parse_robot_ts(generated)

    for top in root.findall("suite"):
        s, e, _ = _status_times(top.find("status"))
        if s and (started is None or s < started):
            started = s
        if e and (finished is None or e > finished):
            finished = e
    return started, finished
