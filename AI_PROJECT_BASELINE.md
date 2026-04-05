# Salesforce AI Automation Architect — Technical Baseline

This document describes the **current** architecture, data flow, and capabilities of the *Salesforce AI Automation Architect* Streamlit application. It is intended for AI assistants and engineers onboarding to the repository.

---

## 1. Repository role

The project turns **natural-language test intents** into **Robot Framework** `.robot` suites that drive **Salesforce Lightning** via **SeleniumLibrary**, using **shared Page Object (PO) keywords** and **GlobalKeywords**—not ad-hoc Selenium in generated tests.

**Entry point (UI):** `streamlit run app.py` (project root).

**UI code layout:** `app.py` holds page config, startup catalog refresh, and **`main_ui()`** orchestration. Supporting modules: **`app_config.py`** (paths, `CLARIFY_SESSION_KEY`, optional `project_manager` / `org_inspector` / `smoke_templates` imports), **`app_csv.py`** (CSV parse, LLM block, scrollable preview, session cache), **`app_catalog.py`** (`rebuild_keyword_catalog`, capabilities cheat sheet), **`app_pipeline.py`** (`run_automation_pipeline`, `run_existing_test`, streaming logs, `build_augmented_prompt`, sidebar API-key sync, persisted results panel).

**Key artifacts:**

| Artifact | Purpose |
|----------|---------|
| `Tests/Generated/temp_test.robot` | Overwritten each successful AI generation; executed by the pipeline |
| `Resources/TestData/EnvData.robot` | Overwritten by `run_test.py` with sandbox URL / username / password |
| `Results/<run_name>/` | `output.xml`, `log.html`, `report.html` per run |
| `keyword_catalog.json` | Machine-readable keyword index for the LLM (regenerated from `.robot` sources) |
| `system_prompt.txt` | Static system / developer instructions for the LLM |
| `Tests/Generated/uploaded_test_data.csv` | Written when a CSV is uploaded and generation runs; loaded at runtime for `@{LEADS_FROM_CSV}` |
| `Libraries/CsvDataLibrary.py` | Robot library used by injected CSV loader keyword |
| `Saved_Projects/<project>/` | Optional per-user workspace: `Tests/*.robot`, `Data/*.csv`, `project.json` (see §3.5) |

---

## 2. End-to-end data flow

### 2.1 Startup (`app.py`)

1. **`st.set_page_config`** — wide layout, expanded sidebar, title *Salesforce AI Automation Architect*.
2. **First session load:** `rebuild_keyword_catalog()` runs once (`st.session_state._catalog_initialized`). This imports `generate_keyword_mapping.main()` and rescans `Resources/PO` and `Resources/Common`, writing `keyword_catalog.json`. Failures are stored in `st.session_state._catalog_init_error` and surfaced in the sidebar.
3. **`main_ui()`** calls **`ai_bridge.hydrate_llm_env()`** so `.env` and optional Streamlit secrets populate `os.environ` for API keys and model/provider settings.

### 2.2 Sidebar: workspace, smoke, credentials, LLM

1. **Workspace / Projects** (if `project_manager` loads): **Active Project** selectbox — `(none — ad-hoc)`, existing projects under **`Saved_Projects/`**, or **+ Create New Project**. Active project is stored in **`st.session_state["active_project"]`**.
2. **Smoke Test App Name** (if `smoke_templates` loads): text field (default `Sales`) used as the **`Launch App`** argument in smoke templates — stored in **`st.session_state["smoke_app_name"]`**.
3. **Salesforce credentials** — Sandbox URL, Username, Password.
4. **Execution mode** — **Background (Fast)** ⇒ headless Chrome; **Watch on Screen (Debug)** ⇒ visible browser + MFA pause variable.
5. **AI (LLM)** — Provider radio (**Gemini** / **OpenAI**), optional session API keys via **`_sync_sidebar_api_key`**.

### 2.3 Main area: prompt, CSV, projects, run

