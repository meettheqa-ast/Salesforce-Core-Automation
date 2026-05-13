"use client";

/**
 * LeftHierarchySidebar
 *
 * Persistent rail showing the user's tree:
 *
 *   Projects
 *     <project slug>
 *       Sprints
 *         <sprint name>
 *           <story title>
 *             <test case title>
 *       Backlog (no-sprint stories)
 *         <story title>
 *
 * Design notes:
 *
 *   - Mounted once at root layout so every authenticated route shares
 *     the same tree state. Hidden on /login and when not authenticated
 *     (same gate as UserMenu via `useMe`).
 *   - Lazy fetch: only top-level projects load on mount. A node's
 *     children are fetched on first expand AND cached for the session.
 *     Re-collapsing keeps the cache; opening again is instant.
 *   - Refresh: subscribes to `useTreeRefresh` events so newly-created
 *     entities (from any modal anywhere in the app) appear in the tree
 *     without a page reload.
 *   - Auto-expand-to-active-route: when the user deep-links to
 *     `/user-stories/<id>`, walk the parent chain (story -> sprint ->
 *     project) and expand each ancestor so the active row is visible.
 *   - SSR-safe: localStorage is read inside useEffect (post-mount),
 *     not during render. First paint always shows the default-expanded
 *     state to keep server + client HTML identical.
 *   - Width: ~280px expanded, ~52px collapsed. Mobile (<768px) hides
 *     the rail entirely; the navbar's hamburger (added later) opens it
 *     as an overlay drawer.
 *
 * Z-index 40 -- under the navbar (50) and modals (50), but above page
 * content (0). Modals overlap the sidebar at its left edge; that's
 * intentional and matches Slack/Linear behavior.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { useMe } from "@/lib/useMe";
import { useTreeRefresh } from "@/lib/useTreeRefresh";
import { useWorkspaceProject } from "@/lib/useWorkspaceProject";
import HierarchyNode, { type HierarchyNodeData } from "./HierarchyNode";
import QuickCreateMenu from "./QuickCreateMenu";
import { ADMIN_NAV_GROUP, CORE_NAV_GROUPS, isNavItemActive, type NavGroup, type NavItem } from "./navConfig";

// ---------- Types ----------

type SprintRow = {
  id: string;
  name: string;
  state: "planned" | "active" | "completed" | "cancelled";
  project_id: string;
};

type StoryRow = {
  id: string;
  title: string;
  project_id: string;
  sprint_id: string | null;
};

type CaseRow = {
  id: string;
  title: string;
  status: "draft" | "approved" | "rejected";
  stale: boolean;
};

interface ProjectChildren {
  /** Portal UUID resolved from slug. */
  projectId: string;
  sprints: SprintRow[];
  stories: StoryRow[];
}

// ---------- Storage keys ----------

const LS_EXPANDED = "sidebar.expanded";

// Routes where the sidebar should be hidden entirely. /login is an
// auth page; /sfdx and /locators are dev-only utilities that wouldn't
// gain much from a project tree on the side.
const HIDDEN_ROUTES = new Set<string>(["/login"]);

// Mobile drawer event channel. The navbar's hamburger dispatches a
// custom event on `window` and the sidebar listens. Decoupled so the
// navbar doesn't need to import or render the sidebar -- the layout
// already mounts both. Same pattern as the Cmd+K palette's hotkey.
export const SIDEBAR_TOGGLE_EVENT = "ai-qa-portal:toggle-sidebar";

// ---------- Component ----------

