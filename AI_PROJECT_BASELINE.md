# Salesforce AI Automation Architect — Technical Baseline

This document describes the **current** architecture, data flow, and capabilities of the *Salesforce AI Automation Architect* Streamlit application. It is intended for AI assistants and engineers onboarding to the repository.

---

## 1. Repository role

The project turns **natural-language test intents** into **Robot Framework** `.robot` suites that drive **Salesforce Lightning** via **SeleniumLibrary**, using **shared Page Object (PO) keywords** and **GlobalKeywords**—not ad-hoc Selenium in generated tests.

**Entry point (UI):** `streamlit run app.py` (project root).

**Key artifacts:**

| Artifact | Purpose |
|----------|---------|
| `Tests/Generated/temp_test.robot` | Overwritten each successful AI generation; executed by the pipeline |
| `Resources/TestData/EnvData.robot` | Overwritten by `run_test.py` with sandbox URL / username / password |
| `Results/<run_name>/` | `output.xml`, `log.html`, `report.html` per run |
| `keyword_catalog.json` | Machine-readable keyword index for the LLM (regenerated from `.robot` sources) |
| `system_prompt.txt` | Static system / developer instructions for the LLM |

---

## 2. End-to-end data flow

### 2.1 Startup (`app.py`)

1. **`st.set_page_config`** — wide layout, expanded sidebar, title *Salesforce AI Automation Architect*.
2. **First session load:** `rebuild_keyword_catalog()` runs once (`st.session_state._catalog_initialized`). This imports `generate_keyword_mapping.main()` and rescans `Resources/PO` and `Resources/Common`, writing `keyword_catalog.json`. Failures are stored in `st.session_state._catalog_init_error` and surfaced in the sidebar.
3. **`main_ui()`** calls **`ai_bridge.hydrate_llm_env()`** so `.env` and optional Streamlit secrets populate `os.environ` for API keys and model/provider settings.

### 2.2 User submits a run

1. **Inputs:** Sidebar — Sandbox URL, Username, Password; **Execution mode** (maps to `headless`); **LLM provider** (Gemini vs OpenAI) and optional session-only API keys synced via **`_sync_sidebar_api_key`** (clears sidebar-injected env vars when the field is emptied so `.env` can apply again). Main area — **User prompt** text area.
2. **Run button:** `🚀 Run Automation` sets validation in motion.

### 2.3 Pre-flight / PM clarification (lead flows)

1. **`ai_bridge.analyze_prompt_for_required_fields(prompt)`** applies lightweight regex/heuristics:
   - Detects lead-creation intent (phrases like “create a lead”, “new lead”, …).
   - Infers whether **Company** and **Last Name** appear to be specified.
   - Builds **`optional_picklist_fields`** (Lead Status, Salutation, Lead Source) when those concepts are not explicitly named in the prompt.
2. If **`should_show_lead_pm_form`** is true, **`st.session_state["clarify_context"]`** stores `original_prompt`, `missing_fields`, `optional_picklists`. The user sees a **form** (“Lead test — PM details”): required fields must be filled; picklists are optional (blank ⇒ augmented instructions tell the LLM to use random visible dropdown behavior).
3. **Submit & Run Automation** validates credentials and required fields, then **`build_augmented_prompt(...)`** appends natural-language clauses (user clarifications + picklist instructions, including “random visible option” via **`Open Dropdown And Select First Option`** when labels are blank). **`clarify_context`** is cleared and the pipeline proceeds with the **augmented** string.
4. If no clarification is needed, **`run_automation_pipeline`** is called with the **raw** prompt.

### 2.4 Automation pipeline (`run_automation_pipeline` in `app.py`)

1. **Refresh catalog:** `rebuild_keyword_catalog()` again (and clears **`@st.cache_data`** on `_load_keyword_catalog_payload` via `clear()` after regen).
2. **AI generation:** `ai_bridge.generate_test_from_prompt(final_prompt)`:
   - Loads **`system_prompt.txt`** (system instruction).
   - Loads **`keyword_catalog.json`** as compact JSON.
   - Builds **user message** containing the full catalog + user request.
   - Calls **Gemini** or **OpenAI** per `LLM_PROVIDER`.
   - **`extract_robot_code`** pulls `.robot` from fenced blocks, `---ROBOT---`, or `***` sections.
   - **`strip_credential_variable_overrides`** removes `*** Variables ***` lines that redefine `${globalSandboxTestUrl}`, `${sandboxUserNameInput}`, `${sandboxPasswordInput}` (prevents suite-level overrides that break `EnvData.robot`).
   - **`strip_llm_robot_garbage`** removes markdown fences and invalid “keyword” headers that look like `${var}` only.
   - Writes **`Tests/Generated/temp_test.robot`**.
