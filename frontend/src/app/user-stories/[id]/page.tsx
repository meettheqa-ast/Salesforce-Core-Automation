"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";

type CardState = "pending" | "approved" | "rejected";

type ReviewCard = {
  test_case_id: string;
  title: string;
  steps: string[];
  expected_result: string;
  preconditions: string;
  tags: string[];
  state: CardState;
};

export default function UserStoryDetailPage() {
  const params = useParams();
  const id = decodeURIComponent(params.id as string);

  const [story, setStory] = useState<any>(null);
  const [tcs, setTcs] = useState<any[]>([]);
  const [tags, setTags] = useState<any[]>([]);
  const [genLoading, setGenLoading] = useState(false);
  const [cards, setCards] = useState<ReviewCard[]>([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");

  const load = useCallback(() => {
    api.userStories.get(id).then(setStory).catch(() => setStory(null));
    api.testCases.list(id).then(setTcs).catch(() => setTcs([]));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!story?.project_id) return;
    api.tags.list(story.project_id).then(setTags).catch(() => setTags([]));
  }, [story?.project_id]);

  const generate = async () => {
    setGenLoading(true);
    setErr("");
    setMsg("");
    try {
      const res = await api.userStories.generate(id);
      const next: ReviewCard[] = (res.generated || []).map((g: any) => ({
        test_case_id: crypto.randomUUID(),
        title: g.title,
        steps: [...(g.steps || [])],
        expected_result: g.expected_result || "",
        preconditions: g.preconditions || "",
        tags: [...(g.suggested_tags || [])],
        state: "pending" as CardState,
      }));
      setCards(next);
      setMsg(`Generated ${next.length} draft test case(s). Review and approve below.`);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setGenLoading(false);
    }
  };

  const submitApproved = async () => {
    const approved = cards.filter((c) => c.state === "approved");
    if (!approved.length) return;
    setErr("");
    try {
      await api.testCases.batchApprove({
        user_story_id: id,
        approved: approved.map((c) => ({
          test_case_id: c.test_case_id,
          title: c.title,
          steps: c.steps,
          expected_result: c.expected_result,
          preconditions: c.preconditions || null,
          tags: c.tags,
        })),
      });
      setCards([]);
      setMsg("Saved approved test cases.");
      load();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Save failed");
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

  const staleCount = tcs.filter((t) => t.stale).length;
  const approvedCount = cards.filter((c) => c.state === "approved").length;

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
            <div className="flex gap-2 mt-3">
              <span className="text-xs px-2 py-0.5 rounded bg-purple-600/30 text-purple-200">v{story.version}</span>
              <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300">{story.status}</span>
              {staleCount > 0 && (
                <span className="text-xs px-2 py-0.5 rounded bg-amber-600/30 text-amber-200" title="Parent story was updated">
                  {staleCount} stale TC(s)
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
              disabled={genLoading}
              onClick={generate}
              className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50"
            >
              {genLoading ? "Generating…" : "Generate test cases"}
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

      {cards.length > 0 && (
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-cyan-400 uppercase tracking-wider">Review drafts</h2>
          <button
            type="button"
            disabled={!approvedCount}
            onClick={submitApproved}
            className="px-4 py-2 rounded-xl bg-emerald-600 text-white text-sm font-semibold disabled:opacity-40"
          >
            Submit approved ({approvedCount})
          </button>
        </div>
      )}

      <div className="space-y-4 mb-10">
        {cards.map((c, idx) => (
          <motion.div
            key={c.test_case_id}
            layout
            className={`glass p-4 border-2 rounded-xl ${
              c.state === "approved"
                ? "border-emerald-500/50"
                : c.state === "rejected"
                  ? "border-red-500/30 opacity-60 line-through"
                  : "border-white/10"
            }`}
          >
            <div className="flex justify-between gap-2 mb-2">
              <input
                className="flex-1 bg-white/5 border border-white/10 rounded px-2 py-1 text-sm text-white font-semibold"
                value={c.title}
                onChange={(e) => {
                  const n = [...cards];
                  n[idx] = { ...c, title: e.target.value };
                  setCards(n);
                }}
              />
              <div className="flex gap-1">
                <button
                  type="button"
                  className="px-2 py-1 text-xs rounded border border-red-400/50 text-red-300"
                  onClick={() => {
                    const n = [...cards];
                    n[idx] = { ...c, state: "rejected" };
                    setCards(n);
                  }}
                >
                  Reject
                </button>
                <button
                  type="button"
                  className="px-2 py-1 text-xs rounded bg-emerald-600 text-white"
                  onClick={() => {
                    const n = [...cards];
                    n[idx] = { ...c, state: "approved" };
                    setCards(n);
                  }}
                >
                  Approve
                </button>
              </div>
            </div>
            <div className="space-y-2">
              {c.steps.map((step, si) => (
                <div key={si} className="flex gap-2">
                  <span className="text-xs text-slate-500 w-6 pt-2">{si + 1}</span>
                  <input
                    className="flex-1 bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-slate-200"
                    value={step}
                    onChange={(e) => {
                      const n = [...cards];
                      const steps = [...n[idx].steps];
                      steps[si] = e.target.value;
                      n[idx] = { ...c, steps };
                      setCards(n);
                    }}
                  />
                  <button
                    type="button"
                    className="text-xs text-slate-500 px-1"
                    onClick={() => {
                      const n = [...cards];
                      const steps = n[idx].steps.filter((_, j) => j !== si);
                      n[idx] = { ...c, steps };
                      setCards(n);
                    }}
                  >
                    ✕
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="text-xs text-purple-400"
                onClick={() => {
                  const n = [...cards];
                  n[idx] = { ...c, steps: [...c.steps, ""] };
                  setCards(n);
                }}
              >
                + Step
              </button>
            </div>
            <label className="block text-xs text-slate-500 mt-2">Expected result</label>
            <textarea
              className="w-full bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-slate-200 min-h-[60px]"
              value={c.expected_result}
              onChange={(e) => {
                const n = [...cards];
                n[idx] = { ...c, expected_result: e.target.value };
                setCards(n);
              }}
            />
            <label className="block text-xs text-slate-500 mt-2">Preconditions</label>
            <textarea
              className="w-full bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-slate-200 min-h-[40px]"
              value={c.preconditions}
              onChange={(e) => {
                const n = [...cards];
                n[idx] = { ...c, preconditions: e.target.value };
                setCards(n);
              }}
            />
            <div className="mt-2 flex flex-wrap gap-2">
              {tags.map((t) => {
                const on = c.tags.includes(t.name);
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => {
                      const n = [...cards];
                      const set = new Set(n[idx].tags);
                      if (set.has(t.name)) set.delete(t.name);
                      else set.add(t.name);
                      n[idx] = { ...c, tags: [...set] };
                      setCards(n);
                    }}
                    className={`text-xs px-2 py-1 rounded-full border ${
                      on ? "border-purple-400 bg-purple-600/30 text-white" : "border-white/10 text-slate-400"
                    }`}
                  >
                    {t.name}
                  </button>
                );
              })}
            </div>
            <input
              className="mt-2 w-full bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-slate-300"
              placeholder="Add tags (comma-separated)"
              onBlur={(e) => {
                const extra = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
                if (!extra.length) return;
                const n = [...cards];
                const set = new Set([...n[idx].tags, ...extra]);
                n[idx] = { ...c, tags: [...set] };
                setCards(n);
                e.target.value = "";
              }}
            />
          </motion.div>
        ))}
      </div>

      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Saved test cases</h2>
      <div className="overflow-x-auto glass rounded-xl">
        <table className="w-full text-sm text-left">
          <thead className="text-xs text-slate-500 uppercase border-b border-white/10">
            <tr>
              <th className="p-3">Title</th>
              <th className="p-3">Tags</th>
              <th className="p-3">Status</th>
              <th className="p-3">Stale</th>
            </tr>
          </thead>
          <tbody>
            {tcs.map((t) => (
              <tr key={t.id} className={t.stale ? "bg-amber-900/20" : ""} title={t.stale ? "Parent story was updated" : ""}>
                <td className="p-3 text-slate-200">{t.title}</td>
                <td className="p-3">
                  <div className="flex flex-wrap gap-1">
                    {(t.tags || []).map((x: string) => (
                      <span key={x} className="text-[10px] px-2 py-0.5 rounded-full bg-purple-600/25 text-purple-200">{x}</span>
                    ))}
                  </div>
                </td>
                <td className="p-3 text-slate-400">{t.status}</td>
                <td className="p-3">{t.stale ? "Yes" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {tcs.length === 0 && <p className="p-4 text-slate-500 text-sm">No saved test cases yet.</p>}
      </div>
    </div>
  );
}
