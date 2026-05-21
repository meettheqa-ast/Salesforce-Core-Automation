"use client";

/**
 * Side-by-side diff viewer for prompt templates. Uses Monaco's
 * built-in DiffEditor so we get word-level highlighting + the same
 * keyboard shortcuts the editor tab uses, without bringing in another
 * dependency.
 *
 * The editor at /settings/prompts/[id] opens this in a slide-over to
 * answer "what did I actually change vs the system default?" -- a
 * cheaper user-research signal than maintaining a custom diff
 * algorithm + UI.
 */

import dynamic from "next/dynamic";
import { motion } from "framer-motion";

const DiffEditor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.DiffEditor),
  { ssr: false },
);

interface PromptDiffProps {
  /** The reference body the draft is compared against. Usually the
   *  system default OR the previously-saved version. */
  original: string;
  /** The current draft body shown on the right. */
  modified: string;
  /** Banner text under the diff -- e.g. "System default v1 vs your
   *  v4". Helps the user remember what they're comparing. */
  caption?: string;
  height?: string;
  onClose?: () => void;
}

export default function PromptDiff({
  original,
  modified,
  caption,
  height = "560px",
  onClose,
}: PromptDiffProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-2xl overflow-hidden border border-white/10 bg-slate-950"
    >
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/5 bg-white/5">
        <span className="text-xs text-slate-400">
          {caption || "Diff: system default ⟷ your draft"}
        </span>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-slate-500 hover:text-white px-2 py-1 rounded"
          >
            Close
          </button>
        )}
      </div>
      <DiffEditor
        height={height}
        language="markdown"
        theme="vs-dark"
        original={original}
        modified={modified}
        options={{
          readOnly: true,
          renderSideBySide: true,
          minimap: { enabled: false },
          fontSize: 13,
          scrollBeyondLastLine: false,
          wordWrap: "on",
          padding: { top: 12 },
        }}
      />
    </motion.div>
  );
}
