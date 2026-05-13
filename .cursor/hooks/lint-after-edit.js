#!/usr/bin/env node
/**
 * Cursor `afterFileEdit` hook -- runs the right linter for the file that
 * was just touched, captures errors, and feeds them back to the agent
 * via `additional_context` so the next turn can self-correct.
 *
 * Why this exists: red squiggles in the primary sidebar were piling up
 * silently because the agent has no built-in awareness of project-level
 * ESLint config. This hook closes that loop -- the agent reads its own
 * lint failures from stdin context just like a human would in the IDE.
 *
 * Triggers:
 *   afterFileEdit, matched on Write|StrReplace|EditNotebook (see hooks.json)
 *
 * Inputs (Cursor stdin):
 *   { tool_name, tool_input: { path|target_notebook|file_path, ... }, ... }
 *
 * Output (stdout):
 *   { additional_context: "..."} when there are lint errors to surface
 *   {}                            when the file is clean / skipped
 *
 * Failure handling:
 *   We never fail closed. The hook is here to inform the agent, not
 *   block edits. If anything goes wrong (eslint missing, tsc crash,
 *   bad JSON), we print {} on stdout and exit 0.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const BACKEND_DIR = path.join(REPO_ROOT, "ai_qa_portal");

/** Read all of stdin synchronously. Cursor sends a single JSON blob. */
function readStdin() {
  try {
    return fs.readFileSync(0, "utf8");
  } catch (_err) {
    return "";
  }
}

/** Best-effort: dig the edited file path out of Cursor's stdin payload.
 *  The shape varies slightly by tool (Write vs StrReplace vs
 *  EditNotebook) so we check a few common keys. */
function extractEditedPath(payload) {
  const ti = (payload && payload.tool_input) || {};
  return (
    ti.path ||
    ti.target_notebook ||
    ti.file_path ||
    (payload && payload.file_path) ||
    ""
  );
}

function emit(obj) {
  try {
    process.stdout.write(JSON.stringify(obj || {}));
  } catch (_err) {
    /* nothing useful we can do */
  }
  process.exit(0);
}

function _startsWithDir(absPath, dir) {
  // Windows is case-insensitive on file paths; lower-case the
  // prefix check so a path supplied as "C:/..." matches a dir
  // resolved as "c:\\...". On POSIX this is a no-op.
  const needle = process.platform === "win32" ? dir.toLowerCase() : dir;
  const hay = process.platform === "win32" ? absPath.toLowerCase() : absPath;
  return hay.startsWith(needle);
}

function isFrontendSource(absPath) {
  if (!absPath) return false;
  if (!_startsWithDir(absPath, FRONTEND_DIR)) return false;
  if (absPath.includes(`${path.sep}.next${path.sep}`)) return false;
  if (absPath.includes(`${path.sep}node_modules${path.sep}`)) return false;
  return /\.(tsx?|jsx?|mjs|cjs)$/.test(absPath);
}

function isBackendPython(absPath) {
  if (!absPath) return false;
  // Cover both the FastAPI backend tree (ai_qa_portal/) and any
  // Python files at the repo root that are part of the app (bridges,
  // ai_bridge.py, run_test.py, etc.). Skip cache + venv noise.
  const isInBackend = _startsWithDir(absPath, BACKEND_DIR);
  const isRepoRootPython = (
    !isInBackend &&
    _startsWithDir(absPath, REPO_ROOT) &&
    !_startsWithDir(absPath, FRONTEND_DIR) &&
    !absPath.includes(`${path.sep}venv${path.sep}`) &&
    !absPath.includes(`${path.sep}__pycache__${path.sep}`) &&
    !absPath.includes(`${path.sep}.cursor${path.sep}`)
  );
  if (!isInBackend && !isRepoRootPython) return false;
  return /\.py$/.test(absPath);
}

/** Run a command and return {ok, stdout, stderr}. Quiet on errors --
 *  we never want the hook itself to crash. */
function runQuiet(cmd, args, opts) {
  try {
    const proc = spawnSync(cmd, args, {
      cwd: opts && opts.cwd,
      encoding: "utf8",
      shell: process.platform === "win32",
      // 50s ceiling so we stay well under Cursor's hook timeout.
      timeout: 50_000,
      maxBuffer: 5 * 1024 * 1024,
    });
    return {
      ok: proc.status === 0,
      status: proc.status,
      stdout: proc.stdout || "",
      stderr: proc.stderr || "",
    };
  } catch (_err) {
    return { ok: false, status: -1, stdout: "", stderr: "" };
  }
}

