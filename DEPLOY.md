# Deploying the AI QA Portal

The portal is two services:

| Service | Where it runs | Why |
|---------|---------------|-----|
| **Frontend** (Next.js, `frontend/`) | **Vercel** (Hobby tier is enough) | Best free Next.js host; static + edge runtime, no persistent state needed. |
| **Backend** (FastAPI + Robot Framework + RF-MCP, repo root + `ai_qa_portal/`) | **Fly.io Machines** + a 3 GB **Volume** | Needs to spawn `robot` + Chromium subprocesses, run RF-MCP on `:8765`, write to disk. Vercel cannot do any of that. Fly free tier stays always-on, no cold-start. |

This document covers the three stages: local prod-like, Fly deploy, Vercel deploy.

---

## Architecture

```
Browser
  |
  v
Vercel (Next.js)  --(NEXT_PUBLIC_API_URL, HTTPS)-->  Fly Machine (FastAPI :8000)
                                                       |-- subprocess: robot + chromium
                                                       |-- subprocess: RF-MCP :8765
                                                       `-- volume mount: /data
                                                              |-- Results/
                                                              |-- Saved_Projects/
                                                              |-- ai_qa_portal_data/
                                                              `-- ai_qa_portal_outputs/
```

---

## 0. One-time prerequisites

- A **GitHub repo** with this code (you already have one).
- A free **Vercel** account connected to that repo.
- A free **Fly.io** account; install `flyctl` (`https://fly.io/docs/flyctl/install/`).
- **Docker Desktop** (or Docker Engine) installed locally.
- A **Postgres database with the `vector` extension** (Postgres 14+). Local dev gets one from `docker compose up postgres`; production wants a managed instance (Fly Postgres + `CREATE EXTENSION vector`, Neon, Supabase, or RDS with the extension enabled).
- Real values for the secrets you intend to use:
  - `FERNET_KEY` (generate once, never change after personas are saved):
    ```bash
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ```
  - `DATABASE_URL` — example: `postgresql+psycopg://portal:portal@db:5432/portal`. Leaving this empty falls back to legacy SQLite under `DATA_DIR/users.db`, which still works for solo dev but disables Jira/GitHub/RAG/scheduler features.
  - LLM API keys. **Recommended primary: `CURSOR_API_KEY`** (mint at <https://cursor.com/dashboard/integrations>) — the backend's `_default_primary_provider()` picks Cursor as the primary as soon as this is set. Fallbacks: `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, etc. -- the failover chain runs through whichever keys you set. See [`docs/cursor-sdk-integration.md`](docs/cursor-sdk-integration.md).
  - `OPENAI_API_KEY` (or `OPENAI_EMBEDDINGS_API_KEY`) for the default OpenAI embedding model used by the RAG index. Override with `EMBEDDING_PROVIDER=ollama` or `EMBEDDING_PROVIDER=gemini` if you'd rather not depend on OpenAI for embeddings.
  - `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` for the optional org-wide Jira connection.
  - `GITHUB_APP_ID` / `GITHUB_APP_PRIVATE_KEY` / `GITHUB_WEBHOOK_SECRET` for the optional GitHub App integration. Per-project PAT fallbacks live in the database, not env.

### Postgres + pgvector quick-start (local)

```bash
# Brings up Postgres 16 with pgvector pre-installed, plus the backend
# wired to it via DATABASE_URL. The first boot creates the `vector`
# extension automatically.
docker compose up -d postgres

# Apply migrations against the freshly-provisioned DB:
DATABASE_URL='postgresql+psycopg://portal:portal@localhost:5432/portal' \
    python -m alembic -c ai_qa_portal/alembic.ini upgrade head

# If you have an existing SQLite users.db, copy its rows over:
DATABASE_URL='postgresql+psycopg://portal:portal@localhost:5432/portal' \
    python scripts/migrate_sqlite_to_postgres.py
```

After this, start the backend normally; it will detect `DATABASE_URL`, skip the SQLite fallback, and use Postgres for users + RBAC + runs + every new Jira/GitHub/RAG/scheduler table.

---

## 0. When the demo breaks: tunnel recovery

While we're on Cloudflare *quick* tunnels (no named tunnel yet), the URL dies a
few times a day. Two helper scripts handle it:

```powershell
# 5-second non-destructive health check. Run this to confirm what's broken
# before doing anything else.
.\scripts\check-tunnel.ps1

# Full recovery: kills the dead tunnel, starts a fresh one, updates Vercel's
# NEXT_PUBLIC_API_URL, and redeploys production. Takes ~60 seconds.
.\scripts\restore-tunnel.ps1
```

`restore-tunnel.ps1` is idempotent and safe to re-run. After it finishes, hard-
refresh the Vercel page (`Ctrl+Shift+R`) and the demo is back.

Symptom that means "run restore-tunnel.ps1":

> **Failed to fetch. Is the API running at https://...trycloudflare.com? (Set
> NEXT_PUBLIC_API_URL if needed.)**

