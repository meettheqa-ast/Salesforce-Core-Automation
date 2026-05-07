"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import RobotCodeEditor from "@/components/editor/RobotCodeEditor";
import ExecutionLogStream from "@/components/execution/ExecutionLogStream";
import StoryExecutionPanel from "@/components/execution/StoryExecutionPanel";
import WorkspaceBar, { type WorkspaceCreds } from "@/components/layout/WorkspaceBar";
import Segmented from "@/components/ui/Segmented";
import StepwisePipeline, {
  type PhaseTiming,
  type PipelinePhase,
  type PipelineStep,
} from "@/components/generate/StepwisePipeline";
import { api, prepareAuth } from "@/lib/api";

const TEMPLATES = [
  { label: "Create Lead", prompt: "Create a new Lead with auto-generated data and verify it was created" },
  { label: "Account CRUD", prompt: "Create an Account named Acme Corp, verify it exists, then delete it" },
  { label: "Update Opp", prompt: "Create an Opportunity, update its Stage to Closed Won, and verify" },
  { label: "Verify Contact", prompt: "Create a Contact and verify First Name, Last Name, and Email" },
];

type ExecMode = "background" | "watch";
type GenMode = "stepwise" | "quick";

type RunRequest = {
  test_path: string;
  sandbox_url: string;
  username: string;
  password: string;
  headless: boolean;
  /** Persona's default Salesforce app -- threaded into the runner as
   *  ${salesAutomationAppName} so PO keywords like
   *  SalesPO.Open New Lead From Sales App land in the right app
   *  (e.g. "Pentair Sales") instead of the generic "Sales" default. */
  default_app?: string;
};

const AUTO_DATA_HINT =
  "\n\n[Auto-generate data] Fill any required fields with realistic synthetic data (use Faker-style names, addresses, companies, emails). Use sensible Salesforce defaults for picklists.";

function loadPref<T extends string>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const v = window.localStorage.getItem(key);
    return (v as T) || fallback;
  } catch {
    return fallback;
  }
}

function savePref(key: string, value: string) {
  try { window.localStorage.setItem(key, value); } catch {}
}

