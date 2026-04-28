"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";
import EditTestCasesModal, { type TestCaseDraft } from "@/components/test-cases/EditTestCasesModal";

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

export default function UserStoryDetailPage() {
  const params = useParams();
  const id = decodeURIComponent(params.id as string);

  const [story, setStory] = useState<any>(null);
  const [tcs, setTcs] = useState<TestCaseRow[]>([]);
  const [genLoading, setGenLoading] = useState(false);
  const [buildLoading, setBuildLoading] = useState(false);
  const [statusBusy, setStatusBusy] = useState<Record<string, boolean>>({});
  const [scriptOpen, setScriptOpen] = useState<Record<string, string>>({});
  const [scriptLoading, setScriptLoading] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");

  // Bulk edit / add modal state. `editAllOpen` carries an "openMode"
  // because the same modal serves both flows: "manual" pre-loads one
  // blank case, "edit-all" pre-loads every existing case.
  const [editAllOpen, setEditAllOpen] = useState<"manual" | "edit-all" | null>(null);
  const [knownTags, setKnownTags] = useState<string[]>([]);
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
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const counts = useMemo(() => {
    const draft = tcs.filter((t) => t.status === "draft" && !t.stale).length;
    const approved = tcs.filter((t) => t.status === "approved" && !t.stale).length;
    const rejected = tcs.filter((t) => t.status === "rejected").length;
    const stale = tcs.filter((t) => t.stale).length;
    const built = tcs.filter((t) => !!t.script_path).length;
    return { draft, approved, rejected, stale, built, total: tcs.length };
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
    try {
      const res = await api.userStories.buildScripts(id);
      const fresh = await api.testCases.list(id);
      setTcs(fresh);
      const skippedNote = res.skipped.length ? ` ${res.skipped.length} skipped.` : "";
      setMsg(`Built ${res.built.length} script(s) under ${res.output_dir}.${skippedNote}`);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Build failed");
    } finally {
      setBuildLoading(false);
    }
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
      window.location.href = `/user-stories/${encodeURIComponent(r.new_story.id)}`;
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Update failed");
    }
  };

  if (!story) {
    return (
      <div className="max-w-6xl mx-auto px-6 py-20 text-slate-500 text-sm">
        <Link href="/user-stories" className="text-purple-400 hover:underline">← Back</Link>
        <p className="mt-4">Story not found or still loading…</p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <Link href="/user-stories" className="text-sm text-slate-500 hover:text-purple-400 mb-4 inline-block">
        ← All stories
      </Link>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-white mb-1">{story.title}</h1>
            <p className="text-slate-400 whitespace-pre-wrap">{story.description}</p>
            <div className="flex flex-wrap gap-2 mt-3 items-center">
              <span className="text-xs px-2 py-0.5 rounded bg-purple-600/30 text-purple-200">v{story.version}</span>
              <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300">{story.status}</span>
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
          </div>
        </div>
      </motion.div>

      {msg && <p className="text-emerald-400 text-sm mb-3">{msg}</p>}
      {err && <p className="text-red-400 text-sm mb-3">{err}</p>}

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
                    <p className="text-white text-sm font-semibold break-words">
                      {t.title}
                    </p>
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
                  </div>
                </div>

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
            }}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
