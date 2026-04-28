"use client";

/**
 * Sprints list page.
 *
 * Top: a Project picker + "+ New sprint" button. Below: sprint cards
 * grouped by state (active first, then planned, completed, cancelled).
 *
 * The "(No sprint) -- N stories" pseudo-card at the bottom links to the
 * project page with `?filter=no-sprint` so users can find their backlog.
 * That filter is a UI affordance only -- the project test-cases panel
 * doesn't currently filter by sprint, but the link gives the user a
 * predictable next step.
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import GlassSelect from "@/components/ui/GlassSelect";
import { api } from "@/lib/api";

type SprintRow = {
  id: string;
  project_id: string;
  name: string;
  goal: string | null;
  state: "planned" | "active" | "completed" | "cancelled";
  start_date: string | null;
  end_date: string | null;
  created_at: string;
};

type StoryRow = {
  id: string;
  sprint_id: string | null;
  status: string;
};

const STATE_LABELS: Record<SprintRow["state"], string> = {
  active: "Active",
  planned: "Planned",
  completed: "Completed",
  cancelled: "Cancelled",
};

const STATE_PILL: Record<SprintRow["state"], string> = {
  active: "bg-emerald-500/30 text-emerald-200",
  planned: "bg-purple-500/30 text-purple-200",
  completed: "bg-slate-500/30 text-slate-200",
  cancelled: "bg-red-500/30 text-red-200",
};

export default function SprintsPage() {
  const searchParams = useSearchParams();
  const initialProject = searchParams.get("project") || "";

  const [projects, setProjects] = useState<string[]>([]);
  const [projectName, setProjectName] = useState(initialProject);
  const [projectId, setProjectId] = useState("");
  const [sprints, setSprints] = useState<SprintRow[]>([]);
  const [stories, setStories] = useState<StoryRow[]>([]);
  const [storyCountBySprint, setStoryCountBySprint] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ name: "", goal: "", start_date: "", end_date: "" });
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.projects.list().then(setProjects).catch(() => setProjects([]));
  }, []);

  // Resolve slug -> portal UUID (every sprint operation lives in
  // UUID-land per the storage layout doc).
  useEffect(() => {
    if (!projectName) {
      setProjectId("");
      return;
    }
    api.projects
      .portalProjectId(projectName)
      .then((r) => setProjectId(r.project_id))
      .catch(() => setProjectId(""));
  }, [projectName]);

  // Load sprints + ALL stories for this project (so we can count
  // stories-per-sprint and surface a "(No sprint)" backlog total).
  const reload = () => {
    if (!projectId) return;
    setLoading(true);
    setErr(null);
    Promise.all([
      api.sprints.list(projectId).catch(() => [] as SprintRow[]),
      api.userStories.list(projectId).catch(() => [] as StoryRow[]),
    ])
      .then(([s, st]) => {
        setSprints(s as SprintRow[]);
        setStories(st as StoryRow[]);
        const counts: Record<string, number> = {};
        for (const story of st as StoryRow[]) {
          if (story.sprint_id) counts[story.sprint_id] = (counts[story.sprint_id] || 0) + 1;
        }
        setStoryCountBySprint(counts);
      })
      .finally(() => setLoading(false));
  };
  useEffect(reload, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const backlogCount = useMemo(
    () => stories.filter((s) => !s.sprint_id).length,
    [stories],
  );

  const grouped = useMemo(() => {
    const out: Record<SprintRow["state"], SprintRow[]> = {
      active: [],
      planned: [],
      completed: [],
      cancelled: [],
    };
    for (const s of sprints) out[s.state]?.push(s);
    return out;
  }, [sprints]);

  const handleCreate = async () => {
    if (!projectId || !createForm.name.trim()) return;
    setCreating(true);
    setErr(null);
    try {
      await api.sprints.create({
        project_id: projectId,
        name: createForm.name.trim(),
        goal: createForm.goal.trim() || null,
        state: "planned",
        start_date: createForm.start_date || null,
        end_date: createForm.end_date || null,
      });
      setCreateForm({ name: "", goal: "", start_date: "", end_date: "" });
      setShowCreate(false);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not create sprint");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-8">
        <h1 className="text-4xl font-bold mb-2">
          <span className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text text-transparent">
            Sprints
          </span>
        </h1>
        <p className="text-slate-400">
          Group user stories into sprints. A story can also live outside any sprint -- those land in the backlog.
        </p>
      </motion.div>

      <div className="grid md:grid-cols-3 gap-4 mb-6">
        <div className="md:col-span-2 flex items-center gap-3 flex-wrap">
          <GlassSelect
            className="min-w-[16rem]"
            value={projectName}
            placeholder="Select a project…"
            onChange={setProjectName}
            options={[
              { value: "", label: "Select a project…" },
              ...projects.map((p) => ({ value: p, label: p })),
            ]}
          />
          {projectId && (
            <button
              type="button"
              onClick={() => setShowCreate((v) => !v)}
              className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold"
            >
              {showCreate ? "Cancel" : "+ New sprint"}
            </button>
          )}
        </div>
      </div>

      <AnimatePresence>
        {showCreate && projectId && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mb-6"
          >
            <AnimatedCard glow="purple">
              <h3 className="text-sm font-bold text-white mb-3">New sprint</h3>
              <div className="grid sm:grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">Name</span>
                  <input
                    value={createForm.name}
                    onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
                    placeholder="e.g. Sprint 23"
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  />
                </label>
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">Goal (optional)</span>
                  <input
                    value={createForm.goal}
                    onChange={(e) => setCreateForm((f) => ({ ...f, goal: e.target.value }))}
                    placeholder="One-line objective"
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  />
                </label>
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">Start date (optional)</span>
                  <input
                    type="date"
                    value={createForm.start_date}
                    onChange={(e) => setCreateForm((f) => ({ ...f, start_date: e.target.value }))}
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  />
                </label>
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">End date (optional)</span>
                  <input
                    type="date"
                    value={createForm.end_date}
                    onChange={(e) => setCreateForm((f) => ({ ...f, end_date: e.target.value }))}
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  />
                </label>
              </div>
              {err && <p className="mt-3 text-xs text-red-300">{err}</p>}
              <div className="mt-4 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="px-3 py-1.5 rounded-lg glass text-xs text-slate-300 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleCreate}
                  disabled={creating || !createForm.name.trim()}
                  className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-xs font-semibold disabled:opacity-50"
                >
                  {creating ? "Creating…" : "Create sprint"}
                </button>
              </div>
            </AnimatedCard>
          </motion.div>
        )}
      </AnimatePresence>

      {!projectId && (
        <p className="text-sm text-slate-500">Pick a project to see its sprints.</p>
      )}

      {projectId && loading && <p className="text-sm text-slate-500">Loading sprints…</p>}

      {projectId && !loading && (
        <div className="space-y-8">
          {(["active", "planned", "completed", "cancelled"] as const).map((state) => {
            const list = grouped[state];
            if (list.length === 0) return null;
            return (
              <div key={state}>
                <h2 className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-3">
                  {STATE_LABELS[state]} ({list.length})
                </h2>
                <div className="grid md:grid-cols-2 gap-4">
                  {list.map((sprint) => (
                    <Link
                      key={sprint.id}
                      href={`/sprints/${encodeURIComponent(sprint.id)}`}
                    >
                      <AnimatedCard glow="purple" className="cursor-pointer">
                        <div className="flex items-start justify-between gap-2 mb-2">
                          <div className="min-w-0">
                            <h3 className="text-base font-bold text-white truncate">{sprint.name}</h3>
                            {sprint.goal && (
                              <p className="text-[11px] text-slate-400 line-clamp-2 mt-1">{sprint.goal}</p>
                            )}
                          </div>
                          <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full ${STATE_PILL[sprint.state]}`}>
                            {sprint.state}
                          </span>
                        </div>
                        <div className="flex items-center gap-3 text-[11px] text-slate-500 mt-3">
                          <span>
                            {storyCountBySprint[sprint.id] || 0} stor{(storyCountBySprint[sprint.id] || 0) === 1 ? "y" : "ies"}
                          </span>
                          {(sprint.start_date || sprint.end_date) && (
                            <span>
                              {sprint.start_date ?? "?"} → {sprint.end_date ?? "?"}
                            </span>
                          )}
                        </div>
                      </AnimatedCard>
                    </Link>
                  ))}
                </div>
              </div>
            );
          })}

          {sprints.length === 0 && (
            <p className="text-sm text-slate-500">
              No sprints yet for this project. Click <span className="text-purple-300">+ New sprint</span> to get started.
            </p>
          )}

          {/* Backlog pseudo-card */}
          <div>
            <h2 className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-3">
              Backlog
            </h2>
            <Link href={`/user-stories?project=${encodeURIComponent(projectName)}`}>
              <AnimatedCard className="cursor-pointer border border-dashed border-white/10">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-bold text-slate-200">(No sprint)</h3>
                    <p className="text-[11px] text-slate-500 mt-1">
                      Stories not yet assigned to any sprint. Move them into a sprint from the story detail page.
                    </p>
                  </div>
                  <span className="text-2xl font-bold text-slate-300">{backlogCount}</span>
                </div>
              </AnimatedCard>
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
