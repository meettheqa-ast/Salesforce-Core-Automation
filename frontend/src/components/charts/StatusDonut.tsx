"use client";

/**
 * Pure-SVG donut chart. No external chart library -- the four-slice case
 * is small enough that the bundle hit of recharts/chart.js isn't worth it,
 * and the visual style stays consistent with the rest of the glass UI.
 *
 * Each slice is rendered as a circle with a precomputed `stroke-dasharray`
 * + `stroke-dashoffset`, drawn in order. This is the canonical SVG donut
 * trick: no arc maths in the path, just dasharray accounting.
 *
 * Slices with `count <= 0` are skipped silently. When the total is 0, the
 * donut renders as a flat empty ring with the centre label "—".
 */

interface DonutSlice {
  /** Stable id used for animation keys; also doubles as accessible label. */
  id: string;
  label: string;
  count: number;
  /** Tailwind-compatible CSS color (e.g. "rgb(52 211 153)" or "#10b981"). */
  color: string;
}

interface StatusDonutProps {
  slices: DonutSlice[];
  /** Outer diameter in px. Default 96 (good for project tiles). */
  size?: number;
  /** Stroke width. Default 14. */
  thickness?: number;
  /** Optional centre label override; defaults to the total count. */
  centerLabel?: string;
  /** Optional subtitle below the centre number. */
  centerSubtitle?: string;
  /** Aria-label for the SVG, defaults to "Test case status breakdown". */
  ariaLabel?: string;
}

export default function StatusDonut({
  slices,
  size = 96,
  thickness = 14,
  centerLabel,
  centerSubtitle,
  ariaLabel = "Test case status breakdown",
}: StatusDonutProps) {
  const total = slices.reduce((sum, s) => sum + Math.max(0, s.count), 0);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const cx = size / 2;
  const cy = size / 2;

  // Slice ordering: keep the caller's order so colours line up with the
  // legend the page renders alongside the donut.
  let cumulative = 0;
  const segments = slices
    .filter((s) => s.count > 0)
    .map((s) => {
      const fraction = total > 0 ? s.count / total : 0;
      const dashLength = fraction * circumference;
      const offset = -cumulative;
      cumulative += dashLength;
      return {
        ...s,
        dashLength,
        offset,
      };
    });

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={ariaLabel}
      className="shrink-0"
    >
      {/* Track ring -- always visible so a 0-total donut still has shape. */}
      <circle
        cx={cx}
        cy={cy}
        r={radius}
        fill="none"
        stroke="rgba(255,255,255,0.08)"
        strokeWidth={thickness}
      />
      {segments.map((seg) => (
        <circle
          key={seg.id}
          cx={cx}
          cy={cy}
          r={radius}
          fill="none"
          stroke={seg.color}
          strokeWidth={thickness}
          strokeDasharray={`${seg.dashLength} ${circumference - seg.dashLength}`}
          strokeDashoffset={seg.offset}
          // Rotate so the first slice starts at the top (12 o'clock).
          transform={`rotate(-90 ${cx} ${cy})`}
          strokeLinecap="butt"
        >
          <title>{`${seg.label}: ${seg.count}`}</title>
        </circle>
      ))}
      <text
        x={cx}
        y={cy - (centerSubtitle ? 4 : -4)}
        textAnchor="middle"
        dominantBaseline="middle"
        className="fill-white font-semibold"
        style={{ fontSize: size * 0.22 }}
      >
        {centerLabel ?? (total > 0 ? total : "—")}
      </text>
      {centerSubtitle && (
        <text
          x={cx}
          y={cy + size * 0.16}
          textAnchor="middle"
          dominantBaseline="middle"
          className="fill-slate-400"
          style={{ fontSize: size * 0.1, letterSpacing: "0.05em" }}
        >
          {centerSubtitle.toUpperCase()}
        </text>
      )}
    </svg>
  );
}

/**
 * Standard slice-color palette so the donut, legend chips, and filter
 * chips agree on which colour means which state across the whole app.
 *
 * Tailwind class equivalents (for chip backgrounds):
 *   approved -> bg-emerald-500/30 text-emerald-200
 *   draft    -> bg-slate-500/40    text-slate-200
 *   rejected -> bg-red-500/30      text-red-200
 *   stale    -> bg-amber-500/30    text-amber-200
 *   built    -> bg-cyan-500/30     text-cyan-200
 *   no-script -> bg-purple-500/30  text-purple-200
 */
export const DONUT_COLORS = {
  approved: "rgb(52 211 153)",     // emerald-400
  draft: "rgb(148 163 184)",        // slate-400
  rejected: "rgb(248 113 113)",     // red-400
  stale: "rgb(251 191 36)",         // amber-400
  built: "rgb(34 211 238)",         // cyan-400
  noScript: "rgb(192 132 252)",     // purple-400
} as const;
