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

Salesforce's multi-step login (username → password → MFA) breaks classic same-page Selenium fills. `Login To Sandbox` (Resources/Common/GlobalKeywords.robot) tries three strategies, in order, before falling back to the UI form:

1. **Manual login** — opens the login page and pauses for a human to log in themselves (any credentials, MFA, SSO, passkey), then continues once Lightning loads. Set via the "Manual login" option in the legacy Streamlit app, or the `${sandboxManualLogin}` suite variable. Requires a non-headless run.
2. **CLI OAuth frontdoor** — CumulusCI-style bypass. Authenticate once on the machine that runs Robot:

   ```bash
   sf org login web --alias my-sandbox
   ```

   Complete fingerprint / MFA in that browser; the CLI stores the session. Set `SF_DX_ORG_ALIAS=my-sandbox` (`.env` / backend env) so the portal's **Run** button resolves a fresh `frontdoor.jsp` session for every run via `sf_session_bootstrap.py` (see `docs/sfdx-login-setup.md`). No login form, no MFA/SSO prompt, because Salesforce already trusts the CLI's access token. Re-run `sf org login web` when the token expires.
3. **UI form (fallback / default)** — fills username → password, auto-detecting Salesforce's classic same-page form vs. the modern split identity → Next → password flow. Headed Watch mode can pause up to 10 minutes for manual MFA/OTP/passkey completion.

Override for a single run: pass `login_mode=ui` (Generate page does this automatically when your prompt names a specific test user's username **and** password) to force the UI form and skip the CLI frontdoor bypass — otherwise the run would silently log in as whichever identity the local `sf` CLI is authenticated as, instead of the named test user.

| Variable / env | Values | Effect |
|----------------|--------|--------|
| `SF_DX_ORG_ALIAS` | CLI org alias | Which `sf`-authenticated org the portal's Run button uses for the frontdoor bypass |
| `login_mode` (Run request) | `ui` | Force UI form for this run, skipping the frontdoor bypass |

## Tech Stack

- Next.js 16 (App Router, TypeScript)
- Tailwind CSS (neon futuristic theme)
- Framer Motion (page transitions, card animations)
- Three.js (3D particle background)
- Monaco Editor (Robot Framework code editing)
- Recharts (pass/fail trend charts)
