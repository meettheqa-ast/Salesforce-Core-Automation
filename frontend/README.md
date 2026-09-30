# AI QA Portal — Next.js Frontend

Full-featured React frontend replacing Streamlit. All pages wired to FastAPI backend.

## Quick Start

```bash
# Terminal 1: Backend (port 8000)
cd backend && pip install -r requirements.txt && python run.py

# Terminal 2: Frontend (port 3000)
cd frontend && npm install && npm run dev
```

## Pages (Full Parity with Streamlit)

| Route | Description | API Endpoints Used |
|-------|-------------|-------------------|
| `/` | Landing page with 3D particles, hero, feature cards | None (static) |
| `/generate` | AI test generator — credentials, MCP/Quick mode, Monaco editor, Run, Save | generate, runs, projects |
| `/dashboard` | Analytics dashboard — metrics, pass/fail chart, recent runs | analytics, runs, mcp, projects |
| `/projects` | Project CRUD — create, delete, list with animated cards | projects |
| `/projects/[name]` | Project detail — envs, tests, source viewer, analytics | projects, analytics |
| `/org-inspector` | Org Inspector — ad-hoc SOQL + Salesforce object schema lookup | salesforce |
| `/sfdx` | Legacy redirect → `/org-inspector` (kept so old bookmarks survive) | salesforce |
| `/settings` | LLM provider, MCP server, catalog rebuild, **Locator Health card** (deep-links to `/locators` for the full scanner) | llm, mcp, catalog, salesforce, locators |
| `/about` | Platform info and credits | None (static) |

## Salesforce login (MFA / fingerprint)

Salesforce’s multi-step login (username → password → MFA) breaks classic same-page Selenium fills. Portal **Run** uses CumulusCI-style **frontdoor** login by default:

1. On the machine that runs Robot (same host as the API), authenticate once:

   ```bash
   sf org login web --alias DEFAULT_TARGET_ORG
   ```

   Complete fingerprint / MFA in that browser. The CLI stores the session.

2. Optional: set `SF_DX_ORG_ALIAS` if you use a different alias (same as Org Inspector).

3. Portal / Robot inject `LOGIN_MODE=auto`:
   - **frontdoor first** — opens Lightning via `/secur/frontdoor.jsp?sid=<CLI accessToken>` (no login form)
   - **UI fallback** — multi-step username → Next → password; headed Watch mode can pause for MFA

4. When the CLI token expires, run `sf org login web` again.

Overrides:

| Variable / env | Values | Effect |
|----------------|--------|--------|
| `LOGIN_MODE` / `ROBOT_LOGIN_MODE` | `auto` (default), `frontdoor`, `ui` | Force strategy |
| `SF_DX_ORG_ALIAS` | CLI org alias | Which authenticated org to use |

## Tech Stack

- Next.js 16 (App Router, TypeScript)
- Tailwind CSS (neon futuristic theme)
- Framer Motion (page transitions, card animations)
- Three.js (3D particle background)
- Monaco Editor (Robot Framework code editing)
- Recharts (pass/fail trend charts)
