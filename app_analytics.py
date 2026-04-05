"""
QA Analytics: parse Robot Framework output.xml under Saved_Projects/<project>/Results/
and render Streamlit charts for suite health over time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from app_config import ROOT, _HAS_WORKSPACE, _pm


@dataclass(frozen=True)
class ProjectRunRecord:
    """One historical run folder under a project's Results/."""

    run_folder: str
    run_dt: datetime | None
    passed: int
    failed: int
    skipped: int
    total_tests: int
    elapsed_ms: float
    xml_path: Path
    parse_error: str | None = None


def _parse_run_folder_dt(folder_name: str) -> datetime | None:
    if not folder_name.startswith("run_") or len(folder_name) < 16:
        return None
    ts = folder_name[4:]
    try:
        return datetime.strptime(ts, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _discover_run_dirs(results_dir: Path) -> list[Path]:
    if not results_dir.is_dir():
        return []
    dirs: list[Path] = []
    for d in results_dir.iterdir():
        if d.is_dir() and (d / "output.xml").is_file():
            dirs.append(d)

    def sort_key(p: Path) -> datetime:
        dt = _parse_run_folder_dt(p.name)
        if dt is not None:
            return dt
        return datetime.fromtimestamp(p.stat().st_mtime)

    return sorted(dirs, key=sort_key)


def _parse_output_xml(xml_path: Path) -> tuple[int, int, int, int, float, str | None]:
    """
    Returns (passed, failed, skipped, total_tests, elapsed_ms, error_message).
    On success error_message is None; on failure counts and elapsed are zeroed.
    """
    try:
        from robot.api import ExecutionResult
    except ImportError as exc:
        return 0, 0, 0, 0, 0.0, f"robot.api unavailable: {exc}"

    try:
        result = ExecutionResult(str(xml_path))
    except Exception as exc:  # noqa: BLE001
        return 0, 0, 0, 0, 0.0, str(exc)

    try:
        total = result.statistics.total
        passed = int(total.passed)
        failed = int(total.failed)
        skipped = int(total.skipped)
        n_total = int(total.total)
        elapsed_ms = float(getattr(result.suite, "elapsedtime", 0) or 0)
        return passed, failed, skipped, n_total, elapsed_ms, None
    except Exception as exc:  # noqa: BLE001
        return 0, 0, 0, 0, 0.0, str(exc)


def load_project_run_history(project_root: Path) -> list[ProjectRunRecord]:
    """
    Read all ``output.xml`` files under ``project_root/Results/<run_dirs>/``,
    ordered chronologically (oldest first).

    Uses ``robot.api.ExecutionResult`` to read pass/fail/skip counts and suite
    elapsed time from each file (no manual XML parsing).
    """
    results_dir = project_root / "Results"
    out: list[ProjectRunRecord] = []
    for d in _discover_run_dirs(results_dir):
        xml = d / "output.xml"
        passed, failed, skipped, n_total, elapsed_ms, err = _parse_output_xml(xml)
        out.append(
            ProjectRunRecord(
                run_folder=d.name,
                run_dt=_parse_run_folder_dt(d.name),
                passed=passed,
                failed=failed,
                skipped=skipped,
                total_tests=n_total,
                elapsed_ms=elapsed_ms,
                xml_path=xml,
                parse_error=err,
            )
        )
    return out


def render_project_analytics_dashboard(active_project: str) -> None:
    """Tab 2: QA metrics and charts for the active project's Results history."""
    st.subheader("Project Analytics")
    st.caption(
        "Trends are built from **output.xml** files under the active project's **Results/** folder "
        "(e.g. full project suite runs)."
    )

    if not active_project.strip():
        st.info(
            "Select an **Active Project** in the sidebar to view suite health over time. "
            "After you run **▶️ Run Entire Project Suite** on the other tab, results appear here."
        )
        return

    if not _HAS_WORKSPACE or _pm is None:
        st.warning("Workspace module unavailable; analytics require Saved_Projects.")
        return

    try:
        proj_root = _pm.get_project_path(active_project)
    except FileNotFoundError:
        st.error(f"Project `{active_project}` was not found.")
        return

    records = load_project_run_history(proj_root)
    ok_records = [r for r in records if r.parse_error is None]
    bad = [r for r in records if r.parse_error is not None]

    if not records:
        st.info(
            f"No **output.xml** runs found under `{proj_root.relative_to(ROOT)}/Results/`. "
            "Run **▶️ Run Entire Project Suite** from the **Test Generation & Execution** tab first."
        )
        return

    if bad:
        with st.expander(f"⚠️ {len(bad)} run(s) could not be parsed", expanded=False):
            for r in bad:
                st.caption(f"`{r.run_folder}` — {r.parse_error}")

    if not ok_records:
        st.error("No runs could be parsed successfully; fix or remove corrupt output.xml files.")
        return

    n_default = min(30, len(ok_records))
    n_show = st.slider(
        "Runs to include in charts",
        min_value=1,
        max_value=len(ok_records),
        value=n_default,
        help="Most recent runs are on the right; older runs on the left.",
    )
    chart_slice = ok_records[-n_show:]

    labels = []
    for r in chart_slice:
        if r.run_dt:
            labels.append(r.run_dt.strftime("%m/%d %H:%M"))
        else:
            labels.append(r.run_folder[:16])

    total_runs = len(ok_records)
    last = ok_records[-1]
    denom = last.passed + last.failed + last.skipped
    latest_pass_pct = (100.0 * last.passed / denom) if denom else 0.0
    avg_sec = sum(r.elapsed_ms for r in ok_records) / len(ok_records) / 1000.0

    m1, m2, m3 = st.columns(3)
    m1.metric("Total runs (parsed)", str(total_runs))
    m2.metric("Latest pass rate", f"{latest_pass_pct:.1f}%")
    m3.metric("Avg execution time", f"{avg_sec:.1f}s")

    st.markdown("##### Passed vs failed (per run)")
    df_pf = pd.DataFrame(
        {
            "Passed": [r.passed for r in chart_slice],
            "Failed": [r.failed for r in chart_slice],
        },
        index=labels,
    )
    st.line_chart(df_pf)

    st.markdown("##### Suite duration over time")
    df_t = pd.DataFrame(
        {"Duration (s)": [r.elapsed_ms / 1000.0 for r in chart_slice]},
        index=labels,
    )
    st.line_chart(df_t)

    st.caption(
        f"Showing **{len(chart_slice)}** run(s). Data: `{proj_root.relative_to(ROOT)}/Results/`."
    )
