#!/usr/bin/env python3
"""
Bridge between natural-language prompts and Robot Framework test files.

Loads system_prompt.txt + keyword_catalog.json, calls the configured LLM, extracts
.robot source from the reply, and writes Tests/Generated/temp_test.robot.

Configure via .env (never commit real keys). Default provider is Gemini.
Supported LLM_PROVIDER values:
  gemini | openai | groq | mistral | together | openrouter | anthropic | cohere

Each provider reads <PROVIDER>_API_KEY and <PROVIDER>_MODEL from env.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

_logger = logging.getLogger(__name__)

try:
    from smoke_templates import detect_smoke_intent as _detect_smoke_intent
except ImportError:
    _detect_smoke_intent = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


ROOT = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = ROOT / "system_prompt.txt"
CATALOG_PATH = ROOT / "keyword_catalog.json"
OUTPUT_PATH = ROOT / "Tests" / "Generated" / "temp_test.robot"
UPLOADED_CSV_FILENAME = "uploaded_test_data.csv"
DOTENV_PATH = ROOT / ".env"

_LEADS_FROM_CSV_LINE = re.compile(r"^@\{\s*LEADS_FROM_CSV\s*\}\s+", re.I)
_CSV_DATA_LIST_LINE = re.compile(r"^@\{\s*CSV\s+Data\s*\}\s+", re.I)

load_dotenv(DOTENV_PATH)

# Appended to the user prompt when Streamlit passes parsed CSV text (data-driven generation).
CSV_DATA_DRIVEN_INSTRUCTION_FOOTER = (
    "Please generate a Robot Framework script that iterates through this data. "
    "You can either use a Robot Framework FOR loop to iterate over the rows, or explicitly "
    "write out the creation keyword (like SalesPO.Create A New Lead) multiple times, once "
    "for each row in the data, injecting the specific values from the CSV."
)


def append_csv_data_to_prompt(user_prompt: str, csv_formatted: str) -> str:
    """
    If csv_formatted is non-empty, append Salesforce data-driven instructions and the table/JSON
    payload for the LLM. Used by the Streamlit app after PM clarification blocks.
    """
    user_prompt = (user_prompt or "").rstrip()
    csv_formatted = (csv_formatted or "").strip()
    if not csv_formatted:
        return user_prompt
    return (
        user_prompt
        + "\n\nThe user has uploaded a CSV file with the following test data:\n\n"
        + csv_formatted
        + "\n\n"
        + CSV_DATA_DRIVEN_INSTRUCTION_FOOTER
    )


def detect_smoke_intent(prompt: str) -> dict | None:
    """
    Detect smoke-test intent in a user prompt.
    Returns {"object": "Lead"|"Account"|"Contact"|"Opportunity"} or None.
    Delegates to smoke_templates.detect_smoke_intent when available.
    """
    if _detect_smoke_intent is not None:
        return _detect_smoke_intent(prompt)
    return None


# All supported providers: id → (env key for API key, env key for model, default model)
LLM_PROVIDERS: dict[str, tuple[str, str, str]] = {
    "gemini":      ("GEMINI_API_KEY",      "GEMINI_MODEL",      "gemini-2.5-flash"),
    "openai":      ("OPENAI_API_KEY",      "OPENAI_MODEL",      "gpt-4o"),
    "groq":        ("GROQ_API_KEY",        "GROQ_MODEL",        "llama-3.3-70b-versatile"),
    "mistral":     ("MISTRAL_API_KEY",     "MISTRAL_MODEL",     "mistral-small-latest"),
    "together":    ("TOGETHER_API_KEY",    "TOGETHER_MODEL",    "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "openrouter":  ("OPENROUTER_API_KEY",  "OPENROUTER_MODEL",  "meta-llama/llama-3.3-70b-instruct"),
    "anthropic":   ("ANTHROPIC_API_KEY",   "ANTHROPIC_MODEL",   "claude-sonnet-4-20250514"),
    "cohere":      ("COHERE_API_KEY",      "COHERE_MODEL",      "command-r-plus"),
}

PROVIDER_LABELS: dict[str, str] = {
    "gemini": "Gemini",
    "openai": "OpenAI (ChatGPT)",
    "groq": "Groq",
    "mistral": "Mistral AI",
    "together": "Together AI",
    "openrouter": "OpenRouter",
    "anthropic": "Anthropic (Claude)",
    "cohere": "Cohere",
}


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
        env_keys = ["LLM_PROVIDER", "GOOGLE_API_KEY"]
        for _key_env, _model_env, _ in LLM_PROVIDERS.values():
            env_keys += [_key_env, _model_env]
        for secret_key in env_keys:
            if os.environ.get(secret_key):
                continue
            try:
                if secret_key in sec:
                    os.environ[secret_key] = str(sec[secret_key]).strip()
            except Exception:
                continue
    except (ImportError, RuntimeError, FileNotFoundError):
        pass


def llm_config_help(provider: str = "") -> str:
    """Human-readable hint for missing API keys."""
    provider = (provider or os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
    if provider == "gemini":
        env_line = "GEMINI_API_KEY=... (or GOOGLE_API_KEY=...)"
    else:
        info = LLM_PROVIDERS.get(provider)
        key_env = info[0] if info else f"{provider.upper()}_API_KEY"
        env_line = f"{key_env}=..."
    return (
        f"Set credentials for **{PROVIDER_LABELS.get(provider, provider)}** in one of these ways:\n"
        f"1) Project file `.env`: {env_line}\n"
        f"2) Streamlit secrets `.streamlit/secrets.toml`\n"
        f"3) Sidebar **AI (LLM)** paste-key field in the app (session only, not saved to disk)."
    )


class PromptFieldAnalysis(TypedDict):
    """Result of PM Assistant pre-flight checks on the user prompt."""

    is_lead_creation: bool
    mentions_company: bool
    mentions_last_name: bool
    missing_lead_fields: list[str]
    optional_picklist_fields: list[str]
    should_show_lead_pm_form: bool
    should_warn_placeholders: bool


def _norm_csv_header(h: str) -> str:
    s = (h or "").strip().lower().replace("_", " ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s


def _csv_header_is_last_name(norm: str) -> bool:
    return norm in (
        "last name",
        "lastname",
        "surname",
        "lead last name",
        "lname",
        "family name",
    )


def _csv_header_is_company(norm: str) -> bool:
    if norm == "company":
        return True
    return norm in (
        "company name",
        "account name",
        "organization",
        "organisation",
        "org",
        "organization name",
        "organisation name",
        "account",
    )


def analyze_csv_lead_column_coverage(csv_bytes: bytes | None) -> dict[str, bool]:
    """
    Detect whether uploaded CSV supplies Company / Last Name via column headers
    and at least one non-empty data row. Used to skip redundant PM form fields.
    """
    out: dict[str, bool] = {"Company": False, "Last Name": False}
    if not csv_bytes or not csv_bytes.strip():
        return out
    try:
        text = csv_bytes.decode("utf-8-sig", errors="replace")
    except Exception:
        return out
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return out
    headers = [h or "" for h in reader.fieldnames]
    company_cols = [h for h in headers if _csv_header_is_company(_norm_csv_header(h))]
    last_cols = [h for h in headers if _csv_header_is_last_name(_norm_csv_header(h))]
    if not company_cols and not last_cols:
        return out
    saw_row = False
    for row in reader:
        saw_row = True
        if company_cols and not out["Company"]:
            if any(str(row.get(c) or "").strip() for c in company_cols):
                out["Company"] = True
        if last_cols and not out["Last Name"]:
            if any(str(row.get(c) or "").strip() for c in last_cols):
                out["Last Name"] = True
        if out["Company"] and out["Last Name"]:
            break
    if not saw_row:
        return {"Company": False, "Last Name": False}
    return out


def analyze_prompt_for_required_fields(
    user_prompt: str,
    *,
    csv_bytes: bytes | None = None,
) -> PromptFieldAnalysis:
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
    optional_picklists: list[str] = []
    if is_lead_creation:
        if not mentions_company:
            missing.append("Company")
        if not mentions_last_name:
            missing.append("Last Name")
        if not re.search(r"\blead\s+status\b", lower):
            optional_picklists.append("Lead Status")
        if not re.search(r"\bsalutation\b", lower):
            optional_picklists.append("Salutation")
        if not re.search(r"\blead\s+source\b", lower):
            optional_picklists.append("Lead Source")

    csv_cov = analyze_csv_lead_column_coverage(csv_bytes)
    if csv_cov:
        missing = [f for f in missing if not csv_cov.get(f, False)]
        if csv_cov.get("Company"):
            mentions_company = True
        if csv_cov.get("Last Name"):
            mentions_last_name = True

    show_pm = bool(is_lead_creation and (missing or optional_picklists))

    result: PromptFieldAnalysis = {
        "is_lead_creation": is_lead_creation,
        "mentions_company": mentions_company,
        "mentions_last_name": mentions_last_name,
        "missing_lead_fields": missing,
        "optional_picklist_fields": optional_picklists,
        "should_show_lead_pm_form": show_pm,
        "should_warn_placeholders": show_pm,
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


CREDENTIAL_SUITE_VARS = (
    "${globalSandboxTestUrl}",
    "${sandboxUserNameInput}",
    "${sandboxPasswordInput}",
)

# List variables the LLM must not define in *** Variables *** (runtime injection loads LEADS_FROM_CSV).
_CSV_HALLUCINATED_LIST_VAR = re.compile(
    r"^@\{\s*(?:LEADS_FROM_CSV|CSV\s+Data)\s*\}\s*",
    re.IGNORECASE,
)


def strip_hallucinated_csv_variables_from_suite(robot_source: str) -> str:
    """
    Remove @{LEADS_FROM_CSV} and @{CSV Data} definitions from *** Variables *** sections,
    including ``Create List`` plus ``...`` continuation lines (stops runaway multi-thousand-line files).
    """
    lines = robot_source.splitlines()
    out: list[str] = []
    in_variables = False
    i = 0
    nl = "\n" if robot_source.endswith("\n") else ""

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("***") and stripped.replace(" ", "").lower() == "***variables***":
            in_variables = True
            out.append(line)
            i += 1
            continue

        if stripped.startswith("***") and in_variables:
            in_variables = False

        if in_variables and _CSV_HALLUCINATED_LIST_VAR.match(stripped):
            if re.search(r"=\s*Create\s+List\s*$", stripped, re.IGNORECASE):
                i += 1
                while i < len(lines):
                    if lines[i].lstrip().startswith("..."):
                        i += 1
                        continue
                    break
                continue
            i += 1
            continue

        out.append(line)
        i += 1

    return "\n".join(out) + nl


def strip_credential_variable_overrides(robot_source: str) -> str:
    """
    Remove *** Variables *** lines that redefine sandbox URL / login fields.

    The LLM often emits ${globalSandboxTestUrl}    ${null} etc.; those override
    EnvData.robot (first-wins in suite scope) and Selenium then navigates to None.
    """
    lines = robot_source.splitlines()
    out: list[str] = []
    in_variables = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("***") and stripped.replace(" ", "") == "***Variables***":
            in_variables = True
            out.append(line)
            continue
        if stripped.startswith("***") and in_variables:
            in_variables = False
        if in_variables:
            lead = line.lstrip()
            if any(lead.startswith(name) for name in CREDENTIAL_SUITE_VARS):
                continue
        out.append(line)
    return "\n".join(out) + ("\n" if robot_source.endswith("\n") else "")


_INVALID_USER_KW_NAME = re.compile(r"^\$\{[^{}]+\}\s*$")


def strip_llm_robot_garbage(robot_source: str) -> str:
    """
    Remove trailing Markdown fences and invalid user-keyword headers like `${foo}` with
    `[Arguments]    ${}` (LLM mistakes variable names for keyword names).
    """
    lines = robot_source.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s == "```" or (s.startswith("```") and not s.startswith("```robot")):
            break
        if _INVALID_USER_KW_NAME.match(s) and not line.startswith((" ", "\t")):
            i += 1
            while i < len(lines):
                inner = lines[i]
                if inner.startswith((" ", "\t")):
                    i += 1
                    continue
                if not inner.strip():
                    i += 1
                    continue
                break
            continue
        out.append(line)
        i += 1
    text = "\n".join(out)
    return text + ("\n" if robot_source.endswith("\n") else "")


def inject_csv_loader_into_robot(robot_source: str) -> str:
    """
    When the LLM emits FOR ... @{LEADS_FROM_CSV} but leaves the list undefined
    (often under *** Keywords ***), drop bogus lines, add CsvDataLibrary, Suite Setup,
    and a keyword that loads Tests/Generated/uploaded_test_data.csv.
    """
    robot_source = strip_hallucinated_csv_variables_from_suite(robot_source)
    lines = [
        ln
        for ln in robot_source.splitlines()
        if not _LEADS_FROM_CSV_LINE.match(ln.strip()) and not _CSV_DATA_LIST_LINE.match(ln.strip())
    ]

    lib_token = "CsvDataLibrary.py"
    csv_kw = "Load Uploaded Csv Into Lead List"
    has_lib = any(lib_token in ln for ln in lines)

    s_start: int | None = None
    s_end: int | None = None
    for i, ln in enumerate(lines):
        st = ln.strip()
        if re.match(r"^\*\*\*\s*Settings\s*\*\*\*", st, re.I):
            s_start = i
            continue
        if s_start is not None and s_end is None and st.startswith("***") and "settings" not in st.lower():
            s_end = i
            break
    if s_start is None:
        return "\n".join(lines) + ("\n" if robot_source.endswith("\n") else "")

    if s_end is None:
        s_end = len(lines)

    settings_body = lines[s_start + 1 : s_end]
    new_body: list[str] = []
    suite_rest: str | None = None
    for ln in settings_body:
        st = ln.strip()
        if re.match(r"^Suite Setup\s+", st, re.I):
            m = re.match(r"^Suite Setup\s+(.+)$", st, re.I)
            suite_rest = (m.group(1).strip() if m else "") or None
            continue
        new_body.append(ln)

    if suite_rest:
        rest_stripped = suite_rest.strip()
        if rest_stripped == csv_kw or rest_stripped.startswith(f"{csv_kw}    "):
            new_ss = f"Suite Setup    {suite_rest}"
        else:
            new_ss = f"Suite Setup    Run Keywords    {csv_kw}    AND    {suite_rest}"
    else:
        new_ss = f"Suite Setup    {csv_kw}"

    insert: list[str] = []
    if not has_lib:
        insert.append("Library             ../../Libraries/CsvDataLibrary.py")
    insert.append(new_ss)

    merged = lines[: s_start + 1] + insert + new_body + lines[s_end:]
    text = "\n".join(merged)

    # Do not use ``csv_kw in text`` — Suite Setup already contains that phrase.
    if re.search(rf"^{re.escape(csv_kw)}\s*$", text, re.M | re.I):
        return text + ("\n" if robot_source.endswith("\n") else "")

    row_line = (
        "    @{rows}=    Load Csv As List Of Dicts    ${CURDIR}${/}"
        + UPLOADED_CSV_FILENAME
        + "\n    Set Suite Variable    @{LEADS_FROM_CSV}    @{rows}\n"
    )
    append_kw = f"{csv_kw}\n{row_line}"

    if re.search(r"^\*\*\*\s*Keywords\s*\*\*\*", text, re.M | re.I):
        text = re.sub(
            r"(^\*\*\*\s*Keywords\s*\*\*\*\s*\n)",
            lambda m: m.group(1) + append_kw,
            text,
            count=1,
            flags=re.M | re.I,
        )
    else:
        blk = "*** Keywords ***\n" + append_kw + "\n"
        m = re.search(r"^\*\*\*\s*Test Cases\s*\*\*\*", text, re.M | re.I)
        if m:
            text = text[: m.start()] + blk + text[m.start() :]
        else:
            text = text.rstrip() + "\n\n" + blk

    return text + ("\n" if robot_source.endswith("\n") else "")


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


def _get_provider_key_and_model(provider_id: str) -> tuple[str, str]:
    """Return (api_key, model) for the given provider, raising on missing key."""
    info = LLM_PROVIDERS.get(provider_id)
    if not info:
        raise ValueError(f"Unknown provider: {provider_id}")
    key_env, model_env, default_model = info
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"{key_env} is not set.\n" + llm_config_help(provider_id)
        )
    model = os.environ.get(model_env, "").strip() or default_model
    return api_key, model


def _call_openai_compatible(
    system_prompt: str,
    user_content: str,
    *,
    api_key: str,
    model: str,
    base_url: str | None = None,
    image_bytes: bytes | None = None,
) -> str:
    """Shared caller for any OpenAI-compatible chat API (OpenAI, Groq, Together, OpenRouter, Mistral)."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)

    if image_bytes:
        import base64

        b64 = base64.b64encode(image_bytes).decode("ascii")
        user_message: dict = {
            "role": "user",
            "content": [
                {"type": "text", "text": user_content},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }
    else:
        user_message = {"role": "user", "content": user_content}

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            user_message,
        ],
        temperature=0.2,
    )
    return completion.choices[0].message.content or ""


