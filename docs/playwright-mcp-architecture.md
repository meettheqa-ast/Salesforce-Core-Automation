# Playwright-MCP integration architecture

This document describes the runtime architecture of the AI QA Portal's Playwright integration. It complements [`mcp_bridge.py`](../mcp_bridge.py) (RF-MCP) and [`cursor_sdk_bridge.py`](../cursor_sdk_bridge.py) -- the three engines run side-by-side, each managed by a dedicated bridge file with the same operational hygiene (subprocess/runtime lifecycle, session caching, TTL eviction, atexit cleanup). For the Cursor SDK sidecar specifically, see [`docs/cursor-sdk-integration.md`](cursor-sdk-integration.md).

## Layers

```
                  +-------------------------+
                  |  FastAPI handlers       |
                  |  (generate, recording,  |
                  |  visual_regression,     |
                  |  explore)               |
                  +-----------+-------------+
                              |
            +-----------------+----------------+
            |                                  |
+-----------v---------+        +---------------v-----------+
| script_validation_  |        | playwright_session_       |
| loop                |        | manager                   |
| + playwright_       |        | (login + storageState)    |
| validate            |        +---------------+-----------+
+-----------+---------+                        |
            |                                  |
            +---------------+------------------+
                            |
              +-------------v-------------+
              | pw_mcp_bridge             |
              | (Playwright runtime,      |
              |  session cache, atexit)   |
              +-------------+-------------+
                            |
                            v
                  +-------------------+
                  | Playwright Python |
                  | (chromium browser)|
                  +-------------------+
```

