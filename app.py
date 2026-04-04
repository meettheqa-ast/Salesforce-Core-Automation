"""
Streamlit UI: Salesforce AI Automation Architect.

Run from project root:
  streamlit run app.py
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
GENERATED_SUITE = ROOT / "Tests" / "Generated" / "temp_test.robot"
CATALOG_JSON_PATH = ROOT / "keyword_catalog.json"

CLARIFY_SESSION_KEY = "clarify_context"
# Limits for LLM context size (full row count still passed in summary line).
_CSV_MARKDOWN_MAX_ROWS = 50
_CSV_JSON_MAX_ROWS = 120


def format_uploaded_csv_for_llm(file_bytes: bytes) -> str:
    """
    Parse CSV bytes into Markdown table preview + JSON row list for the LLM.
    Large files: truncate displayed rows but state total data row count.
    """
    if not file_bytes or not file_bytes.strip():
        return ""
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return "_Could not read CSV column headers._"
    headers = [h or "" for h in reader.fieldnames]
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({h: str(row.get(h) or "").strip() for h in headers})
    if not rows:
        return f"_No data rows (headers only)._\n\n**Columns:** `{', '.join(headers)}`"

    def _esc_cell(val: object) -> str:
        return str(val).replace("|", "\\|").replace("\n", " ").replace("\r", "")

    md_lines = [
        "| " + " | ".join(_esc_cell(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows[:_CSV_MARKDOWN_MAX_ROWS]:
        md_lines.append("| " + " | ".join(_esc_cell(row.get(h, "")) for h in headers) + " |")
    md = "\n".join(md_lines)
    if len(rows) > _CSV_MARKDOWN_MAX_ROWS:
        md += f"\n\n_Showing first {_CSV_MARKDOWN_MAX_ROWS} of **{len(rows)}** data rows._"

    json_rows = rows[:_CSV_JSON_MAX_ROWS]
    json_note = ""
    if len(rows) > _CSV_JSON_MAX_ROWS:
        json_note = (
            f"\n_JSON array truncated to first {_CSV_JSON_MAX_ROWS} objects; "
            f"**total data rows: {len(rows)}** — still generate tests that cover every row._"
        )
    json_block = json.dumps(json_rows, indent=2, ensure_ascii=False)
    return (
        f"**Tabular preview (Markdown)**\n\n{md}\n\n"
        f"**Row objects (JSON, file order)**{json_note}\n```json\n{json_block}\n```\n\n"
        f"**Total data rows in file:** {len(rows)}"
    )


def _sync_csv_session_cache(uploaded: object | None) -> str:
    """Cache formatted CSV text in session_state; return LLM block or empty."""
    if uploaded is None:
        st.session_state.pop("csv_llm_block", None)
        st.session_state.pop("csv_upload_sig", None)
        return ""
    if hasattr(uploaded, "getvalue"):
        raw: bytes = uploaded.getvalue()
    else:
        raw = uploaded.read()
        if hasattr(uploaded, "seek"):
            uploaded.seek(0)
    sig = (getattr(uploaded, "name", ""), len(raw))
    if st.session_state.get("csv_upload_sig") != sig:
        st.session_state["csv_upload_sig"] = sig
        st.session_state["csv_llm_block"] = format_uploaded_csv_for_llm(raw)
    return str(st.session_state.get("csv_llm_block") or "")


def _csv_upload_bytes(uploaded: object | None) -> bytes | None:
    """Raw bytes for the current CSV upload (for coverage checks). Same file as _sync_csv_session_cache."""
    if uploaded is None:
        return None
    if hasattr(uploaded, "getvalue"):
        return uploaded.getvalue()
    raw = uploaded.read()
    if hasattr(uploaded, "seek"):
        uploaded.seek(0)
    return raw


def _open_local_path(path: Path) -> None:
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


def _render_report_log_actions(
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
                _open_local_path(report_path)
        else:
            st.caption("report.html not found.")
    with col_b:
        if log_path and log_path.is_file():
            if st.button(
                "Open Log",
                key=f"{key_prefix}_log",
                help="Opens log.html in your default browser.",
            ):
                _open_local_path(log_path)
        else:
            st.caption("log.html not found.")
    with col_c:
        st.caption(f"Output folder: `{out_dir_rel}`")
        if passed is True:
            st.success("Last run: Passed")
        elif passed is False:
            st.error("Last run: Failed")


# --- Framework maintenance: keep keyword_catalog.json aligned with PO/Common ---
def rebuild_keyword_catalog() -> None:
    """Regenerate keyword_catalog.json from Resources/PO and Resources/Common."""
    from generate_keyword_mapping import main as regenerate_catalog

    regenerate_catalog()
    _load_keyword_catalog_payload.clear()


def _normalize_source_path(source_file: str) -> str:
    return source_file.replace("\\", "/")


def _capability_group_for_source(source_file: str) -> str:
    """Map catalog source_file to a PM-friendly capability section title."""
    p = _normalize_source_path(source_file)
    exact: dict[str, str] = {
        "Resources/Common/GlobalKeywords.robot": "Common Actions",
        "Resources/PO/Platform/SalesPO.robot": "Sales Features",
        "Resources/PO/Platform/WorkOrdersPO.robot": "Work Orders",
        "Resources/Common/LucyChatBot/LucyChatBotCommon.robot": "Lucy Chatbot — Shared",
        "Resources/Common/B2B/B2BCommon.robot": "B2B — Shared",
        "Resources/Common/OmsChatBot/OmsChatBotCommon.robot": "OMS Chatbot — Shared",
        "Resources/Common/Platform/PlatformCommon.robot": "Platform — Shared",
        "Resources/PO/B2B/B2BPageName1PO.robot": "B2B Features",
        "Resources/PO/OmsChatBot/OmsBotPageName1PO.robot": "OMS Chatbot Features",
    }
    if p in exact:
        return exact[p]
    if "/PO/LucyChatBot/" in p:
        stem = Path(p).stem
        if stem.endswith("PO") and len(stem) > 2:
            stem = stem[:-2]
        return f"Lucy Chatbot — {stem}"
    if p.startswith("Resources/PO/Platform/"):
        return f"Platform PO — {Path(p).stem}"
    if p.startswith("Resources/PO/"):
        return f"Other Page Objects — {Path(p).stem}"
    if p.startswith("Resources/Common/"):
        return f"Other Common — {Path(p).stem}"
    return "Other"


def _format_arguments_line(arguments: list[str]) -> str:
    if not arguments:
        return "*No keyword arguments — data usually comes from test variables or prior steps.*"
    joined = ", ".join(f"`{a}`" for a in arguments)
    return f"**Arguments:** {joined}"


@st.cache_data(ttl=30, show_spinner=False)
def _load_keyword_catalog_payload() -> dict:
    """Cached parse of keyword_catalog.json (short TTL picks up regenerations quickly)."""
    if not CATALOG_JSON_PATH.is_file():
        return {"keywords": [], "_missing_file": True}
    try:
        return json.loads(CATALOG_JSON_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"keywords": [], "_parse_error": True}


def render_capabilities_cheat_sheet() -> None:
    """Expandable reference: keywords grouped by source area, with argument hints."""
    payload = _load_keyword_catalog_payload()
    if payload.get("_missing_file"):
        st.warning(
            f"`keyword_catalog.json` was not found at `{CATALOG_JSON_PATH.relative_to(ROOT)}`. "
            "Run the app once or execute `python generate_keyword_mapping.py`."
        )
        return
    if payload.get("_parse_error"):
        st.error("Could not parse `keyword_catalog.json`. Regenerate the catalog.")
        return

    keywords = payload.get("keywords") or []
    if not keywords:
        st.info("No keywords found in the catalog yet.")
        return

    st.caption(
        f"{len(keywords)} keywords from your Page Objects and common libraries. "
        "Mention these flows in natural language; the AI maps them to keywords."
    )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in keywords:
        src = entry.get("source_file") or "unknown"
        grouped[_capability_group_for_source(src)].append(entry)

    group_names = sorted(grouped.keys(), key=str.casefold)
    for idx, group_name in enumerate(group_names):
        entries = grouped[group_name]
        entries.sort(key=lambda e: (e.get("keyword_name") or "").casefold())
        blocks: list[str] = [f"##### {group_name}"]
        for kw in entries:
            name = kw.get("keyword_name") or "(unnamed)"
            args_line = _format_arguments_line(list(kw.get("arguments") or []))
            summary = kw.get("natural_language_summary") or kw.get("documentation")
            short = ""
            if summary:
                short = summary.strip()
                if len(short) > 140:
                    short = short[:137].rstrip() + "…"
            block = f"**{name}**  \n{args_line}"
            if short:
                block += f"  \n*{short}*"
            blocks.append(block)
        st.markdown("\n\n".join(blocks))
        if idx < len(group_names) - 1:
            st.divider()


def _field_input_label(field_name: str) -> str:
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


def run_automation_pipeline(
    final_prompt: str,
    *,
    sandbox_url: str,
    username: str,
    password: str,
    headless: bool,
    csv_bytes: bytes | None = None,
) -> None:
    """Refresh catalog, generate .robot via AI, execute Robot, persist result links."""
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
        return

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

    _render_report_log_actions(
        report_path,
        log_path,
        str(out_dir.relative_to(ROOT)),
        key_prefix="inline_run",
        passed=None,
    )

    with st.expander("Full log (copy)"):
        st.code(full_log or "(empty)", language="text")


st.set_page_config(
    page_title="Salesforce AI Automation Architect",
    layout="wide",
    initial_sidebar_state="expanded",
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

    _render_report_log_actions(
        report_path if report_path and report_path.is_file() else None,
        log_path if log_path and log_path.is_file() else None,
        str(lr.get("out_dir", "")),
        key_prefix="persisted_run",
        passed=lr.get("passed"),
    )


def stream_robot_logs(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """Run robot, return (exit_code, full_log_text)."""
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


def _sync_sidebar_api_key(env_key: str, state_suffix: str, sidebar_api_key: str) -> None:
    """
    Apply optional sidebar API key to os.environ, or clear a prior sidebar-only value
    so .env / secrets can repopulate after the field is cleared (see hydrate_llm_env at end of sidebar).
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