1. **Quick Smoke Tests** buttons (optional): prefill the prompt with lifecycle smoke phrases (Lead / Account / Contact / Opportunity).
2. **User prompt** — natural language; may be **replaced entirely** when smoke intent is detected (§2.4).
3. **Upload Test Data (CSV)** — optional. Parsed text is cached in **`st.session_state["csv_llm_block"]`** for the LLM and for **`analyze_prompt_for_required_fields(..., csv_bytes=...)`** so **Company** / **Last Name** are not re-requested when columns exist. **Preview** uses scrollable HTML (table + JSON), not raw Markdown tables.
4. **Test Case Name** (when a project is active): if set, generated `.robot` (and optional CSV) are **saved** into **`Saved_Projects/<project>/Tests/`** and **`Data/`** via **`project_manager.save_test_to_project`**. **Overwrite** requires an explicit checkbox when the file already exists.
5. **Run Automation** — validates credentials, project overwrite, then runs the logic in §2.4–2.5.

### 2.4 Smoke lifecycle + Org Inspector (before lead PM / pipeline)

1. **`smoke_templates.detect_smoke_intent(prompt)`** (imported in `app.py`; also re-exported from **`ai_bridge.detect_smoke_intent`**) looks for phrases like “smoke test”, “lifecycle smoke”, plus object hints (lead, account, contact, opportunity).
2. If intent matches, **`is_smoke = true`** and the **final prompt** becomes **`get_smoke_prompt(sf_object, app_name, field_context)`** — a long, structured lifecycle template, **not** the user’s original text.
3. **Org Inspector** (optional, `org_inspector.py`): **`OrgInspector.from_credentials(...)`** opens a **headless** Chrome session, logs into the sandbox, then reads **DescribeSObject** JSON **in-browser** (same-origin cookies — no Connected App OAuth). **`get_smoke_field_context`** supplies picklist-oriented text for the template. On failure, a **warning** is shown and **`field_context`** is empty (template still runs with “first option” style guidance).
4. The UI shows an **info** banner: smoke mode **skips** the optional **Lead PM clarification** form (§2.5).

### 2.5 Pre-flight / PM clarification (lead flows — skipped when smoke)

1. **`ai_bridge.analyze_prompt_for_required_fields(prompt, csv_bytes=...)`**:
   - Detects lead-creation intent; infers **Company** / **Last Name** from **prompt text** and/or **CSV column coverage** (`analyze_csv_lead_column_coverage`).
   - Builds **`optional_picklist_fields`** when Lead Status / Salutation / Lead Source are not named in the prompt.
2. If **`should_show_lead_pm_form`** and **not** `is_smoke`, **`clarify_context`** is set and the **Lead test — PM details** form appears.
3. **Submit & Run Automation** on that form calls **`build_augmented_prompt(...)`** then **`run_automation_pipeline`** with CSV bytes and project args as applicable.
4. If no clarification is needed (or smoke), **`run_automation_pipeline`** runs with **`append_csv_data_to_prompt`** merging the CSV block into the final user message.

### 2.6 Automation pipeline (`run_automation_pipeline` in `app.py`)

1. **Refresh catalog:** `rebuild_keyword_catalog()` (and clears **`@st.cache_data`** on `_load_keyword_catalog_payload` after regen).
2. **AI generation:** **`generate_test_from_prompt(final_prompt, csv_bytes=...)`**:
   - Same LLM flow as before; post-processing adds **`strip_hallucinated_csv_variables_from_suite`**, **`inject_csv_loader_into_robot`** when **`LEADS_FROM_CSV`** appears and CSV bytes were passed (writes **`uploaded_test_data.csv`**, adds **`CsvDataLibrary`** + **`Suite Setup`**).
   - Writes **`Tests/Generated/temp_test.robot`**.
3. **Save to project** (if `project_name`, `test_name`, and **`project_manager`** are available): copies generated source (and CSV) into **`Saved_Projects/...`** — failures are **warnings**, not fatal.
4. **Robot execution:** `run_test.build_robot_run(...)` → **`EnvData.robot`**, **`Results/ui_YYYYMMDD_HHMMSS/`**, headless / MFA flags as before.
5. **Live logs, results, persisted panel** — unchanged from prior design.

### 2.7 Rerenders

**`render_persisted_run_panel`** redraws **Latest test results** from **`last_run`** so report/log buttons survive Streamlit reruns until the next run.

---

## 3. How Python modules interact with Robot Framework

### 3.1 `generate_keyword_mapping.py`