def _call_openai(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
) -> str:
    hydrate_llm_env()
    api_key, model = _get_provider_key_and_model("openai")
    return _call_openai_compatible(
        system_prompt, user_content,
        api_key=api_key, model=model, image_bytes=image_bytes,
    )


def _call_gemini(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
) -> str:
    import google.generativeai as genai

    hydrate_llm_env()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is not set.\n" + llm_config_help("gemini")
        )

    genai.configure(api_key=api_key)
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    model = genai.GenerativeModel(
        model_name,
        system_instruction=system_prompt,
    )

    if image_bytes:
        from PIL import Image as _PILImage

        img = _PILImage.open(io.BytesIO(image_bytes))
        content_parts = [user_content, img]
    else:
        content_parts = user_content  # type: ignore[assignment]

    try:
        resp = model.generate_content(
            content_parts,
            generation_config={"temperature": 0.2},
        )
    except Exception as exc:  # noqa: BLE001 — surface 429 with actionable hint
        err = str(exc).lower()
        if "429" in str(exc) or "quota" in err or "resource exhausted" in err:
            raise RuntimeError(
                "Gemini quota or rate limit (often free tier shows limit 0 for a given model). "
                "Set GEMINI_MODEL in `.env` to a model your key can use — try "
                "`gemini-2.5-flash`, `gemini-2.5-flash-lite`, or `gemini-1.5-flash`, then restart. "
                "See https://ai.google.dev/gemini-api/docs/rate-limits\n"
                f"Original error: {exc}"
            ) from exc
        raise
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


