"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api, type RunSummary } from "@/lib/api";

interface RunSummaryCardProps {
  runFolder: string;
}

export default function RunSummaryCard({ runFolder }: RunSummaryCardProps) {
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api.runs
      .summary(runFolder)
      .then((s) => { if (!cancelled) setSummary(s); })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load summary");
      });
    return () => { cancelled = true; };
  }, [runFolder]);

  if (error) {
    return (
      <div className="glass p-3 mt-3 text-xs text-amber-300">
        Could not load run summary: {error}
      </div>
    );
  }
  if (!summary) {
    return (
      <div className="glass p-3 mt-3 text-xs text-slate-500">Loading run summary…</div>
    );
  }

  const arts = summary.artefacts;
  const link = (filename: string | null, label: string, download: boolean) =>
    filename ? (
      <a
        key={`${label}-${filename}`}
        href={api.runs.fileUrl(runFolder, filename, { download })}
        target={download ? undefined : "_blank"}
        rel={download ? undefined : "noreferrer"}
        className="text-[11px] px-2.5 py-1 rounded-md glass text-slate-200 hover:text-white hover:bg-white/10 transition-colors"
      >
        {label}
      </a>
    ) : null;

  const statusClasses =
    summary.status === "PASS"
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
      : summary.status === "FAIL"
      ? "bg-red-500/15 text-red-300 border-red-500/30"
      : "bg-slate-700/40 text-slate-300 border-white/10";

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass p-4 mt-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${statusClasses}`}>
            {summary.status}
          </span>
          <span className="text-xs text-slate-400">
            {summary.passed} passed · {summary.failed} failed · {summary.skipped} skipped
            {summary.total > 0 && (
              <> · <span className="text-slate-200">{summary.pass_rate}%</span> pass rate</>
            )}
          </span>
        </div>
        <Link
          href={`/runs/${encodeURIComponent(runFolder)}`}
          className="text-[11px] px-3 py-1 rounded-md bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold"
        >
          Open full report →
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 mr-1">View</span>
        {link(arts.report_html, "Report", false)}
        {link(arts.log_html, "Log", false)}
        {link(arts.output_xml, "output.xml", false)}
        <span className="text-[10px] uppercase tracking-wider text-slate-500 ml-3 mr-1">Download</span>
        {link(arts.report_html, "Report", true)}
        {link(arts.log_html, "Log", true)}
        {link(arts.output_xml, "output.xml", true)}
        <a
          href={api.runs.bundleUrl(runFolder)}
          className="text-[11px] px-2.5 py-1 rounded-md bg-purple-600/30 text-purple-200 border border-purple-500/30 hover:bg-purple-600/40 transition-colors"
        >
          Bundle .zip
        </a>
      </div>

      {arts.screenshots.length > 0 && (
        <div className="mt-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2">
            Screenshots ({arts.screenshots.length})
          </div>
          <div className="flex gap-2 overflow-x-auto pb-2">
            {arts.screenshots.slice(0, 8).map((s) => (
              <a
                key={s}
                href={api.runs.fileUrl(runFolder, s)}
                target="_blank"
                rel="noreferrer"
                className="shrink-0"
                title={s}
              >
                <img
                  src={api.runs.fileUrl(runFolder, s)}
                  alt={s}
                  className="h-16 w-24 object-cover rounded-md border border-white/10 hover:border-purple-400 transition-colors"
                />
              </a>
            ))}
            {arts.screenshots.length > 8 && (
              <Link
                href={`/runs/${encodeURIComponent(runFolder)}`}
                className="shrink-0 h-16 w-24 rounded-md border border-dashed border-white/10 flex items-center justify-center text-[10px] text-slate-400 hover:text-white hover:border-purple-400"
              >
                +{arts.screenshots.length - 8} more
              </Link>
            )}
          </div>
        </div>
      )}
    </motion.div>
  );
}
