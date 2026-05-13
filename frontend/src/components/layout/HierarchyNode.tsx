"use client";

/**
 * HierarchyNode
 *
 * Recursive tree node used by the LeftHierarchySidebar. One node can
 * represent a project, a sprint, the "Backlog" pseudo-node, a story, or
 * a test case row. The node's `kind` decides:
 *
 *   - what icon + chevron behavior to show
 *   - how (and whether) to lazy-fetch children on first expand
 *   - the route to navigate to when the label is clicked
 *
 * The node is purposely dumb about the shape of its parent state; the
 * parent (LeftHierarchySidebar) owns the expansion + cached-children
 * maps and passes them down. This keeps the tree's source of truth in
 * one place and makes the auto-expand-to-active-route logic in the
 * sidebar tractable.
 *
 * Indent is rendered via left padding scaled by depth, not nested
 * margins, so the node DOM stays flat-ish and a deeply-nested test
 * case doesn't pile up DOM nesting cost.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

export type HierarchyNodeKind =
  | "project"
  | "sprint"
  | "backlog"
  | "story"
  | "test_case";

export interface HierarchyNodeData {
  /** Stable identifier for keying + active-row matching. For projects
   *  this is the slug; for sprints/stories/cases the UUID. */
  id: string;
  kind: HierarchyNodeKind;
  /** Human-readable label. */
  label: string;
  /** Optional small grey metadata (e.g. "active", "12 cases"). */
  badge?: string;
  /** Route to navigate to when the label is clicked. Optional for
   *  pseudo-nodes like "Backlog" that just hold children. */
  href?: string;
}

interface Props {
  data: HierarchyNodeData;
  depth: number;
  expanded: boolean;
  /** True when this node has at least one child to show (so we render
   *  a chevron). False on leaves and on yet-to-be-fetched parents -- the
   *  sidebar swaps it to true after the lazy fetch resolves. */
  expandable: boolean;
  /** The currently-rendered children. Empty when collapsed OR when the
   *  lazy fetch hasn't happened yet. */
  children?: React.ReactNode;
  onToggle: () => void;
}

const KIND_ICON: Record<HierarchyNodeKind, string> = {
  project: "📁",
  sprint: "🏃",
  backlog: "📥",
  story: "📖",
  test_case: "✓",
};

export default function HierarchyNode({
  data,
  depth,
  expanded,
  expandable,
  children,
  onToggle,
}: Props) {
  const pathname = usePathname();
  // Active when the current URL points at this entity's detail page.
  // We don't try to parse arbitrary nested routes; just the canonical
  // detail-page shape.
  const isActive = data.href ? pathname === data.href : false;

  // Padding is base + per-depth step. Designed so depth 0 (project)
  // has 12px left, then +14px per level. Test cases sit at depth 4 ->
  // ~68px, which leaves enough room for ~20-char titles in a 280px
  // sidebar without truncation.
  const paddingLeft = 12 + depth * 14;

  // Chevron + label wrapped in two separate clickable areas: chevron
  // toggles expansion; label navigates. This matches the Slack/Linear
  // sidebar pattern and is a known-working ergonomic.
  const RowInner = (
    <span
      className={`flex-1 min-w-0 flex items-center gap-1.5 text-[13px] truncate ${
        isActive ? "text-white" : "text-slate-300"
      }`}
    >
      <span className="text-[11px] shrink-0 opacity-70">{KIND_ICON[data.kind]}</span>
      <span className="truncate">{data.label}</span>
      {data.badge && (
        <span className="text-[10px] text-slate-500 shrink-0">{data.badge}</span>
      )}
    </span>
  );

  return (
    <div>
      <div
        className={`flex items-center gap-1 pr-2 py-1 rounded-md transition-colors ${
          isActive ? "bg-purple-500/15 border-l-2 border-purple-400" : "hover:bg-white/5"
        }`}
        style={{ paddingLeft }}
      >
        {/* Chevron column: present on expandable nodes, otherwise an
            empty spacer of equal width so labels stay aligned. */}
        {expandable ? (
          <button
            type="button"
            onClick={onToggle}
            aria-label={expanded ? "Collapse" : "Expand"}
            className="shrink-0 w-4 h-4 flex items-center justify-center text-slate-500 hover:text-white"
          >
            <span className={`inline-block transition-transform ${expanded ? "rotate-90" : ""}`}>
              ▸
            </span>
          </button>
        ) : (
          <span className="shrink-0 w-4" aria-hidden="true" />
        )}

        {/* Label: navigates on click when href is set; otherwise
            (e.g. for the "Backlog" pseudo-node) doubles as the
            expand toggle. */}
        {data.href ? (
          <Link href={data.href} className="flex-1 min-w-0">
            {RowInner}
          </Link>
        ) : (
          <button
            type="button"
            onClick={expandable ? onToggle : undefined}
            className="flex-1 min-w-0 text-left"
          >
            {RowInner}
          </button>
        )}
      </div>

      {/* Children: only rendered when expanded, so a collapsed branch
          contributes zero DOM nodes per descendant. */}
      {expanded && children}
    </div>
  );
}
