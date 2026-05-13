# Test Intelligence Platform

**AI-driven Salesforce test automation** for QA engineers and product managers. Describe tests in plain English, review generated [Robot Framework](https://robotframework.org/) scripts, and run them against your sandbox—with optional **data-driven CSV** flows, **parallel suite execution**, and **self-healing** UI keywords that adapt when Salesforce surfaces validation or layout quirks.

---

## Project overview

| Capability | Description |
|------------|-------------|
| **Natural language → Robot** | The app uses an LLM (Cursor SDK by default; Gemini, OpenAI, Anthropic, Groq, Ollama, etc. as fallbacks) with a live **keyword catalog** to generate executable `.robot` suites aligned with your Page Objects and `GlobalKeywords`. |
| **Self-healing UI** | Shared keywords (e.g. `Attempt Save And Auto-Heal Missing Fields`, `Heal Missing Modal Field By Label`, picklist/text fallbacks) react to Salesforce validation panels and missing fields—reducing brittle one-shot scripts. |
| **Data-driven testing** | Upload a CSV; when the generated suite references **`@{LEADS_FROM_CSV}`**, the pipeline injects **`CsvDataLibrary`**, loads **`uploaded_test_data.csv`**, and drives **FOR** loops over real rows. |
| **Jira ingestion + RAG** | Connect a Jira Cloud project, **Sync now** to pull every sprint, issue, and comment into Postgres + pgvector, and the AI sees that context at generation time. See [`docs/jira-integration.md`](docs/jira-integration.md) and [`docs/rag-architecture.md`](docs/rag-architecture.md). |
| **Context files + test data** | Upload CSV / XLSX / PDF / DOCX / MD / TXT to ground generation in your team's own docs. Reusable "test data" tables let prompts address rows by name. See [`docs/context-files.md`](docs/context-files.md). |
| **GitHub script storage + CI** | Push approved `.robot` suites into a connected repo and let GitHub Actions run them on a cron or via `workflow_dispatch`. See [`docs/github-integration.md`](docs/github-integration.md). |
| **Scheduled runs (hybrid)** | Cron-trigger any sprint, story, test case, or tag. Choose `local` (in-process APScheduler) or `github_actions` (cron lives in the repo workflow) per schedule. See [`docs/scheduled-runs.md`](docs/scheduled-runs.md). |
| **Project workspace** | Named projects under `Saved_Projects/` hold tests, data, credentials (local), and **project-scoped** run history for analytics. |
| **In-app results** | After each run, the UI summarizes **pass/fail per test case** from `output.xml` and surfaces failure screenshots—no need to open HTML reports first. |

---

## Getting started

### Prerequisites

- **Python 3.10+** (3.11+ recommended)
- **Node.js 20 LTS** (only required if you want the Cursor SDK as primary LLM; the dispatcher transparently falls back to REST or other providers when Node is absent)
- A **Salesforce sandbox** login (URL, username, password)
- **API keys** for your chosen LLM in `ai_qa_portal/.env`. Recommended primary: `CURSOR_API_KEY` (mint at <https://cursor.com/dashboard/integrations>). Fallback providers: `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, etc. -- the failover chain runs through whichever keys you set. See [`docs/cursor-sdk-integration.md`](docs/cursor-sdk-integration.md) for the full configuration matrix.

### Install dependencies

From the repository root:

```bash
python -m venv .venv
```

**Windows**

```bat
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

**macOS / Linux**

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` (if present) and fill in keys and defaults.

### Launch the app (one-click)

**Windows — `start_app.bat`**

Double-click **`start_app.bat`** in the repo root, or run it from a terminal. The script:

- Prefers **`.venv`** then **`venv`**, activates it when `Scripts\activate.bat` exists  
- Runs **`streamlit run app.py`**  
- **Pauses** when the app exits so error messages stay visible  

**macOS — `start_app.command`**

Double-click **`start_app.command`** (you may need to allow it in **Security & Privacy** the first time), or:

```bash
chmod +x start_app.command
./start_app.command
```

It activates **`.venv`** or **`venv`** when present, then runs Streamlit. The terminal stays open until you press **Enter**.

**Manual launch**

```bash
streamlit run app.py
```

Open the URL shown in the terminal (usually `http://localhost:8501`).

---

## Core features

### Project workspace

- Select an **Active Project** in the sidebar (or work **ad-hoc** without saving).  
- Projects live under **`Saved_Projects/<name>/`** with **`Tests/`**, **`Data/`**, **`Results/`**, and **`config.json`** for sandbox credentials (local desktop use).  
- **Run Entire Project Suite** executes all `*.robot` files under that project’s **`Tests/`** folder, with results under **`Saved_Projects/<name>/Results/run_<timestamp>/`**.

### Human-in-the-loop editor

- After **AI generation**, the app **does not** auto-run or overwrite project files until you confirm.  
- Generated code appears in a large **review text area**; edit locators or variables, then:  
  - **Save & Execute** — writes `Tests/Generated/temp_test.robot` (and saves to the project when a test name is set), then runs Robot.  
  - **Discard** — clears the draft without running.

### CSV data-driven testing

- Upload a **CSV** in the main tab; parsed content is sent to the LLM for **FOR**-loop style tests.  
- When the model uses **`@{LEADS_FROM_CSV}`**, the bridge injects **`Libraries/CsvDataLibrary.py`**, **`Suite Setup`**, and writes **`uploaded_test_data.csv`** next to the generated suite.  
- **`CsvDataLibrary`** loads rows as dictionaries with normalized headers for use in Robot (`${row}[Key]`).

### Parallel execution (Pabot)

- For **Run Entire Project Suite**, enable **Parallel (Pabot)**.  
- Runs use **`pabot`** with **`--testlevelsplit`** and **`--processes 3`** (test-case level parallelism, capped to avoid overloading the machine or Salesforce).

---

## Generation pipeline (3 tiers, no LLM needed for common scenarios)

The portal generates Robot scripts through a layered pipeline so you stop depending on a single LLM (or any LLM at all for the most common cases).

```
prompt -> (1) recipe library -> (2) Cursor SDK / local LLM -> (3) cloud LLM
```

> **Cursor SDK is the new default primary.** When `CURSOR_API_KEY` is set, [`@cursor/sdk`](https://www.npmjs.com/package/@cursor/sdk) (running in a small Node sidecar at `cursor_sdk_sidecar/`) handles every LLM call. On hosts without Node, the dispatcher transparently falls back to the legacy Cursor REST API; on hosts without a Cursor key, behaviour is identical to the pre-change baseline (Ollama if present, then Gemini). See [`docs/cursor-sdk-integration.md`](docs/cursor-sdk-integration.md) for setup, configuration, and failure modes.

### Tier 1 — Recipe library (deterministic, instant, free)

A growing library of parameterized Robot templates for known scenarios (lead routing, lead create, campaign create, more coming). When a prompt matches a recipe at high confidence, the script is rendered from the template with **zero LLM calls** — instant, deterministic, no quota burn.

The UI shows a green badge **"Generated from recipe: \<name\>"** when this fires. Recipes live in `ai_qa_portal/backend/recipes/templates/` and are registered in `ai_qa_portal/backend/services/recipe_library.py`.

To add a recipe: drop a `.robot.j2` template, register a `Recipe` with intent patterns + an extractor, done. No prompt engineering, no fine-tuning.

### Tier 2 — Local LLM via Ollama (free, no quota, no key)

When no recipe matches, the pipeline tries a **local** LLM running on your machine via [Ollama](https://ollama.com). Free forever, no API key, no rate limits, no data leaves the machine.

**One-time setup** (~5 minutes, ~5 GB disk):

```powershell
# Windows (winget):
winget install Ollama.Ollama

# macOS:
brew install ollama

# Linux: https://ollama.com/download
```

Pull a code-tuned model (only needed once; pick ONE):

```powershell
ollama pull qwen2.5-coder:7b      # ~4.5 GB - best code quality at small size (recommended)
ollama pull llama3.1:8b-instruct  # ~4.7 GB - solid general-purpose alternative
ollama pull phi3:medium           # ~7.9 GB - smallest viable, faster on CPU
```

Start the server (auto-runs as a background service on Windows/macOS):

```powershell
ollama serve
```

The backend auto-detects Ollama on startup and adds it to the failover chain. No env vars needed for the default setup. Logs say `Ollama (local LLM) detected at http://localhost:11434; models available: ...` when it's wired up.

**Override defaults** (optional):

```env
OLLAMA_BASE_URL=http://localhost:11434     # if running on a different host/port
OLLAMA_MODEL=qwen2.5-coder:7b              # which model to use
OLLAMA_REQUEST_TIMEOUT_S=120               # bump for large prompts on slow CPUs
```

### Tier 3 — Cloud LLM (Gemini / OpenAI / Groq / Claude / etc.)

Last resort. When a recipe miss AND the local model fails or is unavailable, the pipeline falls through to whichever cloud providers you've configured API keys for. The existing failover chain (`LLM_FAILOVER_ORDER`) handles this — the only change is that `ollama` now slots in front of `gemini` by default.

If you don't install Ollama, this tier becomes the primary — same behavior as before, no migration required.

### How to verify the right tier fired

The generation response surfaces:

- **`used_recipe`** (string) — the recipe name when Tier 1 fired. UI shows a green badge.
- **`provider_switches`** (array) — populated only when Tier 2 or 3 had to fail through provider(s). UI shows an amber banner.
- Both empty / null = the primary tier (recipe or local LLM, whichever was first available) succeeded cleanly.

---

## Prompt engineering guide (for PMs)

Good prompts reduce rework and vague generated steps. Use these habits:

- **Name the Salesforce app** — e.g. *“Open the **Sales** app from the App Launcher, then …”* so navigation matches what users do manually.  
- **State the object explicitly** — *Lead*, *Contact*, *Account*, *Opportunity*—not just “create a record.”  
- **Describe the happy path and assertions** — *“Save and confirm the toast says Success”* or *“Verify the new Lead appears in the list with Last Name X.”*  
- **Picklists** — If you care about a specific value, say the **exact label**; otherwise the stack can use “first visible option” strategies where supported.  
- **Data-driven runs** — Mention that rows come from CSV and what each column represents (e.g. *“Create one Lead per CSV row using Company and Email columns”*).  
- **Smoke / lifecycle** — If your org uses the built-in smoke templates, use clear lifecycle wording (e.g. *“full Lead lifecycle smoke”*) so the app can swap in the right template.

---

## Architecture (for developers)

### Tech stack

| Layer | Technology |
|--------|------------|
| **UI** | [Streamlit](https://streamlit.io/) (`app.py`, `app_pipeline.py`, `app_analytics.py`, `app_reporting.py`, …) |
| **Test runner** | [Robot Framework](https://robotframework.org/) 7.x, [SeleniumLibrary](https://github.com/robotframework/SeleniumLibrary), Chrome |
| **Parallel runs** | [Pabot](https://pabot.org/) (`robotframework-pabot`) |
| **CSV in tests** | Custom **`CsvDataLibrary`** (`Libraries/CsvDataLibrary.py`) — UTF-8-SIG, header normalization |
| **LLM** | **Cursor SDK** primary via `cursor_sdk_bridge.py` + Node sidecar; **Gemini**, **OpenAI**, **Anthropic**, **Groq**, **Ollama**, etc. as fallback chain via `ai_bridge.py` (see `.env` / `LLM_PROVIDER` / `LLM_FAILOVER_ORDER` and [`docs/cursor-sdk-integration.md`](docs/cursor-sdk-integration.md)) |
| **Credentials at run time** | `run_test.py` writes `Resources/TestData/EnvData.robot` before each Robot invocation |

### Self-healing UI loop (under the hood)

“Self-healing” here is **not** magic—it is **deterministic Robot keywords** in `Resources/Common/GlobalKeywords.robot`:

1. On **Save** in a modal, if Salesforce shows the **validation / errors list**, keywords read **field labels** from the UI.  
2. **`Heal Missing Modal Field By Label`** applies strategies: picklist random valid option, **Faker**-backed text, address sub-fields, phone patterns, etc.  
3. **`Attempt Save And Auto-Heal Missing Fields`** retries Save up to a small fixed number of attempts while the validation panel is addressed.

Together with **fallback locators** (e.g. scroll-into-view, dropdown open retries), this reduces one-off failures when labels or layouts shift slightly—within the limits of Selenium and SLDS.

### Repository map (short)

```
app.py                 # Streamlit entry, tabs, sidebar branding
app_pipeline.py        # AI run, Robot subprocess, project suite, pending editor
app_reporting.py       # In-app run summary from output.xml + screenshots
run_test.py            # EnvData + robot/pabot CLI builder
ai_bridge.py           # LLM generation, CSV injection, catalog context
project_manager.py     # Saved_Projects CRUD
Resources/Common/      # GlobalKeywords.robot (self-heal, Salesforce flows)
Tests/Generated/       # temp_test.robot (ad-hoc / review flow)
.streamlit/config.toml # Theme & app config
```

---

## Documentation & support

- Deeper technical notes: **`AI_PROJECT_BASELINE.md`** (architecture, data flow, AI bridge).  
- **Robot** artifacts: `Results/` (default ad-hoc runs) and **`Saved_Projects/<project>/Results/`** (project suite runs).  
- **v1.0** — Test Intelligence Platform: branded UI, workspace, analytics, Pabot, human-in-the-loop generation, and in-app run summaries.

---

*Internal tool — follow your organization’s policies for credentials, API keys, and customer data.*
