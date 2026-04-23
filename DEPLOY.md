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
requires a valid Google ID token. Sign-in is restricted to a single Google
Workspace domain (default `astounddigital.com`).

**Auth model in one paragraph.** The frontend (NextAuth on Vercel) runs the
Google OAuth flow and stashes the **raw Google ID token** in the session. The
client fetches that token from `/api/auth/jwt` and forwards it to the backend
as `Authorization: Bearer <token>`. The backend verifies the token directly
against [Google's JWKS](https://www.googleapis.com/oauth2/v3/certs), checks the
audience (`GOOGLE_CLIENT_ID`), the issuer (`accounts.google.com`), the email
domain, and the `hd` (hosted-domain) claim. **No shared secret between Vercel
and the FastAPI backend** -- the trust anchor is Google itself.

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
- **Google ID tokens last 1 hour.** When one expires, `/api/auth/jwt` returns
  401, the frontend's `apiFetch` clears its cache and refetches; if NextAuth's
  session is still alive it issues a fresh ID token transparently. If the
  whole session has expired, the user is bounced to `/login`.
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
