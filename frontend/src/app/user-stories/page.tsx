"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";
import AnimatedCard from "@/components/cards/AnimatedCard";
import CreateStoryModal from "@/components/user-stories/CreateStoryModal";
import { notifyTreeRefresh } from "@/lib/useTreeRefresh";
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
                <Link href={`/user-stories/${encodeURIComponent(s.id)}?project=${encodeURIComponent(projectName)}`} className="text-purple-400 text-sm">
                  Open →
                </Link>
              </motion.div>
            ))
          )}
        </div>
      )}
    </PageScaffold>
  );
}
