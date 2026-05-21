"use client";

/**
 * ConfirmDeleteModal -- shared confirmation surface for every list page
 * that supports delete / bulk delete. Distinguishes the two lifecycle
 * actions explicitly so users don't accidentally hard-purge:
 *
 *   - "soft":      yellow tone, "Move to <archived-noun>" verb
 *   - "permanent": red tone, "Permanently delete" verb, irreversible
 *                  warning, blocker list when the backend returned 409
 *   - "hard":      red tone, "Remove from mirror" verb (Jira sync rows)
 *
 * Blockers are rendered as a bullet list, capped at 5 with "+ N more"
 * suffix, so a runaway selection doesn't drown the modal. The handler
 * is `async`: while inflight the buttons disable and show "Working…".
 *
 * The hosting page provides `onConfirm` -- it returns either
 * `{ ok: true }` on success or `{ ok: false, blockers? }` on
 * server-side refusal. The modal stays open in the refusal case so the
 * user sees the blockers without losing context; they dismiss
 * explicitly via "Close".
 */
import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";

export type ConfirmDeleteMode = "soft" | "permanent" | "hard";

export interface DeleteBlocker {
  id: string;
  label: string;
  reason?: string;
}

export interface ConfirmDeleteResult {
  ok: boolean;
  blockers?: DeleteBlocker[];
  message?: string;
}

interface Props {
  open: boolean;
  /** Singular entity name, e.g. "sprint" / "test case". */
  entityNoun: string;
  /** Pre-rendered names of the targets to show in the modal body.
   *  Capped at 5 by the component itself. */
  targetLabels: string[];
  mode: ConfirmDeleteMode;
  onConfirm: () => Promise<ConfirmDeleteResult>;
  onClose: () => void;
}

const MAX_LABELS = 5;
const MAX_BLOCKERS = 5;

function pluralise(noun: string, n: number): string {
  if (n === 1) return noun;
  if (/[sxz]$|sh$|ch$/i.test(noun)) return `${noun}es`;
  return `${noun}s`;
}

function bodyForMode(mode: ConfirmDeleteMode, noun: string, count: number): string {
  const target = `${count} ${pluralise(noun, count)}`;
  if (mode === "soft") {
    return `This will soft-delete ${target}. They will move to the cancelled / archived / rejected state and can still be permanently deleted (or recovered) later.`;
  }
  if (mode === "hard") {
    return `This will remove ${target} from the local mirror. Atlassian still has them; the next "Sync now" will re-pull them.`;
  }
  return `This will permanently delete ${target}. This cannot be undone.`;
}

function verb(mode: ConfirmDeleteMode): string {
  if (mode === "soft") return "Delete";
  if (mode === "hard") return "Remove from mirror";
  return "Permanently delete";
}

export default function ConfirmDeleteModal({
  open,
  entityNoun,
  targetLabels,
  mode,
  onConfirm,
  onClose,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [blockers, setBlockers] = useState<DeleteBlocker[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    // Reset surfaces every time the modal re-opens.
    if (open) {
      setBlockers([]);
      setErrorMessage(null);
      setBusy(false);
    }
  }, [open]);

  const count = targetLabels.length;
  const labelHead = targetLabels.slice(0, MAX_LABELS);
  const overflow = Math.max(0, count - MAX_LABELS);

  const handleConfirm = async () => {
    setBusy(true);
    setBlockers([]);
    setErrorMessage(null);
    try {
      const result = await onConfirm();
      if (result.ok) {
        onClose();
        return;
      }
      if (result.blockers && result.blockers.length > 0) {
        setBlockers(result.blockers);
      }
      if (result.message) {
        setErrorMessage(result.message);
      } else if (!result.blockers || result.blockers.length === 0) {
        setErrorMessage("The delete was refused by the server.");
      }
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  };

  const accent = mode === "soft"
    ? "border-amber-400/30 bg-amber-500/10 text-amber-100"
    : "border-red-400/40 bg-red-500/10 text-red-100";
  const actionBtn = mode === "soft"
    ? "bg-amber-500 hover:bg-amber-400 text-slate-900"
    : "bg-red-500 hover:bg-red-400 text-white";

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={busy ? undefined : onClose}
        >
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            className="glass-strong p-6 w-full max-w-lg"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <h2 className="text-lg font-bold text-white mb-2">
              {verb(mode)} {count} {pluralise(entityNoun, count)}?
            </h2>
            <p className="text-sm text-slate-300 mb-4">
              {bodyForMode(mode, entityNoun, count)}
            </p>

            {count > 0 && (
              <div className={`rounded-lg border px-3 py-2 text-xs mb-4 ${accent}`}>
                <ul className="space-y-1 list-disc pl-4">
                  {labelHead.map((l, i) => (
                    <li key={i} className="break-words">{l}</li>
                  ))}
                  {overflow > 0 && (
                    <li className="italic opacity-70">…and {overflow} more</li>
                  )}
                </ul>
              </div>
            )}

            {blockers.length > 0 && (
              <div className="rounded-lg border border-red-400/40 bg-red-500/10 px-3 py-2 text-xs mb-4">
                <div className="font-semibold text-red-200 mb-1">
                  Cannot permanently delete -- some {pluralise(entityNoun, 2)} still have live children:
                </div>
                <ul className="space-y-1 list-disc pl-4 text-red-100">
                  {blockers.slice(0, MAX_BLOCKERS).map((b) => (
                    <li key={b.id} className="break-words">
                      {b.label}
                      {b.reason && <span className="text-red-300/80"> -- {b.reason}</span>}
                    </li>
                  ))}
                  {blockers.length > MAX_BLOCKERS && (
                    <li className="italic opacity-70">
                      +{blockers.length - MAX_BLOCKERS} more
                    </li>
                  )}
                </ul>
                <div className="mt-2 text-red-300/90 text-[11px]">
                  Soft-delete the children first, then retry.
                </div>
              </div>
            )}

            {errorMessage && blockers.length === 0 && (
              <div className="text-xs text-red-300 mb-3 break-words">{errorMessage}</div>
            )}

            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={onClose}
                disabled={busy}
                className="px-4 py-2 rounded-xl text-sm border border-white/10 bg-white/5 text-slate-300 hover:bg-white/10 disabled:opacity-50"
              >
                {blockers.length > 0 ? "Close" : "Cancel"}
              </button>
              {blockers.length === 0 && (
                <button
                  type="button"
                  onClick={handleConfirm}
                  disabled={busy || count === 0}
                  className={`px-4 py-2 rounded-xl text-sm font-semibold disabled:opacity-50 ${actionBtn}`}
                  data-testid="confirm-delete"
                >
                  {busy ? "Working…" : verb(mode)}
                </button>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
