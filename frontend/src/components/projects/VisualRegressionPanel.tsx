"use client";

/**
 * VisualRegressionPanel
 *
 * Phase 3 surface on `/projects/[name]`. Lists test+step pairs whose
 * latest screenshot differs from the stored baseline, plus a per-row
 * "Update baseline" button that snaps the current state as the new
 * canonical.
 *
 * Hidden when the feature is disabled at the deployment level (the
 * pending-baselines endpoint returns 403 / 503 and we silently render
 * nothing -- avoiding noisy "feature unavailable" empty states).
 */

import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { api, type PendingBaseline } from "@/lib/api";

interface Props {
  projectSlug: string;
}

export default function VisualRegressionPanel({ projectSlug }: Props) {
  const [rows, setRows] = useState<PendingBaseline[]>([]);
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState<boolean>(true);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState<string>("");

  const reload = useCallback(() => {
    setLoading(true);
    api.visualRegression
      .pending(projectSlug)
      .then((r) => {
        setRows(r);
        setEnabled(true);
      })
      .catch((err: unknown) => {
        // 403 / 503 = feature off; just hide the panel quietly.
        const m = err instanceof Error ? err.message : "";
        if (m.includes("403") || m.includes("503") || m.includes("not enabled") || m.includes("disabled")) {
          setEnabled(false);
        }
        setRows([]);
      })
      .finally(() => setLoading(false));
  }, [projectSlug]);

  useEffect(() => {
    reload();
  }, [reload]);

  const handlePromote = useCallback(
    async (row: PendingBaseline) => {
      const key = `${row.test_case_id}__${row.step_label}`;
      setBusy((b) => ({ ...b, [key]: true }));
      setMsg("");
      try {
        await api.visualRegression.promote(projectSlug, {
          test_case_id: row.test_case_id,
          step_label: row.step_label,
        });
        setMsg(`Baseline updated for ${row.test_case_id} / ${row.step_label}`);
        reload();
      } catch (err: unknown) {
        const m = err instanceof Error ? err.message : "Failed to promote";
        setMsg(m);
      } finally {
        setBusy((b) => ({ ...b, [key]: false }));
      }
    },
    [projectSlug, reload],
  );

  // Don't render the panel at all when the feature is off -- the
  // existing /projects/[name] page is dense enough without empty
  // "feature unavailable" rectangles.
  if (!enabled) return null;
  // Don't render when there's nothing pending. Users without drift
  // don't need the visual noise.
  if (!loading && rows.length === 0) return null;

  return (
    <div className="mt-6">
      <AnimatedCard glow="pink" delay={0.18}>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h3 className="text-sm font-bold text-white">
            Visual regression{" "}
            <span className="text-slate-500 font-normal">
              ({rows.length} pending baseline{rows.length === 1 ? "" : "s"})
            </span>
          </h3>
        </div>
        {loading ? (
          <p className="text-xs text-slate-500">Loading...</p>
        ) : (
          <ul className="space-y-2">
            {rows.map((row) => {
              const key = `${row.test_case_id}__${row.step_label}`;
              const baselineUrl = row.baseline_path
                ? api.visualRegression.screenshotUrl(
                    projectSlug,
                    "baseline",
                    `${row.test_case_id}__${row.step_label}.png`,
                  )
                : null;
              const currentUrl = api.visualRegression.screenshotUrl(
                projectSlug,
                "current",
                `${row.test_case_id}__${row.step_label}.png`,
              );
              return (
                <li
                  key={key}
                  className="rounded-xl border border-white/10 bg-white/5 p-3 flex flex-wrap items-center gap-3"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-slate-100 truncate">
                      <span className="font-mono text-[10px] text-slate-500">
                        {row.test_case_id.slice(0, 8)}
                      </span>{" "}
                      <span>·</span>{" "}
                      <span className="text-purple-300">{row.step_label}</span>
                    </div>
                    <div className="text-[11px] text-slate-500 mt-0.5">
                      {row.is_new ? (
                        <span className="text-amber-300">New (no baseline yet)</span>
                      ) : (
                        <>
                          Diff{" "}
                          <span className="text-pink-300">
                            {(row.diff_percent * 100).toFixed(2)}%
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {baselineUrl && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={baselineUrl}
                        alt="baseline"
                        className="h-14 w-20 object-cover rounded border border-white/10"
                        title="Baseline (click to view full size)"
                      />
                    )}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={currentUrl}
                      alt="current"
                      className="h-14 w-20 object-cover rounded border border-pink-400/30"
                      title="Current state"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => handlePromote(row)}
                    disabled={Boolean(busy[key])}
                    className="px-3 py-1.5 rounded-lg bg-emerald-600/30 text-emerald-200 border border-emerald-500/30 text-xs font-semibold hover:bg-emerald-600/50 disabled:opacity-50"
                    title="Promote the current screenshot to the new baseline"
                  >
                    {busy[key] ? "..." : "Update baseline"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {msg && (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mt-3 text-[11px] text-slate-400"
          >
            {msg}
          </motion.p>
        )}
      </AnimatedCard>
    </div>
  );
}
