"""CSV upload parsing, LLM formatting, and scrollable preview for the Streamlit app."""

from __future__ import annotations

import csv
import html as html_module
import io
import json

import streamlit as st

from app_config import CSV_JSON_MAX_ROWS, CSV_MARKDOWN_MAX_ROWS


def parse_csv_dict_rows(
    file_bytes: bytes,
) -> tuple[list[str], list[dict[str, str]]] | str | None:
    """
    Parse CSV bytes into headers and row dicts.

    Returns ``None`` if input is empty, a markdown **error string** if headers are
    unreadable, or ``(headers, rows)`` on success (``rows`` may be empty).
    """
    if not file_bytes or not file_bytes.strip():
        return None
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return "_Could not read CSV column headers._"
    headers = [h or "" for h in reader.fieldnames]
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({h: str(row.get(h) or "").strip() for h in headers})
    return headers, rows


def format_uploaded_csv_for_llm(file_bytes: bytes) -> str:
    """
    Parse CSV bytes into Markdown table preview + JSON row list for the LLM.
    Large files: truncate displayed rows but state total data row count.
    """
    parsed = parse_csv_dict_rows(file_bytes)
    if parsed is None:
        return ""
    if isinstance(parsed, str):
        return parsed
    headers, rows = parsed
    if not rows:
        return f"_No data rows (headers only)._\n\n**Columns:** `{', '.join(headers)}`"

    def _esc_cell(val: object) -> str:
        return str(val).replace("|", "\\|").replace("\n", " ").replace("\r", "")

    md_lines = [
        "| " + " | ".join(_esc_cell(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows[:CSV_MARKDOWN_MAX_ROWS]:
        md_lines.append("| " + " | ".join(_esc_cell(row.get(h, "")) for h in headers) + " |")
    md = "\n".join(md_lines)
    if len(rows) > CSV_MARKDOWN_MAX_ROWS:
        md += f"\n\n_Showing first {CSV_MARKDOWN_MAX_ROWS} of **{len(rows)}** data rows._"

    json_rows = rows[:CSV_JSON_MAX_ROWS]
    json_note = ""
    if len(rows) > CSV_JSON_MAX_ROWS:
        json_note = (
            f"\n_JSON array truncated to first {CSV_JSON_MAX_ROWS} objects; "
            f"**total data rows: {len(rows)}** — still generate tests that cover every row._"
        )
    json_block = json.dumps(json_rows, indent=2, ensure_ascii=False)
    return (
        f"**Tabular preview (Markdown)**\n\n{md}\n\n"
        f"**Row objects (JSON, file order)**{json_note}\n```json\n{json_block}\n```\n\n"
        f"**Total data rows in file:** {len(rows)}"
    )


def render_csv_preview_scrollable(file_bytes: bytes) -> None:
    """Wide table + JSON in overflow containers so the expander shows horizontal scrollbars."""
    parsed = parse_csv_dict_rows(file_bytes)
    if parsed is None:
        st.caption("_No CSV data._")
        return
    if isinstance(parsed, str):
        st.markdown(parsed)
        return
    headers, rows = parsed
    if not headers:
        st.markdown("_Could not read CSV column headers._")
        return
    if not rows:
        st.markdown(
            f"_No data rows (headers only)._\n\n**Columns:** `{', '.join(headers)}`"
        )
        return

    max_rows = CSV_MARKDOWN_MAX_ROWS
    display_rows = rows[:max_rows]
    ths = "".join(f"<th>{html_module.escape(h)}</th>" for h in headers)
    trs: list[str] = []
    for row in display_rows:
        tds = "".join(
            f"<td>{html_module.escape(str(row.get(h, '')))}</td>" for h in headers
        )
        trs.append(f"<tr>{tds}</tr>")
    tbody = "".join(trs)
    row_note = ""
    if len(rows) > max_rows:
        row_note = (
            f"<p style='margin:0.5rem 0 0 0;font-size:0.875rem;opacity:0.9;'>"
            f"Showing first {max_rows} of <strong>{len(rows)}</strong> data rows.</p>"
        )

    st.markdown("**Tabular preview**")
    st.markdown(
        f"""
<div style="overflow-x: auto; overflow-y: auto; max-height: min(70vh, 560px); width: 100%;
 -webkit-overflow-scrolling: touch; border: 1px solid rgba(128,128,128,0.35); border-radius: 0.35rem;
 padding: 0.5rem 0.75rem; box-sizing: border-box;">
  <table style="border-collapse: collapse; width: max-content; min-width: 100%; font-size: 0.875rem;">
    <thead><tr style="background: rgba(128,128,128,0.12);">{ths}</tr></thead>
    <tbody>{tbody}</tbody>
  </table>
</div>
{row_note}
""",
        unsafe_allow_html=True,
    )

    json_rows = rows[:CSV_JSON_MAX_ROWS]
    json_note_txt = ""
    if len(rows) > CSV_JSON_MAX_ROWS:
        json_note_txt = (
            f"JSON array truncated to first {CSV_JSON_MAX_ROWS} objects; "
            f"total data rows: {len(rows)}."
        )
    json_block = json.dumps(json_rows, indent=2, ensure_ascii=False)

    st.markdown("**Row objects (JSON, file order)**")
    if json_note_txt:
        st.caption(json_note_txt)
    json_esc = html_module.escape(json_block)
    st.markdown(
        f"""<div style="overflow-x: auto; overflow-y: auto; max-height: min(45vh, 400px); width: 100%;
 -webkit-overflow-scrolling: touch; border: 1px solid rgba(128,128,128,0.35); border-radius: 0.35rem;
 padding: 0.5rem 0.75rem; box-sizing: border-box;">
  <pre style="margin:0; white-space: pre; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
 font-size: 0.8rem; line-height: 1.35;">{json_esc}</pre>
</div>""",
        unsafe_allow_html=True,
    )
    st.caption(f"**Total data rows in file:** {len(rows)}")


def sync_csv_session_cache(uploaded: object | None) -> str:
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


def csv_upload_bytes(uploaded: object | None) -> bytes | None:
    """Raw bytes for the current CSV upload (for coverage checks)."""
    if uploaded is None:
        return None
    if hasattr(uploaded, "getvalue"):
        return uploaded.getvalue()
    raw = uploaded.read()
    if hasattr(uploaded, "seek"):
        uploaded.seek(0)
    return raw
