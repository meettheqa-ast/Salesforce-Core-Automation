"use client";

/**
 * Live UI for parallel bulk runs.
 *
 * Reads SSE events from /run/user-story/{id}/stream or /run/tag/{name}/stream
 * (see api.runs.userStoryStreamUrl / byTagStreamUrl). Renders:
 *   - A header progress bar split into queued / running / passed / failed.
 *   - One card per test case showing status, recent log tail, and links to
 *     log.html / report.html once it finishes.
 *   - A final summary card after the SSE `summary` event, with totals,
 *     duration, and the bulk output directory path.
 *
 * The component is generic over the source URL: pass a `streamUrl` string
 * already built by the api helpers. Set to `null` to clear the view.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";

type CaseStatus = "queued" | "running" | "passed" | "failed" | "healing";

type CaseRow = {
  tc_id: string;
  tc_title: string;
  tags?: string[];
  status: CaseStatus;
  /** Tail of the most recent stdout lines from this case's Robot subprocess. */
  log_tail: string[];
  /** Set after the `done` event for this case. */
  result?: {
    passed: number;
    failed: number;
    skipped: number;
    duration_s: number;
    exit_code: number;
    output_dir: string;
    log_html: string | null;
    report_html: string | null;
    error?: string;
  };
  /** Last-attempted heal feedback so the UI can show "healed -- rerunning" /
   *  "heal failed -- {reason}". */
  heal_note?: string;
};

type BulkSummary = {
  label: string;
  total: number;
  tests_passed: number;
  tests_failed: number;
  assertions_passed: number;
  assertions_failed: number;
  assertions_skipped: number;
  duration_s: number;
  bulk_prefix: string;
};

interface Props {
  streamUrl: string | null;
  /** Cleared to null when the user closes the panel. */
  onClose?: () => void;
  /** When provided, a "Heal & retry" button appears on FAILED test cards.
   *  We need org+persona to re-run a single test case after healing.
   *  Omit to disable the heal feature for this run instance. */
  healContext?: {
    org_id: string;
    persona_id?: string | null;
  };
}

const LOG_TAIL_LINES = 40;

function statusChipClass(s: CaseStatus): string {
  switch (s) {
    case "passed":
      return "bg-emerald-600/30 text-emerald-200 border-emerald-500/40";
    case "failed":
      return "bg-red-600/30 text-red-200 border-red-500/40";
    case "running":
      return "bg-cyan-600/30 text-cyan-100 border-cyan-500/40";
    case "healing":
      return "bg-fuchsia-600/30 text-fuchsia-100 border-fuchsia-500/40";
    default:
      return "bg-slate-700/40 text-slate-300 border-white/10";
  }
}

