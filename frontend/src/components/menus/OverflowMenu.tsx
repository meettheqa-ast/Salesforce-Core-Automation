"use client";

/**
 * Tiny "More actions" overflow menu. Used to collapse per-row action
 * density on list/detail pages without sacrificing affordance.
 *
 * Why a custom component instead of a library: we already have two
 * portal-based dropdowns (UserMenu, NotificationsBell) and this one
 * needs slightly different mechanics -- it's anchored to a button
 * inside a row that may be in a `overflow: hidden` container, so we
 * use a portal too. The component is intentionally tiny (no
 * keyboard arrow navigation, no nested sub-menus) -- those land in
 * Phase 2 once the same pattern lives in 3+ surfaces.
 *
 * Items can be buttons (onClick) or links (href). Disabled items
 * still render so the user sees what's possible but can't click.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";

export interface OverflowMenuAction {
  label: string;
  onClick?: () => void;
  href?: string;
  /** When true the item renders dimmed and is not clickable. */
  disabled?: boolean;
  /** Optional inline icon (emoji or small SVG). */
  icon?: React.ReactNode;
  /** When true, render with a danger tint -- use for Delete /
   *  Permanently delete. Visual cue only; semantics still come from
   *  the label + ConfirmDeleteModal flow. */
  destructive?: boolean;
  /** Optional tooltip surfaced via `title` so the user can hover to
   *  read the rationale on disabled items. */
  title?: string;
}

interface Props {
  actions: OverflowMenuAction[];
  /** Override the trigger label (default is the kebab "...". */
  triggerLabel?: React.ReactNode;
  /** Custom className for the trigger button. */
  triggerClassName?: string;
  /** Right-align the popup relative to the trigger; default true. */
  alignRight?: boolean;
}

export default function OverflowMenu({
  actions,
  triggerLabel,
  triggerClassName,
  alignRight = true,
}: Props) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  const [coords, setCoords] = useState<{ top: number; left?: number; right?: number } | null>(
    null,
  );

  // Reposition when opened + on viewport changes.
  useEffect(() => {
    if (!open || !triggerRef.current) return;
    const update = () => {
      const r = triggerRef.current!.getBoundingClientRect();
      setCoords(
        alignRight
          ? { top: r.bottom + 4, right: window.innerWidth - r.right }
          : { top: r.bottom + 4, left: r.left },
      );
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, alignRight]);

  // Outside-click closes.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t)) return;
      if (popupRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  // Esc closes.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More actions"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={
          triggerClassName ||
          "px-2.5 py-1 text-xs rounded glass text-slate-300 hover:text-white"
        }
      >
        {triggerLabel ?? "More ▾"}
      </button>

      {typeof document !== "undefined" &&
        createPortal(
          <AnimatePresence>
            {open && coords && (
              <motion.div
                ref={popupRef}
                role="menu"
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.1 }}
                style={{
                  position: "fixed",
                  top: coords.top,
                  left: coords.left,
                  right: coords.right,
                  zIndex: 60,
                }}
                className="min-w-[200px] rounded-lg border border-white/10 bg-slate-900/95 backdrop-blur-xl shadow-2xl py-1"
              >
                {actions.map((a, i) => {
                  const cls = [
                    "block w-full text-left px-3 py-1.5 text-xs transition",
                    a.disabled
                      ? "text-slate-600 cursor-not-allowed"
                      : a.destructive
                        ? "text-red-200 hover:bg-red-500/15"
                        : "text-slate-200 hover:bg-white/5",
                  ].join(" ");
                  if (a.href && !a.disabled) {
                    return (
                      <Link
                        key={i}
                        href={a.href}
                        role="menuitem"
                        onClick={() => setOpen(false)}
                        className={cls}
                        title={a.title}
                      >
                        {a.icon && <span className="mr-2">{a.icon}</span>}
                        {a.label}
                      </Link>
                    );
                  }
                  return (
                    <button
                      key={i}
                      type="button"
                      role="menuitem"
                      disabled={a.disabled}
                      onClick={() => {
                        if (a.disabled) return;
                        setOpen(false);
                        a.onClick?.();
                      }}
                      className={cls}
                      title={a.title}
                    >
                      {a.icon && <span className="mr-2">{a.icon}</span>}
                      {a.label}
                    </button>
                  );
                })}
              </motion.div>
            )}
          </AnimatePresence>,
          document.body,
        )}
    </>
  );
}
