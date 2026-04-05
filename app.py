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
    for k in ("sf_sandbox_url", "sf_username", "sf_password", "sf_security_token"):
        if k not in st.session_state:
            st.session_state[k] = ""


def _apply_project_credentials_to_session() -> None:
    """
    When ``active_project`` changes, load that project's ``config.json`` into the
    credential widget keys. Switching to ad-hoc does not clear typed credentials.
    """
    if not _HAS_WORKSPACE or _pm is None:
        return
    bound = st.session_state.get("_credentials_bound_project")
    current = st.session_state.get("active_project") or ""
    if bound == current:
        return
    if current:
        cfg = _pm.read_project_config(current)
        st.session_state["sf_sandbox_url"] = cfg.get("sandbox_url") or ""
        st.session_state["sf_username"] = cfg.get("username") or ""
        st.session_state["sf_password"] = cfg.get("password") or ""
        st.session_state["sf_security_token"] = cfg.get("security_token") or ""
    st.session_state["_credentials_bound_project"] = current


# ---------------------------------------------------------------------------
# Active Workspace header (project selector + credentials in main area)
# ---------------------------------------------------------------------------

def _render_workspace_header() -> tuple[str, str, str, str]:
    """Top-of-page project + credentials panel.

    Returns ``(active_proj, sandbox_url, username, password)``.
    """
    with st.container(border=True):
        col_proj, col_creds = st.columns([1, 2], gap="large")

        with col_proj:
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
            else:
                st.warning("Workspace module unavailable.")

        _apply_project_credentials_to_session()

        with col_creds:
            st.markdown("**🔐 Salesforce Credentials**")
            ca, cb = st.columns(2)
            with ca:
                st.text_input(
                    "Sandbox URL",
                    placeholder="https://yourorg--sbx.sandbox.my.salesforce.com/",
                    help="Login URL for your Salesforce sandbox.",
                    key="sf_sandbox_url",
                )
            with cb:
                st.text_input(
                    "Username",
                    placeholder="user@example.com",
                    key="sf_username",
                )
            cc, cd = st.columns(2)
            with cc:
                st.text_input(
                    "Password",
                    type="password",
                    placeholder="••••••••",
                    key="sf_password",
                )
            with cd:
                st.text_input(
                    "Security Token",
                    type="password",
                    placeholder="Optional — leave blank if IP whitelisted",
                    help="Required for API data seeding when your IP isn't in the org's trusted range.",
                    key="sf_security_token",
                )
            _active = st.session_state.get("active_project") or ""
            if _HAS_WORKSPACE and _pm is not None and _active:
                if st.button("💾 Save Credentials to Project", key="save_creds_btn"):
                    _pm.write_project_credentials(
                        _active,
                        st.session_state.get("sf_sandbox_url", ""),
                        st.session_state.get("sf_username", ""),
                        st.session_state.get("sf_password", ""),
                        st.session_state.get("sf_security_token", ""),
                    )
                    st.toast(f"Credentials saved to **{_active}**.")

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

    uploaded_csv = st.file_uploader(
        "Upload Test Data (CSV)",
        type=["csv"],
        help="Optional. Each row is sent to the AI so it can generate FOR loops or repeated steps.",
        key="pm_test_data_csv",
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

    run_clicked = st.button(
        "🚀 Generate & Run", type="primary", use_container_width=True,
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
        if not is_smoke and pm_hint["should_show_lead_pm_form"]:
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
            run_automation_pipeline(
                augmented,
                sandbox_url=sandbox_url,
                username=username,
                password=password,
                headless=headless,
                csv_bytes=csv_upload_bytes(uploaded_csv),
                project_name=active_proj if active_proj else None,
                test_name=test_target_name if test_target_name else None,
                overwrite=overwrite_ok,
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

    # ── Full-suite run ─────────────────────────────────────────────────────
    if active_proj and _pm is not None:
        r1, r2 = st.columns([3, 1])
        with r1:
            suite_clicked = st.button(
                "▶️ Run Entire Project Suite",
                type="primary",
                use_container_width=True,
                key="run_entire_project_suite_btn",
            )
        with r2:
            pabot_parallel = st.checkbox(
                "🚀 Parallel (Pabot)",
                value=False,
                key="pabot_parallel_project_suite",
                help="pabot --testlevelsplit --processes 3",
            )
        if suite_clicked:
            if not sandbox_url.strip() or not username.strip() or not password.strip():
                st.error("Please fill in credentials in the workspace header.")
            else:
                run_project_entire_suite(
                    active_proj,
                    sandbox_url,
                    username,
                    password,
                    headless,
                    use_pabot=pabot_parallel,
                )
    else:
        st.info("Select an **Active Project** in the workspace header to run a full test suite.")

    # ── Quick Smoke shortcuts ──────────────────────────────────────────────
    if _HAS_SMOKE:
        st.divider()
        st.markdown("**🔥 Quick Smoke Tests**")
        st.caption(
            "Populates the prompt in the **Test Builder** tab — switch there to review & run."
        )
        sc = st.columns([2, 1, 1, 1, 1])
        with sc[0]:
            st.text_input(
                "Salesforce App",
                placeholder="e.g. Sales",
                key="smoke_app_name",
                label_visibility="collapsed",
            )
        if sc[1].button("Lead", use_container_width=True, key="smoke_lead_btn"):
            st.session_state["main_prompt_text"] = "Run full smoke test for Lead lifecycle"
            st.rerun()
        if sc[2].button("Account", use_container_width=True, key="smoke_account_btn"):
            st.session_state["main_prompt_text"] = "Run full smoke test for Account lifecycle"
            st.rerun()
        if sc[3].button("Contact", use_container_width=True, key="smoke_contact_btn"):
            st.session_state["main_prompt_text"] = "Run full smoke test for Contact lifecycle"
            st.rerun()
        if sc[4].button("Opportunity", use_container_width=True, key="smoke_opp_btn"):
            st.session_state["main_prompt_text"] = "Run full smoke test for Opportunity lifecycle"
            st.rerun()

    # ── Project test list ──────────────────────────────────────────────────
    if active_proj and _pm is not None:
        st.divider()
        with st.expander("📂 Project Tests", expanded=True):
            saved_tests = _pm.list_project_tests(active_proj)
            if not saved_tests:
                st.info("No tests saved in this project yet.")
            else:
                for test in saved_tests:
                    col_a, col_b, col_c = st.columns([3, 1, 1])
                    col_a.write(
                        f"📄 **{test['name']}.robot** \n"
                        f"_(modified {test['modified'].strftime('%Y-%m-%d %H:%M')})_"
                    )
                    if col_b.button("▶ Re-run", key=f"run_{test['name']}"):
                        if not sandbox_url.strip() or not username.strip() or not password.strip():
                            st.error("Fill credentials in the workspace header first.")
                        else:
                            run_existing_test(test["path"], sandbox_url, username, password, headless)
                    if col_c.button("👁 View", key=f"view_{test['name']}"):
                        st.code(
                            _pm.load_test_source(active_proj, test["name"]),
                            language="robotframework",
                        )

    # ── Persisted last-run results ─────────────────────────────────────────
    render_persisted_run_panel()


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
    color: #0052CC; letter-spacing: -0.02em; }
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

    # ── Active Workspace (project + credentials) ──────────────────────────
    active_proj, sandbox_url, username, password = _render_workspace_header()

    # ── Three-tab command center ──────────────────────────────────────────
    tab_builder, tab_exec, tab_analytics = st.tabs(
        ["🏗️ Test Builder", "🚀 Suite Execution", "📊 Analytics"]
    )
    with tab_builder:
        _render_test_builder_tab(sandbox_url, username, password, headless, active_proj)
    with tab_exec:
        _render_suite_execution_tab(sandbox_url, username, password, headless, active_proj)
    with tab_analytics:
        render_project_analytics_dashboard(active_proj)


main_ui()
