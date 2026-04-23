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

## 4. CORS gotchas

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

## 5. SSE behind proxies

The two streaming endpoints (`/api/runs/execute/stream`, `/api/generate/mcp-stepwise/stream`) already send `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Fly's edge respects those. If you put a custom Nginx in front, also add:

```
proxy_buffering off;
proxy_read_timeout 1h;
```

---

## 6. Smoke checklist after a fresh deploy

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