Once we move to a named tunnel (requires a domain on Cloudflare -- see
"Permanent fix" at the bottom of this doc), this whole section becomes moot.

---

## 1. Local prod-like (docker compose)

This builds the same image that goes to Fly and runs it against a local volume mount, so you can validate before pushing.

```bash
# Backend only:
docker compose up --build

# Backend + Next.js dev server (with hot reload) on port 3000:
docker compose --profile web up --build
```

Smoke test:

```bash
curl http://localhost:8000/health           # {"status":"ok"}
curl http://localhost:8000/api/runs/latest  # list of runs (empty on first boot)
curl http://localhost:8000/api/mcp/health   # {"running": true, ...} once first stepwise call boots RF-MCP
```

Generated files land in `./_local_data/`.

---

## 2. Backend on Fly.io

```bash
# Pick or create the Fly app.
flyctl launch --no-deploy --copy-config        # accept defaults; pick region near you (e.g. iad, lhr, sin)

# Persistent volume (3 GB free tier).
flyctl volumes create data --size 3 --region <your-region>

# Secrets — set every key you would otherwise put in .env.
# Recommended baseline: Cursor SDK as primary, plus one or more
# fallback providers so the failover chain can route around quota /
# rate-limit issues without operator intervention.
flyctl secrets set \
  FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  LLM_PROVIDER=cursor \
  CURSOR_API_KEY=cursor_... \
  GEMINI_API_KEY=... \
  LLM_FAILOVER_ORDER=cursor,gemini,openai,anthropic,groq

# Stepwise reliability knobs (recommended production baseline):
# MCP_PLAN_TIMEOUT_S=90
# MCP_INIT_TIMEOUT_S=180
# MCP_STEP_TIMEOUT_S=60
# MCP_BUILD_TIMEOUT_S=60
# LLM_REQUEST_TIMEOUT_S=60
# Keep 0 in normal UX (automatic fallback available). Set 1 only while
# diagnosing failures so users see explicit MCP errors.
# MCP_STEPWISE_NO_FALLBACK=0
# Form-healing runtime caps:
# HEAL_MAX_ATTEMPTS_PER_SAVE=3
# HEAL_MAX_PER_TEST=6
# HEAL_MAX_LLM_CALLS=1
# HEAL_TIMEOUT_S=60
# HEAL_PATTERN_LOCALE=en

# Deploy.
flyctl deploy

# Verify.
flyctl status                                  # find the public hostname
curl https://<your-app>.fly.dev/health         # {"status":"ok"}
```

The image already sets `RFMCP_HOST=127.0.0.1`, `RFMCP_PORT=8765`, and points all writable paths into `/data` (the mounted volume). RF-MCP boots inside the same machine on first stepwise call.

### Free → paid upgrade (one command)

```bash
flyctl scale vm shared-cpu-2x --memory 1024
```

