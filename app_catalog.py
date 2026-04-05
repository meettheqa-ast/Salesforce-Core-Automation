"""Keyword catalog rebuild and capabilities cheat sheet for the Streamlit app."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import streamlit as st

from app_config import CATALOG_JSON_PATH, ROOT


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
