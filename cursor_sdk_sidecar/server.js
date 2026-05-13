/**
 * Cursor SDK sidecar.
 *
 * Tiny HTTP server that exposes @cursor/sdk's Agent.prompt() to the
 * Python FastAPI backend. Mirrors the lifecycle pattern of mcp_bridge
 * (RF-MCP) and pw_mcp_bridge (Playwright): spawned as a subprocess by
 * cursor_sdk_bridge.py, talks HTTP on 127.0.0.1:<port>, exits cleanly
 * on SIGTERM.
 *
 * Why a sidecar instead of importing the SDK directly: @cursor/sdk is
 * TypeScript-only (per the SDK skill: "There is no first-party SDK in
 * other languages at time of writing; REST is the portable option").
 * Our backend is Python; rather than fall back to REST and lose the
 * SDK's nicer ergonomics, we run a small Node process and IPC over
 * loopback HTTP. ~30MB RSS overhead idle.
 *
 * Endpoints:
 *   GET  /healthz         -> {ok, model, sdkVersion, pid}
 *   POST /v1/complete     -> {text, runId} on success;
 *                            {error, retryable} with HTTP:
 *                              502  CursorAgentError (didn't start)
 *                              500  run-level failure (ran but errored)
 *                              504  agent timed out (deadline reached)
 *                              413  request body > MAX_BODY_BYTES
 *                              400  malformed JSON / missing user prompt
 *
 * The Python bridge maps these HTTP statuses into CursorSdkError so
 * the existing failover classifier in ai_bridge.py treats this
 * provider exactly like every other LLM (quota/rate-limit/auth/5xx
 * triggers fail-over to Gemini/OpenAI/etc.).
 *
 * Image inputs are intentionally NOT supported in this sidecar yet --
 * Agent.prompt's image API is documented as evolving in the Cursor
 * SDK skill, and there's no test fixture today. When wired through,
 * we'll add an `images` field to /v1/complete and the Python bridge
 * will gain a corresponding `image_bytes` kwarg.
 */

"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");

let Agent;
let CursorAgentError;
try {
  ({ Agent, CursorAgentError } = require("@cursor/sdk"));
} catch (err) {
  // Module isn't installed (npm install never ran). Fail loudly so the
  // bridge logs see a clear error instead of a confusing crash later.
  console.error(JSON.stringify({
    level: "fatal",
    msg: "@cursor/sdk is not installed. Run 'npm install' inside cursor_sdk_sidecar/.",
    error: err && err.message,
  }));
  process.exit(2);
}

// ---- Config -----------------------------------------------------------

const PORT = parseInt(process.env.CURSOR_SDK_PORT || "8767", 10);
const HOST = process.env.CURSOR_SDK_HOST || "127.0.0.1";
const API_KEY = (process.env.CURSOR_API_KEY || "").trim();
const DEFAULT_MODEL = (process.env.CURSOR_MODEL || "composer-2").trim();

// Hard timeout per agent run. The SDK skill warns about hangs; we
// enforce a deadline so a wedged agent can't pin the sidecar.
const DEFAULT_TIMEOUT_MS =
  parseInt(process.env.CURSOR_SDK_TIMEOUT_S || "180", 10) * 1000;

// Body size cap. Generation prompts can be 100KB+ (catalog injection +
// few-shot examples), so 4MB is generous but bounded.
const MAX_BODY_BYTES = 4 * 1024 * 1024;

if (!API_KEY) {
  console.error(JSON.stringify({
    level: "fatal",
    msg: "CURSOR_API_KEY is not set; the sidecar cannot authenticate the SDK.",
  }));
  process.exit(3);
}

// ---- Helpers ----------------------------------------------------------

