"use client";

/**
 * CreateStoryModal
 *
 * Reusable creation surface for new user stories. Lifted from the
 * inline "New story" card on `/user-stories` so we can place
 * "+ New story" buttons on /projects/[name], /sprints/[id]
 * (Create new tab inside `AddStoryToSprintModal`), the dashboard,
 * and the top-nav Quick create menu.
 *
 * Both pickers (project + sprint) expose **"+ Create new..."** entries
 * at the top so users never have to leave the modal to scaffold parents.
 *
 * The handler now honors `sprint_id` end-to-end (a recent backend bug
 * fix); previously the API silently dropped the field and every
 * "create-and-assign-to-sprint" intent fell to the backlog.
 */

import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useRouter } from "next/navigation";
import GlassSelect from "@/components/ui/GlassSelect";
import { api } from "@/lib/api";
import CreateProjectModal, { type CreatedProject } from "@/components/projects/CreateProjectModal";
import CreateSprintModal, { type CreatedSprint } from "@/components/sprints/CreateSprintModal";

export type CreatedStory = {
  id: string;
  project_id: string;
  title: string;
  description: string;
  sprint_id: string | null;
};

interface SprintOption {
  id: string;
  name: string;
  state: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** Pre-selects + locks the project picker. Useful from /projects/[name]. */
  projectId?: string;
  /** Pre-selects + locks the sprint picker. Useful from /sprints/[id]. */
  sprintId?: string;
  /** Default true: navigate to the new story's detail page after create.
   *  Set false when the caller wants to receive the created story (e.g.
   *  AddTestCaseModal which then routes to ?addCase=1 itself). */
  redirectAfterCreate?: boolean;
  onCreated?: (story: CreatedStory) => void;
}

const NEW_PROJECT_SENTINEL = "__new_project__";
const NEW_SPRINT_SENTINEL = "__new_sprint__";

