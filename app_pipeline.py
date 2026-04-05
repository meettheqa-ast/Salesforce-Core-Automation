"""AI generation, Robot execution, results UI, and PM clarification helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

from app_catalog import rebuild_keyword_catalog
from app_config import (
    GENERATED_SUITE,
    PENDING_GEN_CTX_KEY,
    PENDING_ROBOT_EDITOR_KEY,
    ROOT,
    _HAS_WORKSPACE,
    _pm,
)
from app_reporting import render_in_app_run_summary, render_run_summary_for_last_run


def open_local_path(path: Path) -> None:
    """Open a file with the OS default app (e.g. browser for HTML). file:// links from localhost often do nothing."""
    path = path.resolve()
    if not path.is_file():
        return
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def render_report_log_actions(
    report_path: Path | None,
    log_path: Path | None,
    out_dir_rel: str,
    *,
    key_prefix: str,
    passed: bool | None = None,
) -> None:
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if report_path and report_path.is_file():
            if st.button(
                "Open Report",
                key=f"{key_prefix}_report",
                help="Opens report.html in your default browser.",
            ):
                open_local_path(report_path)
        else:
            st.caption("report.html not found.")
    with col_b:
        if log_path and log_path.is_file():
            if st.button(
                "Open Log",
                key=f"{key_prefix}_log",
                help="Opens log.html in your default browser.",
            ):
                open_local_path(log_path)
        else:
            st.caption("log.html not found.")
    with col_c:
        st.caption(f"Output folder: `{out_dir_rel}`")
        if passed is True:
            st.success("Last run: Passed")
        elif passed is False:
            st.error("Last run: Failed")


def field_input_label(field_name: str) -> str:
    """Human-readable label for missing-field form inputs."""
    if field_name == "Last Name":
        return "Lead Last Name"
    if field_name == "Company":
        return "Company"
    if field_name in ("Lead Status", "Salutation", "Lead Source"):
        return f"{field_name} (optional — blank = random dropdown option)"
    return field_name


def build_augmented_prompt(
    original_prompt: str,
    missing_fields: list[str],
    field_values: dict[str, str],
    optional_picklist_fields: list[str],
    csv_llm_block: str | None = None,
) -> str:
    """Append PM clarifications in natural language for the LLM; optionally append CSV data-driven block."""
    try:
        from ai_bridge import append_csv_data_to_prompt
    except ImportError:

        def append_csv_data_to_prompt(p: str, c: str) -> str:  # type: ignore[misc]
            return p.rstrip() + (f"\n\n{c}" if (c or "").strip() else "")

    blocks: list[str] = []
    clauses: list[str] = []
    for field in missing_fields:
        value = str(field_values.get(field, "")).strip()
        if field == "Last Name":
            clauses.append(f"the Last Name is {value}")
        elif field == "Company":
            clauses.append(f"the Company is {value}")
        else:
            clauses.append(f"the {field} is {value}")
    if clauses:
        blocks.append("The user has clarified that " + " and ".join(clauses) + ".")

    pick_parts: list[str] = []
    for field in optional_picklist_fields:
        raw = str(field_values.get(field, "")).strip()
        if raw:
            pick_parts.append(
                f"{field} must be {raw!r} — use Open Dropdown then Select Dropdown Option with that exact visible label"
            )
        else:
            pick_parts.append(
                f"{field}: use GlobalKeywords.Open Dropdown And Select First Option with field label {field!r} (picks a random visible option)"
            )
    if pick_parts:
        blocks.append("Picklist handling: " + " ".join(pick_parts) + ".")

    if not blocks:
        out = original_prompt.rstrip()
    else:
        out = original_prompt.rstrip() + "\n\n" + " ".join(blocks)
    if csv_llm_block and str(csv_llm_block).strip():
        out = append_csv_data_to_prompt(out, str(csv_llm_block).strip())
    return out


def clear_pending_generation() -> None:
    """Remove human-in-the-loop draft script from session state."""
    st.session_state.pop(PENDING_GEN_CTX_KEY, None)
    st.session_state.pop(PENDING_ROBOT_EDITOR_KEY, None)


