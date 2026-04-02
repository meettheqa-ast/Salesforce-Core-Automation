#!/usr/bin/env python3
"""
Bridge between natural-language prompts and Robot Framework test files.

Loads system_prompt.txt + keyword_catalog.json, calls Gemini or OpenAI, extracts
.robot source from the reply, and writes Tests/Generated/temp_test.robot.

Configure via .env (never commit real keys). Default provider is Gemini:
  LLM_PROVIDER=gemini          # or openai
  GEMINI_API_KEY=...           # or GOOGLE_API_KEY (from https://aistudio.google.com/apikey)
  GEMINI_MODEL=gemini-2.0-flash
  OPENAI_API_KEY=sk-...        # if LLM_PROVIDER=openai
  OPENAI_MODEL=gpt-4o
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import TypedDict

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


ROOT = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = ROOT / "system_prompt.txt"
CATALOG_PATH = ROOT / "keyword_catalog.json"
OUTPUT_PATH = ROOT / "Tests" / "Generated" / "temp_test.robot"
DOTENV_PATH = ROOT / ".env"

load_dotenv(DOTENV_PATH)


def hydrate_llm_env() -> None:
    """
    Load .env again (e.g. file created after import) and map Streamlit secrets into os.environ.
    Does not override variables already set in the environment.
    """
    load_dotenv(DOTENV_PATH, override=False)
    try:
        import streamlit as st

        sec = getattr(st, "secrets", None)
        if sec is None:
            return
        pairs = (
            ("OPENAI_API_KEY", "OPENAI_API_KEY"),
            ("GEMINI_API_KEY", "GEMINI_API_KEY"),
            ("GOOGLE_API_KEY", "GOOGLE_API_KEY"),
            ("LLM_PROVIDER", "LLM_PROVIDER"),
            ("OPENAI_MODEL", "OPENAI_MODEL"),
            ("GEMINI_MODEL", "GEMINI_MODEL"),
        )
        for secret_key, env_key in pairs:
            if os.environ.get(env_key):
                continue
            try:
                if secret_key in sec:
                    os.environ[env_key] = str(sec[secret_key]).strip()
            except Exception:
                continue
    except (ImportError, RuntimeError, FileNotFoundError):
        pass


def llm_config_help(*, gemini: bool = False) -> str:
    """Human-readable hint for missing API keys."""
    rel_env = ".env"
    if gemini:
        key_line = 'GEMINI_API_KEY = "..." or GOOGLE_API_KEY = "..."'
        env_line = "GEMINI_API_KEY=... or GOOGLE_API_KEY=..."
    else:
        key_line = 'OPENAI_API_KEY = "sk-..."'
        env_line = "OPENAI_API_KEY=sk-..."
    return (
        f"Set credentials in one of these ways:\n"
        f"1) Project file `{rel_env}` (copy `.env.example` → `.env`): {env_line}\n"
        f"2) Streamlit secrets `.streamlit/secrets.toml`: {key_line} (see `.streamlit/secrets.toml.example`)\n"
        f"3) Sidebar **AI (LLM)** fields in the app (session only, not saved to disk)."
    )


class PromptFieldAnalysis(TypedDict):
    """Result of PM Assistant pre-flight checks on the user prompt."""

    is_lead_creation: bool
    mentions_company: bool
    mentions_last_name: bool
    missing_lead_fields: list[str]
    should_warn_placeholders: bool


def analyze_prompt_for_required_fields(user_prompt: str) -> PromptFieldAnalysis:
    """
    PM Assistant: detect Lead-creation intent and whether Company / Last Name cues exist.

    Used by the Streamlit app to warn when placeholder test data will apply.
    """
    text = user_prompt.strip()
    lower = text.lower()

    lead_signals = (
        "create a lead",
        "create lead",
        "creating a lead",
        "creating lead",
        "new lead",
        "lead named",
        "add a lead",
        "add lead",
        "make a lead",
        "make lead",
    )
    is_lead_creation = any(sig in lower for sig in lead_signals)

    has_company_word = bool(re.search(r"\bcompany\b", lower))
    has_at_org = bool(re.search(r"\bat\s+[\w'.-]+", lower))
    has_org_suffix = bool(re.search(r"\b(inc|corp|llc|ltd)\b\.?", lower))
    mentions_company = has_company_word or has_at_org or has_org_suffix

    mentions_last_name = bool(
        re.search(r"\blast name\b", lower) or re.search(r"\bsurname\b", lower)
    )

    missing: list[str] = []
    if is_lead_creation:
        if not mentions_company:
            missing.append("Company")
        if not mentions_last_name:
            missing.append("Last Name")

    result: PromptFieldAnalysis = {
        "is_lead_creation": is_lead_creation,
        "mentions_company": mentions_company,
        "mentions_last_name": mentions_last_name,
        "missing_lead_fields": missing,
        "should_warn_placeholders": bool(is_lead_creation and missing),
    }
    return result


def _load_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8")


def _load_catalog_compact() -> str:
    """JSON string for the LLM; full catalog so keyword allowlist is honored."""
    raw = _load_text(CATALOG_PATH)
    data = json.loads(raw)
    # Optional: trim huge fields if needed later
    return json.dumps(data, ensure_ascii=False, indent=2)


def extract_robot_code(response_text: str) -> str:
    """
    Pull .robot source from an LLM reply (fences, delimiter, or *** sections).
    """
    text = response_text.strip()

    for fence in ("```robot", "```Robot", "```"):
        if fence in text:
            start = text.find(fence) + len(fence)
            if start < len(text) and text[start] == "\n":
                start += 1
            end = text.find("```", start)
            if end != -1:
                return text[start:end].strip()

    if "---ROBOT---" in text:
        return text.split("---ROBOT---", 1)[1].strip()

    for marker in ("*** Settings ***", "*** Variables ***", "*** Test Cases ***"):
        idx = text.find(marker)
        if idx != -1:
            return text[idx:].strip()

    return text


def _call_openai(system_prompt: str, user_content: str) -> str:
    from openai import OpenAI

    hydrate_llm_env()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.\n" + llm_config_help())

    client = OpenAI(api_key=api_key)
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
    )
    return completion.choices[0].message.content or ""


def _call_gemini(system_prompt: str, user_content: str) -> str:
    import google.generativeai as genai

    hydrate_llm_env()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is not set.\n" + llm_config_help(gemini=True)
        )

    genai.configure(api_key=api_key)
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    model = genai.GenerativeModel(
        model_name,
        system_instruction=system_prompt,
    )
    resp = model.generate_content(
        user_content,
        generation_config={"temperature": 0.2},
    )
    try:
        out = (resp.text or "").strip()
    except ValueError:
        parts: list[str] = []
        for c in resp.candidates or []:
            for p in getattr(c.content, "parts", None) or []:
                if getattr(p, "text", None):
                    parts.append(p.text)
        out = "".join(parts).strip()
    if not out:
        raise RuntimeError("Gemini returned empty text (blocked or unsupported response).")
    return out


def generate_test_from_prompt(user_input: str) -> Path:
    """
    Send system prompt + keyword catalog + user_input to the configured LLM,
    extract .robot code, save to Tests/Generated/temp_test.robot.

    Returns path to the written file.
    """
    hydrate_llm_env()
    system_prompt = _load_text(SYSTEM_PROMPT_PATH)
    catalog_json = _load_catalog_compact()

    user_content = (
        "You are given the full keyword catalog as JSON. "
        "Follow the system instructions exactly.\n\n"
        f"## keyword_catalog.json\n\n{catalog_json}\n\n"
        f"## User request\n\n{user_input.strip()}\n"
    )

    provider = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
    if provider == "openai":
        raw = _call_openai(system_prompt, user_content)
    elif provider in ("gemini", "google"):
        raw = _call_gemini(system_prompt, user_content)
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider!r}. Use 'openai' or 'gemini'.")

    robot_source = extract_robot_code(raw)
    if not robot_source.strip():
        raise ValueError("LLM returned no usable .robot content.")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(robot_source.rstrip() + "\n", encoding="utf-8")
    return OUTPUT_PATH


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "Usage: python ai_bridge.py \"Your natural language test request here\"",
            file=sys.stderr,
        )
        return 2
    prompt = " ".join(sys.argv[1:])
    path = generate_test_from_prompt(prompt)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
