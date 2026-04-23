"use client";

import { useEffect, useRef, useState } from "react";
import { signOut, useSession } from "next-auth/react";
import { motion, AnimatePresence } from "framer-motion";
import { api, clearAuthCache, type MeResponse } from "@/lib/api";

const AUTH_DISABLED =
  (process.env.NEXT_PUBLIC_AUTH_DISABLED || "").toLowerCase() === "true";

export default function UserMenu() {
  const { data: session, status } = useSession();
  const [open, setOpen] = useState(false);
  const [me, setMe] = useState<MeResponse | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  // Pull is_admin / canonical user id from the backend (NextAuth only knows what
  // Google handed us; the backend owns the source of truth for is_admin).
  useEffect(() => {
    if (AUTH_DISABLED) return;
    if (status !== "authenticated") return;
    let cancelled = false;
    api.me().then(
      (m) => {
        if (!cancelled) setMe(m);
      },
      () => {
        // Backend rejected the token (wrong domain, etc). Fall back to session info.
      },
    );
    return () => {
      cancelled = true;
    };
  }, [status]);

  // Close on outside click.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  // Hooks all called above the early returns to satisfy rules of hooks.
  if (AUTH_DISABLED) return null;
  if (status !== "authenticated" || !session?.user) return null;

  const name = me?.name || session.user.name || session.user.email || "Account";
  const email = me?.email || session.user.email || "";
  const picture = me?.picture || session.user.image || "";
  const isAdmin = !!me?.is_admin;
  const initials =
    (name || email || "?")
      .split(/[\s.@]/)
      .filter(Boolean)
      .slice(0, 2)
      .map((s) => s[0]?.toUpperCase())
      .join("") || "?";

  async function handleSignOut() {
    clearAuthCache();
    await signOut({ redirectTo: "/login" });
  }

  return (
    <div ref={ref} className="relative shrink-0 ml-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2 pl-1 pr-2 py-1 rounded-lg hover:bg-white/5 transition"
      >
        {picture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={picture}
            alt=""
            referrerPolicy="no-referrer"
            className="w-6 h-6 rounded-full ring-1 ring-white/20"
          />
        ) : (
          <div className="w-6 h-6 rounded-full bg-gradient-to-br from-purple-500 to-cyan-400 flex items-center justify-center text-[10px] font-bold text-white">
            {initials}
          </div>
        )}
        <span className="text-[12px] text-slate-300 max-w-[120px] truncate">
          {name.split(" ")[0]}
        </span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.12 }}
            role="menu"
            className="absolute right-0 mt-2 w-64 rounded-xl border border-white/10 bg-slate-900/95 backdrop-blur-xl shadow-2xl py-2"
          >
            <div className="px-3 py-2 border-b border-white/5">
              <div className="text-sm text-white font-medium truncate">{name}</div>
              <div className="text-xs text-slate-400 truncate">{email}</div>
              {isAdmin && (
                <div className="mt-1 inline-flex items-center px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-200 text-[10px] font-semibold uppercase tracking-wider">
                  Admin
                </div>
              )}
            </div>
            <button
              type="button"
              role="menuitem"
              onClick={handleSignOut}
              className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-white/5 transition"
            >
              Sign out
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