def _call_groq(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
) -> str:
    hydrate_llm_env()
    api_key, model = _get_provider_key_and_model("groq")
    return _call_openai_compatible(
        system_prompt, user_content,
        api_key=api_key, model=model,
        base_url="https://api.groq.com/openai/v1",
        image_bytes=image_bytes,
    )


def _call_mistral(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
) -> str:
    hydrate_llm_env()
    api_key, model = _get_provider_key_and_model("mistral")
    return _call_openai_compatible(
        system_prompt, user_content,
        api_key=api_key, model=model,
        base_url="https://api.mistral.ai/v1",
        image_bytes=image_bytes,
    )


def _call_together(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
) -> str:
    hydrate_llm_env()
    api_key, model = _get_provider_key_and_model("together")
    return _call_openai_compatible(
        system_prompt, user_content,
        api_key=api_key, model=model,
        base_url="https://api.together.xyz/v1",
        image_bytes=image_bytes,
    )


def _call_openrouter(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
) -> str:
    hydrate_llm_env()
    api_key, model = _get_provider_key_and_model("openrouter")
    return _call_openai_compatible(
        system_prompt, user_content,
        api_key=api_key, model=model,
        base_url="https://openrouter.ai/api/v1",
        image_bytes=image_bytes,
    )


def _call_anthropic(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
) -> str:
    import anthropic

    hydrate_llm_env()
    api_key, model = _get_provider_key_and_model("anthropic")
    client = anthropic.Anthropic(api_key=api_key)

    content: list[dict] = [{"type": "text", "text": user_content}]
    if image_bytes:
        import base64
        b64 = base64.b64encode(image_bytes).decode("ascii")
        content.insert(0, {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })

    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
        temperature=0.2,
    )
    return msg.content[0].text or ""


