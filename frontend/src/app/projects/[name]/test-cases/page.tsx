"use client";

/**
 * First-class project-scoped Test Cases list page (Phase 2 IA audit).
 *
 * Before this page existed, test cases were only reachable through
 * the project home's TC panel (grouped by story) or the per-story
 * detail page. They were excluded from the command palette by design
 * and had no dedicated URL. That meant users couldn't:
 *   - bookmark "all approved smoke TCs for project X"
 *   - filter by tag without scrolling project home
 *   - bulk-select cases across multiple stories
 *
 * This page renders all TCs for a project as a flat table with
 * status/tag/script filters + per-row link to detail. Bulk selection
 * + bulk actions reuse the same SelectionToolbar + ConfirmDeleteModal
 * stack used on the story detail and project home panels.
 */

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api, parseDeleteBlockersError, type DeleteBlocker } from "@/lib/api";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";
import AnimatedCard from "@/components/cards/AnimatedCard";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";
import EmptyState from "@/components/feedback/EmptyState";
import StatusPill from "@/components/data/StatusPill";
import MetricTile from "@/components/data/MetricTile";
import { usePersistedState } from "@/hooks/usePersistedState";
import SelectionToolbar, { type SelectionToolbarMode } from "@/components/lists/SelectionToolbar";
import ConfirmDeleteModal, { type ConfirmDeleteMode } from "@/components/lists/ConfirmDeleteModal";

type TCStatus = "draft" | "approved" | "rejected";

interface TCRow {
  id: string;
  title: string;
  status: TCStatus;
  stale: boolean;
  tags: string[];
  script_path: string | null;
  script_built_at: string | null;
  heal_attempts: number;
}

interface StoryGroup {
  id: string;
  title: string;
  version: number;
  test_cases: TCRow[];
}

type StatusFilter = "all" | TCStatus | "stale";
type ScriptFilter = "all" | "with_script" | "without_script";

