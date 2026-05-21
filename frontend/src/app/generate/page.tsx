"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useSearchParams } from "next/navigation";
import RobotCodeEditor from "@/components/editor/RobotCodeEditor";
import ExecutionLogStream from "@/components/execution/ExecutionLogStream";
import StoryExecutionPanel from "@/components/execution/StoryExecutionPanel";
import WorkspaceBar, { type WorkspaceCreds } from "@/components/layout/WorkspaceBar";
import Segmented from "@/components/ui/Segmented";
import StepwisePipeline, {
  type HealEvent,
  type PhaseTiming,
  type PipelinePhase,
  type PipelineStep,
} from "@/components/generate/StepwisePipeline";
import { api, prepareAuth } from "@/lib/api";
import { PageHeader, PageScaffold, PageSection } from "@/components/layout/PageScaffold";

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
  const searchParams = useSearchParams();
  const projectSlug = searchParams.get("project") || "";
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
  const [savingScript, setSavingScript] = useState(false);

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
    // Phase 1 Playwright fields. Optional for backwards compatibility;
    // only populated when ``kind === "locator_not_found"``.
    page_url?: string;
    suggested_locator?: string;
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

  // Phase 1: locator-validation surface. ``locatorOk`` is null when the
  // gate didn't run (production default). When the gate ran, the count
  // + failed numbers drive the pill text. Shadow mode is purely
  // observational; the user-facing label says "observation only" so
  // they understand a failed locator wasn't blocking their save.
  const [locatorOk, setLocatorOk] = useState<boolean | null>(null);
  const [locatorCount, setLocatorCount] = useState<number>(0);
  const [locatorFailed, setLocatorFailed] = useState<number>(0);
  const [locatorShadow, setLocatorShadow] = useState<boolean>(false);

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

  // When the deterministic recipe-first tier matched the prompt, the
  // backend renders the script directly from a parameterized template
  // (no LLM call) and surfaces the recipe name. We render a small
  // "Generated from recipe: X" badge so the user sees the deterministic
  // path fired and knows the script is reproducible / cheap.
  const [usedRecipe, setUsedRecipe] = useState<string | null>(null);
  const [usedRecipeConfidence, setUsedRecipeConfidence] = useState<string | null>(null);

  // Discoverable recipe catalog. Loaded once on mount; clicking a row
  // pre-fills the prompt textarea with the recipe's sample prompt
  // (which the matcher then re-matches at submit time -> deterministic
  // render -> done, no LLM call). Empty array when the backend doesn't
  // know about recipes (older deploys) -- the panel just hides itself.
  // ``display_name`` is what we render on cards / in the banner;
  // ``name`` stays the stable internal id used by the matcher.
  type RecipeMeta = {
    name: string;
    display_name?: string;
    description: string;
    sample_prompt: string;
  };
  const [recipes, setRecipes] = useState<RecipeMeta[]>([]);

  const [phase, setPhase] = useState<PipelinePhase>("idle");
  const [steps, setSteps] = useState<PipelineStep[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [healEvents, setHealEvents] = useState<HealEvent[]>([]);
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
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<"idle" | "queued" | "running" | "succeeded" | "failed" | "cancelled">("idle");
  const [recoverableJobId, setRecoverableJobId] = useState<string | null>(null);
  const [liveLogOpen, setLiveLogOpen] = useState(false);
  const lastSeqRef = useRef(0);
  const reconnectAttemptsRef = useRef(0);

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

  // Load deterministic-recipe catalog once on mount. Failure is silent
  // (older backends don't have the endpoint; the panel just stays hidden).
  useEffect(() => {
    void api.generate.recipes()
      .then((rs) => Array.isArray(rs) && setRecipes(rs))
      .catch(() => setRecipes([]));
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
    setCurrentJobId(null);
    setJobStatus("idle");
    setRecoverableJobId(null);
    setLiveLogOpen(false);
    lastSeqRef.current = 0;
    reconnectAttemptsRef.current = 0;
    setGenComplete(false);
    setPhase("idle");
    setSteps([]);
    setNotes([]);
    setHealEvents([]);
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
    setUsedRecipe(null);
    setUsedRecipeConfidence(null);
    setLocatorOk(null);
    setLocatorCount(0);
    setLocatorFailed(0);
    setLocatorShadow(false);
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
    // Recipe-first short-circuit: backend may have generated the
    // script deterministically without calling any LLM. Capture both
    // the recipe name and its match-confidence so the UI can show
    // a badge and (later) a "see template" deep link.
    setUsedRecipe(((res as any).used_recipe as string | null) || null);
    setUsedRecipeConfidence(((res as any).used_recipe_confidence as string | null) || null);
    // Phase 1: locator validation surface. ``null`` means the gate
    // didn't run for this generation (default in production); we then
    // skip rendering the pill entirely.
    if ("locator_validation_ok" in (res as any)) {
      const v = (res as any).locator_validation_ok;
      setLocatorOk(typeof v === "boolean" ? v : null);
      setLocatorCount(((res as any).locator_validation_count as number) ?? 0);
      setLocatorFailed(((res as any).locator_validation_failed as number) ?? 0);
      setLocatorShadow(Boolean((res as any).locator_validation_shadow));
    }
    setPhase("done");
  };

  const applyStepwiseEvent = (event: string, raw: any) => {
    const obj = raw || {};
    if (typeof obj.seq === "number") lastSeqRef.current = obj.seq;
    if (event === "phase") {
      const next = (obj.name as PipelinePhase) || "executing";
      const current = phaseRef.current;
      if (current !== "idle") {
        const prevElapsed = typeof obj.elapsed_ms_phase === "number" ? obj.elapsed_ms_phase : 0;
        setPhaseTimings((prev) => [...prev, { phase: current, elapsed_ms: prevElapsed }]);
      }
      const now = performance.now();
      phaseRef.current = next;
      phaseStartedAtRef.current = now;
      setPhase(next);
      setPhaseStartedAt(now);
      setStreamStartedAt((prev) => prev ?? now);
      setTickMs(now);
      if (next === "failed") setJobStatus("failed");
      return;
    }
    if (event === "note") {
      setNotes((prev) => [...prev, String(obj.message || "")]);
      return;
    }
    if (event === "step") {
      setSteps((prev) => [...prev, obj as PipelineStep]);
      return;
    }
    if (event === "heal") {
      setHealEvents((prev) => [...prev, obj as HealEvent]);
      return;
    }
    if (event === "result") {
      setRobotCode(obj.robot_code || "");
      setTestPath(obj.test_path || "");
      if (Array.isArray(obj.lint_errors) && obj.lint_errors.length) {
        setNotes((prev) => [...prev, ...obj.lint_errors]);
      }
      if (typeof obj.validation_ok === "boolean") setValidationOk(obj.validation_ok);
      if (Array.isArray(obj.validation_errors)) setValidationErrors(obj.validation_errors as ValidationErr[]);
      if (Array.isArray(obj.validation_attempts)) setValidationAttempts(obj.validation_attempts as ValidationAttempt[]);
      if (Array.isArray(obj.provider_switches)) setProviderSwitches(obj.provider_switches as ProviderSwitch[]);
      if ("locator_validation_ok" in obj) {
        const v = obj.locator_validation_ok;
        setLocatorOk(typeof v === "boolean" ? v : null);
        setLocatorCount((obj.locator_validation_count as number) ?? 0);
        setLocatorFailed((obj.locator_validation_failed as number) ?? 0);
        setLocatorShadow(Boolean(obj.locator_validation_shadow));
      }
      const current = phaseRef.current;
      const startedAt = phaseStartedAtRef.current;
      const doneAt = performance.now();
      if (current !== "idle" && current !== "done" && startedAt !== null) {
        setPhaseTimings((prev) => [...prev, { phase: current, elapsed_ms: doneAt - startedAt }]);
      }
      phaseRef.current = "done";
      setPhase("done");
      setTickMs(doneAt);
      setJobStatus("succeeded");
      return;
    }
    if (event === "error") {
      setError(String(obj.message || "Generation failed"));
      setJobStatus("failed");
    }
  };

  const runStepwiseStream = async (rawPrompt: string) => {
    const payload = {
      prompt: buildPrompt(rawPrompt),
      sandbox_url: creds?.sandboxUrl ?? "",
      username: creds?.username ?? "",
      password: creds?.password ?? "",
      default_app: creds?.defaultApp ?? "",
      generation_mode: "mcp_stepwise",
      test_name: testName.trim() || undefined,
      headless,
    };
    await prepareAuth();
    const created = await api.generate.jobs.create(payload);
    const jobId = created.job_id;
    setCurrentJobId(jobId);
    setJobStatus("queued");
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem("gen.activeJobId", jobId);
    }
    return new Promise<void>((resolve, reject) => {
      let finished = false;
      let lastError: Error | null = null;
      const open = (fromSeq: number) => {
        const es = new EventSource(api.generate.jobs.eventsUrl(jobId, fromSeq));
        sourceRef.current = { close: () => es.close() };
        es.onmessage = () => {};
        es.addEventListener("phase", (e) => {
          try {
            const payload = JSON.parse((e as MessageEvent).data || "{}");
            applyStepwiseEvent("phase", payload);
            if (payload.name === "executing" || payload.name === "planning" || payload.name === "session") {
              setJobStatus("running");
            }
          } catch {}
        });
        es.addEventListener("note", (e) => {
          try { applyStepwiseEvent("note", JSON.parse((e as MessageEvent).data || "{}")); } catch {}
        });
        es.addEventListener("step", (e) => {
          try { applyStepwiseEvent("step", JSON.parse((e as MessageEvent).data || "{}")); } catch {}
        });
        es.addEventListener("heal", (e) => {
          try { applyStepwiseEvent("heal", JSON.parse((e as MessageEvent).data || "{}")); } catch {}
        });
        es.addEventListener("result", (e) => {
          if (finished) return;
          try { applyStepwiseEvent("result", JSON.parse((e as MessageEvent).data || "{}")); } catch {}
          finished = true;
          es.close();
          resolve();
        });
        es.addEventListener("error", (e) => {
          let msg = "Generation failed";
          try {
            const payload = JSON.parse((e as MessageEvent).data || "{}");
            msg = String(payload.message || msg);
            applyStepwiseEvent("error", payload);
          } catch {}
          // EventSource also triggers generic onerror on disconnect; terminal
          // error payloads are handled here first and complete the promise.
          if (!finished) {
            finished = true;
            es.close();
            reject(new Error(msg));
          }
        });
        es.onerror = async () => {
          if (finished) return;
          es.close();
          reconnectAttemptsRef.current += 1;
          if (reconnectAttemptsRef.current <= 3) {
            const delay = 1000 * reconnectAttemptsRef.current;
            window.setTimeout(() => open(lastSeqRef.current), delay);
            return;
          }
          try {
            const snap = await api.generate.jobs.status(jobId);
            if (snap.status === "succeeded") {
              const maybeResult = [...(snap.events || [])].reverse().find((ev) => ev.event === "result");
              if (maybeResult) applyStepwiseEvent("result", maybeResult.payload);
              finished = true;
              resolve();
              return;
            }
            if (snap.status === "cancelled") {
              finished = true;
              reject(new Error("Generation cancelled"));
              return;
            }
            if (snap.status === "failed") {
              const maybeError = [...(snap.events || [])].reverse().find((ev) => ev.event === "error");
              const msg = maybeError?.payload?.message || snap.error_message || "Generation failed";
              finished = true;
              reject(new Error(msg));
              return;
            }
          } catch (err) {
            lastError = err instanceof Error ? err : new Error("Stepwise stream disconnected");
          }
          finished = true;
          reject(lastError || new Error("Stepwise stream disconnected"));
        };
      };
      open(0);
    });
  };

  const attachToExistingJob = useCallback(async (jobId: string) => {
    setCurrentJobId(jobId);
    setJobStatus("running");
    setLoading(true);
    setError("");
    setRecoverableJobId(null);
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem("gen.activeJobId", jobId);
    }
    const snap = await api.generate.jobs.status(jobId);
    for (const ev of snap.events || []) {
      applyStepwiseEvent(ev.event, { ...(ev.payload || {}), seq: ev.seq, ts: ev.ts });
    }
    lastSeqRef.current = snap.event_seq || 0;
    const es = new EventSource(api.generate.jobs.eventsUrl(jobId, lastSeqRef.current));
    sourceRef.current = { close: () => es.close() };
    es.addEventListener("phase", (e) => {
      try { applyStepwiseEvent("phase", JSON.parse((e as MessageEvent).data || "{}")); } catch {}
    });
    es.addEventListener("note", (e) => {
      try { applyStepwiseEvent("note", JSON.parse((e as MessageEvent).data || "{}")); } catch {}
    });
    es.addEventListener("step", (e) => {
      try { applyStepwiseEvent("step", JSON.parse((e as MessageEvent).data || "{}")); } catch {}
    });
    es.addEventListener("heal", (e) => {
      try { applyStepwiseEvent("heal", JSON.parse((e as MessageEvent).data || "{}")); } catch {}
    });
    es.addEventListener("result", (e) => {
      try { applyStepwiseEvent("result", JSON.parse((e as MessageEvent).data || "{}")); } catch {}
      es.close();
      setLoading(false);
      setGenComplete(true);
      setJobStatus("succeeded");
    });
    es.addEventListener("error", (e) => {
      try {
        const payload = JSON.parse((e as MessageEvent).data || "{}");
        applyStepwiseEvent("error", payload);
      } catch {}
      es.close();
      setLoading(false);
      setJobStatus("failed");
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void api.generate.jobs.inFlight()
      .then((res) => {
        if (cancelled) return;
        const fromApi = res.job?.id || null;
        const fromSession =
          typeof window !== "undefined" ? window.sessionStorage.getItem("gen.activeJobId") : null;
        setRecoverableJobId(fromApi || fromSession || null);
      })
      .catch(() => {
        if (cancelled) return;
        const fromSession =
          typeof window !== "undefined" ? window.sessionStorage.getItem("gen.activeJobId") : null;
        setRecoverableJobId(fromSession || null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  const handleSaveScript = async () => {
    if (!robotCode.trim()) {
      setError("Nothing to save yet.");
      return;
    }
    setSavingScript(true);
    setError("");
    try {
      const res = await api.generate.saveScript({
        robot_code: robotCode,
        test_path: testPath || undefined,
      });
      setTestPath(res.test_path);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not save script");
    } finally {
      setSavingScript(false);
    }
  };

  const handleDiscard = () => {
    sourceRef.current?.close();
    sourceRef.current = null;
    resetGenerationState();
  };

  const handleCancelStepwise = async () => {
    if (!currentJobId) return;
    try {
      await api.generate.jobs.cancel(currentJobId);
      setNotes((prev) => [...prev, "Cancellation requested. Finishing current step..."]);
      setJobStatus("cancelled");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not cancel generation");
    }
  };

  const handleFallbackToQuick = async () => {
    if (!prompt.trim()) return;
    setGenMode("quick");
    await handleGenerate(prompt);
  };

  const generatedFile = testPath ? testPath.split(/[/\\]/).pop() : "";

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="AI Test Studio"
          title="What would you like to test today?"
          description={
            projectSlug
              ? `Project: ${projectSlug}. Configure workspace, choose mode, then describe the scenario in plain English.`
              : "Configure your workspace, choose a generation mode, then describe the scenario in plain English."
          }
          actions={
            projectSlug ? (
              <Link href={`/projects/${encodeURIComponent(projectSlug)}`} className="text-xs text-slate-400 hover:text-white">
                Back to project
              </Link>
            ) : undefined
          }
        />
      </motion.div>

      <WorkspaceBar onChange={setCreds} />

      {recoverableJobId && !loading && (
        <div className="mb-4 p-3 rounded-xl border border-amber-400/30 bg-amber-500/5 flex items-center justify-between gap-3">
          <div className="text-xs text-amber-200">
            A Stepwise generation job is still running in the background.
          </div>
          <button
            type="button"
            onClick={() => attachToExistingJob(recoverableJobId)}
            className="px-3 py-1.5 rounded-lg text-xs bg-amber-500/20 border border-amber-400/30 text-amber-100 hover:bg-amber-500/30"
          >
            Reattach stream
          </button>
        </div>
      )}

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
          {/* Phase 2: link out to the recording flow as a third option.
              Kept as a separate page (not a third Segmented option) because
              it has its own three-state UX that doesn't fit the prompt
              textarea below. The italic caption is intentionally small but
              visible -- the icon-only "●" button isn't self-explanatory for
              first-time users and the native ``title`` tooltip only shows on
              hover. */}
          <Link
            href="/generate/record"
            className="text-xs px-3 py-1.5 rounded-lg border border-fuchsia-400/30 bg-fuchsia-500/10 text-fuchsia-200 hover:bg-fuchsia-500/20 transition-colors"
            title="Open the Record page -- click through Salesforce in a real browser and we'll turn it into a Robot script"
          >
            <span className="text-base mr-1">●</span> Record
          </Link>
          <span className="text-[10px] text-slate-500 italic max-w-[14rem] leading-tight">
            opens a browser, captures your clicks, returns a Robot script
          </span>
        </div>
      </div>

      {currentJobId && (jobStatus === "queued" || jobStatus === "running") && (
        <div className="mb-4 p-2.5 rounded-lg border border-cyan-400/20 bg-cyan-500/5 flex items-center justify-between gap-3">
          <div className="text-[11px] text-cyan-200 font-mono">
            Job in progress · {currentJobId}
          </div>
          <button
            type="button"
            onClick={handleCancelStepwise}
            className="px-3 py-1.5 rounded-lg text-xs border border-red-400/30 bg-red-500/10 text-red-200 hover:bg-red-500/20"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Common scenarios + Test name + Auto data.

          The cards in this panel are the deterministic recipe catalog
          loaded from /api/generate/recipes. Clicking one pre-fills the
          prompt textarea with a sample that matches the recipe at
          submit time -- the matcher renders the script directly with
          NO LLM call. */}
      <PageSection title="Scenario setup" description="Pick a scenario, name the suite, and control data generation.">
        {recipes.length > 0 && (
          <div className="mb-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[10px] uppercase tracking-wider text-emerald-400 font-semibold">
                Common scenarios
              </span>
              <span className="text-[10px] text-slate-600">
                Click to load &mdash; these run instantly without calling the AI
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {recipes.map((r) => (
                <button
                  key={r.name}
                  type="button"
                  onClick={() => setPrompt(r.sample_prompt)}
                  className="text-xs px-3 py-1.5 rounded-full border border-emerald-400/30 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20 transition-colors"
                  title={`${r.description}\n\nSample prompt:\n${r.sample_prompt}`}
                >
                  {r.display_name || r.name}
                </button>
              ))}
            </div>
          </div>
        )}
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
      </PageSection>

      {/* Prompt + Generate */}
      <PageSection title="Prompt" description="Describe the expected Salesforce flow and desired assertions.">
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
      </PageSection>

      {/* Stepwise pipeline */}
      <StepwisePipeline
        phase={phase}
        steps={steps}
        notes={notes}
        healEvents={healEvents}
        timings={phaseTimings}
        currentPhaseElapsedMs={
          phaseStartedAt !== null && phase !== "done" && phase !== "idle"
            ? Math.max(0, tickMs - phaseStartedAt)
            : undefined
        }
        totalElapsedMs={
          streamStartedAt !== null
            ? Math.max(0, tickMs - streamStartedAt)
            : undefined
        }
      />

      {steps.length > 0 && (
        <div className="mb-4 rounded-xl border border-white/10 bg-white/[0.03]">
          <button
            type="button"
            onClick={() => setLiveLogOpen((v) => !v)}
            className="w-full px-3 py-2 text-left text-xs text-slate-300 hover:text-white"
          >
            {liveLogOpen ? "▾" : "▸"} Live keyword log ({steps.length})
          </button>
          {liveLogOpen && (
            <div className="max-h-44 overflow-auto border-t border-white/10 px-3 py-2 space-y-1">
              {steps.map((s, idx) => (
                <div key={`${idx}-${s.keyword}`} className="text-[11px] font-mono text-slate-300">
                  <span className={s.status === "pass" ? "text-emerald-300" : "text-red-300"}>
                    {s.status.toUpperCase()}
                  </span>
                  <span className="mx-2 text-slate-500">{s.index}/{s.total}</span>
                  <span>{s.keyword}</span>
                  {typeof s.elapsed_ms === "number" && (
                    <span className="ml-2 text-slate-500">{Math.round(s.elapsed_ms)}ms</span>
                  )}
                  {s.error && <span className="ml-2 text-red-300">{s.error}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

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
            {phase === "failed" && !loading && (
              <div className="pt-1">
                <button
                  type="button"
                  onClick={handleFallbackToQuick}
                  className="px-3 py-1.5 rounded-lg border border-amber-400/30 bg-amber-500/10 text-amber-200 text-xs hover:bg-amber-500/20"
                >
                  Try Quick Generate Instead
                </button>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Template-first banner. When the deterministic recipe tier
          matched the prompt, the script was rendered from a
          parameterized template with NO LLM call. We resolve the
          backend's stable ``used_recipe`` (snake_case internal id)
          to the recipe's friendly ``display_name`` via the catalog
          loaded on mount, falling back to the raw id for older
          backends that haven't shipped display_name yet. */}
      <AnimatePresence>
        {usedRecipe && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mb-4 p-3 rounded-xl border border-emerald-400/30 bg-emerald-500/5"
          >
            <div className="text-xs text-emerald-200">
              <span className="font-semibold">Generated from template:</span>{" "}
              <span className="text-cyan-200 font-semibold">
                {recipes.find((r) => r.name === usedRecipe)?.display_name || usedRecipe}
              </span>{" "}
              {usedRecipeConfidence && (
                <span className="text-slate-500">
                  ({usedRecipeConfidence} confidence)
                </span>
              )}
              <span className="ml-2 text-slate-400">
                · No AI call was made — script is deterministic.
              </span>
            </div>
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
                {/* Phase 1 Playwright locator pill. Three states:
                    - ok=null: gate didn't run (default in production); pill hidden.
                    - ok=true: green "Locators verified N/N".
                    - ok=false: red "Locators X/N stale" (or amber if shadow mode).
                    Sits beside the existing validation pill so users see
                    "Validated  Locators 12/12" when both gates pass. */}
                {locatorOk === true && (
                  <span
                    className="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-500/20 border border-cyan-400/30 text-cyan-200"
                    title="All literal locators in the script resolved on the live Salesforce page."
                  >
                    Locators {locatorCount}/{locatorCount} live
                  </span>
                )}
                {locatorOk === false && !locatorShadow && (
                  <span
                    className="text-[10px] font-mono px-2 py-0.5 rounded bg-red-500/20 border border-red-400/30 text-red-200"
                    title="One or more locators in this script are NOT on the live Salesforce page; the script will fail at runtime."
                  >
                    {locatorFailed} stale locator{locatorFailed === 1 ? "" : "s"}
                  </span>
                )}
                {locatorOk === false && locatorShadow && (
                  <span
                    className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/20 border border-amber-400/30 text-amber-200"
                    title="Locator validation ran in OBSERVATION mode. We detected stale locators but did not block the save -- this is a heads-up, not a hard fail."
                  >
                    {locatorFailed} potential stale (observed)
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
                      {/* Phase 1 Playwright: locator failures get a
                          live-page URL context line so the user knows
                          which Salesforce page Playwright was checking
                          against -- crucial for "wrong page" failures
                          vs "wrong selector" failures. */}
                      {err.kind === "locator_not_found" && err.page_url && (
                        <div className="ml-6 text-[10px] text-purple-300">
                          on page: <span className="text-slate-300">{err.page_url}</span>
                        </div>
                      )}
                      {err.snippet && (
                        <div className="ml-6 text-slate-500 text-[10px] truncate">
                          on: <span className="text-slate-300">{err.snippet}</span>
                        </div>
                      )}
                      {err.closest_matches.length > 0 && (
                        <div className="ml-6 text-[10px] text-cyan-300">
                          {err.kind === "locator_not_found"
                            ? "live elements on the page:"
                            : "did you mean:"}{" "}
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
                onClick={handleSaveScript}
                disabled={savingScript}
                className="px-4 py-2 rounded-xl bg-cyan-700/70 text-cyan-100 text-sm font-semibold disabled:opacity-50"
              >
                {savingScript ? "Saving…" : "Save script"}
              </button>
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
    </PageScaffold>
  );
}
