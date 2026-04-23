"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { signOut, useSession } from "next-auth/react";
import { motion, AnimatePresence } from "framer-motion";
import { api, clearAuthCache, type MeResponse } from "@/lib/api";

const AUTH_DISABLED =
  (process.env.NEXT_PUBLIC_AUTH_DISABLED || "").toLowerCase() === "true";

export default function UserMenu() {
  const { data: session, status } = useSession();
  const [open, setOpen] = useState(false);
  const [me, setMe] = useState<MeResponse | null>(null);
  // Two refs: the trigger button (to anchor + outside-click) and the dropdown
  // (rendered through a portal so we can detect outside-click on it too).
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  // Anchor position in viewport coords; the dropdown uses `position: fixed` so
  // it escapes the navbar's `overflow-x-auto` clip box. (CSS spec: when one
  // overflow axis is non-visible, the other becomes auto too -- the previous
  // `position: absolute` dropdown rendered as a 2px sliver because of this.)
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(null);

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

  // Recalculate coords on open + on viewport change so the dropdown stays
  // glued to the button if the user resizes / scrolls the page.
  useEffect(() => {
    if (!open || !buttonRef.current) return;
    const update = () => {
      const r = buttonRef.current!.getBoundingClientRect();
      // Anchor top-right of dropdown to bottom-right of button, plus a small gap.
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

  // Outside-click closes -- check both the trigger and the dropdown.
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
    <div className="shrink-0 ml-1">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2 pl-1.5 pr-2.5 py-1.5 rounded-lg hover:bg-white/5 transition"
      >
        {picture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={picture}
            alt=""
            referrerPolicy="no-referrer"
            className="w-7 h-7 rounded-full ring-1 ring-white/20"
          />
        ) : (
          <div className="w-7 h-7 rounded-full bg-gradient-to-br from-purple-500 to-cyan-400 flex items-center justify-center text-xs font-bold text-white">
            {initials}
          </div>
        )}
        <span className="text-[13px] text-slate-300 max-w-[140px] truncate">
          {name.split(" ")[0]}
        </span>
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
                className="w-64 rounded-xl border border-white/10 bg-slate-900/95 backdrop-blur-xl shadow-2xl py-2"
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
          </AnimatePresence>,
          document.body,
        )}
    </div>
  );
}
