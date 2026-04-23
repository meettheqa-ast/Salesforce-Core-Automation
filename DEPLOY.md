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
- Real values for the secrets you intend to use:
  - `FERNET_KEY` (generate once, never change after personas are saved):
    ```bash
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ```
  - LLM API keys you actually use (`GEMINI_API_KEY`, `OPENAI_API_KEY`, etc).

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
flyctl secrets set \
  FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  LLM_PROVIDER=gemini \
  GEMINI_API_KEY=...

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
requires a valid NextAuth session JWT. Sign-in is restricted to a single Google
Workspace domain (default `astounddigital.com`).

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

### 4b. Backend env vars

Local `.env` (or `flyctl secrets set ...`):

```
NEXTAUTH_SECRET=<openssl rand -hex 32>
ALLOWED_EMAIL_DOMAIN=astounddigital.com
INITIAL_ADMINS=m.sheth@astounddigital.com
```

The backend creates a SQLite DB at `${DATA_DIR}/users.db` on first boot. On
Fly the mounted volume keeps it persistent.

### 4c. Frontend env vars (Vercel)

Project Settings -> Environment Variables (set for **all** environments):

```
AUTH_SECRET=<same value as backend NEXTAUTH_SECRET>
NEXTAUTH_URL=https://sf-core-automation-umber.vercel.app
GOOGLE_CLIENT_ID=<from console>
GOOGLE_CLIENT_SECRET=<from console>
ALLOWED_EMAIL_DOMAIN=astounddigital.com
```

Then **Redeploy** (Deployments -> latest -> ... -> Redeploy with cache off).

### 4d. Migrate existing data to the admin

After deploying the auth changes, claim every existing project / persona /
user-story for the bootstrap admin so they don't disappear from the UI:

```
docker compose exec backend python scripts/seed_admin_and_claim.py m.sheth@astounddigital.com
```

The script is idempotent and safe to re-run.

### 4e. Things to know

- **The first user to log in** becomes admin only if their email is listed in
  `INITIAL_ADMINS`. Otherwise they are a regular user (Phase 1 = sees only
  their own data).
- **JWT lifetime is 1 hour.** The frontend silently refreshes the token by
  re-fetching `/api/auth/jwt` whenever the backend returns 401.
- **SSE endpoints** (`/api/runs/execute/stream`, `/api/generate/mcp-stepwise/stream`)
  accept the JWT via `?token=...` query param because EventSource cannot send
  custom headers. Same for `/api/runs/.../file/...` and `/.../bundle.zip` --
  they're embedded in `<a href>` and `<img src>` so the helper appends the
  cached token to the URL.
- **Run history is shared** across all logged-in users in Phase 1. Per-user
  filtering of runs lands in Phase 2 alongside project memberships.

---

## 5. CORS gotchas

`ai_qa_portal/backend/main.py` allows:

- Any `https://*.vercel.app` (production + previews).
- `http(s)://localhost` and `http(s)://127.0.0.1` for dev.
- Any origins listed in `CORS_ORIGINS` or `EXTRA_CORS_ORIGINS` (comma separated) for custom domains.

If you attach a custom domain (e.g. `qa.acme.com`) to Vercel, set:

```bash
flyctl secrets set EXTRA_CORS_ORIGINS=https://qa.acme.com
```

then `flyctl deploy`.

---

## 6. SSE behind proxies

The two streaming endpoints (`/api/runs/execute/stream`, `/api/generate/mcp-stepwise/stream`) already send `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Fly's edge respects those. If you put a custom Nginx in front, also add:

```
proxy_buffering off;
proxy_read_timeout 1h;
```

---

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