export default function GeneratePage() {
  const [creds, setCreds] = useState<WorkspaceCreds | null>(null);
  // Watch is the default so the user can SEE the browser drive Salesforce
  // in real time -- it's what new users expect from a "test automation"
  // demo. Background headless mode is still available via the toggle for
  // long bulk runs / CI-like usage.
  const [execMode, setExecMode] = useState<ExecMode>("watch");
  const [genMode, setGenMode] = useState<GenMode>("stepwise");
  const [prompt, setPrompt] = useState("");
  const [testName, setTestName] = useState("");
  const [autoData, setAutoData] = useState(true);

  const [loading, setLoading] = useState(false);
  const [robotCode, setRobotCode] = useState("");
  const [testPath, setTestPath] = useState("");
  const [error, setError] = useState("");
  const [genComplete, setGenComplete] = useState(false);

  // Validation surfaces from the new validate-fix-validate pipeline.
  // ``validationOk === null`` means the generator returned no validation
  // metadata yet (first paint, or legacy backend) -- treat as
  // optimistically OK so we don't block the Run button on a stale tab.
  type ValidationErr = {
    line: number;
    column: number;
    kind: string;
    symbol: string;
    message: string;
    closest_matches: string[];
    snippet: string;
  };
  type ValidationAttempt = {
    attempt: number;
    ok: boolean;
    error_count: number;
    fix_prompt_excerpt: string;
    script_excerpt: string;
  };
  const [validationOk, setValidationOk] = useState<boolean | null>(null);
  const [validationErrors, setValidationErrors] = useState<ValidationErr[]>([]);
  const [validationAttempts, setValidationAttempts] = useState<ValidationAttempt[]>([]);
  const [validationTrailOpen, setValidationTrailOpen] = useState(false);

  // LLM-provider failover events. Populated when the primary LLM hit a
  // quota / rate-limit / auth / availability error and ``call_llm``
  // automatically fell over to a configured backup. Renders a small
  // banner above the script preview so the user sees, e.g.,
  // "Switched from Gemini to Groq because Gemini hit its quota."
  type ProviderSwitch = {
    from_provider: string;
    from_label: string;
    to_provider: string;
    to_label: string;
    reason: string;
    error_excerpt: string;
  };
  const [providerSwitches, setProviderSwitches] = useState<ProviderSwitch[]>([]);

  const [phase, setPhase] = useState<PipelinePhase>("idle");
  const [steps, setSteps] = useState<PipelineStep[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  // Closed phases with their measured wall-clock duration. Populated each
  // time the backend transitions phases (its `phase` event carries the
  // elapsed_ms_phase the *previous* phase took -- so on transition N+1 we
  // close out phase N and open phase N+1 cleanly).
  const [phaseTimings, setPhaseTimings] = useState<PhaseTiming[]>([]);
  // Wall-clock of the current (still-open) phase. Tracked client-side via
  // performance.now() so the badge ticks live without server pushes.
  const [phaseStartedAt, setPhaseStartedAt] = useState<number | null>(null);
  const [streamStartedAt, setStreamStartedAt] = useState<number | null>(null);
  const [tickMs, setTickMs] = useState<number>(0);

  // Mirror of `phase` we read synchronously from the SSE handler. Without
  // this we'd have to reach into setPhase's updater fn to know the current
  // phase, but doing setPhaseTimings() inside that updater is a side-effect
  // inside a state updater -- React 18 strict mode invokes updaters twice
  // in development to surface exactly this bug, which produced duplicate
  // timing pills ("Planning steps 31 s, Planning steps 31 s") in the UI.
  const phaseRef = useRef<PipelinePhase>("idle");
  const phaseStartedAtRef = useRef<number | null>(null);

  const [runRequest, setRunRequest] = useState<RunRequest | null>(null);
  const sourceRef = useRef<{ close: () => void } | null>(null);

  // Tick the live "current phase" timer at 4 Hz while a phase is open and
  // we're not yet done. Cheap, and avoids the worst "is this hung?" UX.
  useEffect(() => {
    if (phaseStartedAt === null || phase === "done" || phase === "idle") return;
    const id = window.setInterval(() => {
      setTickMs(performance.now());
    }, 250);
    return () => window.clearInterval(id);
  }, [phaseStartedAt, phase]);

  // Restore preferences once mounted (avoids hydration mismatch).
  useEffect(() => {
    void Promise.resolve().then(() => {
      setExecMode(loadPref<ExecMode>("gen.execMode", "watch"));
      setGenMode(loadPref<GenMode>("gen.genMode", "stepwise"));
    });
  }, []);

  useEffect(() => savePref("gen.execMode", execMode), [execMode]);
  useEffect(() => savePref("gen.genMode", genMode), [genMode]);

  useEffect(() => {
    return () => sourceRef.current?.close();
  }, []);

  const headless = execMode === "background";
  const credsReady = Boolean(creds?.sandboxUrl && creds.username && creds.password);

  const buildPrompt = (raw: string): string => {
    let out = raw.trim();
    if (autoData) out += AUTO_DATA_HINT;
    return out;
  };

  const resetGenerationState = () => {
    setError("");
    setRobotCode("");
    setTestPath("");
    setRunRequest(null);
    setGenComplete(false);
    setPhase("idle");
    setSteps([]);
    setNotes([]);
    setPhaseTimings([]);
    setPhaseStartedAt(null);
    setStreamStartedAt(null);
    setTickMs(0);
    phaseRef.current = "idle";
    phaseStartedAtRef.current = null;
    setValidationOk(null);
    setValidationErrors([]);
    setValidationAttempts([]);
    setValidationTrailOpen(false);
    setProviderSwitches([]);
  };

  const runQuickGenerate = async (rawPrompt: string) => {
    const payload = {
      prompt: buildPrompt(rawPrompt),
      sandbox_url: creds?.sandboxUrl ?? "",
      username: creds?.username ?? "",
      password: creds?.password ?? "",
      // Persona's default app -- threaded through so the LLM injects
      // ${salesAutomationAppName} = "<this>" and the generated script
      // lands in the right Salesforce app (Pentair Sales etc.) instead
      // of the global "Sales" default.
      default_app: creds?.defaultApp ?? "",
      generation_mode: "quick",
      test_name: testName.trim() || undefined,
      headless,
    };
    setPhase("planning");
    const res = await api.generate.quick(payload);
    setRobotCode(res.robot_code || "");
    setTestPath(res.test_path || "");
    if (Array.isArray(res.lint_errors) && res.lint_errors.length) {
      setNotes(res.lint_errors);
    }
    // New validation surfaces. ``validation_ok`` defaults to true on a
    // legacy backend that doesn't emit it -- read it explicitly so a
    // truly-failing validation toggles the Run button.
    if (typeof res.validation_ok === "boolean") {
      setValidationOk(res.validation_ok);
    }
    if (Array.isArray(res.validation_errors)) {
      setValidationErrors(res.validation_errors as ValidationErr[]);
    }
    if (Array.isArray(res.validation_attempts)) {
      setValidationAttempts(res.validation_attempts as ValidationAttempt[]);
    }
    if (Array.isArray((res as any).provider_switches)) {
      setProviderSwitches((res as any).provider_switches as ProviderSwitch[]);
    }
    setPhase("done");
  };

  const runStepwiseStream = async (rawPrompt: string) => {
    const payload = {
      prompt: buildPrompt(rawPrompt),
      sandbox_url: creds?.sandboxUrl ?? "",
      username: creds?.username ?? "",
      password: creds?.password ?? "",
      // See runQuickGenerate -- same fix applies to the Stepwise planner.
      default_app: creds?.defaultApp ?? "",
      generation_mode: "mcp_stepwise",
      test_name: testName.trim() || undefined,
      headless,
    };
    // Pre-warm the auth token cache so we can attach the bearer header below.
    // The query-string `?token=` fallback in stepwiseStreamUrl() is a belt-and-
    // braces backup; the header path is the canonical one for POST + fetch.
    await prepareAuth();
    const url = api.generate.stepwiseStreamUrl();
    // Read the cached token directly so we can put it in the header. We can't
    // import the cache from lib/api (private), so we re-fetch via /api/auth/jwt;
    // the response is cached server-side with no-store but client-side this
    // hits the in-memory cache `prepareAuth` just warmed.
    const tokenResp = await fetch("/api/auth/jwt", { credentials: "include", cache: "no-store" });
    const bearer = tokenResp.ok ? (await tokenResp.text()).trim() : "";
    return new Promise<void>((resolve, reject) => {
      let resolved = false;
      const finish = (err?: unknown) => {
        if (resolved) return;
        resolved = true;
        if (err) reject(err);
        else resolve();
      };
      // Use fetch to POST body and read SSE manually (EventSource is GET-only).
      const ctrl = new AbortController();
      sourceRef.current = { close: () => ctrl.abort() };
      fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
        },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      }).then(async (resp) => {
        if (!resp.ok || !resp.body) {
          const text = await resp.text().catch(() => "");
          throw new Error(`Stream ${resp.status}: ${text || resp.statusText}`);
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        const handle = (event: string, data: string) => {
          try {
            const obj = data ? JSON.parse(data) : {};
            if (event === "phase") {
              const next = (obj.name as PipelinePhase) || "executing";
              const current = phaseRef.current;
              // Backend tells us how long the *previous* phase took via
              // elapsed_ms_phase. Stamp it into our running tape, then open
              // the new phase. Reading from phaseRef (not setPhase's updater
              // arg) keeps the timing append outside any state-updater fn,
              // which avoids React 18 strict-mode's double-invocation
              // duplicating every pill.
              if (current !== "idle") {
                const prevElapsed =
                  typeof obj.elapsed_ms_phase === "number"
                    ? obj.elapsed_ms_phase
                    : 0;
                setPhaseTimings((prev) => [
                  ...prev,
                  { phase: current, elapsed_ms: prevElapsed },
                ]);
              }
              const now = performance.now();
              phaseRef.current = next;
              phaseStartedAtRef.current = now;
              setPhase(next);
              setPhaseStartedAt(now);
              setStreamStartedAt((prev) => prev ?? now);
              setTickMs(now);
            }
            else if (event === "note") setNotes((prev) => [...prev, String(obj.message || data)]);
            else if (event === "step") setSteps((prev) => [...prev, obj as PipelineStep]);
            else if (event === "result") {
              setRobotCode(obj.robot_code || "");
              setTestPath(obj.test_path || "");
              if (Array.isArray(obj.lint_errors) && obj.lint_errors.length) {
                setNotes((prev) => [...prev, ...obj.lint_errors]);
              }
              if (typeof obj.validation_ok === "boolean") {
                setValidationOk(obj.validation_ok);
              }
              if (Array.isArray(obj.validation_errors)) {
                setValidationErrors(obj.validation_errors as ValidationErr[]);
              }
              if (Array.isArray(obj.validation_attempts)) {
                setValidationAttempts(obj.validation_attempts as ValidationAttempt[]);
              }
              if (Array.isArray(obj.provider_switches)) {
                setProviderSwitches(obj.provider_switches as ProviderSwitch[]);
              }
              // Result frame doesn't carry a phase change, but we want to
              // close out whatever phase was open so its duration shows up
              // in the timing tape.
              const current = phaseRef.current;
              const startedAt = phaseStartedAtRef.current;
              if (current !== "idle" && current !== "done" && startedAt !== null) {
                setPhaseTimings((prev) => [
                  ...prev,
                  { phase: current, elapsed_ms: performance.now() - startedAt },
                ]);
              }
              phaseRef.current = "done";
              setPhase("done");
            } else if (event === "error") {
              setError(String(obj.message || "Generation failed"));
            }
          } catch {
            // ignore parse errors per-event
          }
        };
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let nl;
          while ((nl = buffer.indexOf("\n\n")) !== -1) {
            const frame = buffer.slice(0, nl);
            buffer = buffer.slice(nl + 2);
            const lines = frame.split("\n");
            let event = "message";
            const dataLines: string[] = [];
            for (const line of lines) {
              if (line.startsWith("event:")) event = line.slice(6).trim();
              else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
            }
            if (dataLines.length) handle(event, dataLines.join("\n"));
          }
        }
        finish();
      }).catch((err) => {
        if ((err as { name?: string }).name === "AbortError") finish();
        else finish(err);
      });
    });
  };

  const handleGenerate = useCallback(async (rawPrompt: string) => {
    if (!rawPrompt.trim()) {
      setError("Please describe what to test.");
      return;
    }
    sourceRef.current?.close();
    sourceRef.current = null;
    setLoading(true);
    resetGenerationState();
    try {
      if (genMode === "stepwise") {
        await runStepwiseStream(rawPrompt);
      } else {
        await runQuickGenerate(rawPrompt);
      }
      setGenComplete(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Generation failed");
      setPhase("idle");
    } finally {
      setLoading(false);
    }
  }, [genMode, creds, autoData, testName, headless]);

  const handleRun = () => {
    if (!testPath || !credsReady || !creds) {
      setError("Generate a script and configure a workspace login first.");
      return;
    }
    setRunRequest({
      test_path: testPath,
      sandbox_url: creds.sandboxUrl,
      username: creds.username,
      password: creds.password,
      headless,
      default_app: creds.defaultApp || undefined,
    });
  };

  const handleDiscard = () => {
    sourceRef.current?.close();
    sourceRef.current = null;
    resetGenerationState();
  };

  const generatedFile = testPath ? testPath.split(/[/\\]/).pop() : "";

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-4">
        <h1 className="text-3xl md:text-4xl font-bold mb-1">
          <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
            What would you like to test today?
          </span>
        </h1>
        <p className="text-slate-400 text-sm">
          Configure your workspace, choose a mode, then describe the test in plain English.
        </p>
      </motion.div>

      <WorkspaceBar onChange={setCreds} />

      {/* Mode toggles */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 mb-4">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">Execution</span>
          <Segmented<ExecMode>
            value={execMode}
            onChange={setExecMode}
            options={[
              { value: "watch", label: "Watch", icon: "◉" },
              { value: "background", label: "Background", icon: "▷" },
            ]}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">Generation</span>
          <Segmented<GenMode>
            value={genMode}
            onChange={setGenMode}
            options={[
              { value: "stepwise", label: "MCP Stepwise", icon: "✓" },
              { value: "quick", label: "Quick Generate", icon: "⚡" },
            ]}
          />
        </div>
      </div>

      {/* Quick Start + Test name + Auto data */}
      <div className="glass p-3 mb-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">Quick start</span>
          <span className="text-[10px] text-slate-600">click a template to fill the prompt</span>
        </div>
        <div className="flex flex-wrap gap-2 mb-3">
          {TEMPLATES.map((t) => (
            <button
              key={t.label}
              type="button"
              onClick={() => setPrompt(t.prompt)}
              className="text-xs px-3 py-1.5 glass rounded-full text-slate-300 hover:text-white hover:bg-purple-500/15 transition-colors"
              title={t.prompt}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-12 gap-2 items-center">
          <div className="col-span-12 sm:col-span-7">
            <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Test name (optional)</div>
            <input
              value={testName}
              onChange={(e) => setTestName(e.target.value)}
              placeholder="Auto-filled from prompt if blank"
              className="w-full bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-purple-500"
            />
          </div>
          <label className="col-span-12 sm:col-span-5 flex items-center gap-2 text-xs text-slate-300 mt-2 sm:mt-0">
            <input
              type="checkbox"
              checked={autoData}
              onChange={(e) => setAutoData(e.target.checked)}
              className="accent-purple-500"
            />
            Auto-generate test data
            <span className="text-slate-600 text-[10px]">(AI + Faker fills required fields)</span>
          </label>
        </div>
      </div>

      {/* Prompt + Generate */}
      <div className="glass p-3 mb-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">Prompt</span>
          {!credsReady && (
            <span className="text-[10px] text-amber-300">Pick a workspace login above to enable Run after generate.</span>
          )}
        </div>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe your test in plain English. E.g. Create a Lead named Demo User in Astound TIP."
          rows={4}
          className="w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500 font-mono"
        />
        <div className="flex justify-end mt-2">
          <button
            type="button"
            disabled={loading || !prompt.trim()}
            onClick={() => handleGenerate(prompt)}
            className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50"
          >
            {loading ? (genMode === "stepwise" ? "Streaming…" : "Generating…") : "Generate Script"}
          </button>
        </div>
      </div>

      {/* Stepwise pipeline */}
      <StepwisePipeline
        phase={phase}
        steps={steps}
        notes={notes}
        timings={phaseTimings}
        currentPhaseElapsedMs={
          phaseStartedAt !== null && phase !== "done" && phase !== "idle"
            ? Math.max(0, tickMs - phaseStartedAt)
            : undefined
        }
        totalElapsedMs={
          streamStartedAt !== null
            ? Math.max(0, (phase === "done" ? tickMs || performance.now() : tickMs) - streamStartedAt)
            : undefined
        }
      />

      {/* Status strip */}
      <AnimatePresence>
        {(genComplete || error) && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="space-y-1 mb-4"
          >
            {genComplete && testPath && (
              <div className="text-xs text-cyan-300">
                Generated suite: <span className="font-mono text-cyan-200">{testPath}</span>
              </div>
            )}
            {genComplete && robotCode && (
              <div className="text-xs text-emerald-300">
                Generation complete — review the script below, then click Run or Discard.
              </div>
            )}
            {genComplete && !robotCode && !error && (
              <div className="text-xs text-amber-300">
                The generation stream finished without returning a script. Check the
                pipeline above for fallback notes, or try again with a more specific prompt.
              </div>
            )}
            {error && (
              <div className="text-xs text-red-300">
                {error}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* LLM-provider failover banner. One row per switch so the user
          sees exactly which provider was active when the script came
          back. Survives across both Quick Generate and the Stepwise
          stream because both response shapes carry provider_switches. */}
      <AnimatePresence>
        {providerSwitches.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mb-4 p-3 rounded-xl border border-amber-400/30 bg-amber-500/5"
          >
            {providerSwitches.map((sw, i) => (
              <div key={i} className="text-xs text-amber-200">
                <span className="font-semibold">LLM switched:</span>{" "}
                <span className="text-slate-300">{sw.from_label}</span>{" "}
                <span className="text-slate-500">→</span>{" "}
                <span className="text-cyan-200 font-semibold">{sw.to_label}</span>{" "}
                <span className="text-slate-400">
                  (reason: {sw.reason})
                </span>
                {sw.error_excerpt && (
                  <div className="mt-1 text-[10px] text-slate-500 font-mono truncate">
                    {sw.error_excerpt}
                  </div>
                )}
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Review section */}
      <AnimatePresence>
        {robotCode && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mb-6"
          >
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold text-cyan-300">Review generated Robot</h3>
              <div className="flex items-center gap-2">
                {/* Validation badge. We render three states:
                    - ok=true (or no metadata): green "Validated"
                    - ok=false: red "Validation failed (N issues)"
                    - attempts>1 even when ok: amber "Self-corrected after N tries"
                    so the user sees the new safety-net at a glance. */}
                {validationOk === false && (
                  <span
                    className="text-[10px] font-mono px-2 py-0.5 rounded bg-red-500/20 border border-red-400/30 text-red-200"
                    title="The script has unresolved keywords or variables; running it will fail."
                  >
                    Validation failed · {validationErrors.length} issue
                    {validationErrors.length === 1 ? "" : "s"}
                  </span>
                )}
                {validationOk === true && validationAttempts.length > 1 && (
                  <span
                    className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/20 border border-amber-400/30 text-amber-200"
                    title="The first generation had errors; the model was asked to self-correct."
                  >
                    Self-corrected · {validationAttempts.length} attempts
                  </span>
                )}
                {validationOk === true && validationAttempts.length <= 1 && (
                  <span
                    className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/20 border border-emerald-400/30 text-emerald-200"
                  >
                    Validated
                  </span>
                )}
                {generatedFile && (
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 border border-white/10 text-slate-300">
                    {generatedFile}
                  </span>
                )}
              </div>
            </div>

            {/* Validation errors panel: visible only when the loop didn't
                 converge. Renders one row per error with the offending
                 line/column, the symbol, and the model's suggested
                 alternatives so the user can hand-edit before running. */}
            {validationOk === false && validationErrors.length > 0 && (
              <div className="mb-3 p-3 rounded-xl border border-red-400/30 bg-red-500/5">
                <div className="text-xs font-semibold text-red-200 mb-2">
                  Validator found {validationErrors.length} issue
                  {validationErrors.length === 1 ? "" : "s"} in the generated script.
                  The Run button is disabled until you fix them.
                </div>
                <ul className="space-y-1.5">
                  {validationErrors.slice(0, 12).map((err, idx) => (
                    <li key={idx} className="text-[11px] text-slate-200 font-mono">
                      <span className="text-red-300">
                        L{err.line || "—"}
                      </span>
                      <span className="ml-2 text-slate-400">[{err.kind}]</span>
                      <span className="ml-2 text-amber-200">{err.symbol}</span>
                      {err.message && (
                        <div className="ml-6 text-slate-300 text-[10px]">
                          {err.message}
                        </div>
                      )}
                      {err.snippet && (
                        <div className="ml-6 text-slate-500 text-[10px] truncate">
                          on: <span className="text-slate-300">{err.snippet}</span>
                        </div>
                      )}
                      {err.closest_matches.length > 0 && (
                        <div className="ml-6 text-[10px] text-cyan-300">
                          did you mean:{" "}
                          {err.closest_matches.map((m, i) => (
                            <span key={i} className="ml-1">
                              <code className="px-1 py-0.5 rounded bg-cyan-500/10 border border-cyan-400/20">
                                {m}
                              </code>
                            </span>
                          ))}
                        </div>
                      )}
                    </li>
                  ))}
                  {validationErrors.length > 12 && (
                    <li className="text-[10px] text-slate-400">
                      …and {validationErrors.length - 12} more
                    </li>
                  )}
                </ul>
              </div>
            )}

            {/* Self-correction trail: collapsible. Available even when
                 validation passed so the user can audit how the model
                 converged on a clean script. */}
            {validationAttempts.length > 1 && (
              <div className="mb-3">
                <button
                  type="button"
                  onClick={() => setValidationTrailOpen((v) => !v)}
                  className="text-[10px] text-slate-400 hover:text-slate-200"
                >
                  {validationTrailOpen ? "▾" : "▸"} Self-correction trail (
                  {validationAttempts.length} attempts)
                </button>
                {validationTrailOpen && (
                  <div className="mt-2 space-y-2">
                    {validationAttempts.map((att) => (
                      <div
                        key={att.attempt}
                        className="text-[10px] font-mono p-2 rounded bg-white/5 border border-white/10"
                      >
                        <div className="text-slate-300">
                          Attempt {att.attempt} ·{" "}
                          {att.ok ? (
                            <span className="text-emerald-300">passed</span>
                          ) : (
                            <span className="text-red-300">{att.error_count} error(s)</span>
                          )}
                        </div>
                        {att.fix_prompt_excerpt && (
                          <div className="mt-1 text-slate-400 whitespace-pre-wrap">
                            {att.fix_prompt_excerpt}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <RobotCodeEditor value={robotCode} onChange={setRobotCode} height="380px" />
            <div className="flex items-center justify-end gap-2 mt-3">
              <button
                type="button"
                onClick={handleDiscard}
                className="px-4 py-2 rounded-xl glass text-sm text-slate-300 hover:text-white"
              >
                Discard
              </button>
              <button
                type="button"
                onClick={handleRun}
                disabled={!credsReady || validationOk === false}
                title={
                  validationOk === false
                    ? "Validation failed -- fix the issues above before running"
                    : credsReady
                      ? "Run the generated script"
                      : "Select a workspace login first"
                }
                className="px-4 py-2 rounded-xl bg-emerald-600 text-white text-sm font-semibold disabled:opacity-50"
              >
                Run
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Live execution log */}
      <ExecutionLogStream runRequest={runRequest} />

      {/* Bulk execution by user story / tag */}
      <StoryExecutionPanel />
    </div>
  );
}