Free tier ships with `shared-cpu-1x / 512 MB`, which is fine for demo runs. Bigger Salesforce suites with several Chromium tabs will need more RAM.

### Restrict access (optional)

If you don't want the API publicly callable:

- Use Fly's `[[services.http_options.headers]]` to require a custom header that your frontend sends.
- Or put a reverse-proxy with HTTP basic auth in front.

---

## 3. Frontend on Vercel

In the Vercel project for `sf-core-automation.vercel.app`:

1. **Project Settings → Build & Development Settings → Root Directory**: set to `frontend`.
2. **Project Settings → Environment Variables** (apply to Production, Preview, Development):
   - `NEXT_PUBLIC_API_URL` = `https://<your-fly-app>.fly.dev`
3. Push to `main` (or click **Redeploy** on the latest deployment) to make it Production.

Vercel auto-detects Next.js from `frontend/vercel.json`. Each push to `main` ships to production; PR branches get preview URLs that the backend's CORS regex (`*.vercel.app`) already allows.

---

## 4. Authentication (Google OAuth)

Phase 1 of the auth model is now in. Every API endpoint except `/health` and `/`
requires a valid Google ID token. Sign-in is restricted to a single Google
Workspace domain (default `astounddigital.com`).

**Auth model in one paragraph.** The frontend (NextAuth on Vercel) runs the
Google OAuth flow and stashes the **raw Google ID token + a Google
refresh_token** in the encrypted NextAuth session cookie. The client fetches
the (silently-refreshed) ID token from `/api/auth/jwt` and forwards it to the
backend as `Authorization: Bearer <token>`. The backend verifies the token
directly against [Google's JWKS](https://www.googleapis.com/oauth2/v3/certs),
checks the audience (`GOOGLE_CLIENT_ID`), the issuer (`accounts.google.com`),
the email domain, and the `hd` (hosted-domain) claim. **No shared secret
between Vercel and the FastAPI backend** -- the trust anchor is Google itself.

**Silent refresh.** Google ID tokens live ~1 hour; NextAuth's `jwt` callback
in [`frontend/src/auth.ts`](frontend/src/auth.ts) trades the stored
`refresh_token` for a fresh `id_token` whenever the current one is within 60s
of expiry. The user only logs out when they click sign-out, when they revoke
access from their Google account, or when the NextAuth session cookie's outer
30-day window elapses. Laptop lock / sleep / overnight idle no longer log
users out.

### 4a. Google Cloud Console (one-time, ~3 min)

1. Go to https://console.cloud.google.com/apis/credentials and pick (or create)
   a project named e.g. `ai-qa-portal-prod`.
2. **OAuth consent screen** -> User Type **Internal** (so only your Workspace
   can sign in) -> fill App name, support email, dev contact -> Save.
3. **Credentials** tab -> **+ Create Credentials** -> **OAuth client ID**:
   - Application type: **Web application**
   - Name: `AI QA Portal`
   - **Authorized redirect URIs** -- add ALL of these (one per line):
     - `http://localhost:3000/api/auth/callback/google` (local dev)
     - `https://sf-core-automation-umber.vercel.app/api/auth/callback/google` (prod)
     - Plus any preview / custom domains you want to support.
4. **Create** -> copy the **Client ID** and **Client secret**.

> Web-application OAuth clients return a `refresh_token` automatically when
> the auth request sets `access_type=offline` and `prompt=consent` (the
> frontend does both -- see `frontend/src/auth.ts`). No extra Cloud Console
> setting is required.

### 4b. Backend env vars

Local `.env` (or `flyctl secrets set ...`):

```
GOOGLE_CLIENT_ID=<paste the Client ID from 4a; MUST match the frontend value>
ALLOWED_EMAIL_DOMAIN=astounddigital.com
INITIAL_ADMINS=m.sheth@astounddigital.com
AUTH_DISABLED=false
```

The backend creates a SQLite DB at `${DATA_DIR}/users.db` on first boot. On
Fly the mounted volume keeps it persistent.

### 4c. Frontend env vars (Vercel)

Project Settings -> Environment Variables (set for **all** environments):

```
AUTH_SECRET=<openssl rand -hex 32>            # encrypts the NextAuth session cookie
NEXTAUTH_URL=https://sf-core-automation-umber.vercel.app
GOOGLE_CLIENT_ID=<same value as backend>
GOOGLE_CLIENT_SECRET=<from console>
ALLOWED_EMAIL_DOMAIN=astounddigital.com
NEXT_PUBLIC_AUTH_DISABLED=false
```

`AUTH_SECRET` here is for NextAuth's own session-cookie encryption only -- the
backend never sees it. Then **Redeploy** (Deployments -> latest -> ... ->
Redeploy with cache off).

### 4d. Migrate existing data to the admin

After deploying the auth changes, claim every existing project / persona /
user-story for the bootstrap admin so they don't disappear from the UI:

```
docker compose exec backend python scripts/seed_admin_and_claim.py m.sheth@astounddigital.com
```

The script is idempotent and safe to re-run.

### 4e. Demo / pre-OAuth bypass

While Google OAuth isn't yet wired, both sides ship a feature flag that
disables the entire auth path:

- Backend: `AUTH_DISABLED=true` in `.env` -> every request resolves to a
  synthetic `dev@local` admin user.
- Frontend: `NEXT_PUBLIC_AUTH_DISABLED=true` in Vercel env -> middleware
  skips the `/login` redirect and the navbar hides the user menu.

Flip both to `false` once 4a-4c are complete.

### 4f. Things to know

- **The first user to log in** becomes admin only if their email is listed in
  `INITIAL_ADMINS`. Otherwise they are a regular user (Phase 1 = sees only
  their own data).
- **Google ID tokens last ~1 hour, but the user does NOT log out every hour.**
  NextAuth's `jwt` callback transparently refreshes the ID token against
  Google's `/token` endpoint using the stored `refresh_token` (captured at
  initial sign-in because the OAuth request uses `access_type=offline` +
  `prompt=consent`). `/api/auth/jwt` only returns 401 when refresh itself
  fails -- typically because the user revoked access at
  https://myaccount.google.com/permissions, or the 30-day NextAuth session
  cookie window has elapsed. In either case the frontend lands cleanly on
  `/login?reason=expired`.
