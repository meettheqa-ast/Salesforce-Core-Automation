"use client";

/**
 * Shared empty state -- icon (optional), title, description, and an
 * optional primary action. Use on lists / detail pages instead of
 * one-off "No X yet" copy so the layout, spacing, and CTA placement
 * stay consistent.
 */

import Link from "next/link";
import type { ReactNode } from "react";

interface ActionLink {
  label: string;
  href: string;
}

interface ActionButton {
  label: string;
  onClick: () => void;
}

interface Props {
  title: string;
  description?: ReactNode;
  /** Optional emoji or inline icon node rendered above the title. */
  icon?: ReactNode;
  /** Primary CTA -- either a Link (href) or button (onClick). */
  primary?: ActionLink | ActionButton;
  /** Optional secondary action with the same shape as primary. */
  secondary?: ActionLink | ActionButton;
  className?: string;
}

function isLink(action: ActionLink | ActionButton | undefined): action is ActionLink {
  return !!action && "href" in action;
}

export default function EmptyState({
  title,
  description,
  icon,
  primary,
  secondary,
  className = "",
}: Props) {
  return (
    <div
      className={`text-center py-10 px-4 rounded-xl border border-dashed border-white/10 bg-white/[0.02] ${className}`}
      role="status"
    >
      {icon && <div className="text-3xl mb-3 opacity-80">{icon}</div>}
      <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
      {description && (
        <p className="mt-1 text-xs text-slate-400 max-w-md mx-auto">{description}</p>
      )}
      {(primary || secondary) && (
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          {primary && (
            isLink(primary) ? (
              <Link
                href={primary.href}
                className="px-3 py-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-xs font-semibold"
              >
                {primary.label}
              </Link>
            ) : (
              <button
                type="button"
                onClick={primary.onClick}
                className="px-3 py-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-xs font-semibold"
              >
                {primary.label}
              </button>
            )
          )}
          {secondary && (
            isLink(secondary) ? (
              <Link
                href={secondary.href}
                className="px-3 py-1.5 rounded-lg border border-white/10 text-xs text-slate-200 hover:bg-white/5"
              >
                {secondary.label}
              </Link>
            ) : (
              <button
                type="button"
                onClick={secondary.onClick}
                className="px-3 py-1.5 rounded-lg border border-white/10 text-xs text-slate-200 hover:bg-white/5"
              >
                {secondary.label}
              </button>
            )
          )}
        </div>
      )}
    </div>
  );
}
