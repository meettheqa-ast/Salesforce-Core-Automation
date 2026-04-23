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
};

interface StepwisePipelineProps {
  phase: PipelinePhase;
  steps: PipelineStep[];
  notes: string[];
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

export default function StepwisePipeline({ phase, steps, notes }: StepwisePipelineProps) {
  if (phase === "idle" && steps.length === 0 && notes.length === 0) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass p-3 mb-4"
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
          MCP Stepwise pipeline
        </span>
        <span className={"text-[11px] font-mono " + (phase === "done" ? "text-emerald-300" : "text-cyan-300")}>
          {PHASE_LABEL[phase]}
        </span>
      </div>
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
                <span className="text-slate-300 truncate" title={s.keyword}>{s.keyword}</span>
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
