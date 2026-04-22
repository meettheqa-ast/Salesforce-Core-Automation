"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";
import AnimatedCard from "@/components/cards/AnimatedCard";

export default function UserStoriesPage() {
  const [projects, setProjects] = useState<string[]>([]);
  const [projectName, setProjectName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [stories, setStories] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.projects.list().then(setProjects).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!projectName) {
      void Promise.resolve().then(() => {
        setProjectId("");
        setStories([]);
      });
      return;
    }
    api.projects.portalProjectId(projectName).then((r) => {
      setProjectId(r.project_id);
      return api.userStories.list(r.project_id);
    }).then(setStories).catch(() => setStories([]));
  }, [projectName]);

  const createStory = async () => {
    if (!projectId || !title.trim()) {
      setErr("Select a project and enter a title.");
      return;
    }
    setCreating(true);
    setErr("");
    try {
      const s = await api.userStories.create({
        project_id: projectId,
        title: title.trim(),
        description: description.trim(),
      });
      setTitle("");
      setDescription("");
      window.location.href = `/user-stories/${encodeURIComponent(s.id)}`;
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Create failed");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-8">
        <h1 className="text-4xl font-bold mb-2">
          <span className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text text-transparent">
            User Stories
          </span>
        </h1>
        <p className="text-slate-400">Author stories, generate structured test cases, approve, and run.</p>
      </motion.div>

      <div className="grid md:grid-cols-2 gap-6 mb-10">
        <AnimatedCard glow="purple" className="p-5">
          <h2 className="text-lg font-semibold text-white mb-4">New story</h2>
          <div className="space-y-3">
            <GlassSelect
              className="w-full"
              value={projectName}
              placeholder="Project…"
              onChange={setProjectName}
              options={[{ value: "", label: "Select project…" }, ...projects.map((p) => ({ value: p, label: p }))]}
            />
            <input
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200"
              placeholder="Title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <textarea
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 min-h-[100px]"
              placeholder="Description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
            {err && <p className="text-red-400 text-xs">{err}</p>}
            <button
              type="button"
              disabled={creating || !projectId}
              onClick={createStory}
              className="w-full py-2.5 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-40"
            >
              {creating ? "Creating…" : "Create & open"}
            </button>
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

      {loading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Stories</h2>
          {stories.length === 0 ? (
            <p className="text-slate-500 text-sm">No active stories for this project.</p>
          ) : (
            stories.map((s) => (
              <Link key={s.id} href={`/user-stories/${encodeURIComponent(s.id)}`}>
                <motion.div
                  whileHover={{ scale: 1.01 }}
                  className="glass p-4 flex items-center justify-between cursor-pointer border border-white/5 hover:border-purple-500/30"
                >
                  <div>
                    <div className="font-semibold text-white">{s.title}</div>
                    <div className="text-xs text-slate-500">v{s.version} · {s.status}</div>
                  </div>
                  <span className="text-purple-400 text-sm">Open →</span>
                </motion.div>
              </Link>
            ))
          )}
        </div>
      )}
    </div>
  );
}
