"use client";

/**
 * /generate/record -- Phase 2: Recording mode.
 *
 * Three-state UX:
 *
 *   pre-record     "Click Start. Pick a workspace login."
 *   recording      "Click through Salesforce. Live action log on the right."
 *   post-record    "Here's the script we generated. Edit, save, run."
 *
 * Each state is its own React render branch driven by a single
 * ``mode`` state. The page reuses validation pills, error panels,
 * Run buttons, and the WorkspaceBar from /generate so users get a
 * consistent surface across all three generation modes.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import RobotCodeEditor from "@/components/editor/RobotCodeEditor";
import WorkspaceBar, { type WorkspaceCreds } from "@/components/layout/WorkspaceBar";
import { api } from "@/lib/api";

type Mode = "pre" | "recording" | "post";

type Action = {
  type: string;
  selector?: string;
  text?: string;
  value?: string;
  url?: string;
  key?: string;
  timestamp_ms?: number;
};

const POLL_INTERVAL_MS = 1000;

export default function RecordPage() {
  const [creds, setCreds] = useState<WorkspaceCreds | null>(null);
  const [mode, setMode] = useState<Mode>("pre");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [actions, setActions] = useState<Action[]>([]);
  const [robotCode, setRobotCode] = useState<string>("");
  const [validationOk, setValidationOk] = useState<boolean | null>(null);
  const [validationErrors, setValidationErrors] = useState<any[]>([]);
  const [testPath, setTestPath] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [recordStartedAt, setRecordStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState<number>(0);

  const credsReady = Boolean(creds?.sandboxUrl && creds.username && creds.password);

  // Live duration ticker (1Hz). Cheap; only runs in recording state.
  useEffect(() => {
    if (mode !== "recording") return;
    const id = window.setInterval(() => setNow(performance.now()), 250);
    return () => window.clearInterval(id);
  }, [mode]);

  // Poll the action log while recording. Stops automatically when
  // mode flips out of "recording".
  const pollRef = useRef<number | null>(null);
  useEffect(() => {
    if (mode !== "recording" || !sessionId) {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    const fetchActions = async () => {
      try {
        const r = await api.generate.record.actions(sessionId);
        setActions(r.actions || []);
      } catch {
        // Soft-fail: a single missed poll is fine; next tick will retry.
      }
    };
    void fetchActions();
    pollRef.current = window.setInterval(fetchActions, POLL_INTERVAL_MS);
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [mode, sessionId]);

  const handleStart = useCallback(async () => {
    if (!creds || !credsReady) {
      setError("Pick a workspace login above first.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      const r = await api.generate.record.start({
        sandbox_url: creds.sandboxUrl,
        username: creds.username,
        password: creds.password,
        persona_id: undefined,
      });
      setSessionId(r.session_id);
      setRecordStartedAt(performance.now());
      setActions([]);
      setMode("recording");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to start recording";
      // 403 / 503 from feature flag -- surface a friendly message.
      if (msg.includes("403") || msg.includes("not enabled")) {
        setError("Recording mode is not enabled for this deployment. Ask your admin to set PW_RECORDING=true in the backend environment.");
      } else if (msg.includes("503") || msg.includes("not installed")) {
        setError("Playwright is not installed on the server. Ask your admin to run 'python -m playwright install chromium'.");
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  }, [creds, credsReady]);

  const handleStop = useCallback(async () => {
    if (!sessionId) return;
    setBusy(true);
    setError("");
    try {
      const resp = await api.generate.record.stop(sessionId);
      setRobotCode(resp.robot_code || "");
      setTestPath(resp.test_path || "");
      setValidationOk(typeof resp.validation_ok === "boolean" ? resp.validation_ok : null);
      setValidationErrors(Array.isArray(resp.validation_errors) ? resp.validation_errors : []);
      setMode("post");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to stop recording");
    } finally {
      setBusy(false);
      setSessionId(null);
    }
  }, [sessionId]);

  const handleDiscard = useCallback(() => {
    setMode("pre");
    setSessionId(null);
    setActions([]);
    setRobotCode("");
    setValidationOk(null);
    setValidationErrors([]);
    setTestPath("");
    setError("");
    setRecordStartedAt(null);
  }, []);

  const recordingDurationMs =
    recordStartedAt !== null && now > 0 ? Math.max(0, now - recordStartedAt) : 0;
  const recordingDurationLabel = formatMs(recordingDurationMs);

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-4">
        <div className="flex items-center gap-3 text-xs text-slate-500 mb-2">
          <Link href="/generate" className="hover:text-white">
            Generate
          </Link>
          <span>/</span>
          <span className="text-slate-300">Record</span>
        </div>
        <h1 className="text-3xl md:text-4xl font-bold mb-1">
          <span className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text text-transparent">
            Record a test by clicking through Salesforce
          </span>
        </h1>
        <p className="text-slate-400 text-sm">
          Click Start, do your flow in the browser that opens, click Stop. We&apos;ll generate a
          Robot Framework test from your actions.
        </p>
      </motion.div>

      <WorkspaceBar onChange={setCreds} />

      {error && (
        <div className="mb-4 p-3 rounded-xl border border-red-400/30 bg-red-500/5 text-xs text-red-200">
          {error}
        </div>
      )}

      <AnimatePresence mode="wait">
        {mode === "pre" && (
          <motion.div
            key="pre"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="glass p-6 mb-6"
          >
            <h2 className="text-lg font-semibold text-white mb-2">Ready to record?</h2>
            <p className="text-sm text-slate-400 mb-5">
              When you click Start, a real Chromium browser opens and auto-logs in to your
              selected sandbox. Click through the flow you want to automate, then come back
              here and click Stop. We&apos;ll translate your clicks into a Robot Framework
              test using this project&apos;s keyword catalog.
            </p>

            {/* "How it works" inline guide. Lives on the pre-record screen
                so it's visible before the user commits to clicking Start
                but disappears once recording is underway (when on-screen
                guidance for the action log + Stop button is more useful). */}
            <div className="grid md:grid-cols-2 gap-4 mb-5">
              <div className="rounded-xl border border-fuchsia-400/20 bg-fuchsia-500/5 p-4">
                <div className="text-[10px] uppercase tracking-wider text-fuchsia-300 font-semibold mb-2">
                  How it works
                </div>
                <ol className="text-xs text-slate-300 space-y-1.5 list-decimal pl-4 marker:text-fuchsia-400">
                  <li>
                    Click <strong>Start Recording</strong>. A new Chromium window opens on
                    the server / your machine.
                  </li>
                  <li>
                    The browser navigates to your sandbox URL and logs in automatically
                    using the workspace credentials above.
                  </li>
                  <li>
                    <strong>Drive the flow yourself</strong> -- click buttons, type into
                    fields, pick from dropdowns, navigate between pages. Each action shows
                    up live in the <em>Captured actions</em> panel here.
                  </li>
                  <li>
                    When you&apos;re done, switch back to this tab and click{" "}
                    <strong>Stop Recording</strong>. We close the browser, send your
                    actions to the LLM translator, and produce a Robot test using your
                    Page Objects and shared keywords.
                  </li>
                  <li>
                    Review &amp; edit the generated script, then open it in the Generate
                    page to save / run.
                  </li>
                </ol>
              </div>

              <div className="rounded-xl border border-cyan-400/20 bg-cyan-500/5 p-4">
                <div className="text-[10px] uppercase tracking-wider text-cyan-300 font-semibold mb-2">
                  Tips for a clean recording
                </div>
                <ul className="text-xs text-slate-300 space-y-1.5 list-disc pl-4 marker:text-cyan-400">
                  <li>
                    Keep flows short and linear -- one outcome per recording (e.g. &ldquo;create
                    a Lead&rdquo;, not &ldquo;create a Lead, then convert it, then run a report&rdquo;).
                  </li>
                  <li>
                    Prefer clicking on <em>visible labels</em> (button text, field labels) rather
                    than icons -- the translator gets stronger locators that way.
                  </li>
                  <li>
                    Hovers, scrolls, and right-clicks are <strong>not</strong> captured. If
                    your test genuinely needs those, add them by hand to the generated
                    script.
                  </li>
                  <li>
                    The auto-login can&apos;t solve MFA prompts. Disable MFA on the
                    recording user, or pre-warm a session via the Playwright storage state.
                  </li>
                  <li>
                    You can open new pages by typing into the address bar or clicking links --
                    avoid opening extra tabs / windows, only the first tab is recorded.
                  </li>
                </ul>
              </div>
            </div>

            <button
              type="button"
              disabled={busy || !credsReady}
              onClick={handleStart}
              className="px-6 py-3 rounded-xl bg-gradient-to-r from-fuchsia-600 to-purple-600 text-white text-sm font-semibold disabled:opacity-50"
            >
              {busy ? "Starting..." : "Start Recording"}
            </button>
            {!credsReady && (
              <span className="ml-3 text-xs text-amber-300">
                Pick a workspace login above to enable Start.
              </span>
            )}
          </motion.div>
        )}

        {mode === "recording" && (
          <motion.div
            key="recording"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="grid md:grid-cols-2 gap-4 mb-6"
          >
            <div className="glass p-5">
              <div className="flex items-center gap-2 mb-3">
                <span className="h-2 w-2 rounded-full bg-red-400 animate-pulse" />
                <span className="text-sm font-semibold text-white">Recording</span>
                <span className="text-xs text-slate-500 ml-auto font-mono">
                  {recordingDurationLabel}
                </span>
              </div>
              <p className="text-sm text-slate-400 mb-2">
                A Chromium window has opened &mdash; <strong className="text-slate-200">switch
                to that window</strong> and click through your test scenario.
              </p>
              <p className="text-xs text-slate-500 mb-4 leading-relaxed">
                Each click, fill, and navigation appears live in the <em>Captured actions</em>
                panel on the right. When you&apos;re done, come back to this tab and hit
                Stop &mdash; we&apos;ll close the browser and generate your Robot script. Hit
                Cancel to throw the session away without generating anything.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={handleStop}
                className="px-6 py-3 rounded-xl bg-emerald-600 text-white text-sm font-semibold disabled:opacity-50"
              >
                {busy ? "Stopping..." : "Stop Recording"}
              </button>
              <button
                type="button"
                onClick={handleDiscard}
                className="ml-2 px-4 py-3 rounded-xl glass text-slate-300 text-sm hover:text-white"
              >
                Cancel
              </button>
            </div>

            <div className="glass p-5">
              <div className="flex items-center justify-between mb-3">
                <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
                  Captured actions
                </span>
                <span className="text-[11px] text-slate-400 font-mono">{actions.length}</span>
              </div>
              <div className="max-h-72 overflow-y-auto space-y-1 text-xs font-mono">
                {actions.length === 0 ? (
                  <p className="text-slate-500 italic">
                    No actions yet. The browser is loading or you haven&apos;t clicked anything.
                  </p>
                ) : (
                  actions.map((a, i) => (
                    <div key={i} className="text-slate-300 truncate">
                      <span className="text-cyan-300">{i + 1}.</span>{" "}
                      <span className="text-purple-300">{a.type}</span>
                      {a.text && <span className="ml-1 text-slate-200">&quot;{a.text}&quot;</span>}
                      {a.value && <span className="ml-1 text-amber-200">= {a.value}</span>}
                      {a.url && <span className="ml-1 text-slate-500">{a.url}</span>}
                    </div>
                  ))
                )}
              </div>
            </div>
          </motion.div>
        )}

        {mode === "post" && (
          <motion.div
            key="post"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mb-6"
          >
            <div className="glass p-5 mb-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-cyan-300">Generated test</h3>
                <div className="flex items-center gap-2">
                  {validationOk === true && (
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/20 border border-emerald-400/30 text-emerald-200">
                      Validated
                    </span>
                  )}
                  {validationOk === false && (
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-red-500/20 border border-red-400/30 text-red-200">
                      Validation failed · {validationErrors.length}
                    </span>
                  )}
                  {testPath && (
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 border border-white/10 text-slate-300">
                      {testPath.split(/[/\\]/).pop()}
                    </span>
                  )}
                </div>
              </div>
              <RobotCodeEditor value={robotCode} onChange={setRobotCode} height="380px" />
              <div className="flex items-center justify-end gap-2 mt-3">
                <button
                  type="button"
                  onClick={handleDiscard}
                  className="px-4 py-2 rounded-xl glass text-sm text-slate-300 hover:text-white"
                >
                  Discard
                </button>
                <Link href="/generate">
                  <span className="inline-block px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold">
                    Open in Generate
                  </span>
                </Link>
              </div>
            </div>

            <div className="glass p-5">
              <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-2">
                Captured actions ({actions.length})
              </span>
              <div className="text-xs font-mono text-slate-400 max-h-40 overflow-y-auto space-y-0.5">
                {actions.map((a, i) => (
                  <div key={i} className="truncate">
                    <span className="text-cyan-300">{i + 1}.</span>{" "}
                    <span className="text-purple-300">{a.type}</span>
                    {a.text && <span className="ml-1 text-slate-200">&quot;{a.text}&quot;</span>}
                    {a.value && <span className="ml-1 text-amber-200">= {a.value}</span>}
                  </div>
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function formatMs(ms: number): string {
  if (!isFinite(ms) || ms < 0) return "0s";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}m ${r.toString().padStart(2, "0")}s`;
}
