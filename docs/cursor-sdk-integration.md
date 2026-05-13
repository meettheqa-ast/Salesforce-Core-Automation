# Cursor SDK as the primary LLM provider

## TL;DR

When `CURSOR_API_KEY` is set in the backend's environment, the
[`@cursor/sdk`](https://www.npmjs.com/package/@cursor/sdk) becomes the
default primary LLM for every code path that calls
`ai_bridge.call_llm` (Quick Generate, MCP Stepwise, Heal, Visual
regression, Recording translator, exploratory testing). All other
providers (Gemini, OpenAI, Groq, Anthropic, Ollama, ...) keep working
unchanged and serve as the failover chain.

If anything goes wrong with the SDK path -- Node not installed, sidecar
crashes, you set `CURSOR_USE_SDK=false` -- the system transparently
falls back to the legacy Cursor Cloud Agents REST API and then to the
remaining providers. Behaviour with `CURSOR_API_KEY` unset is identical
to the pre-change baseline.

## Architecture

```
ai_bridge.call_llm
        │
        ▼
_build_failover_chain (primary first)
        │
        ▼
   _call_cursor (dispatcher)
        │
   ┌────┴─────────────┐
   ▼                  ▼
SDK sidecar     _call_cursor_rest
(Node @ 8767)   (Cloud Agents API)
   │                  │
   ▼                  ▼
@cursor/sdk      api.cursor.com
```

Three sibling sidecars now run alongside the FastAPI process:

| Sidecar | Lifecycle module | Port | Purpose |
| --- | --- | --- | --- |
| RF-MCP | `mcp_bridge.py` | 8765 | Robot Framework Stepwise execution |
| Playwright | `pw_mcp_bridge.py` | _in-process_ | Locator validation, recording, visual regression |
| Cursor SDK | `cursor_sdk_bridge.py` | 8767 | LLM completion via @cursor/sdk |

All three follow the same lifecycle pattern: lazy spawn on first use,
port-orphan cleanup on Windows, `atexit.register(stop_*)`, automatic
restart when the process is alive but unresponsive. Stopping the
backend cleanly tears them all down.

## Local development

1. **Install Node 20 LTS or newer.** The sidecar refuses to start
   without Node on `PATH`; the dispatcher then transparently falls
   back to the REST path so generation still works.
2. **Install the SDK once.**
   ```bash
   cd cursor_sdk_sidecar
   npm install
   ```
3. **Mint a key** at <https://cursor.com/dashboard/integrations> and
   add it to `ai_qa_portal/.env`:
   ```bash
   LLM_PROVIDER=cursor
   CURSOR_API_KEY=cursor_...
   CURSOR_MODEL=composer-2
   ```
4. **Restart the backend.** The sidecar lazy-starts on the first LLM
   call (~1-2s warm thereafter).

> **Tip:** delete `cursor_sdk_sidecar/node_modules/` to test the
> bridge's auto-install path. The bridge logs a one-time warning when
> it runs `npm install` for you.

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_PROVIDER` | _auto_ | Set to `cursor` to lock; otherwise `_default_primary_provider()` picks Cursor when the API key is set. |
| `CURSOR_API_KEY` | _(required)_ | The `cursor_...` key from the dashboard. Required by both the sidecar and the REST fallback. |
| `CURSOR_MODEL` | `composer-2` | Default model id; per-request overrides supported. Run `Cursor.models.list({apiKey})` to enumerate. |
| `CURSOR_USE_SDK` | `true` | `false` forces the legacy REST path; useful as a hard escape hatch in prod. |
| `CURSOR_SDK_PORT` | `8767` | Loopback port for the sidecar. Falls back to default if you accidentally set it to RF-MCP's port. |
| `CURSOR_SDK_HOST` | `127.0.0.1` | Don't change without firewalling. |
| `CURSOR_SDK_TIMEOUT_S` | `180` | Hard wall-clock cap per agent run. Sidecar returns 504 + `retryable=true` on hit. |
| `CURSOR_AGENT_REPO` | _(only for REST)_ | Required only when `CURSOR_USE_SDK=false`. The Cloud Agents API mandates a repo target. |
| `LLM_FAILOVER_ORDER` | _(unset)_ | Optional CSV. Recommended: `cursor,gemini,openai,anthropic,groq`. |

## Failure modes + recovery

| Failure | What happens | What to do |
| --- | --- | --- |
| Node not installed | `is_node_available()` returns False; dispatcher logs and uses REST. | Install Node 20+ or accept REST. |
| `npm install` missing | Bridge runs it on first call (one-time, ~5-30s). Logged at WARN. | Pre-install in CI / container build. |
| Port collision (8767 held by RF-MCP) | `_host_port()` falls back to default and warns. | Free the port or set `CURSOR_SDK_PORT`. |
| Sidecar crash | `is_sidecar_responsive()` detects, dispatcher restarts on next call. | Inspect stderr in the backend logs for the JSON error line. |
| Cursor 401 | Sidecar returns 502 + `retryable=false`; failover moves on to Gemini. | Check the key in `.env`. Watch for trailing whitespace. |
| Cursor 429 / quota | Sidecar surfaces the message; classifier marks transient. | Failover handles it. Long-term: bump quota or add `LLM_FAILOVER_ORDER`. |
| Agent hangs > 180s | Sidecar 504; failover continues. | Adjust `CURSOR_SDK_TIMEOUT_S` if your typical generations are slower. |
| `CURSOR_USE_SDK=false` | Dispatcher skips SDK entirely. | Use as an emergency escape hatch only -- REST is ~10x slower per call. |

## Resource budget

| Resource | SDK sidecar | REST path |
| --- | --- | --- |
| Memory (idle) | ~30 MB | _none_ (in-process) |
| Memory (per call) | +50 MB during agent run, GC-bounded | +5 MB per stream |
| Cold start | ~5 s first call (Node + module load) | ~30 s per call (agent container spin-up) |
| Warm latency | ~1-2 s | ~30 s |
| Image size | +80 MB (Node + @cursor/sdk) | _none_ |

## Updating the SDK

```bash
cd cursor_sdk_sidecar
npm install @cursor/sdk@latest
git add package.json package-lock.json
```

After upgrading, run the test suite:

```bash
py -3 -m pytest ai_qa_portal/tests/test_cursor_sdk_bridge.py -v
```

The SDK skill warns that "Model IDs change", so also re-confirm
`CURSOR_MODEL` (default `composer-2`) is still a valid id by hitting
`Cursor.models.list({apiKey})` from a one-off Node script.

## Testing without burning quota

Every test in `ai_qa_portal/tests/test_cursor_sdk_bridge.py` mocks
`requests.post` / `requests.get`, so the suite never spawns a Node
process or hits `api.cursor.com`. Same pattern as
`test_pw_mcp_bridge.py`.

## See also

- [`docs/playwright-mcp-architecture.md`](playwright-mcp-architecture.md) -- how the Playwright sidecar plugs into the same lifecycle pattern.
- [`docs/playwright-mcp-deployment.md`](playwright-mcp-deployment.md) -- "Sidecar inventory" table for ops.
- [`cursor_sdk_sidecar/README.md`](../cursor_sdk_sidecar/README.md) -- HTTP contract reference for the sidecar itself.