def _call_cohere(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
) -> str:
    import cohere

    hydrate_llm_env()
    api_key, model = _get_provider_key_and_model("cohere")
    client = cohere.ClientV2(api_key=api_key)

    resp = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
    )
    return resp.message.content[0].text or ""


_PROVIDER_CALLERS: dict[str, callable] = {
    "gemini": _call_gemini,
    "google": _call_gemini,
    "openai": _call_openai,
    "groq": _call_groq,
    "mistral": _call_mistral,
    "together": _call_together,
    "openrouter": _call_openrouter,
    "anthropic": _call_anthropic,
    "cohere": _call_cohere,
}


def call_llm(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
    provider: str | None = None,
) -> str:
    """Route to the correct LLM backend based on LLM_PROVIDER env var or explicit override."""
    hydrate_llm_env()
    provider = (provider or os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
    caller = _PROVIDER_CALLERS.get(provider)
    if not caller:
        supported = ", ".join(sorted(LLM_PROVIDERS.keys()))
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider!r}. Supported: {supported}")
    return caller(system_prompt, user_content, image_bytes=image_bytes)


def break_prompt_into_steps(
    user_prompt: str,
    catalog_json: str | None = None,
    scenario_analysis: dict | None = None,
) -> list[dict[str, list[str] | str]]:
    """Use the current LLM to decompose a natural-language prompt into
    a list of Robot Framework keyword steps.

    Returns a list of dicts: [{"keyword": "...", "args": ["...", ...]}, ...]
    """
    hydrate_llm_env()
    if catalog_json is None:
        catalog_json = _load_catalog_compact()

    analysis_block = ""
    if scenario_analysis:
        analysis_block = (
            "\n\nRF-MCP scenario analysis result:\n"
            + json.dumps(scenario_analysis, indent=2, ensure_ascii=False)
        )

    system = (
        "You are a Robot Framework step planner. Given a user prompt and keyword catalog, "
        "break the prompt into an ordered list of Robot Framework keyword calls. "
        "Each step must use a keyword from the catalog (prefer GlobalKeywords.* and SalesPO.* prefixes). "
        "Return ONLY a JSON array where each element is an object with 'keyword' (string) and "
        "'args' (array of strings, may be empty). No markdown fences, no explanation — just the JSON array.\n\n"
        "CRITICAL FORMAT RULES:\n"
        "- 'keyword' must be ONLY the keyword name — NEVER include 'with args' or arguments in the keyword string.\n"
        "- 'args' is a separate array of argument values.\n"
        "- For SalesPO.Create A New Lead, pass ZERO args (it reads from suite variables) or three "
        "plain string args: [\"John\", \"Doe\", \"Acme Corp\"]. NEVER pass named args like "
        "\"${leadFirstName}=${EMPTY}\".\n\n"
        "EXAMPLE (correct):\n"
        '[{"keyword": "GlobalKeywords.Login To Sandbox", '
        '"args": ["${globalSandboxTestUrl}", "${sandboxUserNameInput}", "${sandboxPasswordInput}"]},\n'
        '{"keyword": "SalesPO.Open New Lead From Sales App", "args": []},\n'
        '{"keyword": "SalesPO.Create A New Lead", "args": []},\n'
        '{"keyword": "SalesPO.Verify Lead Created Successfully", "args": []}]\n\n'
        "WORKFLOW RULES:\n"
        "1. Always start with GlobalKeywords.Login To Sandbox (3 args as shown above).\n"
        "2. Use qualified keyword names (GlobalKeywords.Launch App, SalesPO.Create A New Lead, etc.).\n"
        "3. For Lead creation: SalesPO.Open New Lead From Sales App then SalesPO.Create A New Lead.\n"
        "4. End with verification keywords when appropriate.\n"
        "5. Keep variable references like ${leadFirstName} as-is in args.\n"
    )
    user_content = (
        f"## Keyword Catalog\n\n{catalog_json}\n\n"
        f"## User Request\n\n{user_prompt.strip()}"
        f"{analysis_block}\n"
    )

    raw = call_llm(system, user_content)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        steps = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            steps = json.loads(match.group())
        else:
            raise ValueError(f"LLM did not return valid JSON steps: {raw[:500]}")

    if not isinstance(steps, list):
        raise ValueError(f"Expected a JSON array of steps, got: {type(steps)}")
    return [_sanitize_step(s) for s in steps]


