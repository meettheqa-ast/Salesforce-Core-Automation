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
    # Cursor Cloud Agents API. Routes prompts through your Cursor
    # subscription (same account you're logged into in the IDE). Spawns
    # a cloud agent against ``CURSOR_AGENT_REPO`` and returns the agent's
    # assistant text. Higher latency than direct LLM APIs because each
    # call cold-starts an agent container; recommended last in the
    # failover chain.
    "cursor":      ("CURSOR_API_KEY",      "CURSOR_MODEL",      "composer-2"),
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
    "cursor": "Cursor",
}


_dotenv_mtime_ns: int | None = None


def hydrate_llm_env() -> None:
    """
    Load .env again (e.g. file created after import) and map Streamlit
    secrets into os.environ.

    Refresh semantics: on the very first call, load WITHOUT overriding so a
    shell-exported var (e.g. ``set OPENAI_API_KEY=...``) wins. On any
    subsequent call where ``.env``'s mtime has changed since last hydrate,
    reload WITH override so dev edits to ``LLM_PROVIDER`` / model names
    actually take effect on the next request -- without this, swapping
    providers in ``.env`` was a silent no-op against the long-running
    backend (the provider name was sticky from process start).
    """
    global _dotenv_mtime_ns  # pylint: disable=global-statement
    try:
        current_mtime = DOTENV_PATH.stat().st_mtime_ns
    except OSError:
        current_mtime = None

    if _dotenv_mtime_ns is None:
        load_dotenv(DOTENV_PATH, override=False)
    elif current_mtime is not None and current_mtime != _dotenv_mtime_ns:
        load_dotenv(DOTENV_PATH, override=True)

    if current_mtime is not None:
        _dotenv_mtime_ns = current_mtime

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
    """Lean catalog JSON for LLM prompts (legacy/standalone path).

    Used when the full ``ai_qa_portal.backend.services.keyword_catalog``
    isn't importable (CLI/Streamlit-only invocations). Strips the heavy
    ``documentation`` / ``tags`` / ``source_file`` fields the planner
    doesn't read, mirroring ``keyword_catalog.compact_for_prompt``. The
    full pretty-printed version was ~36k tokens on this codebase, big
    enough to 413 Groq's free tier and slow Gemini to 30-100 s.
    """
    raw = _load_text(CATALOG_PATH)
    data = json.loads(raw)
    keywords = data.get("keywords") or []
    lean: list[dict] = []
    for kw in keywords:
        name = (kw.get("keyword_name") or "").strip()
        if not name:
            continue
        src = kw.get("source_file") or ""
        # "Resources/PO/Platform/SalesPO.robot" -> "SalesPO"
        lib = src.rsplit("/", 1)[-1].rsplit(".", 1)[0] if src else ""
        qualified = name if "." in name or not lib else f"{lib}.{name}"
        entry: dict = {"name": qualified}
        if kw.get("arguments"):
            entry["args"] = list(kw["arguments"])
        summary = (kw.get("natural_language_summary") or "").strip()
        if summary and not summary.startswith("Robot Framework keyword:"):
            summary = summary.splitlines()[0].strip()
            if len(summary) > 140:
                summary = summary[:139].rstrip() + "…"
            entry["doc"] = summary
        lean.append(entry)
    return json.dumps(lean, ensure_ascii=False, separators=(",", ":"))


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


_EMPTY_VAR_OVERRIDE = re.compile(
    r"^\s*\$\{[A-Za-z_][A-Za-z0-9_]*\}\s*=?\s*\$\{(?:EMPTY|SPACE)\}\s*$"
)


