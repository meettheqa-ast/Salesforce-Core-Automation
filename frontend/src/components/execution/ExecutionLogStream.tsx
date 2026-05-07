"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";
import RunSummaryCard from "@/components/execution/RunSummaryCard";

interface RunRequestData {
  test_path: string;
  sandbox_url: string;
  username: string;
  password: string;
  headless: boolean;
  /** Persona's default Salesforce app, forwarded to the runner so PO
   *  keywords land in the right app via ${salesAutomationAppName}. */
  default_app?: string;
}

interface RunResult {
  status: string;
  passed: number;
  failed: number;
  skipped: number;
  duration_s?: number;
  output_dir?: string;
  log_html?: string | null;
  report_html?: string | null;
  exit_code?: number;
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
  const sourceRef = useRef<EventSource | null>(null);
  const prevRequestRef = useRef<RunRequestData | null>(null);
  const onCompleteRef = useRef(onComplete);
  useEffect(() => { onCompleteRef.current = onComplete; });

  useEffect(() => {
    if (!runRequest || runRequest === prevRequestRef.current) return;
    prevRequestRef.current = runRequest;

    sourceRef.current?.close();
    setLogs([]);
    setResult(null);
    setStatus("running");

    const url = api.runs.executeStreamUrl(runRequest);
    const es = new EventSource(url);
    sourceRef.current = es;

    es.addEventListener("start", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        setLogs((prev) => [...prev, `[ start ] output_dir=${d.output_dir} headless=${d.headless}`]);
      } catch {
        // ignore
      }
    });
    es.addEventListener("log", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        if (d.line != null) setLogs((prev) => [...prev, String(d.line)]);
      } catch {
        // ignore
      }
    });
    es.addEventListener("done", (ev) => {
      try {
        const d: RunResult = JSON.parse((ev as MessageEvent).data);
        setResult(d);
        setStatus(d.status === "PASS" ? "done" : "error");
        setLogs((prev) => [
          ...prev,
          `\n--- Run ${d.status} (exit ${d.exit_code ?? "?"}) ---`,
          `Passed: ${d.passed}  Failed: ${d.failed}  Skipped: ${d.skipped}  Duration: ${d.duration_s ?? 0}s`,
        ]);
        onCompleteRef.current?.(d);
      } catch (e) {
        const msg = e instanceof Error ? e.message : "parse error";
        setLogs((prev) => [...prev, `ERROR parsing done event: ${msg}`]);
      } finally {
        es.close();
        sourceRef.current = null;
      }
    });
    es.addEventListener("error", (ev) => {
      const data = (ev as MessageEvent).data;
      let msg = "Stream error";
      try {
        if (data) msg = JSON.parse(data).message || msg;
      } catch {
        // ignore
      }
      setStatus("error");
      setLogs((prev) => [...prev, `ERROR: ${msg}`]);
      es.close();
      sourceRef.current = null;
    });
  }, [runRequest]);

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, []);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  if (status === "idle") return null;

  const statusColor = status === "done" ? "text-emerald-400" : status === "error" ? "text-red-400" : "text-cyan-400";
  const statusIcon = status === "done" ? "✓" : status === "error" ? "✗" : "⟳";
  const runFolder = result?.output_dir?.split(/[/\\]/).pop();

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

      <div className="bg-black/40 rounded-xl p-3 font-mono text-xs text-slate-400 max-h-72 overflow-y-auto">
        {logs.map((line, i) => (
          <div
            key={i}
            className={
              /\bFAIL\b|ERROR/.test(line)
                ? "text-red-400"
                : /\bPASS\b/.test(line)
                ? "text-emerald-400"
                : ""
            }
          >
            {line || " "}
          </div>
        ))}
        {status === "running" && (
          <motion.span
            animate={{ opacity: [1, 0.3, 1] }}
            transition={{ duration: 1, repeat: Infinity }}
            className="text-cyan-400"
          >
            ▌
          </motion.span>
        )}
        <div ref={logEndRef} />
      </div>

      <AnimatePresence>
        {result && runFolder && (
          <RunSummaryCard runFolder={runFolder} />
        )}
      </AnimatePresence>
    </motion.div>
  );
}