def _sanitize_step(step: dict) -> dict:
    """Fix common LLM mistakes in step dicts before execution."""
    kw = step.get("keyword", "")
    args = list(step.get("args", []))

    # Strip "with args" appended to keyword name
    kw = re.sub(r"\s+with\s+args?\s*$", "", kw, flags=re.IGNORECASE).strip()

    # Fix named-arg patterns like "${leadFirstName}=${EMPTY}" or "${leadFirstName}=John"
    # These are Robot named-args the LLM emits; the "=" must be the separator between
    # parameter name and value, and the param name is NOT a ${variable} — it's a plain name.
    cleaned_args = []
    for a in args:
        a = str(a)
        # Pattern: ${someVar}=${EMPTY} → drop entirely (let keyword use its default)
        if re.match(r"^\$\{[^}]+\}\s*=\s*\$\{EMPTY\}\s*$", a, re.IGNORECASE):
            continue
        # Pattern: ${someVar}=<value> → keep just <value> as a positional arg
        m = re.match(r"^\$\{[^}]+\}\s*=\s*(.+)$", a)
        if m and "=" in a:
            val = m.group(1).strip()
            # But if the whole thing is just a variable ref like ${globalSandboxTestUrl}
            # with no "=" after the closing brace, it's a normal arg — don't touch it
            if not re.match(r"^\$\{[^}]+\}$", a):
                cleaned_args.append(val)
                continue
        cleaned_args.append(a)

    return {"keyword": kw, "args": cleaned_args}


