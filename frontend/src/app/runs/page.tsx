"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api, type RunHistoryRow } from "@/lib/api";
import { PageHeader, PageScaffold, PageSection } from "@/components/layout/PageScaffold";
import { useSearchParams } from "next/navigation";

type StatusFilter = "ALL" | "PASS" | "FAIL" | "EMPTY";
type SavedRunView = {
  id: string;
  name: string;
  filter: StatusFilter;
  search: string;
};
const SAVED_VIEWS_KEY = "runs.savedViews.v1";

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
  const searchParams = useSearchParams();
  const projectFilter = searchParams.get("project") || "";
  // Honour ?search= / ?status= URL params so deep-links from bulk
  // execution panels can land users on a pre-filtered view. The IA
  // audit flagged the inconsistency: story/sprint bulk runs had no
  // path to the full run dashboard. With this param honoured, the
  // BulkExecutionStream's "Open run dashboard" link drops users
  // straight onto the right rows.
  const initialSearch = searchParams.get("search") || "";
  const initialStatus = (searchParams.get("status") || "ALL").toUpperCase();
  const [rows, setRows] = useState<RunHistoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<StatusFilter>(
    (["ALL", "PASS", "FAIL"].includes(initialStatus) ? initialStatus : "ALL") as StatusFilter,
  );
  const [search, setSearch] = useState(initialSearch);
  const [savedViews, setSavedViews] = useState<SavedRunView[]>([]);
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);
  const searchRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(SAVED_VIEWS_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as SavedRunView[];
      if (Array.isArray(parsed)) setSavedViews(parsed);
    } catch {
      setSavedViews([]);
    }
  }, []);

  const persistSavedViews = (next: SavedRunView[]) => {
    setSavedViews(next);
    try {
      window.localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(next));
    } catch {
      // ignore localStorage failures
    }
  };

  const saveCurrentView = () => {
    const name = window.prompt("Name this run view:");
    if (!name || !name.trim()) return;
    const next: SavedRunView[] = [
      {
        id: `${Date.now()}`,
        name: name.trim(),
        filter,
        search,
      },
      ...savedViews,
    ].slice(0, 8);
    persistSavedViews(next);
  };

  const applySavedView = (v: SavedRunView) => {
    setFilter(v.filter);
    setSearch(v.search);
  };

  const removeSavedView = (id: string) => {
    persistSavedViews(savedViews.filter((v) => v.id !== id));
  };

  useEffect(() => {
    api.runs.latest(100)
      .then((d) => setRows(d.runs || []))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load runs"))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((r) => {
      if (projectFilter && (r.project_slug || "") !== projectFilter) return false;
      if (filter !== "ALL" && r.status !== filter) return false;
      if (q && !r.run_name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [rows, filter, search, projectFilter]);

  useEffect(() => {
    setSelectedRuns((prev) => prev.filter((run) => filtered.some((r) => r.run_name === run)));
  }, [filtered]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (
        e.key === "/" &&
        tag !== "input" &&
        tag !== "textarea" &&
        !(e.target as HTMLElement | null)?.isContentEditable
      ) {
        e.preventDefault();
        searchRef.current?.focus();
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveCurrentView();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [filter, search, savedViews]);

  const allSelected = filtered.length > 0 && selectedRuns.length === filtered.length;
  const toggleAll = () => {
    if (allSelected) setSelectedRuns([]);
    else setSelectedRuns(filtered.map((r) => r.run_name));
  };
  const toggleRun = (run: string) => {
    setSelectedRuns((prev) => (prev.includes(run) ? prev.filter((x) => x !== run) : [...prev, run]));
  };
  const bulkDownloadBundles = () => {
    selectedRuns.forEach((run) => {
      const a = document.createElement("a");
      a.href = api.runs.bundleUrl(run);
      a.target = "_blank";
      a.rel = "noreferrer";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    });
  };
  const exportJson = () => {
    const blob = new Blob([JSON.stringify(filtered, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "runs-export.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };
  const exportCsv = () => {
    const header = ["run_name", "timestamp", "status", "total", "passed", "failed", "skipped", "duration_s"];
    const rowsCsv = filtered.map((r) =>
      [
        r.run_name,
        r.timestamp,
        r.status,
        r.total,
        r.passed,
        r.failed,
        r.skipped,
        r.duration_s,
      ]
        .map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`)
        .join(","),
    );
    const blob = new Blob([[header.join(","), ...rowsCsv].join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "runs-export.csv";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Execution"
          title="Run history"
          description={
            projectFilter
              ? `Runs for project ${projectFilter}. Filter and drill into logs, reports, and bundles.`
              : "Every Robot run captured by the platform. Filter and drill into logs, reports, and bundles."
          }
        />
      </motion.div>

      <PageSection>
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <input
          ref={searchRef}
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
      {projectFilter && (
        <div className="mb-2">
          <Link href="/runs" className="text-xs text-cyan-300 hover:text-cyan-200">
            Clear project filter
          </Link>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5 mb-2">
        <label className="inline-flex items-center gap-2 text-[11px] text-slate-300">
          <input type="checkbox" checked={allSelected} onChange={toggleAll} className="accent-cyan-500" />
          Select all ({filtered.length})
        </label>
        <button
          type="button"
          disabled={selectedRuns.length === 0}
          onClick={bulkDownloadBundles}
          className="text-[11px] px-2 py-1 rounded-md bg-purple-600/25 text-purple-200 border border-purple-500/30 hover:bg-purple-600/35 disabled:opacity-40"
        >
          Download selected bundles ({selectedRuns.length})
        </button>
        <button
          type="button"
          onClick={exportCsv}
          className="text-[11px] px-2 py-1 rounded-md glass text-slate-300 hover:text-white"
        >
          Export CSV
        </button>
        <button
          type="button"
          onClick={exportJson}
          className="text-[11px] px-2 py-1 rounded-md glass text-slate-300 hover:text-white"
        >
          Export JSON
        </button>
        <button
          type="button"
          onClick={saveCurrentView}
          className="text-[11px] px-2 py-1 rounded-md bg-cyan-600/25 text-cyan-200 border border-cyan-500/30 hover:bg-cyan-600/35"
        >
          Save current view
        </button>
        {savedViews.map((v) => (
          <div key={v.id} className="inline-flex items-center gap-1 rounded-md bg-white/5 border border-white/10 px-1 py-0.5">
            <button
              type="button"
              onClick={() => applySavedView(v)}
              className="text-[11px] px-1.5 py-0.5 text-slate-300 hover:text-white"
              title={`Filter: ${v.filter} | Search: ${v.search || "(none)"}`}
            >
              {v.name}
            </button>
            <button
              type="button"
              onClick={() => removeSavedView(v.id)}
              className="text-[10px] px-1 text-slate-500 hover:text-red-300"
              title="Remove saved view"
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      {error && <p className="text-red-400 text-sm mb-2">{error}</p>}

      {loading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : filtered.length === 0 ? (
        <p className="text-slate-500 text-sm">No runs match the filter.</p>
      ) : (
        <div className="overflow-x-auto glass rounded-xl">
          <table className="w-full text-sm text-left">
            <thead className="text-[10px] uppercase tracking-wider text-slate-500 border-b border-white/10">
              <tr>
                <th className="px-3 py-2">Select</th>
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
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={selectedRuns.includes(r.run_name)}
                        onChange={() => toggleRun(r.run_name)}
                        className="accent-cyan-500"
                      />
                    </td>
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
                          href={`/runs/${encodeURIComponent(r.run_name)}${projectFilter ? `?project=${encodeURIComponent(projectFilter)}` : ""}`}
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
      </PageSection>
    </PageScaffold>
  );
}
