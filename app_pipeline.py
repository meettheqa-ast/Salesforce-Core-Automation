"""AI generation, Robot execution, results UI, and PM clarification helpers."""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import requests
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
    """Open an HTML report/log in a new browser tab via a file:/// URI."""
    path = path.resolve()
    if not path.is_file():
        return
    webbrowser.open_new_tab(f"file:///{path.as_posix()}")


def send_slack_notification(
    webhook_url: str,
    project_name: str,
    total_tests: int,
    passed: int,
    failed: int,
    elapsed_time: str,
) -> bool:
    """Post a suite run summary to a Slack Incoming Webhook.

    Returns ``True`` if the message was accepted (HTTP 200), ``False`` otherwise.
    Network or formatting errors are caught so they never crash the app.
    """
    pass_rate = f"{passed / total_tests * 100:.0f}" if total_tests else "N/A"
    status_emoji = "✅" if failed == 0 else "⚠️" if failed < passed else "🔴"
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🧪 Salesforce Automation Run Complete",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Project:*\n{project_name}"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{status_emoji} {pass_rate}% pass rate"},
                    {"type": "mrkdwn", "text": f"*Results:*\n✅ {passed} Passed  |  ❌ {failed} Failed"},
                    {"type": "mrkdwn", "text": f"*Duration:*\n⏱️ {elapsed_time}"},
                ],
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Total tests: {total_tests}  •  Sent from *Test Intelligence Platform*",
                    }
                ],
            },
        ],
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


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


def commit_pending_test() -> None:
    """Save the pending script to the project and commit to Git. Does NOT execute."""
    ctx = st.session_state.get(PENDING_GEN_CTX_KEY)
    if not ctx:
        return
    code_text = st.session_state.get(PENDING_ROBOT_EDITOR_KEY, "")
    if not str(code_text).strip():
        st.error("Generated script is empty.")
        return

    project_name = ctx.get("project_name")
    test_name = ctx.get("test_name")
    overwrite = ctx.get("overwrite", True)
    csv_bytes = ctx.get("csv_bytes")
    user_story_id = ctx.get("user_story_id", "")

    if not project_name or not test_name:
        st.error("A project and test name are required to save. Set them above the prompt.")
        return
    if not _HAS_WORKSPACE or _pm is None:
        st.error("Workspace module unavailable.")
        return

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
            "**Generate & Run**, then regenerate before saving."
        )
        return
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not save to project: {exc}")
        return

    if user_story_id:
        try:
            from app_git import commit_test_to_branch

            ok = commit_test_to_branch(
                ROOT, str(proj_robot), user_story_id, test_name,
            )
            if ok:
                st.toast(f"Committed to branch: feature/{user_story_id} 🌿")
        except Exception:  # noqa: BLE001
            pass

    clear_pending_generation()
    st.rerun()


def debug_pending_test(
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
) -> None:
    """Run the draft script locally for debugging. Does NOT save to project or Git."""
    ctx = st.session_state.get(PENDING_GEN_CTX_KEY)
    if not ctx:
        return
    code_text = st.session_state.get(PENDING_ROBOT_EDITOR_KEY, "")
    if not str(code_text).strip():
        st.error("Generated script is empty.")
        return

    GENERATED_SUITE.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_SUITE.write_text(str(code_text), encoding="utf-8")

    run_temp_generated_suite(
        sandbox_url, username, password, headless, key_prefix="debug_inline_run"
    )


