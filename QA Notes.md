# QA Notes

Short guide for QA engineers using the **QA** branch of this repository. For full setup and architecture detail, see **`README.md`**.

---

## Getting this code

```bash
git clone https://github.com/meettheqa-ast/Salesforce-Core-Automation.git
cd Salesforce-Core-Automation
git checkout QA
git pull origin QA
```

Use the **QA** branch for shared test automation work aligned with your team’s validation cycle. **`main`** holds the primary integration line; **`QA`** is the branch to pull for QA-focused drops.

---

## What this project is

**Test Intelligence Platform** — a **Streamlit** web app plus **Robot Framework** test assets for **Salesforce** (UI via Selenium, optional API libraries). Users describe tests in plain English; an LLM generates **`.robot`** suites using a live **keyword catalog** tied to Page Objects and shared keywords. Runs target a Salesforce **sandbox** (URL, user, password supplied at run time — not committed to git).

---

## Main capabilities (what exists)

| Area | What it does |
|------|----------------|
| **Natural language → Robot** | LLM (Gemini or OpenAI) turns descriptions into executable Robot scripts using project keywords and locators. |
| **Keyword catalog** | Scans `Resources/PO` and related paths so generated tests align with real keywords and Page Objects. |
| **Self-healing UI keywords** | Shared Robot keywords retry saves, read Salesforce validation panels, and fill missing modal fields (picklists, text, etc.) to reduce flaky failures. |
| **Data-driven CSV** | CSV upload; when tests use **`@{LEADS_FROM_CSV}`**, **`CsvDataLibrary`** loads **`uploaded_test_data.csv`** and drives **FOR** loops over rows. |
| **Project workspace** | **`Saved_Projects/<name>/`** can hold tests, data, local config, and per-project **`Results/`** (not required for ad-hoc runs). |
| **Parallel runs (Pabot)** | Optional **Pabot** for running multiple test cases in parallel (project suite flows). |
| **In-app results** | Streamlit summarizes pass/fail from **`output.xml`** and shows failure screenshots without opening HTML reports first. |
| **Human-in-the-loop** | Generated code is shown for review before save/run; avoids silent overwrites. |
| **Modular Salesforce tests** | Repo layout supports apps/areas such as **LucyChatBot**, **OmsChatBot**, **Platform**, and **B2B** under **`Tests/`** and **`Resources/`** (Page Objects, env-specific data). |

---

## What QA should verify (suggested checklist)

Use this as a smoke / regression outline when validating a new **QA** build or release.

1. **Environment & app launch**
   - Python **3.10+**, **`pip install -r requirements.txt`**, app starts via **`start_app.bat`** (Windows) or **`streamlit run app.py`**.
   - Salesforce sandbox login works when credentials are provided through the documented flow (see **`README.md`** — **`EnvData.robot`** is generated at run time and is **gitignored**).

2. **AI generation**
   - Plain-English prompt produces a **`.robot`** draft that references real keywords from the catalog where possible.
   - **Review / Save & Execute / Discard** behave as expected (no run without confirmation where applicable).

3. **Execution**
   - A simple generated or bundled test **opens the browser**, reaches Salesforce, and completes a minimal flow (navigation, form, save) against your sandbox.
   - **Results** appear in-app (pass/fail, screenshots on failure). **`Results/`** (ad-hoc) or project **`Results/`** folders receive **`log.html`**, **`output.xml`**, **`report.html`** as configured.

4. **Self-healing / resilience**
   - If Salesforce shows validation errors after Save, healing keywords attempt to fix missing required fields without manual script edits (within supported cases).

5. **CSV-driven flows**
   - With a sample CSV, tests that use **`@{LEADS_FROM_CSV}`** iterate rows and behave consistently with **`CsvDataLibrary`** (headers normalized, row dict access).

6. **Parallel execution (if used)**
   - **Pabot** project suite runs complete without clashes; machine and Salesforce load remain acceptable (see **`README.md`** for process limits).

7. **Multi-module layout (optional, if your team uses these suites)**
   - Under **`Tests/`** and **`Resources/`**, area-specific folders (**LucyChatBot**, **OmsChatBot**, **Platform**, **B2B**) load the right **`*Common.robot`**, **`*Data.robot`**, **`*Env.robot`**, and **PO** files for that product line.

---

## Files QA often opens first

| File / folder | Why |
|---------------|-----|
| **`README.md`** | Install, features, architecture, prompt tips. |
| **`Documentation/Project Details.md`** | High-level folder map for tests and resources. |
| **`Tests/`** | Top-level Robot suites per application area. |
| **`Resources/Common/GlobalKeywords.robot`** | Shared Salesforce UI keywords (including self-heal behavior). |

---

## Security & data

- Do **not** commit **API keys**, **passwords**, or **customer CSVs** with real PII. Follow your org’s policy for sandboxes and LLM keys (typically **`.env`** / Streamlit secrets — see **`README.md`**).

---

*This document is maintained for the **QA** branch audience. For the latest platform behavior, always cross-check **`README.md`** on the same commit.*
