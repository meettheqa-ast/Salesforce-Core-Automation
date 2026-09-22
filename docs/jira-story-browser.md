# Jira Stories (in-app browser)

A lightweight, session/Streamlit-native way to pull a Jira user story straight
into the **AI Test Agent** prompt — no Postgres, no sync job. This is separate
from the heavier **Jira ingestion + RAG** pipeline documented in
[`jira-integration.md`](jira-integration.md), which lives in
`ai_qa_portal/backend` and mirrors entire Jira projects into pgvector for
retrieval-augmented generation. Use this page when you just want to browse a
handful of stories and generate a test from one, without standing up the
FastAPI portal.

Ported from the "Browse user stories" feature of the Test-artifact-agent
portal (`src/lib/jira*.ts`, `BrowseUserStoryModal.tsx`) onto this repo's
Python/Streamlit stack — see [`jira_bridge.py`](../jira_bridge.py).

## Where it lives

* **Sidebar → Jira Stories** — search, view, and pull a story into the prompt.
* **Project Settings** (⚙️ next to the active project) — save the Jira
  connection so it's remembered per project (`config.json`, same file as
  Salesforce credentials).

## Connecting

1. Open **Project Settings** for the active project (or use the **Jira
   Stories** page's own "Jira connection" panel for a session-only, ad-hoc
   connection).
2. Fill in:
   * **Jira Site URL** — e.g. `https://yourcompany.atlassian.net`.
   * **Project Key** — e.g. `QA`. Used as the default search scope.
   * **Atlassian Account Email** + **Jira API Token** — Jira Cloud auth
     (generate a token at
     [id.atlassian.com → Security → API tokens](https://id.atlassian.com/manage-profile/security/api-tokens)).
     Leave **Email** blank to instead send the token as a `Bearer` header
     (Jira Server / Data Center Personal Access Token, or Zephyr Scale —
     the same scheme `publish_results_to_zephyr` in `app_reporting.py`
     already uses for pushing results back to Jira).
3. Click **Test Connection** — calls `/rest/api/3/myself` and reports the
   signed-in Jira account.

## Browsing & searching

Type any of the following into the search box on the **Jira Stories** page:

* **Nothing** — lists recent (`updated >= -90d`) non-Epic issues in the
  configured project key.
* **A project key** (e.g. `QA`) — same recent-issues list, scoped to that
  project.
* **A keyword** (e.g. `login error`) — `summary ~ "login error"` scoped to
  the configured project.
* **A full JQL query** (e.g. `project = QA AND status = "To Do"`) — run
  as-is.

Results show key, issue type, status, and summary. Click **View** to load
the full issue.

## Using a story to generate a test

On a story's detail view:

* **Description**, **Comments**, and **Attachment names** (content isn't
  downloaded/extracted — only filenames are listed) are shown inline.
* If the issue is an **Epic**, a **Load child stories** button re-runs the
  search scoped to that Epic's children (tries the modern `parent =` field,
  `childIssuesOf()`, the Agile REST API, and the legacy `"Epic Link"` field,
  merging whatever each org supports).
* **Use in AI Test Agent** builds a Markdown context block (summary +
  description + comments + attachment list) and:
  1. Sends it to the **Home** page's prompt as `_pending_prompt`.
  2. Pre-fills **Jira user story ID** with the issue key.
  3. Switches the sidebar to **Home** so you land straight in the prompt
     editor, ready to click **Generate Script**.

## What the User Story ID field does downstream

The **Jira user story ID** field on the Home page (next to the prompt) is
plumbed into the existing pipeline in `app_pipeline.py`:

* The generated Robot Framework test is tagged with the story ID (e.g.
  `US-QA-123`) so results can be traced back to the story.
* If you later enable the (currently demo-hidden) Git auto-commit and
  Jira/Zephyr result-sync blocks in `app_pipeline.py`, they already key off
  this same value — no extra wiring needed.

## Limitations / follow-ups

* Attachment **content** isn't downloaded or text-extracted (unlike the
  source TS feature, which pulls PDFs/text files into the prompt). Only
  filenames are shown. Add this in `jira_bridge.fetch_issue` if needed.
* Search results aren't cached across reruns beyond the current session —
  re-searching re-hits Jira.
* No issue *creation* (bugs/tasks) is ported — only browsing/reading.