def strip_empty_variable_overrides(robot_source: str) -> str:
    """Drop ``${name}    ${EMPTY}`` lines from ``*** Variables ***``.

    The LLM frequently emits empty overrides for fields the user didn't
    name in the prompt (e.g. user says "Create a Lead named Meet Sheth"
    and the model writes ``${leadFirstName}    Meet`` /
    ``${leadLastName}    Sheth`` / ``${leadCompany}    ${EMPTY}``).
    Those empty assignments override the FakerLibrary defaults baked into
    Resources/TestData/Platform/SalesData.robot, leaving the required
    Salesforce field blank at runtime -- which then trips Salesforce's
    own "Complete this field" validation and the Save fails.

    Removing those lines is always safe: when a variable isn't redefined
    in the test suite, Robot falls back to the SalesData.robot value
    (FakerLibrary-generated synthetic data), which is exactly what the
    "[Auto-generate data]" prompt hint asks for.

    ``${SPACE}`` is treated the same way -- a single space is just as
    bad as empty for required Salesforce fields.
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
        if in_variables and _EMPTY_VAR_OVERRIDE.match(line):
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


def fix_misplaced_setup_teardown(robot_source: str) -> str:
    """Fix LLM mistake of placing Test Setup / Test Teardown inside a test body.

    When the AI emits lines like ``    Test Setup    Begin Web Test`` indented under
    a test case, Robot Framework interprets them as keyword calls and fails with
    "No keyword with name 'Test Setup' found."  This function detects those lines,
    removes them from the test body, and ensures the *** Settings *** section has
    proper ``Test Setup`` and ``Test Teardown`` directives.
    """
    lines = robot_source.splitlines()
    setup_kw: str | None = None
    teardown_kw: str | None = None
    cleaned: list[str] = []
    settings_end_idx: int | None = None
    in_settings = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^\*\*\*\s*Settings\s*\*\*\*", stripped, re.I):
            in_settings = True
            cleaned.append(line)
            continue
        if in_settings:
            if stripped.startswith("***") and "settings" not in stripped.lower():
                in_settings = False
                settings_end_idx = len(cleaned)
            elif re.match(r"^Test\s+Setup\b", stripped, re.I):
                cleaned.append(line)
                continue
            elif re.match(r"^Test\s+Teardown\b", stripped, re.I):
                cleaned.append(line)
                continue

        if line.startswith((" ", "\t")):
            m_setup = re.match(r"^\s+Test\s+Setup\s+(.+)", line, re.I)
            m_teardown = re.match(r"^\s+Test\s+Teardown\s+(.+)", line, re.I)
            if m_setup:
                setup_kw = m_setup.group(1).strip()
                continue
            if m_teardown:
                teardown_kw = m_teardown.group(1).strip()
                continue

        cleaned.append(line)

    if not setup_kw and not teardown_kw:
        return robot_source

    has_setup = any(re.match(r"^Test\s+Setup\b", ln.strip(), re.I) for ln in cleaned)
    has_teardown = any(re.match(r"^Test\s+Teardown\b", ln.strip(), re.I) for ln in cleaned)

    inject: list[str] = []
    if setup_kw and not has_setup:
        inject.append(f"Test Setup          {setup_kw}")
    if teardown_kw and not has_teardown:
        inject.append(f"Test Teardown       {teardown_kw}")

    if inject and settings_end_idx is not None:
        for j, inj_line in enumerate(inject):
            cleaned.insert(settings_end_idx + j, inj_line)
    elif inject:
        for i, ln in enumerate(cleaned):
            if re.match(r"^\*\*\*\s*Settings\s*\*\*\*", ln.strip(), re.I):
                for j, inj_line in enumerate(inject):
                    cleaned.insert(i + 1 + j, inj_line)
                break

    return "\n".join(cleaned) + ("\n" if robot_source.endswith("\n") else "")


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

    # Hard timeout so a slow / hung upstream returns an error instead of
    # blocking the user-facing request for the SDK default (~10 min).
    # Configurable via LLM_REQUEST_TIMEOUT_S env var.
    _llm_timeout = float(os.environ.get("LLM_REQUEST_TIMEOUT_S", "40"))
    client_kwargs: dict = {"api_key": api_key, "timeout": _llm_timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

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

    # Hard SDK-level timeout so a wedged Gemini call surfaces as an error
    # instead of hanging for the SDK default (~600 s). Configurable via
    # LLM_REQUEST_TIMEOUT_S env var. The orchestrator (e.g.
    # ai_qa_portal generate.py) wraps this in its own thread-level timeout
    # for defense in depth, but that only abandons the thread -- the HTTP
    # request keeps running in the background until *something* fails it.
    # Setting it here actually cancels the upstream request.
    _llm_timeout = float(os.environ.get("LLM_REQUEST_TIMEOUT_S", "40"))

    try:
        resp = model.generate_content(
            content_parts,
            generation_config={"temperature": 0.2},
            request_options={"timeout": _llm_timeout},
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
        if "deadline" in err or "timeout" in err:
            raise RuntimeError(
                f"Gemini request exceeded {_llm_timeout:.0f}s and was cancelled. "
                "Try a faster model (e.g. gemini-2.5-flash-lite) or increase "
                "LLM_REQUEST_TIMEOUT_S. Original error: {exc}"
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


def _call_cursor(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
) -> str:
    """Route the prompt through the Cursor Cloud Agents API.

    Why this exists: lets users on a Cursor subscription reuse the same
    quota for our portal's LLM calls. No new account, just an API key
    minted from cursor.com/dashboard/integrations.

    Required env:
      ``CURSOR_API_KEY`` -- the ``crsr_...`` key from Cursor dashboard.
      ``CURSOR_AGENT_REPO`` -- a GitHub repo URL the agent operates
        against. The Cloud Agents API mandates a repo target; we don't
        actually care about its contents because we extract the
        assistant text from the SSE stream and discard everything else.
        Any repo you have access to works -- the agent never opens a PR
        (we set ``autoCreatePR=false``) and we delete the agent record
        after every call so your dashboard stays clean.

    Optional env:
      ``CURSOR_MODEL`` -- explicit model id, e.g. ``claude-4-sonnet-thinking``.
        Defaults to ``LLM_PROVIDERS["cursor"][2]``. ``GET /v1/models``
        returns the current valid set if you want to override.
      ``CURSOR_AGENT_TIMEOUT_S`` -- max seconds to stream a single
        completion (default 180; cloud-agent cold start adds 30-60 s on
        top of LLM latency).

    Latency note: cold starts mean Cursor is ~10-30 x slower per call
    than a direct Claude/OpenAI call. Recommended placement: end of the
    ``LLM_FAILOVER_ORDER`` so it only fires when faster providers are
    exhausted -- keeps the validator-loop budget reasonable.
    """
    import base64
    import json

    import requests

    hydrate_llm_env()
    api_key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "Cursor: CURSOR_API_KEY is not set. Generate a key at "
            "https://cursor.com/dashboard/integrations and add it to .env."
        )
    repo = (os.environ.get("CURSOR_AGENT_REPO") or "").strip()
    if not repo:
        raise RuntimeError(
            "Cursor: CURSOR_AGENT_REPO is not set. Cursor Cloud Agents "
            "require a GitHub repo URL to operate against. Use any repo "
            "you have access to (e.g. https://github.com/<you>/scratch); "
            "we never push PRs to it -- the response is read from the "
            "agent's assistant text stream."
        )
    model_id = (os.environ.get("CURSOR_MODEL") or LLM_PROVIDERS["cursor"][2]).strip()
    timeout_s = int(os.environ.get("CURSOR_AGENT_TIMEOUT_S", "180"))

    auth_header = "Basic " + base64.b64encode(f"{api_key}:".encode()).decode()
    json_headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
    }
    sse_headers = {
        "Authorization": auth_header,
        "Accept": "text/event-stream",
    }

    # Cursor agents are designed for repo-edit workflows. We're using them
    # as a chat-completion endpoint, so the prompt explicitly tells the
    # agent NOT to edit files / open PRs and to emit the answer directly
    # in its assistant stream. This works because the assistant deltas
    # carry the same text the agent would otherwise put in chat
    # responses; we just ignore the file-system side effects on the
    # throwaway branch.
    full_prompt = (
        "You are an LLM completion endpoint. Do NOT edit files, do NOT "
        "create commits, do NOT open pull requests. Your ONLY job is to "
        "respond with the answer below in your assistant text stream "
        "and stop.\n\n"
        f"# System instructions\n\n{system_prompt}\n\n"
        f"# User request\n\n{user_content}\n\n"
        "# Reminder\n\nReturn ONLY the requested content as plain "
        "assistant text. No file edits, no PR, no commentary outside the "
        "requested content."
    )

    # Note: ``autoGenerateBranch`` is INTENTIONALLY omitted -- it is
    # only valid when ``repos[0].prUrl`` is set, and including it on a
    # non-PR run produces ``validation_error: Unrecognized key(s) in
    # object: 'autoGenerateBranch'`` from Cursor's API.
    body: dict = {
        "prompt": {"text": full_prompt},
        "repos": [{"url": repo}],
        "autoCreatePR": False,
    }
    if model_id:
        body["model"] = {"id": model_id}

    # 1. Create the agent + initial run.
    create = requests.post(
        "https://api.cursor.com/v1/agents",
        headers=json_headers,
        json=body,
        timeout=30,
    )
    if create.status_code >= 400:
        # Surface the body so failover classifier sees a useful message
        # (the classifier scans for "quota", "429", "401" etc.).
        raise RuntimeError(
            f"Cursor agent create failed: HTTP {create.status_code}: {create.text[:300]}"
        )
    payload = create.json()
    try:
        agent_id = payload["agent"]["id"]
        run_id = payload["run"]["id"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"Cursor agent create: unexpected response shape: {payload!r}") from exc

    # 2. Stream the run; collect assistant text deltas until ``result``.
    parts: list[str] = []
    error: str | None = None
    finished = False
    try:
        with requests.get(
            f"https://api.cursor.com/v1/agents/{agent_id}/runs/{run_id}/stream",
            headers=sse_headers,
            stream=True,
            timeout=timeout_s,
        ) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Cursor stream failed: HTTP {resp.status_code}: {resp.text[:300]}"
                )
            event = ""
            for raw in resp.iter_lines(decode_unicode=True):
                if raw is None:
                    continue
                line = raw.rstrip("\r")
                if not line:
                    event = ""  # blank line terminates an SSE event block
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if event == "assistant":
                    text = data.get("text") or ""
                    if text:
                        parts.append(text)
                elif event == "error":
                    error = (
                        f"{data.get('code', 'unknown')}: "
                        f"{data.get('message', 'unspecified error')}"
                    )
                    break
                elif event == "result":
                    finished = True
                    status = (data.get("status") or "").upper()
                    # CANCELLED / FAILED are terminal failure states; the
                    # ``done`` event would close the stream cleanly but
                    # without payload, so we capture the reason here.
                    if status not in ("FINISHED", "COMPLETED", ""):
                        error = f"run terminated with status={status}"
                    break
                elif event == "done":
                    finished = True
                    break
    finally:
        # 3. Always clean up the agent record so the user's dashboard
        # doesn't accumulate one entry per script generation. Best-effort:
        # a failed delete just leaves a (harmless) row in their list.
        try:
            requests.delete(
                f"https://api.cursor.com/v1/agents/{agent_id}",
                headers=json_headers,
                timeout=10,
            )
        except Exception:  # noqa: BLE001
            pass

    if error:
        raise RuntimeError(f"Cursor agent: {error}")
    if not parts:
        raise RuntimeError(
            f"Cursor agent returned no assistant text (finished={finished})"
        )
    return "".join(parts)


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
    "cursor": _call_cursor,
}


# ---------------------------------------------------------------------------
# Auto-failover across LLM providers
# ---------------------------------------------------------------------------
# When the configured primary provider returns a quota / rate-limit / auth /
# availability error, ``call_llm`` automatically tries the next configured
# provider with an API key, records the switch on a thread-local notes
# buffer, and returns the successful response. API endpoints can drain the
# buffer after the call to surface a banner like "Switched from Gemini to
# Groq because Gemini hit its quota."
#
# Provider order:
#   1. ``LLM_PROVIDER`` env var (the user's primary choice).
#   2. ``LLM_FAILOVER_ORDER`` env var (CSV of preferred fallbacks).
#   3. Any remaining provider with an API key, in ``LLM_PROVIDERS`` dict
#      order (gemini, openai, groq, mistral, together, openrouter,
#      anthropic, cohere).
#
# Cooldowns are best-effort and live only for this process (no persistence
# across reloads -- a uvicorn restart re-tries the failed provider).

import threading as _threading
from dataclasses import dataclass as _dataclass


@_dataclass
class ProviderSwitchNote:
    """One LLM provider failover event. Surfaced to API consumers so the
    UI can render 'Switched from Gemini to Groq because Gemini hit its
    quota'."""

    from_provider: str
    to_provider: str
    reason: str
    error_excerpt: str

    def to_dict(self) -> dict:
        return {
            "from_provider": self.from_provider,
            "from_label": PROVIDER_LABELS.get(self.from_provider, self.from_provider),
            "to_provider": self.to_provider,
            "to_label": PROVIDER_LABELS.get(self.to_provider, self.to_provider),
            "reason": self.reason,
            "error_excerpt": self.error_excerpt,
        }


