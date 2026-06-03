"use client";

/**
 * Full notifications inbox -- the bell dropdown only shows the latest
 * few; this page is what "View all" links to so users can browse the
 * full history, filter unread vs read, and mark items in bulk.
 *
 * Backed by /api/me/notifications + /api/me/notifications/read-all
 * which already exist. The bell badge polls unread-count separately
 * and is unaffected by what happens on this page.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api, type NotificationRow } from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";
import EmptyState from "@/components/feedback/EmptyState";
import StatusPill from "@/components/data/StatusPill";

type Filter = "all" | "unread";

const TYPE_TONE: Record<string, "info" | "success" | "warning" | "danger" | "muted"> = {
  project_invitation: "info",
  project_access_request: "info",
  invitation_accepted: "success",
  invitation_rejected: "warning",
  access_request_approved: "success",
  access_request_rejected: "warning",
  story_mention: "info",
  "run.failed": "danger",
  "run.passed": "success",
  "generation.completed": "success",
  "prompt.activated_by_admin": "info",
  "heal.aggregated": "info",
};

function toneFor(type: string) {
  return TYPE_TONE[type] || "muted";
}

export default function NotificationsPage() {
  const [rows, setRows] = useState<NotificationRow[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const list = await api.notifications.list(filter === "unread");
      setRows(list);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not load notifications");
    } finally {
      setBusy(false);
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const unreadCount = useMemo(
    () => (rows || []).filter((r) => !r.read_at).length,
    [rows],
  );

  async function handleMarkRead(id: string) {
    try {
      await api.notifications.markRead(id);
      setRows((prev) =>
        prev?.map((r) =>
          r.id === id ? { ...r, read_at: new Date().toISOString() } : r,
        ) || null,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Mark-read failed");
    }
  }

  async function handleMarkAllRead() {
    setBusy(true);
    setErr(null);
    try {
      await api.notifications.markAllRead();
      const now = new Date().toISOString();
      setRows((prev) =>
        prev?.map((r) => (r.read_at ? r : { ...r, read_at: now })) || null,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Mark-all-read failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Inbox"
          title="Notifications"
          description={
            unreadCount > 0
              ? `${unreadCount} unread of ${rows?.length ?? 0} total.`
              : "Mentions, invitations, run alerts, and generation events."
          }
        />
      </motion.div>

      <ErrorBanner error={err} onDismiss={() => setErr(null)} />

      <AnimatedCard glow="purple">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div className="flex gap-1">
            {(["all", "unread"] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={`px-3 py-1.5 rounded-lg text-xs ${
                  filter === f
                    ? "bg-purple-500/20 text-purple-100 border border-purple-500/40"
                    : "glass text-slate-300 hover:text-white"
                }`}
              >
                {f === "all" ? "All" : `Unread (${unreadCount})`}
              </button>
            ))}
          </div>
          <button
            type="button"
            disabled={busy || unreadCount === 0}
            onClick={handleMarkAllRead}
            className="px-3 py-1.5 rounded-lg glass text-xs text-slate-300 hover:text-white disabled:opacity-40"
          >
            Mark all as read
          </button>
        </div>

        {rows === null ? (
          <LoadingState variant="block" label="Loading notifications…" />
        ) : rows.length === 0 ? (
          <EmptyState
            title={filter === "unread" ? "No unread notifications" : "No notifications yet"}
            description={
              filter === "unread"
                ? "You're all caught up. Switch to All to see history."
                : "Run failures, generation events, and mentions will land here as the platform fires them."
            }
            primary={
              filter === "unread"
                ? { label: "Show all", onClick: () => setFilter("all") }
                : undefined
            }
          />
        ) : (
          <ul className="divide-y divide-white/5">
            {rows.map((n) => {
              const unread = !n.read_at;
              return (
                <li
                  key={n.id}
                  className={`py-3 flex flex-wrap items-start gap-3 ${
                    unread ? "bg-purple-500/[0.04]" : ""
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap mb-0.5">
                      <StatusPill tone={toneFor(n.type)} label={n.type.replaceAll("_", " ")} />
                      {unread && (
                        <span className="text-[10px] uppercase tracking-wider text-purple-300">
                          New
                        </span>
                      )}
                      <span className="text-[11px] text-slate-500">
                        {n.created_at ? new Date(n.created_at).toLocaleString() : ""}
                      </span>
                    </div>
                    <p className="text-sm text-slate-100 font-medium">{n.title}</p>
                    {n.body && (
                      <p className="text-xs text-slate-400 mt-0.5 whitespace-pre-line">
                        {n.body}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    {n.action_url && (
                      <Link
                        href={n.action_url}
                        onClick={() => unread && void handleMarkRead(n.id)}
                        className="text-xs px-2.5 py-1 rounded bg-cyan-600/30 text-cyan-100 hover:bg-cyan-600/50"
                      >
                        Open →
                      </Link>
                    )}
                    {unread && (
                      <button
                        type="button"
                        onClick={() => void handleMarkRead(n.id)}
                        className="text-[11px] text-slate-400 hover:text-white px-2 py-0.5"
                      >
                        Mark read
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </AnimatedCard>
    </PageScaffold>
  );
}
