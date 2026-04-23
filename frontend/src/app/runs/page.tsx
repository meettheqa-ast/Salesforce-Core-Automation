"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api, type RunHistoryRow } from "@/lib/api";

type StatusFilter = "ALL" | "PASS" | "FAIL" | "EMPTY";

function fmtTime(iso: string): string {
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function pillCls(status: string): string {
  switch (status) {
    case "PASS": return "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
    case "FAIL": return "bg-red-500/15 text-red-300 border-red-500/30";
    default:     return "bg-slate-700/40 text-slate-300 border-white/10";
  }
}

export default function RunHistoryPage() {
  const [rows, setRows] = useState<RunHistoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<StatusFilter>("ALL");
  const [search, setSearch] = useState("");

  useEffect(() => {
    api.runs.latest(100)
      .then((d) => setRows(d.runs || []))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load runs"))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((r) => {
      if (filter !== "ALL" && r.status !== filter) return false;
      if (q && !r.run_name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [rows, filter, search]);

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl md:text-4xl font-bold mb-1">
          <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
            Run history
          </span>
        </h1>
        <p className="text-slate-400 text-sm">Every Robot run captured by the portal. Click a row for the full report.</p>
      </motion.div>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by run folder name…"
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-slate-200 outline-none focus:border-purple-500 min-w-[260px]"
        />
        <div className="flex gap-1">
          {(["ALL", "PASS", "FAIL", "EMPTY"] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={
                "text-[11px] px-2.5 py-1 rounded-md transition-colors " +
                (filter === f
                  ? "bg-purple-600/30 text-purple-200"
                  : "text-slate-400 hover:text-slate-200")
              }
            >
              {f}
            </button>
          ))}
        </div>
        <span className="text-[11px] text-slate-500 ml-auto">{filtered.length} of {rows.length}</span>
      </div>

      {error && <p className="text-red-400 text-sm mb-3">{error}</p>}

      {loading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : filtered.length === 0 ? (
        <p className="text-slate-500 text-sm">No runs match the filter.</p>
      ) : (
        <div className="overflow-x-auto glass rounded-xl">
          <table className="w-full text-sm text-left">
            <thead className="text-[10px] uppercase tracking-wider text-slate-500 border-b border-white/10">
              <tr>
                <th className="px-3 py-2">When</th>
                <th className="px-3 py-2">Run folder</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Total / Pass / Fail / Skip</th>
                <th className="px-3 py-2 w-32">Pass rate</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => {
                const passRate = r.total > 0 ? Math.round((r.passed / r.total) * 100) : 0;
                return (
                  <tr key={r.run_name} className="border-b border-white/5 hover:bg-white/5">
                    <td className="px-3 py-2 text-slate-400 text-xs">{fmtTime(r.timestamp)}</td>
                    <td className="px-3 py-2 text-xs font-mono text-slate-200">{r.run_name}</td>
                    <td className="px-3 py-2">
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${pillCls(r.status)}`}>
                        {r.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-300">
                      <span className="text-slate-200 font-semibold">{r.total}</span>
                      <span className="text-slate-500"> · </span>
                      <span className="text-emerald-300">{r.passed}</span>
                      <span className="text-slate-500"> · </span>
                      <span className="text-red-300">{r.failed}</span>
                      <span className="text-slate-500"> · </span>
                      <span className="text-amber-300">{r.skipped}</span>
                    </td>
                    <td className="px-3 py-2">
                      <div className="h-1.5 w-28 rounded-full bg-white/5 overflow-hidden">
                        <div
                          className={
                            "h-full " +
                            (r.failed === 0 && r.total > 0 ? "bg-emerald-400" : r.failed > 0 ? "bg-red-400" : "bg-slate-600")
                          }
                          style={{ width: `${passRate}%` }}
                        />
                      </div>
                      <div className="text-[10px] text-slate-500 mt-0.5">{passRate}%</div>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="inline-flex gap-1">
                        <Link
                          href={`/runs/${encodeURIComponent(r.run_name)}`}
                          className="text-[11px] px-2.5 py-1 rounded-md glass text-slate-200 hover:text-white hover:bg-white/10 transition-colors"
                        >
                          Open
                        </Link>
                        <a
                          href={api.runs.bundleUrl(r.run_name)}
                          className="text-[11px] px-2.5 py-1 rounded-md bg-purple-600/30 text-purple-200 border border-purple-500/30 hover:bg-purple-600/40 transition-colors"
                          title="Download log + report + output.xml + screenshots as ZIP"
                        >
                          Bundle
                        </a>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