export default function LeftHierarchySidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { me } = useMe();
  const { currentProject, projectFromPath, setCurrentProject, currentRole } = useWorkspaceProject(me);

  // Sidebar collapsed/expanded toggle. Default is `true` (expanded) so
  // first-paint matches what the server rendered. The actual stored
  // preference is read post-mount in the effect below; flipping then
  // is fine because the only visual delta is sidebar width.
  const [expanded, setExpanded] = useState(true);
  // Mobile drawer open/closed. Triggered by the navbar's hamburger;
  // the desktop sidebar uses `expanded` instead.
  const [mobileOpen, setMobileOpen] = useState(false);

  // Hydration guard: until we've read localStorage, treat the
  // sidebar as in its default state. After mount, swap to whatever
  // the user prefers.
  useEffect(() => {
    try {
      const v = window.localStorage.getItem(LS_EXPANDED);
      if (v === "false") setExpanded(false);
    } catch {
      // Private browsing / SSR / blocked storage -- silent fallback.
    }
  }, []);
  useEffect(() => {
    try {
      window.localStorage.setItem(LS_EXPANDED, String(expanded));
    } catch {
      /* ignore */
    }
  }, [expanded]);

  // Listen for the mobile hamburger toggle event from the navbar.
  useEffect(() => {
    const handler = () => setMobileOpen((v) => !v);
    window.addEventListener(SIDEBAR_TOGGLE_EVENT, handler);
    return () => window.removeEventListener(SIDEBAR_TOGGLE_EVENT, handler);
  }, []);

  // Auto-close the mobile drawer on route change. Otherwise the drawer
  // would stay open after the user picks a tree node, blocking the
  // page they just navigated to.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Top-level project list (slugs).
  const [projects, setProjects] = useState<string[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);

  // Per-project lazy-loaded children. Keys are project SLUGs; the value
  // carries the resolved portal UUID + cached sprints/stories.
  const [projectChildren, setProjectChildren] = useState<Map<string, ProjectChildren>>(
    () => new Map(),
  );

  // Per-story lazy-loaded test cases. Keys are story UUIDs.
  const [storyCases, setStoryCases] = useState<Map<string, CaseRow[]>>(() => new Map());

  // Expansion state: set of node ids that are currently expanded. Node
  // ids are namespaced strings -- `proj:<slug>`, `sprints:<slug>` (the
  // Sprints group under a project), `backlog:<slug>`, `sprint:<uuid>`,
  // `story:<uuid>`. Module-scoped Sets in React state are intentional
  // (we always create a new Set on update so React notices).
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(() => new Set());
  const [explorerOpen, setExplorerOpen] = useState(false);

  // ---------- Loaders ----------

  const loadProjects = useCallback(() => {
    setProjectsLoading(true);
    api.projects
      .list()
      .then((rows) => setProjects(rows))
      .catch(() => setProjects([]))
      .finally(() => setProjectsLoading(false));
  }, []);

  const loadProjectChildren = useCallback(async (slug: string): Promise<void> => {
    try {
      const reg = await api.projects.portalProjectId(slug);
      const pid = reg.project_id;
      const [sprints, stories] = await Promise.all([
        api.sprints.list(pid).catch(() => [] as SprintRow[]),
        api.userStories.list(pid).catch(() => [] as StoryRow[]),
      ]);
      setProjectChildren((prev) => {
        const next = new Map(prev);
        next.set(slug, {
          projectId: pid,
          sprints: sprints as SprintRow[],
          stories: stories as StoryRow[],
        });
        return next;
      });
    } catch {
      // Per-project failure is noisy in this UI; just leave the node
      // empty and the user can collapse / try again later.
    }
  }, []);

  const loadStoryCases = useCallback(async (storyId: string): Promise<void> => {
    try {
      const rows = await api.testCases.list(storyId);
      setStoryCases((prev) => {
        const next = new Map(prev);
        next.set(storyId, rows as CaseRow[]);
        return next;
      });
    } catch {
      /* ignore */
    }
  }, []);

  // Initial fetch -- only when authenticated. `me` may be null briefly
  // while the bootstrap call is in flight; the sidebar just shows its
  // skeleton until then.
  const isAuthed = !!me;
  useEffect(() => {
    if (!isAuthed) return;
    loadProjects();
  }, [isAuthed, loadProjects]);

  // ---------- Tree refresh subscription ----------
  //
  // Generic "blow away and refetch" strategy: any tree-refresh event
  // invalidates the relevant cached children. The tree re-fetches the
  // affected branch the next time it's visible. This is intentionally
  // coarse-grained -- the tree isn't huge and a wide refetch is cheaper
  // than threading per-event diff logic through the loaders.
  const handleRefresh = useCallback((event: Parameters<Parameters<typeof useTreeRefresh>[0]>[0]) => {
    if (event.kind === "project" || event.kind === "all") {
      loadProjects();
      // A new project means we don't yet have a slug; conservative:
      // clear all per-project caches so they refetch on next expand.
      setProjectChildren(new Map());
      setStoryCases(new Map());
      return;
    }
    // Sprint / story / test_case changes: invalidate caches downstream
    // of the affected project (if known) or all of them (if not).
    if (event.kind === "sprint" || event.kind === "story") {
      setProjectChildren((prev) => {
        if (!event.projectId) return new Map();
        const next = new Map(prev);
        // Find slugs whose cached children point at this project UUID.
        for (const [slug, kids] of prev.entries()) {
          if (kids.projectId === event.projectId) {
            next.delete(slug);
          }
        }
        return next;
      });
      // Also drop story cases since they may have moved sprints.
      setStoryCases(new Map());
      return;
    }
    if (event.kind === "test_case") {
      if (event.storyId) {
        setStoryCases((prev) => {
          const next = new Map(prev);
          next.delete(event.storyId!);
          return next;
        });
      } else {
        setStoryCases(new Map());
      }
    }
  }, [loadProjects]);

  useTreeRefresh(handleRefresh);

  // ---------- Auto-expand-to-active-route ----------
  //
  // When the user deep-links to /user-stories/<id> or /sprints/<id>,
  // walk the parent chain so the active row is visible in the tree.
  // We do this by fetching the entity once and using its IDs to mark
  // ancestors expanded.
  //
  // Note: ``autoExpandFromPath`` / ``findProjectSlugForId`` /
  // ``markExpanded`` are declared further down -- the useEffect that
  // closes over them is hoisted to the bottom of this block so the
  // React 19 compiler rule ``react-hooks/immutability`` (which
  // flags TDZ-style "access before declaration") stays clean.

  // Leaf callbacks first, then ``autoExpandFromPath`` which composes
  // them, then the ``useEffect`` that fires the auto-expand. Order
  // matters: React 19's ``react-hooks/immutability`` rule flags any
  // closure that captures a ``useCallback`` declared below it.

  const markExpanded = useCallback((ids: string[]) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.add(id);
      return next;
    });
  }, []);

  const findProjectSlugForId = useCallback(async (projectId: string): Promise<string | null> => {
    // Check anything we already have cached.
    for (const [slug, kids] of projectChildren.entries()) {
      if (kids.projectId === projectId) return slug;
    }
    // Otherwise, resolve unknown projects on demand. Capped so a
    // typo'd UUID doesn't stampede the backend.
    const list = projects.length > 0 ? projects : await api.projects.list().catch(() => [] as string[]);
    for (const slug of list) {
      try {
        const reg = await api.projects.portalProjectId(slug);
        if (reg.project_id === projectId) return slug;
      } catch {
        /* try next */
      }
    }
    return null;
  }, [projectChildren, projects]);

  const autoExpandFromPath = useCallback(async (path: string): Promise<void> => {
    try {
      const storyMatch = path.match(/^\/user-stories\/([^/]+)$/);
      const sprintMatch = path.match(/^\/sprints\/([^/]+)$/);
      const projectMatch = path.match(/^\/projects\/([^/]+)/);

      if (storyMatch) {
        // Fetch the story to get project_id + sprint_id, then expand
        // project -> (sprint or backlog) -> ensure story is rendered.
        const story = await api.userStories.get(storyMatch[1]);
        const projectId: string = story.project_id;
        const sprintId: string | null = story.sprint_id ?? null;
        // Resolve project_id -> slug. The /api/projects/registry/{slug}
        // endpoint goes slug->id; we don't have id->slug. Cheapest path
        // is to resolve every known project until we hit it. Most users
        // have <10 projects so this is fine.
        const slug = await findProjectSlugForId(projectId);
        if (!slug) return;
        if (!projectChildren.has(slug)) await loadProjectChildren(slug);
        markExpanded([
          `proj:${slug}`,
          sprintId ? `sprints:${slug}` : `backlog:${slug}`,
          ...(sprintId ? [`sprint:${sprintId}`] : []),
        ]);
        return;
      }
      if (sprintMatch) {
        const sprint = await api.sprints.get(sprintMatch[1]);
        const slug = await findProjectSlugForId(sprint.project_id);
        if (!slug) return;
        if (!projectChildren.has(slug)) await loadProjectChildren(slug);
        markExpanded([`proj:${slug}`, `sprints:${slug}`, `sprint:${sprint.id}`]);
        return;
      }
      if (projectMatch) {
        const slug = decodeURIComponent(projectMatch[1]);
        markExpanded([`proj:${slug}`]);
      }
    } catch {
      // Auto-expand is best-effort; failure is silent so a deep-linked
      // user with a 404 entity doesn't see a sidebar error.
    }
  }, [projectChildren, loadProjectChildren, findProjectSlugForId, markExpanded]);

  useEffect(() => {
    if (!isAuthed || !pathname) return;
    void autoExpandFromPath(pathname);
    // Intentionally narrow deps: we only want to re-run on pathname change,
    // not on every projectChildren mutation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthed, pathname]);

  // ---------- Toggle handlers ----------

  const isExpanded = useCallback((id: string) => expandedNodes.has(id), [expandedNodes]);

  const toggle = useCallback((id: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const expandProject = useCallback((slug: string) => {
    if (!projectChildren.has(slug)) {
      void loadProjectChildren(slug);
    }
    toggle(`proj:${slug}`);
  }, [projectChildren, loadProjectChildren, toggle]);

  const expandStory = useCallback((storyId: string) => {
    if (!storyCases.has(storyId)) {
      void loadStoryCases(storyId);
    }
    toggle(`story:${storyId}`);
  }, [storyCases, loadStoryCases, toggle]);

  // ---------- Render ----------

  // Hide entirely on /login + when unauthenticated.
  if (!isAuthed || HIDDEN_ROUTES.has(pathname || "")) return null;

  // The tree body is shared by both the desktop rail and the mobile
  // drawer. Pulling it into a local sub-render avoids duplicating the
  // empty-state + project loop in two places.
  const treeBody = (
    <>
      {projectsLoading && projects.length === 0 && (
        <div className="px-3 py-2 text-[11px] text-slate-500">Loading projects…</div>
      )}
      {!projectsLoading && projects.length === 0 && (
        <div className="px-3 py-2 text-[11px] text-slate-500">
          No projects yet. Use{" "}
          <span className="text-purple-300">Quick create</span> in the sidebar.
        </div>
      )}
      {projects.map((slug) => (
        <ProjectBranch
          key={slug}
          slug={slug}
          kids={projectChildren.get(slug)}
          storyCases={storyCases}
          isExpanded={isExpanded}
          onToggleProject={() => expandProject(slug)}
          onToggle={toggle}
          onExpandStory={expandStory}
        />
      ))}
    </>
  );

  const navGroups: NavGroup[] = CORE_NAV_GROUPS;
  const resolvedProject = currentProject || projectFromPath;
  const globalGroup = navGroups.find((group) => group.id === "global");
  const workspaceGroup = navGroups.find((group) => group.id === "workspace");
  const platformGroup = navGroups.find((group) => group.id === "platform");

  const workspaceItemsBySection: Record<"plan" | "build" | "run" | "operate", NavItem[]> = {
    plan: [],
    build: [],
    run: [],
    operate: [],
  };
  for (const item of workspaceGroup?.items || []) {
    const section = item.section || "operate";
    workspaceItemsBySection[section].push(item);
  }

  const resolveNavHref = (item: NavItem): string | null => {
    if (!item.requiresProject) return item.href;
    if (!resolvedProject) return null;
    return item.href.replace(":project", encodeURIComponent(resolvedProject));
  };

  const renderNavItem = (item: NavItem) => {
    const href = resolveNavHref(item);
    if (item.disabled) {
      return (
        <div
          key={item.href}
          className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-slate-500 border border-transparent cursor-not-allowed"
          title="Coming soon"
        >
          <span className="text-base opacity-70">{item.icon}</span>
          <span>{item.label}</span>
        </div>
      );
    }
    if (!href) return null;
    const active = isNavItemActive(pathname || "", { ...item, href });
    return (
      <Link
        key={item.href}
        href={href}
        className={`relative flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition-colors ${
          active
            ? "bg-purple-500/20 border border-purple-500/30 text-white"
            : "text-slate-300 hover:text-white hover:bg-white/5 border border-transparent"
        }`}
      >
        {active && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-cyan-300" />}
        <span className="text-base">{item.icon}</span>
        <span>{item.label}</span>
      </Link>
    );
  };

  const renderUtilityLink = (href: string, label: string, icon: string) => {
    const active = pathname === href || pathname?.startsWith(`${href}/`);
    return (
      <Link
        href={href}
        className={`relative flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition-colors ${
          active
            ? "bg-purple-500/20 border border-purple-500/30 text-white"
            : "text-slate-300 hover:text-white hover:bg-white/5 border border-transparent"
        }`}
      >
        {active && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-cyan-300" />}
        <span className="text-base">{icon}</span>
        <span>{label}</span>
      </Link>
    );
  };

  const sectionLabel: Record<"plan" | "build" | "run" | "operate", string> = {
    plan: "Plan",
    build: "Build",
    run: "Run",
    operate: "Operate",
  };

  const renderSidebarContent = () => (
    <div className="flex-1 overflow-y-auto px-2 py-2">
      <div className="min-h-full flex flex-col gap-4">
        <div className="space-y-3">
          <div>
            <div className="px-2 mb-1 text-[10px] uppercase tracking-[0.12em] text-slate-500">Global</div>
            <div className="space-y-1">
              {(globalGroup?.items || []).map((item) => renderNavItem(item))}
            </div>
          </div>

          <div className="rounded-lg border border-white/10 bg-white/[0.03] p-2 space-y-2">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Current workspace</p>
            <select
              value={resolvedProject || ""}
              onChange={(e) => {
                const next = e.target.value || null;
                setCurrentProject(next);
                if (next) router.push(`/p/${encodeURIComponent(next)}`);
              }}
              className="w-full rounded-md border border-white/10 bg-slate-900/80 px-2 py-1.5 text-xs text-slate-100 focus:outline-none focus:ring-1 focus:ring-purple-500/70"
            >
              <option value="">Choose project…</option>
              {projects.map((slug) => (
                <option key={slug} value={slug}>
                  {slug}
                </option>
              ))}
            </select>
            {resolvedProject && (
              <div className="inline-flex items-center gap-1 rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-1 text-[10px] uppercase tracking-[0.08em] text-cyan-200">
                <span>Project</span>
                <span className="font-semibold text-cyan-100 normal-case tracking-normal">{resolvedProject}</span>
                {currentRole && (
                  <span className="ml-1 rounded border border-cyan-400/30 bg-cyan-400/15 px-1.5 py-0.5 text-[9px] font-bold text-cyan-100">
                    {currentRole.toUpperCase()}
                  </span>
                )}
              </div>
            )}
          </div>

          <div className="pt-1 border-t border-white/5 space-y-2">
            <div className="px-2 text-[10px] uppercase tracking-[0.12em] text-slate-500">Project modules</div>
            {!resolvedProject ? (
              <div className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-3 text-xs text-slate-400">
                Select a project to access overview, stories, sprints, runs, and integrations.
              </div>
            ) : (
              (Object.keys(workspaceItemsBySection) as Array<keyof typeof workspaceItemsBySection>).map((section) => (
                workspaceItemsBySection[section].length > 0 ? (
                  <div key={section} className="space-y-1">
                    <div className="px-2 text-[10px] uppercase tracking-[0.12em] text-slate-600">{sectionLabel[section]}</div>
                    {workspaceItemsBySection[section].map((item) => renderNavItem(item))}
                  </div>
                ) : null
              ))
            )}
          </div>

          <div className="pt-1 border-t border-white/5">
            <button
              type="button"
              onClick={() => setExplorerOpen((v) => !v)}
              className="w-full flex items-center justify-between rounded-lg px-2.5 py-2 text-sm text-slate-300 hover:text-white hover:bg-white/5 border border-transparent"
            >
              <span className="inline-flex items-center gap-2">
                <span className="text-base">🗂️</span>
                <span>Browse hierarchy</span>
              </span>
              <span className="text-xs text-slate-500">{explorerOpen ? "▾" : "▸"}</span>
            </button>
            {explorerOpen && <div className="mt-2 space-y-1.5">{treeBody}</div>}
          </div>
        </div>

        <div className="mt-auto sticky bottom-0 border-t border-white/10 bg-slate-950/80 backdrop-blur px-1 py-2 space-y-2">
          <QuickCreateMenu
            label="Quick create"
            generateHref={resolvedProject ? `/p/${encodeURIComponent(resolvedProject)}/generate` : "/generate"}
            buttonClassName="w-full px-3 py-2 rounded-lg text-sm font-semibold text-white bg-gradient-to-r from-purple-600 to-cyan-500 hover:brightness-110 flex items-center justify-center gap-1.5"
          />
          <div className="space-y-1">
            {renderUtilityLink("/settings", "Settings", "⚙️")}
            {(platformGroup?.items || [])
              .filter((item) => item.href !== "/settings")
              .map((item) => renderNavItem(item))}
            {me?.is_admin && renderUtilityLink(ADMIN_NAV_GROUP.items[0].href, ADMIN_NAV_GROUP.items[0].label, ADMIN_NAV_GROUP.items[0].icon)}
          </div>
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop rail (md+ only). Two states: collapsed (52px icon-only)
          and expanded (280px tree). Mobile uses the overlay drawer below
          instead of this rail. */}
      {expanded ? (
        <aside
          aria-label="Project hierarchy"
          className="hidden lg:flex shrink-0 w-[308px] flex-col sidebar-surface"
          style={{ zIndex: 40 }}
        >
          <div className="flex items-center justify-between px-3 py-3 border-b border-white/5">
            <span className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">
              Navigation
            </span>
            <button
              type="button"
              onClick={() => setExpanded(false)}
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
              className="p-1 rounded text-slate-500 hover:text-white hover:bg-white/5"
            >
              <span className="text-xs">◂</span>
            </button>
          </div>
          {renderSidebarContent()}
        </aside>
      ) : (
        <aside
          aria-label="Project hierarchy (collapsed)"
          className="hidden lg:flex shrink-0 w-[58px] flex-col sidebar-surface"
          style={{ zIndex: 40 }}
        >
          <button
            type="button"
            onClick={() => setExpanded(true)}
            aria-label="Expand sidebar"
            title="Expand sidebar"
            className="mt-3 mx-auto p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5"
          >
            <span className="text-base">▸</span>
          </button>
        </aside>
      )}

      {/* Mobile drawer (<md). Triggered by the navbar's hamburger.
          Renders as a fixed overlay so it doesn't shove page content
          around. Z-index between sidebar (40) and modals (50) so a
          create modal opened from inside the drawer still sits on top. */}
      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0"
          style={{ zIndex: 45 }}
          role="dialog"
          aria-label="Project hierarchy (mobile)"
        >
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <motion.aside
            initial={{ x: -300 }}
            animate={{ x: 0 }}
            exit={{ x: -300 }}
            className="absolute left-0 top-0 bottom-0 w-[300px] flex flex-col sidebar-surface backdrop-blur-xl"
          >
            <div className="flex items-center justify-between px-3 py-3 border-b border-white/5">
              <span className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">
                Navigation
              </span>
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                aria-label="Close drawer"
                className="p-1 rounded text-slate-400 hover:text-white"
              >
                <span className="text-base">×</span>
              </button>
            </div>
            {renderSidebarContent()}
          </motion.aside>
        </div>
      )}
    </>
  );
}

// ---------- Project branch (recursive subtree) ----------
//
// Split into a separate component so React can short-circuit re-renders
// when a different branch's expansion state changes -- only the affected
// branch updates.

function ProjectBranch({
  slug,
  kids,
  storyCases,
  isExpanded,
  onToggleProject,
  onToggle,
  onExpandStory,
}: {
  slug: string;
  kids: ProjectChildren | undefined;
  storyCases: Map<string, CaseRow[]>;
  isExpanded: (id: string) => boolean;
  onToggleProject: () => void;
  onToggle: (id: string) => void;
  onExpandStory: (storyId: string) => void;
}) {
  const projectExpanded = isExpanded(`proj:${slug}`);
  const sprintsExpanded = isExpanded(`sprints:${slug}`);
  const backlogExpanded = isExpanded(`backlog:${slug}`);

  const node: HierarchyNodeData = {
    id: `proj:${slug}`,
    kind: "project",
    label: slug,
    href: `/p/${encodeURIComponent(slug)}`,
  };

  // Group stories by sprint so each sprint shows its members; orphan
  // stories (sprint_id null) go under the Backlog pseudo-node.
  const grouping = useMemo(() => {
    if (!kids) return null;
    const bySprint = new Map<string, StoryRow[]>();
    const backlog: StoryRow[] = [];
    for (const story of kids.stories) {
      if (story.sprint_id) {
        const arr = bySprint.get(story.sprint_id) ?? [];
        arr.push(story);
        bySprint.set(story.sprint_id, arr);
      } else {
        backlog.push(story);
      }
    }
    return { bySprint, backlog };
  }, [kids]);

  return (
    <HierarchyNode
      data={node}
      depth={0}
      expanded={projectExpanded}
      // Treat as expandable optimistically: until first expand we
      // don't know if it has sprints/stories. Showing a chevron is
      // the right call -- worst case, expanding it shows "(empty)".
      expandable
      onToggle={onToggleProject}
    >
      {kids && grouping ? (
        <>
          {/* Sprints group (only when there's at least one sprint). */}
          {kids.sprints.length > 0 && (
            <HierarchyNode
              data={{
                id: `sprints:${slug}`,
                kind: "sprint",
                label: "Sprints",
                badge: `${kids.sprints.length}`,
              }}
              depth={1}
              expanded={sprintsExpanded}
              expandable
              onToggle={() => onToggle(`sprints:${slug}`)}
            >
              {kids.sprints.map((s) => {
                const sprintExpanded = isExpanded(`sprint:${s.id}`);
                const sprintStories = grouping.bySprint.get(s.id) ?? [];
                return (
                  <HierarchyNode
                    key={s.id}
                    data={{
                      id: `sprint:${s.id}`,
                      kind: "sprint",
                      label: s.name,
                      badge: s.state,
                      href: `/p/${encodeURIComponent(slug)}/sprints/${encodeURIComponent(s.id)}`,
                    }}
                    depth={2}
                    expanded={sprintExpanded}
                    expandable={sprintStories.length > 0}
                    onToggle={() => onToggle(`sprint:${s.id}`)}
                  >
                    {sprintStories.map((story) => (
                      <StoryBranch
                        key={story.id}
                        story={story}
                        projectSlug={slug}
                        depth={3}
                        cases={storyCases.get(story.id)}
                        expanded={isExpanded(`story:${story.id}`)}
                        onExpand={() => onExpandStory(story.id)}
                      />
                    ))}
                  </HierarchyNode>
                );
              })}
            </HierarchyNode>
          )}

          {/* Backlog pseudo-node (only when there's at least one). */}
          {grouping.backlog.length > 0 && (
            <HierarchyNode
              data={{
                id: `backlog:${slug}`,
                kind: "backlog",
                label: "Backlog",
                badge: `${grouping.backlog.length}`,
              }}
              depth={1}
              expanded={backlogExpanded}
              expandable
              onToggle={() => onToggle(`backlog:${slug}`)}
            >
              {grouping.backlog.map((story) => (
                <StoryBranch
                  key={story.id}
                  story={story}
                  projectSlug={slug}
                  depth={2}
                  cases={storyCases.get(story.id)}
                  expanded={isExpanded(`story:${story.id}`)}
                  onExpand={() => onExpandStory(story.id)}
                />
              ))}
            </HierarchyNode>
          )}

          {kids.sprints.length === 0 && grouping.backlog.length === 0 && (
            <div
              className="text-[11px] text-slate-500 italic"
              style={{ paddingLeft: 12 + 1 * 14 + 18 }}
            >
              No sprints or stories yet
            </div>
          )}
        </>
      ) : projectExpanded ? (
        <div
          className="text-[11px] text-slate-500"
          style={{ paddingLeft: 12 + 1 * 14 + 18 }}
        >
          Loading…
        </div>
      ) : null}
    </HierarchyNode>
  );
}

// ---------- Story branch (with optional test cases) ----------

function StoryBranch({
  story,
  projectSlug,
  depth,
  cases,
  expanded,
  onExpand,
}: {
  story: StoryRow;
  projectSlug: string;
  depth: number;
  cases: CaseRow[] | undefined;
  expanded: boolean;
  onExpand: () => void;
}) {
  return (
    <HierarchyNode
      data={{
        id: `story:${story.id}`,
        kind: "story",
        label: story.title,
        badge: cases ? `${cases.length}` : undefined,
        href: `/p/${encodeURIComponent(projectSlug)}/stories/${encodeURIComponent(story.id)}`,
      }}
      depth={depth}
      expanded={expanded}
      expandable
      onToggle={onExpand}
    >
      {cases ? (
        cases.length === 0 ? (
          <div
            className="text-[11px] text-slate-500 italic"
            style={{ paddingLeft: 12 + (depth + 1) * 14 + 18 }}
          >
            No test cases yet
          </div>
        ) : (
          cases.map((tc) => (
            <HierarchyNode
              key={tc.id}
              data={{
                id: `case:${tc.id}`,
                kind: "test_case",
                label: tc.title,
                badge: tc.stale ? "stale" : tc.status,
                href: `/p/${encodeURIComponent(projectSlug)}/stories/${encodeURIComponent(story.id)}`,
              }}
              depth={depth + 1}
              expanded={false}
              expandable={false}
              onToggle={() => undefined}
            />
          ))
        )
      ) : (
        <div
          className="text-[11px] text-slate-500"
          style={{ paddingLeft: 12 + (depth + 1) * 14 + 18 }}
        >
          Loading…
        </div>
      )}
    </HierarchyNode>
  );
}