# Provider -> ``time.monotonic()`` deadline before which the provider is
# considered exhausted (skipped during failover). Process-local; reset on
# every backend restart.
_provider_exhausted_until: dict[str, float] = {}
_provider_exhausted_lock = _threading.Lock()

# Per-thread switch notes buffer so concurrent uvicorn workers don't see
# each other's events. Drained explicitly by callers via
# ``drain_provider_notes()`` after a sequence of LLM calls.
_provider_notes_local = _threading.local()


def _get_notes_buffer() -> list[ProviderSwitchNote]:
    if not hasattr(_provider_notes_local, "buffer"):
        _provider_notes_local.buffer = []
    return _provider_notes_local.buffer


def drain_provider_notes() -> list[dict]:
    """Pop and return every ``ProviderSwitchNote`` recorded by ``call_llm``
    on this thread since the last drain. Returns ``[]`` when the call(s)
    completed on the primary provider without failover.

    Designed for a "drain after the high-level operation" pattern: API
    handlers call this once after their LLM-driven work and pass the list
    into the response payload so the UI can render the banner.
    """
    buf = _get_notes_buffer()
    out = [n.to_dict() for n in buf]
    buf.clear()
    return out


# Pattern sets for ``_classify_error``. Matched against
# ``f"{type(exc).__name__}: {exc}".lower()``. Order doesn't matter -- first
# bucket that hits decides cooldown + reason.
_QUOTA_PATTERNS = (
    "quota", "exhausted", "resource_exhausted",
    "exceeded your current quota", "billing",
    "insufficient_quota", "out of credits",
)
_RATE_LIMIT_PATTERNS = (
    "rate limit", "rate_limit", "rate_limited", "ratelimit",
    "tokens per minute", " rpm", " tpm",
    " 429 ", "429,", "(429)",
)
_AUTH_PATTERNS = (
    "api key not valid", "invalid api key", "incorrect api key",
    " 401 ", " 403 ", "unauthorized", "forbidden",
    "permission_denied", "permissiondenied",
    "authenticationerror", "authentication failed",
)
_AVAILABILITY_PATTERNS = (
    " 500 ", " 502 ", " 503 ", " 504 ",
    "internal server error", "service unavailable",
    "timed out", "timeout", "connection refused",
    "remote disconnected", "connection reset",
    "connection error", "connectionerror",
)