_FORBIDDEN_SLEEP = re.compile(r"^\s+Sleep\s", re.MULTILINE)
_FORBIDDEN_RAW_KW = re.compile(
    r"^\s+(Click Element|Input Text|Wait Until Element Is Visible|Click Button|Input Password)\s",
    re.MULTILINE,
)
_HAS_BEGIN_WEB_TEST = re.compile(r"Begin Web Test", re.IGNORECASE)
_HAS_LOGIN = re.compile(r"Login To Sandbox", re.IGNORECASE)


def validate_generated_robot(robot_source: str) -> list[str]:
    """Check generated Robot code against Salesforce automation rules.

    Returns a list of violation strings (empty if clean).
    """
    errors: list[str] = []

    sleep_matches = _FORBIDDEN_SLEEP.findall(robot_source)
    if sleep_matches:
        errors.append(
            f"**Sleep** keyword used {len(sleep_matches)}x — "
            "forbidden per rule 2.1. Use dynamic waits instead."
        )

    raw_kw = _FORBIDDEN_RAW_KW.findall(robot_source)
    if raw_kw:
        unique = sorted(set(k.strip() for k in raw_kw))
        errors.append(
            f"Raw SeleniumLibrary keyword(s) detected: {', '.join(unique)} — "
            "use GlobalKeywords wrappers instead (rule 1.1)."
        )

    if _HAS_BEGIN_WEB_TEST.search(robot_source) and not _HAS_LOGIN.search(robot_source):
        errors.append(
            "**Login To Sandbox** is missing after **Begin Web Test** — "
            "tests must authenticate before interacting with Salesforce."
        )

    return errors


