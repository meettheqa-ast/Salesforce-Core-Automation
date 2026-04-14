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
    page_title="TIP — Test Intelligence Platform",
    page_icon="assets/tip_logo.png",
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

    import streamlit_antd_components as _sac_tb
    user_story_id = ""

    # ── STEP 2: DEFINE TEST ─────────────────────────────────────────
    _section_header("Define Your Test", "Describe what you want to test")

    # Pending prompt + name handling (must run before widgets render)
    if st.session_state.get("_pending_prompt"):
        st.session_state["main_prompt"] = st.session_state.pop("_pending_prompt")
    if "main_prompt" not in st.session_state:
        st.session_state["main_prompt"] = ""

    _suggested_name = st.session_state.pop("_suggested_test_name", "")

    if active_proj:
        default_name = _suggested_name if _suggested_name else test_target_name
        test_target_name = st.text_input(
            "Test Case Name",
            value=default_name,
            placeholder="Auto-filled from description, or type your own",
            help="Give your test a short, descriptive name. Auto-suggested when you use a template.",
            key="test_target_name_input",
        )

    st.caption("Start with a template:")
    _ex1, _ex2, _ex3, _ex4 = st.columns(4)
    _examples = {
        "ex_lead": ("Create a Lead", "Create_Lead_Verify", "Create a new Lead with auto-generated data and verify it was created"),
        "ex_account": ("Account CRUD", "Account_Create_Delete", "Create an Account named Acme Corp, verify it exists, then delete it"),
        "ex_contact": ("Verify Contact", "Contact_Create_Verify", "Create a Contact and verify First Name, Last Name, and Email on the record page"),
        "ex_opp": ("Update Opportunity", "Opportunity_Stage_Update", "Create an Opportunity, then update its Stage to Closed Won and verify"),
    }
    for col, (key, (label, name, prompt_text)) in zip([_ex1, _ex2, _ex3, _ex4], _examples.items()):
        with col:
            if st.button(label, key=key, use_container_width=True):
                st.session_state["_pending_prompt"] = prompt_text
                st.session_state["_suggested_test_name"] = name
                st.rerun()

    # Main prompt
    prompt_col, clear_col = st.columns([12, 1])
    with prompt_col:
        prompt = st.text_area(
            "Describe your test",
            height=120,
            placeholder='Describe what the test should do, e.g. "Create a Lead named John at Acme Corp and verify it"',
            key="main_prompt",
            label_visibility="collapsed",
        )
    with clear_col:
        st.markdown("<br>", unsafe_allow_html=True)
        def _clear_prompt() -> None:
            st.session_state["_pending_prompt"] = ""
        st.button("X", key="clear_prompt_btn", on_click=_clear_prompt, help="Clear prompt")

    # ── STEP 3: CONFIGURE ──────────────────────────────────────────
    _section_header("Configure", "Choose test type and data settings")

    cfg_c1, cfg_c2 = st.columns([3, 2])
    with cfg_c1:
        _type_labels = ["Custom", "Smoke", "Regression"]
        _type_help_map = {"Custom": "Define your own steps", "Smoke": "Quick health check across core objects", "Regression": "Full workflow validation"}
        test_type_val = _sac_tb.segmented(
            items=[
                _sac_tb.SegmentedItem(label="Custom", icon="pencil-square"),
                _sac_tb.SegmentedItem(label="Smoke", icon="fire"),
                _sac_tb.SegmentedItem(label="Regression", icon="arrow-repeat"),
            ],
            index=0,
            color="violet",
            use_container_width=True,
            key="test_type_segmented",
            return_index=True,
        )
        test_type_label = _type_labels[test_type_val] if isinstance(test_type_val, int) else str(test_type_val)
        st.caption(_type_help_map.get(test_type_label, ""))

        if test_type_label == "Smoke" and not prompt.strip():
            st.session_state["_pending_prompt"] = "Run a smoke test for Leads, Accounts, Contacts, and Opportunities"
            st.session_state["_suggested_test_name"] = "Smoke_Test"
            st.rerun()
        elif test_type_label == "Regression" and not prompt.strip():
            st.session_state["_pending_prompt"] = "Run a regression test for Leads, Accounts, Contacts, and Opportunities"
            st.session_state["_suggested_test_name"] = "Regression_Test"
            st.rerun()

    with cfg_c2:
        auto_gen = st.toggle(
            "Auto-generate test data",
            value=True,
            key="auto_gen_toggle",
        )
        st.caption("Fills required fields with realistic data (AI + Faker)" if auto_gen else "You'll be asked to provide field values manually")

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

    with st.expander("What can I ask for? (Available Capabilities)", expanded=False):
        render_capabilities_cheat_sheet()

    # ── STEP 4: GENERATE ───────────────────────────────────────────
    _section_header("Generate", "Create and run your test script")

    overwrite_ok = True
    if active_proj and test_target_name and _pm is not None:
        if _pm.test_exists_in_project(active_proj, test_target_name):
            st.warning(f"'{test_target_name}' already exists in project '{active_proj}'.")
            if not st.checkbox("Yes, overwrite the existing test script"):
                overwrite_ok = False

    run_clicked = st.button(
        "Generate Script", type="primary", use_container_width=True,
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
            _active_gen_mode = st.session_state.get("_tb_gen_mode", "MCP Stepwise")
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
# CSS Theme: dark sidebar + light main area + card styles
# ---------------------------------------------------------------------------

_THEME_CSS = """
<style>
/* ==========================================================================
   Modern SaaS — Dark blue sidebar + clean white main (WCAG AA)
   Inspired by Stripe/Vercel: indigo active, soft shadows, 10px radius
   ========================================================================== */

/* ── Clean white main area ── */
.stApp {
    background: #F9FAFB !important;
}

/* ── Dark sidebar ── */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1F2A44 0%, #121826 100%) !important;
    box-shadow: 0 8px 24px rgba(0,0,0,0.25);
    padding: 16px !important;
}
section[data-testid="stSidebar"] > div {
    background: transparent !important;
}
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] .stMarkdown {
    color: #A0AEC0 !important;
}
section[data-testid="stSidebar"] hr {
    border: none !important;
    height: 1px !important;
    background: rgba(255,255,255,0.08) !important;
    margin: 12px 0 !important;
}
section[data-testid="stSidebar"] button {
    background: transparent !important;
    border: none !important;
    color: #A0AEC0 !important;
    font-weight: 400 !important;
    border-radius: 10px !important;
    transition: all 0.2s ease !important;
}
section[data-testid="stSidebar"] button:hover {
    background: rgba(255,255,255,0.06) !important;
    color: #FFFFFF !important;
    transform: translateX(2px) !important;
}
section[data-testid="stSidebar"] button p {
    color: #A0AEC0 !important;
}
section[data-testid="stSidebar"] button:hover p {
    color: #FFFFFF !important;
}

/* ── Sidebar nav buttons — stripped to look like nav items ── */
section[data-testid="stSidebar"] .stButton {
    margin-bottom: 0 !important;
}
section[data-testid="stSidebar"] .stButton button {
    background: transparent !important;
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: #A0AEC0 !important;
    font-size: 14px !important;
    font-weight: 400 !important;
    text-align: center !important;
    justify-content: center !important;
    padding: 10px 14px !important;
    border-radius: 10px !important;
    transition: all 0.2s ease !important;
    cursor: pointer !important;
}
section[data-testid="stSidebar"] .stButton button:hover {
    background: rgba(255,255,255,0.06) !important;
    background-color: rgba(255,255,255,0.06) !important;
    color: #FFFFFF !important;
    transform: translateX(2px) !important;
    border: none !important;
    box-shadow: none !important;
}
section[data-testid="stSidebar"] .stButton button:active {
    background: rgba(255,255,255,0.1) !important;
    color: #FFFFFF !important;
}
section[data-testid="stSidebar"] .stButton button p {
    color: inherit !important;
    font-size: 14px !important;
}

/* ── Active nav item (rendered as HTML div) ── */
.sidebar-nav-active {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 10px 14px;
    border-radius: 10px;
    background: linear-gradient(90deg, #4F46E5, #6366F1);
    color: #FFFFFF;
    font-size: 14px;
    font-weight: 500;
    text-align: center;
    margin: 3px 0;
    box-shadow: 0 4px 12px rgba(79,70,229,0.4);
}

/* ── Branding ── */
.sidebar-brand {
    padding: 0.5rem 0.75rem 0.25rem 0.75rem;
    text-align: center;
    background: rgba(255,255,255,0.05);
    border-radius: 12px;
    margin-bottom: 0.5rem;
}
.sidebar-brand-title {
    margin: 0; font-size: 1.3rem; font-weight: 700;
    color: #FFFFFF !important;
    -webkit-text-fill-color: #FFFFFF;
    letter-spacing: -0.01em;
}
.sidebar-brand-sub {
    margin: 0.1rem 0 0 0; font-size: 0.7rem;
    color: #A0AEC0 !important; font-weight: 500;
    letter-spacing: 0.04em; text-transform: uppercase;
}

/* ── Cards ── */
.feature-card {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 12px;
    padding: 1.5rem;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    transition: all 0.2s ease;
    height: 100%;
}
.feature-card:hover {
    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
    border-color: #6366F1;
    transform: translateY(-2px);
}
.feature-card-icon { font-size: 2rem; margin-bottom: 0.5rem; }
.feature-card-title { font-size: 1rem; font-weight: 600; color: #111827; margin-bottom: 0.25rem; }
.feature-card-desc { font-size: 0.8rem; color: #6B7280; line-height: 1.4; }

/* ── Page header ── */
.page-header { margin-bottom: 1.5rem; }
.page-header h1 { font-size: 1.8rem; font-weight: 700; color: #111827; margin-bottom: 0.25rem; }
.page-header p { font-size: 0.9rem; color: #6B7280; margin: 0; }

/* ── Metric cards ── */
div[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 12px;
    padding: 1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}

/* ── Rounded inputs ── */
.stTextInput input, .stTextArea textarea {
    border-radius: 10px !important;
    border: 1.5px solid #E5E7EB !important;
    transition: border-color 0.2s ease !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: #6366F1 !important;
    box-shadow: 0 0 0 2px rgba(99,102,241,0.15) !important;
}
.stSelectbox > div > div {
    border-radius: 10px !important;
}

/* ── Expanders ── */
.streamlit-expanderHeader {
    border-radius: 10px !important;
    background: #FFFFFF !important;
    border: 1px solid #E5E7EB !important;
}

/* ── Primary buttons — indigo ── */
.stButton button[kind="primary"],
.stButton button[data-testid="stBaseButton-primary"] {
    background: linear-gradient(135deg, #4F46E5, #6366F1) !important;
    color: #FFFFFF !important;
    border: none !important;
    font-weight: 600 !important;
    border-radius: 10px !important;
    box-shadow: 0 2px 8px rgba(79,70,229,0.25) !important;
    transition: all 0.2s ease !important;
}
.stButton button[kind="primary"]:hover,
.stButton button[data-testid="stBaseButton-primary"]:hover {
    background: linear-gradient(135deg, #4338CA, #4F46E5) !important;
    box-shadow: 0 4px 16px rgba(79,70,229,0.35) !important;
    transform: translateY(-1px) !important;
}

/* ── Secondary buttons ── */
.stButton button[kind="secondary"],
.stButton button[data-testid="stBaseButton-secondary"] {
    background: #FFFFFF !important;
    color: #374151 !important;
    border: 1px solid #E5E7EB !important;
    font-weight: 500 !important;
    border-radius: 10px !important;
    transition: all 0.2s ease !important;
}
.stButton button[kind="secondary"]:hover,
.stButton button[data-testid="stBaseButton-secondary"]:hover {
    background: #F9FAFB !important;
    border-color: #6366F1 !important;
    color: #4F46E5 !important;
}

/* ── Tabs — indigo accent ── */
.stTabs [data-baseweb="tab-list"] {
    border-radius: 10px;
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
}
.stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {
    border-bottom-color: #4F46E5 !important;
    color: #4F46E5 !important;
    font-weight: 600 !important;
}
.stTabs [data-baseweb="tab-list"] button {
    color: #6B7280 !important;
    border-radius: 8px !important;
}

/* ── Focus outlines (accessibility) ── */
button:focus-visible, a:focus-visible, input:focus-visible,
textarea:focus-visible, select:focus-visible {
    outline: 2px solid #4F46E5 !important;
    outline-offset: 2px !important;
}

/* ── Smooth transitions ── */
button, input, textarea, select {
    transition: all 0.2s ease !important;
}

/* ── Section headers ── */
.section-header {
    margin-top: 1.5rem;
    margin-bottom: 0.75rem;
}
.section-header h3 {
    font-size: 1rem;
    font-weight: 600;
    color: #2C2C2C;
    margin: 0 0 0.2rem 0;
    letter-spacing: -0.01em;
}
.section-header p {
    font-size: 0.8rem;
    color: #6B7280;
    margin: 0 0 0.5rem 0;
}
.section-header hr {
    border: none;
    border-top: 1px solid #E5E5E5;
    margin: 0;
}

/* ── Layout — constrained width for readability ── */
.block-container {
    padding-top: 1.5rem !important;
    max-width: 960px !important;
    margin: 0 auto !important;
}
</style>
"""


def _section_header(title: str, subtitle: str = "") -> None:
    """Render a clean section header with title, optional subtitle, and light divider."""
    sub_html = f"<p>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f'<div class="section-header">'
        f"<h3>{title}</h3>"
        f"{sub_html}"
        f"<hr>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------

def _render_test_builder_page(
    sandbox_url: str, username: str, password: str, headless: bool, active_proj: str,
) -> None:
    """Home / Test Builder page — guided flow: Setup > Define > Configure > Generate."""
    import streamlit_antd_components as sac

    st.markdown(
        '<div class="page-header" style="margin-bottom:0.5rem">'
        '<h1 style="font-size:1.6rem;margin-bottom:0.1rem">What would you like to test today?</h1>'
        "<p>Describe your test in plain English -- no coding needed.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── STEP 1: SETUP ───────────────────────────────────────────────
    _section_header("Setup", "Configure workspace and execution settings")

    setup_c1, setup_c2, setup_c3, setup_c4 = st.columns([2, 1, 2, 1])

    with setup_c1:
        all_projs = _pm.list_projects() if _HAS_WORKSPACE and _pm is not None else []
        proj_opts = ["Ad-hoc"] + all_projs
        cur_proj = st.session_state.get("active_project", "")
        cur_idx = proj_opts.index(cur_proj) if cur_proj in proj_opts else 0

        proj_sel = st.selectbox(
            "Workspace",
            proj_opts,
            index=cur_idx,
            key="tb_project_select",
        )
        if proj_sel == "Ad-hoc":
            if st.session_state.get("active_project", "") != "":
                st.session_state["active_project"] = ""
                st.session_state.pop("_credentials_bound_key", None)
                st.rerun()
        else:
            if st.session_state.get("active_project") != proj_sel:
                st.session_state["active_project"] = proj_sel
                st.session_state.pop("_credentials_bound_key", None)
                _apply_project_credentials_to_session()
                st.rerun()

    active_proj = st.session_state.get("active_project", "")

    if active_proj and _HAS_WORKSPACE and _pm is not None:
        env_list = _pm.list_environments(active_proj) or ["Dev"]
        cur_env = st.session_state.get("active_environment", "Dev")

        with setup_c2:
            env_sel = st.selectbox(
                "Environment",
                env_list,
                index=env_list.index(cur_env) if cur_env in env_list else 0,
                key="tb_env_select",
            )
            if env_sel != cur_env:
                st.session_state["active_environment"] = env_sel
                st.session_state.pop("_credentials_bound_key", None)
                _apply_project_credentials_to_session()
                st.rerun()

        _apply_project_credentials_to_session()
        sandbox_url = st.session_state.get("sf_sandbox_url", "")
        username = st.session_state.get("sf_username", "")
        password = st.session_state.get("sf_password", "")

        with setup_c3:
            if sandbox_url:
                st.text_input("Sandbox", value=sandbox_url, disabled=True, key=f"tb_sandbox_{cur_env}")
            else:
                st.warning("No sandbox URL. Set up in **Projects**.")
        with setup_c4:
            if username:
                st.text_input("User", value=username, disabled=True, key=f"tb_user_{cur_env}")
            else:
                st.caption("Not configured")
    else:
        sandbox_url = st.session_state.get("sf_sandbox_url", "")
        username = st.session_state.get("sf_username", "")
        password = st.session_state.get("sf_password", "")
        with setup_c2:
            st.caption("")
        with setup_c3:
            if sandbox_url:
                st.caption(f"**{sandbox_url.split('//')[-1][:40]}**")
            else:
                st.info("Go to **Projects** to configure credentials.")
        with setup_c4:
            if username:
                st.caption(f"**{username}**")

    # Execution settings
    import streamlit_antd_components as sac
    es_c1, es_c2 = st.columns(2)
    with es_c1:
        st.caption("Execution Mode")
        exec_idx = sac.segmented(
            items=[
                sac.SegmentedItem(label="Watch (live browser)", icon="eye"),
                sac.SegmentedItem(label="Background (headless)", icon="lightning-charge"),
            ],
            index=0,
            color="violet",
            use_container_width=True,
            key="tb_exec_segmented",
        )
        headless = exec_idx == 1
    with es_c2:
        st.caption("Generation Mode")
        gen_idx = sac.segmented(
            items=[
                sac.SegmentedItem(label="MCP Stepwise (verified)", icon="check2-circle"),
                sac.SegmentedItem(label="Quick Generate", icon="lightning"),
            ],
            index=0,
            color="violet",
            use_container_width=True,
            key="gen_mode_radio",
        )
        st.session_state["_tb_gen_mode"] = "MCP Stepwise" if gen_idx == 0 else "Quick Generate"

    _render_test_builder_tab(sandbox_url, username, password, headless, active_proj)


def _render_projects_page(
    sandbox_url: str, username: str, password: str,
) -> None:
    """Projects page: structured sections for project, environment, persona, credentials."""
    st.markdown(
        '<div class="page-header">'
        "<h1>Projects</h1>"
        "<p>Create and manage test projects, environments, and credentials.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    if not _HAS_WORKSPACE or _pm is None:
        st.warning("Project workspace module is unavailable.")
        return

    _ENV_PRESETS = ["Dev", "QA", "UAT", "Prod"]

    # ══════════════════════════════════════════════════════════════════
    # SECTION 1: PROJECT INFO
    # ══════════════════════════════════════════════════════════════════
    _section_header("Project Info", "Select or create a project")

    all_projs = _pm.list_projects()

    if not all_projs and not st.session_state.get("_show_create_project"):
        st.markdown(
            '<div class="feature-card" style="text-align:center;padding:2rem">'
            '<div class="feature-card-icon">📂</div>'
            '<div class="feature-card-title">No projects yet</div>'
            '<div class="feature-card-desc">Create your first project to organize tests, environments, and credentials.</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button("Create Your First Project", type="primary", key="create_first_proj_btn", use_container_width=True):
            st.session_state["_show_create_project"] = True
            st.rerun()

    else:
        proj_opts = ["+ Create New Project"] + all_projs
        active_proj = st.session_state.get("active_project", "")
        cur_idx = proj_opts.index(active_proj) if active_proj in proj_opts else (1 if all_projs else 0)

        proj_sel = st.selectbox("Project", proj_opts, index=cur_idx, key="proj_page_select", label_visibility="collapsed")

        if proj_sel == "+ Create New Project" or st.session_state.get("_show_create_project"):
            st.session_state.pop("_show_create_project", None)
            with st.container(border=True):
                p_c1, p_c2 = st.columns(2)
                with p_c1:
                    new_name = st.text_input("Project Name", placeholder="e.g. Regression_Suite_Q3", key="proj_new_name")
                with p_c2:
                    new_desc = st.text_input("Description (optional)", placeholder="e.g. End-to-end tests for CPQ", key="proj_new_desc")

                init_env = st.selectbox("Initial Environment", _ENV_PRESETS + ["Custom..."], key="proj_new_env")
                if init_env == "Custom...":
                    init_env = st.text_input("Custom environment name", key="proj_new_env_custom")

                np_url = st.text_input("Sandbox URL", placeholder="https://yourorg--sbx.sandbox.my.salesforce.com/", key="proj_new_url")
                cred_c1, cred_c2 = st.columns(2)
                with cred_c1:
                    np_user = st.text_input("Username", key="proj_new_user")
                with cred_c2:
                    np_pw = st.text_input("Password", type="password", key="proj_new_pw")

                if st.button("Save Project", type="primary", key="proj_save_btn", use_container_width=True):
                    if not new_name or not new_name.strip():
                        st.error("Project name cannot be empty.")
                    elif not init_env or not init_env.strip():
                        st.error("Please select or enter an environment name.")
                    else:
                        try:
                            proj_dir = _pm.create_project(new_name, new_desc or "")
                            project_id = proj_dir.name
                            _pm.write_project_credentials(
                                project_id, np_url.strip(), np_user.strip(), np_pw,
                                environment=init_env.strip(), persona="System Admin",
                            )
                            st.session_state["active_project"] = project_id
                            st.session_state["active_environment"] = init_env.strip()
                            st.session_state["active_persona"] = "System Admin"
                            st.session_state.pop("_credentials_bound_key", None)
                            st.toast(f"Project **{new_name}** created!")
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
        else:
            if st.session_state.get("active_project") != proj_sel:
                st.session_state["active_project"] = proj_sel
                st.session_state.pop("_credentials_bound_key", None)
                st.session_state["edit_creds_mode"] = False
                env_list_init = _pm.list_environments(proj_sel)
                if env_list_init:
                    st.session_state["active_environment"] = env_list_init[0]
                st.session_state["active_persona"] = "System Admin"
                st.rerun()

    active = st.session_state.get("active_project", "")
    if not active:
        return

    # ══════════════════════════════════════════════════════════════════
    # SECTION 2: ENVIRONMENT SETUP
    # ══════════════════════════════════════════════════════════════════
    _section_header("Environment Setup", "Configure sandbox environments for this project")

    env_list = _pm.list_environments(active) or []
    env_opts = env_list + ["+ Add Environment"]
    cur_env = st.session_state.get("active_environment", env_list[0] if env_list else "Dev")
    env_idx = env_opts.index(cur_env) if cur_env in env_opts else 0

    env_sel = st.selectbox("Environment", env_opts, index=env_idx, key="proj_env_select")

    if env_sel == "+ Add Environment":
        with st.container(border=True):
            st.caption("New Environment + Persona + Credentials")

            available_presets = [e for e in _ENV_PRESETS if e not in env_list]
            env_choices = available_presets + ["Custom..."] if available_presets else ["Custom..."]

            top_c1, top_c2 = st.columns(2)
            with top_c1:
                add_env_sel = st.selectbox("Environment Name", env_choices, key="proj_add_env_sel")
                if add_env_sel == "Custom...":
                    add_env_name = st.text_input("Custom environment name", key="proj_add_env_custom")
                else:
                    add_env_name = add_env_sel
                if not available_presets and add_env_sel != "Custom...":
                    st.caption("All predefined environments exist. Use a custom name.")
            with top_c2:
                ae_persona = st.text_input(
                    "Test Persona",
                    value="System Admin",
                    placeholder="e.g. System Admin, Marketing User",
                    key="proj_add_env_persona",
                    help="The user persona for this environment. Default: System Admin.",
                )

            ae_url = st.text_input("Sandbox URL", placeholder="https://yourorg--sbx.sandbox.my.salesforce.com/", key="proj_add_env_url")
            ae_c1, ae_c2 = st.columns(2)
            with ae_c1:
                ae_user = st.text_input("Username", key="proj_add_env_user")
            with ae_c2:
                ae_pw = st.text_input("Password", type="password", key="proj_add_env_pw")

            btn_c1, btn_c2 = st.columns(2)
            with btn_c1:
                if st.button("Save Environment", type="primary", key="proj_save_env_btn", use_container_width=True):
                    if not add_env_name or not add_env_name.strip():
                        st.error("Please enter an environment name.")
                    elif add_env_name.strip() in env_list:
                        st.error(f"Environment **{add_env_name}** already exists.")
                    elif not ae_persona or not ae_persona.strip():
                        st.error("Persona name cannot be empty.")
                    else:
                        _pm.write_project_credentials(
                            active, ae_url.strip(), ae_user.strip(), ae_pw,
                            environment=add_env_name.strip(),
                            persona=ae_persona.strip(),
                        )
                        st.session_state["active_environment"] = add_env_name.strip()
                        st.session_state["active_persona"] = ae_persona.strip()
                        st.session_state.pop("_credentials_bound_key", None)
                        st.toast(f"Environment **{add_env_name}** / **{ae_persona}** added!")
                        st.rerun()
            with btn_c2:
                if st.button("Cancel", key="proj_cancel_env_btn", use_container_width=True):
                    st.session_state["active_environment"] = env_list[0] if env_list else "Dev"
                    st.rerun()
    else:
        st.session_state["active_environment"] = env_sel

    # ══════════════════════════════════════════════════════════════════
    # SECTION 3: TEST PERSONA
    # ══════════════════════════════════════════════════════════════════
    act_env = st.session_state.get("active_environment") or "Dev"
    if env_sel != "+ Add Environment":
        _section_header("Test Persona", "Select the user persona for testing")

        persona_list = _pm.list_personas(active, act_env) or ["System Admin"]
        persona_opts = persona_list + ["+ Add Persona"]
        cur_per = st.session_state.get("active_persona", "System Admin")
        per_idx = persona_opts.index(cur_per) if cur_per in persona_opts else 0

        per_sel = st.selectbox("Persona", persona_opts, index=per_idx, key="proj_persona_select")

        if per_sel == "+ Add Persona":
            per_c1, per_c2 = st.columns([3, 1])
            with per_c1:
                new_per = st.text_input("Persona name", placeholder="e.g. Marketing User", key="proj_new_persona_name")
            with per_c2:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("Create", type="primary", key="proj_create_persona_btn", use_container_width=True):
                    if new_per and new_per.strip():
                        _pm.write_project_credentials(
                            active, "", "", "",
                            environment=act_env, persona=new_per.strip(),
                        )
                        st.session_state["active_persona"] = new_per.strip()
                        st.session_state.pop("_credentials_bound_key", None)
                        st.toast(f"Persona **{new_per}** created!")
                        st.rerun()
                    else:
                        st.error("Persona name cannot be empty.")
        else:
            st.session_state["active_persona"] = per_sel

    # ══════════════════════════════════════════════════════════════════
    # SECTION 4: CREDENTIALS
    # ══════════════════════════════════════════════════════════════════
    _persona = st.session_state.get("active_persona") or "System Admin"
    if env_sel != "+ Add Environment":
        _section_header("Credentials", f"Sandbox connection for {act_env} / {_persona}")

        _apply_project_credentials_to_session()
        editing = st.session_state.get("edit_creds_mode", False)

        if not editing:
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

        cr_c1, cr_c2 = st.columns(2)
        with cr_c1:
            st.text_input(
                "Sandbox URL",
                placeholder="https://yourorg--sbx.sandbox.my.salesforce.com/",
                help="Login URL for your Salesforce sandbox.",
                key="sf_sandbox_url",
                disabled=not editing,
            )
        with cr_c2:
            st.text_input("Username", placeholder="user@example.com", key="sf_username", disabled=not editing)
        cr_c3, cr_c4 = st.columns(2)
        with cr_c3:
            st.text_input("Password", type="password", placeholder="••••••••", key="sf_password", disabled=not editing)
        with cr_c4:
            st.text_input(
                "Security Token", type="password",
                placeholder="Leave blank if IP whitelisted",
                help="Required for API data seeding when your IP isn't in the org's trusted range.",
                key="sf_security_token",
                disabled=not editing,
            )

        if not editing:
            if st.button("Edit Credentials", key="proj_edit_creds_btn"):
                st.session_state["edit_creds_mode"] = True
                st.rerun()
        else:
            save_c, cancel_c = st.columns(2)
            with save_c:
                if st.button("Save Credentials", type="primary", key="proj_save_creds_btn", use_container_width=True):
                    _pm.write_project_credentials(
                        active,
                        st.session_state.get("sf_sandbox_url", ""),
                        st.session_state.get("sf_username", ""),
                        st.session_state.get("sf_password", ""),
                        st.session_state.get("sf_security_token", ""),
                        environment=act_env,
                        persona=_persona,
                    )
                    st.session_state["edit_creds_mode"] = False
                    st.session_state.pop("_credentials_bound_key", None)
                    st.toast(f"Credentials saved for **{act_env}** / **{_persona}**.")
                    st.rerun()
            with cancel_c:
                if st.button("Cancel", key="proj_cancel_creds_btn", use_container_width=True):
                    st.session_state["edit_creds_mode"] = False
                    st.session_state.pop("_credentials_bound_key", None)
                    st.rerun()

    # Set env token
    tok = st.session_state.get("sf_security_token", "").strip()
    if tok:
        os.environ["SF_SECURITY_TOKEN"] = tok
    else:
        os.environ.pop("SF_SECURITY_TOKEN", None)

    # ══════════════════════════════════════════════════════════════════
    # SECTION 5: SAVED TESTS
    # ══════════════════════════════════════════════════════════════════
    saved = _pm.list_project_tests(active)
    if saved:
        _section_header("Saved Tests", f"{len(saved)} test scripts in this project")
        cols = st.columns(3)
        for i, t in enumerate(saved):
            with cols[i % 3]:
                st.markdown(
                    f'<div class="feature-card">'
                    f'<div class="feature-card-icon">📄</div>'
                    f'<div class="feature-card-title">{t["name"]}</div>'
                    f'<div class="feature-card-desc">.robot file</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button("Load", key=f"load_test_{t['name']}", use_container_width=True):
                    load_test_into_editor(active, t["name"])
                    st.session_state["nav_page"] = "test_builder"
                    st.rerun()
    else:
        _section_header("Saved Tests", "No tests saved yet")
        st.markdown(
            '<div class="feature-card" style="text-align:center;padding:1.5rem">'
            '<div class="feature-card-desc">No saved tests in this project yet. Generate one from the Test Builder.</div>'
            "</div>",
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════════
    # SECTION 6: ANALYTICS
    # ══════════════════════════════════════════════════════════════════
    _section_header("Analytics", "Run history and pass/fail trends")
    render_project_analytics_dashboard(active)


def _render_sfdx_page() -> None:
    """SF DX Tools page: SOQL, Apex Tests, Org Schema as cards."""
    st.markdown(
        '<div class="page-header">'
        "<h1>Salesforce DX Tools</h1>"
        "<p>Query your org, run Apex tests, and inspect object schemas directly.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    try:
        import sf_dx_bridge
        st.caption(f"Status: {sf_dx_bridge.get_status_summary()}")
    except ImportError:
        st.warning("sf_dx_bridge module not available. Ensure Node.js and Salesforce CLI are installed.")
        return

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(
            '<div class="feature-card">'
            '<div class="feature-card-icon">🔍</div>'
            '<div class="feature-card-title">SOQL Query</div>'
            '<div class="feature-card-desc">Run queries against your Salesforce org</div>'
            "</div>",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            '<div class="feature-card">'
            '<div class="feature-card-icon">🧪</div>'
            '<div class="feature-card-title">Apex Tests</div>'
            '<div class="feature-card-desc">Execute Apex unit tests in your org</div>'
            "</div>",
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            '<div class="feature-card">'
            '<div class="feature-card-icon">📋</div>'
            '<div class="feature-card-title">Org Schema</div>'
            '<div class="feature-card-desc">Inspect object fields and picklist values</div>'
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")

    tab_soql, tab_apex, tab_schema = st.tabs(["SOQL Query", "Apex Tests", "Org Schema"])

    with tab_soql:
        soql_query = st.text_area(
            "Enter SOQL",
            placeholder="SELECT Id, Name FROM Lead ORDER BY CreatedDate DESC LIMIT 5",
            height=120,
            key="soql_query_input_page",
        )
        if st.button("Run Query", key="run_soql_page_btn", type="primary", use_container_width=True):
            if soql_query.strip():
                with st.spinner("Running SOQL query..."):
                    try:
                        result = sf_dx_bridge.run_soql_query(soql_query.strip())
                        st.session_state["_soql_result_page"] = result
                    except Exception as exc:
                        st.error(f"SOQL failed: {exc}")
            else:
                st.warning("Enter a query first.")
        if st.session_state.get("_soql_result_page"):
            st.json(st.session_state["_soql_result_page"])

    with tab_apex:
        apex_classes = st.text_input(
            "Test class names (comma-separated)",
            placeholder="MyTestClass, AnotherTestClass",
            key="apex_test_input_page",
        )
        if st.button("Run Tests", key="run_apex_page_btn", type="primary", use_container_width=True):
            if apex_classes.strip():
                with st.spinner("Running Apex tests..."):
                    try:
                        result = sf_dx_bridge.run_apex_tests(apex_classes.strip())
                        st.session_state["_apex_result_page"] = result
                    except Exception as exc:
                        st.error(f"Apex tests failed: {exc}")
            else:
                st.warning("Enter test class names first.")
        if st.session_state.get("_apex_result_page"):
            st.json(st.session_state["_apex_result_page"])

    with tab_schema:
        schema_obj = st.selectbox(
            "Salesforce Object",
            ["Lead", "Account", "Contact", "Opportunity", "Case"],
            key="schema_obj_select_page",
        )
        if st.button("Fetch Fields", key="fetch_schema_page_btn", type="primary", use_container_width=True):
            with st.spinner(f"Fetching {schema_obj} fields..."):
                try:
                    result = sf_dx_bridge.describe_object_fields(schema_obj)
                    st.session_state["_schema_result_page"] = result
                except Exception as exc:
                    st.error(f"Schema fetch failed: {exc}")
        if st.session_state.get("_schema_result_page"):
            st.json(st.session_state["_schema_result_page"])


def _render_locator_page(sandbox_url: str, username: str, password: str) -> None:
    """Locator Scanner page (Plan Dhurandhar)."""
    st.markdown(
        '<div class="page-header">'
        "<h1>Locator Health Scanner</h1>"
        "<p>Test your GlobalLocators.robot selectors against the live Salesforce DOM. "
        "Identifies stale locators that need updating after Salesforce releases.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    col_info, col_action = st.columns([2, 1])
    with col_info:
        st.markdown(
            '<div class="feature-card">'
            '<div class="feature-card-icon">🔬</div>'
            '<div class="feature-card-title">Scan All Locators</div>'
            '<div class="feature-card-desc">'
            "Logs into your sandbox, navigates through the Lead flow, "
            "and tests each locator against the live DOM."
            "</div></div>",
            unsafe_allow_html=True,
        )
    with col_action:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button(
            "Start Scan", type="primary", use_container_width=True, key="scan_locators_page_btn",
        ):
            st.session_state["_run_locator_scan_page"] = True
            st.rerun()

    if st.session_state.pop("_run_locator_scan_page", False):
        try:
            import locator_validator
            with st.status("Scanning org locators...", expanded=True) as scan_status:
                report = locator_validator.run_scan(sandbox_url, username, password)
                scan_status.update(label="Locator scan complete", state="complete")
            st.session_state["_locator_report"] = report
        except Exception as exc:
            st.error(f"Locator scan failed: {exc}")

    report = st.session_state.get("_locator_report")
    if report:
        st.divider()
        passed = [r for r in report if r["status"] == "FOUND"]
        stale = [r for r in report if r["status"] == "NOT_FOUND"]
        errors = [r for r in report if r["status"] == "ERROR"]

        c1, c2, c3 = st.columns(3)
        c1.metric("Healthy", len(passed))
        c2.metric("Stale", len(stale))
        c3.metric("Skipped", len(errors))

        if stale:
            st.subheader("Stale Locators")
            for r in stale:
                st.markdown(f"- **`{r['name']}`** -- `{r['locator'][:80]}...`")
        if passed:
            with st.expander(f"Healthy ({len(passed)})", expanded=False):
                for r in passed:
                    st.markdown(f"- `{r['name']}`")
        if errors:
            with st.expander(f"Skipped ({len(errors)})", expanded=False):
                for r in errors:
                    st.markdown(f"- `{r['name']}` -- {r.get('error', 'needs record context')}")


def _render_settings_page() -> None:
    """Settings page: AI provider, generation mode, MCP controls."""
    st.markdown(
        '<div class="page-header">'
        "<h1>Settings</h1>"
        "<p>Configure your AI provider and test generation preferences.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    from ai_bridge import LLM_PROVIDERS, PROVIDER_LABELS
    _label_to_id = {v: k for k, v in PROVIDER_LABELS.items()}

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("AI Provider")
        provider_labels = list(PROVIDER_LABELS.values())
        llm_prov_label = st.selectbox(
            "LLM Provider",
            provider_labels,
            index=provider_labels.index(st.session_state.get("llm_provider_select", "Gemini")),
            key="llm_provider_select",
            help="Gemini is the default (free tier).",
        )
        selected_provider_id = _label_to_id.get(llm_prov_label, "gemini")
        os.environ["LLM_PROVIDER"] = selected_provider_id

        provider_info = LLM_PROVIDERS[selected_provider_id]
        key_env_name = provider_info[0]

        sidebar_api_key = st.text_input(
            f"{llm_prov_label} API Key (optional -- overrides .env)",
            type="password",
            placeholder="Uses .env key if empty; paste here for session override",
            key="sidebar_api_key_input",
        )
        sync_sidebar_api_key(key_env_name, selected_provider_id, sidebar_api_key)

    with col_right:
        st.subheader("MCP Server")
        try:
            import mcp_bridge
            if mcp_bridge.is_server_running():
                st.success("RF-MCP Server: Running")
            else:
                st.info("RF-MCP Server: Stopped (auto-starts on generate)")
            _c1, _c2 = st.columns(2)
            with _c1:
                if st.button("Start Server", key="mcp_start_settings", use_container_width=True):
                    try:
                        mcp_bridge.start_mcp_server()
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            with _c2:
                if st.button("Stop Server", key="mcp_stop_settings", use_container_width=True):
                    mcp_bridge.stop_mcp_server()
                    st.rerun()
        except ImportError:
            st.warning("mcp_bridge module not found.")

        st.divider()
        st.subheader("SF DX Status")
        try:
            import sf_dx_bridge
            st.info(sf_dx_bridge.get_status_summary())
        except ImportError:
            st.caption("SF DX bridge not available.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_NAV_PAGES = [
    ("test_builder",     "Test Builder"),
    ("projects",         "Projects"),
    ("---",              "---"),
    ("sfdx_tools",       "SF DX Tools"),
    ("locator_scanner",  "Locator Scanner"),
    ("---",              "---"),
    ("settings",         "Settings"),
]


def main_ui() -> None:
    st.markdown(_THEME_CSS, unsafe_allow_html=True)

    try:
        from ai_bridge import hydrate_llm_env
        hydrate_llm_env()
    except ImportError:
        pass

    _init_sf_credential_session_keys()

    from ai_bridge import LLM_PROVIDERS, PROVIDER_LABELS
    if "llm_provider_select" not in st.session_state:
        p = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
        st.session_state["llm_provider_select"] = PROVIDER_LABELS.get(p, "Gemini")
    if "smoke_app_name" not in st.session_state:
        st.session_state["smoke_app_name"] = "Sales"
    if "nav_page" not in st.session_state:
        st.session_state["nav_page"] = "test_builder"

    # ── Sidebar: logo + branding + navigation ────────────────────────
    with st.sidebar:
        st.markdown(
            '<div class="sidebar-brand">'
            '<p class="sidebar-brand-title">AI QA Portal</p>'
            '<p class="sidebar-brand-sub">Test Intelligence Platform (TIP)</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("")

        current_page = st.session_state.get("nav_page", "test_builder")

        for page_id, page_label in _NAV_PAGES:
            if page_id == "---":
                st.markdown(
                    '<div style="height:1px;background:rgba(255,255,255,0.08);margin:12px 0"></div>',
                    unsafe_allow_html=True,
                )
                continue

            is_active = current_page == page_id
            if is_active:
                st.markdown(
                    f'<div class="sidebar-nav-active">{page_label}</div>',
                    unsafe_allow_html=True,
                )
            else:
                if st.button(page_label, key=f"nav_{page_id}", use_container_width=True):
                    st.session_state["nav_page"] = page_id
                    st.rerun()

    # ── Resolve credentials from session for non-builder pages ───────
    sandbox_url = st.session_state.get("sf_sandbox_url", "")
    username = st.session_state.get("sf_username", "")
    password = st.session_state.get("sf_password", "")

    # ── Page router ───────────────────────────────────────────────────
    page = st.session_state.get("nav_page", "test_builder")

    if page == "test_builder":
        active_proj = st.session_state.get("active_project", "")
        _render_test_builder_page(sandbox_url, username, password, False, active_proj)

    elif page == "projects":
        _render_projects_page(sandbox_url, username, password)

    elif page == "sfdx_tools":
        _render_sfdx_page()

    elif page == "locator_scanner":
        _render_locator_page(sandbox_url, username, password)

    elif page == "settings":
        _render_settings_page()


main_ui()
