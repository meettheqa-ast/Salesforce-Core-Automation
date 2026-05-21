"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { useSearchParams } from "next/navigation";
import { api, parseDeleteBlockersError, type BulkDeleteRowResult } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";
import AnimatedCard from "@/components/cards/AnimatedCard";
import CreateStoryModal from "@/components/user-stories/CreateStoryModal";
import SelectionToolbar from "@/components/lists/SelectionToolbar";
import ConfirmDeleteModal, { type ConfirmDeleteMode, type DeleteBlocker } from "@/components/lists/ConfirmDeleteModal";
import { notifyTreeRefresh } from "@/lib/useTreeRefresh";
import { useToast } from "@/components/ui/ToastProvider";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

export default function UserStoriesPage() {
  const searchParams = useSearchParams();
  // Honor `?project=<slug>` deep links (e.g. from /sprints' Backlog
  // card). Without this the picker ignored the query string and the
  // user ended up on a blank page despite the URL claiming a project.
  const initialProject = searchParams.get("project") || "";

  const [projects, setProjects] = useState<string[]>([]);
  const [projectName, setProjectName] = useState(initialProject);
  const [projectId, setProjectId] = useState("");
  type StoryRow = { id: string; title: string; version: number; status: string; sprint_id: string | null };
  const [stories, setStories] = useState<StoryRow[]>([]);
  const [sprints, setSprints] = useState<Array<{ id: string; name: string; state: string }>>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [targetSprintId, setTargetSprintId] = useState("");
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkMsg, setBulkMsg] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmTargets, setConfirmTargets] = useState<StoryRow[]>([]);
  const toast = useToast();

  useEffect(() => {
    api.projects.list().then(setProjects).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!projectName) {
      void Promise.resolve().then(() => {
        setProjectId("");
        setStories([]);
        setSprints([]);
        setSelectedIds([]);
      });
      return;
    }
    api.projects.portalProjectId(projectName).then((r) => {
      setProjectId(r.project_id);
      return Promise.all([
        api.userStories.list(r.project_id),
        api.sprints.list(r.project_id).catch(() => []),
      ]);
    }).then(([storyRows, sprintRows]) => {
      setStories(storyRows as StoryRow[]);
      setSprints((sprintRows as any[]).map((s) => ({ id: s.id, name: s.name, state: s.state })));
    }).catch(() => {
      setStories([]);
      setSprints([]);
    });
  }, [projectName]);

  const reloadStories = () => {
    if (!projectId) return;
    Promise.all([
      api.userStories.list(projectId).catch(() => [] as StoryRow[]),
      api.sprints.list(projectId).catch(() => [] as any[]),
    ]).then(([storyRows, sprintRows]) => {
      setStories(storyRows as StoryRow[]);
      setSprints((sprintRows as any[]).map((s) => ({ id: s.id, name: s.name, state: s.state })));
    }).catch(() => {});
  };

  useEffect(() => {
    setSelectedIds((prev) => prev.filter((id) => stories.some((s) => s.id === id)));
  }, [stories]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || (e.target as HTMLElement | null)?.isContentEditable) return;
      if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        setShowCreate(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const allSelected = stories.length > 0 && selectedIds.length === stories.length;
  const storyById = useMemo(
    () => new Map(stories.map((s) => [s.id, s])),
    [stories],
  );

  const toggleSelectAll = () => {
    if (allSelected) setSelectedIds([]);
    else setSelectedIds(stories.map((s) => s.id));
  };

  const toggleStory = (id: string) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  // The /user-stories list only shows active stories (the backend
  // filters out archived rows), so the toolbar mode here is always
  // "soft" -- the first click archives. To permanently delete an
  // already-archived story, the user has to use a project-detail
  // surface where archived rows are visible (out of scope for this
  // pass; see plan section "Out of scope").
  const selectedStories = useMemo(
    () => stories.filter((s) => selectedIds.includes(s.id)),
    [stories, selectedIds],
  );

  const openDeleteModal = (targets: StoryRow[]) => {
    if (targets.length === 0) return;
    setConfirmTargets(targets);
    setConfirmOpen(true);
  };

  const handleConfirmDelete = async (): Promise<{ ok: boolean; blockers?: DeleteBlocker[]; message?: string }> => {
    const ids = confirmTargets.map((t) => t.id);
    try {
      if (ids.length === 1) {
        await api.userStories.delete(ids[0], false);
      } else {
        const res = await api.userStories.bulkDelete(ids, false);
        const blocked = res.results.filter((r: BulkDeleteRowResult) => r.status === "skipped_blocked");
        if (blocked.length > 0) {
          // Soft delete shouldn't trip blocker checks (those are hard-only),
          // but surface the message if the backend ever changes.
          return {
            ok: false,
            blockers: blocked.flatMap((r) => r.blockers || []),
            message: `${blocked.length} stor${blocked.length === 1 ? "y" : "ies"} could not be archived.`,
          };
        }
      }
      toast.success(`${ids.length} stor${ids.length === 1 ? "y" : "ies"} archived.`);
      setSelectedIds([]);
      reloadStories();
      notifyTreeRefresh({ kind: "story", projectId: projectId || undefined });
      return { ok: true };
    } catch (err: unknown) {
      const parsed = parseDeleteBlockersError(err);
      if (parsed) {
        return { ok: false, blockers: parsed.blockers, message: parsed.detail };
      }
      return { ok: false, message: err instanceof Error ? err.message : "Archive failed" };
    }
  };

  const bulkMove = async (destSprintId: string | null) => {
    if (selectedIds.length === 0) return;
    setBulkBusy(true);
    setBulkMsg("");
    let moved = 0;
    let failed = 0;
    await Promise.all(
      selectedIds.map(async (storyId) => {
        const story = storyById.get(storyId);
        if (!story) return;
        try {
          if (story.sprint_id && story.sprint_id !== destSprintId) {
            await api.sprints.unassignStory(story.sprint_id, story.id);
          }
          if (destSprintId && story.sprint_id !== destSprintId) {
            await api.sprints.assignStory(destSprintId, story.id);
          }
          moved += 1;
        } catch {
          failed += 1;
        }
      }),
    );
    setBulkBusy(false);
    setSelectedIds([]);
    setTargetSprintId("");
    setBulkMsg(failed > 0 ? `Moved ${moved}; ${failed} failed.` : `Moved ${moved} stor${moved === 1 ? "y" : "ies"}.`);
    reloadStories();
    notifyTreeRefresh({ kind: "story", projectId: projectId || undefined });
  };

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Delivery"
          title="User Stories"
          description="Author stories, generate test cases, and move approved coverage into execution."
          actions={
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={() => setShowCreate(true)}
              className="px-5 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm"
            >
              + New story
            </motion.button>
          }
        />
      </motion.div>

      <div className="grid md:grid-cols-2 gap-6 mb-10">
        <AnimatedCard glow="purple" className="p-5">
          <h2 className="text-lg font-semibold text-white mb-4">Browse</h2>
          <div className="space-y-3">
            <GlassSelect
              className="w-full"
              value={projectName}
              placeholder="Project…"
              onChange={setProjectName}
              options={[{ value: "", label: "Select project…" }, ...projects.map((p) => ({ value: p, label: p }))]}
            />
            <p className="text-xs text-slate-500">
              Pick a project to list its active stories. Use <span className="text-purple-300">+ New story</span> in
              the header (or <span className="text-purple-300">Create</span> in the app bar) to author one.
            </p>
          </div>
        </AnimatedCard>
        <AnimatedCard glow="cyan" className="p-5">
          <h2 className="text-lg font-semibold text-white mb-2">Tip</h2>
          <p className="text-sm text-slate-400">
            Each project gets stable UUIDs for stories and tags. Open a story to generate test cases with the LLM,
            edit them in review cards, then batch-approve. Approved cases can be run from the Generate page under
            Bulk execution.
          </p>
        </AnimatedCard>
      </div>

      <CreateStoryModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        projectId={projectId || undefined}
        onCreated={(story) => {
          reloadStories();
          notifyTreeRefresh({
            kind: "story",
            projectId: story.project_id,
            sprintId: story.sprint_id || undefined,
          });
        }}
      />
      <ConfirmDeleteModal
        open={confirmOpen}
        entityNoun="story"
        targetLabels={confirmTargets.map((t) => t.title || t.id)}
        mode={"soft" as ConfirmDeleteMode}
        onConfirm={handleConfirmDelete}
        onClose={() => setConfirmOpen(false)}
      />
      {projectId && stories.length > 0 && (
        <SelectionToolbar
          count={selectedIds.length}
          entityNoun="story"
          mode="soft"
          onSoftDelete={() => openDeleteModal(selectedStories)}
          onClear={() => setSelectedIds([])}
        />
      )}
      {projectId && stories.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <label className="inline-flex items-center gap-2 text-xs text-slate-300">
            <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} className="accent-cyan-500" />
            Select all ({stories.length})
          </label>
          <GlassSelect
            className="min-w-[15rem]"
            value={targetSprintId}
            placeholder="Move selected to sprint…"
            onChange={setTargetSprintId}
            options={[
              { value: "", label: "Select sprint…" },
              ...sprints
                .filter((s) => s.state !== "cancelled")
                .map((s) => ({ value: s.id, label: `${s.name} (${s.state})` })),
            ]}
          />
          <button
            type="button"
            disabled={bulkBusy || selectedIds.length === 0 || !targetSprintId}
            onClick={() => bulkMove(targetSprintId)}
            className="px-3 py-1.5 rounded-lg bg-cyan-600/35 text-cyan-100 text-xs disabled:opacity-40"
          >
            Move selected
          </button>
          <button
            type="button"
            disabled={bulkBusy || selectedIds.length === 0}
            onClick={() => bulkMove(null)}
            className="px-3 py-1.5 rounded-lg glass text-xs text-slate-300 disabled:opacity-40"
          >
            Move selected to backlog
          </button>
          {bulkMsg && <span className="text-xs text-slate-400">{bulkMsg}</span>}
        </div>
      )}

      {loading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Stories</h2>
          {stories.length === 0 ? (
            <p className="text-slate-500 text-sm">No active stories for this project.</p>
          ) : (
            stories.map((s) => (
              <motion.div
                key={s.id}
                whileHover={{ scale: 1.01 }}
                className="glass p-4 flex items-center justify-between border border-white/5 hover:border-purple-500/30"
              >
                <label className="flex items-center gap-3 min-w-0 flex-1">
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(s.id)}
                    onChange={() => toggleStory(s.id)}
                    className="accent-cyan-500"
                  />
                  <Link href={`/user-stories/${encodeURIComponent(s.id)}?project=${encodeURIComponent(projectName)}`} className="min-w-0 flex-1">
                    <div className="font-semibold text-white truncate">{s.title}</div>
                    <div className="text-xs text-slate-500">v{s.version} · {s.status}</div>
                  </Link>
                </label>
                <div className="flex items-center gap-3 shrink-0">
                  <button
                    type="button"
                    onClick={() => openDeleteModal([s])}
                    title="Archive this story"
                    className="p-1.5 rounded hover:bg-red-500/20 text-slate-400 hover:text-red-300"
                    aria-label="Delete story"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <polyline points="3 6 5 6 21 6"></polyline>
                      <path d="M19 6l-2 14H7L5 6"></path>
                      <path d="M10 11v6M14 11v6"></path>
                      <path d="M9 6V4h6v2"></path>
                    </svg>
                  </button>
                  <Link href={`/user-stories/${encodeURIComponent(s.id)}?project=${encodeURIComponent(projectName)}`} className="text-purple-400 text-sm">
                    Open →
                  </Link>
                </div>
              </motion.div>
            ))
          )}
        </div>
      )}
    </PageScaffold>
  );
}
