"use client";

/**
 * Shared loading indicator used across list / detail pages.
 *
 * Variants:
 *   - "inline"    -- small spinner + label, fits in headers and cells
 *   - "block"     -- centered spinner + label inside a card/section
 *   - "skeleton"  -- animated bars sized to a typical row, repeated `rows` times
 *
 * Page authors should reach for this instead of hand-rolling
 * `<p>Loading...</p>` so empty / busy states feel consistent across
 * the platform.
 */

import { motion } from "framer-motion";

export type LoadingStateVariant = "inline" | "block" | "skeleton";

interface Props {
  variant?: LoadingStateVariant;
  label?: string;
  rows?: number;
  className?: string;
}

export default function LoadingState({
  variant = "block",
  label = "Loading…",
  rows = 3,
  className = "",
}: Props) {
  if (variant === "skeleton") {
    return (
      <div
        className={`space-y-2 ${className}`}
        role="status"
        aria-busy="true"
        aria-label={label}
      >
        {Array.from({ length: Math.max(1, rows) }).map((_, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0.5 }}
            animate={{ opacity: [0.5, 0.8, 0.5] }}
            transition={{ repeat: Infinity, duration: 1.4, delay: i * 0.08 }}
            className="h-9 rounded-lg bg-white/5 border border-white/5"
          />
        ))}
      </div>
    );
  }

  if (variant === "inline") {
    return (
      <span
        className={`inline-flex items-center gap-2 text-xs text-slate-400 ${className}`}
        role="status"
        aria-busy="true"
      >
        <Spinner size={14} />
        {label}
      </span>
    );
  }

  return (
    <div
      className={`flex items-center justify-center gap-3 py-10 text-sm text-slate-400 ${className}`}
      role="status"
      aria-busy="true"
    >
      <Spinner size={18} />
      <span>{label}</span>
    </div>
  );
}

function Spinner({ size = 16 }: { size?: number }) {
  // Plain inline SVG keeps the bundle small; framer-motion already
  // ships for the skeleton variant so we reuse it for the rotation.
  return (
    <motion.svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      animate={{ rotate: 360 }}
      transition={{ repeat: Infinity, duration: 0.9, ease: "linear" }}
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </motion.svg>
  );
}