- **One-time consent for existing users.** Users who signed in BEFORE the
  refresh-token rollout do not have a stored `refresh_token` (the original
  OAuth request did not ask for offline access). They will be bounced to
  `/login` exactly once after deploy and will see Google's consent screen on
  re-sign-in to grant offline access. New users see the consent screen once
  on first sign-in and never again.
- **SSE endpoints** (`/api/runs/execute/stream`, `/api/generate/mcp-stepwise/stream`)
  accept the token via `?token=...` query param because EventSource cannot send
  custom headers. Same for `/api/runs/.../file/...` and `/.../bundle.zip` --
  they're embedded in `<a href>` and `<img src>` so the helper appends the
  cached token to the URL.
- **`POST /api/runs`, `/run/user-story/{id}`, `/run/tag/{name}`** restrict the
  persona resolver to personas owned by the current user (or all, if admin),
  so a logged-in user cannot trigger a run with someone else's encrypted SF
  credentials by guessing a `persona_id`.
- **`POST /personas`, `POST /orgs`, `POST /user-stories`** verify that the
  caller owns the parent `project_id`; cross-user attachment returns 403.
- **Run history is shared** across all logged-in users in Phase 1. Per-user
  filtering of runs lands in Phase 2 alongside project memberships.

---

## 5. CORS gotchas

`ai_qa_portal/backend/main.py` allows:

- This project's deployments only -- the regex matches `sf-core-automation*.vercel.app`,
  not any random Vercel app on the internet.
- `http(s)://localhost` and `http(s)://127.0.0.1` for dev.
- Any origins listed in `CORS_ORIGINS` or `EXTRA_CORS_ORIGINS` (comma separated) for custom domains.