3. **Robot execution:** `run_test.build_robot_run(...)`:
   - **`write_envdata`** overwrites **`Resources/TestData/EnvData.robot`** with escaped scalar values for the three credential variables.
   - Builds **`robot`** CLI (or `python -m robot`): `--outputdir`, `output.xml`, `log.html`, `report.html`, optional **`-v headless:true`**, or **`-v MFA_PAUSE_FOR_MANUAL_COMPLETION:true`** when **not** headless (manual MFA/OTP in browser).
   - Returns **`cmd`** and **`out_dir`** (e.g. `Results/ui_YYYYMMDD_HHMMSS/`).
4. **Live logs:** **`stream_robot_logs`** runs **`subprocess.Popen`** with line-buffered stdout, appends to a list, and updates **`st.empty()`** with cumulative output (**“Live Execution Log”**).
5. **Results UX:** Pass/fail message; **`st.session_state.last_run`** stores `out_dir`, absolute `report_path`, `log_path`, `passed`. **`_render_report_log_actions`** offers **Open Report** / **Open Log** using **`_open_local_path`** (`os.startfile` on Windows, `open` / `xdg-open` elsewhere)—**not** raw `file://` links, which often fail from localhost.
6. **Full log expander:** Plain text copy of the full Robot stdout/stderr merge.

### 2.5 Rerenders

**`render_persisted_run_panel`** redraws **Latest test results** from **`last_run`** so report/log buttons survive Streamlit reruns until the next run.

---

## 3. How Python modules interact with Robot Framework

### 3.1 `generate_keyword_mapping.py`

- **Scans:** `Resources/PO/**/*.robot`, `Resources/Common/**/*.robot`.
- **Parses** each file’s `*** Keywords ***` section: keyword names, `[Documentation]`, `[Arguments]`, `[Tags]`.
- **Emits** `keyword_catalog.json` with metadata: `keyword_name`, `arguments`, `documentation`, `natural_language_summary` / inferred descriptions, `source_file`, `generated_at`, counts, etc.
- **Invoked:** CLI `python generate_keyword_mapping.py`; also **from `app.rebuild_keyword_catalog()`** on startup and before each pipeline run.

### 3.2 `ai_bridge.py`

- **Does not execute Robot**; only **generates** `temp_test.robot`.
- **Depends on:** `system_prompt.txt`, `keyword_catalog.json`, `.env` / Streamlit secrets for keys.
- **Exports used by `app.py`:** `hydrate_llm_env`, `analyze_prompt_for_required_fields`, `generate_test_from_prompt`.
- **CLI:** `python ai_bridge.py "natural language prompt"` writes the same generated file (useful without Streamlit).

### 3.3 `run_test.py`

- **Single responsibility:** materialize credentials into **`EnvData.robot`** and return a **complete `robot` command** + output directory.
- **CLI:** `python run_test.py --sandbox_url ... --username ... --password ... --test_path ... [--headless] [-- -- extra robot args]`.
- **`build_robot_run`** is the API used by **`app.py`**; path to suite is typically **`Tests/Generated/temp_test.robot`**.

### 3.4 Robot layout (conceptual)

- **`Resources/Common/GlobalKeywords.robot`** — browser lifecycle, login, app launcher, many UI primitives; **`Begin Web Test` / `End Web Test`** for setup/teardown.
- **`Resources/PO/**`** — domain workflows (e.g. **`SalesPO.robot`**).
- **`Resources/TestData/**`** — data variables (e.g. lead defaults); **`EnvData.robot`** is **runtime-generated** for secrets.
- **`Tests/SmokeTests/`** — reference suites (e.g. sales smoke).
- **Generated suites** live under **`Tests/Generated/`** and use **relative** `Resource` paths like `../../Resources/...` per **`system_prompt.txt`**.

---

## 4. Current Streamlit UI feature set

- **Title and caption** — Plain-English description of generate-then-run behavior.
- **Sidebar — Salesforce credentials** — Sandbox URL, Username, Password (password field masked).
- **Sidebar — Execution mode** — **Background (Fast)** ⇒ headless Chrome; **Watch on Screen (Debug)** ⇒ visible browser + MFA pause variable.
- **Sidebar — AI (LLM)** — Provider radio (**Gemini** / **OpenAI**), sets `LLM_PROVIDER`; optional **session** API key fields with **env sync** so clearing the field restores `.env`/secrets behavior.
- **User prompt** — Large text area for natural language test description.
- **Capabilities cheat sheet** — Expander *“What can I ask for? (Available Capabilities)”* — reads cached **`keyword_catalog.json`**, groups keywords by **`_capability_group_for_source`**, shows arguments + short summaries.
- **Run Automation** — Primary button; triggers validation, optional lead clarification form, then full pipeline.
- **Lead PM clarification form** — Conditional: required Company/Last Name when missing from prompt; optional picklist fields with help text for “blank = random visible option.”
- **Live execution log** — Streaming Robot stdout into a live code block during the run.
- **Pass/fail banner** — After process exit.
- **Open Report / Open Log** — OS default app for HTML artifacts; caption with output folder path.
- **Full log (copy)** expander — Complete merged log text.
- **Latest test results** — Persisted panel from **`session_state.last_run`** with same open buttons and last pass/fail caption.
- **Startup warning** — If initial catalog rebuild failed.

