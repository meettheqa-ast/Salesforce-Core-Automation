# Cursor SDK sidecar

Tiny Node service that exposes [`@cursor/sdk`](https://www.npmjs.com/package/@cursor/sdk) to the Python FastAPI backend over loopback HTTP.

The Python `cursor_sdk_bridge.py` spawns `node server.js`, talks to it on `127.0.0.1:8767` (configurable), and tears it down at process exit. The lifecycle mirrors `mcp_bridge.py` (RF-MCP) and `pw_mcp_bridge.py` (Playwright) -- this is a sibling sidecar, not an external service.

## Why

`@cursor/sdk` is TypeScript-only. Our backend is Python. Rather than fall back to the older Cursor REST API and lose the SDK's nicer ergonomics (`Agent.prompt`, structured run results, typed errors), we keep the SDK in its native runtime and IPC over HTTP. The marginal cost is one ~30 MB Node process; the win is parity with the SDK's evolving feature set (model list, image inputs, cancellation, etc.).

If the sidecar can't start (no Node, npm install missing, port collision), `cursor_sdk_bridge.py` transparently falls back to the existing `_call_cursor_rest` REST path in `ai_bridge.py`. Set `CURSOR_USE_SDK=false` to force the REST path even when the sidecar is healthy.

## Local setup

```bash
# Requires Node 20 LTS or newer.
cd cursor_sdk_sidecar
npm install
```

Then export the API key in the environment of your Python backend:

```bash
export CURSOR_API_KEY=cursor_..."
```

The Python bridge will spawn the sidecar lazily on the first LLM call.

## Endpoints

```
GET  /healthz          -> 200 {ok, model, sdkVersion, pid}
POST /v1/complete      -> 200 {text, runId}
                          400 invalid body
                          413 body too large (4MB cap)
                          500 agent ran but failed (retryable=false)
                          502 CursorAgentError (auth/config/network; retryable=err.isRetryable)
                          504 timeout (retryable=true)
```

`POST /v1/complete` body:

```json
{
  "system": "string -- prepended as a 'System instructions' block",
  "user":   "string -- the user request",
  "model":  "optional string -- overrides CURSOR_MODEL",
  "timeout_ms": "optional number -- overrides CURSOR_SDK_TIMEOUT_S"
}
```

## Environment

| Var | Default | Notes |
| --- | --- | --- |
| `CURSOR_API_KEY` | _(required)_ | Sidecar refuses to start without this. |
| `CURSOR_MODEL` | `composer-2` | Default model id; override per request via the body. |
| `CURSOR_SDK_PORT` | `8767` | Loopback port. |
| `CURSOR_SDK_HOST` | `127.0.0.1` | Bind address; do not change without firewalling. |
| `CURSOR_SDK_TIMEOUT_S` | `180` | Hard wall-clock cap per agent run. |

## Logging

The sidecar emits structured JSON log lines to **stderr**. Capture them with the bridge's subprocess pipe (already wired in `cursor_sdk_bridge.py`).

## Updating `@cursor/sdk`

```bash
cd cursor_sdk_sidecar
npm install @cursor/sdk@latest
git add package.json package-lock.json
```

The model id surface evolves; when bumping the SDK, also re-run `Cursor.models.list({apiKey})` to confirm the default model is still valid.