function lintFrontendFile(absPath) {
  // Use --no-ignore so we don't silently skip files outside the default
  // glob. ESLint exits non-zero when there are any lint errors; we
  // parse JSON output so we can distinguish errors from warnings.
  const rel = path.relative(FRONTEND_DIR, absPath).replace(/\\/g, "/");
  const proc = runQuiet(
    "npx",
    ["--offline", "eslint", "--format", "json", rel],
    { cwd: FRONTEND_DIR },
  );
  if (!proc.stdout.trim()) return null; // npx couldn't run -- stay quiet.

  let parsed;
  try {
    parsed = JSON.parse(proc.stdout);
  } catch {
    return null;
  }
  const file = parsed[0];
  if (!file) return null;

  const errs = (file.messages || []).filter((m) => m.severity === 2);
  if (errs.length === 0) return null;

  const lines = errs.slice(0, 12).map((m) => {
    const rule = m.ruleId || "?";
    const msg = (m.message || "").split("\n")[0].slice(0, 200);
    return `  ${rel}:${m.line}:${m.column}  [${rule}]  ${msg}`;
  });
  const more = errs.length > 12 ? `\n  ... ${errs.length - 12} more error(s) suppressed` : "";
  return (
    `ESLint detected ${errs.length} error(s) in the file you just edited:\n` +
    lines.join("\n") +
    more +
    `\n\nPlease fix these before continuing. Run \`npm run lint\` in ` +
    `frontend/ to see the full output.`
  );
}

function lintPythonFile(absPath) {
  // Run ruff scoped to the single edited file. The ``F`` rule family
  // covers pyflakes-style real bugs (undefined names, unused imports,
  // duplicate keys, etc.) -- the ones an agent edit can realistically
  // introduce. We intentionally skip style rules (E501, W) here so
  // the agent doesn't get spammed with cosmetic complaints after
  // every edit.
  //
  // ``ruff`` is installed via ``py -m pip install ruff``; if it's
  // missing on this host the hook silently no-ops (same fail-open
  // policy as ESLint).
  const rel = path.relative(REPO_ROOT, absPath).replace(/\\/g, "/");
  const proc = runQuiet(
    "py",
    ["-3", "-m", "ruff", "check", "--select", "F", "--output-format", "json", rel],
    { cwd: REPO_ROOT },
  );
  if (!proc.stdout.trim()) return null;

  let parsed;
  try {
    parsed = JSON.parse(proc.stdout);
  } catch {
    return null;
  }
  if (!Array.isArray(parsed) || parsed.length === 0) return null;

  // ruff returns an array of {code, message, location:{row,column}, ...}
  const lines = parsed.slice(0, 12).map((m) => {
    const code = m.code || "?";
    const msg = (m.message || "").split("\n")[0].slice(0, 200);
    const row = (m.location && m.location.row) || "?";
    const col = (m.location && m.location.column) || "?";
    return `  ${rel}:${row}:${col}  [${code}]  ${msg}`;
  });
  const more = parsed.length > 12 ? `\n  ... ${parsed.length - 12} more error(s) suppressed` : "";
  return (
    `Ruff detected ${parsed.length} Python issue(s) in the file you just edited:\n` +
    lines.join("\n") +
    more +
    `\n\nPlease fix these before continuing. Run ` +
    `\`py -3 -m ruff check ${rel} --select F\` to reproduce.`
  );
}

// --- main ---------------------------------------------------------------

const raw = readStdin();
let payload = {};
try {
  payload = raw ? JSON.parse(raw) : {};
} catch {
  emit({});
}

const DEBUG = process.env.CURSOR_HOOK_DEBUG === "1";
function debug(...args) {
  if (DEBUG) process.stderr.write("[lint-after-edit] " + args.join(" ") + "\n");
}

const editedPath = extractEditedPath(payload);
debug("editedPath:", editedPath);
if (!editedPath) emit({});

// Normalise to absolute path so prefix checks are reliable. On
// Windows, also lowercase the drive letter / normalise slashes so
// startsWith() comparisons work regardless of how the agent typed
// the path.
let absPath = path.isAbsolute(editedPath)
  ? path.normalize(editedPath)
  : path.resolve(REPO_ROOT, editedPath);
if (process.platform === "win32") {
  // Path.resolve already lowercases the drive, but if the caller
  // supplied an already-absolute upper-case path we keep it as-is.
  absPath = path.normalize(absPath);
}
debug("absPath:", absPath, "FRONTEND_DIR:", FRONTEND_DIR);

if (isFrontendSource(absPath)) {
  debug("running eslint on", absPath);
  const ctx = lintFrontendFile(absPath);
  debug("ctx?", ctx ? "yes" : "no");
  if (ctx) emit({ additional_context: ctx });
}

if (isBackendPython(absPath)) {
  debug("running ruff on", absPath);
  const ctx = lintPythonFile(absPath);
  debug("ctx?", ctx ? "yes" : "no");
  if (ctx) emit({ additional_context: ctx });
}

emit({});
