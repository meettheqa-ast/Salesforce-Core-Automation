"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { useSession } from "next-auth/react";
import UserMenu from "./UserMenu";
import NotificationsBell from "./NotificationsBell";
import { SIDEBAR_TOGGLE_EVENT } from "./LeftHierarchySidebar";
import QuickCreateMenu from "./QuickCreateMenu";

const AUTH_DISABLED =
  (process.env.NEXT_PUBLIC_AUTH_DISABLED || "").toLowerCase() === "true";

export default function FloatingNavbar() {
  const pathname = usePathname();
  const { status } = useSession();
  const canUseWorkspaceActions = AUTH_DISABLED || status === "authenticated";
  // Pick the right modifier label so Mac users see "Cmd" and others see "Ctrl".
  // SSR-safe: defaults to "Ctrl" on the server; flips after mount when we
  // can read navigator.platform without a hydration mismatch.
  const [modKey, setModKey] = useState("Ctrl");
  useEffect(() => {
    if (typeof navigator === "undefined") return;
    if (/Mac|iPhone|iPad/.test(navigator.platform)) setModKey("\u2318");
  }, []);

  const triggerCmdK = () => {
    // Synthesize a Cmd+K (Ctrl+K on non-Mac) event so the global listener
    // in CommandPalette reacts the same way it would for a real keypress.
    // Cleaner than coupling components with a shared store for one click.
    const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
    const ev = new KeyboardEvent("keydown", {
      key: "k",
      metaKey: isMac,
      ctrlKey: !isMac,
      bubbles: true,
    });
    window.dispatchEvent(ev);
  };

  return (
    <motion.nav
      initial={{ y: -100, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.6, ease: "easeOut" }}
      aria-label="Primary"
      className="fixed top-0 left-0 right-0 z-50 app-topbar px-3 md:px-6 h-14"
    >
      <div className="max-w-[1240px] mx-auto h-full flex items-center gap-2 md:gap-4">
        {canUseWorkspaceActions && (
          <button
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent(SIDEBAR_TOGGLE_EVENT))}
            aria-label="Open navigation"
            className="lg:hidden shrink-0 p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
        )}

        <Link href="/" className="flex items-center gap-2 px-2 py-1.5 shrink-0" title={pathname === "/" ? "Home" : "Go to home"}>
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-purple-500 to-cyan-400 animate-pulse-glow shadow-[0_0_14px_rgba(139,92,246,0.45)]" />
          <span className="inline text-xs sm:text-sm font-semibold tracking-wide text-slate-100">
            AI QA Portal
          </span>
        </Link>

        <div className="ml-auto flex items-center gap-1.5">
          {canUseWorkspaceActions && (
            <>
              <QuickCreateMenu
                label="Quick create"
                buttonClassName="shrink-0 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold text-white bg-gradient-to-r from-purple-600 to-cyan-500 hover:brightness-110"
              />
              <button
                type="button"
                onClick={triggerCmdK}
                title={`Quick search (${modKey} K)`}
                aria-label="Open command palette"
                className="shrink-0 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-slate-400 hover:text-white hover:bg-white/5 border border-white/10 transition-colors"
              >
                <span className="text-sm">⌕</span>
                <kbd className="font-mono text-[10px] text-slate-500">{modKey}</kbd>
                <kbd className="font-mono text-[10px] text-slate-500">K</kbd>
              </button>
            </>
          )}
          <NotificationsBell />
          <UserMenu />
        </div>
      </div>
    </motion.nav>
  );
}
