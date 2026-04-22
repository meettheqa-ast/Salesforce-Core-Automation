"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";

export default function StoryExecutionPanel() {
  const [projects, setProjects] = useState<string[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [stories, setStories] = useState<any[]>([]);
  const [storyId, setStoryId] = useState("");
  const [tags, setTags] = useState<any[]>([]);
  const [tagName, setTagName] = useState("");
  const [orgs, setOrgs] = useState<any[]>([]);
  const [orgId, setOrgId] = useState("");
  const [personas, setPersonas] = useState<any[]>([]);
  const [personaId, setPersonaId] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

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
      });
      return;
    }
    api.userStories.list(projectId).then(setStories).catch(() => setStories([]));
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

  const runStory = async () => {
    setErr("");
    setMsg("");
    try {
      const res = await api.runs.userStory(storyId, {
        org_id: orgId,
        persona_id: personaId || null,
      });
      setMsg(`Started ${res.length} run(s).`);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Run failed");
    }
  };

  const runTag = async () => {
    setErr("");
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
      setErr(e instanceof Error ? e.message : "Run failed");
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
                ...stories.map((s: any) => ({
                  value: s.id,
                  label: `${s.title} (v${s.version})`,
                })),
              ]}
            />
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
      {err && <p className="text-red-400 text-sm mt-3">{err}</p>}
    </motion.div>
  );
}
