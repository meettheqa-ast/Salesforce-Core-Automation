"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";

type StoryRow = {
  id: string;
  title: string;
  version: number;
};

type CountsByStory = Record<string, { approved: number; total: number }>;

export default function StoryExecutionPanel() {
  const [projects, setProjects] = useState<string[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [stories, setStories] = useState<StoryRow[]>([]);
  const [storyId, setStoryId] = useState("");
  const [storyCounts, setStoryCounts] = useState<CountsByStory>({});
  const [tags, setTags] = useState<any[]>([]);
  const [tagName, setTagName] = useState("");
  const [orgs, setOrgs] = useState<any[]>([]);
  const [orgId, setOrgId] = useState("");
  const [personas, setPersonas] = useState<any[]>([]);
  const [personaId, setPersonaId] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState<{ text: string; storyId?: string } | null>(null);

  useEffect(() => {
    api.projects.list().then(setProjects).catch(() => {});
  }, []);

  useEffect(() => {
    if (!projectName) {
      void Promise.resolve().then(() => setProjectId(""));
      return;
    }
    api.projects.portalProjectId(projectName).then((r) => setProjectId(r.project_id)).catch(() => setProjectId(""));
  }, [projectName]);

  useEffect(() => {
    if (!projectId) {
      void Promise.resolve().then(() => {
        setStories([]);
        setTags([]);
        setOrgs([]);
        setStoryCounts({});
      });
      return;
    }
    api.userStories.list(projectId).then((rows: any[]) => {
      const trimmed: StoryRow[] = rows.map((r) => ({
        id: r.id,
        title: r.title,
        version: r.version,
      }));
      setStories(trimmed);
      // Fetch test-case counts in parallel so the dropdown can show
      // "(N approved)" -- saves the user a round-trip into each story
      // just to find out which ones are runnable.
      Promise.all(
        trimmed.map((s) =>
          api.testCases.list(s.id).then(
            (tcs: any[]) => ({
              id: s.id,
              approved: tcs.filter((t: any) => t.status === "approved" && !t.stale).length,
              total: tcs.length,
            }),
            () => ({ id: s.id, approved: 0, total: 0 }),
          ),
        ),
      ).then((rows2) => {
        const next: CountsByStory = {};
        rows2.forEach((c) => { next[c.id] = { approved: c.approved, total: c.total }; });
        setStoryCounts(next);
      });
    }).catch(() => setStories([]));
    api.tags.list(projectId).then(setTags).catch(() => setTags([]));
    api.orgs.list(projectId).then(setOrgs).catch(() => setOrgs([]));
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !orgId) {
      void Promise.resolve().then(() => setPersonas([]));
      return;
    }
    api.personas.list(projectId, orgId).then(setPersonas).catch(() => setPersonas([]));
  }, [projectId, orgId]);

  const selectedStoryApproved = useMemo(() => {
    if (!storyId) return undefined;
    return storyCounts[storyId]?.approved;
  }, [storyId, storyCounts]);

  const runStory = async () => {
    setErr(null);
    setMsg("");
    // Pre-flight: if we already know the story has 0 approved, surface the
    // friendly message + deep link instead of round-tripping for a 400.
    if (selectedStoryApproved === 0) {
      setErr({
        text: "This story has 0 approved test cases yet. Approve some, then come back.",
        storyId,
      });
      return;
    }
    try {
      const res = await api.runs.userStory(storyId, {
        org_id: orgId,
        persona_id: personaId || null,
      });
      setMsg(`Started ${res.length} run(s).`);
    } catch (e: unknown) {
      const text = e instanceof Error ? e.message : "Run failed";
      // Backend's "No approved non-stale test cases" comes through verbatim;
      // wrap it with a deep-link to the story so the user can fix it in one click.
      const looksLikeNoApproved = /no approved/i.test(text);
      setErr({ text, storyId: looksLikeNoApproved ? storyId : undefined });
    }
  };

  const runTag = async () => {
    setErr(null);
    setMsg("");
    if (!tagName || !projectId) return;
    try {
      const res = await api.runs.byTag(tagName, {
        project_id: projectId,
        org_id: orgId,
        persona_id: personaId || null,
      });
      setMsg(`Started ${res.length} run(s) for tag ${tagName}.`);
    } catch (e: unknown) {
      const text = e instanceof Error ? e.message : "Run failed";
      setErr({ text });
    }
  };

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="glass p-5 mt-8 mb-6">
      <h3 className="text-sm font-semibold text-purple-300 mb-4 uppercase tracking-wider">Bulk execution</h3>
      <div className="grid md:grid-cols-2 gap-6">
        <div>
          <p className="text-xs text-slate-500 mb-2">Run by user story</p>
          <div className="space-y-2">
            <GlassSelect
              className="w-full"
              value={projectName}
              placeholder="Project…"
              onChange={(v) => { setProjectName(v); setStoryId(""); setOrgId(""); }}
              options={[{ value: "", label: "Select project…" }, ...projects.map((p) => ({ value: p, label: p }))]}
            />
            <GlassSelect
              className="w-full"
              value={storyId}
              placeholder="User story…"
              onChange={setStoryId}
              disabled={!projectId}
              options={[
                { value: "", label: "Select story…" },
                ...stories.map((s) => {
                  const c = storyCounts[s.id];
                  const suffix = c
                    ? ` -- ${c.approved} approved / ${c.total} total`
                    : "";
                  return {
                    value: s.id,
                    label: `${s.title} (v${s.version})${suffix}`,
                  };
                }),
              ]}
            />
            {selectedStoryApproved !== undefined && (
              <p className={`text-xs ${selectedStoryApproved === 0 ? "text-amber-300" : "text-slate-500"}`}>
                {selectedStoryApproved === 0
                  ? "0 approved test cases -- nothing will run."
                  : `${selectedStoryApproved} test case(s) will run.`}
              </p>
            )}
            <GlassSelect
              className="w-full"
              value={orgId}
              placeholder="Org…"
              onChange={setOrgId}
              disabled={!projectId}
              options={[
                { value: "", label: "Select org…" },
                ...orgs.map((o: any) => ({ value: o.id, label: o.name || o.id })),
              ]}
            />
            <GlassSelect
              className="w-full"
              value={personaId}
              placeholder="Persona (optional)"
              onChange={setPersonaId}
              disabled={!orgId}
              options={[
                { value: "", label: "Default resolution" },
                ...personas.map((p: any) => ({ value: p.id, label: p.name })),
              ]}
            />
            <button
              type="button"
              onClick={runStory}
              disabled={!storyId || !orgId}
              className="w-full py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-cyan-600 text-white text-sm font-semibold disabled:opacity-40"
            >
              Run all approved test cases
            </button>
          </div>
        </div>
        <div>
          <p className="text-xs text-slate-500 mb-2">Run by tag</p>
          <div className="space-y-2">
            <GlassSelect
              className="w-full"
              value={tagName}
              placeholder="Tag…"
              onChange={setTagName}
              disabled={!projectId}
              options={[
                { value: "", label: "Select tag…" },
                ...tags.map((t: any) => ({ value: t.name, label: `${t.name} (${t.scope})` })),
              ]}
            />
            <p className="text-xs text-slate-500">
              Uses the same org and persona selections as the left panel.
            </p>
            <button
              type="button"
              onClick={runTag}
              disabled={!tagName || !orgId || !projectId}
              className="w-full py-2 rounded-xl bg-gradient-to-r from-purple-600 to-pink-600 text-white text-sm font-semibold disabled:opacity-40"
            >
              Run by tag
            </button>
          </div>
        </div>
      </div>
      {msg && <p className="text-emerald-400 text-sm mt-3">{msg}</p>}
      {err && (
        <div className="text-red-300 text-sm mt-3 flex flex-wrap gap-2 items-center">
          <span>{err.text}</span>
          {err.storyId && (
            <Link
              href={`/user-stories/${encodeURIComponent(err.storyId)}`}
              className="px-2 py-0.5 rounded bg-purple-600/40 text-purple-100 text-xs hover:bg-purple-600/60"
            >
              Open story to approve
            </Link>
          )}
        </div>
      )}
    </motion.div>
  );
}