- **Scans:** `Resources/PO/**/*.robot`, `Resources/Common/**/*.robot`.
- **Parses** each file’s `*** Keywords ***` section: keyword names, `[Documentation]`, `[Arguments]`, `[Tags]`.
- **Emits** `keyword_catalog.json` with metadata: `keyword_name`, `arguments`, `documentation`, `natural_language_summary` / inferred descriptions, `source_file`, `generated_at`, counts, etc.
- **Invoked:** CLI `python generate_keyword_mapping.py`; also **from `app.rebuild_keyword_catalog()`** on startup and before each pipeline run.

### 3.2 `ai_bridge.py`

- **Does not execute Robot**; only **generates** `temp_test.robot` (and may write **`uploaded_test_data.csv`** next to it when CSV bytes are passed).
- **Depends on:** `system_prompt.txt`, `keyword_catalog.json`, `.env` / Streamlit secrets for keys. Optionally imports **`smoke_templates.detect_smoke_intent`** for **`detect_smoke_intent`** re-export.
- **Exports used by `app.py`:** `hydrate_llm_env`, `analyze_prompt_for_required_fields`, `append_csv_data_to_prompt`, `generate_test_from_prompt` (CSV column coverage is applied inside **`analyze_prompt_for_required_fields`** when **`csv_bytes`** is passed).
- **Post-processing:** `strip_credential_variable_overrides`, `strip_llm_robot_garbage`, `strip_hallucinated_csv_variables_from_suite`, `inject_csv_loader_into_robot` (see §5.3).
- **CLI:** `python ai_bridge.py "natural language prompt"` writes the same generated file (useful without Streamlit).

### 3.3 `run_test.py`

- **Single responsibility:** materialize credentials into **`EnvData.robot`** and return a **complete `robot` command** + output directory.
- **CLI:** `python run_test.py --sandbox_url ... --username ... --password ... --test_path ... [--headless] [-- -- extra robot args]`.
- **`build_robot_run`** is the API used by **`app.py`**; path to suite is typically **`Tests/Generated/temp_test.robot`**.

### 3.4 Robot layout (conceptual)

- **`Resources/Common/GlobalKeywords.robot`** — browser lifecycle, login, app launcher, many UI primitives; **`Begin Web Test` / `End Web Test`** for setup/teardown.
- **`Resources/PO/**`** — domain workflows (e.g. **`SalesPO.robot`**).
- **`Resources/TestData/**`** — data variables (e.g. lead defaults); **`EnvData.robot`** is **runtime-generated** for secrets.
- **`Tests/SmokeTests/`** — reference suites (e.g. lifecycle smokes for Lead, Account, Contact, Opportunity).
- **Generated suites** live under **`Tests/Generated/`** and use **relative** `Resource` paths like `../../Resources/...` per **`system_prompt.txt`**.

### 3.5 Auxiliary Python modules (optional imports in `app.py`)

| Module | Role |
|--------|------|
| **`project_manager.py`** | Creates **`Saved_Projects/<slug>/`** with `project.json`, **`Tests/`**, **`Data/`**; **`save_test_to_project`**, **`list_project_tests`**, **`load_test_source`**, etc. Pure Python (no Streamlit). |
| **`org_inspector.py`** | **`OrgInspector`**: headless Selenium login + in-browser **Describe** REST read for picklist-oriented **`get_smoke_field_context(object)`**. |
| **`smoke_templates.py`** | **`detect_smoke_intent`**, **`get_smoke_prompt(sf_object, app_name, field_context)`** for full-lifecycle smoke prompts. |

If a module fails to import, the UI shows a **warning** and disables that feature (`_HAS_WORKSPACE`, `_HAS_ORG_INSPECTOR`, `_HAS_SMOKE` flags).

---

## 4. Current Streamlit UI feature set