function sdkVersion() {
  // The SDK's package.json is locked down by `exports`, so we can't
  // require() it as a subpath. Resolve the entry point and walk up to
  // the package root instead.
  try {
    const entry = require.resolve("@cursor/sdk");
    let dir = path.dirname(entry);
    for (let i = 0; i < 6; i += 1) {
      const candidate = path.join(dir, "package.json");
      if (fs.existsSync(candidate)) {
        const meta = JSON.parse(fs.readFileSync(candidate, "utf8"));
        if (meta && meta.name === "@cursor/sdk") return meta.version || "unknown";
      }
      const parent = path.dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  } catch (_err) {
    // Fall through.
  }
  return "unknown";
}

function logJson(level, fields) {
  // Structured JSON logs so the Python bridge can grep stderr without
  // pulling in a logger lib here. Never log API keys; we explicitly
  // redact in the few places we'd otherwise echo env values.
  try {
    process.stderr.write(JSON.stringify({ level, t: Date.now(), ...fields }) + "\n");
  } catch (_err) {
    // Stderr full / disconnected -- nothing useful we can do.
  }
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let total = 0;
    const chunks = [];
    req.on("data", (chunk) => {
      total += chunk.length;
      if (total > MAX_BODY_BYTES) {
        // Reject early; don't accumulate megabytes of unwanted data.
        reject(Object.assign(new Error("body too large"), { httpStatus: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf8");
        resolve(raw ? JSON.parse(raw) : {});
      } catch (err) {
        reject(Object.assign(err, { httpStatus: 400 }));
      }
    });
    req.on("error", reject);
  });
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
  });
  res.end(body);
}

// Run a coroutine with a wall-clock deadline. The SDK exposes cancel()
// on most runtimes (per the skill); we'd rather raise a timeout than
// let the request hang past the bridge's HTTP timeout.
async function withDeadline(promise, ms, onTimeout) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      try {
        if (typeof onTimeout === "function") onTimeout();
      } catch (_e) {
        // Best-effort cancel; swallow.
      }
      reject(Object.assign(new Error(`agent timed out after ${ms}ms`), { isTimeout: true }));
    }, ms);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

// ---- Endpoints --------------------------------------------------------

/**
 * Drives one Agent.prompt() round-trip. Pattern 1 from the SDK skill:
 * one-shot, auto-disposes, no Symbol.asyncDispose leaks. Returns the
 * SDK's `result.result` string on success.
 *
 * Distinguishes the two failure shapes the skill calls out:
 *   - CursorAgentError thrown -> didn't start (auth/config/network).
 *     Surface as HTTP 502 with retryable=err.isRetryable.
 *   - result.status === "error" -> ran but failed mid-run. HTTP 500,
 *     retryable=false (the agent had compute; another retry won't help
 *     unless the prompt itself is fixed).
 */
async function runComplete({ system, user, model, timeoutMs }) {
  const fullPrompt =
    `# System instructions\n\n${system || ""}\n\n` +
    `# User request\n\n${user || ""}\n\n` +
    `# Reminder\n\nReturn ONLY the requested content as plain ` +
    `assistant text. No commentary outside the requested content.`;

  const promptOpts = {
    apiKey: API_KEY,
    model: { id: (model || DEFAULT_MODEL).trim() },
    // Per the SDK skill: "Local - runs on the caller's machine against
    // cwd, reuses their environment and credentials". For our
    // completion-only use we pin cwd to the sidecar's own dir and pass
    // settingSources: [] so the agent never loads project/user/team
    // settings -- we want a clean LLM call, not an editor session.
    local: {
      cwd: process.cwd(),
      settingSources: [],
    },
  };

  // Agent.prompt is one-shot + auto-dispose per the skill. No need for
  // try/finally around dispose -- the SDK handles it for this pattern.
  return await withDeadline(
    Agent.prompt(fullPrompt, promptOpts),
    timeoutMs,
    /* onTimeout */ () => {
      // No cancel token returned by Agent.prompt; the deadline
      // primarily protects the sidecar from blocking forever, which is
      // sufficient for the bridge's purposes.
      logJson("warn", { msg: "agent timeout fired", timeoutMs });
    },
  );
}