def main_ui() -> None:
    st.title("Salesforce AI Automation Architect")
    st.caption(
        "Describe a test in plain English. The AI generates Robot Framework code, "
        "then executes it against your sandbox."
    )

    try:
        from ai_bridge import hydrate_llm_env

        hydrate_llm_env()
    except ImportError:
        pass
    if "llm_provider_radio" not in st.session_state:
        p = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
        st.session_state["llm_provider_radio"] = "OpenAI" if p == "openai" else "Gemini"

    with st.sidebar:
        st.header("Salesforce credentials")
        sandbox_url = st.text_input(
            "Sandbox URL",
            placeholder="https://yourorg--sandbox.sandbox.my.salesforce.com/",
            help="Login URL for your Salesforce sandbox.",
        )
        username = st.text_input(
            "Username",
            placeholder="user@example.com",
        )
        password = st.text_input(
            "Password",
            type="password",
            placeholder="••••••••",
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

        _sync_sidebar_api_key(
            "GEMINI_API_KEY",
            "gemini",
            gemini_sidebar_key if llm_prov == "Gemini" else "",
        )
        _sync_sidebar_api_key(
            "OPENAI_API_KEY",
            "openai",
            openai_sidebar_key if llm_prov == "OpenAI" else "",
        )
        try:
            from ai_bridge import hydrate_llm_env

            hydrate_llm_env()
        except ImportError:
            pass


    prompt = st.text_area(
        "User prompt",
        height=160,
        placeholder='e.g. Verify I can create an Account named "Acme Corp"',
        help="Natural language description of what the test should do.",
    )

    uploaded_csv = st.file_uploader(
        "Upload Test Data (CSV)",
        type=["csv"],
        help="Optional. Each row is formatted and sent to the AI so it can generate FOR loops or repeated create steps.",
        key="pm_test_data_csv",
    )
    csv_llm_block = _sync_csv_session_cache(uploaded_csv)
    if csv_llm_block:
        with st.expander("Preview parsed CSV (sent to the AI)", expanded=False):
            preview = csv_llm_block if len(csv_llm_block) <= 14000 else csv_llm_block[:14000] + "\n\n…_(truncated in UI only)_"
            st.markdown(preview)

    with st.expander("💡 What can I ask for? (Available Capabilities)", expanded=False):
        render_capabilities_cheat_sheet()

    run_clicked = st.button("🚀 Run Automation", type="primary")

    render_persisted_run_panel()

    # --- Initial Run: either queue clarification or run immediately ---
    if run_clicked:
        try:
            from ai_bridge import analyze_prompt_for_required_fields
        except ImportError as exc:
            st.error(f"Could not import ai_bridge: {exc}")
            return
        if not sandbox_url.strip() or not username.strip() or not password.strip():
            st.error("Please fill in Sandbox URL, Username, and Password in the sidebar.")
            return
        if not prompt.strip():
            st.error("Please enter a user prompt.")
            return

        pm_hint = analyze_prompt_for_required_fields(
            prompt.strip(),
            csv_bytes=_csv_upload_bytes(uploaded_csv),
        )
        if pm_hint["should_show_lead_pm_form"]:
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

            final_prompt = append_csv_data_to_prompt(prompt.strip(), csv_llm_block)
            run_automation_pipeline(
                final_prompt,
                sandbox_url=sandbox_url,
                username=username,
                password=password,
                headless=headless,
                csv_bytes=_csv_upload_bytes(uploaded_csv),
            )

    # --- Interactive clarification (same run after state set, or later reruns) ---
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
                    _field_input_label(field),
                    key=f"clarify_input_{field.replace(' ', '_')}",
                )
            for field in clarify_ctx.get("optional_picklists", []):
                field_values[field] = st.text_input(
                    _field_input_label(field),
                    key=f"clarify_picklist_{field.replace(' ', '_')}",
                    help="Leave blank: random visible option in that dropdown. Or type the exact option label.",
                )
            submit_clarify = st.form_submit_button("Submit & Run Automation")

        if submit_clarify:
            if not sandbox_url.strip() or not username.strip() or not password.strip():
                st.error("Please fill in Sandbox URL, Username, and Password in the sidebar.")
                return
            missing = clarify_ctx["missing_fields"]
            optional_pl = clarify_ctx.get("optional_picklists", [])
            empty = [f for f in missing if not str(field_values.get(f, "")).strip()]
            if empty:
                st.error(
                    "Please provide all required values: "
                    + ", ".join(_field_input_label(f) for f in empty)
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
                csv_bytes=_csv_upload_bytes(uploaded_csv),
            )


main_ui()
