"use client";

/**
 * Sticky "N selected" toolbar that appears at the top of a list when the
 * user has at least one row selected. Renders the entity name in the
 * count ("3 sprints selected"), a Delete button whose verb depends on
 * the lifecycle mode, and an optional "Delete permanently" affordance
 * when the selection contains rows that have already been soft-deleted.
 *
 * The hosting page owns the entity list + selection state and computes
 * `mode` from the selected rows:
 *
 *   - "soft":      every selected row is still live; first delete moves
 *                  them to cancelled / archived / rejected
 *   - "permanent": every selected row is already soft-deleted; the
 *                  next delete is destructive
 *   - "mixed":     selection spans both -- we show two buttons (Delete
 *                  the live ones, Permanently delete the soft-deleted
 *                  ones) so the user can finish the lifecycle in one
 *                  shot without de-selecting
 *
 * Jira-mirror lists pass `mode="hard"` directly because there is no
 * soft step for synced rows -- they hard-delete from the local mirror
 * (next "Sync now" re-pulls). The button label switches to "Remove
 * from mirror" in that case.
 */
import { motion, AnimatePresence } from "framer-motion";

export type SelectionToolbarMode = "soft" | "permanent" | "mixed" | "hard";

interface Props {
  count: number;
  /** Singular entity name, e.g. "sprint" / "story" / "test case" /
   *  "Jira sprint" / "Jira issue". Plural is auto-derived (+ "s"). */
  entityNoun: string;
  mode: SelectionToolbarMode;
  busy?: boolean;
  /** Override the verb on the soft-delete button. Defaults to "Delete"
   *  which suits sprints and stories. Test cases use "Archive" because
   *  the lifecycle UX promises restorability. */
  softLabel?: string;
  /** Soft delete (sprint -> cancelled, story -> archived, tc ->
   *  rejected). Not rendered when mode === "permanent" or "hard". */
  onSoftDelete?: () => void;
  /** Hard / permanent delete. Always rendered when mode is "permanent",
   *  "mixed", or "hard". Caller decides what to show in the
   *  confirmation modal. */
  onHardDelete?: () => void;
  /** Clear selection (acts on the same hook the parent owns). */
  onClear: () => void;
}

function pluralise(noun: string, n: number): string {
  if (n === 1) return noun;
  if (/[sxz]$|sh$|ch$/i.test(noun)) return `${noun}es`;
  return `${noun}s`;
}

export default function SelectionToolbar({
  count,
  entityNoun,
  mode,
  busy = false,
  softLabel = "Delete",
  onSoftDelete,
  onHardDelete,
  onClear,
}: Props) {
  const noun = pluralise(entityNoun, count);
  const showSoft = (mode === "soft" || mode === "mixed") && onSoftDelete !== undefined;
  const showHard = (mode === "permanent" || mode === "mixed" || mode === "hard") && onHardDelete !== undefined;
  const hardLabel = mode === "hard" ? "Remove from mirror" : "Delete permanently";

  return (
    <AnimatePresence>
      {count > 0 && (
        <motion.div
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          className="sticky top-0 z-20 mb-3 flex items-center justify-between gap-3 rounded-xl border border-amber-400/30 bg-amber-500/10 px-3 py-2 backdrop-blur"
          data-testid="selection-toolbar"
        >
          <div className="text-xs text-amber-100">
            <span className="font-semibold">{count}</span>{" "}
            <span className="opacity-80">{noun} selected</span>
          </div>
          <div className="flex gap-2">
            {showSoft && (
              <button
                type="button"
                onClick={onSoftDelete}
                disabled={busy}
                className="px-3 py-1.5 rounded-lg text-xs border border-amber-300/40 bg-amber-500/20 text-amber-100 hover:bg-amber-500/30 disabled:opacity-50"
                data-testid="bulk-soft-delete"
              >
                {busy ? "Working…" : `${softLabel} (${count})`}
              </button>
            )}
            {showHard && (
              <button
                type="button"
                onClick={onHardDelete}
                disabled={busy}
                className="px-3 py-1.5 rounded-lg text-xs border border-red-400/40 bg-red-500/20 text-red-100 hover:bg-red-500/30 disabled:opacity-50"
                data-testid="bulk-hard-delete"
              >
                {busy ? "Working…" : `${hardLabel} (${count})`}
              </button>
            )}
            <button
              type="button"
              onClick={onClear}
              disabled={busy}
              className="px-3 py-1.5 rounded-lg text-xs border border-white/10 bg-white/5 text-slate-300 hover:bg-white/10 disabled:opacity-50"
            >
              Clear
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
