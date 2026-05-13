"use client";

/**
 * AddStoryToSprintModal
 *
 * Two-tab modal used from `/sprints/[id]`. Replaces the obsolete
 * "Open a story and assign it from there" copy that used to live on
 * the sprint detail page.
 *
 *   1. **Create new** -- inline form (title + description). Posts to
 *      `POST /user-stories` with `sprint_id` set so the new story
 *      lands in this sprint immediately. Backend now honors `sprint_id`
 *      end-to-end (recent bug fix); previously the field was silently
 *      dropped and the story fell to backlog.
 *
 *   2. **Assign existing** -- searchable list of every backlog story
 *      in this project (sprint_id == null). Multi-select with
 *      checkboxes; "Assign N stories" bulk-calls
 *      `api.sprints.assignStory` for each pick.
 *
 * Both tabs share a single modal frame so the user can flip between
 * them without remounting.
 */

import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";

type StoryRow = {
  id: string;
  title: string;
  status: string;
  version: number;
  sprint_id: string | null;
};

interface Props {
  open: boolean;
  onClose: () => void;
  sprintId: string;
  projectId: string;
  /** Fired after either tab finishes a successful action so the caller
   *  can reload the sprint's stories list. */
  onChanged?: () => void;
}

type Tab = "create" | "assign";

