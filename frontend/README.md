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
| `/sfdx` | SF DX Tools — SOQL, Apex tests, org schema | salesforce |
| `/locators` | Locator health scanner — scan against live DOM | locators |
| `/settings` | LLM provider, MCP server, catalog rebuild, SF DX status | llm, mcp, catalog, salesforce |
| `/about` | Platform info and credits | None (static) |

## Tech Stack

- Next.js 16 (App Router, TypeScript)
- Tailwind CSS (neon futuristic theme)
- Framer Motion (page transitions, card animations)
- Three.js (3D particle background)
- Monaco Editor (Robot Framework code editing)
- Recharts (pass/fail trend charts)
