"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";
import { notifyTreeRefresh } from "@/lib/useTreeRefresh";
import EditTestCasesModal, { type TestCaseDraft } from "@/components/test-cases/EditTestCasesModal";
import BulkExecutionStream from "@/components/execution/BulkExecutionStream";

type TestCaseRow = {
  id: string;
  user_story_id: string;
  project_id: string;
  title: string;
  steps: string[];
  expected_result: string;
  preconditions: string | null;
  status: "draft" | "approved" | "rejected";
  stale: boolean;
  tags: string[];
  created_at: string;
  script_path?: string | null;
  script_built_at?: string | null;
};

const ACTIVITY_LABELS: Record<string, string> = {
  story_created: "Story created",
  story_updated: "Story updated",
  story_assigned: "Owner reassigned",
  story_comment_added: "Comment added",
  story_cases_generated: "AI test cases generated",
  story_scripts_built: "Robot scripts generated",
};

function parseActivityMetadata(raw: string): Record<string, any> {
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

export default function UserStoryDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const id = decodeURIComponent(params.id as string);
  const projectSlug = searchParams.get("project") || "";

  const [story, setStory] = useState<any>(null);
  const [tcs, setTcs] = useState<TestCaseRow[]>([]);
  const [genLoading, setGenLoading] = useState(false);
  const [buildLoading, setBuildLoading] = useState(false);
  const [buildBusy, setBuildBusy] = useState<Record<string, boolean>>({});
  const [statusBusy, setStatusBusy] = useState<Record<string, boolean>>({});
  const [selectedCases, setSelectedCases] = useState<string[]>([]);
  const [scriptOpen, setScriptOpen] = useState<Record<string, string>>({});
  const [scriptLoading, setScriptLoading] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [meId, setMeId] = useState("");
  const [comments, setComments] = useState<Array<{
    id: string;
    author_email: string;
    body: string;
    mentions: string[];
    created_at: string;
  }>>([]);
  const [newComment, setNewComment] = useState("");
  const [commentBusy, setCommentBusy] = useState(false);
  const [activity, setActivity] = useState<Array<any>>([]);

  // Bulk edit / add modal state. `editAllOpen` carries an "openMode"
  // because the same modal serves both flows: "manual" pre-loads one
  // blank case, "edit-all" pre-loads every existing case.
  const [editAllOpen, setEditAllOpen] = useState<"manual" | "edit-all" | null>(null);
  const [knownTags, setKnownTags] = useState<string[]>([]);
  // Skip reasons from the most recent build-scripts call. Surfaced inline
  // under the success message so users see *why* each case was skipped
  // (rate-limit, parse error, etc.) instead of just a count.
  const [buildSkipped, setBuildSkipped] = useState<
    Array<{ test_case_id: string; title: string; reason: string }>
  >([]);
  // Per-test-case Run flow.
  type OrgRow = { id: string; name?: string; environment?: string };
  const [orgs, setOrgs] = useState<OrgRow[]>([]);
  // When non-null, the page is showing the org-picker UI for this test case.
  const [orgPickerForCase, setOrgPickerForCase] = useState<string | null>(null);
  const [orgPickerSelected, setOrgPickerSelected] = useState<string>("");
  // When set, a run is live and BulkExecutionStream is rendered with this URL.
  // ``tcId`` is null when the run is the whole-story "Run all approved" flow
  // (which still uses BulkExecutionStream but isn't tied to one test case).
  const [activeRun, setActiveRun] = useState<
    { tcId: string | null; tcTitle: string; streamUrl: string; runHealContext?: { org_id: string; persona_id: string | null } } | null
  >(null);
  // Story-level "Run all approved" picker. Opens an inline org/persona/auto-heal
  // chooser; on confirm it builds the SSE URL via api.runs.userStoryStreamUrl
  // and feeds it into the existing BulkExecutionStream below.
  const [storyRunPickerOpen, setStoryRunPickerOpen] = useState(false);
  const [storyRunOrgId, setStoryRunOrgId] = useState("");
  const [storyRunPersonaId, setStoryRunPersonaId] = useState("");
  const [storyRunAutoHeal, setStoryRunAutoHeal] = useState(false);
  const [storyRunPersonas, setStoryRunPersonas] = useState<Array<{ id: string; name: string }>>([]);
  // Sprint reassignment state. The chip is always visible; clicking it
  // toggles `sprintMenuOpen` to show a dropdown of active+planned
  // sprints in this project plus a "(No sprint)" option.
  const [availableSprints, setAvailableSprints] = useState<Array<{ id: string; name: string; state: string }>>([]);
  const [currentSprint, setCurrentSprint] = useState<{ id: string; name: string } | null>(null);
  const [sprintMenuOpen, setSprintMenuOpen] = useState(false);
  const [sprintBusy, setSprintBusy] = useState(false);

  // Pull the project's known tags once we know the story's project id, so
  // the Edit modal can show clickable tag suggestions instead of forcing
  // free-text entry every time.
  useEffect(() => {
    if (!story?.project_id) return;
    api.tags
      .list(story.project_id)
      .then((rows: Array<{ name: string }>) =>
        setKnownTags(rows.map((r) => r.name).filter(Boolean)),
      )
      .catch(() => setKnownTags([]));
  }, [story?.project_id]);

  // Available Salesforce orgs for this project, used by the per-test-case
  // Run button. We need an org_id to build the stream URL; the persona is
  // optional and resolved server-side from project + user defaults.
  useEffect(() => {
    if (!story?.project_id) return;
    api.orgs
      .list(story.project_id)
      .then((rows: any[]) => setOrgs((rows || []) as OrgRow[]))
      .catch(() => setOrgs([]));
  }, [story?.project_id]);

  // Available sprints + the story's current sprint label. Two requests
  // because the story payload only carries the sprint UUID; we need to
  // resolve the name for the chip text.
  useEffect(() => {
    if (!story?.project_id) return;
    api.sprints
      .list(story.project_id)
      .then((rows: any[]) =>
        setAvailableSprints(
          rows.map((s) => ({ id: s.id, name: s.name, state: s.state })),
        ),
      )
      .catch(() => setAvailableSprints([]));
  }, [story?.project_id]);

  useEffect(() => {
    const sid = story?.sprint_id as string | undefined;
    if (!sid) {
      setCurrentSprint(null);
      return;
    }
    // Look it up locally first; fall back to a fetch if the list hasn't
    // loaded yet (e.g. sprint is in 'completed' state and the project
    // page filters those out -- we still want to show its name here).
    const local = availableSprints.find((s) => s.id === sid);
    if (local) {
      setCurrentSprint({ id: local.id, name: local.name });
      return;
    }
    api.sprints.get(sid).then((s: any) => setCurrentSprint({ id: s.id, name: s.name })).catch(() => {});
  }, [story?.sprint_id, availableSprints]);

  const reassignSprint = async (newSprintId: string | null) => {
    if (!story) return;
    setSprintBusy(true);
    try {
      const currentId = story.sprint_id as string | null;
      // Two-step move: unassign from old sprint (if any), assign to new (if any).
      // The backend's assign endpoint accepts the move in one call but we go
      // through unassign first so the per-sprint index reflects the previous
      // owner cleanly; save_user_story handles index churn either way.
      if (currentId && currentId !== newSprintId) {
        await api.sprints.unassignStory(currentId, story.id);
      }
      if (newSprintId && newSprintId !== currentId) {
        await api.sprints.assignStory(newSprintId, story.id);
      }
      setSprintMenuOpen(false);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not reassign sprint");
    } finally {
      setSprintBusy(false);
    }
  };

  const load = useCallback(() => {
    api.userStories.get(id).then(setStory).catch(() => setStory(null));
    api.testCases.list(id).then(setTcs).catch(() => setTcs([]));
    api.userStories.comments(id).then(setComments).catch(() => setComments([]));
    api.userStories.activity(id).then((r) => setActivity(r.items || [])).catch(() => setActivity([]));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    api.me().then((m) => setMeId(m.id)).catch(() => setMeId(""));
  }, []);

  // Auto-open the test-case author when arriving via
  // `/user-stories/[id]?addCase=1`. The project-hub `+ Add test case`
  // flow lands the user here after picking a story so the existing
  // EditTestCasesModal is the single authoring surface.
  useEffect(() => {
    if (searchParams.get("addCase") === "1") {
      setEditAllOpen("manual");
    }
  }, [searchParams]);

  // Story-run persona list keyed off the picker's chosen org. Mirrors
  // the per-test-case run flow; persona is optional (server resolves a
  // default), so we don't gate the run button on this.
  useEffect(() => {
    if (!story?.project_id || !storyRunOrgId) {
      setStoryRunPersonas([]);
      return;
    }
    api.personas
      .list(story.project_id, storyRunOrgId)
      .then((rows: Array<{ id: string; name: string }>) =>
        setStoryRunPersonas(rows.map((p) => ({ id: p.id, name: p.name }))),
      )
      .catch(() => setStoryRunPersonas([]));
  }, [story?.project_id, storyRunOrgId]);

  const counts = useMemo(() => {
    const draft = tcs.filter((t) => t.status === "draft" && !t.stale).length;
    const approved = tcs.filter((t) => t.status === "approved" && !t.stale).length;
    const rejected = tcs.filter((t) => t.status === "rejected").length;
    const stale = tcs.filter((t) => t.stale).length;
    const built = tcs.filter((t) => !!t.script_path).length;
    return { draft, approved, rejected, stale, built, total: tcs.length };
  }, [tcs]);

  useEffect(() => {
    setSelectedCases((prev) => prev.filter((id2) => tcs.some((tc) => tc.id === id2)));
  }, [tcs]);

  const generate = async () => {
    setGenLoading(true);
    setErr("");
    setMsg("");
    try {
      const res = await api.userStories.generate(id);
      const fresh = await api.testCases.list(id);
      setTcs(fresh);
      setMsg(`Generated ${(res.generated || []).length} draft test case(s). Review and approve below.`);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setGenLoading(false);
    }
  };

  const setCaseStatus = async (
    tcId: string,
    status: "approved" | "rejected" | "draft",
  ) => {
    setStatusBusy((s) => ({ ...s, [tcId]: true }));
    setErr("");
    try {
      const updated = await api.testCases.patch(tcId, { status });
      setTcs((prev) => prev.map((t) => (t.id === tcId ? { ...t, ...updated } : t)));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Status update failed");
    } finally {
      setStatusBusy((s) => ({ ...s, [tcId]: false }));
    }
  };

  const buildScripts = async () => {
    setBuildLoading(true);
    setErr("");
    setMsg("");
    setBuildSkipped([]);
    try {
      const res = await api.userStories.buildScripts(id);
      const fresh = await api.testCases.list(id);
      setTcs(fresh);
      // Capture each skipped case so the inline panel can surface
      // titles + reasons (rate-limit, parse error, ...) instead of just
      // a count.
      setBuildSkipped(res.skipped || []);
      const skippedNote = res.skipped.length
        ? ` ${res.skipped.length} skipped — see details below.`
        : "";
      setMsg(`Built ${res.built.length} script(s) under ${res.output_dir}.${skippedNote}`);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Build failed");
    } finally {
      setBuildLoading(false);
    }
  };

  const toggleSelectedCase = (tcId: string) => {
    setSelectedCases((prev) =>
      prev.includes(tcId) ? prev.filter((id2) => id2 !== tcId) : [...prev, tcId],
    );
  };

  const selectableIds = useMemo(
    () => tcs.filter((t) => t.status === "approved" && !t.stale).map((t) => t.id),
    [tcs],
  );
  const allSelectableChecked =
    selectableIds.length > 0 && selectableIds.every((id2) => selectedCases.includes(id2));

  const toggleSelectAll = () => {
    if (!allSelectableChecked) {
      setSelectedCases(selectableIds);
      return;
    }
    setSelectedCases([]);
  };

  const buildSingleScript = async (tcId: string) => {
    setBuildBusy((s) => ({ ...s, [tcId]: true }));
    setErr("");
    try {
      await api.testCases.buildScript(tcId);
      const fresh = await api.testCases.list(id);
      setTcs(fresh);
      setMsg("Script generated.");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not generate script");
    } finally {
      setBuildBusy((s) => ({ ...s, [tcId]: false }));
    }
  };

  const buildSelectedScripts = async () => {
    const targets = selectedCases.filter((tcId) =>
      tcs.some((tc) => tc.id === tcId && tc.status === "approved" && !tc.stale),
    );
    if (targets.length === 0) {
      setErr("Select at least one approved, non-stale test case.");
      return;
    }
    setBuildLoading(true);
    setErr("");
    setMsg("");
    let ok = 0;
    let failed = 0;
    await Promise.all(
      targets.map(async (tcId) => {
        try {
          await api.testCases.buildScript(tcId);
          ok += 1;
        } catch {
          failed += 1;
        }
      }),
    );
    const fresh = await api.testCases.list(id).catch(() => null);
    if (fresh) setTcs(fresh);
    setBuildLoading(false);
    setSelectedCases([]);
    if (failed > 0) setErr(`Generated ${ok} script(s); ${failed} failed.`);
    else setMsg(`Generated ${ok} script(s).`);
  };

  /** Decide whether we have everything needed to launch a run, and either
   *  start streaming or open the org picker. The persona is intentionally
   *  unset so the backend resolver picks the project default; users can
   *  fine-tune persona from the global Runs page when needed. */
  const startRunForCase = (tcId: string) => {
    setErr("");
    if (orgs.length === 0) {
      setErr(
        "No Salesforce orgs registered for this project. Add one under Settings → Orgs first.",
      );
      return;
    }
    if (orgs.length === 1) {
      launchRun(tcId, orgs[0].id);
      return;
    }
    setOrgPickerSelected(orgs[0].id);
    setOrgPickerForCase(tcId);
  };

  const launchRun = (tcId: string, orgId: string) => {
    const tc = tcs.find((t) => t.id === tcId);
    const url = api.runs.testCaseStreamUrl(tcId, { org_id: orgId });
    setActiveRun({
      tcId,
      tcTitle: tc?.title ?? "Test case",
      streamUrl: url,
    });
    setOrgPickerForCase(null);
  };

  /** Story-level "Run all approved" launcher. Builds the SSE URL via
   *  api.runs.userStoryStreamUrl and routes it through the same
   *  BulkExecutionStream component the per-tc + sprint flows use. */
  const launchStoryRun = () => {
    if (!storyRunOrgId) {
      setErr("Pick an org first.");
      return;
    }
    const url = api.runs.userStoryStreamUrl(id, {
      org_id: storyRunOrgId,
      persona_id: storyRunPersonaId || null,
      auto_heal: storyRunAutoHeal,
    });
    setActiveRun({
      tcId: null,
      tcTitle: story?.title ?? "User story",
      streamUrl: url,
      runHealContext: { org_id: storyRunOrgId, persona_id: storyRunPersonaId || null },
    });
    setStoryRunPickerOpen(false);
  };

  const closeRun = () => {
    setActiveRun(null);
  };

  const toggleScript = async (tcId: string) => {
    if (scriptOpen[tcId] !== undefined) {
      setScriptOpen((s) => {
        const n = { ...s };
        delete n[tcId];
        return n;
      });
      return;
    }
    setScriptLoading((s) => ({ ...s, [tcId]: true }));
    setErr("");
    try {
      const r = await api.testCases.script(tcId);
      setScriptOpen((s) => ({ ...s, [tcId]: r.content }));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not load script");
    } finally {
      setScriptLoading((s) => ({ ...s, [tcId]: false }));
    }
  };

  const saveStory = async () => {
    setErr("");
    try {
      const r = await api.userStories.update(id, {
        title: editTitle || undefined,
        description: editDesc || undefined,
      });
      setEditOpen(false);
      setMsg(r.message || "Updated");
      const qs = projectSlug ? `?project=${encodeURIComponent(projectSlug)}` : "";
      window.location.href = `/user-stories/${encodeURIComponent(r.new_story.id)}${qs}`;
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Update failed");
    }
  };

  const assignToMe = async () => {
    setErr("");
    try {
      await api.userStories.assign(id, meId || undefined);
      setMsg("Story ownership updated.");
      load();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not assign story");
    }
  };

  const addComment = async () => {
    const text = newComment.trim();
    if (!text) return;
    setCommentBusy(true);
    setErr("");
    try {
      await api.userStories.addComment(id, text);
      setNewComment("");
      setMsg("Comment posted.");
      load();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not post comment");
    } finally {
      setCommentBusy(false);
    }
  };

  const activityMessage = (entry: any) => {
    const label = ACTIVITY_LABELS[entry.action] || entry.action;
    const meta = parseActivityMetadata(String(entry.metadata_json || ""));
    if (entry.action === "story_updated" && meta.version) {
      return `${label} to v${meta.version}`;
    }
    if (entry.action === "story_assigned" && meta.owner_user_id) {
      return `${label} (${meta.owner_user_id})`;
    }
    if (entry.action === "story_cases_generated" && typeof meta.count === "number") {
      return `${label}: ${meta.count}`;
    }
    if (entry.action === "story_scripts_built") {
      const built = typeof meta.built === "number" ? meta.built : 0;
      const skipped = typeof meta.skipped === "number" ? meta.skipped : 0;
      return `${label}: ${built} built, ${skipped} skipped`;
    }
    if (entry.action === "story_comment_added" && meta.preview) {
      return `${label}: ${String(meta.preview)}`;
    }
    return label;
  };

  if (!story) {
    return (
      <div className="max-w-6xl mx-auto px-6 py-20 text-slate-500 text-sm">
        <Link
          href={projectSlug ? `/projects/${encodeURIComponent(projectSlug)}` : "/user-stories"}
          className="text-purple-400 hover:underline"
        >
          ← Back
        </Link>
        <p className="mt-4">Story not found or still loading…</p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <Link
        href={
          projectSlug
            ? `/projects/${encodeURIComponent(projectSlug)}`
            : "/user-stories"
        }
        className="text-sm text-slate-500 hover:text-purple-400 mb-4 inline-block"
      >
        ← {projectSlug ? "Back to project" : "All stories"}
      </Link>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-white mb-1">{story.title}</h1>
            <p className="text-slate-400 whitespace-pre-wrap">{story.description}</p>
            <div className="flex flex-wrap gap-2 mt-3 items-center">
              <span className="text-xs px-2 py-0.5 rounded bg-purple-600/30 text-purple-200">v{story.version}</span>
              <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300">{story.status}</span>
              <span className={`text-xs px-2 py-0.5 rounded ${story.owner_user_id === meId ? "bg-cyan-600/25 text-cyan-200" : "bg-slate-600/30 text-slate-300"}`}>
                owner: {story.owner_user_id === meId ? "you" : (story.owner_user_id || "unassigned")}
              </span>
              {/* Sprint chip with click-to-reassign dropdown */}
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setSprintMenuOpen((v) => !v)}
                  disabled={sprintBusy}
                  className={`text-xs px-2 py-0.5 rounded transition-colors ${
                    currentSprint
                      ? "bg-fuchsia-500/30 text-fuchsia-100 hover:bg-fuchsia-500/40"
                      : "bg-slate-600/30 text-slate-300 hover:bg-slate-600/40 border border-dashed border-white/15"
                  }`}
                  title="Reassign sprint"
                >
                  {currentSprint ? `Sprint: ${currentSprint.name} ▾` : "No sprint ▾"}
                </button>
                {sprintMenuOpen && (
                  <div className="absolute z-20 top-full mt-1 left-0 min-w-[240px] glass-strong rounded-xl py-1 border border-white/10 shadow-xl">
                    <button
                      type="button"
                      onClick={() => reassignSprint(null)}
                      disabled={sprintBusy || !story.sprint_id}
                      className="w-full text-left px-3 py-1.5 text-xs text-slate-300 hover:bg-white/10 disabled:opacity-50"
                    >
                      (No sprint -- backlog)
                    </button>
                    {availableSprints.length === 0 && (
                      <p className="px-3 py-2 text-[11px] text-slate-500 italic">
                        No sprints in this project yet.
                      </p>
                    )}
                    {availableSprints.map((s) => {
                      const isCurrent = s.id === story.sprint_id;
                      return (
                        <button
                          key={s.id}
                          type="button"
                          onClick={() => reassignSprint(s.id)}
                          disabled={sprintBusy || isCurrent}
                          className={`w-full text-left px-3 py-1.5 text-xs hover:bg-white/10 disabled:opacity-50 ${
                            isCurrent ? "text-fuchsia-200" : "text-slate-200"
                          }`}
                        >
                          {s.name} <span className="text-slate-500">({s.state})</span>
                          {isCurrent && <span className="ml-2 text-[10px]">current</span>}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
              {counts.stale > 0 && (
                <span className="text-xs px-2 py-0.5 rounded bg-amber-600/30 text-amber-200" title="Parent story was updated">
                  {counts.stale} stale
                </span>
              )}
              <span className="text-xs px-2 py-0.5 rounded bg-emerald-600/20 text-emerald-200">
                {counts.approved} approved
              </span>
              <span className="text-xs px-2 py-0.5 rounded bg-slate-700/40 text-slate-300">
                {counts.draft} draft
              </span>
              {counts.built > 0 && (
                <span className="text-xs px-2 py-0.5 rounded bg-cyan-600/20 text-cyan-200">
                  {counts.built} script(s) built
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => { setEditTitle(story.title); setEditDesc(story.description); setEditOpen(true); }}
              className="px-4 py-2 rounded-xl glass text-sm text-slate-300 hover:text-white"
            >
              Update story
            </button>
            <button
              type="button"
              onClick={assignToMe}
              disabled={!meId || story.owner_user_id === meId}
              className="px-4 py-2 rounded-xl glass text-sm text-cyan-200 hover:text-white border border-cyan-500/30 disabled:opacity-40"
            >
              {story.owner_user_id === meId ? "Assigned to you" : "Assign to me"}
            </button>
            <button
              type="button"
              onClick={() => setEditAllOpen("manual")}
              className="px-4 py-2 rounded-xl glass text-sm text-slate-200 hover:text-white border border-cyan-500/30"
            >
              + Add case manually
            </button>
            {tcs.length > 0 && (
              <button
                type="button"
                onClick={() => setEditAllOpen("edit-all")}
                className="px-4 py-2 rounded-xl glass text-sm text-slate-200 hover:text-white border border-amber-500/30"
              >
                Edit all ({tcs.length})
              </button>
            )}
            <button
              type="button"
              disabled={genLoading}
              onClick={generate}
              className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50"
            >
              {genLoading ? "Generating…" : "Generate test cases"}
            </button>
            <button
              type="button"
              disabled={buildLoading || counts.approved === 0}
              onClick={buildScripts}
              title={counts.approved === 0 ? "Approve at least one test case first" : "Materialise a Robot script per approved case"}
              className="px-4 py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-cyan-600 text-white text-sm font-semibold disabled:opacity-40"
            >
              {buildLoading ? "Building…" : `Generate scripts (${counts.approved})`}
            </button>
            <button
              type="button"
              disabled={buildLoading || selectedCases.length === 0}
              onClick={buildSelectedScripts}
              className="px-4 py-2 rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 text-white text-sm font-semibold disabled:opacity-40"
              title="Generate scripts only for selected approved cases"
            >
              {buildLoading ? "Working…" : `Generate selected (${selectedCases.length})`}
            </button>
            <button
              type="button"
              disabled={counts.approved === 0}
              onClick={() => {
                setErr("");
                if (orgs.length === 0) {
                  setErr("No Salesforce orgs registered for this project. Add one under Settings → Orgs first.");
                  return;
                }
                setStoryRunOrgId((prev) => prev || orgs[0].id);
                setStoryRunPickerOpen(true);
              }}
              title={
                counts.approved === 0
                  ? "No approved test cases to run yet."
                  : "Run every approved test case in this story, in parallel."
              }
              className="px-4 py-2 rounded-xl bg-gradient-to-r from-fuchsia-600 to-purple-600 text-white text-sm font-semibold disabled:opacity-40"
            >
              Run all approved ({counts.approved})
            </button>
          </div>
        </div>
      </motion.div>

      {/* Inline picker for the story-level "Run all approved" launcher.
          Mirrors the per-test-case picker pattern -- org is required;
          persona is optional (server resolves a default). Auto-heal
          flips the engine into the heal/retry loop on failures. */}
      <AnimatePresence>
        {storyRunPickerOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mb-4"
          >
            <div className="glass-strong p-4 rounded-xl">
              <div className="flex flex-wrap items-end gap-3">
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">Org</span>
                  <select
                    value={storyRunOrgId}
                    onChange={(e) => setStoryRunOrgId(e.target.value)}
                    className="block w-48 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200"
                  >
                    {orgs.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name || o.id}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">Persona</span>
                  <select
                    value={storyRunPersonaId}
                    onChange={(e) => setStoryRunPersonaId(e.target.value)}
                    className="block w-48 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200"
                  >
                    <option value="">Default resolution</option>
                    {storyRunPersonas.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-2 text-xs text-slate-300 ml-2">
                  <input
                    type="checkbox"
                    className="accent-fuchsia-500"
                    checked={storyRunAutoHeal}
                    onChange={(e) => setStoryRunAutoHeal(e.target.checked)}
                  />
                  <span>
                    <span className="text-slate-200 font-medium">Auto-heal failures</span>
                  </span>
                </label>
                <div className="ml-auto flex gap-2">
                  <button
                    type="button"
                    onClick={() => setStoryRunPickerOpen(false)}
                    className="px-3 py-2 rounded-lg glass text-sm text-slate-300 hover:text-white"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={launchStoryRun}
                    disabled={!storyRunOrgId}
                    className="px-4 py-2 rounded-lg bg-gradient-to-r from-emerald-600 to-cyan-600 text-white text-sm font-semibold disabled:opacity-50"
                  >
                    Run {counts.approved} {counts.approved === 1 ? "case" : "cases"}
                    {storyRunAutoHeal ? " with auto-heal" : ""}
                  </button>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {msg && <p className="text-emerald-400 text-sm mb-3">{msg}</p>}
      {err && <p className="text-red-400 text-sm mb-3">{err}</p>}

      <div className="grid lg:grid-cols-2 gap-4 mb-5">
        <div className="glass p-4 rounded-xl border border-white/10">
          <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-2">Comments</h3>
          <div className="flex items-start gap-2 mb-3">
            <textarea
              value={newComment}
              onChange={(e) => setNewComment(e.target.value)}
              placeholder="Add a comment... Mention teammates with @email"
              className="flex-1 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-slate-200 min-h-[72px]"
            />
            <button
              type="button"
              onClick={addComment}
              disabled={commentBusy || !newComment.trim()}
              className="px-3 py-2 rounded-lg bg-cyan-700/60 text-cyan-100 text-xs disabled:opacity-40"
            >
              {commentBusy ? "Posting…" : "Post"}
            </button>
          </div>
          {comments.length === 0 ? (
            <p className="text-xs text-slate-500">No comments yet.</p>
          ) : (
            <div className="space-y-2 max-h-56 overflow-auto">
              {comments.map((c) => (
                <div key={c.id} className="rounded-lg bg-white/5 border border-white/10 p-2">
                  <p className="text-[10px] text-slate-500">
                    {c.author_email} · {new Date(c.created_at).toLocaleString()}
                  </p>
                  <p className="text-xs text-slate-200 whitespace-pre-wrap">{c.body}</p>
                  {c.mentions.length > 0 && (
                    <p className="text-[10px] text-fuchsia-300 mt-1">mentions: {c.mentions.join(", ")}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="glass p-4 rounded-xl border border-white/10">
          <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-2">Activity</h3>
          {activity.length === 0 ? (
            <p className="text-xs text-slate-500">No activity logged yet.</p>
          ) : (
            <div className="space-y-2 max-h-56 overflow-auto">
              {activity.map((a) => (
                <div key={a.id} className="rounded-lg bg-white/5 border border-white/10 p-2">
                  <p className="text-[10px] text-slate-300">{activityMessage(a)}</p>
                  <p className="text-[10px] text-slate-500">
                    {a.timestamp ? new Date(a.timestamp).toLocaleString() : "—"}
                  </p>
                  {a.user_id && (
                    <p className="text-[10px] text-slate-600">actor: {a.user_id}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Skipped-during-build details. Each row links the case title to
          the underlying reason (rate limit / parse error / etc.) so the
          user can decide to retry just those cases or fix prompts. */}
      {buildSkipped.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-4 p-3 rounded-xl border border-amber-400/30 bg-amber-500/5"
        >
          <div className="text-xs font-semibold text-amber-200 mb-2">
            {buildSkipped.length} case{buildSkipped.length === 1 ? "" : "s"} were
            skipped during the last build. Click <span className="text-cyan-300">Generate scripts</span> again
            to retry, or fix the underlying issue first.
          </div>
          <ul className="space-y-1">
            {buildSkipped.map((s) => (
              <li key={s.test_case_id} className="text-[11px] font-mono text-slate-200">
                <span className="text-amber-300">{s.title}</span>
                <span className="text-slate-500"> — </span>
                <span className="text-slate-300">{s.reason}</span>
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => setBuildSkipped([])}
            className="mt-2 text-[10px] text-slate-400 hover:text-slate-200 underline"
          >
            Dismiss
          </button>
        </motion.div>
      )}

      {/* Live run stream for a single test case. Reuses the same
          BulkExecutionStream component used by /runs so per-case Run
          shows the exact same per-test card the bulk view does. */}
      <AnimatePresence>
        {activeRun && (
          <motion.div
            key={activeRun.streamUrl}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mb-6 p-4 rounded-xl glass border border-cyan-500/30"
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-cyan-300">
                Running: <span className="text-white">{activeRun.tcTitle}</span>
              </h3>
              <button
                type="button"
                onClick={closeRun}
                className="px-3 py-1 text-xs rounded glass text-slate-300 hover:text-white"
              >
                Close
              </button>
            </div>
            <BulkExecutionStream
              streamUrl={activeRun.streamUrl}
              healContext={activeRun.runHealContext}
            />
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {editOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
            onClick={() => setEditOpen(false)}
          >
            <motion.div
              initial={{ scale: 0.95 }}
              animate={{ scale: 1 }}
              className="glass-strong p-6 w-full max-w-lg"
              onClick={(e) => e.stopPropagation()}
            >
              <h2 className="text-lg font-bold text-white mb-3">Update story</h2>
              <input
                className="w-full mb-3 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
              />
              <textarea
                className="w-full mb-4 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 min-h-[120px]"
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
              />
              <div className="flex gap-2">
                <button type="button" onClick={saveStory} className="flex-1 py-2 rounded-xl bg-purple-600 text-white text-sm font-semibold">
                  Save new version
                </button>
                <button type="button" onClick={() => setEditOpen(false)} className="px-4 py-2 glass text-slate-400 rounded-xl text-sm">
                  Cancel
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <h2 className="text-sm font-semibold text-cyan-400 uppercase tracking-wider mb-3">
        Test cases ({counts.total})
      </h2>
      {counts.total > 0 && (
        <div className="mb-3 flex items-center gap-2">
          <label className="inline-flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={allSelectableChecked}
              onChange={toggleSelectAll}
              className="accent-cyan-500"
            />
            Select all approved ({selectableIds.length})
          </label>
          {selectedCases.length > 0 && (
            <span className="text-xs text-cyan-300">{selectedCases.length} selected</span>
          )}
        </div>
      )}

      {tcs.length === 0 ? (
        <div className="glass p-6 text-sm text-slate-400 rounded-xl">
          No test cases yet. Click <span className="text-purple-300">Generate test cases</span> to create drafts from the
          story description. Drafts are saved automatically; you can approve, reject, or revise each one below.
        </div>
      ) : (
        <div className="space-y-3 mb-10">
          {tcs.map((t) => {
            const busy = !!statusBusy[t.id];
            const scriptShown = scriptOpen[t.id] !== undefined;
            const borderClass =
              t.status === "approved"
                ? "border-emerald-500/40"
                : t.status === "rejected"
                  ? "border-red-500/30 opacity-60"
                  : "border-white/10";
            return (
              <motion.div
                key={t.id}
                layout
                className={`glass p-4 border-2 rounded-xl ${borderClass}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3 mb-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <input
                        type="checkbox"
                        checked={selectedCases.includes(t.id)}
                        onChange={() => toggleSelectedCase(t.id)}
                        disabled={t.status !== "approved" || t.stale}
                        className="accent-cyan-500"
                        title={
                          t.status !== "approved" || t.stale
                            ? "Only approved, non-stale cases are selectable"
                            : "Select for bulk script generation"
                        }
                      />
                      <span className="text-[10px] text-slate-500">bulk</span>
                    </div>
                    <Link
                      href={`/test-cases/${encodeURIComponent(t.id)}${projectSlug ? `?project=${encodeURIComponent(projectSlug)}` : ""}`}
                      className="text-white text-sm font-semibold break-words hover:text-cyan-200"
                    >
                      {t.title}
                    </Link>
                    <div className="flex flex-wrap gap-1 mt-1">
                      <span
                        className={`text-[10px] px-2 py-0.5 rounded-full uppercase tracking-wide ${
                          t.status === "approved"
                            ? "bg-emerald-600/25 text-emerald-200"
                            : t.status === "rejected"
                              ? "bg-red-600/25 text-red-200"
                              : "bg-slate-700/50 text-slate-300"
                        }`}
                      >
                        {t.status}
                      </span>
                      {t.stale && (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-600/25 text-amber-200">
                          stale
                        </span>
                      )}
                      {t.tags.map((x) => (
                        <span
                          key={x}
                          className="text-[10px] px-2 py-0.5 rounded-full bg-purple-600/25 text-purple-200"
                        >
                          {x}
                        </span>
                      ))}
                      {t.script_path && (
                        <span
                          className="text-[10px] px-2 py-0.5 rounded-full bg-cyan-600/20 text-cyan-200"
                          title={t.script_path}
                        >
                          script ready
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {t.status !== "approved" && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setCaseStatus(t.id, "approved")}
                        className="px-2.5 py-1 text-xs rounded bg-emerald-600 text-white disabled:opacity-50"
                      >
                        Approve
                      </button>
                    )}
                    {t.status !== "rejected" && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setCaseStatus(t.id, "rejected")}
                        className="px-2.5 py-1 text-xs rounded border border-red-400/40 text-red-300 disabled:opacity-50"
                      >
                        Reject
                      </button>
                    )}
                    {t.status !== "draft" && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setCaseStatus(t.id, "draft")}
                        title="Move back to draft"
                        className="px-2.5 py-1 text-xs rounded glass text-slate-300 disabled:opacity-50"
                      >
                        Re-draft
                      </button>
                    )}
                    {t.script_path && (
                      <button
                        type="button"
                        disabled={!!scriptLoading[t.id]}
                        onClick={() => toggleScript(t.id)}
                        className="px-2.5 py-1 text-xs rounded bg-cyan-600/40 text-cyan-100 disabled:opacity-50"
                      >
                        {scriptLoading[t.id]
                          ? "Loading…"
                          : scriptShown
                            ? "Hide script"
                            : "View script"}
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={buildBusy[t.id] || t.status !== "approved" || t.stale}
                      onClick={() => buildSingleScript(t.id)}
                      title={
                        t.status !== "approved"
                          ? "Approve this test case first"
                          : t.stale
                            ? "Test case is stale; update/re-approve first"
                            : "Generate or refresh this case script"
                      }
                      className="px-2.5 py-1 text-xs rounded bg-cyan-700/60 text-cyan-100 disabled:opacity-40"
                    >
                      {buildBusy[t.id] ? "Generating…" : t.script_path ? "Re-generate script" : "Generate script"}
                    </button>
                    {/* Run button: only shown for script-ready cases.
                        Disabled while another run is live, so the
                        BulkExecutionStream isn't fighting two SSE
                        sources at once. */}
                    {t.script_path && (
                      <button
                        type="button"
                        disabled={!!activeRun || orgs.length === 0}
                        onClick={() => startRunForCase(t.id)}
                        title={
                          activeRun
                            ? "A run is already in progress -- close it first"
                            : orgs.length === 0
                              ? "No Salesforce orgs registered for this project"
                              : "Run this test case against a project org"
                        }
                        className="px-2.5 py-1 text-xs rounded bg-emerald-600 text-white disabled:opacity-40"
                      >
                        Run
                      </button>
                    )}
                  </div>
                </div>

                {/* Inline org-picker shown when this card's Run button was
                    clicked AND multiple orgs are registered. Single-org
                    projects skip the picker and run immediately. */}
                {orgPickerForCase === t.id && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    className="mt-3 p-3 rounded border border-cyan-500/30 bg-cyan-500/5"
                  >
                    <label className="block text-[11px] text-slate-400 mb-1">
                      Run on which org?
                    </label>
                    <div className="flex items-center gap-2">
                      <select
                        value={orgPickerSelected}
                        onChange={(e) => setOrgPickerSelected(e.target.value)}
                        className="flex-1 bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-slate-200"
                      >
                        {orgs.map((o) => (
                          <option key={o.id} value={o.id}>
                            {o.name || o.id}
                            {o.environment ? ` (${o.environment})` : ""}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => launchRun(t.id, orgPickerSelected)}
                        className="px-3 py-1 text-xs rounded bg-emerald-600 text-white"
                      >
                        Start
                      </button>
                      <button
                        type="button"
                        onClick={() => setOrgPickerForCase(null)}
                        className="px-3 py-1 text-xs rounded glass text-slate-300"
                      >
                        Cancel
                      </button>
                    </div>
                  </motion.div>
                )}

                {(t.preconditions || t.steps.length > 0 || t.expected_result) && (
                  <details className="mt-2 text-xs text-slate-300">
                    <summary className="cursor-pointer text-slate-500 hover:text-slate-300 select-none">
                      Steps ({t.steps.length}) / Expected
                    </summary>
                    <div className="mt-2 pl-2 space-y-2">
                      {t.preconditions && (
                        <div>
                          <span className="text-slate-500">Preconditions:</span>{" "}
                          <span className="text-slate-300 whitespace-pre-wrap">{t.preconditions}</span>
                        </div>
                      )}
                      {t.steps.length > 0 && (
                        <ol className="list-decimal pl-5 space-y-1">
                          {t.steps.map((s, i) => (
                            <li key={i} className="text-slate-300">{s}</li>
                          ))}
                        </ol>
                      )}
                      {t.expected_result && (
                        <div>
                          <span className="text-slate-500">Expected:</span>{" "}
                          <span className="text-slate-300 whitespace-pre-wrap">{t.expected_result}</span>
                        </div>
                      )}
                    </div>
                  </details>
                )}

                <AnimatePresence>
                  {scriptShown && (
                    <motion.pre
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      className="mt-3 bg-black/40 border border-cyan-900/40 rounded p-3 text-[11px] text-cyan-100 overflow-x-auto whitespace-pre"
                    >
                      {scriptOpen[t.id]}
                    </motion.pre>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })}
        </div>
      )}

      <AnimatePresence>
        {editAllOpen && (
          <EditTestCasesModal
            open={!!editAllOpen}
            storyId={id}
            storyTitle={story?.title ?? ""}
            existing={
              editAllOpen === "manual"
                ? []
                : tcs.map<TestCaseDraft>((t) => ({
                    id: t.id,
                    title: t.title,
                    steps: t.steps.length > 0 ? [...t.steps] : [""],
                    expected_result: t.expected_result,
                    preconditions: t.preconditions ?? "",
                    tags: [...t.tags],
                    status: t.status,
                    removed: false,
                    dirty: false,
                  }))
            }
            knownTags={knownTags}
            onClose={() => setEditAllOpen(null)}
            onSaved={() => {
              setMsg(
                editAllOpen === "manual"
                  ? "Test case created."
                  : "Saved changes.",
              );
              load();
              // Test cases changed; refresh sidebar's case tree under
              // this story.
              notifyTreeRefresh({ kind: "test_case", storyId: id });
            }}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
