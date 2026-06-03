"use client";

/**
 * Sprint-scoped reporting page (Phase 3 IA audit).
 *
 * Closes the gap where sprint runs had no dedicated reporting view --
 * only the inline BulkExecutionStream summary card + global /runs.
 * This page slices the global runs feed to runs whose folder name
 * starts with this sprint's bulk-prefix, then renders:
 *
 *   * MetricTiles (passed / failed / pass-rate / total wall clock)
 *   * Coverage matrix per story (same shape as /sprints/[id] but
 *     filtered to the runs in scope)
 *   * Pass-rate trend (a tiny inline bar chart -- one bar per run)
 *
 * Backend-wise we lean entirely on existing endpoints
 * (api.runs.history, api.sprints.testCases) -- no new schema. A
 * dedicated /api/sprints/{id}/report endpoint can come later if the
 * client-side aggregation grows expensive.
 */

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api, type RunHistoryRow } from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";
import EmptyState from "@/components/feedback/EmptyState";
import MetricTile from "@/components/data/MetricTile";
import StatusPill from "@/components/data/StatusPill";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

type SprintTestCases = Awaited<ReturnType<typeof api.sprints.testCases>>;

export default function SprintReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [tcs, setTcs] = useState<SprintTestCases | null>(null);
  const [runs, setRuns] = useState<RunHistoryRow[] | null>(null);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [t, history] = await Promise.all([
          api.sprints.testCases(id),
          api.runs.latest(200),
        ]);
        if (cancelled) return;
        setTcs(t);
        // The sprint's bulk prefix isn't directly retrievable from the
        // sprint record, but bulk runs from /sprints/[id] embed the
        // sprint id (short form) in their output_dir name. Filter on
        // that prefix to scope the report.
        const shortId = id.replace(/-/g, "").slice(0, 8);
        setRuns(
          (history.runs || []).filter((r) =>
            (r.output_dir || "").toLowerCase().includes(shortId.toLowerCase()),
          ),
        );
      } catch (e) {
        if (!cancelled) setErr(e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const summary = useMemo(() => {
    if (!runs || runs.length === 0) {
      return { passed: 0, failed: 0, total: 0, passRate: 0, durationS: 0 };
    }
    let passed = 0;
    let failed = 0;
    let durationS = 0;
    for (const r of runs) {
      passed += r.passed || 0;
      failed += r.failed || 0;
      durationS += r.duration_s || 0;
    }
    const total = passed + failed;
    const passRate = total === 0 ? 0 : Math.round((passed / total) * 100);
    return { passed, failed, total, passRate, durationS };
  }, [runs]);

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Sprint"
          title="Report"
          description="Pass-rate, coverage, and per-run history for this sprint."
        />
        <p className="text-xs text-slate-500 mt-2">
          <Link href={`/sprints/${encodeURIComponent(id)}`} className="hover:text-slate-300">
            ← Back to sprint
          </Link>
        </p>
      </motion.div>

      <ErrorBanner error={err} onDismiss={() => setErr(null)} />

      {/* Summary tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <MetricTile label="Passed" value={summary.passed} tone="success" fluid />
        <MetricTile label="Failed" value={summary.failed} tone="danger" fluid />
        <MetricTile
          label="Pass rate"
          value={`${summary.passRate}%`}
          tone={summary.passRate >= 80 ? "success" : summary.passRate >= 50 ? "warning" : "danger"}
          fluid
        />
        <MetricTile
          label="Wall clock"
          value={`${summary.durationS.toFixed(0)}s`}
          tone="info"
          fluid
        />
      </div>

      {/* Coverage matrix -- per-story breakdown using already-fetched tcs */}
      <AnimatedCard glow="cyan" className="mb-6">
        <h2 className="text-sm font-bold text-cyan-400 uppercase tracking-wider mb-3">
          Coverage by story
        </h2>
        {tcs === null ? (
          <LoadingState variant="skeleton" rows={4} />
        ) : tcs.stories.length === 0 ? (
          <EmptyState
            title="No stories in this sprint"
            description="Add stories to the sprint to see coverage and run history here."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-slate-900/60 text-slate-400">
                <tr>
                  <th className="px-2 py-2 text-left">Story</th>
                  <th className="px-2 py-2 text-right">Approved</th>
                  <th className="px-2 py-2 text-right">Stale</th>
                  <th className="px-2 py-2 text-right">Scripts</th>
                  <th className="px-2 py-2 text-right">Coverage</th>
                </tr>
              </thead>
              <tbody>
                {tcs.stories.map((story) => {
                  const approved = story.test_cases.filter((t) => t.status === "approved" && !t.stale).length;
                  const stale = story.test_cases.filter((t) => t.stale).length;
                  const withScript = story.test_cases.filter((t) => t.script_path).length;
                  const coverable = story.test_cases.filter((t) => t.status !== "rejected").length;
                  const cov = coverable > 0 ? Math.round((withScript / coverable) * 100) : 0;
                  return (
                    <tr key={story.id} className="border-t border-white/5">
                      <td className="px-2 py-2 text-slate-100">{story.title}</td>
                      <td className="px-2 py-2 text-right text-emerald-200">{approved}</td>
                      <td className={`px-2 py-2 text-right ${stale > 0 ? "text-amber-400 font-semibold" : "text-slate-500"}`}>{stale}</td>
                      <td className="px-2 py-2 text-right text-cyan-200">{withScript}/{coverable}</td>
                      <td className={`px-2 py-2 text-right font-semibold ${cov >= 80 ? "text-emerald-300" : cov >= 40 ? "text-amber-300" : "text-red-300"}`}>
                        {cov}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </AnimatedCard>

      {/* Pass-rate trend -- one inline bar per run, oldest -> newest. */}
      <AnimatedCard glow="purple">
        <h2 className="text-sm font-bold text-purple-300 uppercase tracking-wider mb-3">
          Pass-rate trend ({runs?.length ?? 0} run{runs?.length === 1 ? "" : "s"})
        </h2>
        {runs === null ? (
          <LoadingState variant="block" label="Loading run history…" />
        ) : runs.length === 0 ? (
          <EmptyState
            title="No runs for this sprint yet"
            description="Once you trigger a run from the sprint detail page, results will start populating this view."
            primary={{ label: "Open sprint", href: `/sprints/${encodeURIComponent(id)}` }}
          />
        ) : (
          <div className="flex items-end gap-1 h-32">
            {[...runs].reverse().map((r) => {
              const total = (r.passed || 0) + (r.failed || 0);
              const pct = total === 0 ? 0 : (r.passed / total) * 100;
              const status = (r.status || "").toUpperCase();
              const color =
                status === "PASS"
                  ? "bg-emerald-500"
                  : status === "FAIL"
                    ? "bg-red-500"
                    : "bg-slate-500";
              return (
                <Link
                  key={r.id || r.output_dir}
                  href={`/runs/${encodeURIComponent((r.output_dir || "").split("/").pop() || r.id)}`}
                  className="flex-1 min-w-[6px] flex flex-col items-center justify-end group"
                  title={`${status} · ${r.passed}/${total} · ${r.duration_s.toFixed(1)}s · ${r.started_at}`}
                >
                  <div
                    className={`w-full ${color} group-hover:opacity-80 transition`}
                    style={{ height: `${Math.max(2, pct)}%` }}
                  />
                  <span className="text-[9px] text-slate-600 mt-0.5">{Math.round(pct)}</span>
                </Link>
              );
            })}
          </div>
        )}
        {runs && runs.length > 0 && (
          <div className="mt-3 flex justify-end">
            <StatusPill
              tone={summary.passRate >= 80 ? "success" : summary.passRate >= 50 ? "warning" : "danger"}
              label={`Sprint avg ${summary.passRate}%`}
              size="md"
            />
          </div>
        )}
      </AnimatedCard>
    </PageScaffold>
  );
}