def analyze_test_failure(test_name: str, error_message: str) -> str:
    """Ask the LLM for a plain-English root cause analysis of a failed test.

    Returns a short explanation string.  Never raises — returns a fallback
    message on any error so the calling code can always display *something*.
    """
    hydrate_llm_env()
    prompt = (
        f"You are an expert Salesforce QA Architect. A Robot Framework UI test "
        f"named '{test_name}' just failed with this error:\n\n"
        f"'{error_message}'\n\n"
        "Briefly explain in 2-3 sentences what likely went wrong in Salesforce "
        "(e.g., missing field, changed locator, validation rule, timing issue) "
        "and suggest a fix. Do not output markdown code blocks, just plain text."
    )
    system = "You are a concise QA debugging assistant. Answer in plain text only."
    try:
        return call_llm(system, prompt).strip()
    except Exception:  # noqa: BLE001
        return "AI Analysis unavailable."


def format_robot_code(file_path: Path) -> bool:
    """Run ``robotidy`` on *file_path* to enforce consistent formatting.

    Returns ``True`` if formatting succeeded, ``False`` on any error (missing
    tool, bad syntax, etc.).  Never raises.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "robotidy", str(file_path)],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            _logger.info("robotidy formatted %s successfully.", file_path.name)
            return True
        _logger.warning(
            "robotidy exited with code %s for %s: %s",
            result.returncode, file_path.name,
            (result.stderr or result.stdout or b"").decode(errors="replace")[:300],
        )
    except FileNotFoundError:
        _logger.warning("robotidy is not installed — skipping formatting.")
    except subprocess.TimeoutExpired:
        _logger.warning("robotidy timed out on %s — skipping.", file_path.name)
    except Exception:
        _logger.warning("robotidy failed on %s.", file_path.name, exc_info=True)
    return False


def generate_test_from_prompt(
    user_input: str,
    csv_bytes: bytes | None = None,
    image_bytes: bytes | None = None,
    output_path: Path | None = None,
) -> Path:
    """
    Send system prompt + keyword catalog + user_input to the configured LLM,
    extract .robot code, save to Tests/Generated/temp_test.robot.

    If ``csv_bytes`` is set, writes ``uploaded_test_data.csv`` next to the suite and,
    when the generated source references ``@{LEADS_FROM_CSV}``, injects library + Suite Setup
    so the FOR loop receives real rows.

    If ``image_bytes`` is set, the screenshot is sent alongside the text prompt so the
    LLM can visually identify field names, buttons, and layout.

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

    raw = call_llm(system_prompt, user_content, image_bytes=image_bytes)

    robot_source = extract_robot_code(raw)
    if not robot_source.strip():
        raise ValueError("LLM returned no usable .robot content.")

    robot_source = strip_credential_variable_overrides(robot_source)
    robot_source = strip_llm_robot_garbage(robot_source)
    robot_source = strip_hallucinated_csv_variables_from_suite(robot_source)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if csv_bytes and csv_bytes.strip():
        (OUTPUT_PATH.parent / UPLOADED_CSV_FILENAME).write_bytes(csv_bytes)
        if "LEADS_FROM_CSV" in robot_source:
            robot_source = inject_csv_loader_into_robot(robot_source)

    final_source = robot_source.rstrip() + "\n"
    OUTPUT_PATH.write_text(final_source, encoding="utf-8")
    format_robot_code(OUTPUT_PATH)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_source, encoding="utf-8")
        format_robot_code(output_path)
        return output_path

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
