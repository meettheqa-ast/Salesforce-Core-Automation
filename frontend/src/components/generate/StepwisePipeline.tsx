"use client";

import { motion, AnimatePresence } from "framer-motion";

export type PipelinePhase =
  | "idle"
  | "mcp-init"
  | "planning"
  | "session"
  | "executing"
  | "build"
  | "fallback"
  | "done";

export type PipelineStep = {
  index: number;
  total: number;
  keyword: string;
  status: "pass" | "fail";
  error?: string;
  /** Wall-clock the backend spent inside this single execute_step call. */
  elapsed_ms?: number;
};

/** One row of the per-phase timing summary. The page tracks how long each
 *  phase spent (computed from the backend's `elapsed_ms_phase` field on the
 *  next phase transition + a final wall-clock close on `done`/`error`). */
export type PhaseTiming = {
  phase: PipelinePhase;
  elapsed_ms: number;
};

interface StepwisePipelineProps {
  phase: PipelinePhase;
  steps: PipelineStep[];
  notes: string[];
  /** Closed phases with their measured durations, oldest first. */
  timings?: PhaseTiming[];
  /** Live elapsed time in the current phase (ms). UI ticks this on the
   *  page side so we don't have to push periodic updates from the server. */
  currentPhaseElapsedMs?: number;
  /** Total elapsed time for the whole stream so far (ms). */
  totalElapsedMs?: number;
}

const PHASE_LABEL: Record<PipelinePhase, string> = {
  "idle": "Idle",
  "mcp-init": "Starting RF-MCP",
  "planning": "Planning steps",
  "session": "Opening session",
  "executing": "Executing keywords",
  "build": "Building suite",
  "fallback": "Falling back to Quick Generate",
  "done": "Done",
};

function fmtMs(ms: number): string {
  if (!isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 10) return `${s.toFixed(1)} s`;
  return `${Math.round(s)} s`;
}

export default function StepwisePipeline({
  phase,
  steps,
  notes,
  timings = [],
  currentPhaseElapsedMs,
  totalElapsedMs,
}: StepwisePipelineProps) {
  if (phase === "idle" && steps.length === 0 && notes.length === 0) return null;

  const showCurrentTimer =
    phase !== "idle" && phase !== "done" && currentPhaseElapsedMs !== undefined;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass p-3 mb-4"
    >
      <div className="flex items-center justify-between mb-2 gap-2">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
          MCP Stepwise pipeline
        </span>
        <div className="flex items-center gap-2">
          {totalElapsedMs !== undefined && totalElapsedMs > 0 && (
            <span className="text-[10px] font-mono text-slate-500" title="Total elapsed">
              total {fmtMs(totalElapsedMs)}
            </span>
          )}
          <span
            className={
              "text-[11px] font-mono " +
              (phase === "done" ? "text-emerald-300" : "text-cyan-300")
            }
          >
            {PHASE_LABEL[phase]}
            {showCurrentTimer && (
              <span className="ml-1 text-slate-500">· {fmtMs(currentPhaseElapsedMs!)}</span>
            )}
          </span>
        </div>
      </div>

      {timings.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {timings.map((t, i) => (
            <span
              key={`${t.phase}-${i}`}
              className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-slate-300"
              title={`${PHASE_LABEL[t.phase]} took ${fmtMs(t.elapsed_ms)}`}
            >
              <span className="text-slate-500">{PHASE_LABEL[t.phase]}</span>
              <span className="ml-1.5 text-slate-200">{fmtMs(t.elapsed_ms)}</span>
            </span>
          ))}
        </div>
      )}

      {steps.length > 0 && (
        <div className="space-y-1 mb-2 max-h-44 overflow-y-auto">
          <AnimatePresence initial={false}>
            {steps.map((s) => (
              <motion.div
                key={`${s.index}-${s.keyword}`}
                initial={{ opacity: 0, x: -4 }}
                animate={{ opacity: 1, x: 0 }}
                className="flex items-center gap-2 text-xs"
              >
                <span className="text-slate-600 w-10">{s.index}/{s.total}</span>
                <span
                  className={
                    "h-1.5 w-1.5 rounded-full " +
                    (s.status === "pass" ? "bg-emerald-400" : "bg-red-400")
                  }
                />
                <span className="text-slate-300 truncate flex-1" title={s.keyword}>{s.keyword}</span>
                {s.elapsed_ms !== undefined && (
                  <span className="text-[10px] font-mono text-slate-500 shrink-0">
                    {fmtMs(s.elapsed_ms)}
                  </span>
                )}
                {s.error && (
                  <span className="text-[10px] text-red-400 truncate" title={s.error}>
                    {s.error}
                  </span>
                )}
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
      {notes.length > 0 && (
        <div className="border-t border-white/5 pt-2 space-y-0.5">
          {notes.map((n, i) => (
            <div key={i} className="text-[11px] text-amber-300/90">{n}</div>
          ))}
        </div>
      )}
    </motion.div>
  );
}