def _classify_error(exc: BaseException) -> tuple[bool, float, str]:
    """Decide whether the exception should trigger failover, and if so,
    how long to cool the failed provider down before re-trying it.

    Returns ``(failover_eligible, cooldown_seconds, human_reason)``.

    Anything not in the four buckets is left as the user's bug (prompt
    too long, schema validation, etc.) and re-raised by the caller --
    failing over wouldn't help.
    """
    msg = f"{type(exc).__name__}: {exc}".lower()
    if any(p in msg for p in _QUOTA_PATTERNS):
        # Daily quota usually resets at midnight UTC. 30 min keeps the
        # provider sidelined long enough that a re-run on the same
        # session doesn't keep banging on a known-exhausted key.
        return True, 30 * 60.0, "daily quota exhausted"
    if any(p in msg for p in _RATE_LIMIT_PATTERNS):
        return True, 60.0, "rate-limited"
    if any(p in msg for p in _AUTH_PATTERNS):
        # Bad / missing API key. Cool for 24 h so we don't keep hitting
        # it; operator can fix the env var and restart the backend.
        return True, 24 * 3600.0, "authentication failed (check API key)"
    if any(p in msg for p in _AVAILABILITY_PATTERNS):
        return True, 60.0, "provider unavailable"
    return False, 0.0, ""


