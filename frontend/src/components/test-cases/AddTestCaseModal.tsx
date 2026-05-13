"use client";

/**
 * AddTestCaseModal
 *
 * Two-step picker that lets users add a test case from the project hub
 * (`/projects/[name]`), where there isn't a single obvious parent
 * story. Keeps the actual case authoring on `/user-stories/[id]`
 * (where `EditTestCasesModal` already lives) -- this component is just
 * the "which story?" pre-step.
 *
 * Flow:
 *   1. List the project's stories. User picks one (or clicks the
 *      sticky **"+ Create new story..."** option to author a new one
 *      via `CreateStoryModal` stacked).
 *   2. Navigate to `/user-stories/[id]?addCase=1` so the story page
 *      auto-opens its existing `EditTestCasesModal` for case authoring.
 *
 * Test cases require a parent `user_story_id` per the backend schema,
 * so this two-step approach keeps creation deterministic without
 * duplicating the case-authoring UI.
 */

import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import CreateStoryModal, { type CreatedStory } from "@/components/user-stories/CreateStoryModal";

type StoryRow = {
  id: string;
  title: string;
  status: string;
  version: number;
};

interface Props {
  open: boolean;
  onClose: () => void;
  /** Portal UUID for the project we're adding the test case under. */
  projectId: string;
}

const NEW_STORY_SENTINEL = "__new_story__";

export default function AddTestCaseModal({ open, onClose, projectId }: Props) {
  const router = useRouter();
  const [stories, setStories] = useState<StoryRow[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [stackedCreateStory, setStackedCreateStory] = useState(false);

  useEffect(() => {
    if (!open || !projectId) return;
    setSearch("");
    setError("");
    setLoading(true);
    api.userStories
      .list(projectId)
      .then((rows) => {
        // Active stories only -- archived rows are versioning detritus
        // and shouldn't be picker targets.
        setStories((rows as StoryRow[]).filter((s) => s.status === "active"));
      })
      .catch(() => setStories([]))
      .finally(() => setLoading(false));
  }, [open, projectId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return stories;
    return stories.filter((s) => s.title.toLowerCase().includes(q));
  }, [stories, search]);

  const goToStory = (storyId: string) => {
    onClose();
    // The story page reads ?addCase=1 and auto-opens its existing
    // EditTestCasesModal -- centralised authoring stays in one place.
    router.push(`/user-stories/${encodeURIComponent(storyId)}?addCase=1`);
  };

  const handleStoryCreated = (story: CreatedStory) => {
    // We deliberately set redirectAfterCreate=false on CreateStoryModal
    // so we land here with the new story id and can append ?addCase=1
    // ourselves.
    goToStory(story.id);
  };

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
              <h2 className="text-xl font-bold text-white mb-1">Add test case</h2>
              <p className="text-xs text-slate-500 mb-4">
                Pick the story this test case belongs to. The case author opens on the next step.
              </p>

              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search stories…"
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500 mb-3"
              />

              <div className="max-h-72 overflow-y-auto space-y-1.5">
                {/* Sticky "+ Create new story..." option at the top. */}
                <button
                  type="button"
                  onClick={() => setStackedCreateStory(true)}
                  className="w-full text-left px-3 py-2 rounded-lg border border-purple-400/30 bg-purple-500/5 hover:bg-purple-500/15 text-purple-200 text-sm"
                >
                  + Create new story…
                </button>

                {loading ? (
                  <p className="text-xs text-slate-500 px-1">Loading stories…</p>
                ) : filtered.length === 0 ? (
                  <p className="text-xs text-slate-500 px-1">
                    {stories.length === 0
                      ? "No stories in this project yet. Use 'Create new story…' above."
                      : "No stories match that search."}
                  </p>
                ) : (
                  filtered.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => goToStory(s.id)}
                      data-story-id={NEW_STORY_SENTINEL /* harmless; just satisfies any future ref */}
                      className="w-full text-left px-3 py-2 rounded-lg border border-white/5 hover:border-white/15 hover:bg-white/5 transition-colors"
                    >
                      <div className="text-sm text-slate-100 truncate">{s.title}</div>
                      <div className="text-[10px] text-slate-500">
                        v{s.version} · {s.status}
                      </div>
                    </button>
                  ))
                )}
              </div>

              {error && <p className="text-red-400 text-xs mt-3">{error}</p>}

              <div className="flex justify-end mt-5">
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
      <CreateStoryModal
        open={stackedCreateStory}
        onClose={() => setStackedCreateStory(false)}
        projectId={projectId}
        redirectAfterCreate={false}
        onCreated={handleStoryCreated}
      />
    </>
  );
}
