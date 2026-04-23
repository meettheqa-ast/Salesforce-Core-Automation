"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import RobotCodeEditor from "@/components/editor/RobotCodeEditor";
import ExecutionLogStream from "@/components/execution/ExecutionLogStream";
import StoryExecutionPanel from "@/components/execution/StoryExecutionPanel";
import WorkspaceBar, { type WorkspaceCreds } from "@/components/layout/WorkspaceBar";
import Segmented from "@/components/ui/Segmented";
import StepwisePipeline, {
  type PipelinePhase,
  type PipelineStep,
} from "@/components/generate/StepwisePipeline";
import { api } from "@/lib/api";

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
  const [execMode, setExecMode] = useState<ExecMode>("background");
  const [genMode, setGenMode] = useState<GenMode>("stepwise");
  const [prompt, setPrompt] = useState("");
  const [testName, setTestName] = useState("");
  const [autoData, setAutoData] = useState(true);

  const [loading, setLoading] = useState(false);
  const [robotCode, setRobotCode] = useState("");
  const [testPath, setTestPath] = useState("");
  const [error, setError] = useState("");
  const [genComplete, setGenComplete] = useState(false);

  const [phase, setPhase] = useState<PipelinePhase>("idle");
  const [steps, setSteps] = useState<PipelineStep[]>([]);
  const [notes, setNotes] = useState<string[]>([]);

  const [runRequest, setRunRequest] = useState<RunRequest | null>(null);
  const sourceRef = useRef<{ close: () => void } | null>(null);

  // Restore preferences once mounted (avoids hydration mismatch).
  useEffect(() => {
    void Promise.resolve().then(() => {
      setExecMode(loadPref<ExecMode>("gen.execMode", "background"));
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
  };

  const runQuickGenerate = async (rawPrompt: string) => {
    const payload = {
      prompt: buildPrompt(rawPrompt),
      sandbox_url: creds?.sandboxUrl ?? "",
      username: creds?.username ?? "",
      password: creds?.password ?? "",
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
    setPhase("done");
  };

  const runStepwiseStream = async (rawPrompt: string) => {
    const payload = {
      prompt: buildPrompt(rawPrompt),
      sandbox_url: creds?.sandboxUrl ?? "",
      username: creds?.username ?? "",
      password: creds?.password ?? "",
      generation_mode: "mcp_stepwise",
      test_name: testName.trim() || undefined,
      headless,
    };
    const url = api.generate.stepwiseStreamUrl();
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
        headers: { "Content-Type": "application/json" },
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
            if (event === "phase") setPhase((obj.name as PipelinePhase) || "executing");
            else if (event === "note") setNotes((prev) => [...prev, String(obj.message || data)]);
            else if (event === "step") setSteps((prev) => [...prev, obj as PipelineStep]);
            else if (event === "result") {
              setRobotCode(obj.robot_code || "");
              setTestPath(obj.test_path || "");
              if (Array.isArray(obj.lint_errors) && obj.lint_errors.length) {
                setNotes((prev) => [...prev, ...obj.lint_errors]);
              }
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
      <StepwisePipeline phase={phase} steps={steps} notes={notes} />

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
              {generatedFile && (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 border border-white/10 text-slate-300">
                  {generatedFile}
                </span>
              )}
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
              <button
                type="button"
                onClick={handleRun}
                disabled={!credsReady}
                title={credsReady ? "Run the generated script" : "Select a workspace login first"}
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
