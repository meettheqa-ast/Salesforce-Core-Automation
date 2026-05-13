# Playwright-MCP deployment guide

## Local dev

```bash
# Backend
cd <repo>
pip install -r ai_qa_portal/requirements.txt
python -m playwright install chromium

# Set feature flags in .env (or shell env)
PW_LOCATOR_VALIDATION=true
PW_LOCATOR_VALIDATION_SHADOW=true   # observation only for first 1-2 weeks
PLAYWRIGHT_ENABLED=true

# Run as usual
uvicorn ai_qa_portal.backend.main:app --reload
```

The first Playwright operation in a fresh checkout downloads the chromium browser binary (~150MB). Subsequent runs reuse the binary from `~/.cache/ms-playwright/`.

## Docker

The `Dockerfile` already includes the Playwright install step (added in Phase 0). The base image stays at `python:3.13-slim-bookworm`; we add `python -m playwright install chromium --with-deps` after pip install.

```dockerfile
RUN python -m playwright install chromium --with-deps
```

This adds ~300MB to the image (chromium binary + system libs). If image size is critical, switch the base to `mcr.microsoft.com/playwright/python:v1.49.0-noble` which has chromium pre-installed; revert the explicit install line.

## Production runtime ports

The Playwright runtime is in-process with FastAPI (no extra port). RF-MCP still uses port 8765. The Cursor SDK sidecar (added in the LLM provider re-prioritisation) listens on 8767. No further port allocations needed.

## Sidecar inventory

Three sibling sidecars now run alongside the FastAPI backend. All follow the same lifecycle pattern (lazy spawn, `atexit` teardown, port-orphan cleanup, restart-on-wedge):

| Sidecar | Lifecycle module | Port | Image-size impact | Idle memory |
| --- | --- | --- | --- | --- |
| RF-MCP | `mcp_bridge.py` | 8765 | _included_ in the Python deps | ~80 MB |
| Playwright | `pw_mcp_bridge.py` | _in-process_ | +150 MB (chromium binary) | +30 MB |
| Cursor SDK | `cursor_sdk_bridge.py` | 8767 | +80 MB (Node + `@cursor/sdk`) | ~30 MB |

If you need to disable any of them at runtime:

- RF-MCP -- not optional today (Stepwise uses it).
- Playwright -- set `PLAYWRIGHT_ENABLED=false`. Quick Generate falls back to AST-only validation.
- Cursor SDK -- set `CURSOR_USE_SDK=false`. The dispatcher in `ai_bridge._call_cursor` falls back to the legacy REST path (`api.cursor.com`); see [`docs/cursor-sdk-integration.md`](cursor-sdk-integration.md) for the full configuration matrix.

## Persistent volumes

These directories must be writable by the FastAPI process:

| Path | Purpose | Lifetime |
|------|---------|----------|
| `_local_data/pw_storage/` | Salesforce login storageState (cookies). | 6 hours; auto-refreshed. |
| `_local_data/pw_metrics.json` | Counter snapshot. | Continuous. |
| `_local_data/recordings/` | Playwright video recordings (Phase 2). | Per-session; clean up periodically. |
| `Saved_Projects/<slug>/screenshots/` | Visual regression baselines + diffs (Phase 3). | Indefinite (baselines are canonical). |

In Docker, mount `_local_data/` as a persistent volume so storageState survives container restarts (avoids cold-login on every redeploy).

## Resource budget

| Resource | Cost |
|----------|------|
| Memory (idle) | +30MB (Playwright Python module loaded). |
| Memory (active session) | +50-80MB per BrowserContext. |
| CPU | Negligible idle; Phase 1 validation = ~3s warm / 18s cold per generation. |
| Disk | 150MB chromium binary; baselines ~200KB per test case. |
| Network | SF login (~50KB) per cold session; storageState reuse skips this. |

## Concurrency caps

`PW_MCP_MAX_CONTEXTS` (default 3) caps simultaneous BrowserContexts. Salesforce also rate-limits parallel sessions per user (typically 5-10 depending on org). Stay conservative.

## Salesforce account hygiene

Each Playwright session creates a real Salesforce login. Recommendations:

- Use a dedicated automation user account for Playwright sessions (separate from Selenium/Robot tests). Per-engine accounts make session-limit issues debuggable.
- Set the user's Login IP Range (or a permission set) so Playwright sessions don't get caught by login flow alerts.
- Multi-factor auth is NOT supported by the auto-login flow. Disable MFA on the automation user OR use a Connected App with OAuth password flow (future enhancement).