If you attach a custom domain (e.g. `qa.acme.com`) to Vercel, set:

```bash
flyctl secrets set EXTRA_CORS_ORIGINS=https://qa.acme.com
```

then `flyctl deploy`.

If you ever rename the Vercel project, update the regex in `main.py` to match
the new project slug.

---

## 6. SSE behind proxies

The two streaming endpoints (`/api/runs/execute/stream`, `/api/generate/mcp-stepwise/stream`) already send `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Fly's edge respects those. If you put a custom Nginx in front, also add:

```
proxy_buffering off;
proxy_read_timeout 1h;
```

---

## 6.5. Test case CSV / Excel import

The import wizard lets users bulk-load test cases from a spreadsheet
without going through the AI generation pipeline.

### Routes

- `POST /api/imports/test-cases/parse`   (multipart upload, returns preview + suggested mapping)
- `POST /api/imports/test-cases/commit`  (persists rows under target story)
- `GET  /api/imports?project_slug=...`   (history list)
- `GET  /api/imports/{batch_id}`         (single batch + failed-rows detail)
- `POST /api/imports/{batch_id}/rollback` (hard-deletes every TC the batch created; lead+ only)

### Env knobs

```bash
# Hard caps applied in ai_qa_portal/backend/services/import_parser.py.
# Reduce to tighten resource use on lean tiers; bump on dedicated hosts.
IMPORT_MAX_ROWS=10000          # /parse cap
IMPORT_MAX_FILE_BYTES=33554432 # 32 MB
IMPORT_MAX_CELL_BYTES=65536    # per-cell truncation threshold

# /commit cap. Lower than the parse cap because the JSON-store writes
# happen per row; very large batches should be split client-side.
IMPORT_COMMIT_MAX_ROWS=1000
IMPORT_COMMIT_CHUNK=100        # chunk size for the per-row write loop
```

### Storage

- The uploaded file is persisted to `{data_dir}/imports/{batch_id}{ext}`
  between `/parse` and `/commit`. It is deleted on successful commit.
- The `import_batches` SQL table (alembic revision `0006_import_batches`)
  retains the batch row indefinitely, including `failed_rows_json` so
  partial-imports remain auditable.
- Each created TestCase is tagged with
  `external_source = "csv_import:<batch_id>"` so the rollback endpoint
  can find every row a given batch produced.

### Future-readiness (Jira / Zephyr / Xray / TestRail / Azure DevOps)

The same `ImportBatch` table + `external_id` / `external_source` /
`external_payload` columns on `TestCase` are reused by future external
syncs. To wire a new source, add a parser module that returns the same
`ParsedFile` shape (`columns + rows`), register a new `source_kind`
value, and reuse the mapping + dedupe + commit pipeline unchanged.

### Smoke

```
curl -F "project_slug=demo" -F "file=@tests.csv" \
     https://<fly-app>.fly.dev/api/imports/test-cases/parse
```

Then post the returned `batch_id` + a confirmed mapping to `/commit`.

## 6.6. AI Prompt Management

Test-case generation, script building, healing, and stepwise planning
all consult a SQL-backed prompt registry instead of inline string
constants. Users can edit the prompts that drive their own generations
without affecting anyone else.

### Routes

- `GET    /api/prompts/categories`              -- categories + placeholder allow-lists
- `GET    /api/prompts`                          -- list templates (system seeds + clones)
- `GET    /api/prompts/{id}`                     -- template + versions + active overrides
- `GET    /api/prompts/{id}/versions/{n}`        -- single version body
- `POST   /api/prompts`                          -- create a user / project / org clone
- `POST   /api/prompts/{id}/versions`            -- append immutable version (save)
- `POST   /api/prompts/{id}/activate`            -- pin a version as active for a scope
- `POST   /api/prompts/{id}/reset`               -- drop a scope's override (fall back)
- `POST   /api/prompts/{id}/preview`             -- render against mock context (no LLM)
- `POST   /api/prompts/{id}/dry-run`             -- full LLM round-trip + parse, not persisted
- `DELETE /api/prompts/{id}` (`?permanent=true` admin) -- soft / hard delete
- `GET    /api/prompts/audit`                    -- prompt_usage_audit feed

### Resolution chain

```
user override → project override → org override → system seed (baked .md)
```

Sparse rows -- a missing override is "no row found" and the resolver
falls through. The system seed is whichever `is_system=true` template
for the category has the OLDEST `created_at` (deterministic across
reboots; admins flip the org default by activating a different
template at `scope='org'`).

### Env knobs

```bash
# Master switch. When OFF, ai_qa_portal.backend.prompts.assembler
# returns the legacy inline tail bodies. When ON, every call site
# (test_case_generator, test_case_script_builder, recording_translator,
# etc.) routes through the registry resolver. Default OFF -- flip to
# 1/true/yes/on to enable.
PROMPT_REGISTRY_ENABLED=1

