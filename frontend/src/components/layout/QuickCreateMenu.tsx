"use client";

/**
 * QuickCreateMenu
 *
 * "+ Quick create" dropdown in the floating top nav. Visible on every
 * page so users don't have to remember which page hosts which create
 * flow. Four entries:
 *
 *   - New project   -> CreateProjectModal
 *   - New sprint    -> CreateSprintModal (with optional project picker)
 *   - New story     -> CreateStoryModal  (with optional project + sprint pickers)
 *   - New test      -> /generate (existing AI-generation flow)
 *
 * The first three reuse the same modals as the in-page "+ New X"
 * buttons, so the experience is consistent regardless of entry point.
 *
 * Implementation note: the dropdown is rendered through a React portal
 * with `position: fixed` rather than `position: absolute`. The floating
 * navbar uses `overflow-x-auto` for narrow-screen scrolling, and per
 * CSS spec, when one overflow axis is non-visible the other becomes
 * `auto` too -- which would clip the dropdown to a thin sliver. This is
 * the same bug `UserMenu` hit and solved; we mirror that pattern here.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import CreateProjectModal from "@/components/projects/CreateProjectModal";
import CreateSprintModal from "@/components/sprints/CreateSprintModal";
import CreateStoryModal from "@/components/user-stories/CreateStoryModal";

type QuickCreateMenuProps = {
  buttonClassName?: string;
  label?: string;
  generateHref?: string;
};

export default function QuickCreateMenu({
  buttonClassName,
  label = "Create",
  generateHref = "/generate",
}: QuickCreateMenuProps) {
  const [open, setOpen] = useState(false);
  const [showProject, setShowProject] = useState(false);
  const [showSprint, setShowSprint] = useState(false);
  const [showStory, setShowStory] = useState(false);
  // Two refs: the trigger button (anchor + outside-click target) and the
  // portal-rendered dropdown itself (also an outside-click target so a
  // click INSIDE the menu doesn't dismiss it).
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  // Anchor coords in viewport space; the popup uses `position: fixed` so
  // it escapes the navbar's `overflow-x-auto` clip box.
  const [coords, setCoords] = useState<{ top: number; right: number } | null>(null);

  // Recompute coords on open + on viewport changes (resize, scroll), so
  // the dropdown stays glued to the trigger button.
  useEffect(() => {
    if (!open || !buttonRef.current) return;
    const update = () => {
      const r = buttonRef.current!.getBoundingClientRect();
      // Anchor top-right of dropdown to bottom-right of button + small gap.
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

  // Outside-click closes -- check both the trigger AND the portal popup.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (buttonRef.current?.contains(target)) return;
      if (popupRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const item = (label: string, icon: string, onSelect: () => void) => (
    <button
      type="button"
      onClick={() => {
        setOpen(false);
        onSelect();
      }}
      role="menuitem"
      className="w-full text-left px-3 py-2 rounded-md text-sm text-slate-200 hover:text-white hover:bg-purple-500/15 transition-colors flex items-center gap-2"
    >
      <span className="text-base">{icon}</span>
      <span>{label}</span>
    </button>
  );

  return (
    <>
      <div className="shrink-0">
        <motion.button
          ref={buttonRef}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          onClick={() => setOpen((v) => !v)}
          aria-haspopup="menu"
          aria-label="Quick create"
          aria-expanded={open}
          className={
            buttonClassName ||
            "px-3 py-2 rounded-lg text-sm font-semibold text-white bg-gradient-to-r from-purple-600 to-cyan-500 hover:brightness-110 flex items-center gap-1.5 whitespace-nowrap"
          }
        >
          <span className="text-base leading-none">+</span>
          <span>{label}</span>
        </motion.button>
      </div>

      {/* Portal-rendered popup. `position: fixed` + viewport-coords escape
          the navbar's overflow clip box. z-index 60 sits above the navbar
          (z-50) and modals (also z-50 fixed inset-0) but below the Cmd+K
          palette (z-70). */}
      {typeof document !== "undefined" &&
        createPortal(
          <AnimatePresence>
            {open && coords && (
              <motion.div
                ref={popupRef}
                initial={{ opacity: 0, y: -6, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -6, scale: 0.97 }}
                transition={{ duration: 0.12 }}
                role="menu"
                style={{
                  position: "fixed",
                  top: coords.top,
                  right: coords.right,
                  zIndex: 60,
                }}
                className="w-56 rounded-lg border border-white/10 bg-slate-900/95 backdrop-blur-xl shadow-2xl p-1.5"
              >
                {item("New project", "📂", () => setShowProject(true))}
                {item("New sprint", "🏃", () => setShowSprint(true))}
                {item("New story", "📖", () => setShowStory(true))}
                <Link
                  href={generateHref}
                  onClick={() => setOpen(false)}
                  role="menuitem"
                  className="w-full text-left px-3 py-2 rounded-md text-sm text-slate-200 hover:text-white hover:bg-purple-500/15 transition-colors flex items-center gap-2"
                >
                  <span className="text-base">🧪</span>
                  <span>New test (AI Generate)</span>
                </Link>
              </motion.div>
            )}
          </AnimatePresence>,
          document.body,
        )}

      <CreateProjectModal open={showProject} onClose={() => setShowProject(false)} />
      <CreateSprintModal open={showSprint} onClose={() => setShowSprint(false)} />
      <CreateStoryModal open={showStory} onClose={() => setShowStory(false)} />
    </>
  );
}
