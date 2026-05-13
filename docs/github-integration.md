# GitHub integration

The portal connects to GitHub for **two** things:

1. **Script storage / sync** — push approved `.robot` suites + companion
   CSVs into a repo so they live alongside any other CI you have.
2. **CI execution** — write a portal-owned GitHub Actions workflow into
   the repo, then trigger runs via `workflow_dispatch` from the
   scheduler.

GitHub as a context source (issues / PRs into RAG) is intentionally
out of scope for v1.

## Auth

**Preferred**: a GitHub App configured at the org level.

1. Create the app: <https://github.com/settings/apps/new>. Permissions:
   * Repository contents: read & write
   * Actions: read & write
   * Workflows: read & write
   * Metadata: read
   Subscribe to events: `workflow_run`, `check_run`, `installation`.
   Set the webhook URL to `${API_BASE_URL}/webhooks/github`.
2. Set `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (PEM contents),
   `GITHUB_APP_INSTALL_URL`, and `GITHUB_WEBHOOK_SECRET` in the
   backend's `.env`.
3. Visit `Settings → Integrations` as an admin, click **Install GitHub
   App**, install on the GitHub org. Paste the installation id back
   into the portal to finalise.

**Fallback**: per-project Personal Access Token. From
`Projects → {name} → Integrations → GitHub`, paste the org / owner login
and a PAT with `repo` + `workflow` scopes.

Both paths store an encrypted webhook secret unique to that connection;
the plaintext is shown once on creation so you can paste it into
GitHub's webhook configuration.

## Workflow YAML

When you connect a repo, the portal writes
`.github/workflows/portal-automation.yml`. The template is at
[`ai_qa_portal/backend/services/github_templates/portal_automation.yml.j2`](../ai_qa_portal/backend/services/github_templates/portal_automation.yml.j2).
It supports:

* `workflow_dispatch` for manual / portal-triggered runs.
* `schedule:` cron entries, one per portal schedule whose runner is
  `github_actions` (regenerated every time you create/update such a
  schedule).

Each run resolves persona credentials from repo secrets named
`PERSONA_<USER>_PASSWORD` (uppercased + `_`-flattened from the
username). Set these via the portal when connecting a persona.

## Webhook flow

1. Workflow finishes -> GitHub POSTs `workflow_run.completed` to
   `${API_BASE_URL}/webhooks/github`.
2. The portal verifies the HMAC-SHA256 signature against every stored
   connection's webhook secret (constant-time comparison).
3. On match, the linked `schedule_runs` row is updated with the
   conclusion (`passed` / `failed` / `cancelled`) and the
   `html_url` to the workflow run.

The workflow's "Notify portal" step also POSTs to the same endpoint as
a fallback when the App's webhook isn't reachable (e.g. during local
dev without a tunnel). That fallback path doesn't carry a GitHub
signature, but it has the `schedule_run_id` from the run inputs.

## Public URL requirement

GitHub needs to reach your backend's `/webhooks/github` endpoint over
HTTPS. For production, that's the same domain as the rest of the API.
For local dev, run a tunnel (smee.io, ngrok, Cloudflare Tunnel) and
point the App's webhook URL at it. Without a reachable endpoint, the
portal falls back to polling `get_workflow_run` after each manual
trigger.