# Hard cap on prompt body bytes. Editor + /versions endpoint refuse
# saves above this. Default 200 KB.
PROMPT_MAX_BODY_BYTES=204800
```

### Schema

Alembic revision `0007_prompt_management` creates five tables:

- `prompt_templates`    -- one row per named template (system + clones)
- `prompt_versions`     -- immutable history; every save = new row
- `prompt_overrides`    -- sparse (scope, scope_id, category) → version pointer
- `prompt_usage_audit`  -- one row per LLM generation with model + tokens + latency
- `prompt_meta`         -- single-row `cache_epoch` for resolver invalidation

### Seeds

Seed bodies live as `.md` files under `ai_qa_portal/backend/services/prompt_seeds/`.
On every boot the seeder hashes each file; new files become a system
template + version 1, changed files append a new system version
(release-upgrade transparent). User overrides are never touched.

Shipped seeds (Phase 1):

- `test_case_drafter.md` -- legacy JSON drafter (OOTB default)
- `test_case_drafter_structured_sf.md` -- Salesforce Structured QA (markdown table)
- `test_case_drafter_zephyr_enterprise.md` -- Enterprise Zephyr QA (markdown table)
- `script_builder.md` / `quick_robot.md` / `stepwise_planner.md` / `healer.md`
- `recording_translator.md`

### Security

- Jinja2 SandboxedEnvironment blocks `__class__` walks, `os`,
  `subprocess`, and attribute access on disallowed types.
- Server-side body cap (`PROMPT_MAX_BODY_BYTES`, default 200 KB).
- Placeholder allow-list per category; undeclared `{{ var }}` rejected
  at save with a clear 400 message.
- Mutations on `scope='user'` require `current_user.id == scope_id`.
- Mutations on `scope='project'` require `ProjectRole.lead`.
- Mutations on `scope='org'` and any system template require `is_admin`.
- Every create / version / activate / reset / delete logs to the
  shared `audit_log` via `services.audit.log_action`.

### Smoke

```bash
# List system seeds
curl -H "Authorization: Bearer $TOKEN" https://<fly-app>.fly.dev/api/prompts

# Preview the default drafter against a mock story (no LLM call)
curl -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"context":{"story":{"title":"Login","description":"User signs in"},"qa_mode":"salesforce"}}' \
  https://<fly-app>.fly.dev/api/prompts/<template_id>/preview
```

## 7. Smoke checklist after a fresh deploy

```
curl https://<fly-app>.fly.dev/health
curl https://<fly-app>.fly.dev/api/runs/latest
curl https://<fly-app>.fly.dev/api/mcp/health
```

Then in the browser at `https://<vercel-app>.vercel.app`:

1. Generate page loads and the workspace dropdowns populate.
2. Quick Generate produces a script.
3. **Run** in headless mode runs Robot inside the Fly machine and the live log streams in.
4. The Run dashboard at `/runs/<run>` shows real KPIs + screenshots.
5. **Bundle .zip** download works.