- **Title and caption** — Plain-English description of generate-then-run behavior.
- **Sidebar — Workspace / Projects** — Active project, create new project, or ad-hoc runs.
- **Sidebar — Smoke Test App Name** — Launcher name for smoke templates (e.g. Sales).
- **Sidebar — Salesforce credentials** — Sandbox URL, Username, Password (password field masked).
- **Sidebar — Execution mode** — **Background (Fast)** ⇒ headless Chrome; **Watch on Screen (Debug)** ⇒ visible browser + MFA pause variable.
- **Sidebar — AI (LLM)** — Provider radio (**Gemini** / **OpenAI**), sets `LLM_PROVIDER`; optional **session** API key fields with **env sync** so clearing the field restores `.env`/secrets behavior.
- **Quick Smoke Tests** — One-click prompt presets for Lead / Account / Contact / Opportunity lifecycle wording.
- **User prompt** — Large text area; may be **replaced** when smoke intent is detected (see §2.4).
- **Test Case Name** — When a project is active, names the file saved under **`Saved_Projects/<project>/Tests/`**; overwrite confirmation when the file exists.
- **Upload Test Data (CSV)** — Optional; preview in expander with **horizontal/vertical scroll**; data appended to the LLM request and used for lead-field preflight when applicable.
- **Capabilities cheat sheet** — Expander *“What can I ask for? (Available Capabilities)”* — reads cached **`keyword_catalog.json`**, groups keywords by **`_capability_group_for_source`**, shows arguments + short summaries.
- **Project Tests expander** (when a project is active) — List saved tests, **Re-run**, **View** source.
- **Run Automation** — Primary button; triggers validation, optional lead clarification form (unless smoke), then full pipeline.
- **Lead PM clarification form** — Conditional when **not** smoke: required Company/Last Name when missing from prompt **and** CSV; optional picklist fields with help text for “blank = random visible option.”
- **Smoke mode banner** — Info message when lifecycle smoke template is applied (Lead form skipped).
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
- **`strip_hallucinated_csv_variables_from_suite`** — removes LLM-defined **`@{LEADS_FROM_CSV}`** / **`@{CSV Data}`** lines (and **`Create List` / `...`** continuations) from **`*** Variables ***`**; runtime injection supplies **`@{LEADS_FROM_CSV}`** instead.
- **`inject_csv_loader_into_robot`** — when CSV bytes are present and the generated source references **`LEADS_FROM_CSV`**, adds **`Libraries/CsvDataLibrary.py`**, **`Suite Setup`**, and a loader keyword that reads **`uploaded_test_data.csv`** next to the suite.

---

## 6. Recent updates and fixes (architectural / product)

Verify against git history for exact dates.

- **Workspace / projects** — **`Saved_Projects/`** holds named projects with **`Tests/`** and **`Data/`**; **`project_manager`** persists generated suites and optional CSV. Directory is **gitignored** by default to avoid committing customer data.
- **Smoke lifecycle + Org Inspector** — Natural-language **smoke** intents trigger **`smoke_templates`** prompts; optional **`OrgInspector`** enriches picklist context via in-browser Describe API.
- **CSV data-driven** — Upload augments the LLM; **`CsvDataLibrary`** + injection load **`@{LEADS_FROM_CSV}`**; **CSV column coverage** can skip redundant Lead PM fields; **scrollable** UI preview for wide files.
- **Lead navigation guidance** — System prompt emphasizes **New Lead** via list header / **`forceActionLink`**; **`SalesPO.Open New Lead From Sales App`** encapsulates Sales app → Leads → New.
- **Picklist robustness** — PM augmentation and **`Open Dropdown And Select First Option`** when labels are omitted; **`GlobalKeywords.robot`** / **`GlobalLocators.robot`** extended for Lightning dropdowns and **Attempt Save And Auto-Heal Missing Fields**.
- **Credential override stripping** — LLM-generated `*** Variables ***` lines for sandbox URL/username/password are stripped.
- **Sidebar API key lifecycle** — **`_sync_sidebar_api_key`** clears sidebar-injected keys from **`os.environ`** when fields are emptied.
- **Report opening** — **`_open_local_path`** uses OS handlers instead of **`file://`** for HTML reports/logs.
- **MFA** — Non-headless runs pass **`-v MFA_PAUSE_FOR_MANUAL_COMPLETION:true`**.
- **Catalog freshness** — Regenerate on app first load and before each **Run Automation**.

---

## 7. Operational notes for assistants

- **Models:** Default Gemini model name from env (e.g. **`GEMINI_MODEL`**); OpenAI uses **`OPENAI_MODEL`**. **`ai_bridge`** surfaces actionable hints on Gemini **429 / quota** errors.
- **Dry runs:** Use `robot --dryrun` on a suite path to validate syntax without hitting Salesforce.
- **CLI-only path:** `python ai_bridge.py "…"` then `python run_test.py --sandbox_url … --username … --password … --test_path Tests/Generated/temp_test.robot [--headless]`.

---

*Generated for external AI / engineer onboarding. Update this file when `app.py`, `ai_bridge.py`, `run_test.py`, or generation policy change materially.*