---

## 5. LLM and prompt architecture

### 5.1 `system_prompt.txt`

- Loaded **verbatim** as the **system** (OpenAI) or **system_instruction** (Gemini) content in **`ai_bridge.generate_test_from_prompt`**.
- Defines **role**, **strict rules**: keyword allowlist from catalog only, no raw SeleniumLibrary in generated tests (policy), mandatory **`Begin Web Test` / `End Web Test`**, **`Login To Sandbox`** with the three env-backed variables, **no** redefinition of those variables in generated `*** Variables ***`, import patterns, qualified names (`GlobalKeywords.`, PO prefixes), BC Commercial account guidance, **Leads** flow (prefer **`SalesPO.Open New Lead From Sales App`** + **`Create A New Lead`**, no **Convert View** before New), picklist handling alignment with **`Open Dropdown And Select First Option`** vs explicit **`Select Dropdown Option`**, MFA notes, etc.
- The **user message** from the bridge is separate and contains the **full JSON catalog** plus the **user request** (original or **augmented** from `build_augmented_prompt`).

### 5.2 `keyword_catalog.json`

- **Produced** by **`generate_keyword_mapping.py`**; **embedded in full** in the user message as `## keyword_catalog.json` so the model can **only** choose keywords that exist (in theory).
- **`app.py`** also uses it **only for UI**: **`_load_keyword_catalog_payload`** (`@st.cache_data`, short TTL) feeds **`render_capabilities_cheat_sheet`**.
- Regeneration before generation keeps the LLM view aligned with the latest PO/Common keywords.

### 5.3 Post-processing (guardrails)

Even with a strong system prompt, **`ai_bridge`** applies:

- **`strip_credential_variable_overrides`** — prevents `${globalSandboxTestUrl}` etc. in suite variables from overriding **`EnvData.robot`** (fixes “url None” class failures).
- **`strip_llm_robot_garbage`** — removes trailing ``` fences and bogus `${name}` keyword headers.

---

## 6. Recent updates and fixes (architectural / product)

These reflect recent evolution; verify against git history for exact dates.

- **Lead navigation guidance** — System prompt and smoke-style flows emphasize **New Lead** via list header / **`forceActionLink`**, not **List View conversion** before opening the new-lead dialog; **`SalesPO.Open New Lead From Sales App`** encapsulates Sales app → Leads → New.
- **Picklist robustness** — PM augmentation and docs describe **random visible** options when picklist labels are omitted (**`Open Dropdown And Select First Option`** naming retained for compatibility). Robot-side keywords were extended for portal/modal dropdown handling (see **`GlobalKeywords.robot`** / **`GlobalLocators.robot`** in repo).
- **Credential override stripping** — LLM-generated `*** Variables ***` lines for sandbox URL/username/password are stripped so **first-wins** suite scope does not break **`EnvData.robot`**.
- **LLM garbage stripping** — Markdown fences and invalid `${var}`-as-keyword-name blocks removed from generated source.
- **Sidebar API key lifecycle** — **`_sync_sidebar_api_key`** removes a prior sidebar key from **`os.environ`** when cleared so **`.env`** / secrets can supply the key again without restart confusion.
- **Report opening** — **`_open_local_path`** uses OS handlers instead of relying on **`file://`** from Streamlit for HTML reports/logs.
- **MFA** — Non-headless runs pass **`-v MFA_PAUSE_FOR_MANUAL_COMPLETION:true`** for manual completion in the browser.
- **Catalog freshness** — Regenerate on app first load and before each **Run Automation**; cheat sheet cache invalidated via **`_load_keyword_catalog_payload.clear()`** after regen.

---

## 7. Operational notes for assistants

- **Models:** Default Gemini model name from env (e.g. **`GEMINI_MODEL`**); OpenAI uses **`OPENAI_MODEL`**. **`ai_bridge`** surfaces actionable hints on Gemini **429 / quota** errors.
- **Dry runs:** Use `robot --dryrun` on a suite path to validate syntax without hitting Salesforce.
- **CLI-only path:** `python ai_bridge.py "…"` then `python run_test.py --sandbox_url … --username … --password … --test_path Tests/Generated/temp_test.robot [--headless]`.

---

*Generated for external AI / engineer onboarding. Update this file when `app.py`, `ai_bridge.py`, `run_test.py`, or generation policy change materially.*