def _is_exhausted(provider: str, now: float) -> bool:
    with _provider_exhausted_lock:
        return _provider_exhausted_until.get(provider, 0.0) > now


def _mark_exhausted(provider: str, cooldown_s: float) -> None:
    import time as _t
    with _provider_exhausted_lock:
        _provider_exhausted_until[provider] = _t.monotonic() + cooldown_s


def _has_api_key(provider: str) -> bool:
    """True when the provider has a usable API key in env. Gemini also
    accepts ``GOOGLE_API_KEY`` for backward compat with the official
    Google SDK examples."""
    info = LLM_PROVIDERS.get(provider)
    if info is None:
        return False
    if os.environ.get(info[0], "").strip():
        return True
    if provider == "gemini" and os.environ.get("GOOGLE_API_KEY", "").strip():
        return True
    return False


def _build_failover_chain(primary: str) -> list[str]:
    """Build the ordered provider-attempt chain for one failover-aware
    LLM call. Primary first, then ``LLM_FAILOVER_ORDER`` (CSV), then any
    remaining providers with API keys configured.

    Duplicates are removed; providers without keys are skipped (no point
    listing them in the chain when we know they'll fail with auth).
    """
    if primary == "google":
        primary = "gemini"
    chain: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        p = p.strip().lower()
        if p == "google":
            p = "gemini"
        if p in LLM_PROVIDERS and p not in seen and _has_api_key(p):
            chain.append(p)
            seen.add(p)

    if primary:
        _add(primary)
    explicit = os.environ.get("LLM_FAILOVER_ORDER", "").strip()
    if explicit:
        for p in explicit.split(","):
            _add(p)
    for p in LLM_PROVIDERS:
        _add(p)
    return chain