export default function BulkExecutionStream({ streamUrl, onClose, healContext }: Props) {
  const [start, setStart] = useState<{
    label: string;
    total: number;
    concurrency: number;
    bulk_prefix: string;
    persona: string;
    org: string;
    auto_heal?: boolean;
    max_heal_attempts?: number;
  } | null>(null);
  const [cases, setCases] = useState<CaseRow[]>([]);
  const [summary, setSummary] = useState<BulkSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [openLogs, setOpenLogs] = useState<Record<string, boolean>>({});

  const sourceRef = useRef<EventSource | null>(null);
  const prevUrlRef = useRef<string | null>(null);
  /** Per-tc heal-and-rerun state. The keys are tc_ids that are currently in
   *  the heal flow; values track which EventSource is feeding the rerun. */
  const healStreamsRef = useRef<Map<string, EventSource>>(new Map());

  useEffect(() => {
    if (streamUrl === prevUrlRef.current) return;
    prevUrlRef.current = streamUrl;

    sourceRef.current?.close();
    sourceRef.current = null;
    setStart(null);
    setCases([]);
    setSummary(null);
    setError(null);
    setOpenLogs({});

    if (!streamUrl) {
      setStreaming(false);
      return;
    }

    setStreaming(true);
    const es = new EventSource(streamUrl);
    sourceRef.current = es;

    es.addEventListener("start", (ev) => {
      try { setStart(JSON.parse((ev as MessageEvent).data)); } catch { /* ignore */ }
    });
    es.addEventListener("queued", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        setCases((prev) => [
          ...prev,
          { tc_id: d.tc_id, tc_title: d.tc_title, tags: d.tags ?? [], status: "queued", log_tail: [] },
        ]);
      } catch { /* ignore */ }
    });
    es.addEventListener("running", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        setCases((prev) =>
          prev.map((c) =>
            c.tc_id === d.tc_id
              ? {
                  ...c,
                  status: "running",
                  // On a retry attempt the prior log tail is no longer
                  // relevant -- start fresh so the user sees only the
                  // current attempt's output.
                  log_tail: d.attempt && d.attempt > 1 ? [] : c.log_tail,
                }
              : c,
          ),
        );
      } catch { /* ignore */ }
    });
    es.addEventListener("healing", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        setCases((prev) =>
          prev.map((c) =>
            c.tc_id === d.tc_id
              ? {
                  ...c,
                  status: "healing",
                  heal_note: `Healing... (LLM rewriting after attempt ${d.attempt ?? 1})`,
                }
              : c,
          ),
        );
      } catch { /* ignore */ }
    });
    es.addEventListener("healed", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        setCases((prev) =>
          prev.map((c) =>
            c.tc_id === d.tc_id
              ? {
                  ...c,
                  heal_note: `Healed (rewrote ${d.fixed_keyword || "failing keyword"}); re-running...`,
                }
              : c,
          ),
        );
      } catch { /* ignore */ }
    });
    es.addEventListener("heal_failed", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        setCases((prev) =>
          prev.map((c) =>
            c.tc_id === d.tc_id
              ? {
                  ...c,
                  status: "failed",
                  heal_note: `Heal aborted: ${d.reason || "unknown reason"}`,
                }
              : c,
          ),
        );
      } catch { /* ignore */ }
    });
    es.addEventListener("log", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        if (typeof d.line !== "string") return;
        setCases((prev) =>
          prev.map((c) => {
            if (c.tc_id !== d.tc_id) return c;
            const next = [...c.log_tail, d.line];
            // Trim to keep memory + render cost bounded.
            return { ...c, log_tail: next.slice(-LOG_TAIL_LINES) };
          }),
        );
      } catch { /* ignore */ }
    });
    es.addEventListener("done", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        setCases((prev) =>
          prev.map((c) =>
            c.tc_id === d.tc_id
              ? {
                  ...c,
                  status: d.status === "PASS" ? "passed" : "failed",
                  result: {
                    passed: d.passed ?? 0,
                    failed: d.failed ?? 0,
                    skipped: d.skipped ?? 0,
                    duration_s: d.duration_s ?? 0,
                    exit_code: d.exit_code ?? 0,
                    output_dir: d.output_dir ?? "",
                    log_html: d.log_html ?? null,
                    report_html: d.report_html ?? null,
                    error: d.error,
                  },
                }
              : c,
          ),
        );
      } catch { /* ignore */ }
    });
    es.addEventListener("summary", (ev) => {
      try {
        const d: BulkSummary = JSON.parse((ev as MessageEvent).data);
        setSummary(d);
      } catch { /* ignore */ }
      setStreaming(false);
      es.close();
      sourceRef.current = null;
    });
    es.addEventListener("error", () => {
      // EventSource fires `error` both on transport hiccups (ES auto-retries
      // unless we close it) and on real failures. Treat as fatal once we're
      // not in a clean teardown.
      if (sourceRef.current === es) {
        setError("Connection lost. Refresh the panel and try again.");
        setStreaming(false);
        es.close();
        sourceRef.current = null;
      }
    });

    return () => {
      es.close();
      if (sourceRef.current === es) sourceRef.current = null;
    };
  }, [streamUrl]);

  /** Open a per-tc rerun stream and let it overwrite this card's status. */
  const _attachRerunStream = (tcId: string, url: string) => {
    const prev = healStreamsRef.current.get(tcId);
    if (prev) {
      prev.close();
      healStreamsRef.current.delete(tcId);
    }
    const es = new EventSource(url);
    healStreamsRef.current.set(tcId, es);

    // The per-tc stream uses the same engine, so it emits the same events
    // (start, queued, running, log, done, summary). We only care about
    // running / log / done for THIS tc; the engine's `start` / `summary`
    // are noise for the per-card view.
    es.addEventListener("running", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        if (d.tc_id !== tcId) return;
        setCases((prev2) =>
          prev2.map((c) =>
            c.tc_id === tcId
              ? { ...c, status: "running", log_tail: [], result: undefined }
              : c,
          ),
        );
      } catch { /* ignore */ }
    });
    es.addEventListener("log", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        if (d.tc_id !== tcId || typeof d.line !== "string") return;
        setCases((prev2) =>
          prev2.map((c) => {
            if (c.tc_id !== tcId) return c;
            return { ...c, log_tail: [...c.log_tail, d.line].slice(-LOG_TAIL_LINES) };
          }),
        );
      } catch { /* ignore */ }
    });
    es.addEventListener("done", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        if (d.tc_id !== tcId) return;
        setCases((prev2) =>
          prev2.map((c) =>
            c.tc_id === tcId
              ? {
                  ...c,
                  status: d.status === "PASS" ? "passed" : "failed",
                  result: {
                    passed: d.passed ?? 0,
                    failed: d.failed ?? 0,
                    skipped: d.skipped ?? 0,
                    duration_s: d.duration_s ?? 0,
                    exit_code: d.exit_code ?? 0,
                    output_dir: d.output_dir ?? "",
                    log_html: d.log_html ?? null,
                    report_html: d.report_html ?? null,
                    error: d.error,
                  },
                }
              : c,
          ),
        );
      } catch { /* ignore */ }
    });
    es.addEventListener("summary", () => {
      es.close();
      healStreamsRef.current.delete(tcId);
    });
    es.addEventListener("error", () => {
      es.close();
      healStreamsRef.current.delete(tcId);
    });
  };

  const _runHealAndRetry = async (tc: CaseRow) => {
    if (!healContext) return;
    const runFolder =
      tc.result?.output_dir
        ?.replace(/\\/g, "/")
        .split("/")
        .filter(Boolean)
        .pop() ?? "";
    if (!runFolder) {
      setCases((prev) => prev.map((c) =>
        c.tc_id === tc.tc_id ? { ...c, heal_note: "No prior run folder to learn from." } : c,
      ));
      return;
    }
    setCases((prev) => prev.map((c) =>
      c.tc_id === tc.tc_id
        ? { ...c, status: "healing", heal_note: "Healing... (LLM rewrite in progress)" }
        : c,
    ));
    try {
      const r = await api.testCases.heal(tc.tc_id, runFolder);
      setCases((prev) => prev.map((c) =>
        c.tc_id === tc.tc_id
          ? { ...c, heal_note: `Heal attempt ${r.attempts}: ${r.message} -- re-running...` }
          : c,
      ));
      const url = api.runs.testCaseStreamUrl(tc.tc_id, healContext);
      _attachRerunStream(tc.tc_id, url);
    } catch (e: unknown) {
      const text = e instanceof Error ? e.message : "Heal failed";
      setCases((prev) => prev.map((c) =>
        c.tc_id === tc.tc_id ? { ...c, status: "failed", heal_note: text } : c,
      ));
    }
  };

  const counts = useMemo(() => {
    const queued = cases.filter((c) => c.status === "queued").length;
    const running = cases.filter((c) => c.status === "running").length;
    const passed = cases.filter((c) => c.status === "passed").length;
    const failed = cases.filter((c) => c.status === "failed").length;
    return { queued, running, passed, failed, total: cases.length };
  }, [cases]);

  if (!streamUrl) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass p-5 mt-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wider text-purple-300 font-semibold">
            Bulk run {streaming ? "in progress" : summary ? "complete" : ""}
          </p>
          <p className="text-sm text-white truncate">
            {start?.label ?? "Starting…"}
          </p>
          {start && (
            <p className="text-[11px] text-slate-500 mt-0.5">
              {start.total} test(s) -- up to {start.concurrency} in parallel -- persona{" "}
              <span className="text-slate-300">{start.persona}</span> on org{" "}
              <span className="text-slate-300">{start.org}</span>
              {start.auto_heal && (
                <span className="text-fuchsia-300">
                  {" "}-- auto-heal ON (up to {start.max_heal_attempts ?? 1} retry/test)
                </span>
              )}
            </p>
          )}
        </div>
        {onClose && !streaming && (
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1 text-xs rounded glass text-slate-400 hover:text-white"
          >
            Close
          </button>
        )}
      </div>

      <ProgressBar counts={counts} />

      {error && (
        <p className="mt-3 text-sm text-red-300">{error}</p>
      )}

      <div className="mt-5 space-y-2">
        <AnimatePresence initial={false}>
          {cases.map((c) => {
            const open = !!openLogs[c.tc_id];
            return (
              <motion.div
                key={c.tc_id}
                layout
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className={`rounded-xl border ${statusChipClass(c.status)} bg-black/20 p-3`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <StatusBadge status={c.status} />
                    <p className="text-sm text-slate-100 truncate">{c.tc_title}</p>
                    {c.tags && c.tags.length > 0 && (
                      <div className="hidden sm:flex gap-1 ml-2">
                        {c.tags.slice(0, 3).map((t) => (
                          <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-purple-600/30 text-purple-200">
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {c.result && c.result.duration_s > 0 && (
                      <span className="text-[11px] text-slate-400 font-mono">
                        {c.result.duration_s.toFixed(1)}s
                      </span>
                    )}
                    {c.result?.report_html && (
                      <ReportLink runFolder={runFolderFrom(c.result.output_dir)} kind="report" />
                    )}
                    {c.result?.log_html && (
                      <ReportLink runFolder={runFolderFrom(c.result.output_dir)} kind="log" />
                    )}
                    {(c.status === "running" || c.status === "passed" || c.status === "failed" || c.status === "healing") && (
                      <button
                        type="button"
                        onClick={() => setOpenLogs((s) => ({ ...s, [c.tc_id]: !s[c.tc_id] }))}
                        className="text-[11px] px-2 py-0.5 rounded glass text-slate-300 hover:text-white"
                      >
                        {open ? "Hide log" : "Show log"}
                      </button>
                    )}
                    {c.status === "failed" && healContext && c.result?.output_dir && (
                      <button
                        type="button"
                        onClick={() => _runHealAndRetry(c)}
                        title="Feed the failure back to the LLM, rewrite the script, and re-run this test."
                        className="text-[11px] px-2 py-0.5 rounded bg-fuchsia-600/40 text-fuchsia-100 hover:bg-fuchsia-600/60"
                      >
                        Heal &amp; retry
                      </button>
                    )}
                  </div>
                </div>

                {c.heal_note && (
                  <p className="mt-2 text-xs text-fuchsia-200">{c.heal_note}</p>
                )}
                {c.result?.error && (
                  <p className="mt-2 text-xs text-red-300 break-words">{c.result.error}</p>
                )}

                <AnimatePresence>
                  {open && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      className="mt-2 bg-black/40 rounded p-2 font-mono text-[11px] text-slate-300 max-h-60 overflow-y-auto whitespace-pre-wrap"
                    >
                      {c.log_tail.length === 0 ? (
                        <span className="text-slate-500">No log lines yet.</span>
                      ) : (
                        c.log_tail.map((line, i) => (
                          <div
                            key={i}
                            className={
                              /\bFAIL\b|ERROR/.test(line)
                                ? "text-red-300"
                                : /\bPASS\b/.test(line)
                                  ? "text-emerald-300"
                                  : ""
                            }
                          >
                            {line || " "}
                          </div>
                        ))
                      )}
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>

      {summary && <SummaryCard summary={summary} cases={cases} />}
    </motion.div>
  );
}

function StatusBadge({ status }: { status: CaseStatus }) {
  const label = status[0].toUpperCase() + status.slice(1);
  const dot =
    status === "passed"
      ? "bg-emerald-400"
      : status === "failed"
        ? "bg-red-400"
        : status === "running"
          ? "bg-cyan-300 animate-pulse"
          : status === "healing"
            ? "bg-fuchsia-300 animate-pulse"
            : "bg-slate-500";
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-100">
      <span className={`w-2 h-2 rounded-full ${dot}`} />
      {label}
    </span>
  );
}

function ProgressBar({
  counts,
}: {
  counts: { queued: number; running: number; passed: number; failed: number; total: number };
}) {
  const total = Math.max(counts.total, 1);
  const pct = (n: number) => (n / total) * 100;
  return (
    <div>
      <div className="flex h-3 w-full rounded-full overflow-hidden bg-white/5 border border-white/10">
        <span
          style={{ width: `${pct(counts.passed)}%` }}
          className="bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all"
          title={`${counts.passed} passed`}
        />
        <span
          style={{ width: `${pct(counts.failed)}%` }}
          className="bg-gradient-to-r from-red-500 to-red-400 transition-all"
          title={`${counts.failed} failed`}
        />
        <span
          style={{ width: `${pct(counts.running)}%` }}
          className="bg-gradient-to-r from-cyan-500 to-cyan-400 transition-all"
          title={`${counts.running} running`}
        />
        <span
          style={{ width: `${pct(counts.queued)}%` }}
          className="bg-slate-700/60 transition-all"
          title={`${counts.queued} queued`}
        />
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-slate-400">
        <Legend dotClass="bg-emerald-400" label={`Passed ${counts.passed}`} />
        <Legend dotClass="bg-red-400" label={`Failed ${counts.failed}`} />
        <Legend dotClass="bg-cyan-300" label={`Running ${counts.running}`} />
        <Legend dotClass="bg-slate-500" label={`Queued ${counts.queued}`} />
        <span className="ml-auto text-slate-500">
          {counts.passed + counts.failed} / {counts.total} done
        </span>
      </div>
    </div>
  );
}

function Legend({ dotClass, label }: { dotClass: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`w-2 h-2 rounded-full ${dotClass}`} />
      {label}
    </span>
  );
}

function SummaryCard({ summary, cases }: { summary: BulkSummary; cases: CaseRow[] }) {
  const passRate = summary.total === 0 ? 0 : (summary.tests_passed / summary.total) * 100;
  const allFolders = useMemo(
    () =>
      cases
        .map((c) => c.result?.output_dir)
        .filter((p): p is string => !!p)
        .map(runFolderFrom)
        .filter(Boolean),
    [cases],
  );
  // Open run dashboard deep-link. The /runs page honours ?search= so
  // we land the user on the rows produced by this bulk job. Closes
  // the IA audit gap where story/sprint runs had no path to the full
  // run dashboard (only inline summary + per-test report download).
  const dashboardUrl = `/runs?search=${encodeURIComponent(summary.bulk_prefix)}`;
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="mt-5 rounded-xl border border-white/10 bg-black/30 p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-purple-300 font-semibold">Final report</p>
          <p className="text-sm text-white">
            {summary.tests_passed} passed · {summary.tests_failed} failed · {summary.total} total
          </p>
          <p className="text-[11px] text-slate-500">
            {summary.assertions_passed} assertion(s) passed, {summary.assertions_failed} failed,{" "}
            {summary.assertions_skipped} skipped · {summary.duration_s.toFixed(1)}s wall clock
          </p>
        </div>
        <div className="text-right">
          <p className="text-3xl font-bold text-white">{passRate.toFixed(0)}%</p>
          <p className="text-[10px] uppercase tracking-wider text-slate-500">Pass rate</p>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between gap-3 flex-wrap">
        <p className="text-[11px] text-slate-500 break-all">
          Bulk prefix: {summary.bulk_prefix}
        </p>
        <Link
          href={dashboardUrl}
          className="text-xs px-3 py-1.5 rounded-lg bg-cyan-600/30 text-cyan-100 hover:bg-cyan-600/50"
        >
          Open run dashboard →
        </Link>
      </div>
      {allFolders.length > 0 && (
        <details className="mt-3 text-xs text-slate-400">
          <summary className="cursor-pointer text-slate-500 hover:text-slate-300 select-none">
            Per-test reports ({allFolders.length})
          </summary>
          <div className="mt-2 grid sm:grid-cols-2 gap-2">
            {cases.map((c) => {
              const runFolder = c.result ? runFolderFrom(c.result.output_dir) : null;
              if (!runFolder) return null;
              return (
                <div key={c.tc_id} className="flex items-center justify-between gap-2 px-2 py-1 rounded bg-white/5">
                  <span className="truncate text-slate-200">{c.tc_title}</span>
                  <div className="flex gap-1 shrink-0">
                    {c.result?.report_html && <ReportLink runFolder={runFolder} kind="report" />}
                    {c.result?.log_html && <ReportLink runFolder={runFolder} kind="log" />}
                  </div>
                </div>
              );
            })}
          </div>
        </details>
      )}
    </motion.div>
  );
}

/**
 * Per-test bulk run output dirs are flat under RESULTS_DIR:
 *   <RESULTS_DIR>/bulk_<ts>_<token>__<tcSlug>_<tc8>/{log,report}.html
 * They're flat (not nested) so the existing /api/runs/{run_folder}/file
 * endpoint -- which rejects '/' in run_folder -- still serves them.
 * Take just the basename here.
 */
function runFolderFrom(absDir: string): string {
  if (!absDir) return "";
  const norm = absDir.replace(/\\/g, "/");
  const parts = norm.split("/").filter(Boolean);
  return parts[parts.length - 1] || "";
}

function ReportLink({
  runFolder,
  kind,
}: {
  runFolder: string;
  kind: "log" | "report";
}) {
  if (!runFolder) return null;
  const filename = kind === "log" ? "log.html" : "report.html";
  return (
    <a
      href={api.runs.fileUrl(runFolder, filename)}
      target="_blank"
      rel="noreferrer"
      className="text-[10px] px-2 py-0.5 rounded bg-cyan-600/30 text-cyan-100 hover:bg-cyan-600/50"
      title={`Open ${filename}`}
    >
      {kind === "log" ? "Log" : "Report"}
    </a>
  );
}

