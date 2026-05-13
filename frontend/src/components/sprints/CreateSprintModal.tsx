"use client";

/**
 * CreateSprintModal
 *
 * Reusable creation surface for new sprints. Lifted from the inline
 * `+ New sprint` card on `/sprints` so we can place "+ New sprint"
 * buttons on the project detail page, dashboard, top-nav Quick create
 * menu, etc.
 *
 * The project picker has a sticky **"+ Create new project..."** option
 * at the top that opens `CreateProjectModal` stacked, then auto-selects
 * the new project. This lets users go from "I have a fresh idea" to "I
 * have a sprint in a brand new project" in one flow without bouncing
 * between pages.
 */

import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import GlassSelect from "@/components/ui/GlassSelect";
import { api } from "@/lib/api";
import CreateProjectModal, { type CreatedProject } from "@/components/projects/CreateProjectModal";

export type CreatedSprint = {
  id: string;
  project_id: string;
  name: string;
  state: "planned" | "active" | "completed" | "cancelled";
};

interface Props {
  open: boolean;
  onClose: () => void;
  /** When set, the project picker is locked to this project and not
   *  shown -- callers like /projects/[name] already know the context. */
  projectId?: string;
  onCreated?: (sprint: CreatedSprint) => void;
}

const NEW_PROJECT_SENTINEL = "__new_project__";
const EMPTY_FORM = { name: "", goal: "", start_date: "", end_date: "" };

export default function CreateSprintModal({ open, onClose, projectId, onCreated }: Props) {
  const [projects, setProjects] = useState<string[]>([]);
  const [projectName, setProjectName] = useState("");
  // Resolved UUID. Mirrors the slug -> portal id resolution that
  // /sprints already does.
  const [resolvedProjectId, setResolvedProjectId] = useState(projectId || "");
  const [form, setForm] = useState(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [stackedCreateProject, setStackedCreateProject] = useState(false);

  const projectLocked = !!projectId;

  // Reset state on open. When projectId is locked we don't fetch the
  // project list -- we just trust the caller's context.
  useEffect(() => {
    if (!open) return;
    setForm(EMPTY_FORM);
    setError("");
    if (projectLocked) {
      setResolvedProjectId(projectId!);
      setProjectName("");
      return;
    }
    setResolvedProjectId("");
    setProjectName("");
    api.projects
      .list()
      .then(setProjects)
      .catch(() => setProjects([]));
  }, [open, projectId, projectLocked]);

  // Slug -> portal UUID lookup, mirroring /sprints/page.tsx.
  useEffect(() => {
    if (projectLocked || !projectName || projectName === NEW_PROJECT_SENTINEL) return;
    api.projects
      .portalProjectId(projectName)
      .then((r) => setResolvedProjectId(r.project_id))
      .catch(() => setResolvedProjectId(""));
  }, [projectName, projectLocked]);

  const handleProjectChange = (value: string) => {
    if (value === NEW_PROJECT_SENTINEL) {
      setStackedCreateProject(true);
      return;
    }
    setProjectName(value);
  };

  const handleNewProjectCreated = (created: CreatedProject) => {
    // Refresh the list and snap the picker to the new project.
    api.projects
      .list()
      .then((all) => {
        setProjects(all);
        setProjectName(created.name);
        setResolvedProjectId(created.project_id);
      })
      .catch(() => {
        // Fallback: at least seed the picker with the new value.
        setProjectName(created.name);
        setResolvedProjectId(created.project_id);
      });
  };

  const handleCreate = async () => {
    if (!resolvedProjectId) {
      setError("Pick a project first.");
      return;
    }
    if (!form.name.trim()) {
      setError("Sprint name is required.");
      return;
    }
    setCreating(true);
    setError("");
    try {
      const sprint = (await api.sprints.create({
        project_id: resolvedProjectId,
        name: form.name.trim(),
        goal: form.goal.trim() || null,
        state: "planned",
        start_date: form.start_date || null,
        end_date: form.end_date || null,
      })) as CreatedSprint;
      onCreated?.(sprint);
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not create sprint");
    } finally {
      setCreating(false);
    }
  };

  const projectOptions = useMemo(
    () => [
      { value: "", label: "Select a project…" },
      // Sticky "+ Create new project..." option at the top of the list.
      // Selecting it pops `CreateProjectModal` stacked on top so the user
      // can create-and-pick in one motion.
      { value: NEW_PROJECT_SENTINEL, label: "+ Create new project..." },
      ...projects.map((p) => ({ value: p, label: p })),
    ],
    [projects],
  );

  return (
    <>
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
              className="glass-strong p-6 w-full max-w-lg"
              onClick={(e) => e.stopPropagation()}
            >
              <h2 className="text-xl font-bold text-white mb-4">New sprint</h2>
              <div className="space-y-3">
                {!projectLocked && (
                  <GlassSelect
                    className="w-full"
                    value={projectName}
                    placeholder="Select a project…"
                    onChange={handleProjectChange}
                    options={projectOptions}
                  />
                )}
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">Name</span>
                  <input
                    value={form.name}
                    onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                    placeholder="e.g. Sprint 23"
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  />
                </label>
                <label className="block">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">Goal (optional)</span>
                  <input
                    value={form.goal}
                    onChange={(e) => setForm((f) => ({ ...f, goal: e.target.value }))}
                    placeholder="One-line objective"
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  />
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-[10px] uppercase tracking-wider text-slate-500">Start date (optional)</span>
                    <input
                      type="date"
                      value={form.start_date}
                      onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))}
                      className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                    />
                  </label>
                  <label className="block">
                    <span className="text-[10px] uppercase tracking-wider text-slate-500">End date (optional)</span>
                    <input
                      type="date"
                      value={form.end_date}
                      onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))}
                      className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                    />
                  </label>
                </div>
              </div>
              {error && <p className="text-red-400 text-xs mt-3">{error}</p>}
              <div className="flex gap-3 mt-5">
                <motion.button
                  whileTap={{ scale: 0.95 }}
                  onClick={handleCreate}
                  disabled={creating || !resolvedProjectId || !form.name.trim()}
                  className="flex-1 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm disabled:opacity-50"
                >
                  {creating ? "Creating…" : "Create sprint"}
                </motion.button>
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
      <CreateProjectModal
        open={stackedCreateProject}
        onClose={() => setStackedCreateProject(false)}
        onCreated={handleNewProjectCreated}
      />
    </>
  );
}
