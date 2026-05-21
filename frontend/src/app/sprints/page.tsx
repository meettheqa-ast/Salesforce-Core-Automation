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
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import GlassSelect from "@/components/ui/GlassSelect";
import CreateSprintModal from "@/components/sprints/CreateSprintModal";
import SelectionToolbar, { type SelectionToolbarMode } from "@/components/lists/SelectionToolbar";
import ConfirmDeleteModal, { type ConfirmDeleteMode, type DeleteBlocker } from "@/components/lists/ConfirmDeleteModal";
import { api, parseDeleteBlockersError, type BulkDeleteRowResult } from "@/lib/api";
import { notifyTreeRefresh } from "@/lib/useTreeRefresh";
import { useToast } from "@/components/ui/ToastProvider";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

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
  const [err, setErr] = useState<string | null>(null);
  const [selectedSprintIds, setSelectedSprintIds] = useState<string[]>([]);
  const [bulkState, setBulkState] = useState<SprintRow["state"]>("active");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkMsg, setBulkMsg] = useState("");
  // Delete-modal state. Lives at page level so per-row trash buttons
  // and the bulk toolbar share one confirmation surface.
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmMode, setConfirmMode] = useState<ConfirmDeleteMode>("soft");
  // Targets the modal will operate on: explicit list, not derived from
  // `selectedSprintIds`, so a per-row click doesn't accidentally take
  // the bulk selection along with it.
  const [confirmTargets, setConfirmTargets] = useState<SprintRow[]>([]);
  const toast = useToast();

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
  useEffect(reload, [projectId]);  

  useEffect(() => {
    setSelectedSprintIds((prev) => prev.filter((id) => sprints.some((s) => s.id === id)));
  }, [sprints]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || (e.target as HTMLElement | null)?.isContentEditable) return;
      if (e.key.toLowerCase() === "n" && projectId) {
        e.preventDefault();
        setShowCreate(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [projectId]);

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

  const allSelected = sprints.length > 0 && selectedSprintIds.length === sprints.length;
  const toggleSprint = (id: string) => {
    setSelectedSprintIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };
  const toggleAllSprints = () => {
    if (allSelected) setSelectedSprintIds([]);
    else setSelectedSprintIds(sprints.map((s) => s.id));
  };

  // Derive the lifecycle mode for the SelectionToolbar from the
  // currently-selected sprint states. "cancelled" rows are
  // soft-deleted already, so they advance to permanent-delete on the
  // next click. Any other state is still in the soft-delete column.
  const selectedSprints = useMemo(
    () => sprints.filter((s) => selectedSprintIds.includes(s.id)),
    [sprints, selectedSprintIds],
  );
  const toolbarMode: SelectionToolbarMode = useMemo(() => {
    if (selectedSprints.length === 0) return "soft";
    const allCancelled = selectedSprints.every((s) => s.state === "cancelled");
    const noneCancelled = selectedSprints.every((s) => s.state !== "cancelled");
    if (allCancelled) return "permanent";
    if (noneCancelled) return "soft";
    return "mixed";
  }, [selectedSprints]);

  const openDeleteModal = (targets: SprintRow[], mode: ConfirmDeleteMode) => {
    if (targets.length === 0) return;
    setConfirmTargets(targets);
    setConfirmMode(mode);
    setConfirmOpen(true);
  };

  const handleConfirmDelete = async (): Promise<{ ok: boolean; blockers?: DeleteBlocker[]; message?: string }> => {
    const ids = confirmTargets.map((t) => t.id);
    const permanent = confirmMode === "permanent";
    try {
      if (ids.length === 1) {
        await api.sprints.delete(ids[0], permanent);
      } else {
        const res = await api.sprints.bulkDelete(ids, permanent);
        // Aggregate blockers across rows for the modal. The bulk
        // endpoint always returns 200 so we have to read the
        // per-row status to know if anything was refused.
        const blocked = res.results.filter((r: BulkDeleteRowResult) => r.status === "skipped_blocked");
        const invalid = res.results.filter((r: BulkDeleteRowResult) => r.status === "skipped_invalid_state");
        if (blocked.length > 0) {
          const agg: DeleteBlocker[] = blocked.flatMap((r) =>
            (r.blockers || []).map((b) => ({ id: b.id, label: b.label, reason: b.reason })),
          );
          return {
            ok: false,
            blockers: agg,
            message: `${blocked.length} sprint(s) still have live stories.`,
          };
        }
        if (invalid.length > 0) {
          return {
            ok: false,
            message: invalid[0].detail || "Some sprints aren't ready for permanent delete.",
          };
        }
      }
      toast.success(
        permanent
          ? `${ids.length} sprint(s) permanently deleted.`
          : `${ids.length} sprint(s) cancelled.`,
      );
      setSelectedSprintIds([]);
      reload();
      notifyTreeRefresh({ kind: "sprint", projectId: projectId || undefined });
      return { ok: true };
    } catch (err: unknown) {
      // Single-id 409 path: parse blockers from the apiFetch error.
      const parsed = parseDeleteBlockersError(err);
      if (parsed) {
        return { ok: false, blockers: parsed.blockers, message: parsed.detail };
      }
      return {
        ok: false,
        message: err instanceof Error ? err.message : "Delete failed",
      };
    }
  };

  const applyBulkState = async () => {
    if (selectedSprintIds.length === 0) return;
    setBulkBusy(true);
    setBulkMsg("");
    let ok = 0;
    let failed = 0;
    await Promise.all(
      selectedSprintIds.map(async (id) => {
        const current = sprints.find((s) => s.id === id);
        if (!current || current.state === bulkState) return;
        try {
          await api.sprints.update(id, { state: bulkState });
          ok += 1;
        } catch {
          failed += 1;
        }
      }),
    );
    setBulkBusy(false);
    setSelectedSprintIds([]);
    setBulkMsg(failed > 0 ? `Updated ${ok}; ${failed} failed.` : `Updated ${ok} sprint(s).`);
    reload();
    notifyTreeRefresh({ kind: "sprint", projectId: projectId || undefined });
  };

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Delivery"
          title="Sprints"
          description="Group stories into planned iterations while keeping backlog stories visible and manageable."
        />
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
              onClick={() => setShowCreate(true)}
              className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold"
            >
              + New sprint
            </button>
          )}
        </div>
      </div>
      {projectId && sprints.length > 0 && (
        <>
          <SelectionToolbar
            count={selectedSprintIds.length}
            entityNoun="sprint"
            mode={toolbarMode}
            onSoftDelete={
              toolbarMode === "soft" || toolbarMode === "mixed"
                ? () => openDeleteModal(selectedSprints.filter((s) => s.state !== "cancelled"), "soft")
                : undefined
            }
            onHardDelete={
              toolbarMode === "permanent" || toolbarMode === "mixed"
                ? () => openDeleteModal(selectedSprints.filter((s) => s.state === "cancelled"), "permanent")
                : undefined
            }
            onClear={() => setSelectedSprintIds([])}
          />
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <label className="inline-flex items-center gap-2 text-xs text-slate-300">
              <input type="checkbox" checked={allSelected} onChange={toggleAllSprints} className="accent-cyan-500" />
              Select all ({sprints.length})
            </label>
            <GlassSelect
              className="min-w-[13rem]"
              value={bulkState}
              onChange={(v) => setBulkState(v as SprintRow["state"])}
              options={[
                { value: "active", label: "Active" },
                { value: "planned", label: "Planned" },
                { value: "completed", label: "Completed" },
                { value: "cancelled", label: "Cancelled" },
              ]}
            />
            <button
              type="button"
              disabled={bulkBusy || selectedSprintIds.length === 0}
              onClick={applyBulkState}
              className="px-3 py-1.5 rounded-lg bg-cyan-600/35 text-cyan-100 text-xs disabled:opacity-40"
            >
              Update selected state
            </button>
            {bulkMsg && <span className="text-xs text-slate-400">{bulkMsg}</span>}
          </div>
        </>
      )}

      <CreateSprintModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        projectId={projectId || undefined}
        onCreated={() => {
          setErr(null);
          reload();
          notifyTreeRefresh({ kind: "sprint", projectId: projectId || undefined });
        }}
      />

      {!projectId && (
        <p className="text-sm text-slate-500">Pick a project to see its sprints.</p>
      )}

      {projectId && loading && <p className="text-sm text-slate-500">Loading sprints…</p>}

      <ConfirmDeleteModal
        open={confirmOpen}
        entityNoun="sprint"
        targetLabels={confirmTargets.map((t) => t.name || t.id)}
        mode={confirmMode}
        onConfirm={handleConfirmDelete}
        onClose={() => setConfirmOpen(false)}
      />

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
                    <AnimatedCard key={sprint.id} glow="purple" className="cursor-pointer">
                      <div className="flex items-start justify-between gap-2 mb-2">
                        <label className="inline-flex items-start gap-2 min-w-0 flex-1">
                          <input
                            type="checkbox"
                            checked={selectedSprintIds.includes(sprint.id)}
                            onChange={() => toggleSprint(sprint.id)}
                            className="accent-cyan-500 mt-1"
                          />
                          <Link href={`/sprints/${encodeURIComponent(sprint.id)}?project=${encodeURIComponent(projectName)}`} className="min-w-0">
                            <h3 className="text-base font-bold text-white truncate">{sprint.name}</h3>
                            {sprint.goal && (
                              <p className="text-[11px] text-slate-400 line-clamp-2 mt-1">{sprint.goal}</p>
                            )}
                          </Link>
                        </label>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full ${STATE_PILL[sprint.state]}`}>
                            {sprint.state}
                          </span>
                          {/* Per-row delete. The mode flips to "permanent"
                              once a sprint is already cancelled so the
                              second click finishes the lifecycle. */}
                          <button
                            type="button"
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              openDeleteModal(
                                [sprint],
                                sprint.state === "cancelled" ? "permanent" : "soft",
                              );
                            }}
                            title={sprint.state === "cancelled" ? "Permanently delete this sprint" : "Cancel this sprint"}
                            className="p-1 rounded hover:bg-red-500/20 text-slate-400 hover:text-red-300"
                            aria-label="Delete sprint"
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                              <polyline points="3 6 5 6 21 6"></polyline>
                              <path d="M19 6l-2 14H7L5 6"></path>
                              <path d="M10 11v6M14 11v6"></path>
                              <path d="M9 6V4h6v2"></path>
                            </svg>
                          </button>
                        </div>
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
    </PageScaffold>
  );
}