def render_pending_robot_review_panel(
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
) -> None:
    """Show editor + Save & Commit / Debug Run / Discard when AI generation produced a pending script."""
    if not st.session_state.get(PENDING_GEN_CTX_KEY):
        return

    st.divider()
    st.subheader("Review generated Robot")
    st.caption(
        "Edit the script if needed. **Save & Commit** persists to the project and Git branch. "
        "**Debug Run** executes the draft locally without saving. **Discard** clears this draft."
    )
    st.text_area(
        "Generated `.robot`",
        height=500,
        key=PENDING_ROBOT_EDITOR_KEY,
        help="Robot Framework syntax. Fix locators or variables before running.",
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("💾 Save & Commit", type="primary", key="pending_commit_btn"):
            commit_pending_test()
    with c2:
        if st.button("▶️ Debug Run (Local)", key="pending_debug_btn"):
            if not sandbox_url.strip() or not username.strip() or not password.strip():
                st.error("Please fill in Sandbox URL, Username, and Password.")
                return
            debug_pending_test(sandbox_url, username, password, headless)
    with c3:
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


_AUTO_GEN_INSTRUCTION = (
    "\n\nCRITICAL: The user has requested auto-generated data. DO NOT return a "
    "clarification JSON asking for missing fields. You MUST invent realistic dummy "
    "data (e.g. 'Acme Corp', 'John Doe', '555-0199') for any required fields and "
    "immediately generate the Robot Framework script. For picklist fields, use "
    "GlobalKeywords.Open Dropdown And Select First Option with the field label."
)


def run_automation_pipeline(
    final_prompt: str,
    *,
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
    csv_bytes: bytes | None = None,
    image_bytes: bytes | None = None,
    project_name: str | None = None,
    test_name: str | None = None,
    overwrite: bool = False,
    auto_generate_data: bool = False,
    user_story_id: str = "",
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

    effective_prompt = final_prompt.strip()
    if auto_generate_data:
        effective_prompt += _AUTO_GEN_INSTRUCTION

    if user_story_id:
        effective_prompt += (
            f"\n\nCRITICAL: You MUST include the tag    {user_story_id}    "
            "in the [Tags] section of every Robot Framework test case you generate."
        )

    try:
        from app_schema import get_schema_context

        sec_tok = os.environ.get("SF_SECURITY_TOKEN", "")
        with st.spinner("🔍 Fetching org schema for field accuracy…"):
            schema_ctx = get_schema_context(
                effective_prompt, sandbox_url, username, password, sec_tok,
            )
        if schema_ctx:
            effective_prompt += schema_ctx
            st.caption("✅ Live org schema injected into AI context.")
    except Exception:  # noqa: BLE001
        pass

    if image_bytes:
        effective_prompt += (
            "\n\nCRITICAL: I have attached a screenshot of the Salesforce UI. "
            "Analyze this image to identify the exact field names, button labels, "
            "and layout structure. Generate the Robot Framework test to interact "
            "with the elements you see in this screenshot."
        )

    try:
        with st.spinner("AI is architecting your test case…"):
            out_path = generate_test_from_prompt(
                effective_prompt,
                csv_bytes=csv_bytes,
                image_bytes=image_bytes,
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
        "user_story_id": user_story_id,
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
    include_tags: str = "",
    exclude_tags: str = "",
    seed_data: bool = False,
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
    seeded_vars: dict[str, str] | None = None
    teardown_list: list[dict[str, str]] = []
    if seed_data and _HAS_WORKSPACE and _pm is not None:
        template_str = _pm.read_data_template(project_name)
        if template_str.strip() not in ("", "[]"):
            try:
                from app_tdm import seed_salesforce_data

                sec_tok = os.environ.get("SF_SECURITY_TOKEN", "")
                with st.spinner("🌱 Seeding prerequisite data via API…"):
                    seeded_vars, teardown_list = seed_salesforce_data(
                        sandbox_url, username, password, sec_tok, template_str,
                    )
                if seeded_vars:
                    summary = ", ".join(f"{k}={v[:15]}…" for k, v in seeded_vars.items())
                    st.toast(f"Seeded: {summary}")
                    st.success(f"🌱 Injected **{len(seeded_vars)}** variable(s): {summary}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Data seeding failed: {exc}")
                return
        else:
            st.warning("Seed checkbox is on but the data template is empty. Skipping.")

    try:
        from run_test import build_robot_run

        inc_list = [t.strip() for t in include_tags.split(",") if t.strip()] if include_tags else None
        exc_list = [t.strip() for t in exclude_tags.split(",") if t.strip()] if exclude_tags else None
        cmd, out_dir = build_robot_run(
            sandbox_url.strip(),
            username.strip(),
            password.strip(),
            tests_dir,
            output_dir=out_dir,
            headless=headless,
            use_pabot=use_pabot,
            pabot_processes=3,
            include_tags=inc_list,
            exclude_tags=exc_list,
            variables=seeded_vars,
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not prepare Robot run: {exc}")
        return
    try:
        st.subheader(f"Project suite: `{project_name}`")
        runner = "Pabot (parallel)" if use_pabot else "Robot"
        filter_info = ""
        if inc_list:
            filter_info += f"  •  **Include:** {', '.join(inc_list)}"
        if exc_list:
            filter_info += f"  •  **Exclude:** {', '.join(exc_list)}"
        st.caption(
            f"**{runner}** — **{len(robot_files)}** suite file(s) under `{tests_dir.relative_to(ROOT)}` → "
            f"`{out_dir.relative_to(ROOT)}`{filter_info}"
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

        # ── Slack notification ────────────────────────────────────────
        slack_url = st.session_state.get("slack_webhook_url", "").strip()
        if slack_url:
            xml_path = out_dir / "output.xml"
            total, n_pass, n_fail, elapsed = 0, 0, 0, "N/A"
            if xml_path.is_file():
                try:
                    from robot.api import ExecutionResult

                    result = ExecutionResult(str(xml_path))
                    stats = result.statistics.total.all
                    n_pass = stats.passed
                    n_fail = stats.failed
                    total = n_pass + n_fail
                    elapsed_ms = result.suite.elapsed_time.total_seconds()
                    elapsed = f"{elapsed_ms:.1f}s"
                except Exception:  # noqa: BLE001
                    total = len(robot_files)
                    elapsed = "unknown"
            ok = send_slack_notification(
                slack_url, project_name, total, n_pass, n_fail, elapsed,
            )
            if ok:
                st.toast("Slack notification sent!")
            else:
                st.warning("Could not deliver Slack notification — check the webhook URL.")
        with st.expander("Full log (copy)"):
            st.code(full_log or "(empty)", language="text")
    finally:
        # ── Zero Data Footprint: delete seeded records ────────────────
        if teardown_list:
            try:
                from app_tdm import teardown_salesforce_data

                sec_tok = os.environ.get("SF_SECURITY_TOKEN", "")
                with st.spinner("🧹 Cleaning up seeded data…"):
                    deleted = teardown_salesforce_data(
                        sandbox_url, username, password, sec_tok, teardown_list,
                    )
                st.toast(f"🧹 Cleaned up {deleted}/{len(teardown_list)} seeded record(s)!")
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Data teardown encountered an error: {exc}")


def render_persisted_run_panel() -> None:
    """Keep report/log actions available across Streamlit reruns."""
    lr = st.session_state.get("last_run")
    if not lr:
        return
    st.divider()
    col_hdr, col_clear = st.columns([4, 1])
    col_hdr.subheader("Latest test results")
    if col_clear.button("🧹 Clear Run Results", key="clear_run_results_btn", type="secondary"):
        st.session_state.pop("last_run", None)
        st.session_state.pop("last_run_summary_rendered_for", None)
        st.rerun()
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