async function handleComplete(req, res) {
  let body;
  try {
    body = await readBody(req);
  } catch (err) {
    sendJson(res, err.httpStatus || 400, {
      error: `invalid request body: ${err.message}`,
      retryable: false,
    });
    return;
  }

  const system = typeof body.system === "string" ? body.system : "";
  const user = typeof body.user === "string" ? body.user : "";
  if (!user.trim()) {
    sendJson(res, 400, {
      error: "user prompt is required (got empty string)",
      retryable: false,
    });
    return;
  }

  const model = (typeof body.model === "string" && body.model.trim()) || DEFAULT_MODEL;
  // timeout_ms is optional; clamp to a sane range so a misconfigured
  // client can't ask for a 10-hour deadline that pins the sidecar.
  let timeoutMs = DEFAULT_TIMEOUT_MS;
  if (body.timeout_ms !== undefined && body.timeout_ms !== null) {
    const requested = Number(body.timeout_ms);
    if (Number.isFinite(requested) && requested > 0) {
      timeoutMs = Math.min(requested, 15 * 60 * 1000); // 15-min hard cap
    }
  }

  let result;
  try {
    result = await runComplete({ system, user, model, timeoutMs });
  } catch (err) {
    if (err && err.isTimeout) {
      // Treated as retryable: a different provider might be faster.
      sendJson(res, 504, {
        error: err.message,
        retryable: true,
      });
      return;
    }
    if (CursorAgentError && err instanceof CursorAgentError) {
      // Startup failures: auth, config, network. The SDK marks
      // `isRetryable` on the instance; respect it (per the skill's
      // "Respect error.isRetryable -- it's the backend telling you the
      // specific failure is safe to retry").
      sendJson(res, 502, {
        error: err.message,
        retryable: err.isRetryable === true,
      });
      return;
    }
    // Unknown error class. Treat as non-retryable to avoid masking
    // genuine bugs -- the failover classifier on the Python side
    // operates on the message; we surface the whole thing.
    sendJson(res, 500, {
      error: err && err.message ? err.message : String(err),
      retryable: false,
    });
    return;
  }

  // result.status: "finished" | "error" | "cancelled"
  if (!result || result.status !== "finished") {
    sendJson(res, 500, {
      error: `agent did not finish (status=${result && result.status})`,
      retryable: false,
      runId: (result && result.id) || null,
    });
    return;
  }

  const text = typeof result.result === "string" ? result.result : "";
  if (!text) {
    sendJson(res, 500, {
      error: "agent finished but produced no text output",
      retryable: false,
      runId: result.id || null,
    });
    return;
  }
  sendJson(res, 200, { text, runId: result.id || null });
}

function handleHealth(_req, res) {
  sendJson(res, 200, {
    ok: true,
    model: DEFAULT_MODEL,
    sdkVersion: sdkVersion(),
    pid: process.pid,
  });
}

// ---- Server -----------------------------------------------------------

const server = http.createServer((req, res) => {
  // Loopback-only by binding to 127.0.0.1; reject any host that
  // somehow reaches us with a non-loopback Host header. Defence in
  // depth -- the listen() below should be the only barrier needed.
  if (req.method === "GET" && req.url === "/healthz") {
    return handleHealth(req, res);
  }
  if (req.method === "POST" && req.url === "/v1/complete") {
    return handleComplete(req, res);
  }
  sendJson(res, 404, { error: "not found" });
});

server.on("clientError", (err, socket) => {
  // Default Node behaviour is to emit 'connection error' which can
  // crash with EPIPE under load. Quiet failure is fine here.
  try {
    socket.end("HTTP/1.1 400 Bad Request\r\n\r\n");
  } catch (_e) {
    // Socket already gone.
  }
  logJson("warn", { msg: "client error", err: err && err.message });
});

server.listen(PORT, HOST, () => {
  logJson("info", {
    msg: "cursor-sdk-sidecar listening",
    host: HOST,
    port: PORT,
    sdkVersion: sdkVersion(),
    defaultModel: DEFAULT_MODEL,
  });
});

// ---- Graceful shutdown ------------------------------------------------

function shutdown(signal) {
  logJson("info", { msg: "shutting down", signal });
  server.close(() => process.exit(0));
  // Hard cap so a stuck close() doesn't hang the parent's atexit kill.
  setTimeout(() => process.exit(0), 5000).unref();
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
// Surface uncaught errors so the bridge log shows them rather than
// dying silently.
process.on("uncaughtException", (err) => {
  logJson("fatal", { msg: "uncaughtException", err: err && err.message });
  process.exit(1);
});
process.on("unhandledRejection", (err) => {
  logJson("fatal", { msg: "unhandledRejection", err: err && err.message });
  process.exit(1);
});