export default function CreateStoryModal({
  open,
  onClose,
  projectId,
  sprintId,
  redirectAfterCreate = true,
  onCreated,
}: Props) {
  const router = useRouter();
  const [projects, setProjects] = useState<string[]>([]);
  const [projectName, setProjectName] = useState("");
  const [resolvedProjectId, setResolvedProjectId] = useState(projectId || "");
  const [sprints, setSprints] = useState<SprintOption[]>([]);
  const [selectedSprintId, setSelectedSprintId] = useState(sprintId || "");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [stackedCreateProject, setStackedCreateProject] = useState(false);
  const [stackedCreateSprint, setStackedCreateSprint] = useState(false);

  const projectLocked = !!projectId;
  const sprintLocked = !!sprintId;

  useEffect(() => {
    if (!open) return;
    setTitle("");
    setDescription("");
    setError("");
    if (projectLocked) {
      setResolvedProjectId(projectId!);
      setProjectName("");
    } else {
      setResolvedProjectId("");
      setProjectName("");
      api.projects
        .list()
        .then(setProjects)
        .catch(() => setProjects([]));
    }
    setSelectedSprintId(sprintId || "");
  }, [open, projectId, sprintId, projectLocked]);

  useEffect(() => {
    if (projectLocked || !projectName || projectName === NEW_PROJECT_SENTINEL) return;
    api.projects
      .portalProjectId(projectName)
      .then((r) => setResolvedProjectId(r.project_id))
      .catch(() => setResolvedProjectId(""));
  }, [projectName, projectLocked]);

  // Load sprint options whenever the resolved project id changes (or on
  // initial mount if the project is locked from props). Only show
  // active/planned -- you can't drop a fresh story into a completed
  // sprint by accident, and that matches the existing /user-stories
  // behaviour.
  useEffect(() => {
    if (!resolvedProjectId) {
      setSprints([]);
      return;
    }
    if (sprintLocked) return;
    api.sprints
      .list(resolvedProjectId)
      .then((rows) =>
        setSprints(
          (rows as SprintOption[]).filter((s) => s.state === "active" || s.state === "planned"),
        ),
      )
      .catch(() => setSprints([]));
  }, [resolvedProjectId, sprintLocked]);

  const handleProjectChange = (value: string) => {
    if (value === NEW_PROJECT_SENTINEL) {
      setStackedCreateProject(true);
      return;
    }
    setProjectName(value);
    setSelectedSprintId(""); // sprint list refreshes on project change
  };

  const handleSprintChange = (value: string) => {
    if (value === NEW_SPRINT_SENTINEL) {
      setStackedCreateSprint(true);
      return;
    }
    setSelectedSprintId(value);
  };

  const handleNewProjectCreated = (created: CreatedProject) => {
    api.projects
      .list()
      .then((all) => {
        setProjects(all);
        setProjectName(created.name);
        setResolvedProjectId(created.project_id);
      })
      .catch(() => {
        setProjectName(created.name);
        setResolvedProjectId(created.project_id);
      });
  };

  const handleNewSprintCreated = (created: CreatedSprint) => {
    // Slot it into the picker and select it. We only filtered by
    // active/planned, but a brand-new sprint defaults to planned.
    setSprints((prev) => [{ id: created.id, name: created.name, state: created.state }, ...prev]);
    setSelectedSprintId(created.id);
  };

  const handleCreate = async () => {
    if (!resolvedProjectId) {
      setError("Pick a project first.");
      return;
    }
    if (!title.trim()) {
      setError("Title is required.");
      return;
    }
    setCreating(true);
    setError("");
    try {
      const story = (await api.userStories.create({
        project_id: resolvedProjectId,
        title: title.trim(),
        description: description.trim(),
        sprint_id: selectedSprintId || undefined,
      })) as CreatedStory;
      onCreated?.(story);
      onClose();
      if (redirectAfterCreate) {
        router.push(`/user-stories/${encodeURIComponent(story.id)}`);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not create story");
    } finally {
      setCreating(false);
    }
  };

  const projectOptions = useMemo(
    () => [
      { value: "", label: "Select a project…" },
      { value: NEW_PROJECT_SENTINEL, label: "+ Create new project..." },
      ...projects.map((p) => ({ value: p, label: p })),
    ],
    [projects],
  );

  const sprintOptions = useMemo(
    () => [
      { value: "", label: "No sprint (backlog)" },
      ...(resolvedProjectId
        ? [{ value: NEW_SPRINT_SENTINEL, label: "+ Create new sprint..." }]
        : []),
      ...sprints.map((s) => ({ value: s.id, label: `${s.name} (${s.state})` })),
    ],
    [sprints, resolvedProjectId],
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
              <h2 className="text-xl font-bold text-white mb-4">New story</h2>
              <div className="space-y-3">
                {!projectLocked && (
                  <GlassSelect
                    className="w-full"
                    value={projectName}
                    placeholder="Project…"
                    onChange={handleProjectChange}
                    options={projectOptions}
                  />
                )}
                {!sprintLocked && (
                  <GlassSelect
                    className="w-full"
                    value={selectedSprintId}
                    placeholder="Sprint (optional)"
                    onChange={handleSprintChange}
                    disabled={!resolvedProjectId}
                    options={sprintOptions}
                  />
                )}
                <input
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  placeholder="Title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
                <textarea
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 min-h-[100px] outline-none focus:border-purple-500"
                  placeholder="Description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              {error && <p className="text-red-400 text-xs mt-3">{error}</p>}
              <div className="flex gap-3 mt-5">
                <motion.button
                  whileTap={{ scale: 0.95 }}
                  onClick={handleCreate}
                  disabled={creating || !resolvedProjectId || !title.trim()}
                  className="flex-1 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm disabled:opacity-50"
                >
                  {creating ? "Creating…" : redirectAfterCreate ? "Create & open" : "Create story"}
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
      <CreateSprintModal
        open={stackedCreateSprint}
        onClose={() => setStackedCreateSprint(false)}
        projectId={resolvedProjectId || undefined}
        onCreated={handleNewSprintCreated}
      />
    </>
  );
}
