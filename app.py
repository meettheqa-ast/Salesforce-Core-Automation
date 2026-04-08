"""
Streamlit UI: Test Intelligence Platform (AI QA automation).

Run from project root:
  streamlit run app.py

Orchestration lives here; helpers are split across app_config, app_csv, app_catalog, app_pipeline,
and app_reporting (in-app run summaries after each Robot execution, invoked from app_pipeline).
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Test Intelligence Platform",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

import os
from pathlib import Path

from app_catalog import rebuild_keyword_catalog, render_capabilities_cheat_sheet
from app_config import (
    CLARIFY_SESSION_KEY,
    _HAS_ORG_INSPECTOR,
    _HAS_SMOKE,
    _HAS_WORKSPACE,
    _detect_smoke_fn,
    _org_inspector_mod,
    _pm,
    _smoke_prompt_fn,
)
from app_csv import (
    csv_upload_bytes,
    render_csv_preview_scrollable,
    sync_csv_session_cache,
)
from app_analytics import render_project_analytics_dashboard
from app_pipeline import (
    build_augmented_prompt,
    field_input_label,
    render_pending_robot_review_panel,
    render_persisted_run_panel,
    run_automation_pipeline,
    run_existing_test,
    run_project_entire_suite,
    sync_sidebar_api_key,
)

# First load: build catalog once so the app starts with a current index.
if "_catalog_initialized" not in st.session_state:
    try:
        rebuild_keyword_catalog()
        st.session_state._catalog_init_error = None
    except Exception as exc:  # noqa: BLE001
        st.session_state._catalog_init_error = str(exc)
    st.session_state._catalog_initialized = True

if st.session_state.get("_catalog_init_error"):
    st.sidebar.warning(
        "Startup catalog refresh failed: "
        f"{st.session_state['_catalog_init_error']}. "
        "Fix errors and reload, or run generate_keyword_mapping.py manually."
    )


def _init_sf_credential_session_keys() -> None:
    """Ensure Streamlit widget keys for Salesforce credentials exist before first render."""
    for k in ("sf_sandbox_url", "sf_username", "sf_password", "sf_security_token", "slack_webhook_url"):
        if k not in st.session_state:
            st.session_state[k] = ""
    if "active_environment" not in st.session_state:
        st.session_state["active_environment"] = "Dev"
    if "active_persona" not in st.session_state:
        st.session_state["active_persona"] = "System Admin"
    if "edit_creds_mode" not in st.session_state:
        st.session_state["edit_creds_mode"] = False


def _apply_project_credentials_to_session() -> None:
    """Load credentials for the active project + environment + persona into widget keys.

    Re-applies whenever any of the three selectors change.
    Switching to ad-hoc does not clear typed credentials.
    """
    if not _HAS_WORKSPACE or _pm is None:
        return
    current = st.session_state.get("active_project") or ""
    env = st.session_state.get("active_environment") or "Dev"
    persona = st.session_state.get("active_persona") or "System Admin"
    bound_key = f"{current}::{env}::{persona}"
    if st.session_state.get("_credentials_bound_key") == bound_key:
        return
    if current:
        cfg = _pm.read_project_config(current, environment=env, persona=persona)
        st.session_state["sf_sandbox_url"] = cfg.get("sandbox_url") or ""
        st.session_state["sf_username"] = cfg.get("username") or ""
        st.session_state["sf_password"] = cfg.get("password") or ""
        st.session_state["sf_security_token"] = cfg.get("security_token") or ""
        st.session_state["slack_webhook_url"] = cfg.get("slack_webhook_url") or ""
        jira_cfg = _pm.read_jira_config(current)
        st.session_state["jira_base_url"] = jira_cfg.get("jira_base_url") or ""
        st.session_state["jira_api_token"] = jira_cfg.get("jira_api_token") or ""
        st.session_state["jira_project_key"] = jira_cfg.get("jira_project_key") or ""
        st.session_state["edit_creds_mode"] = False
    st.session_state["_credentials_bound_key"] = bound_key


# ---------------------------------------------------------------------------
# Active Workspace header (project selector + credentials in main area)
# ---------------------------------------------------------------------------

def _render_workspace_header() -> tuple[str, str, str, str]:
    """Top-of-page project + environment + persona selector and credentials panel.

    Returns ``(active_proj, sandbox_url, username, password)``.
    """
    col_proj, col_creds = st.columns([1, 2], gap="large")

    # ── Column 1: Project / Environment / Persona selectors ───────────
    with col_proj:
        _lbl_col, _sync_col = st.columns([3, 1])
        _lbl_col.markdown("**🗂️ Project**")
        if _sync_col.button("🔄 Sync", key="sync_workspace_btn", help="Pull latest from remote"):
            try:
                from app_git import sync_local_workspace

                _repo_root = Path(__file__).resolve().parent
                if sync_local_workspace(_repo_root):
                    st.success("Workspace synced with remote! ☁️")
                    st.rerun()
                else:
                    st.warning("Sync failed — check that a Git remote is configured and accessible.")
            except Exception as _exc:  # noqa: BLE001
                st.warning(f"Could not sync workspace: {_exc}")
        if _HAS_WORKSPACE and _pm is not None:
            active_proj = st.session_state.get("active_project", "")
            all_projs = _pm.list_projects()
            opts = ["(none — ad-hoc)"] + all_projs + ["+ Create New Project"]
            idx = 0
            if active_proj in opts:
                idx = opts.index(active_proj)
            proj_sel = st.selectbox(
                "Active Project",
                opts,
                index=idx,
                label_visibility="collapsed",
            )
            if proj_sel == "+ Create New Project":
                with st.form("new_proj_form"):
                    new_name = st.text_input("Name (alphanumeric + underscores)")
                    new_desc = st.text_input("Description (optional)")
                    if st.form_submit_button("✅ Create Project"):
                        try:
                            _pm.create_project(new_name, new_desc)
                            st.session_state["active_project"] = new_name
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
            elif proj_sel == "(none — ad-hoc)":
                st.session_state["active_project"] = ""
            else:
                st.session_state["active_project"] = proj_sel

            # Environment + Persona selectors (only when a project is active)
            _active = st.session_state.get("active_project") or ""
            if _active:
                env_list = _pm.list_environments(_active) or ["Dev"]
                env_opts = env_list + ["+ Add Environment"]
                cur_env = st.session_state.get("active_environment", "Dev")
                env_idx = env_opts.index(cur_env) if cur_env in env_opts else 0
                env_sel = st.selectbox("Environment", env_opts, index=env_idx, key="env_selectbox")

                if env_sel == "+ Add Environment":
                    new_env = st.text_input("New environment name", key="new_env_name_input")
                    if st.button("➕ Create", key="create_env_btn") and new_env.strip():
                        _pm.write_project_credentials(_active, "", "", "", environment=new_env.strip())
                        st.session_state["active_environment"] = new_env.strip()
                        st.rerun()
                else:
                    st.session_state["active_environment"] = env_sel

                act_env = st.session_state.get("active_environment") or "Dev"
                persona_list = _pm.list_personas(_active, act_env) or ["System Admin"]
                persona_opts = persona_list + ["+ Add Persona"]
                cur_per = st.session_state.get("active_persona", "System Admin")
                per_idx = persona_opts.index(cur_per) if cur_per in persona_opts else 0
                per_sel = st.selectbox("Test Persona", persona_opts, index=per_idx, key="persona_selectbox")

                if per_sel == "+ Add Persona":
                    new_per = st.text_input("New persona name", key="new_persona_name_input")
                    if st.button("➕ Create", key="create_persona_btn") and new_per.strip():
                        _pm.write_project_credentials(
                            _active, "", "", "",
                            environment=act_env, persona=new_per.strip(),
                        )
                        st.session_state["active_persona"] = new_per.strip()
                        st.rerun()
                else:
                    st.session_state["active_persona"] = per_sel
        else:
            st.warning("Workspace module unavailable.")

    _apply_project_credentials_to_session()

    # ── Column 2: Credentials ─────────────────────────────────────────
    _active = st.session_state.get("active_project") or ""
    _env = st.session_state.get("active_environment") or "Dev"
    _persona = st.session_state.get("active_persona") or "System Admin"
    editing = st.session_state.get("edit_creds_mode", False)
    is_project_mode = bool(_active) and _HAS_WORKSPACE and _pm is not None

    with col_creds:
        if is_project_mode:
            st.markdown(f"**🔐 Credentials — {_env} / {_persona}**")
        else:
            st.markdown("**🔐 Salesforce Credentials (Ad-Hoc)**")

        readonly = is_project_mode and not editing

        ca, cb = st.columns(2)
        with ca:
            st.text_input(
                "Sandbox URL",
                placeholder="https://yourorg--sbx.sandbox.my.salesforce.com/",
                help="Login URL for your Salesforce sandbox.",
                key="sf_sandbox_url",
                disabled=readonly,
            )
        with cb:
            st.text_input(
                "Username",
                placeholder="user@example.com",
                key="sf_username",
                disabled=readonly,
            )
        cc, cd = st.columns(2)
        with cc:
            st.text_input(
                "Password",
                type="password",
                placeholder="••••••••",
                key="sf_password",
                disabled=readonly,
            )
        with cd:
            st.text_input(
                "Security Token",
                type="password",
                placeholder="Optional — leave blank if IP whitelisted",
                help="Required for API data seeding when your IP isn't in the org's trusted range.",
                key="sf_security_token",
                disabled=readonly,
            )
        st.text_input(
            "Slack Webhook URL (Optional)",
            placeholder="https://hooks.slack.com/services/T.../B.../...",
            help="Incoming Webhook URL. Suite run summaries will be posted to this channel automatically.",
            key="slack_webhook_url",
            disabled=readonly,
        )

        if is_project_mode:
            st.markdown("**📋 Jira / Zephyr Integration (Optional)**")
            ja, jb = st.columns(2)
            with ja:
                st.text_input(
                    "Jira Base URL",
                    placeholder="https://yourorg.atlassian.net",
                    key="jira_base_url",
                    disabled=readonly,
                )
            with jb:
                st.text_input(
                    "Jira Project Key",
                    placeholder="e.g. QA or SFDC",
                    key="jira_project_key",
                    disabled=readonly,
                )
            st.text_input(
                "Jira API Token",
                type="password",
                placeholder="Atlassian API token or PAT",
                help="Used to push Pass/Fail results to Zephyr Scale or Jira comments after suite runs.",
                key="jira_api_token",
                disabled=readonly,
            )

        # ── Action buttons ────────────────────────────────────────────
        if is_project_mode:
            if not editing:
                if st.button("✏️ Edit Credentials", key="edit_creds_btn"):
                    st.session_state["edit_creds_mode"] = True
                    st.rerun()
            else:
                b_save, b_cancel = st.columns(2)
                with b_save:
                    if st.button("💾 Save Credentials", type="primary", key="save_creds_btn"):
                        _pm.write_project_credentials(
                            _active,
                            st.session_state.get("sf_sandbox_url", ""),
                            st.session_state.get("sf_username", ""),
                            st.session_state.get("sf_password", ""),
                            st.session_state.get("sf_security_token", ""),
                            st.session_state.get("slack_webhook_url", ""),
                            environment=_env,
                            persona=_persona,
                        )
                        _pm.write_jira_config(
                            _active,
                            st.session_state.get("jira_base_url", ""),
                            st.session_state.get("jira_api_token", ""),
                            st.session_state.get("jira_project_key", ""),
                        )
                        st.session_state["edit_creds_mode"] = False
                        st.session_state.pop("_credentials_bound_key", None)
                        st.toast(f"Credentials saved to **{_active}** → **{_env}** / **{_persona}**.")
                        st.rerun()
                with b_cancel:
                    if st.button("❌ Cancel", key="cancel_creds_btn"):
                        st.session_state["edit_creds_mode"] = False
                        st.session_state.pop("_credentials_bound_key", None)
                        st.rerun()

    tok = st.session_state.get("sf_security_token", "").strip()
    if tok:
        os.environ["SF_SECURITY_TOKEN"] = tok
    else:
        os.environ.pop("SF_SECURITY_TOKEN", None)

    return (
        st.session_state.get("active_project", ""),
        st.session_state.get("sf_sandbox_url", ""),
        st.session_state.get("sf_username", ""),
        st.session_state.get("sf_password", ""),
    )


# ---------------------------------------------------------------------------
# Tab 1 — Test Builder
# ---------------------------------------------------------------------------

def _render_test_builder_tab(
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
    active_proj: str,
) -> None:
    """AI prompt, generation controls, and human-in-the-loop review editor."""
    test_target_name = ""
    if active_proj:
        test_target_name = st.text_input(
            "Test Case Name",
            placeholder="e.g. B2B_Lead_Creation",
            help=f"Saved under Saved_Projects/{active_proj}/Tests/. Leave blank for ad-hoc runs.",
        )

    user_story_id = st.text_input(
        "🎫 User Story / Ticket ID (Optional)",
        placeholder="e.g., US-1234",
        help="Links the generated test to a requirement. "
        "The tag is injected into the .robot [Tags] section and the file is committed to a feature branch.",
        key="user_story_id_input",
    )

    def _update_prompt() -> None:
        st.session_state["main_prompt_text"] = st.session_state.main_prompt_text_widget

    prompt = st.text_area(
        "Describe your test in plain English",
        value=st.session_state.get("main_prompt_text", ""),
        height=160,
        placeholder='e.g. "Verify I can create an Account named Acme Corp and then delete it"',
        help="Natural-language description of what the test should do.",
        key="main_prompt_text_widget",
        on_change=_update_prompt,
    )

    def _clear_prompt() -> None:
        st.session_state["main_prompt_text"] = ""
        st.session_state.pop("main_prompt_text_widget", None)

    auto_gen = st.checkbox(
        "🎲 Auto-generate missing test data (AI/Faker)",
        value=True,
        help="When checked, the AI invents realistic dummy data for any required fields "
        "instead of asking you to fill in a clarification form.",
    )

    csv_col, img_col = st.columns(2)
    with csv_col:
        uploaded_csv = st.file_uploader(
            "Upload Test Data (CSV)",
            type=["csv"],
            help="Optional. Each row is sent to the AI so it can generate FOR loops or repeated steps.",
            key="pm_test_data_csv",
        )
    with img_col:
        uploaded_image = st.file_uploader(
            "📸 Upload UI Screenshot (Optional)",
            type=["png", "jpg", "jpeg"],
            help="Upload a screenshot of the Salesforce form/page. The AI will analyse the fields and buttons visible in the image.",
            key="pm_ui_screenshot",
        )
    csv_llm_block = sync_csv_session_cache(uploaded_csv)
    if csv_llm_block:
        with st.expander("Preview parsed CSV (sent to the AI)", expanded=False):
            raw_preview = csv_upload_bytes(uploaded_csv)
            if raw_preview:
                render_csv_preview_scrollable(raw_preview)
            else:
                st.markdown(
                    csv_llm_block
                    if len(csv_llm_block) <= 14000
                    else csv_llm_block[:14000] + "\n\n…_(truncated in UI only)_"
                )

    with st.expander("💡 What can I ask for? (Available Capabilities)", expanded=False):
        render_capabilities_cheat_sheet()

    overwrite_ok = True
    if active_proj and test_target_name and _pm is not None:
        if _pm.test_exists_in_project(active_proj, test_target_name):
            st.warning(f"⚠️ '{test_target_name}' already exists in project '{active_proj}'.")
            if not st.checkbox("Yes, overwrite the existing test script"):
                overwrite_ok = False

    btn_run_col, btn_clear_col = st.columns([3, 1])
    with btn_run_col:
        run_clicked = st.button(
            "🚀 Generate & Run", type="primary", use_container_width=True,
        )
    with btn_clear_col:
        st.button(
            "🧹 Clear", key="clear_prompt_btn", on_click=_clear_prompt,
            use_container_width=True,
        )

    if run_clicked:
        try:
            from ai_bridge import analyze_prompt_for_required_fields
        except ImportError as exc:
            st.error(f"Could not import ai_bridge: {exc}")
            return
        if not sandbox_url.strip() or not username.strip() or not password.strip():
            st.error("Please fill in Sandbox URL, Username, and Password in the workspace header.")
            return
        if not prompt.strip():
            st.error("Please enter a user prompt.")
            return
        if active_proj and test_target_name and not overwrite_ok:
            st.error("Please confirm overwrite before running.")
            return

        is_smoke = False
        final_prompt_txt = prompt.strip()

        if _HAS_SMOKE and _detect_smoke_fn is not None and _smoke_prompt_fn is not None:
            smoke_ctx = _detect_smoke_fn(final_prompt_txt)
            if smoke_ctx:
                is_smoke = True
                sf_obj = smoke_ctx["object"]
                app_n = st.session_state.get("smoke_app_name", "Sales")
                field_context = ""
                if _HAS_ORG_INSPECTOR and _org_inspector_mod is not None:
                    try:
                        with st.spinner(
                            f"🔍 Live Org Inspector: Grabbing picklist values for {sf_obj}..."
                        ):
                            insp = _org_inspector_mod.OrgInspector.from_credentials(
                                sandbox_url, username, password
                            )
                            field_context = insp.get_smoke_field_context(sf_obj)
                            insp.close()
                    except Exception as e:
                        st.warning(f"Live Org Inspector ran into an issue (using fallback): {e}")

                final_prompt_txt = _smoke_prompt_fn(sf_obj, app_n, field_context)
                st.info(
                    f"🔥 **Smoke lifecycle mode** — prompt replaced with the **{sf_obj}** lifecycle "
                    "template. The optional **Lead clarification** form is skipped. "
                    "Org Inspector may run briefly to load picklist values (falls back on error)."
                )

        pm_hint = analyze_prompt_for_required_fields(
            final_prompt_txt,
            csv_bytes=csv_upload_bytes(uploaded_csv),
        )
        if not is_smoke and not auto_gen and pm_hint["should_show_lead_pm_form"]:
            st.session_state[CLARIFY_SESSION_KEY] = {
                "original_prompt": prompt.strip(),
                "missing_fields": list(pm_hint["missing_lead_fields"]),
                "optional_picklists": list(pm_hint["optional_picklist_fields"]),
            }
            st.warning(
                "Lead flow: fill **required** fields below. **Picklist** rows are optional—leave blank "
                "to generate tests that pick a **random visible** dropdown option (stable across orgs). "
                "Then click **Submit & Run Automation**."
            )
        else:
            st.session_state.pop(CLARIFY_SESSION_KEY, None)
            try:
                from ai_bridge import append_csv_data_to_prompt
            except ImportError:

                def append_csv_data_to_prompt(p: str, c: str) -> str:  # type: ignore[misc]
                    p, c = (p or "").rstrip(), (c or "").strip()
                    return p if not c else f"{p}\n\nThe user uploaded CSV test data:\n\n{c}"

            final_prompt = append_csv_data_to_prompt(final_prompt_txt, csv_llm_block)
            img_bytes = uploaded_image.getvalue() if uploaded_image else None
            run_automation_pipeline(
                final_prompt,
                sandbox_url=sandbox_url,
                username=username,
                password=password,
                headless=headless,
                csv_bytes=csv_upload_bytes(uploaded_csv),
                project_name=active_proj if active_proj else None,
                test_name=test_target_name if test_target_name else None,
                overwrite=overwrite_ok,
                auto_generate_data=auto_gen,
                image_bytes=img_bytes,
                user_story_id=user_story_id.strip() if user_story_id else "",
            )

    clarify_ctx = st.session_state.get(CLARIFY_SESSION_KEY)
    if clarify_ctx:
        with st.form("missing_salesforce_data"):
            st.markdown("### ⚠️ Lead test — PM details")
            st.caption(
                "Required fields must be filled. Picklist fields are optional; leave blank to use "
                "`Open Dropdown And Select First Option` (random visible option) in generated tests."
            )
            field_values: dict[str, str] = {}
            for field in clarify_ctx["missing_fields"]:
                field_values[field] = st.text_input(
                    field_input_label(field),
                    key=f"clarify_input_{field.replace(' ', '_')}",
                )
            for field in clarify_ctx.get("optional_picklists", []):
                field_values[field] = st.text_input(
                    field_input_label(field),
                    key=f"clarify_picklist_{field.replace(' ', '_')}",
                    help="Leave blank: random visible option in that dropdown. Or type the exact option label.",
                )
            submit_clarify = st.form_submit_button("Submit & Run Automation")

        if submit_clarify:
            if not sandbox_url.strip() or not username.strip() or not password.strip():
                st.error("Please fill in credentials in the workspace header.")
                return
            missing = clarify_ctx["missing_fields"]
            optional_pl = clarify_ctx.get("optional_picklists", [])
            empty = [f for f in missing if not str(field_values.get(f, "")).strip()]
            if empty:
                st.error(
                    "Please provide all required values: "
                    + ", ".join(field_input_label(f) for f in empty)
                )
                return

            csv_for_llm = str(st.session_state.get("csv_llm_block") or "")
            augmented = build_augmented_prompt(
                clarify_ctx["original_prompt"],
                missing,
                field_values,
                optional_pl,
                csv_llm_block=csv_for_llm,
            )
            st.session_state.pop(CLARIFY_SESSION_KEY, None)
            img_bytes_cl = uploaded_image.getvalue() if uploaded_image else None
            run_automation_pipeline(
                augmented,
                sandbox_url=sandbox_url,
                username=username,
                password=password,
                headless=headless,
                csv_bytes=csv_upload_bytes(uploaded_csv),
                image_bytes=img_bytes_cl,
                project_name=active_proj if active_proj else None,
                test_name=test_target_name if test_target_name else None,
                overwrite=overwrite_ok,
                user_story_id=user_story_id.strip() if user_story_id else "",
            )

    render_pending_robot_review_panel(sandbox_url, username, password, headless)


# ---------------------------------------------------------------------------
# Tab 2 — Suite Execution
# ---------------------------------------------------------------------------

def _render_suite_execution_tab(
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
    active_proj: str,
) -> None:
    """Project suite runs, smoke shortcuts, and saved-test management."""

    col_exec, col_tests = st.columns([1, 1.2], gap="large")

    # ── Column 1: Execution & Smoke ───────────────────────────────────────
    with col_exec:
        # Card: Run Suite
        with st.container(border=True):
            st.subheader("🚀 Run Suite")
            if active_proj and _pm is not None:
                tag_inc_col, tag_exc_col = st.columns(2)
                with tag_inc_col:
                    include_tags = st.text_input(
                        "Include Tags",
                        placeholder="e.g. smoke, US-1234",
                        help="Comma-separated. Only tests matching these tags will run.",
                        key="suite_include_tags",
                    )
                with tag_exc_col:
                    exclude_tags = st.text_input(
                        "Exclude Tags",
                        placeholder="e.g. in-progress, unstable",
                        help="Comma-separated. Tests matching these tags will be skipped.",
                        key="suite_exclude_tags",
                    )
                r1, r2 = st.columns([3, 1])
                with r1:
                    suite_clicked = st.button(
                        "▶️ Execute Project Suite",
                        type="primary",
                        use_container_width=True,
                        key="run_entire_project_suite_btn",
                    )
                with r2:
                    pabot_parallel = st.checkbox(
                        "Parallel",
                        value=False,
                        key="pabot_parallel_project_suite",
                        help="pabot --testlevelsplit --processes 3",
                    )
                seed_data = st.checkbox(
                    "🌱 Seed Data Template Before Run",
                    value=False,
                    key="seed_data_before_run",
                    help="Creates prerequisite records via the API using the project's data template, "
                    "then injects the IDs as Robot variables.",
                )
                if suite_clicked:
                    if not sandbox_url.strip() or not username.strip() or not password.strip():
                        st.error("Fill in credentials in the workspace header.")
                    else:
                        run_project_entire_suite(
                            active_proj,
                            sandbox_url,
                            username,
                            password,
                            headless,
                            use_pabot=pabot_parallel,
                            include_tags=include_tags.strip(),
                            exclude_tags=exclude_tags.strip(),
                            seed_data=seed_data,
                        )
            else:
                st.info("Select an **Active Project** to run a full suite.")

        # Card: Quick Smoke Tests
        if _HAS_SMOKE:
            with st.container(border=True):
                st.subheader("🔥 Quick Smoke Tests")
                st.text_input(
                    "Salesforce App",
                    placeholder="e.g. Sales",
                    key="smoke_app_name",
                    label_visibility="collapsed",
                )
                st.caption("Sets the prompt in **Test Builder** — switch there to review & run.")
                s1, s2 = st.columns(2)
                if s1.button("Lead", use_container_width=True, key="smoke_lead_btn"):
                    st.session_state["main_prompt_text"] = "Run full smoke test for Lead lifecycle"
                    st.rerun()
                if s2.button("Account", use_container_width=True, key="smoke_account_btn"):
                    st.session_state["main_prompt_text"] = "Run full smoke test for Account lifecycle"
                    st.rerun()
                s3, s4 = st.columns(2)
                if s3.button("Contact", use_container_width=True, key="smoke_contact_btn"):
                    st.session_state["main_prompt_text"] = "Run full smoke test for Contact lifecycle"
                    st.rerun()
                if s4.button("Opportunity", use_container_width=True, key="smoke_opp_btn"):
                    st.session_state["main_prompt_text"] = "Run full smoke test for Opportunity lifecycle"
                    st.rerun()

    # ── Column 2: Saved Tests Inventory ───────────────────────────────────
    with col_tests:
        with st.container(border=True):
            hdr_col, refresh_col = st.columns([4, 1])
            hdr_col.subheader("📂 Saved Project Tests")
            if refresh_col.button("🔄", key="refresh_tests_btn", help="Refresh test list"):
                st.rerun()

            if active_proj and _pm is not None:
                saved_tests = _pm.list_project_tests(active_proj)
                if not saved_tests:
                    st.info("No tests saved in this project yet.")
                else:
                    for test in saved_tests:
                        col_a, col_b, col_c = st.columns([3, 1, 1])
                        col_a.write(
                            f"📄 **{test['name']}.robot**\n"
                            f"_{test['modified'].strftime('%Y-%m-%d %H:%M')}_"
                        )
                        if col_b.button("▶ Run", key=f"run_{test['name']}"):
                            if not sandbox_url.strip() or not username.strip() or not password.strip():
                                st.error("Fill credentials first.")
                            else:
                                run_existing_test(test["path"], sandbox_url, username, password, headless)
                        if col_c.button("👁 View", key=f"view_{test['name']}"):
                            st.code(
                                _pm.load_test_source(active_proj, test["name"]),
                                language="robotframework",
                            )
            else:
                st.info("Select a project to see saved tests.")

    # ── Persisted last-run results ─────────────────────────────────────────
    render_persisted_run_panel()


# ---------------------------------------------------------------------------
# Tab 3 — Data Templates
# ---------------------------------------------------------------------------

def _render_data_templates_tab(active_proj: str) -> None:
    """JSON-based TDM template editor for the active project."""
    if not active_proj or not _HAS_WORKSPACE or _pm is None:
        st.info("Select an **Active Project** to manage data templates.")
        return

    from app_tdm import EXAMPLE_TEMPLATE

    existing = _pm.read_data_template(active_proj)
    display = existing if existing.strip() != "[]" else EXAMPLE_TEMPLATE

    st.markdown(
        "Define prerequisite Salesforce records as a JSON array. "
        "Each entry needs `object`, `var_name`, and `fields`. "
        "When **🌱 Seed Data Template Before Run** is checked in Suite Execution, "
        "these records are created via the API and the resulting IDs are injected "
        "as Robot variables."
    )

    template_text = st.text_area(
        "JSON Template",
        value=display,
        height=300,
        key="tdm_template_editor",
        help='[{"object":"Account","var_name":"SeededAccountId","fields":{"Name":"Acme"}}]',
    )

    if st.button("💾 Save Data Template", key="save_tdm_btn"):
        try:
            _pm.write_data_template(active_proj, template_text.strip())
            st.toast(f"Data template saved to **{active_proj}**.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Invalid JSON: {exc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main_ui() -> None:
    st.title("Test Intelligence Platform")
    st.caption(
        "Describe a test in plain English. The AI generates Robot Framework code, "
        "then executes it against your sandbox."
    )

    try:
        from ai_bridge import hydrate_llm_env

        hydrate_llm_env()
    except ImportError:
        pass

    _init_sf_credential_session_keys()

    if "llm_provider_radio" not in st.session_state:
        p = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
        st.session_state["llm_provider_radio"] = "OpenAI" if p == "openai" else "Gemini"

    if "smoke_app_name" not in st.session_state:
        st.session_state["smoke_app_name"] = "Sales"

    # ── Minimal sidebar: branding + execution mode + AI / LLM ──────────────
    with st.sidebar:
        st.markdown(
            """
