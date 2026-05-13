"use client";

/**
 * Sprint detail page.
 *
 * Shows the sprint's metadata + every story assigned to it + a
 * dashboard summary (counts of approved/draft/etc.). The footer
 * "Run all approved test cases" button opens the existing
 * BulkExecutionStream component, pointed at the new
 * `/run/sprint/{id}/stream` endpoint -- reuses the same engine and UI
 * that powers per-story / per-tag bulk runs.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import GlassSelect from "@/components/ui/GlassSelect";
import BulkExecutionStream from "@/components/execution/BulkExecutionStream";
import AddStoryToSprintModal from "@/components/sprints/AddStoryToSprintModal";
import { api } from "@/lib/api";
import { notifyTreeRefresh } from "@/lib/useTreeRefresh";

type Sprint = {
  id: string;
  project_id: string;
  name: string;
  goal: string | null;
  state: "planned" | "active" | "completed" | "cancelled";
  start_date: string | null;
  end_date: string | null;
  created_at: string;
  updated_at: string;
};

type StoryRow = {
  id: string;
  title: string;
  version: number;
  status: string;
  sprint_id: string | null;
};

type SprintTestCases = Awaited<ReturnType<typeof api.sprints.testCases>>;

const STATE_PILL: Record<Sprint["state"], string> = {
  active: "bg-emerald-500/30 text-emerald-200",
  planned: "bg-purple-500/30 text-purple-200",
  completed: "bg-slate-500/30 text-slate-200",
  cancelled: "bg-red-500/30 text-red-200",
};

export default function SprintDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const id = decodeURIComponent(params.id as string);
  const projectSlug = searchParams.get("project") || "";

  const [sprint, setSprint] = useState<Sprint | null>(null);
  const [stories, setStories] = useState<StoryRow[]>([]);
  const [tcs, setTcs] = useState<SprintTestCases | null>(null);
  const [orgs, setOrgs] = useState<Array<{ id: string; name: string }>>([]);
  const [orgId, setOrgId] = useState("");
  const [personas, setPersonas] = useState<Array<{ id: string; name: string }>>([]);
  const [personaId, setPersonaId] = useState("");
  const [autoHeal, setAutoHeal] = useState(false);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ name: "", goal: "", state: "planned", start_date: "", end_date: "" });
  const [err, setErr] = useState<string | null>(null);
  const [showAddStory, setShowAddStory] = useState(false);

  const load = useCallback(() => {
    api.sprints.get(id).then((s: Sprint) => {
      setSprint(s);
      setEditForm({
        name: s.name,
        goal: s.goal ?? "",
        state: s.state,
        start_date: s.start_date ?? "",
        end_date: s.end_date ?? "",
      });
      // Once we know the project, populate org/persona pickers.
      api.orgs.list(s.project_id).then(setOrgs).catch(() => setOrgs([]));
      api.userStories.list(s.project_id).then((all: StoryRow[]) => {
        setStories(all.filter((st) => st.sprint_id === id));
      }).catch(() => setStories([]));
    }).catch(() => setSprint(null));

    api.sprints.testCases(id).then(setTcs).catch(() => setTcs(null));
  }, [id]);

  useEffect(load, [load]);

  useEffect(() => {
    if (!sprint || !orgId) {
      setPersonas([]);
      return;
    }
    api.personas
      .list(sprint.project_id, orgId)
      .then(setPersonas)
      .catch(() => setPersonas([]));
  }, [sprint, orgId]);

  if (!sprint) {
    return (
      <div className="max-w-6xl mx-auto px-6 py-20 text-slate-500 text-sm">
        <Link
          href={projectSlug ? `/projects/${encodeURIComponent(projectSlug)}` : "/sprints"}
          className="text-purple-400 hover:underline"
        >
          ← Back to sprints
        </Link>
        <p className="mt-4">Sprint not found or still loading…</p>
      </div>
    );
  }

  const approvedCount = tcs?.by_status.approved ?? 0;

  const runSprint = () => {
    if (!orgId) return;
    setStreamUrl(null);
    setTimeout(() => {
      setStreamUrl(
        api.runs.sprintStreamUrl(id, {
          org_id: orgId,
          persona_id: personaId || null,
          auto_heal: autoHeal,
        }),
      );
    }, 0);
  };

  const removeStory = async (storyId: string) => {
    try {
      await api.sprints.unassignStory(id, storyId);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not remove story from sprint");
    }
  };

  const saveEdit = async () => {
    setErr(null);
    try {
      await api.sprints.update(id, {
        name: editForm.name.trim() || sprint.name,
        goal: editForm.goal.trim() || null,
        state: editForm.state as Sprint["state"],
        start_date: editForm.start_date || null,
        end_date: editForm.end_date || null,
      });
      setEditing(false);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Update failed");
    }
  };

  const cancelSprint = async () => {
    if (!confirm("Cancel this sprint? Every story will be moved back to the backlog.")) return;
    try {
      await api.sprints.delete(id);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Cancel failed");
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <Link
        href={projectSlug ? `/projects/${encodeURIComponent(projectSlug)}` : "/sprints"}
        className="text-sm text-slate-500 hover:text-purple-400 mb-3 inline-block"
      >
        ← {projectSlug ? "Back to project" : "All sprints"}
      </Link>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <h1 className="text-3xl font-bold text-white">{sprint.name}</h1>
              <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full ${STATE_PILL[sprint.state]}`}>
                {sprint.state}
              </span>
            </div>
            {sprint.goal && <p className="text-slate-400 mt-1">{sprint.goal}</p>}
            <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-500">
              {(sprint.start_date || sprint.end_date) && (
                <span>{sprint.start_date ?? "?"} → {sprint.end_date ?? "?"}</span>
              )}
              <span>{stories.length} stor{stories.length === 1 ? "y" : "ies"}</span>
              <span>{approvedCount} approved test case{approvedCount === 1 ? "" : "s"}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setEditing((v) => !v)}
              className="px-3 py-1.5 rounded-xl glass text-sm text-slate-300 hover:text-white"
            >
              {editing ? "Cancel" : "Edit"}
            </button>
            {sprint.state !== "cancelled" && (
              <button
                type="button"
                onClick={cancelSprint}
                className="px-3 py-1.5 rounded-xl glass text-sm text-red-300 hover:text-red-200 border border-red-500/30"
              >
                Cancel sprint
              </button>
            )}
          </div>
        </div>
      </motion.div>

      {editing && (
        <AnimatedCard glow="purple" className="mb-6">
          <h3 className="text-sm font-bold text-white mb-3">Edit sprint</h3>
          <div className="grid sm:grid-cols-2 gap-3">
            <label className="block">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">Name</span>
              <input
                value={editForm.name}
                onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200"
              />
            </label>
            <label className="block">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">State</span>
              <GlassSelect
                value={editForm.state}
                onChange={(v) => setEditForm((f) => ({ ...f, state: v }))}
                options={[
                  { value: "planned", label: "Planned" },
                  { value: "active", label: "Active" },
                  { value: "completed", label: "Completed" },
                  { value: "cancelled", label: "Cancelled" },
                ]}
              />
            </label>
            <label className="block sm:col-span-2">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">Goal</span>
              <input
                value={editForm.goal}
                onChange={(e) => setEditForm((f) => ({ ...f, goal: e.target.value }))}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200"
              />
            </label>
            <label className="block">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">Start date</span>
              <input
                type="date"
                value={editForm.start_date}
                onChange={(e) => setEditForm((f) => ({ ...f, start_date: e.target.value }))}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200"
              />
            </label>
            <label className="block">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">End date</span>
              <input
                type="date"
                value={editForm.end_date}
                onChange={(e) => setEditForm((f) => ({ ...f, end_date: e.target.value }))}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200"
              />
            </label>
          </div>
          {err && <p className="mt-3 text-xs text-red-300">{err}</p>}
          <div className="mt-4 flex justify-end">
            <button
              type="button"
              onClick={saveEdit}
              className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-xs font-semibold"
            >
              Save changes
            </button>
          </div>
        </AnimatedCard>
      )}

      {/* Stories in this sprint */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h2 className="text-sm font-bold text-cyan-400 uppercase tracking-wider">
          Stories ({stories.length})
        </h2>
        {sprint && (
          <button
            type="button"
            onClick={() => setShowAddStory(true)}
            className="px-3 py-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-xs font-semibold"
          >
            + Add story
          </button>
        )}
      </div>

      {stories.length === 0 ? (
        <AnimatedCard className="mb-8 border border-dashed border-white/10">
          <p className="text-sm text-slate-400">
            No stories in this sprint yet. Click{" "}
            <span className="text-purple-300">+ Add story</span> above to create a new one or pull from the backlog.
          </p>
        </AnimatedCard>
      ) : (
        <div className="space-y-2 mb-8">
          {stories.map((s) => (
            <div
              key={s.id}
              className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl bg-white/5 border border-white/10"
            >
              <Link
                href={`/user-stories/${encodeURIComponent(s.id)}${projectSlug ? `?project=${encodeURIComponent(projectSlug)}` : ""}`}
                className="flex items-center gap-2 min-w-0 flex-1 hover:text-purple-300"
              >
                <span className="text-sm text-slate-100 truncate">{s.title}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-600/30 text-purple-200">
                  v{s.version}
                </span>
              </Link>
              <button
                type="button"
                onClick={() => removeStory(s.id)}
                className="text-[11px] text-slate-400 hover:text-red-300"
                title="Remove story from this sprint (it goes back to the backlog)"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Run all approved cases */}
      <h2 className="text-sm font-bold text-emerald-400 uppercase tracking-wider mb-3">
        Run sprint
      </h2>
      <AnimatedCard glow="cyan" className="mb-6">
        <div className="grid sm:grid-cols-2 gap-3">
          <GlassSelect
            value={orgId}
            placeholder="Org…"
            onChange={setOrgId}
            options={[
              { value: "", label: "Select org…" },
              ...orgs.map((o) => ({ value: o.id, label: o.name || o.id })),
            ]}
          />
          <GlassSelect
            value={personaId}
            placeholder="Persona (optional)"
            onChange={setPersonaId}
            disabled={!orgId}
            options={[
              { value: "", label: "Default resolution" },
              ...personas.map((p) => ({ value: p.id, label: p.name })),
            ]}
          />
        </div>
        <label className="flex items-start gap-2 text-xs text-slate-300 select-none cursor-pointer mt-3">
          <input
            type="checkbox"
            className="mt-0.5 accent-fuchsia-500"
            checked={autoHeal}
            onChange={(e) => setAutoHeal(e.target.checked)}
          />
          <span>
            <span className="text-slate-200 font-medium">Auto-heal failures</span>{" "}
            <span className="text-slate-500">-- failed tests are healed by the LLM and re-run once.</span>
          </span>
        </label>
        <button
          type="button"
          onClick={runSprint}
          disabled={!orgId || approvedCount === 0}
          title={
            approvedCount === 0
              ? "No approved test cases in this sprint -- approve some on the story detail pages first."
              : "Run every approved test case across every story in this sprint, in parallel."
          }
          className="w-full mt-3 py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-cyan-600 text-white text-sm font-semibold disabled:opacity-40"
        >
          Run all approved test cases ({approvedCount})
          {autoHeal ? " with auto-heal" : ""}
        </button>
      </AnimatedCard>

      <BulkExecutionStream
        streamUrl={streamUrl}
        onClose={() => setStreamUrl(null)}
        healContext={orgId ? { org_id: orgId, persona_id: personaId || null } : undefined}
      />

      {/* + Add story modal: tabbed Create new / Assign existing.
          Only mountable once we know the sprint's project_id. */}
      {sprint && (
        <AddStoryToSprintModal
          open={showAddStory}
          onClose={() => setShowAddStory(false)}
          sprintId={sprint.id}
          projectId={sprint.project_id}
          onChanged={() => {
            load();
            // Sprint stories changed (create OR assign-existing). Both
            // kinds of mutation invalidate the project's branch.
            notifyTreeRefresh({
              kind: "story",
              projectId: sprint.project_id,
              sprintId: sprint.id,
            });
          }}
        />
      )}
    </div>
  );
}
