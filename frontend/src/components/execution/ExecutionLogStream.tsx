"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";

interface RunRequestData {
  test_path: string;
  sandbox_url: string;
  username: string;
  password: string;
  headless: boolean;
}

interface RunResult {
  status: string;
  passed: number;
  failed: number;
  skipped: number;
  output_dir?: string;
  log_html?: string;
  report_html?: string;
}

interface ExecutionLogStreamProps {
  runRequest: RunRequestData | null;
  onComplete?: (result: RunResult) => void;
}

export default function ExecutionLogStream({ runRequest, onComplete }: ExecutionLogStreamProps) {
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [result, setResult] = useState<RunResult | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);
  const prevRequestRef = useRef<RunRequestData | null>(null);
  const onCompleteRef = useRef(onComplete);
  useEffect(() => { onCompleteRef.current = onComplete; });

  const executeRun = useCallback(async (req: RunRequestData) => {
    setLogs([]);
    setStatus("running");
    setResult(null);

    try {
      const res: RunResult = await api.runs.execute(req);
      setResult(res);
      setStatus(res.status === "PASS" ? "done" : "error");
      setLogs((prev) => [...prev, `\n--- Run ${res.status} ---`, `Passed: ${res.passed}  Failed: ${res.failed}  Skipped: ${res.skipped}`]);
      onCompleteRef.current?.(res);
    } catch (err: unknown) {
      setStatus("error");
      const msg = err instanceof Error ? err.message : String(err);
      setLogs((prev) => [...prev, `ERROR: ${msg}`]);
    }
  }, []);

  useEffect(() => {
    if (!runRequest || runRequest === prevRequestRef.current) return;
    prevRequestRef.current = runRequest;
    executeRun(runRequest);
  }, [runRequest, executeRun]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  if (status === "idle") return null;

  const statusColor = status === "done" ? "text-emerald-400" : status === "error" ? "text-red-400" : "text-cyan-400";
  const statusIcon = status === "done" ? "✓" : status === "error" ? "✗" : "⟳";

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass p-4 mt-4"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={`text-lg ${statusColor}`}>{statusIcon}</span>
          <span className="text-sm font-semibold text-white">Execution</span>
        </div>
        <span className={`text-xs font-mono ${statusColor}`}>
          {status === "running" ? "Running..." : status.toUpperCase()}
        </span>
      </div>

      <div className="bg-black/40 rounded-xl p-3 font-mono text-xs text-slate-400 max-h-64 overflow-y-auto">
        {logs.map((line, i) => (
          <div key={i} className={line.includes("FAIL") || line.includes("ERROR") ? "text-red-400" : line.includes("PASS") ? "text-emerald-400" : ""}>
            {line}
          </div>
        ))}
        {status === "running" && (
          <motion.span animate={{ opacity: [1, 0.3, 1] }} transition={{ duration: 1, repeat: Infinity }} className="text-cyan-400">
            ▌
          </motion.span>
        )}
        <div ref={logEndRef} />
      </div>

      <AnimatePresence>
        {result && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-3 flex gap-3">
            {result.log_html && (
              <a href={`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/results/${result.output_dir?.split(/[/\\]/).pop()}/log.html`}
                target="_blank" rel="noreferrer" className="text-xs px-3 py-1.5 bg-purple-500/20 text-purple-300 rounded-lg hover:bg-purple-500/30 transition-colors">
                View Log
              </a>
            )}
            {result.report_html && (
              <a href={`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/results/${result.output_dir?.split(/[/\\]/).pop()}/report.html`}
                target="_blank" rel="noreferrer" className="text-xs px-3 py-1.5 bg-cyan-500/20 text-cyan-300 rounded-lg hover:bg-cyan-500/30 transition-colors">
                View Report
              </a>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