export default function AddStoryToSprintModal({
  open,
  onClose,
  sprintId,
  projectId,
  onChanged,
}: Props) {
  const [tab, setTab] = useState<Tab>("create");

  // Create-tab state
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);

  // Assign-tab state
  const [backlog, setBacklog] = useState<StoryRow[]>([]);
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [assigning, setAssigning] = useState(false);

  const [error, setError] = useState("");

  // Reset whenever the modal opens, so a previous session doesn't bleed.
  useEffect(() => {
    if (!open) return;
    setTab("create");
    setTitle("");
    setDescription("");
    setSearch("");
    setPicked(new Set());
    setError("");
  }, [open]);

  // Lazy-load backlog stories the first time we hit the Assign tab.
  // Filter to active+backlog (sprint_id null) so we never surface
  // stories already in another sprint -- moving across sprints is
  // still possible from the story detail page's sprint chip.
  useEffect(() => {
    if (!open || tab !== "assign" || !projectId) return;
    setLoading(true);
    api.userStories
      .list(projectId)
      .then((rows) => {
        const list = (rows as StoryRow[]).filter(
          (s) => !s.sprint_id && s.status === "active",
        );
        setBacklog(list);
      })
      .catch(() => setBacklog([]))
      .finally(() => setLoading(false));
  }, [open, tab, projectId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return backlog;
    return backlog.filter((s) => s.title.toLowerCase().includes(q));
  }, [backlog, search]);

  const toggle = (id: string) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleCreate = async () => {
    if (!title.trim()) {
      setError("Title is required.");
      return;
    }
    setCreating(true);
    setError("");
    try {
      await api.userStories.create({
        project_id: projectId,
        title: title.trim(),
        description: description.trim(),
        sprint_id: sprintId,
      });
      onChanged?.();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not create story");
    } finally {
      setCreating(false);
    }
  };

  const handleAssign = async () => {
    if (picked.size === 0) {
      setError("Pick at least one story.");
      return;
    }
    setAssigning(true);
    setError("");
    try {
      // Sequential, not parallel, so a partial-failure surfaces a
      // useful error instead of swallowing some assigns silently.
      const failures: string[] = [];
      for (const storyId of picked) {
        try {
          await api.sprints.assignStory(sprintId, storyId);
        } catch (err: unknown) {
          failures.push(err instanceof Error ? err.message : "unknown error");
        }
      }
      if (failures.length === picked.size) {
        setError(`Could not assign any stories. First error: ${failures[0]}`);
        return;
      }
      onChanged?.();
      onClose();
      if (failures.length > 0) {
        // Soft warning -- the modal is already closed; surface a
        // browser alert so users notice the partial failure.
        alert(`${failures.length} of ${picked.size} assigns failed: ${failures[0]}`);
      }
    } finally {
      setAssigning(false);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={onClose}
        >
          <motion.div
            initial={{ scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.9, opacity: 0 }}
            className="glass-strong p-6 w-full max-w-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-xl font-bold text-white mb-3">Add story to sprint</h2>

            <div className="flex gap-1 mb-4 p-1 rounded-lg bg-white/5 border border-white/10">
              {(
                [
                  { id: "create", label: "Create new" },
                  { id: "assign", label: "Assign existing" },
                ] as const
              ).map((t) => {
                const active = tab === t.id;
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => {
                      setTab(t.id);
                      setError("");
                    }}
                    className={`flex-1 px-3 py-1.5 rounded text-xs font-semibold transition-colors ${
                      active
                        ? "bg-gradient-to-r from-purple-600/40 to-cyan-500/40 text-white"
                        : "text-slate-400 hover:text-white"
                    }`}
                  >
                    {t.label}
                  </button>
                );
              })}
            </div>

            {tab === "create" ? (
              <div className="space-y-3">
                <input
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  placeholder="Title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
                <textarea
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 min-h-[120px] outline-none focus:border-purple-500"
                  placeholder="Description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
                <p className="text-[10px] text-slate-500">
                  This story will be created directly in the current sprint.
                </p>
              </div>
            ) : (
              <div>
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search backlog…"
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500 mb-3"
                />
                <div className="max-h-72 overflow-y-auto space-y-1.5">
                  {loading ? (
                    <p className="text-xs text-slate-500 px-1">Loading backlog…</p>
                  ) : filtered.length === 0 ? (
                    <p className="text-xs text-slate-500 px-1">
                      {backlog.length === 0
                        ? "No backlog stories in this project. Switch to 'Create new' to author one."
                        : "No backlog stories match that search."}
                    </p>
                  ) : (
                    filtered.map((s) => {
                      const isPicked = picked.has(s.id);
                      return (
                        <button
                          key={s.id}
                          type="button"
                          onClick={() => toggle(s.id)}
                          className={`w-full text-left px-3 py-2 rounded-lg border transition-colors ${
                            isPicked
                              ? "border-purple-500/40 bg-purple-500/10"
                              : "border-white/5 hover:border-white/15 hover:bg-white/5"
                          }`}
                        >
                          <div className="flex items-center gap-3">
                            <input
                              type="checkbox"
                              checked={isPicked}
                              readOnly
                              className="accent-purple-500"
                            />
                            <div className="min-w-0 flex-1">
                              <div className="text-sm text-slate-100 truncate">{s.title}</div>
                              <div className="text-[10px] text-slate-500">
                                v{s.version} · {s.status}
                              </div>
                            </div>
                          </div>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            )}

            {error && <p className="text-red-400 text-xs mt-3">{error}</p>}

            <div className="flex gap-3 mt-5">
              {tab === "create" ? (
                <motion.button
                  whileTap={{ scale: 0.95 }}
                  onClick={handleCreate}
                  disabled={creating || !title.trim()}
                  className="flex-1 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm disabled:opacity-50"
                >
                  {creating ? "Creating…" : "Create story in sprint"}
                </motion.button>
              ) : (
                <motion.button
                  whileTap={{ scale: 0.95 }}
                  onClick={handleAssign}
                  disabled={assigning || picked.size === 0}
                  className="flex-1 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm disabled:opacity-50"
                >
                  {assigning
                    ? "Assigning…"
                    : `Assign ${picked.size || ""} ${picked.size === 1 ? "story" : "stories"}`.trim()}
                </motion.button>
              )}
              <button
                onClick={onClose}
                className="px-4 py-2.5 glass text-slate-400 rounded-xl text-sm hover:text-white transition-colors"
              >
                Cancel
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