The Playwright runtime is **in-process** (Python's `playwright` library), unlike RF-MCP which runs as a separate HTTP subprocess. This trade-off was deliberate:

- No node runtime in deployment.
- No HTTP/MCP-protocol round-trip for our internal callers.
- All browser instances live inside the FastAPI process; cleanup is direct via `atexit`.

The trade-off cost: the LLM cannot drive Playwright via MCP tool-calling (we'd need a separate MCP server for that). When/if Phase 5 (AI exploratory testing) demands true MCP semantics for an external agent, we add a thin MCP layer around `pw_mcp_bridge`. The current internal-use surface (validation, recording, visual regression) doesn't need it.

## Lifecycle

1. **First Playwright operation** triggers `pw_mcp_bridge.start_pw_runtime()`. The runtime starts a chromium browser instance (~150MB binary), spins up a dedicated asyncio event loop on a worker thread (so synchronous handlers can drive Playwright via `run_async`), and registers `atexit.register(stop_pw_runtime)`.

2. **Each session** = one `BrowserContext` (cookies + localStorage isolated per persona). Sessions cache for `PW_MCP_SESSION_TTL_S` (default 600s).

3. **storageState persistence**: when a session is created, the post-login storageState (cookies + localStorage) is saved to `_local_data/pw_storage/<hash>.json`. Subsequent cold starts within `PW_MCP_STORAGE_LIFETIME_S` (default 6 hours) skip the SF login form entirely -- new context is built with the persisted state.

4. **Eviction**: stale sessions (older than TTL) are evicted on every `get_or_init_session` call. The cache is also bounded by `PW_MCP_MAX_CONTEXTS` (default 3) to cap memory + Salesforce session count per user.

5. **Shutdown**: `atexit` closes every cached BrowserContext, then the browser, then the playwright instance, then the dedicated event loop. Idempotent: safe to call multiple times.

## Public surfaces by phase

- **Phase 0**: `pw_mcp_bridge` lifecycle + session cache; `playwright_session_manager` (typed handle wrapper); `playwright_metrics` (counters + histograms).
- **Phase 1** (locator validation): `playwright_validate.validate_locators()` -- called by `script_validation_loop` as an optional third tier after AST + dryrun.
- **Phase 2** (recording): `pw_mcp_bridge.record_start/actions/stop`; `recording_translator.translate_actions()`; `routers/recording.py`.
- **Phase 3** (visual regression): `services/visual_regression`; `routers/visual_regression.py`.
- **Phase 4** (trace viewer): `Resources/Common/PlaywrightDebug.robot` (opt-in Robot resource); run-bundle includes `trace.zip`; frontend opens [trace.playwright.dev](https://trace.playwright.dev/) externally.
- **Phase 5** (exploratory): `routers/explore.py` (admin-only).

## Feature flags

| Flag | Default | Purpose |
|------|---------|---------|
| `PLAYWRIGHT_ENABLED` | `true` | Master kill-switch. False = no Playwright code path activates anywhere. |
| `PW_LOCATOR_VALIDATION` | `false` | Phase 1: locator gate runs during script generation. |
| `PW_LOCATOR_VALIDATION_SHADOW` | `false` | Phase 1: log results but don't block the Run button. Used during 1-2 week burn-in. |
| `PW_RECORDING` | `false` | Phase 2: recording endpoints accept requests. |
| `PW_VISUAL_REGRESSION` | `false` | Phase 3: visual regression endpoints accept requests. |
| `PW_TRACE_VIEWER` | `false` | Phase 4: trace viewer link surfaces on run pages. |
| `PW_EXPLORATORY` | `false` | Phase 5: exploratory endpoint accepts requests (admin only). |
| `PW_PREWARM` | `false` | Pre-launch Playwright on FastAPI startup. |

Per-project: `playwright_mcp_enabled` in the project's CredentialsUpdate row. A project must opt in AND the global flag must be on for any feature to activate.

## Data layout

```
_local_data/
  pw_storage/                    <- storageState (cookies + localStorage)
    <sha256-of-key>.json
  pw_metrics.json                <- counter snapshot (periodic flush)
  recordings/
    <session_id>/
      <video files>              <- Playwright video recordings

Saved_Projects/
  <slug>/
    screenshots/
      baselines/                 <- canonical "what should be"
        <test_case_id>__<step>.png
      current/                   <- last captured state
        <test_case_id>__<step>.png
      diffs/
        <run_folder>/
          <test_case_id>__<step>.diff.png
```

## Z-index of failure modes (most-likely first)

1. **Playwright not installed** (`ImportError`). Soft-fail: gates skip, log line, request continues with AST + dryrun verdict.
2. **Salesforce login form changed**. Detected by `wait_for_selector` timeout on `.appLauncher`. Hard fail with helpful error; admin updates the login keyword in `pw_mcp_bridge._do_salesforce_login`.
3. **storageState expired mid-session**. Detected by redirect to login URL. The `playwright_session_manager.release_on_logout_hint` helper drops the cached state; next acquire runs a full login.
4. **Network flakiness during locator validation**. Per-call timeout (60s) bails; result reported as soft-pass.
5. **Subprocess orphan after backend hard-kill**. `pw_mcp_bridge` doesn't bind a port (in-process), so no orphan-port issue like RF-MCP. atexit handles cleanup.

## Monitoring

`playwright_metrics.snapshot()` returns counters + histogram p50/p95/p99 in a single dict. Periodically flushed to `_local_data/pw_metrics.json`. Suggested ops dashboard: tail the file or curl an exposed `/api/playwright/metrics` endpoint.

Key counters to watch:

- `pw_locator_validate_total{result="all_live"}` -- generations where every locator passed.
- `pw_locator_validate_total{result="all_stale"}` -- everything failed; usually a Salesforce auth/redirect issue, not real validation failures.
- `pw_locator_validate_total{result="partial"}` -- the LLM produced some good + some bad locators. Interesting; track over time.
- `pw_locator_validate_duration_seconds` -- if p95 > 30s, the warm-cache hit rate is bad.
- `pw_visual_diff_total{result="drift"}` -- visual regression drift events.

## Rollback

Each phase ships behind a kill switch. Rollback is one env var flip:

```bash
# Disable Playwright entirely
PLAYWRIGHT_ENABLED=false uvicorn ai_qa_portal.backend.main:app

# Or just disable a specific phase
PW_LOCATOR_VALIDATION=false uvicorn ...
```
