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

import json
import os
from pathlib import Path

from app_catalog import rebuild_keyword_catalog, render_capabilities_cheat_sheet
from app_config import (
    CLARIFY_SESSION_KEY,
    PENDING_GEN_CTX_KEY,
    PENDING_ROBOT_EDITOR_KEY,
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
    clear_pending_generation,
    field_input_label,
    load_test_into_editor,
    render_pending_robot_review_panel,
    render_persisted_run_panel,
    run_automation_pipeline,
    run_existing_test,
    run_mcp_stepwise_pipeline,
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
        st.session_state["sf_sandbox_url"] = (cfg.get("sandbox_url") or "").strip()
        st.session_state["sf_username"] = (cfg.get("username") or "").strip()
        st.session_state["sf_password"] = cfg.get("password") or ""
        st.session_state["sf_security_token"] = (cfg.get("security_token") or "").strip()
        st.session_state["slack_webhook_url"] = (cfg.get("slack_webhook_url") or "").strip()
        jira_cfg = _pm.read_jira_config(current)
        st.session_state["jira_base_url"] = jira_cfg.get("jira_base_url") or ""
        st.session_state["jira_api_token"] = jira_cfg.get("jira_api_token") or ""
        st.session_state["jira_project_key"] = jira_cfg.get("jira_project_key") or ""
        st.session_state["edit_creds_mode"] = False
    st.session_state["_credentials_bound_key"] = bound_key


# ---------------------------------------------------------------------------
# Project Settings dialog
# ---------------------------------------------------------------------------

@st.dialog("Project Settings")
def _project_settings_dialog(project_name: str) -> None:
    """Modal dialog for editing project metadata (project.json)."""
    meta = _pm.read_project_meta(project_name)

    st.caption(f"Project: **{meta.get('display_name', project_name)}**")
    st.caption(f"Created: {meta.get('created_at', 'N/A')}")

    new_desc = st.text_area(
        "Project Description",
        value=meta.get("description", ""),
        height=120,
        placeholder="e.g. End-to-end regression suite for the Pentair CPQ module",
        key="proj_settings_desc",
    )
    new_owner = st.text_input(
        "Business Unit / Owner",
        value=meta.get("owner", ""),
        placeholder="e.g. QA Engineering — Jane Doe",
        key="proj_settings_owner",
    )

    if st.button("💾 Save Changes", type="primary", key="save_proj_settings_btn"):
        _pm.update_project_meta(project_name, description=new_desc, owner=new_owner)
        st.session_state.pop("_show_project_settings", None)
        st.toast(f"Project settings updated for **{project_name}**.")
        st.rerun()


# ---------------------------------------------------------------------------
# Active Workspace header (project selector + credentials in main area)
# ---------------------------------------------------------------------------

def _render_workspace_header() -> tuple[str, str, str, str]:
    """Top-of-page project + environment + persona selector and credentials panel.

    Returns ``(active_proj, sandbox_url, username, password)``.
    """
    col_proj, col_creds = st.columns([1, 2], gap="large")

    _ENV_SUGGESTIONS = ["Dev", "QA", "UAT", "Prod", "Custom..."]

    # ── Column 1: Project / Environment / Persona selectors ───────────
    with col_proj:
        # DEMO: Git Sync button hidden for clean demo
        # _lbl_col, _sync_col = st.columns([3, 1])
        # _lbl_col.markdown("**🗂️ Project**")
        # if _sync_col.button("🔄 Sync", key="sync_workspace_btn", help="Pull latest from remote"):
        #     try:
        #         from app_git import sync_local_workspace
        #
        #         _repo_root = Path(__file__).resolve().parent
        #         if sync_local_workspace(_repo_root):
        #             st.success("Workspace synced with remote! ☁️")
        #             st.rerun()
        #         else:
        #             st.warning("Sync failed — check that a Git remote is configured and accessible.")
        #     except Exception as _exc:  # noqa: BLE001
        #         st.warning(f"Could not sync workspace: {_exc}")
        st.markdown("**🗂️ Project**")
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

            # ── Create New Project (reactive, no st.form) ─────────────
            if proj_sel == "+ Create New Project":
                with st.container(border=True):
                    st.caption("New Project Setup")
                    new_name = st.text_input(
                        "Project Name", placeholder="e.g. Regression_Suite_Q3",
                        key="new_proj_name_input",
                    )
                    new_desc = st.text_input(
                        "Description (optional)", key="new_proj_desc_input",
                    )
                    init_env_sel = st.selectbox(
                        "Initial Environment", _ENV_SUGGESTIONS,
                        key="new_proj_env_selectbox",
                    )
                    if init_env_sel == "Custom...":
                        init_env_custom = st.text_input(
                            "Custom environment name", key="new_proj_env_custom_input",
                        )
                    else:
                        init_env_custom = ""
                    resolved_env = init_env_custom.strip() if init_env_sel == "Custom..." else init_env_sel

                    np_url = st.text_input(
                        "Sandbox URL", placeholder="https://yourorg--sbx.sandbox.my.salesforce.com/",
                        key="new_proj_url_input",
                    )
                    np_c1, np_c2 = st.columns(2)
                    with np_c1:
                        np_user = st.text_input("Username", key="new_proj_user_input")
                    with np_c2:
                        np_pw = st.text_input("Password", type="password", key="new_proj_pw_input")

                    if st.button("✅ Create Project & Environment", type="primary", key="create_proj_env_btn"):
                        if not new_name.strip():
                            st.error("Project name cannot be empty.")
                        elif init_env_sel == "Custom..." and not resolved_env:
                            st.error("Please enter a custom environment name.")
                        else:
                            try:
                                # Folder name is slugified (e.g. "My Project" -> My_Project); credentials must use that id.
                                proj_dir = _pm.create_project(new_name, new_desc)
                                project_id = proj_dir.name
                                _pm.write_project_credentials(
                                    project_id,
                                    np_url.strip(),
                                    np_user.strip(),
                                    np_pw,
                                    environment=resolved_env,
                                    persona="System Admin",
                                )
                                st.session_state["active_project"] = project_id
                                st.session_state["active_environment"] = resolved_env
                                st.session_state["active_persona"] = "System Admin"
                                st.session_state.pop("_credentials_bound_key", None)
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))

            elif proj_sel == "(none — ad-hoc)":
                st.session_state["active_project"] = ""
            else:
                if st.session_state.get("active_project") != proj_sel:
                    st.session_state["active_project"] = proj_sel
                    st.session_state.pop("_credentials_bound_key", None)
                    st.session_state["edit_creds_mode"] = False
                    env_list_init = _pm.list_environments(proj_sel)
                    if env_list_init:
                        st.session_state["active_environment"] = env_list_init[0]
                    st.session_state["active_persona"] = "System Admin"

            # ── Environment + Persona selectors (active project) ──────
            _active = st.session_state.get("active_project") or ""
            if _active:
                env_list = _pm.list_environments(_active) or ["Dev"]
                env_opts = env_list + ["+ Add Environment"]
                cur_env = st.session_state.get("active_environment", "Dev")
                env_idx = env_opts.index(cur_env) if cur_env in env_opts else 0

                env_sel = st.selectbox("Environment", env_opts, index=env_idx, key="env_selectbox")

                if env_sel == "+ Add Environment":
                    with st.container(border=True):
                        st.caption("Add Environment")
                        add_env_sel = st.selectbox(
                            "Environment Name", _ENV_SUGGESTIONS,
                            key="add_env_suggestion_selectbox",
                        )
                        if add_env_sel == "Custom...":
                            add_env_custom = st.text_input(
                                "Custom environment name", key="add_env_custom_input",
                            )
                        else:
                            add_env_custom = ""
                        resolved_add_env = add_env_custom.strip() if add_env_sel == "Custom..." else add_env_sel

                        ae_url = st.text_input(
                            "Sandbox URL", placeholder="https://yourorg--sbx.sandbox.my.salesforce.com/",
                            key="add_env_url_input",
                        )
                        ae_c1, ae_c2 = st.columns(2)
                        with ae_c1:
                            ae_user = st.text_input("Username", key="add_env_user_input")
                        with ae_c2:
                            ae_pw = st.text_input("Password", type="password", key="add_env_pw_input")

                        _btn_save, _btn_cancel = st.columns(2)
                        with _btn_save:
                            if st.button("💾 Save New Environment", type="primary", key="save_new_env_btn"):
                                if not resolved_add_env:
                                    st.error("Please enter an environment name.")
                                elif resolved_add_env in env_list:
                                    st.error(f"Environment **{resolved_add_env}** already exists.")
                                else:
                                    _pm.write_project_credentials(
                                        _active,
                                        ae_url.strip(),
                                        ae_user.strip(),
                                        ae_pw,
                                        environment=resolved_add_env,
                                        persona="System Admin",
                                    )
                                    st.session_state["active_environment"] = resolved_add_env
                                    st.session_state["active_persona"] = "System Admin"
                                    st.session_state.pop("_credentials_bound_key", None)
                                    st.session_state.pop("env_selectbox", None)
                                    st.toast(f"Environment **{resolved_add_env}** created!")
                                    st.rerun()
                        with _btn_cancel:
                            if st.button("❌ Cancel", key="cancel_add_env_btn"):
                                env_fallback = env_list[0] if env_list else "Dev"
                                st.session_state["active_environment"] = env_fallback
                                st.session_state.pop("env_selectbox", None)
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

        if readonly:
            st.markdown(
                "<style>"
                "div[data-testid='stTextInput'] input:disabled {"
                "  color: #1a1a2e !important;"
                "  -webkit-text-fill-color: #1a1a2e !important;"
                "  opacity: 1 !important;"
                "}"
                "</style>",
                unsafe_allow_html=True,
            )

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

        # DEMO: Slack webhook hidden for clean demo
        # DEMO: Jira/Zephyr integration hidden for clean demo

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

    # ── Handle incoming repair request from "Fix with AI" ─────────────
    _repair = st.session_state.pop("_repair_request", None)
    if _repair:
        robot_code = _repair.get("robot_code", "")
        error_msg = _repair.get("error", "")
        test_name = _repair.get("test_name", "Test")

        if robot_code:
            st.session_state[PENDING_ROBOT_EDITOR_KEY] = robot_code
            st.session_state[PENDING_GEN_CTX_KEY] = {
                "project_name": active_proj,
                "test_name": "",
                "overwrite": True,
                "csv_bytes": None,
                "user_story_id": "",
            }

        repair_prompt = (
            f"The following test **{test_name}** failed with this error:\n\n"
            f"```\n{error_msg}\n```\n\n"
            "Please analyze the Robot Framework code and provide a self-healing fix. "
            "Focus on fixing broken locators, missing waits, or incorrect keyword usage."
        )
        st.session_state["_pending_prompt"] = repair_prompt

        st.success(f"🔧 **Repair mode active** — loaded failing test **{test_name}** into the editor.")

    # SIMPLIFIED: Load existing test selector hidden for clean flow
    # if active_proj and _HAS_WORKSPACE and _pm is not None:
    #     saved = _pm.list_project_tests(active_proj)
    #     test_names = [t["name"] for t in saved]
    #     load_opts = ["(Create New Test)"] + test_names
    #     cur_load = st.session_state.get("load_existing_test_selector", "(Create New Test)")
    #     load_idx = load_opts.index(cur_load) if cur_load in load_opts else 0
    #
    #     load_sel = st.selectbox(
    #         "📂 Load Existing Test to Edit",
    #         load_opts,
    #         index=load_idx,
    #         key="load_existing_test_selector",
    #         help="Select a saved test to load it into the editor for editing, debugging, or committing changes.",
    #     )
    #
    #     if load_sel != "(Create New Test)":
    #         load_test_into_editor(active_proj, load_sel)
    #         test_target_name = load_sel
    #     else:
    #         if st.session_state.get("_loaded_test_name"):
    #             clear_pending_generation()
    #             st.session_state.pop("_loaded_test_name", None)

    if active_proj:
        test_target_name = st.text_input(
            "Test Case Name",
            value=test_target_name,
            placeholder="e.g. B2B_Lead_Creation",
            help=f"Saved under Saved_Projects/{active_proj}/Tests/. Leave blank for ad-hoc runs.",
            key="test_target_name_input",
        )

    # DEMO: User Story / Git integration hidden for clean demo
    # user_story_id = st.text_input(
    #     "🎫 User Story / Ticket ID (Optional)",
    #     placeholder="e.g., US-1234",
    #     help="Links the generated test to a requirement. "
    #     "The tag is injected into the .robot [Tags] section and the file is committed to a feature branch.",
    #     key="user_story_id_input",
    # )
    user_story_id = ""

    # ── Quick-action: Smoke & Regression buttons ─────────────────────
    _smoke_col, _regr_col = st.columns(2)
    with _smoke_col:
        if st.button("🔥 Run Smoke Test", key="quick_smoke_btn", use_container_width=True):
            st.session_state["_pending_prompt"] = (
                "Run a smoke test for Leads, Accounts, Contacts, and Opportunities"
            )
            st.rerun()
    with _regr_col:
        if st.button("🧪 Run Regression Test", key="quick_regression_btn", use_container_width=True):
            st.session_state["_pending_prompt"] = (
                "Run a regression test for Leads, Accounts, Contacts, and Opportunities"
            )
            st.rerun()

    if st.session_state.get("_pending_prompt"):
        st.session_state["main_prompt"] = st.session_state.pop("_pending_prompt")
    if "main_prompt" not in st.session_state:
        st.session_state["main_prompt"] = ""

    prompt = st.text_area(
        "Describe your test in plain English",
        height=160,
        placeholder='e.g. "Verify I can create an Account named Acme Corp and then delete it"',
        help="Natural-language description of what the test should do.",
        key="main_prompt",
    )

    def _clear_prompt() -> None:
        st.session_state["_pending_prompt"] = ""

    st.button("🧹 Clear Prompt", key="clear_prompt_btn", on_click=_clear_prompt)

    auto_gen = st.checkbox(
        "🎲 Auto-generate missing test data (AI/Faker)",
        value=True,
        help="When checked, the AI invents realistic dummy data for any required fields "
        "instead of asking you to fill in a clarification form.",
    )

    # SIMPLIFIED: CSV uploader, image uploader, and CSV preview hidden for clean flow
    # csv_col, img_col = st.columns(2)
    # with csv_col:
    #     uploaded_csv = st.file_uploader(
    #         "Upload Test Data (CSV)",
    #         type=["csv"],
    #         help="Optional. Each row is sent to the AI so it can generate FOR loops or repeated steps.",
    #         key="pm_test_data_csv",
    #     )
    # with img_col:
    #     uploaded_image = st.file_uploader(
    #         "📸 Upload UI Screenshot (Optional)",
    #         type=["png", "jpg", "jpeg"],
    #         help="Upload a screenshot of the Salesforce form/page. The AI will analyse the fields and buttons visible in the image.",
    #         key="pm_ui_screenshot",
    #     )
    # csv_llm_block = sync_csv_session_cache(uploaded_csv)
    # if csv_llm_block:
    #     with st.expander("Preview parsed CSV (sent to the AI)", expanded=False):
    #         raw_preview = csv_upload_bytes(uploaded_csv)
    #         if raw_preview:
    #             render_csv_preview_scrollable(raw_preview)
    #         else:
    #             st.markdown(
    #                 csv_llm_block
    #                 if len(csv_llm_block) <= 14000
    #                 else csv_llm_block[:14000] + "\n\n…_(truncated in UI only)_"
    #             )
    uploaded_csv = None
    uploaded_image = None
    csv_llm_block = ""

    with st.expander("💡 What can I ask for? (Available Capabilities)", expanded=False):
        render_capabilities_cheat_sheet()

    overwrite_ok = True
    if active_proj and test_target_name and _pm is not None:
        if _pm.test_exists_in_project(active_proj, test_target_name):
            st.warning(f"⚠️ '{test_target_name}' already exists in project '{active_proj}'.")
            if not st.checkbox("Yes, overwrite the existing test script"):
                overwrite_ok = False

    run_clicked = st.button(
        "🚀 Generate Script", type="primary", use_container_width=True,
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

        _is_test_plan = False
        try:
            from test_plans import detect_plan_intent
            if detect_plan_intent(final_prompt_txt):
                _is_test_plan = True
        except Exception:  # noqa: BLE001
            pass

        if not _is_test_plan and _HAS_SMOKE and _detect_smoke_fn is not None and _smoke_prompt_fn is not None:
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
            _active_gen_mode = st.session_state.get("gen_mode_radio", "Quick Generate")
            if _active_gen_mode == "MCP Stepwise":
                run_mcp_stepwise_pipeline(
                    final_prompt,
                    sandbox_url=sandbox_url,
                    username=username,
                    password=password,
                    project_name=active_proj if active_proj else None,
                    test_name=test_target_name if test_target_name else None,
                    overwrite=overwrite_ok,
                    user_story_id=user_story_id.strip() if user_story_id else "",
                )
            else:
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
                auto_retry = st.checkbox(
                    "🔁 Auto-Retry Flaky Tests",
                    value=True,
                    key="auto_retry_flaky",
                    help="Silently reruns any failed tests once and merges the results "
                    "to prevent false positives before reporting to Slack or Jira.",
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
                            auto_retry=auto_retry,
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
                    st.session_state["_pending_prompt"] = "Run full smoke test for Lead lifecycle"
                    st.rerun()
                if s2.button("Account", use_container_width=True, key="smoke_account_btn"):
                    st.session_state["_pending_prompt"] = "Run full smoke test for Account lifecycle"
                    st.rerun()
                s3, s4 = st.columns(2)
                if s3.button("Contact", use_container_width=True, key="smoke_contact_btn"):
                    st.session_state["_pending_prompt"] = "Run full smoke test for Contact lifecycle"
                    st.rerun()
                if s4.button("Opportunity", use_container_width=True, key="smoke_opp_btn"):
                    st.session_state["_pending_prompt"] = "Run full smoke test for Opportunity lifecycle"
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
                        if col_c.button("✏️ Edit", key=f"edit_{test['name']}"):
                            load_test_into_editor(active_proj, test["name"])
                            st.session_state["load_existing_test_selector"] = test["name"]
                            st.toast("Test loaded! Switch to the 🏗️ Test Architect tab to edit. ✏️")
            else:
                st.info("Select a project to see saved tests.")

    # ── Persisted last-run results ─────────────────────────────────────────
    render_persisted_run_panel()


# ---------------------------------------------------------------------------
# Tab 3 — Data Templates
# ---------------------------------------------------------------------------

def _render_data_templates_tab(active_proj: str) -> None:
    """Visual TDM template builder for the active project."""
    if not active_proj or not _HAS_WORKSPACE or _pm is None:
        st.info("Select an **Active Project** to manage data templates.")
        return

    st.markdown(
        "Define prerequisite Salesforce records to seed before a test run. "
        "When **🌱 Seed Data Template Before Run** is checked in the Release Manager, "
        "these records are created via the API and the resulting IDs are injected "
        "as Robot variables."
    )

    # ── Hydrate session state from disk (once per project) ────────────
    _tdm_bound_key = f"_tdm_bound_{active_proj}"
    if st.session_state.get("_tdm_project_key") != _tdm_bound_key:
        raw = _pm.read_data_template(active_proj)
        try:
            records = json.loads(raw)
            if not isinstance(records, list):
                records = []
        except (json.JSONDecodeError, TypeError):
            records = []
        st.session_state["tdm_records"] = records
        st.session_state["_tdm_project_key"] = _tdm_bound_key

    records: list[dict] = st.session_state.get("tdm_records", [])

    # ── Visual record cards ───────────────────────────────────────────
    indices_to_delete: list[int] = []
    for i, rec in enumerate(records):
        obj_name = rec.get("object", "")
        var_name = rec.get("var_name", "")
        fields: dict = rec.get("fields", {})
        label = f"📦 {obj_name or '(Object)'} → {var_name or '(VarName)'}"

        with st.expander(label, expanded=True):
            oc, vc = st.columns(2)
            with oc:
                new_obj = st.text_input(
                    "Salesforce Object",
                    value=obj_name,
                    placeholder="e.g. Account, Lead, Contact",
                    key=f"tdm_obj_{i}",
                )
            with vc:
                new_var = st.text_input(
                    "Robot Variable Name",
                    value=var_name,
                    placeholder="e.g. SeededAccountId",
                    key=f"tdm_var_{i}",
                )
            rec["object"] = new_obj
            rec["var_name"] = new_var

            # ── Field rows ────────────────────────────────────────────
            st.caption("Fields")
            field_keys = list(fields.keys())
            field_indices_to_delete: list[str] = []

            for fi, fk in enumerate(field_keys):
                fv = fields[fk]
                fc1, fc2, fc3 = st.columns([2, 2, 0.4])
                with fc1:
                    new_fk = st.text_input(
                        "API Name",
                        value=fk,
                        key=f"tdm_fk_{i}_{fi}",
                        label_visibility="collapsed",
                        placeholder="Field API Name",
                    )
                with fc2:
                    new_fv = st.text_input(
                        "Value",
                        value=str(fv),
                        key=f"tdm_fv_{i}_{fi}",
                        label_visibility="collapsed",
                        placeholder="Value",
                    )
                with fc3:
                    if st.button("🗑️", key=f"tdm_fdel_{i}_{fi}", help="Remove field"):
                        field_indices_to_delete.append(fk)

                if new_fk != fk:
                    del fields[fk]
                    if new_fk.strip():
                        fields[new_fk] = new_fv
                else:
                    fields[fk] = new_fv

            for dk in field_indices_to_delete:
                fields.pop(dk, None)
            rec["fields"] = fields

            bc1, bc2 = st.columns([1, 1])
            with bc1:
                if st.button("➕ Add Field", key=f"tdm_addf_{i}"):
                    placeholder_key = f"NewField{len(fields) + 1}"
                    fields[placeholder_key] = ""
                    rec["fields"] = fields
                    st.rerun()
            with bc2:
                if st.button("🗑️ Delete Record", key=f"tdm_delrec_{i}", type="secondary"):
                    indices_to_delete.append(i)

    if indices_to_delete:
        for idx in sorted(indices_to_delete, reverse=True):
            records.pop(idx)
        st.session_state["tdm_records"] = records
        st.rerun()

    # ── Global actions ────────────────────────────────────────────────
    st.divider()
    ga1, ga2 = st.columns(2)
    with ga1:
        if st.button("➕ Add New Record to Seed", use_container_width=True, key="tdm_add_rec_btn"):
            records.append({"object": "", "var_name": "", "fields": {}})
            st.session_state["tdm_records"] = records
            st.rerun()
    with ga2:
        if st.button("💾 Save Data Template", type="primary", use_container_width=True, key="save_tdm_btn"):
            clean: list[dict] = []
            for rec in records:
                obj = (rec.get("object") or "").strip()
                var = (rec.get("var_name") or "").strip()
                if not obj and not var:
                    continue
                cleaned_fields = {
                    k.strip(): v
                    for k, v in (rec.get("fields") or {}).items()
                    if k.strip()
                }
                clean.append({"object": obj, "var_name": var, "fields": cleaned_fields})
            try:
                json_str = json.dumps(clean, indent=2, ensure_ascii=False)
                _pm.write_data_template(active_proj, json_str)
                st.session_state["tdm_records"] = clean
                st.toast(f"Data template saved to **{active_proj}**.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not save template: {exc}")

    # ── Raw JSON preview for advanced users ───────────────────────────
    with st.expander("View Raw JSON"):
        preview = json.dumps(records, indent=2, ensure_ascii=False)
        st.code(preview, language="json")


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

    from ai_bridge import LLM_PROVIDERS, PROVIDER_LABELS
    _label_to_id = {v: k for k, v in PROVIDER_LABELS.items()}
    _id_to_label = PROVIDER_LABELS

    if "llm_provider_select" not in st.session_state:
        p = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
        st.session_state["llm_provider_select"] = _id_to_label.get(p, "Gemini")

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
        provider_labels = list(PROVIDER_LABELS.values())
        llm_prov_label = st.selectbox(
            "LLM Provider",
            provider_labels,
            index=provider_labels.index(st.session_state.get("llm_provider_select", "Gemini")),
            key="llm_provider_select",
            help="Select an AI provider. Gemini is the default (free tier). Set your API key in `.env` or paste below.",
        )
        selected_provider_id = _label_to_id.get(llm_prov_label, "gemini")
        os.environ["LLM_PROVIDER"] = selected_provider_id

        provider_info = LLM_PROVIDERS[selected_provider_id]
        key_env_name = provider_info[0]

        sidebar_api_key = st.text_input(
            f"{llm_prov_label} API Key (optional — overrides .env)",
            type="password",
            placeholder="Uses .env key if empty; paste here for session override",
            key="sidebar_api_key_input",
        )

        sync_sidebar_api_key(key_env_name, selected_provider_id, sidebar_api_key)

        st.divider()
        st.subheader("Generation Mode")
        gen_mode = st.radio(
            "How should tests be generated?",
            ("Quick Generate", "MCP Stepwise"),
            index=0,
            key="gen_mode_radio",
            help=(
                "**Quick Generate:** LLM generates the .robot file directly from the prompt (fast). "
                "**MCP Stepwise:** RF-MCP executes each keyword against a live browser to verify "
                "it works, then builds the .robot file from verified steps (slower but higher quality)."
            ),
        )

        if gen_mode == "MCP Stepwise":
            try:
                import mcp_bridge
                if mcp_bridge.is_server_running():
                    st.caption("RF-MCP Server: Running")
                else:
                    st.caption("RF-MCP Server: Stopped (starts on generate)")
                _c1, _c2 = st.columns(2)
                with _c1:
                    if st.button("Start Server", key="mcp_start_btn", use_container_width=True):
                        try:
                            mcp_bridge.start_mcp_server()
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))
                with _c2:
                    if st.button("Stop Server", key="mcp_stop_btn", use_container_width=True):
                        mcp_bridge.stop_mcp_server()
                        st.rerun()
            except ImportError:
                st.warning("mcp_bridge module not found.")

        # ── Salesforce DX Tools ──────────────────────────────────────────
        st.divider()
        st.subheader("Salesforce DX Tools")
        try:
            import sf_dx_bridge
            st.caption(sf_dx_bridge.get_status_summary())

            with st.expander("SOQL Query", expanded=False):
                soql_query = st.text_area(
                    "Enter SOQL",
                    placeholder="SELECT Id, Name FROM Lead ORDER BY CreatedDate DESC LIMIT 5",
                    height=80,
                    key="soql_query_input",
                )
                if st.button("Run SOQL", key="run_soql_btn", use_container_width=True):
                    if soql_query.strip():
                        with st.spinner("Running SOQL query..."):
                            try:
                                result = sf_dx_bridge.run_soql_query(soql_query.strip())
                                st.session_state["_soql_result"] = result
                            except Exception as exc:
                                st.error(f"SOQL failed: {exc}")
                    else:
                        st.warning("Enter a query first.")
                if st.session_state.get("_soql_result"):
                    st.json(st.session_state["_soql_result"])

            with st.expander("Apex Tests", expanded=False):
                apex_classes = st.text_input(
                    "Test class names (comma-separated)",
                    placeholder="MyTestClass, AnotherTestClass",
                    key="apex_test_input",
                )
                if st.button("Run Apex Tests", key="run_apex_btn", use_container_width=True):
                    if apex_classes.strip():
                        with st.spinner("Running Apex tests..."):
                            try:
                                result = sf_dx_bridge.run_apex_tests(apex_classes.strip())
                                st.session_state["_apex_result"] = result
                            except Exception as exc:
                                st.error(f"Apex tests failed: {exc}")
                    else:
                        st.warning("Enter test class names first.")
                if st.session_state.get("_apex_result"):
                    st.json(st.session_state["_apex_result"])

            with st.expander("Org Schema", expanded=False):
                schema_obj = st.selectbox(
                    "Object",
                    ["Lead", "Account", "Contact", "Opportunity", "Case"],
                    key="schema_obj_select",
                )
                if st.button("Fetch Fields", key="fetch_schema_btn", use_container_width=True):
                    with st.spinner(f"Fetching {schema_obj} fields..."):
                        try:
                            result = sf_dx_bridge.describe_object_fields(schema_obj)
                            st.session_state["_schema_result"] = result
                        except Exception as exc:
                            st.error(f"Schema fetch failed: {exc}")
                if st.session_state.get("_schema_result"):
                    st.json(st.session_state["_schema_result"])

        except ImportError:
            st.caption("sf_dx_bridge module not found.")

        # ── Locator Health Scanner (Plan Dhurandhar) ─────────────────────
        st.divider()
        st.subheader("Locator Scanner")
        try:
            import locator_validator
            if st.button("Scan Org Locators", key="scan_locators_btn", use_container_width=True,
                         help="Login to the sandbox, navigate through Lead flow, and test all locators against the live DOM."):
                st.session_state["_run_locator_scan"] = True
                st.rerun()
        except ImportError:
            st.caption("locator_validator module not found.")

    # ── Active Workspace (project + credentials) — collapsible ──────────
    with st.expander("⚙️ Workspace & Credentials", expanded=True):
        active_proj, sandbox_url, username, password = _render_workspace_header()

    # ── Locator scan results (rendered in main area) ─────────────────────
    if st.session_state.pop("_run_locator_scan", False):
        try:
            import locator_validator
            with st.status("Scanning org locators...", expanded=True) as scan_status:
                report = locator_validator.run_scan(sandbox_url, username, password)
                scan_status.update(label="Locator scan complete", state="complete")
            st.session_state["_locator_report"] = report
        except Exception as exc:
            st.error(f"Locator scan failed: {exc}")

    if st.session_state.get("_locator_report"):
        with st.expander("Locator Health Report", expanded=True):
            report = st.session_state["_locator_report"]
            passed = [r for r in report if r["status"] == "FOUND"]
            stale = [r for r in report if r["status"] == "NOT_FOUND"]
            errors = [r for r in report if r["status"] == "ERROR"]

            c1, c2, c3 = st.columns(3)
            c1.metric("Healthy", len(passed))
            c2.metric("Stale", len(stale))
            c3.metric("Skipped", len(errors))

            if stale:
                st.markdown("### Stale Locators")
                for r in stale:
                    st.markdown(f"- **`{r['name']}`** — `{r['locator'][:80]}...`")
            if passed:
                with st.expander(f"Healthy ({len(passed)})", expanded=False):
                    for r in passed:
                        st.markdown(f"- `{r['name']}`")
            if errors:
                with st.expander(f"Skipped ({len(errors)})", expanded=False):
                    for r in errors:
                        st.markdown(f"- `{r['name']}` — {r.get('error', 'needs record context')}")

    # ── Single-page Test Architect ────────────────────────────────────────
    _render_test_builder_tab(sandbox_url, username, password, headless, active_proj)


main_ui()
