"use client";

/**
 * Small status pill used everywhere statuses appear (test cases,
 * sprints, stories, runs, integrations, healing). Picks colour + label
 * from a small semantic palette so we don't sprinkle Tailwind classes
 * across 40 files.
 *
 * Tone can be set explicitly OR auto-derived from a known status
 * string (so a caller can just pass the raw enum value).
 */

import type { ReactNode } from "react";

export type StatusTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "muted";

interface Props {
  /** Human-readable label rendered inside the pill. */
  label: ReactNode;
  /** Explicit tone override. If absent we try to infer from `status`. */
  tone?: StatusTone;
  /** Optional status string to drive auto-tone. Recognises the common
   *  values used across the platform: draft / approved / rejected /
   *  active / completed / cancelled / passed / failed / running etc. */
  status?: string;
  /** Slightly larger pill -- useful when the pill is the only thing on
   *  a row (sprint state, run result). */
  size?: "sm" | "md";
  className?: string;
}

const TONE_CLASSES: Record<StatusTone, string> = {
  neutral: "bg-slate-700/50 text-slate-200 border border-slate-500/30",
  info: "bg-cyan-600/20 text-cyan-200 border border-cyan-500/30",
  success: "bg-emerald-600/25 text-emerald-200 border border-emerald-500/30",
  warning: "bg-amber-600/25 text-amber-100 border border-amber-500/30",
  danger: "bg-red-600/25 text-red-200 border border-red-500/30",
  muted: "bg-white/5 text-slate-400 border border-white/10",
};

function inferTone(status?: string): StatusTone {
  const s = (status || "").toLowerCase();
  if (!s) return "neutral";
  if (["approved", "completed", "passed", "ok", "active", "running"].includes(s)) {
    return "success";
  }
  if (["rejected", "failed", "error", "cancelled", "blocked"].includes(s)) {
    return "danger";
  }
  if (["stale", "warning", "pending", "partial"].includes(s)) return "warning";
  if (["draft", "review", "in_progress", "queued"].includes(s)) return "info";
  return "neutral";
}

export default function StatusPill({
  label,
  tone,
  status,
  size = "sm",
  className = "",
}: Props) {
  const resolved = tone ?? inferTone(status);
  const sizing =
    size === "md"
      ? "text-[11px] px-2.5 py-1"
      : "text-[10px] px-2 py-0.5";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full uppercase tracking-wider ${sizing} ${TONE_CLASSES[resolved]} ${className}`}
    >
      {label}
    </span>
  );
}
