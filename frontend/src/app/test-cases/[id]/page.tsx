"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import RobotCodeEditor from "@/components/editor/RobotCodeEditor";
import { api } from "@/lib/api";

export default function TestCaseDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const id = decodeURIComponent(params.id as string);
  const projectSlug = searchParams.get("project") || "";

  const [tc, setTc] = useState<any | null>(null);
  const [story, setStory] = useState<any | null>(null);
  const [script, setScript] = useState("");
  const [originalScript, setOriginalScript] = useState("");
  const [history, setHistory] = useState<Array<{ name: string; modified_at: string; size: number }>>([]);
  const [selectedHistory, setSelectedHistory] = useState<{ name: string; content: string } | null>(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = async () => {
    try {
      const row = await api.testCases.get(id);
      setTc(row);
      if (row?.user_story_id) {
        api.userStories.get(row.user_story_id).then(setStory).catch(() => setStory(null));
      }
      try {
        const s = await api.testCases.script(id);
        setScript(s.content || "");
        setOriginalScript(s.content || "");
      } catch {
        setScript("");
        setOriginalScript("");
      }
      const h = await api.testCases.scriptHistory(id).catch(() => ({ items: [] }));
      setHistory(h.items || []);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not load test case");
    }
  };

  useEffect(() => {
    void load();
  }, [id]);

  const dirty = script !== originalScript;
  const diffSummary = useMemo(() => {
    if (!selectedHistory) return null;
    const a = selectedHistory.content.split("\n");
    const b = script.split("\n");
    let changed = 0;
    const max = Math.max(a.length, b.length);
    for (let i = 0; i < max; i += 1) {
      if ((a[i] || "") !== (b[i] || "")) changed += 1;
    }
    return { changed, base: a.length, current: b.length };
  }, [selectedHistory, script]);

  const buildScript = async () => {
    setBusy("build");
    setErr("");
    setMsg("");
    try {
      await api.testCases.buildScript(id);
      await load();
      setMsg("Script generated.");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not generate script");
    } finally {
      setBusy(null);
    }
  };

  const saveScript = async () => {
    setBusy("save");
    setErr("");
    setMsg("");
    try {
      await api.testCases.saveScript(id, script);
      await load();
      setMsg("Script saved. Previous version was snapshotted in history.");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not save script");
    } finally {
      setBusy(null);
    }
  };

  const previewHistory = async (name: string) => {
    setBusy(`history:${name}`);
    try {
      const row = await api.testCases.scriptHistoryItem(id, name);
      setSelectedHistory({ name, content: row.content });
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not load history item");
    } finally {
      setBusy(null);
    }
  };

  if (!tc) {
    return (
      <div className="max-w-6xl mx-auto px-6 py-20 text-slate-500 text-sm">
        <p>Loading test case…</p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <Link
        href={
          story?.id
            ? `/user-stories/${encodeURIComponent(story.id)}${projectSlug ? `?project=${encodeURIComponent(projectSlug)}` : ""}`
            : "/user-stories"
        }
        className="text-sm text-slate-500 hover:text-purple-400 mb-4 inline-block"
      >
        ← Back
      </Link>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mb-5">
        <h1 className="text-3xl font-bold text-white mb-1">{tc.title}</h1>
        <p className="text-xs text-slate-500">
          status: {tc.status} · {tc.stale ? "stale" : "fresh"} · {tc.tags?.join(", ") || "no tags"}
        </p>
      </motion.div>

      {(msg || err) && (
        <p className={`mb-3 text-sm ${err ? "text-red-300" : "text-emerald-300"}`}>{err || msg}</p>
      )}

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button
          type="button"
          onClick={buildScript}
          disabled={busy !== null || tc.status !== "approved" || tc.stale}
          className="px-3 py-1.5 rounded-lg bg-cyan-700/60 text-cyan-100 text-xs disabled:opacity-40"
        >
          {busy === "build" ? "Generating…" : tc.script_path ? "Re-generate script" : "Generate script"}
        </button>
        <button
          type="button"
          onClick={saveScript}
          disabled={busy !== null || !dirty || !script.trim()}
          className="px-3 py-1.5 rounded-lg bg-emerald-700/60 text-emerald-100 text-xs disabled:opacity-40"
        >
          {busy === "save" ? "Saving…" : "Save script"}
        </button>
        <span className="text-xs text-slate-500">{dirty ? "Unsaved changes" : "Saved"}</span>
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <RobotCodeEditor value={script} onChange={setScript} height="520px" />
        </div>
        <div className="space-y-3">
          <div className="glass p-3 rounded-xl border border-white/10">
            <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-2">Script history</h3>
            {history.length === 0 ? (
              <p className="text-xs text-slate-500">No snapshots yet. Save the script to create one.</p>
            ) : (
              <div className="space-y-1">
                {history.map((h) => (
                  <button
                    key={h.name}
                    type="button"
                    onClick={() => previewHistory(h.name)}
                    className="w-full text-left px-2 py-1 rounded bg-white/5 hover:bg-white/10"
                  >
                    <p className="text-[11px] text-slate-200 truncate">{h.name}</p>
                    <p className="text-[10px] text-slate-500">
                      {new Date(h.modified_at).toLocaleString()} · {h.size} bytes
                    </p>
                  </button>
                ))}
              </div>
            )}
          </div>

          {selectedHistory && (
            <div className="glass p-3 rounded-xl border border-white/10">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs uppercase tracking-wider text-slate-500">Selected snapshot</h3>
                <button
                  type="button"
                  onClick={() => setScript(selectedHistory.content)}
                  className="text-[10px] px-2 py-0.5 rounded bg-purple-600/30 text-purple-200"
                >
                  Restore into editor
                </button>
              </div>
              <p className="text-[11px] text-slate-300 mb-2">{selectedHistory.name}</p>
              {diffSummary && (
                <p className="text-[10px] text-slate-500 mb-2">
                  ~{diffSummary.changed} line(s) different · snapshot {diffSummary.base} lines vs editor {diffSummary.current}
                </p>
              )}
              <pre className="max-h-56 overflow-auto bg-black/40 border border-white/10 rounded p-2 text-[10px] text-slate-300 whitespace-pre">
                {selectedHistory.content}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
