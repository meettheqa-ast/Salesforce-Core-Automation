"use client";

/**
 * CommandPalette
 *
 * Global Cmd+K / Ctrl+K overlay for fuzzy-navigating to any project,
 * sprint, story, or recent run -- and triggering the four "Create"
 * actions without having to open the navbar's quick-create dropdown.
 *
 * Design:
 *   - Singleton; mounted once at the root layout. Listens for Cmd/Ctrl+K
 *     globally via a `keydown` handler on document.
 *   - Portal-rendered (z-70) so it sits above the navbar (z-50), above
 *     existing modals (also z-50), but below nothing.
 *   - Lazy data fetch on first open: projects + recent runs immediately;
 *     sprints + stories fanned out in parallel only when the user types
 *     >=2 characters (avoids fetching for users who Cmd+K just to dismiss).
 *   - Session-scoped in-memory cache (module-level Map). `useTreeRefresh`
 *     hook (built later) will invalidate the cache so the palette stays
 *     in sync with newly-created entities.
 *   - Keyboard nav: arrows move selection, Enter activates, Esc dismisses.
 *
 * Test cases are intentionally NOT searchable in this first cut: each
 * story carries N cases and fanning out api.testCases.list() per story
 * would balloon cold-start time. Power users can navigate "story X" -> hit
 * Enter -> see cases on the story page. We can add a "Test cases" tab
 * later if usage data shows it's needed.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { useSession } from "next-auth/react";
import { api, type SearchHit } from "@/lib/api";
import { subscribeTreeRefresh } from "@/lib/useTreeRefresh";
import CreateProjectModal from "@/components/projects/CreateProjectModal";
import CreateSprintModal from "@/components/sprints/CreateSprintModal";
import CreateStoryModal from "@/components/user-stories/CreateStoryModal";

// ---------- Result model ----------

type ResultKind = "action" | "project" | "sprint" | "story" | "test_case" | "run";

type Result = {
  kind: ResultKind;
  /** Unique key for React + selection. */
  id: string;
  title: string;
  subtitle?: string;
  /** Either a navigation target OR a click handler -- not both. */
  href?: string;
  onSelect?: () => void;
  /** Pre-lowered haystack for fuzzy match. */
  searchKey: string;
  /** Small icon character. */
  icon: string;
};

// ---------- Module-level session cache ----------
//
// Kept module-scoped because the palette is mounted once at root and
// every open should hit the same cache. Reset by `clearPaletteCache()`.

type ProjectRow = { name: string; display_name?: string };
type SprintRow = { id: string; name: string; project_id: string; state: string };
type StoryRow = { id: string; title: string; project_id: string; sprint_id: string | null };
type RunRow = { run_name: string; passed: number; failed: number; total: number; status: string };

interface PaletteCache {
  projects?: ProjectRow[];
  /** projectName slug -> portal UUID (cached so we don't double-resolve). */
  projectIds: Map<string, string>;
  sprintsByProject: Map<string, SprintRow[]>;
  storiesByProject: Map<string, StoryRow[]>;
  recentRuns?: RunRow[];
  /** True once we've kicked off the deep fetch (sprints + stories per project). */
  deepFetchStarted: boolean;
  /** Server-side search results (Phase 2 IA audit). Keyed by the
   *  query string so a typed sequence "te" -> "tes" doesn't show
   *  the wrong hits while the new request is in flight. */
  serverHits?: SearchHit[];
  serverHitsQuery?: string;
}

const cache: PaletteCache = {
  projectIds: new Map(),
  sprintsByProject: new Map(),
  storiesByProject: new Map(),
  deepFetchStarted: false,
};

/** Drop the cache. Called by useTreeRefresh later so newly created
 *  entities show up in the palette without a page reload. */
export function clearPaletteCache(): void {
  cache.projects = undefined;
  cache.projectIds.clear();
  cache.sprintsByProject.clear();
  cache.storiesByProject.clear();
  cache.serverHits = undefined;
  cache.serverHitsQuery = undefined;
  cache.recentRuns = undefined;
  cache.deepFetchStarted = false;
}

// ---------- Fetch helpers ----------

async function ensureShallow(): Promise<void> {
  // Top-level data: projects + recent runs. Cheap enough for first paint.
  const tasks: Array<Promise<void>> = [];
  if (!cache.projects) {
    tasks.push(
      api.projects
        .list()
        .then((rows) => {
          cache.projects = rows.map((name) => ({ name }));
        })
        .catch(() => {
          cache.projects = [];
        }),
    );
  }
  if (!cache.recentRuns) {
    tasks.push(
      api.runs
        .latest(20)
        .then((d) => {
          cache.recentRuns = d.runs as RunRow[];
        })
        .catch(() => {
          cache.recentRuns = [];
        }),
    );
  }
  await Promise.all(tasks);
}