<style>
  .tip-brand-title { margin: 0 0 0.15rem 0; font-size: 1.35rem; font-weight: 700;
    color: #0047B3; letter-spacing: -0.02em; }
  .tip-brand-sub { margin: 0; font-size: 0.78rem; color: #42526E; font-weight: 500; }
</style>
<div>
  <p class="tip-brand-title">🚀 AI QA Portal</p>
  <p class="tip-brand-sub">Test Intelligence Platform</p>
</div>
            """,
            unsafe_allow_html=True,
        )
        st.divider()
        execution_mode = st.radio(
            "Execution mode",
            ("Background (Fast)", "Watch on Screen (Debug)"),
            index=1,
            help="Background uses headless Chrome. Watch opens a visible browser for debugging.",
        )
        headless = execution_mode == "Background (Fast)"
        st.divider()
        st.subheader("AI (LLM)")
        llm_prov = st.radio(
            "LLM provider",
            ("Gemini", "OpenAI"),
            horizontal=True,
            key="llm_provider_radio",
            help="Default stack uses Google Gemini. Use OpenAI only if LLM_PROVIDER=openai in `.env`.",
        )
        os.environ["LLM_PROVIDER"] = "gemini" if llm_prov == "Gemini" else "openai"

        gemini_sidebar_key = ""
        openai_sidebar_key = ""
        if llm_prov == "Gemini":
            gemini_sidebar_key = st.text_input(
                "Gemini API key (optional)",
                type="password",
                placeholder="Uses .env or .streamlit/secrets.toml if empty",
                help=(
                    "Create a key at https://aistudio.google.com/apikey. "
                    "Set GEMINI_API_KEY in `.env`, or paste here for this session only."
                ),
                key="gemini_sidebar_key",
            )
        else:
            openai_sidebar_key = st.text_input(
                "OpenAI API key (optional)",
                type="password",
                placeholder="Uses .env or .streamlit/secrets.toml if empty",
                help=(
                    "Set OPENAI_API_KEY in `.env` (see `.env.example`) or "
                    "`.streamlit/secrets.toml` — see `secrets.toml.example`."
                ),
                key="openai_sidebar_key",
            )

        sync_sidebar_api_key(
            "GEMINI_API_KEY",
            "gemini",
            gemini_sidebar_key if llm_prov == "Gemini" else "",
        )
        sync_sidebar_api_key(
            "OPENAI_API_KEY",
            "openai",
            openai_sidebar_key if llm_prov == "OpenAI" else "",
        )

    # ── Active Workspace (project + credentials) — collapsible ──────────
    with st.expander("⚙️ Workspace & Credentials", expanded=True):
        active_proj, sandbox_url, username, password = _render_workspace_header()

    # ── Three-tab command center ──────────────────────────────────────────
    tab_builder, tab_exec, tab_data, tab_analytics = st.tabs(
        ["🏗️ Test Architect", "🚀 Release Manager", "🧪 Data Templates", "📊 Analytics"]
    )
    with tab_builder:
        _render_test_builder_tab(sandbox_url, username, password, headless, active_proj)
    with tab_exec:
        _render_suite_execution_tab(sandbox_url, username, password, headless, active_proj)
    with tab_data:
        _render_data_templates_tab(active_proj)
    with tab_analytics:
        render_project_analytics_dashboard(active_proj)


main_ui()