export default function ProjectTestCasesPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);
  const [stories, setStories] = useState<StoryGroup[] | null>(null);
  const [byStatus, setByStatus] = useState<{ approved: number; draft: number; rejected: number; stale: number } | null>(null);
  const [err, setErr] = useState<unknown>(null);

  // Persisted filters so the user's last view sticks across navigations.
  const [statusFilter, setStatusFilter] = usePersistedState<StatusFilter>(
    "tc.list.statusFilter",
    "all",
  );
  const [scriptFilter, setScriptFilter] = usePersistedState<ScriptFilter>(
    "tc.list.scriptFilter",
    "all",
  );
  const [tagFilter, setTagFilter] = usePersistedState<string>("tc.list.tagFilter", "");
  const [storyFilter, setStoryFilter] = useState<string>("");
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = usePersistedState<boolean>(
    "tc.list.showArchived",
    false,
  );

  // Bulk selection.
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmMode, setConfirmMode] = useState<ConfirmDeleteMode>("soft");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const r = await api.projects.testCases(name);
      setStories(r.stories as StoryGroup[]);
      setByStatus({
        approved: r.by_status.approved,
        draft: r.by_status.draft,
        rejected: r.by_status.rejected,
        stale: r.by_status.stale ?? 0,
      });
    } catch (e) {
      setErr(e);
    }
  }

  useEffect(() => {
    setStories(null);
    setErr(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  // Flatten + filter. Story group is preserved as a subtitle on each
  // row so the user can see which story owns a case at a glance.
  const flat = useMemo(() => {
    const out: Array<TCRow & { story_id: string; story_title: string }> = [];
    for (const s of stories || []) {
      for (const tc of s.test_cases) {
        out.push({ ...tc, story_id: s.id, story_title: s.title });
      }
    }
    return out;
  }, [stories]);

  const allTags = useMemo(() => {
    const t = new Set<string>();
    for (const r of flat) r.tags.forEach((x) => t.add(x));
    return Array.from(t).sort();
  }, [flat]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return flat.filter((r) => {
      if (!showArchived && r.status === "rejected") return false;
      if (statusFilter === "stale" && !r.stale) return false;
      if (statusFilter !== "all" && statusFilter !== "stale" && r.status !== statusFilter) return false;
      if (scriptFilter === "with_script" && !r.script_path) return false;
      if (scriptFilter === "without_script" && r.script_path) return false;
      if (tagFilter && !r.tags.includes(tagFilter)) return false;
      if (storyFilter && r.story_id !== storyFilter) return false;
      if (needle && !r.title.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [flat, statusFilter, scriptFilter, tagFilter, storyFilter, search, showArchived]);

  const visibleSet = useMemo(() => new Set(visible.map((r) => r.id)), [visible]);
  const selectedRows = useMemo(
    () => visible.filter((r) => selected.includes(r.id)),
    [visible, selected],
  );

  const toolbarMode: SelectionToolbarMode = useMemo(() => {
    if (selectedRows.length === 0) return "soft";
    const allRejected = selectedRows.every((r) => r.status === "rejected");
    const noneRejected = selectedRows.every((r) => r.status !== "rejected");
    return allRejected ? "permanent" : noneRejected ? "soft" : "mixed";
  }, [selectedRows]);

  function toggleOne(id: string) {
    setSelected((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  }
  function toggleAll() {
    if (selected.length === visible.length) {
      setSelected([]);
    } else {
      setSelected(visible.map((r) => r.id));
    }
  }

  async function handleConfirm() {
    if (selected.length === 0) return { ok: false, message: "Nothing selected" } as const;
    setBusy(true);
    try {
      const permanent = confirmMode === "permanent";
      const eligible = selected.filter((id) => {
        const r = flat.find((x) => x.id === id);
        if (!r) return false;
        return permanent ? r.status === "rejected" : r.status !== "rejected";
      });
      if (eligible.length === 0) {
        return { ok: false, message: "No eligible test cases for this action." } as const;
      }
      await api.testCases.bulkDelete(eligible, permanent);
      setSelected([]);
      await load();
      return { ok: true } as const;
    } catch (e: unknown) {
      const parsed = parseDeleteBlockersError(e);
      if (parsed) return { ok: false, blockers: parsed.blockers as DeleteBlocker[], message: parsed.detail } as const;
      return { ok: false, message: e instanceof Error ? e.message : "Bulk delete failed" } as const;
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Project"
          title="Test cases"
          description="Flat list of every test case in the project. Filter by status, script, tag, or story."
        />
        <p className="text-xs text-slate-500 mt-2">
          <Link href={`/projects/${encodeURIComponent(name)}`} className="hover:text-slate-300">
            ← Back to project home
          </Link>
        </p>
      </motion.div>

      <ErrorBanner error={err} onDismiss={() => setErr(null)} />

      {byStatus && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <MetricTile label="Approved" value={byStatus.approved} tone="success" fluid />
          <MetricTile label="Draft" value={byStatus.draft} tone="info" fluid />
          <MetricTile label="Stale" value={byStatus.stale} tone="warning" fluid />
          <MetricTile label="Archived" value={byStatus.rejected} tone="muted" fluid />
        </div>
      )}

      <AnimatedCard glow="purple">
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search titles…"
            className="bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 outline-none focus:border-purple-500 min-w-[200px]"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            className="bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-200"
          >
            <option value="all">All statuses</option>
            <option value="approved">Approved</option>
            <option value="draft">Draft</option>
            <option value="rejected">Archived</option>
            <option value="stale">Stale</option>
          </select>
          <select
            value={scriptFilter}
            onChange={(e) => setScriptFilter(e.target.value as ScriptFilter)}
            className="bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-200"
          >
            <option value="all">All scripts</option>
            <option value="with_script">With script</option>
            <option value="without_script">No script</option>
          </select>
          <select
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value)}
            className="bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-200"
          >
            <option value="">All tags</option>
            {allTags.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select
            value={storyFilter}
            onChange={(e) => setStoryFilter(e.target.value)}
            className="bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-200"
          >
            <option value="">All stories</option>
            {(stories || []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
          <label className="inline-flex items-center gap-1.5 text-[11px] text-slate-400 ml-auto">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
              className="accent-amber-500"
            />
            Show archived
          </label>
        </div>

        {selectedRows.length > 0 && (
          <SelectionToolbar
            count={selectedRows.length}
            entityNoun="test case"
            mode={toolbarMode}
            busy={busy}
            softLabel="Archive"
            onSoftDelete={
              toolbarMode === "soft" || toolbarMode === "mixed"
                ? () => {
                    setConfirmMode("soft");
                    setConfirmOpen(true);
                  }
                : undefined
            }
            onHardDelete={
              toolbarMode === "permanent" || toolbarMode === "mixed"
                ? () => {
                    setConfirmMode("permanent");
                    setConfirmOpen(true);
                  }
                : undefined
            }
            onClear={() => setSelected([])}
          />
        )}

        {stories === null ? (
          <LoadingState variant="skeleton" rows={6} />
        ) : visible.length === 0 ? (
          <EmptyState
            title="No matching test cases"
            description="Adjust the filters above, or jump to the project home to create one."
            primary={{
              label: "Open project home",
              href: `/projects/${encodeURIComponent(name)}`,
            }}
          />
        ) : (
          <div className="overflow-x-auto rounded-lg border border-white/10">
            <table className="w-full text-xs">
              <thead className="bg-slate-900/60 text-slate-400">
                <tr>
                  <th className="px-2 py-2 text-left w-8">
                    <input
                      type="checkbox"
                      aria-label="Select all"
                      checked={visible.length > 0 && selected.length === visible.length}
                      onChange={toggleAll}
                      className="accent-amber-500"
                    />
                  </th>
                  <th className="px-2 py-2 text-left">Title</th>
                  <th className="px-2 py-2 text-left">Story</th>
                  <th className="px-2 py-2 text-left">Status</th>
                  <th className="px-2 py-2 text-left">Script</th>
                  <th className="px-2 py-2 text-left">Tags</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => (
                  <tr
                    key={r.id}
                    className="border-t border-white/5 hover:bg-white/[0.02]"
                  >
                    <td className="px-2 py-2">
                      <input
                        type="checkbox"
                        aria-label={`Select ${r.title}`}
                        checked={selected.includes(r.id)}
                        onChange={() => toggleOne(r.id)}
                        className="accent-amber-500"
                      />
                    </td>
                    <td className="px-2 py-2 text-slate-200 min-w-[220px]">
                      <Link
                        href={`/test-cases/${encodeURIComponent(r.id)}?project=${encodeURIComponent(name)}`}
                        className="hover:text-cyan-200 break-words"
                      >
                        {r.title}
                      </Link>
                    </td>
                    <td className="px-2 py-2 text-slate-500">
                      <Link
                        href={`/user-stories/${encodeURIComponent(r.story_id)}?project=${encodeURIComponent(name)}`}
                        className="hover:text-cyan-200"
                      >
                        {r.story_title}
                      </Link>
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex items-center gap-1">
                        <StatusPill
                          status={r.status}
                          label={r.status === "rejected" ? "archived" : r.status}
                        />
                        {r.stale && <StatusPill tone="warning" label="stale" />}
                      </div>
                    </td>
                    <td className="px-2 py-2 text-[11px]">
                      {r.script_path ? (
                        <span className="text-cyan-300">ready</span>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-2 py-2 text-[11px] text-purple-200">
                      {r.tags.join(", ") || <span className="text-slate-600">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-[11px] text-slate-500">
          {visible.length} of {flat.length} shown · {visibleSet.size} unique
        </p>
      </AnimatedCard>

      <ConfirmDeleteModal
        open={confirmOpen}
        entityNoun="test case"
        targetLabels={selected
          .map((id) => flat.find((r) => r.id === id)?.title || id)
          .slice(0, 5)
          .concat(selected.length > 5 ? [`...and ${selected.length - 5} more`] : [])}
        mode={confirmMode}
        onConfirm={handleConfirm}
        onClose={() => setConfirmOpen(false)}
      />
    </PageScaffold>
  );
}