async function ensureDeep(): Promise<void> {
  // Sprints + stories per project. Fanned out in parallel; capped at
  // 50 projects so a power-user with many projects doesn't melt the
  // backend on first Cmd+K.
  if (cache.deepFetchStarted) return;
  cache.deepFetchStarted = true;
  const projects = (cache.projects ?? []).slice(0, 50);
  await Promise.all(
    projects.map(async (p) => {
      try {
        const reg = await api.projects.portalProjectId(p.name);
        const pid = reg.project_id;
        cache.projectIds.set(p.name, pid);
        const [sprints, stories] = await Promise.all([
          api.sprints.list(pid).catch(() => [] as SprintRow[]),
          api.userStories.list(pid).catch(() => [] as StoryRow[]),
        ]);
        cache.sprintsByProject.set(pid, sprints as SprintRow[]);
        cache.storiesByProject.set(pid, stories as StoryRow[]);
      } catch {
        // Per-project failure should not blow up the entire palette.
      }
    }),
  );
}

// ---------- Component ----------

interface Props {
  /** Optional: hide the global keydown listener (for tests / SSR). */
  disableHotkey?: boolean;
}

const AUTH_DISABLED =
  (process.env.NEXT_PUBLIC_AUTH_DISABLED || "").toLowerCase() === "true";

