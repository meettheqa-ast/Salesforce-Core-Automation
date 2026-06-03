"use client";

/**
 * Shared right-/left-side Drawer primitive.
 *
 * Closes the IA audit gap where every page that wanted a drawer
 * (mobile sidebar, future activity drawer, future test-case detail
 * pop-out) was rolling its own portal + backdrop + focus-trap.
 *
 * Behaviour:
 *   - Slides in from the configured side
 *   - Backdrop click + Esc close
 *   - Body scroll lock while open
 *   - Renders into document.body via a portal so containers with
 *     `overflow: hidden` don't clip it
 *
 * The component is presentational; the caller owns open/close state
 * (this gives us the same control pattern used by ConfirmDeleteModal +
 * the existing UserMenu / NotificationsBell popovers).
 */

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Drawer side. Defaults to right which fits the project-home
   *  right-rail Activity feed use case + the future story-detail
   *  pop-outs. */
  side?: "right" | "left";
  /** Optional title rendered at the top of the drawer. */
  title?: string;
  /** Width in pixels (drawer slides full-height). */
  width?: number;
  children: React.ReactNode;
}

export default function Drawer({
  open,
  onClose,
  side = "right",
  title,
  width = 380,
  children,
}: Props) {
  const panelRef = useRef<HTMLDivElement | null>(null);

  // Body scroll lock + Esc close while open.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (typeof document === "undefined") return null;

  const initialX = side === "right" ? width : -width;
  const positionClass = side === "right" ? "right-0" : "left-0";

  return createPortal(
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40"
            aria-hidden="true"
          />
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            initial={{ x: initialX }}
            animate={{ x: 0 }}
            exit={{ x: initialX }}
            transition={{ type: "tween", duration: 0.2 }}
            style={{ width }}
            className={`fixed top-0 bottom-0 ${positionClass} z-50 bg-slate-900 border-${side === "right" ? "l" : "r"} border-white/10 shadow-2xl flex flex-col`}
          >
            {title !== undefined && (
              <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 shrink-0">
                <h2 className="text-sm font-semibold text-white truncate">{title}</h2>
                <button
                  type="button"
                  onClick={onClose}
                  aria-label="Close drawer"
                  className="text-slate-400 hover:text-white text-lg leading-none px-2"
                >
                  ×
                </button>
              </div>
            )}
            <div className="flex-1 min-h-0 overflow-y-auto">{children}</div>
          </motion.div>
        </>
      )}
    </AnimatePresence>,
    document.body,
  );
}
