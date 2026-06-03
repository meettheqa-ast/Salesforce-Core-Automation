"use client";

/**
 * Inline (non-portal) right rail. Use when the rail belongs inside
 * the page flow rather than as a sliding drawer overlay -- e.g. the
 * /settings/prompts/[id] editor's placeholder reference column, or
 * the project home's Activity feed when the screen is wide enough.
 *
 * For overlay behaviour (mobile or pop-out flows) use `Drawer`
 * instead.
 *
 * Responsive: on screens smaller than `lg:`, the rail drops below
 * the main column instead of competing for width.
 */

import type { ReactNode } from "react";

interface Props {
  /** Width in px on lg+ screens. Defaults to 320. */
  width?: number;
  /** Optional title rendered above the rail content. */
  title?: ReactNode;
  /** Sticky offset from the top of the viewport (px). When set, the
   *  rail stays visible as the user scrolls the main column. */
  stickyTop?: number;
  className?: string;
  children: ReactNode;
}

export default function RightRail({
  width = 320,
  title,
  stickyTop,
  className = "",
  children,
}: Props) {
  const stickyStyle = stickyTop !== undefined
    ? { position: "sticky" as const, top: stickyTop }
    : undefined;
  return (
    <aside
      style={{ ...(stickyStyle || {}), width: "100%", maxWidth: width }}
      className={`lg:max-w-none ${className}`}
    >
      {title && (
        <div className="mb-2 text-xs uppercase tracking-wider text-slate-400">
          {title}
        </div>
      )}
      <div className="space-y-3">{children}</div>
    </aside>
  );
}