def run_temp_generated_suite(
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
    *,
    key_prefix: str = "inline_run",
) -> int:
    """Execute ``Tests/Generated/temp_test.robot``; write ``last_run`` and return exit code."""
    if not headless:
        st.warning(
            "A browser window will open shortly. Please do not close it manually."
        )
    run_name = f"ui_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        from run_test import build_robot_run

        cmd, out_dir = build_robot_run(
            sandbox_url.strip(),
            username.strip(),
            password.strip(),
            "Tests/Generated/temp_test.robot",
            run_name=run_name,
            headless=headless,
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not prepare Robot run: {exc}")
        return -1

    st.subheader("Live Execution Log")
    st.caption("Streaming output from Robot Framework.")
    code, full_log = stream_robot_logs(cmd, ROOT)

    if code == 0:
        st.success("Test Passed!")
    else:
        st.error("Test Failed.")

    report_html = out_dir / "report.html"
    log_html = out_dir / "log.html"
    report_path = report_html if report_html.is_file() else None
    log_path = log_html if log_html.is_file() else None

    st.session_state.last_run = {
        "out_dir": str(out_dir.relative_to(ROOT)),
        "report_path": str(report_html.resolve()) if report_path else None,
        "log_path": str(log_html.resolve()) if log_path else None,
        "passed": code == 0,
    }

    render_in_app_run_summary(out_dir, key_prefix=f"{key_prefix}_sum")
    st.session_state["last_run_summary_rendered_for"] = str(out_dir.resolve())

    render_report_log_actions(
        report_path,
        log_path,
        str(out_dir.relative_to(ROOT)),
        key_prefix=key_prefix,
        passed=None,
    )

    with st.expander("Full log (copy)"):
        st.code(full_log or "(empty)", language="text")
    return code


def execute_pending_generated_run(
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
) -> None:
    """Persist edited script (project + temp), run temp suite, clear pending on success."""
    ctx = st.session_state.get(PENDING_GEN_CTX_KEY)
    if not ctx:
        return
    code_text = st.session_state.get(PENDING_ROBOT_EDITOR_KEY, "")
    if not str(code_text).strip():
        st.error("Generated script is empty.")
        return

    GENERATED_SUITE.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_SUITE.write_text(str(code_text), encoding="utf-8")

    project_name = ctx.get("project_name")
    test_name = ctx.get("test_name")
    overwrite = ctx.get("overwrite", True)
    csv_bytes = ctx.get("csv_bytes")

    if project_name and test_name and _HAS_WORKSPACE and _pm is not None:
        try:
            proj_robot, _ = _pm.save_test_to_project(
                project_name,
                test_name,
                str(code_text),
                csv_bytes,
                overwrite=overwrite,
            )
            st.success(f"💾 Saved to **{project_name}** › `{proj_robot.name}`")
        except FileExistsError:
            st.error(
                "That test already exists in the project. Enable **overwrite** when you click "
                "**Run Automation**, then regenerate before Save & Execute."
            )
            return
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Could not save to project: {exc}")

    exit_code = run_temp_generated_suite(
        sandbox_url, username, password, headless, key_prefix="pending_inline_run"
    )
    if exit_code == 0:
        clear_pending_generation()


def render_pending_robot_review_panel(
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
) -> None:
    """Show editor + Save & Execute / Discard when AI generation produced a pending script."""
    if not st.session_state.get(PENDING_GEN_CTX_KEY):
        return

    st.divider()
    st.subheader("Review generated Robot")
    st.caption(
        "Edit the script if needed. **Save & Execute** writes to the project (or `temp_test.robot` "
        "for ad-hoc) and runs Robot. **Discard** clears this draft without running."
    )
    st.text_area(
        "Generated `.robot`",
        height=500,
        key=PENDING_ROBOT_EDITOR_KEY,
        help="Robot Framework syntax. Fix locators or variables before running.",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Save & Execute", type="primary", key="pending_save_execute_btn"):
            if not sandbox_url.strip() or not username.strip() or not password.strip():
                st.error("Please fill in Sandbox URL, Username, and Password in the sidebar.")
                return
            execute_pending_generated_run(sandbox_url, username, password, headless)
    with c2:
        if st.button("❌ Discard", key="pending_discard_btn"):
            clear_pending_generation()
            st.rerun()


def stream_robot_logs(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """Run robot or pabot, return (exit_code, full_log_text)."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    lines: list[str] = []
    log_box = st.empty()
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        log_box.code("".join(lines), language="text")
    proc.wait()
    return proc.returncode or 0, "".join(lines)


def run_automation_pipeline(
    final_prompt: str,
    *,
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
    csv_bytes: bytes | None = None,
    project_name: str | None = None,
    test_name: str | None = None,
    overwrite: bool = False,
) -> None:
    """Refresh catalog, generate .robot via AI, store draft in session for human review (no run yet)."""
    try:
        with st.spinner("Refreshing keyword catalog from Page Objects…"):
            rebuild_keyword_catalog()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not refresh keyword catalog: {exc}")
        return

    try:
        from ai_bridge import generate_test_from_prompt
    except ImportError as exc:
        st.error(f"Could not import ai_bridge: {exc}")
        return

    try:
        with st.spinner("AI is architecting your test case…"):
            out_path = generate_test_from_prompt(
                final_prompt.strip(),
                csv_bytes=csv_bytes,
            )
        if not GENERATED_SUITE.is_file():
            st.error("Generated suite was not written to disk.")
            return
        st.info(f"Generated suite: `{out_path.relative_to(ROOT)}`")
    except Exception as exc:  # noqa: BLE001
        st.error(f"AI generation failed: {exc}")
        return

    robot_code = GENERATED_SUITE.read_text(encoding="utf-8")
    st.session_state[PENDING_ROBOT_EDITOR_KEY] = robot_code
    st.session_state[PENDING_GEN_CTX_KEY] = {
        "project_name": project_name,
        "test_name": test_name,
        "overwrite": overwrite,
        "csv_bytes": csv_bytes,
    }
    st.success(
        "Generation complete. Review the script below, then **💾 Save & Execute** or **❌ Discard**."
    )


def run_existing_test(
    robot_path: Path,
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
) -> None:
    """Execute a pre-saved project .robot file directly — no AI generation step."""
    if not robot_path.is_file():
        st.error(f"Test file not found: {robot_path}")
        return
    if not headless:
        st.warning("A browser window will open shortly. Please do not close it manually.")
    run_name = f"proj_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        from run_test import build_robot_run

        cmd, out_dir = build_robot_run(
            sandbox_url.strip(),
            username.strip(),
            password.strip(),
            robot_path,
            run_name=run_name,
            headless=headless,
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not prepare Robot run: {exc}")
        return
    st.subheader(f"Running saved test: `{robot_path.name}`")
    code, full_log = stream_robot_logs(cmd, ROOT)
    if code == 0:
        st.success("Test Passed! ✅")
    else:
        st.error("Test Failed. ❌")
    rp = out_dir / "report.html"
    lp = out_dir / "log.html"
    st.session_state.last_run = {
        "out_dir": str(out_dir.relative_to(ROOT)),
        "report_path": str(rp.resolve()) if rp.is_file() else None,
        "log_path": str(lp.resolve()) if lp.is_file() else None,
        "passed": code == 0,
    }
    render_in_app_run_summary(out_dir, key_prefix="proj_run_sum")
    st.session_state["last_run_summary_rendered_for"] = str(out_dir.resolve())
    render_report_log_actions(
        rp if rp.is_file() else None,
        lp if lp.is_file() else None,
        str(out_dir.relative_to(ROOT)),
        key_prefix="proj_run",
        passed=code == 0,
    )
    with st.expander("Full log (copy)"):
        st.code(full_log or "(empty)", language="text")


def run_project_entire_suite(
    project_name: str,
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
    *,
    use_pabot: bool = False,
) -> None:
    """Run Robot (or Pabot) against all suites in Saved_Projects/<project>/Tests/; output under project Results/."""
    if not _HAS_WORKSPACE or _pm is None:
        st.error("Workspace module unavailable.")
        return
    tests_dir = _pm.get_project_path(project_name) / "Tests"
    if not tests_dir.is_dir():
        st.error(f"No Tests/ folder for project `{project_name}`.")
        return
    robot_files = sorted(tests_dir.glob("*.robot"))
    if not robot_files:
        st.error("No `.robot` files in this project's Tests/ folder.")
        return
    if not headless:
        st.warning("A browser window will open shortly. Please do not close it manually.")
    run_ts = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir = _pm.get_project_path(project_name) / "Results" / run_ts
    try:
        from run_test import build_robot_run

        cmd, out_dir = build_robot_run(
            sandbox_url.strip(),
            username.strip(),
            password.strip(),
            tests_dir,
            output_dir=out_dir,
            headless=headless,
            use_pabot=use_pabot,
            pabot_processes=3,
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not prepare Robot run: {exc}")
        return
    st.subheader(f"Project suite: `{project_name}`")
    runner = "Pabot (parallel)" if use_pabot else "Robot"
    st.caption(
        f"**{runner}** — **{len(robot_files)}** suite file(s) under `{tests_dir.relative_to(ROOT)}` → "
        f"`{out_dir.relative_to(ROOT)}`"
    )
    code, full_log = stream_robot_logs(cmd, ROOT)
    passed = code == 0
    if passed:
        st.success(f"Project suite finished successfully (exit {code}).")
    else:
        st.error(
            f"Project suite finished with failures (exit {code}). Review the report for details."
        )
    rp = out_dir / "report.html"
    lp = out_dir / "log.html"
    st.session_state.last_run = {
        "out_dir": str(out_dir.relative_to(ROOT)),
        "report_path": str(rp.resolve()) if rp.is_file() else None,
        "log_path": str(lp.resolve()) if lp.is_file() else None,
        "passed": passed,
        "run_kind": "project_suite",
    }
    render_in_app_run_summary(out_dir, key_prefix="proj_suite_sum")
    st.session_state["last_run_summary_rendered_for"] = str(out_dir.resolve())
    render_report_log_actions(
        rp if rp.is_file() else None,
        lp if lp.is_file() else None,
        str(out_dir.relative_to(ROOT)),
        key_prefix="proj_suite",
        passed=passed,
    )
    if rp.is_file():
        st.download_button(
            label="Download report.html",
            data=rp.read_bytes(),
            file_name=f"{project_name}_{run_ts}_report.html",
            mime="text/html",
            key="download_project_suite_report",
        )
    with st.expander("Full log (copy)"):
        st.code(full_log or "(empty)", language="text")


def render_persisted_run_panel() -> None:
    """Keep report/log actions available across Streamlit reruns."""
    lr = st.session_state.get("last_run")
    if not lr:
        return
    st.divider()
    st.subheader("Latest test results")
    st.caption(
        "Artifacts stay here until you run again. Use the buttons below to open report/log in your browser."
    )
    rp = lr.get("report_path")
    lp = lr.get("log_path")
    report_path = Path(rp) if rp else None
    log_path = Path(lp) if lp else None

    out_rel = lr.get("out_dir")
    if out_rel:
        od = ROOT / out_rel
        if st.session_state.get("last_run_summary_rendered_for") != str(od.resolve()):
            render_run_summary_for_last_run(key_prefix="persisted_sum")

    render_report_log_actions(
        report_path if report_path and report_path.is_file() else None,
        log_path if log_path and log_path.is_file() else None,
        str(lr.get("out_dir", "")),
        key_prefix="persisted_run",
        passed=lr.get("passed"),
    )


def sync_sidebar_api_key(env_key: str, state_suffix: str, sidebar_api_key: str) -> None:
    """
    Apply optional sidebar API key to os.environ, or clear a prior sidebar-only value
    so .env / secrets can repopulate after the field is cleared.
    """
    state_key = f"_last_sidebar_{state_suffix}"
    last = st.session_state.get(state_key, "")
    cur = (sidebar_api_key or "").strip()
    if cur:
        os.environ[env_key] = cur
        st.session_state[state_key] = cur
        return
    if last and os.environ.get(env_key) == last:
        del os.environ[env_key]
    st.session_state[state_key] = ""
