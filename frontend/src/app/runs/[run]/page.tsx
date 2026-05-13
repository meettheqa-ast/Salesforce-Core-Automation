"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
  Legend,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";
import AnimatedCard from "@/components/cards/AnimatedCard";
import MetricCard from "@/components/cards/MetricCard";
import { api, type RunHistoryRow, type RunSummary, type RunTestRow } from "@/lib/api";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

const PIE_COLORS = {
  PASS: "#10b981",
  FAIL: "#ef4444",
  SKIP: "#f59e0b",
};

function fmtDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return `${m}m ${s}s`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

export default function RunDashboardPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const runFolder = decodeURIComponent(params.run as string);
  const projectSlug = searchParams.get("project") || "";

  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"ALL" | "PASS" | "FAIL" | "SKIP">("ALL");
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [recentRuns, setRecentRuns] = useState<RunHistoryRow[]>([]);
  const [compareRun, setCompareRun] = useState("");
  const [compareSummary, setCompareSummary] = useState<RunSummary | null>(null);
  const [flakyRows, setFlakyRows] = useState<Array<{ name: string; pass: number; fail: number; seen: number }>>([]);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      setSummary(null);
      setError("");
    });
    api.runs.summary(runFolder)
      .then((s) => { if (!cancelled) setSummary(s); })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load summary");
      });
    return () => { cancelled = true; };
  }, [runFolder]);

  useEffect(() => {
    api.runs
      .latest(20)
      .then((d) => {
        const rows = (d.runs || []) as RunHistoryRow[];
        setRecentRuns(rows.filter((r) => r.run_name !== runFolder));
      })
      .catch(() => setRecentRuns([]));
  }, [runFolder]);

  useEffect(() => {
    if (!compareRun) {
      setCompareSummary(null);
      return;
    }
    api.runs.summary(compareRun).then(setCompareSummary).catch(() => setCompareSummary(null));
  }, [compareRun]);

  useEffect(() => {
    const baseline = recentRuns.slice(0, 5).map((r) => r.run_name);
    if (baseline.length === 0) {
      setFlakyRows([]);
      return;
    }
    Promise.all(
      baseline.map((name) =>
        api.runs.summary(name).catch(() => null),
      ),
    ).then((summaries) => {
      const index = new Map<string, { pass: number; fail: number; seen: number }>();
      summaries.filter(Boolean).forEach((s) => {
        (s as RunSummary).tests.forEach((t) => {
          const key = t.name;
          const row = index.get(key) || { pass: 0, fail: 0, seen: 0 };
          if (t.status === "PASS") row.pass += 1;
          if (t.status === "FAIL") row.fail += 1;
          row.seen += 1;
          index.set(key, row);
        });
      });
      const flaky = Array.from(index.entries())
        .filter(([, v]) => v.pass > 0 && v.fail > 0)
        .map(([name, v]) => ({ name, ...v }))
        .sort((a, b) => b.fail - a.fail || b.seen - a.seen)
        .slice(0, 12);
      setFlakyRows(flaky);
    }).catch(() => setFlakyRows([]));
  }, [recentRuns]);

  if (error) {
    return (
      <PageScaffold>
        <PageHeader
          eyebrow="Runs"
          title="Run report"
          description={runFolder}
          actions={<Link href={projectSlug ? `/runs?project=${encodeURIComponent(projectSlug)}` : "/runs"} className="text-xs text-slate-400 hover:text-white">Back to run history</Link>}
        />
        <p className="text-red-300">Error: {error}</p>
      </PageScaffold>
    );
  }
  if (!summary) {
    return (
      <PageScaffold>
        <PageHeader
          eyebrow="Runs"
          title="Run report"
          description={runFolder}
          actions={<Link href={projectSlug ? `/runs?project=${encodeURIComponent(projectSlug)}` : "/runs"} className="text-xs text-slate-400 hover:text-white">Back to run history</Link>}
        />
        <p className="text-slate-500 text-sm">Loading run…</p>
      </PageScaffold>
    );
  }

  const arts = summary.artefacts;
  const pieData = [
    { name: "Passed", value: summary.passed, key: "PASS" as const },
    { name: "Failed", value: summary.failed, key: "FAIL" as const },
    { name: "Skipped", value: summary.skipped, key: "SKIP" as const },
  ].filter((d) => d.value > 0);

  const suiteBars = summary.by_suite.map((s) => ({
    name: s.name.length > 18 ? s.name.slice(0, 18) + "…" : s.name,
    Passed: s.passed,
    Failed: s.failed,
    Skipped: s.skipped,
  }));

  const visibleTests = summary.tests.filter((t) => filter === "ALL" || t.status === filter);

  const statusClasses =
    summary.status === "PASS"
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
      : summary.status === "FAIL"
      ? "bg-red-500/15 text-red-300 border-red-500/30"
      : "bg-slate-700/40 text-slate-300 border-white/10";

  const link = (filename: string | null, label: string, download: boolean) =>
    filename ? (
      <a
        key={`${label}-${filename}`}
        href={api.runs.fileUrl(runFolder, filename, { download })}
        target={download ? undefined : "_blank"}
        rel={download ? undefined : "noreferrer"}
        className="text-xs px-3 py-1.5 rounded-lg glass text-slate-200 hover:text-white hover:bg-white/10 transition-colors"
      >
        {label}
      </a>
    ) : null;

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
        <PageHeader
          eyebrow="Runs"
          title="Run report"
          description={runFolder}
          actions={
            <div className="flex items-center gap-2">
              <Link href={projectSlug ? `/runs?project=${encodeURIComponent(projectSlug)}` : "/runs"} className="text-xs text-slate-400 hover:text-white">
                Back to run history
              </Link>
              <span className={`text-sm font-semibold px-3 py-1 rounded-full border ${statusClasses}`}>
                {summary.status}
              </span>
            </div>
          }
        />

        <div className="text-xs text-slate-500">
          Started {fmtTime(summary.started_at)} · Finished {fmtTime(summary.finished_at)} ·
          Duration {fmtDuration(summary.duration_s)}
        </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <MetricCard icon="🧪" label="Total" value={summary.total} color="purple" delay={0} />
        <MetricCard icon="✅" label="Passed" value={summary.passed} color="green" delay={0.05} />
        <MetricCard icon="❌" label="Failed" value={summary.failed} color="pink" delay={0.1} />
        <MetricCard icon="⏭" label="Skipped" value={summary.skipped} color="cyan" delay={0.15} />
        <MetricCard icon="📈" label="Pass rate" value={`${summary.pass_rate}%`} color="purple" delay={0.2} />
      </div>

      {/* Charts */}
      <div className="grid md:grid-cols-2 gap-6 mb-6">
        <AnimatedCard glow="purple" delay={0.1}>
          <h3 className="text-sm font-bold text-white mb-3">Outcome distribution</h3>
          {pieData.length === 0 ? (
            <p className="text-xs text-slate-500">No tests in this run.</p>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={90} paddingAngle={2}>
                  {pieData.map((d) => (
                    <Cell key={d.key} fill={PIE_COLORS[d.key]} stroke="rgba(0,0,0,0.4)" />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 10, fontSize: 12 }}
                  labelStyle={{ color: "#e2e8f0" }}
                />
                <Legend wrapperStyle={{ fontSize: 11, color: "#cbd5e1" }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </AnimatedCard>
        <AnimatedCard glow="cyan" delay={0.15}>
          <h3 className="text-sm font-bold text-white mb-3">By suite</h3>
          {suiteBars.length === 0 ? (
            <p className="text-xs text-slate-500">No suite stats available.</p>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={suiteBars} barGap={2}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="name" tick={{ fill: "#64748b", fontSize: 10 }} />
                <YAxis tick={{ fill: "#64748b", fontSize: 11 }} allowDecimals={false} />
                <Tooltip
                  contentStyle={{ background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 10, fontSize: 12 }}
                  labelStyle={{ color: "#e2e8f0" }}
                />
                <Legend wrapperStyle={{ fontSize: 11, color: "#cbd5e1" }} />
                <Bar dataKey="Passed" stackId="x" fill="#10b981" radius={[4, 4, 0, 0]} />
                <Bar dataKey="Failed" stackId="x" fill="#ef4444" />
                <Bar dataKey="Skipped" stackId="x" fill="#f59e0b" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </AnimatedCard>
      </div>

      {/* Tests table */}
      <AnimatedCard glow="purple" delay={0.2} className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-bold text-white">Tests</h3>
          <div className="flex gap-1">
            {(["ALL", "PASS", "FAIL", "SKIP"] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={
                  "text-[11px] px-2 py-1 rounded-md transition-colors " +
                  (filter === f
                    ? "bg-purple-600/30 text-purple-200"
                    : "text-slate-400 hover:text-slate-200")
                }
              >
                {f}
              </button>
            ))}
          </div>
        </div>
        {visibleTests.length === 0 ? (
          <p className="text-xs text-slate-500">No tests match this filter.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="text-[10px] uppercase tracking-wider text-slate-500 border-b border-white/10">
                <tr>
                  <th className="py-2 pr-2">Test</th>
                  <th className="py-2 pr-2">Suite</th>
                  <th className="py-2 pr-2">Status</th>
                  <th className="py-2 pr-2">Duration</th>
                  <th className="py-2 pr-2">Tags</th>
                </tr>
              </thead>
              <tbody>
                {visibleTests.map((t: RunTestRow, i) => {
                  const id = `${t.suite}::${t.name}::${i}`;
                  const open = openRow === id;
                  const pillCls =
                    t.status === "PASS"
                      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                      : t.status === "FAIL"
                      ? "bg-red-500/15 text-red-300 border-red-500/30"
                      : "bg-amber-500/15 text-amber-300 border-amber-500/30";
                  return (
                    <Row
                      key={id}
                      onToggle={() => setOpenRow(open ? null : id)}
                      open={open}
                      pillCls={pillCls}
                      test={t}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </AnimatedCard>

      {/* Artefacts */}
      <AnimatedCard glow="purple" delay={0.23} className="mb-6">
        <h3 className="text-sm font-bold text-white mb-3">Run comparison</h3>
        <div className="flex flex-wrap items-end gap-3 mb-3">
          <label className="block">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Compare against</span>
            <select
              value={compareRun}
              onChange={(e) => setCompareRun(e.target.value)}
              className="block mt-1 min-w-[22rem] bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-slate-200"
            >
              <option value="">Select another run…</option>
              {recentRuns.map((r) => (
                <option key={r.run_name} value={r.run_name}>
                  {r.run_name} · {r.status} · {fmtTime(r.timestamp)}
                </option>
              ))}
            </select>
          </label>
          {compareSummary && (
            <div className="text-xs text-slate-300">
              Δ Passed {summary.passed - compareSummary.passed >= 0 ? "+" : ""}
              {summary.passed - compareSummary.passed}
              <span className="text-slate-500 mx-2">|</span>
              Δ Failed {summary.failed - compareSummary.failed >= 0 ? "+" : ""}
              {summary.failed - compareSummary.failed}
              <span className="text-slate-500 mx-2">|</span>
              Δ Pass rate {summary.pass_rate - compareSummary.pass_rate >= 0 ? "+" : ""}
              {(summary.pass_rate - compareSummary.pass_rate).toFixed(1)}%
            </div>
          )}
        </div>
        {!compareSummary && (
          <p className="text-xs text-slate-500">Pick a prior run to view deltas.</p>
        )}
      </AnimatedCard>

      <AnimatedCard glow="pink" delay={0.24} className="mb-6">
        <h3 className="text-sm font-bold text-white mb-3">Flaky signals (recent runs)</h3>
        {flakyRows.length === 0 ? (
          <p className="text-xs text-slate-500">No pass/fail-flipping tests detected in recent runs.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase tracking-wider text-slate-500 border-b border-white/10">
                <tr>
                  <th className="py-1.5 text-left">Test</th>
                  <th className="py-1.5 text-left">PASS count</th>
                  <th className="py-1.5 text-left">FAIL count</th>
                  <th className="py-1.5 text-left">Seen in runs</th>
                </tr>
              </thead>
              <tbody>
                {flakyRows.map((f) => (
                  <tr key={f.name} className="border-b border-white/5">
                    <td className="py-1.5 text-slate-200">{f.name}</td>
                    <td className="py-1.5 text-emerald-300">{f.pass}</td>
                    <td className="py-1.5 text-red-300">{f.fail}</td>
                    <td className="py-1.5 text-slate-400">{f.seen}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AnimatedCard>

      <AnimatedCard glow="cyan" delay={0.25} className="mb-6">
        <h3 className="text-sm font-bold text-white mb-3">Artefacts</h3>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 mr-1">View</span>
          {link(arts.report_html, "Report (Robot)", false)}
          {link(arts.log_html, "Log (Robot)", false)}
          {link(arts.output_xml, "output.xml", false)}
          <span className="text-[10px] uppercase tracking-wider text-slate-500 ml-3 mr-1">Download</span>
          {link(arts.report_html, "Report", true)}
          {link(arts.log_html, "Log", true)}
          {link(arts.output_xml, "output.xml", true)}
          <a
            href={api.runs.bundleUrl(runFolder)}
            className="text-xs px-3 py-1.5 rounded-lg bg-purple-600/30 text-purple-200 border border-purple-500/30 hover:bg-purple-600/40 transition-colors"
          >
            Bundle .zip
          </a>
        </div>
        <p className="text-[11px] text-slate-500 mt-3">
          Robot generates two HTML files. <span className="text-slate-300">Report</span> is the executive summary
          (totals, by-tag, by-suite). <span className="text-slate-300">Log</span> is the engineer view
          (every keyword call, arguments, screenshots on failure).
        </p>

        {/* Phase 4: Playwright trace viewer links. Only rendered when
            the suite opted in via PlaywrightDebug.robot AND captured
            a trace.zip. The Microsoft-hosted trace viewer accepts a
            URL to the trace file, so we just hand it ours -- no
            additional infrastructure on our side. */}
        {arts.playwright_traces && arts.playwright_traces.length > 0 && (
          <div className="mt-4 pt-3 border-t border-white/5">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[10px] uppercase tracking-wider text-purple-300">
                Playwright trace
              </span>
              <span className="text-[10px] text-slate-500">
                opens in trace.playwright.dev
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {arts.playwright_traces.map((traceFile) => {
                // The viewer takes ?trace=<URL>. We hand it the
                // authenticated file URL the existing artifact server
                // produces.
                const traceUrl = api.runs.fileUrl(runFolder, traceFile);
                const viewerUrl = `https://trace.playwright.dev/?trace=${encodeURIComponent(traceUrl)}`;
                return (
                  <a
                    key={traceFile}
                    href={viewerUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs px-3 py-1.5 rounded-lg bg-fuchsia-600/30 text-fuchsia-200 border border-fuchsia-500/30 hover:bg-fuchsia-600/40 transition-colors"
                    title={`Open ${traceFile} in Playwright trace viewer`}
                  >
                    {traceFile}
                  </a>
                );
              })}
            </div>
            <p className="text-[10px] text-slate-500 mt-2">
              Click any test step in the viewer to see the DOM, screenshot,
              network calls, and console logs at that exact moment. This is
              the best debug surface available -- far richer than log.html.
            </p>
          </div>
        )}
      </AnimatedCard>

      {/* Screenshots */}
      {arts.screenshots.length > 0 && (
        <AnimatedCard glow="pink" delay={0.3} className="mb-6">
          <h3 className="text-sm font-bold text-white mb-3">
            Screenshots <span className="text-slate-500 text-xs font-normal">({arts.screenshots.length})</span>
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
            {arts.screenshots.map((s) => (
              <a key={s} href={api.runs.fileUrl(runFolder, s)} target="_blank" rel="noreferrer" title={s}>
                <img
                  src={api.runs.fileUrl(runFolder, s)}
                  alt={s}
                  className="w-full h-28 object-cover rounded-md border border-white/10 hover:border-purple-400 transition-colors"
                />
              </a>
            ))}
          </div>
        </AnimatedCard>
      )}
      </motion.div>
    </PageScaffold>
  );
}

function Row({
  test,
  open,
  pillCls,
  onToggle,
}: {
  test: RunTestRow;
  open: boolean;
  pillCls: string;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        className={"border-b border-white/5 cursor-pointer hover:bg-white/5"}
        onClick={onToggle}
      >
        <td className="py-2 pr-2 text-slate-200">{test.name}</td>
        <td className="py-2 pr-2 text-slate-400 text-xs">{test.suite}</td>
        <td className="py-2 pr-2">
          <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${pillCls}`}>
            {test.status}
          </span>
        </td>
        <td className="py-2 pr-2 text-slate-400 text-xs">{fmtDuration(test.duration_s)}</td>
        <td className="py-2 pr-2">
          <div className="flex flex-wrap gap-1">
            {test.tags.map((tag) => (
              <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded-full bg-white/5 text-slate-300">
                {tag}
              </span>
            ))}
          </div>
        </td>
      </tr>
      {open && test.message && (
        <tr className="border-b border-white/5 bg-black/20">
          <td colSpan={5} className="px-2 py-3 text-xs text-amber-200 font-mono whitespace-pre-wrap">
            {test.message}
          </td>
        </tr>
      )}
    </>
  );
}
