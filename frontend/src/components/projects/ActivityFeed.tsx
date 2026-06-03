"use client";

/**
 * Compact "what happened in this project" feed. Mounts on the
 * project home page (right rail per the IA audit recommendation,
 * inline column on narrower screens) so members can see recent
 * activity without admin permissions or hopping to /admin/audit.
 *
 * Backed by /api/projects/{slug}/activity. Newest-first; clicking
 * Load more pages through the `next_before` cursor.
 */

import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  api,
  type ProjectActivityResponse,
  type ProjectActivityRow,
} from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";
import EmptyState from "@/components/feedback/EmptyState";
import StatusPill from "@/components/data/StatusPill";

interface Props {
  projectSlug: string;
  /** Initial page size. The feed pages with `next_before`. */
  limit?: number;
  className?: string;
}

function toneFor(canonical: string) {
  if (canonical.startsWith("tc.purged")) return "danger";
  if (canonical.startsWith("tc.archived") || canonical.startsWith("story.archived")) return "warning";
  if (canonical.endsWith(".failed")) return "danger";
  if (canonical.endsWith(".passed") || canonical.endsWith(".completed")) return "success";
  if (canonical.startsWith("admin.")) return "warning";
  return "info";
}

function relTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const ms = Date.now() - d.getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const dy = Math.floor(h / 24);
  return `${dy}d ago`;
}

export default function ActivityFeed({
  projectSlug,
  limit = 20,
  className = "",
}: Props) {
  const [rows, setRows] = useState<ProjectActivityRow[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const load = useCallback(
    async (before?: string) => {
      try {
        const r: ProjectActivityResponse = await api.projects.activity(projectSlug, {
          limit,
          before,
        });
        setRows((prev) => (before ? [...prev, ...r.items] : r.items));
        setNextBefore(r.next_before);
      } catch (e) {
        setErr(e);
      }
    },
    [projectSlug, limit],
  );

  useEffect(() => {
    setLoading(true);
    setErr(null);
    setRows([]);
    setNextBefore(null);
    load().finally(() => setLoading(false));
  }, [load]);

  return (
    <AnimatedCard glow="purple" className={`p-3 ${className}`}>
      <h3 className="text-xs uppercase tracking-wider text-cyan-300 mb-2">
        Project activity
      </h3>
      <ErrorBanner error={err} onDismiss={() => setErr(null)} />
      {loading ? (
        <LoadingState variant="skeleton" rows={4} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No activity yet"
          description="Audit events for stories, test cases, runs, and prompt changes will appear here as the team uses the project."
        />
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li
              key={r.id}
              className="flex items-start gap-2 rounded-lg border border-white/5 px-2 py-1.5 bg-white/[0.02]"
            >
              <motion.span
                initial={{ scale: 0.85 }}
                animate={{ scale: 1 }}
                className="mt-0.5 shrink-0"
              >
                <StatusPill tone={toneFor(r.canonical)} label={r.canonical.split(".")[0]} />
              </motion.span>
              <div className="min-w-0 flex-1">
                <p className="text-xs text-slate-200 break-words">
                  <span className="text-slate-100 font-medium">
                    {r.user_name || r.user_email || "Someone"}
                  </span>{" "}
                  <span className="text-slate-400">{r.label}</span>
                </p>
                <p className="text-[10px] text-slate-500 mt-0.5">{relTime(r.timestamp)}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
      {!loading && nextBefore && (
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            await load(nextBefore);
            setBusy(false);
          }}
          className="mt-3 w-full text-xs px-3 py-1.5 rounded-lg glass text-slate-300 hover:text-white disabled:opacity-40"
        >
          {busy ? "Loading…" : "Load more"}
        </button>
      )}
    </AnimatedCard>
  );
}
