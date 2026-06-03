"use client";

/**
 * Single-number metric tile used on dashboards and summary strips.
 *
 * Standardises the look of "12 stories", "84% pass rate",
 * "23 minutes" etc. so every dashboard renders the same way and we
 * can tune the look in one place.
 */

import type { ReactNode } from "react";

export type MetricTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger";

interface Props {
  label: string;
  value: ReactNode;
  /** Optional small note under the value -- delta, percentage, last
   *  updated, "of N", etc. */
  hint?: ReactNode;
  /** Optional small icon glyph (emoji or SVG) shown left of the label. */
  icon?: ReactNode;
  tone?: MetricTone;
  /** When true the tile fills the available width; otherwise it sizes
   *  to content with a min-width that keeps a row of tiles tidy. */
  fluid?: boolean;
  className?: string;
}

const TONE_CLASSES: Record<MetricTone, string> = {
  neutral: "border-white/10 bg-white/[0.03]",
  info: "border-cyan-500/30 bg-cyan-500/10",
  success: "border-emerald-500/30 bg-emerald-500/10",
  warning: "border-amber-500/30 bg-amber-500/10",
  danger: "border-red-500/30 bg-red-500/10",
};

const VALUE_TONES: Record<MetricTone, string> = {
  neutral: "text-white",
  info: "text-cyan-100",
  success: "text-emerald-100",
  warning: "text-amber-100",
  danger: "text-red-100",
};

export default function MetricTile({
  label,
  value,
  hint,
  icon,
  tone = "neutral",
  fluid = false,
  className = "",
}: Props) {
  return (
    <div
      className={`rounded-xl border px-3 py-3 ${TONE_CLASSES[tone]} ${fluid ? "w-full" : "min-w-[120px]"} ${className}`}
    >
      <div className="flex items-center gap-2">
        {icon && <span className="opacity-80">{icon}</span>}
        <span className="text-[11px] uppercase tracking-wider text-slate-400 truncate">
          {label}
        </span>
      </div>
      <div className={`mt-1 text-2xl font-bold ${VALUE_TONES[tone]}`}>{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}