def call_llm(
    system_prompt: str,
    user_content: str,
    image_bytes: bytes | None = None,
    provider: str | None = None,
) -> str:
    """Route to an LLM backend with **automatic failover**.

    Default behaviour: read ``LLM_PROVIDER`` env var as the primary, then
    on quota / rate-limit / auth / 5xx / network errors fail over to the
    next provider with an API key. Each switch is logged + appended to
    the per-thread notes buffer (drainable via
    :func:`drain_provider_notes`) so API endpoints can surface a
    "Switched from X to Y because Z" banner to the user without writing
    bespoke retry logic.

    Pass ``provider="..."`` to force a specific provider with NO
    failover -- useful for tests / A-B comparisons that need
    deterministic routing.

    Failover can be disabled globally with ``LLM_FAILOVER_DISABLED=1``
    (legacy single-provider behaviour for environments that prefer
    failing fast over auto-switching).
    """
    import time as _t
    hydrate_llm_env()

    # Forced provider: legacy strict path, no failover.
    if provider:
        prov = provider.strip().lower()
        if prov == "google":
            prov = "gemini"
        caller = _PROVIDER_CALLERS.get(prov)
        if not caller:
            supported = ", ".join(sorted(LLM_PROVIDERS.keys()))
            raise ValueError(f"Unsupported LLM_PROVIDER: {provider!r}. Supported: {supported}")
        return caller(system_prompt, user_content, image_bytes=image_bytes)

    if os.environ.get("LLM_FAILOVER_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        primary_only = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
        if primary_only == "google":
            primary_only = "gemini"
        caller = _PROVIDER_CALLERS.get(primary_only)
        if not caller:
            raise ValueError(f"Unsupported LLM_PROVIDER: {primary_only!r}")
        return caller(system_prompt, user_content, image_bytes=image_bytes)

    primary = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
    chain = _build_failover_chain(primary)
    if not chain:
        configured = ", ".join(LLM_PROVIDERS[p][0] for p in LLM_PROVIDERS)
        raise RuntimeError(
            "No LLM provider has an API key configured. Set at least one of: "
            f"{configured}."
        )

    log = logging.getLogger("ai_bridge.llm")
    now = _t.monotonic()
    last_exc: BaseException | None = None
    failed_along_the_way: list[tuple[str, str]] = []

    for prov in chain:
        if _is_exhausted(prov, now):
            failed_along_the_way.append((prov, "in cooldown"))
            continue
        caller = _PROVIDER_CALLERS.get(prov)
        if caller is None:
            continue
        try:
            result = caller(system_prompt, user_content, image_bytes=image_bytes)
        except BaseException as exc:  # noqa: BLE001 -- broad on purpose
            failover, cooldown, reason = _classify_error(exc)
            if not failover:
                # Unknown error class: treat as a real bug, not a quota
                # signal. Failing over could mask a prompt issue every
                # provider would reject the same way.
                raise
            _mark_exhausted(prov, cooldown)
            failed_along_the_way.append((prov, reason))
            last_exc = exc
            log.warning(
                "LLM provider %s failed (%s); failing over. err=%s",
                prov, reason, str(exc)[:160],
            )
            continue
        # Success. If we had to fail through earlier provider(s), record
        # a switch note so the API surface can show it to the user.
        if failed_along_the_way:
            from_prov, why = failed_along_the_way[0]
            note = ProviderSwitchNote(
                from_provider=from_prov,
                to_provider=prov,
                reason=why,
                error_excerpt=(str(last_exc)[:160] if last_exc else ""),
            )
            _get_notes_buffer().append(note)
            log.info(
                "LLM failover: %s -> %s (%s)",
                from_prov, prov, why,
            )
        return result

    detail = "; ".join(f"{p}: {r}" for p, r in failed_along_the_way) or "no providers attempted"
    raise RuntimeError(
        f"All configured LLM providers failed or are in cooldown ({detail})."
        + (f" Last error: {last_exc}" if last_exc else "")
    )


def break_prompt_into_steps(
    user_prompt: str,
    catalog_json: str | None = None,
    scenario_analysis: dict | None = None,
    default_app: str = "",
) -> list[dict[str, list[str] | str]]:
    """Use the current LLM to decompose a natural-language prompt into
    a list of Robot Framework keyword steps.

    Returns a list of dicts: [{"keyword": "...", "args": ["...", ...]}, ...]
    """
    hydrate_llm_env()

    # Prefer the assembler-built system prompt (Salesforce playbook + stepwise
    # tail) over the legacy inline string. Live catalog supersedes static
    # JSON when the scanner is available.
    try:
        from ai_qa_portal.backend.prompts import assembler as _assembler
        from ai_qa_portal.backend.services import keyword_catalog as _kw_catalog
        system = _assembler.build_system_prompt("stepwise")
        if catalog_json is None:
            # Lean projection: ~3-5k tokens vs ~36k for the full catalog,
            # which is the difference between "Groq 413s, Gemini takes
            # 30-100 s" and "any model returns in a few seconds".
            catalog_json = _kw_catalog.compact_for_prompt()
    except Exception:  # pylint: disable=broad-exception-caught
        if catalog_json is None:
            catalog_json = _load_catalog_compact()
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

    analysis_block = ""
    if scenario_analysis:
        analysis_block = (
            "\n\nRF-MCP scenario analysis result:\n"
            + json.dumps(scenario_analysis, indent=2, ensure_ascii=False)
        )

    persona_block = ""
    try:
        from ai_qa_portal.backend.prompts import assembler as _assembler
        persona_block = _assembler.render_persona_context(default_app)
    except Exception:  # pylint: disable=broad-exception-caught
        # Standalone CLI use: assembler unavailable. Inline a minimal block
        # so the LLM still gets the hint.
        app = (default_app or "").strip()
        if app:
            persona_block = (
                f"## Persona context\n\nPersona default app: {app} "
                f"(injected as ${{salesAutomationAppName}} at runtime)\n\n"
            )

    user_content = (
        f"{persona_block}"
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


def _build_quick_generate_prompts(
    user_input: str,
    *,
    default_app: str = "",
) -> tuple[str, str]:
    """Assemble the (system_prompt, user_content) pair used by Quick Generate.

    Extracted so both the legacy ``generate_test_from_prompt`` path and
    the new validation-loop path share one source of truth for prompt
    construction.
    """
    persona_block = ""
    try:
        from ai_qa_portal.backend.prompts import assembler as _assembler
        from ai_qa_portal.backend.services import keyword_catalog as _kw_catalog
        system_prompt = _assembler.build_system_prompt("quick")
        # Lean projection -- see compact_for_prompt() for why. Same fix as
        # the stepwise planner above.
        catalog_json = _kw_catalog.compact_for_prompt()
        persona_block = _assembler.render_persona_context(default_app)
    except Exception:  # pylint: disable=broad-exception-caught
        system_prompt = _load_text(SYSTEM_PROMPT_PATH)
        catalog_json = _load_catalog_compact()
        app = (default_app or "").strip()
        if app:
            persona_block = (
                f"## Persona context\n\nPersona default app: {app} "
                f"(injected as ${{salesAutomationAppName}} at runtime)\n\n"
            )

    user_content = (
        "You are given the full keyword catalog as JSON. "
        "Follow the system instructions exactly.\n\n"
        f"{persona_block}"
        f"## keyword_catalog.json\n\n{catalog_json}\n\n"
        f"## User request\n\n{user_input.strip()}\n"
    )
    return system_prompt, user_content


def _post_process_robot_source(robot_source: str) -> str:
    """Apply the standard cleanup pipeline (strip credentials, garbage,
    misplaced setup/teardown, ...). Idempotent; safe to call on an
    already-clean source.
    """
    robot_source = strip_credential_variable_overrides(robot_source)
    robot_source = strip_empty_variable_overrides(robot_source)
    robot_source = strip_llm_robot_garbage(robot_source)
    robot_source = strip_hallucinated_csv_variables_from_suite(robot_source)
    robot_source = fix_misplaced_setup_teardown(robot_source)
    return robot_source


def generate_test_from_prompt(
    user_input: str,
    csv_bytes: bytes | None = None,
    image_bytes: bytes | None = None,
    output_path: Path | None = None,
    default_app: str = "",
) -> Path:
    """
    Send system prompt + keyword catalog + user_input to the configured LLM,
    extract .robot code, save to Tests/Generated/temp_test.robot.

    Single-shot convenience wrapper: NO validation/retry. Use
    ``generate_test_from_prompt_validated`` for the production path that
    self-corrects against the AST validator + ``robot --dryrun`` gate.

    If ``csv_bytes`` is set, writes ``uploaded_test_data.csv`` next to the suite and,
    when the generated source references ``@{LEADS_FROM_CSV}``, injects library + Suite Setup
    so the FOR loop receives real rows.

    If ``image_bytes`` is set, the screenshot is sent alongside the text prompt so the
    LLM can visually identify field names, buttons, and layout.

    Returns path to the written file.
    """
    hydrate_llm_env()
    system_prompt, user_content = _build_quick_generate_prompts(
        user_input, default_app=default_app,
    )
    raw = call_llm(system_prompt, user_content, image_bytes=image_bytes)

    robot_source = extract_robot_code(raw)
    if not robot_source.strip():
        raise ValueError("LLM returned no usable .robot content.")

    robot_source = _post_process_robot_source(robot_source)

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


def generate_test_from_prompt_validated(
    user_input: str,
    csv_bytes: bytes | None = None,
    image_bytes: bytes | None = None,
    output_path: Path | None = None,
    default_app: str = "",
    max_attempts: int | None = None,
    skip_dryrun: bool = False,
):
    """Production-grade Quick Generate: LLM call wrapped in the
    ``script_validation_loop`` so the script that lands on disk is
    AST-clean and passes ``robot --dryrun``.

    Returns a ``ValidationLoopResult``. The final script is at the
    returned ``final_script`` field AND written to disk at
    ``output_path`` (or ``OUTPUT_PATH`` when omitted). Even when the
    loop exhausts its attempt budget without converging, the LAST
    attempt's script is on disk and the report's ``errors`` are
    populated so the caller can surface them inline.

    On the very first call, ``llm_call`` is invoked with no fix prompt
    (fresh generation). Subsequent calls receive the fix prompt the
    validator built from the previous attempt's errors -- this is what
    drives the LLM to self-correct without us having to teach it
    anything new.

    CSV / image handling matches ``generate_test_from_prompt``.
    """
    from ai_qa_portal.backend.services.script_validation_loop import (
        run_with_validation,
    )

    hydrate_llm_env()
    system_prompt, base_user_content = _build_quick_generate_prompts(
        user_input, default_app=default_app,
    )

    target_path = output_path or OUTPUT_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if csv_bytes and csv_bytes.strip():
        (target_path.parent / UPLOADED_CSV_FILENAME).write_bytes(csv_bytes)

    def _llm(fix_prompt: str | None) -> str:
        # First attempt: no fix prompt; subsequent attempts append the
        # validator's structured correction request to the user message.
        # We deliberately keep the system prompt + catalog block stable
        # across attempts so the LLM's grounding doesn't shift between
        # rounds; only the fix instructions accumulate.
        content = base_user_content
        if fix_prompt:
            content = (
                base_user_content
                + "\n\n## Validator feedback (attempt failed)\n\n"
                + fix_prompt
            )
        return call_llm(system_prompt, content, image_bytes=image_bytes)

    def _post(robot_source: str) -> str:
        cleaned = _post_process_robot_source(robot_source)
        # CSV loader injection is only relevant when the generated body
        # references the variable -- otherwise it'd fail dryrun anyway.
        if csv_bytes and csv_bytes.strip() and "LEADS_FROM_CSV" in cleaned:
            cleaned = inject_csv_loader_into_robot(cleaned)
        return cleaned

    result = run_with_validation(
        llm_call=_llm,
        post_process=_post,
        extract_robot=extract_robot_code,
        suite_path=target_path,
        max_attempts=max_attempts,
        skip_dryrun=skip_dryrun,
    )

    # Format the final on-disk file so robotidy normalisation matches
    # the rest of the codebase.
    if target_path.is_file():
        try:
            format_robot_code(target_path)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    return result


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
