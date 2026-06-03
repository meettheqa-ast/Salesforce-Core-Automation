"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import useSWR from "swr";
import { useSession } from "next-auth/react";
import { api, type NotificationRow } from "@/lib/api";

const AUTH_DISABLED =
  (process.env.NEXT_PUBLIC_AUTH_DISABLED || "").toLowerCase() === "true";

const POLL_MS = 60_000;

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export default function NotificationsBell() {
  const { status } = useSession();
  const enabled = AUTH_DISABLED || status === "authenticated";
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(null);

  const { data: countData, mutate: refreshCount } = useSWR<{ count: number }>(
    enabled ? "/api/me/notifications/unread-count" : null,
    () => api.notifications.unreadCount(),
    { refreshInterval: POLL_MS, revalidateOnFocus: true, shouldRetryOnError: false },
  );
  const unread = countData?.count ?? 0;

  const { data: rows, mutate: refreshRows } = useSWR<NotificationRow[]>(
    enabled && open ? "/api/me/notifications?recent" : null,
    () => api.notifications.list(false),
    { revalidateOnFocus: false },
  );

  // Anchor coords for portal-rendered popup (same trick as UserMenu).
  useEffect(() => {
    if (!open || !buttonRef.current) return;
    const update = () => {
      const r = buttonRef.current!.getBoundingClientRect();
      setCoords({ top: r.bottom + 8, right: window.innerWidth - r.right });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  // Outside-click closes.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      const target = e.target as Node;
      if (buttonRef.current?.contains(target)) return;
      if (popupRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  if (!enabled) return null;

  async function handleMarkAll() {
    try {
      await api.notifications.markAllRead();
      refreshCount();
      refreshRows();
    } catch {
      /* swallow -- next poll will retry */
    }
  }

  async function handleClick(n: NotificationRow) {
    if (!n.read_at) {
      try {
        await api.notifications.markRead(n.id);
        refreshCount();
        refreshRows();
      } catch {
        /* ignore */
      }
    }
    setOpen(false);
  }

  return (
    <div className="shrink-0">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={unread ? `${unread} unread notification${unread === 1 ? "" : "s"}` : "Notifications"}
        className="relative p-2 rounded-lg hover:bg-white/5 transition"
      >
        <BellIcon />
        {unread > 0 && (
          <span className="absolute top-1 right-1 min-w-[16px] h-[16px] px-1 rounded-full bg-pink-500 text-white text-[9px] font-bold flex items-center justify-center">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {typeof document !== "undefined" &&
        createPortal(
          <AnimatePresence>
            {open && coords && (
              <motion.div
                ref={popupRef}
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.12 }}
                role="menu"
                style={{
                  position: "fixed",
                  top: coords.top,
                  right: coords.right,
                  zIndex: 60,
                }}
                className="w-[360px] max-h-[480px] overflow-y-auto rounded-xl border border-white/10 bg-slate-900/95 backdrop-blur-xl shadow-2xl"
              >
                <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
                  <span className="text-sm font-semibold text-white">Notifications</span>
                  <button
                    type="button"
                    onClick={handleMarkAll}
                    disabled={unread === 0}
                    className="text-[11px] text-cyan-300 hover:text-cyan-200 disabled:text-slate-600"
                  >
                    Mark all read
                  </button>
                </div>
                {!rows ? (
                  <div className="px-4 py-6 text-xs text-slate-500">Loading...</div>
                ) : rows.length === 0 ? (
                  <div className="px-4 py-6 text-xs text-slate-500 text-center">No notifications yet.</div>
                ) : (
                  <ul className="py-1">
                    {rows.map((n) => {
                      const unreadDot = !n.read_at;
                      const inner = (
                        <div className={`flex gap-3 px-4 py-3 hover:bg-white/5 transition ${unreadDot ? "bg-purple-500/5" : ""}`}>
                          <span className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${unreadDot ? "bg-purple-400" : "bg-transparent"}`} />
                          <div className="flex-1 min-w-0">
                            <div className="text-sm text-white truncate">{n.title}</div>
                            {n.body && (
                              <div className="text-xs text-slate-400 mt-0.5">{n.body}</div>
                            )}
                            <div className="text-[10px] text-slate-600 mt-1">{timeAgo(n.created_at)}</div>
                          </div>
                        </div>
                      );
                      return (
                        <li key={n.id}>
                          {n.action_url ? (
                            <Link href={n.action_url} onClick={() => handleClick(n)}>
                              {inner}
                            </Link>
                          ) : (
                            <button type="button" onClick={() => handleClick(n)} className="w-full text-left">
                              {inner}
                            </button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {/* Footer: deep-link into the full inbox. Closes the
                    IA gap where the bell was the only surface and
                    users couldn't browse history beyond the latest
                    ~10. The /notifications page lives in app/ now. */}
                <div className="border-t border-white/5 px-4 py-2 text-right">
                  <Link
                    href="/notifications"
                    onClick={() => setOpen(false)}
                    className="text-[11px] text-cyan-300 hover:text-cyan-200"
                  >
                    View all →
                  </Link>
                </div>
              </motion.div>
            )}
          </AnimatePresence>,
          document.body,
        )}
    </div>
  );
}

function BellIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="text-slate-300">
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  );
}