export default function CommandPalette({ disableHotkey }: Props = {}) {
  const router = useRouter();
  const { status } = useSession();
  const enabled = AUTH_DISABLED || status === "authenticated";
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  // Force re-render once async fetches complete (the cache is module-level
  // so React doesn't track it). `tick` is bumped after each fetch.
  const [, setTick] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Stacked create-modal state. Selecting an "action" result opens one
  // of these. Same modals as the navbar's Quick create + page buttons,
  // so behavior is consistent across all entry points.
  const [showProject, setShowProject] = useState(false);
  const [showSprint, setShowSprint] = useState(false);
  const [showStory, setShowStory] = useState(false);

  // Global hotkey: Cmd+K on Mac, Ctrl+K everywhere else. Intercepts even
  // when focus is in an input -- matches Linear/Notion/Slack expectations.
  useEffect(() => {
    if (!enabled) return;
    if (disableHotkey) return;
    const onKey = (e: KeyboardEvent) => {
      const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isCmdK) {
        e.preventDefault();
        setOpen((v) => !v);
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [disableHotkey, enabled]);

  // Subscribe to tree-refresh events so newly-created entities show up
  // in the palette without a page reload. Module-scoped subscription:
  // we only care about cache invalidation, not React re-renders, so we
  // call clearPaletteCache() rather than touching component state.
  useEffect(() => {
    if (!enabled) return;
    return subscribeTreeRefresh(() => {
      clearPaletteCache();
      // If the palette is currently open, kick off a fresh fetch so the
      // result list reflects the new entity within the same session.
      if (open) {
        void ensureShallow().then(() => setTick((t) => t + 1));
      }
    });
  }, [open, enabled]);

  // On open: focus input, reset query/selection, kick off shallow fetch.
  useEffect(() => {
    if (!enabled) return;
    if (!open) return;
    setQuery("");
    setSelected(0);
    void ensureShallow().then(() => setTick((t) => t + 1));
    // Defer focus to next tick so the input is in the DOM after portal mount.
    const id = window.setTimeout(() => inputRef.current?.focus(), 30);
    return () => window.clearTimeout(id);
  }, [open, enabled]);

  // Once the user types >=2 chars, kick off the deep fetch (sprints +
  // stories) so subsequent keystrokes see richer matches.
  useEffect(() => {
    if (!enabled) return;
    if (!open) return;
    if (query.trim().length < 2) return;
    void ensureDeep().then(() => setTick((t) => t + 1));
  }, [open, query, enabled]);

  // Server-side search (Phase 2 IA audit). Surfaces test cases the
  // client-side cache excluded. Debounced ~200ms; cached per-query so
  // a backspace doesn't re-fetch.
  useEffect(() => {
    if (!enabled || !open) return;
    const q = query.trim();
    if (q.length < 2) {
      cache.serverHits = undefined;
      cache.serverHitsQuery = undefined;
      return;
    }
    if (cache.serverHitsQuery === q) return; // already have these
    const id = setTimeout(async () => {
      try {
        const r = await api.search(q, 8);
        if (cache.serverHitsQuery === q) return; // a newer query took over
        cache.serverHits = r.hits;
        cache.serverHitsQuery = q;
        setTick((t) => t + 1);
      } catch {
        // Search failures fall back to the local cache silently.
      }
    }, 200);
    return () => clearTimeout(id);
  }, [open, query, enabled]);

  // ---------- Result composition ----------

  const results = useMemo<Result[]>(() => buildResults(query, {
    onCreateProject: () => setShowProject(true),
    onCreateSprint: () => setShowSprint(true),
    onCreateStory: () => setShowStory(true),
  }), [query]);

  // Reset selection when result list changes shape, but only if the new
  // list is shorter than current `selected` (avoid scrolling to top on
  // every keystroke).
  useEffect(() => {
    if (selected >= results.length) setSelected(0);
  }, [results.length, selected]);

  // Scroll the selected row into view as keyboard nav moves it.
  useEffect(() => {
    if (!listRef.current) return;
    const node = listRef.current.querySelector(
      `[data-cmd-idx="${selected}"]`,
    ) as HTMLElement | null;
    node?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const activate = useCallback((r: Result) => {
    setOpen(false);
    if (r.href) {
      router.push(r.href);
    } else if (r.onSelect) {
      // Defer one tick so the palette unmount doesn't race with the
      // stacked modal's open animation.
      window.setTimeout(() => r.onSelect?.(), 0);
    }
  }, [router]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((s) => Math.min(s + 1, Math.max(results.length - 1, 0)));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const r = results[selected];
      if (r) activate(r);
      return;
    }
  };

  // ---------- Render ----------

  if (!enabled) return null;

  return (
    <>
      {typeof document !== "undefined" &&
        createPortal(
          <AnimatePresence>
            {open && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.12 }}
                style={{ zIndex: 70 }}
                className="fixed inset-0 flex items-start justify-center bg-black/60 backdrop-blur-sm pt-[12vh] px-4"
                onClick={() => setOpen(false)}
              >
                <motion.div
                  initial={{ y: -8, opacity: 0, scale: 0.98 }}
                  animate={{ y: 0, opacity: 1, scale: 1 }}
                  exit={{ y: -8, opacity: 0, scale: 0.98 }}
                  transition={{ duration: 0.14 }}
                  onClick={(e) => e.stopPropagation()}
                  role="dialog"
                  aria-label="Command palette"
                  className="w-full max-w-xl rounded-xl border border-white/10 bg-slate-900/95 backdrop-blur-xl shadow-2xl overflow-hidden"
                >
                  <div className="flex items-center gap-2 px-4 py-3 border-b border-white/5">
                    <span className="text-slate-500 text-base">⌕</span>
                    <input
                      ref={inputRef}
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      onKeyDown={onKeyDown}
                      placeholder="Search projects, sprints, stories, runs… or 'create project'"
                      className="flex-1 bg-transparent text-sm text-slate-100 placeholder:text-slate-500 outline-none"
                      autoComplete="off"
                      spellCheck={false}
                    />
                    <kbd className="hidden sm:inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono text-slate-500 border border-white/10">
                      Esc
                    </kbd>
                  </div>

                  <div ref={listRef} className="max-h-[60vh] overflow-y-auto py-1">
                    {results.length === 0 ? (
                      <div className="px-4 py-8 text-center text-sm text-slate-500">
                        Nothing matches <span className="text-slate-300">{query}</span>.
                      </div>
                    ) : (
                      <PaletteList
                        results={results}
                        selectedIdx={selected}
                        onHover={setSelected}
                        onActivate={activate}
                      />
                    )}
                  </div>

                  <div className="flex items-center justify-between px-4 py-2 border-t border-white/5 text-[10px] text-slate-500">
                    <span>
                      <kbd className="px-1 py-0.5 rounded border border-white/10 font-mono">↑</kbd>{" "}
                      <kbd className="px-1 py-0.5 rounded border border-white/10 font-mono">↓</kbd>{" "}
                      to navigate
                    </span>
                    <span>
                      <kbd className="px-1 py-0.5 rounded border border-white/10 font-mono">↵</kbd>{" "}
                      to select
                    </span>
                    <span>
                      <kbd className="px-1 py-0.5 rounded border border-white/10 font-mono">⌘</kbd>{" "}
                      <kbd className="px-1 py-0.5 rounded border border-white/10 font-mono">K</kbd>{" "}
                      to toggle
                    </span>
                  </div>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>,
          document.body,
        )}

      {/* Stacked create modals -- same components as the navbar Quick create
          + every page-level + button uses, so behavior is consistent. */}
      <CreateProjectModal
        open={showProject}
        onClose={() => setShowProject(false)}
        onCreated={() => clearPaletteCache()}
      />
      <CreateSprintModal
        open={showSprint}
        onClose={() => setShowSprint(false)}
        onCreated={() => clearPaletteCache()}
      />
      <CreateStoryModal
        open={showStory}
        onClose={() => setShowStory(false)}
        onCreated={() => clearPaletteCache()}
      />
    </>
  );
}

// ---------- Result list (memo'd row renderer) ----------

function PaletteList({
  results,
  selectedIdx,
  onHover,
  onActivate,
}: {
  results: Result[];
  selectedIdx: number;
  onHover: (i: number) => void;
  onActivate: (r: Result) => void;
}) {
  // Group consecutive results of the same kind under a section heading.
  const sections: Array<{ kind: ResultKind; label: string; items: Array<Result & { idx: number }> }> = [];
  results.forEach((r, idx) => {
    const last = sections[sections.length - 1];
    if (last && last.kind === r.kind) {
      last.items.push({ ...r, idx });
    } else {
      sections.push({
        kind: r.kind,
        label: SECTION_LABELS[r.kind],
        items: [{ ...r, idx }],
      });
    }
  });

  return (
    <>
      {sections.map((sec) => (
        <div key={`${sec.kind}-${sec.items[0].id}`}>
          <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
            {sec.label}
          </div>
          {sec.items.map((r) => {
            const isSel = r.idx === selectedIdx;
            return (
              <button
                key={r.id}
                type="button"
                data-cmd-idx={r.idx}
                onMouseEnter={() => onHover(r.idx)}
                onClick={() => onActivate(r)}
                className={`w-full text-left px-3 py-2 flex items-center gap-3 transition-colors ${
                  isSel ? "bg-purple-500/15 text-white" : "text-slate-200 hover:bg-white/5"
                }`}
              >
                <span className="text-base shrink-0">{r.icon}</span>
                <span className="flex-1 min-w-0">
                  <span className="block text-sm truncate">{r.title}</span>
                  {r.subtitle && (
                    <span className="block text-[11px] text-slate-500 truncate">{r.subtitle}</span>
                  )}
                </span>
                {isSel && <span className="text-[10px] text-slate-500">↵</span>}
              </button>
            );
          })}
        </div>
      ))}
    </>
  );
}

const SECTION_LABELS: Record<ResultKind, string> = {
  action: "Actions",
  project: "Projects",
  sprint: "Sprints",
  story: "Stories",
  run: "Recent runs",
};

// ---------- Result builder + fuzzy match ----------

function buildResults(
  rawQuery: string,
  actions: {
    onCreateProject: () => void;
    onCreateSprint: () => void;
    onCreateStory: () => void;
  },
): Result[] {
  const q = rawQuery.trim().toLowerCase();

  // Always-on action items: shown at the top when query is empty,
  // mixed in by score when query is non-empty.
  const allActions: Result[] = [
    {
      kind: "action",
      id: "act:create-project",
      title: "Create new project",
      subtitle: "Open the new-project modal",
      icon: "📂",
      onSelect: actions.onCreateProject,
      searchKey: "create new project make add",
    },
    {
      kind: "action",
      id: "act:create-sprint",
      title: "Create new sprint",
      subtitle: "Open the new-sprint modal",
      icon: "🏃",
      onSelect: actions.onCreateSprint,
      searchKey: "create new sprint make add",
    },
    {
      kind: "action",
      id: "act:create-story",
      title: "Create new story",
      subtitle: "Open the new-story modal",
      icon: "📖",
      onSelect: actions.onCreateStory,
      searchKey: "create new story make add user story",
    },
    {
      kind: "action",
      id: "act:generate-test",
      title: "Generate test (AI)",
      subtitle: "Go to /generate",
      icon: "🧪",
      href: "/generate",
      searchKey: "generate test ai run new test",
    },
  ];

  // Project / Sprint / Story / Run results sourced from the cache.
  const proj = cache.projects ?? [];
  const projectResults: Result[] = proj.map((p) => ({
    kind: "project",
    id: `proj:${p.name}`,
    title: p.display_name || p.name,
    subtitle: "Project",
    icon: "📁",
    href: `/projects/${encodeURIComponent(p.name)}`,
    searchKey: `${p.name} ${p.display_name ?? ""}`.toLowerCase(),
  }));

  // Sprint + story results require the deep fetch. If not yet loaded,
  // these arrays are empty.
  const sprintResults: Result[] = [];
  const storyResults: Result[] = [];
  // Build a lookup so we can label sprints/stories with their project.
  const projectNameById = new Map<string, string>();
  for (const [name, id] of cache.projectIds.entries()) {
    projectNameById.set(id, name);
  }

  for (const [pid, sprints] of cache.sprintsByProject.entries()) {
    const projName = projectNameById.get(pid) ?? "";
    for (const s of sprints) {
      sprintResults.push({
        kind: "sprint",
        id: `sprint:${s.id}`,
        title: s.name,
        subtitle: `${projName} · ${s.state}`,
        icon: "🏃",
        href: `/sprints/${encodeURIComponent(s.id)}`,
        searchKey: `${s.name} ${projName} ${s.state}`.toLowerCase(),
      });
    }
  }

  for (const [pid, stories] of cache.storiesByProject.entries()) {
    const projName = projectNameById.get(pid) ?? "";
    for (const s of stories) {
      storyResults.push({
        kind: "story",
        id: `story:${s.id}`,
        title: s.title,
        subtitle: `${projName} · ${s.sprint_id ? "in sprint" : "backlog"}`,
        icon: "📖",
        href: `/user-stories/${encodeURIComponent(s.id)}`,
        searchKey: `${s.title} ${projName}`.toLowerCase(),
      });
    }
  }

  const runs = cache.recentRuns ?? [];
  const runResults: Result[] = runs.map((r) => ({
    kind: "run",
    id: `run:${r.run_name}`,
    title: r.run_name,
    subtitle: `${r.passed}/${r.total} passed · ${r.status}`,
    icon: r.status === "PASS" ? "✅" : r.status === "FAIL" ? "❌" : "⏺",
    href: `/runs/${encodeURIComponent(r.run_name)}`,
    searchKey: `${r.run_name} ${r.status}`.toLowerCase(),
  }));

  // Empty-query layout: actions on top, then projects, then last 5 runs
  // (skip sprints/stories so the cold list isn't overwhelming).
  if (!q) {
    return [
      ...allActions,
      ...projectResults,
      ...runResults.slice(0, 5),
    ];
  }

  // Score = position of first substring match (lower = better).
  // Equal scores fall back to result-kind order (actions > project > sprint > story > test_case > run).
  const kindOrder: Record<ResultKind, number> = {
    action: 0,
    project: 1,
    sprint: 2,
    story: 3,
    test_case: 4,
    run: 5,
  };

  // Server-side hits (Phase 2 IA audit). These surface test cases
  // the client-side cache excludes + extend project / sprint / story
  // coverage past the 50-project deep-fetch cap. Deduped against the
  // local hits by id (server kind -> local result id format).
  const serverResults: Result[] = [];
  if (cache.serverHits && cache.serverHitsQuery === query.trim().toLowerCase()) {
    for (const hit of cache.serverHits) {
      const idPrefix: Record<SearchHit["kind"], string> = {
        project: "proj:",
        sprint: "sprint:",
        story: "story:",
        test_case: "tc:",
        run: "run:",
      };
      const icon: Record<SearchHit["kind"], string> = {
        project: "📁",
        sprint: "🏃",
        story: "📖",
        test_case: "🧪",
        run: "⏺",
      };
      serverResults.push({
        kind: hit.kind === "test_case" ? "test_case" : hit.kind,
        id: `${idPrefix[hit.kind]}${hit.id}`,
        title: hit.title,
        subtitle: hit.subtitle || undefined,
        icon: icon[hit.kind],
        href: hit.url,
        searchKey: `${hit.title} ${hit.subtitle || ""}`.toLowerCase(),
      });
    }
  }

  const all: Result[] = [
    ...allActions,
    ...projectResults,
    ...sprintResults,
    ...storyResults,
    ...serverResults,
    ...runResults,
  ];
  // Dedupe by id so a result that exists in both local cache + server
  // hits doesn't render twice.
  const seenIds = new Set<string>();
  const unique: Result[] = [];
  for (const r of all) {
    if (seenIds.has(r.id)) continue;
    seenIds.add(r.id);
    unique.push(r);
  }
  const scored: Array<{ r: Result; score: number }> = [];
  for (const r of unique) {
    const idx = r.searchKey.indexOf(q);
    if (idx >= 0) {
      scored.push({ r, score: idx + kindOrder[r.kind] * 0.001 });
    }
  }
  scored.sort((a, b) => a.score - b.score);
  // Cap the result list at 50 -- past that, refine the query.
  return scored.slice(0, 50).map((s) => s.r);
}
