"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
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
import { api, type RunSummary, type RunTestRow } from "@/lib/api";

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
  const runFolder = decodeURIComponent(params.run as string);

  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"ALL" | "PASS" | "FAIL" | "SKIP">("ALL");
  const [openRow, setOpenRow] = useState<string | null>(null);

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

  if (error) {
    return (
      <div className="max-w-5xl mx-auto px-6 py-10">
        <Link href="/runs" className="text-sm text-purple-400 hover:underline">← Run history</Link>
        <p className="mt-4 text-red-300">Error: {error}</p>
      </div>
    );
  }
  if (!summary) {
    return (
      <div className="max-w-5xl mx-auto px-6 py-10 text-slate-500 text-sm">Loading run…</div>
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
    <div className="max-w-6xl mx-auto px-6 py-8">
      <Link href="/runs" className="text-sm text-slate-500 hover:text-purple-400 transition-colors mb-3 inline-block">
        ← Run history
      </Link>

      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="flex flex-wrap items-center justify-between gap-3 mb-2">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold mb-1">
            <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
              Run report
            </span>
          </h1>
          <p className="text-xs font-mono text-slate-400">{runFolder}</p>
        </div>
        <span className={`text-sm font-semibold px-3 py-1 rounded-full border ${statusClasses}`}>
          {summary.status}
        </span>
      </motion.div>

      <div className="text-xs text-slate-500 mb-6">
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
    </div>
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
