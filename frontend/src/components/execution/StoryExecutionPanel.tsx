"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";
import BulkExecutionStream from "@/components/execution/BulkExecutionStream";

type StoryRow = {
  id: string;
  title: string;
  version: number;
};

type CountsByStory = Record<string, { approved: number; total: number }>;

type SprintRow = {
  id: string;
  name: string;
  state: "planned" | "active" | "completed" | "cancelled";
};

export default function StoryExecutionPanel() {
  const [projects, setProjects] = useState<string[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [stories, setStories] = useState<StoryRow[]>([]);
  const [storyId, setStoryId] = useState("");
  const [storyCounts, setStoryCounts] = useState<CountsByStory>({});
  const [tags, setTags] = useState<any[]>([]);
  const [tagName, setTagName] = useState("");
  const [sprints, setSprints] = useState<SprintRow[]>([]);
  const [sprintId, setSprintId] = useState("");
  const [sprintApprovedCount, setSprintApprovedCount] = useState<Record<string, number>>({});
  const [orgs, setOrgs] = useState<any[]>([]);
  const [orgId, setOrgId] = useState("");
  const [personas, setPersonas] = useState<any[]>([]);
  const [personaId, setPersonaId] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState<{ text: string; storyId?: string } | null>(null);
  // SSE URL passed to the live bulk-run panel. Set on Run, cleared on Close.
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  // When checked, failed tests are healed by the LLM (1 retry) before the
  // bulk run reports their outcome. Defaults OFF so the user picks in.
  const [autoHeal, setAutoHeal] = useState(false);

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
        setSprints([]);
        setSprintApprovedCount({});
      });
      return;
    }
    // Sprints + per-sprint approved test count for the third column.
    api.sprints.list(projectId).then(async (rows: any[]) => {
      const trimmed: SprintRow[] = rows.map((r) => ({
        id: r.id,
        name: r.name,
        state: r.state,
      }));
      setSprints(trimmed);
      const counts: Record<string, number> = {};
      await Promise.all(
        trimmed.map((sp) =>
          api.sprints.testCases(sp.id)
            .then((tc) => { counts[sp.id] = tc.by_status.approved; })
            .catch(() => { counts[sp.id] = 0; }),
        ),
      );
      setSprintApprovedCount(counts);
    }).catch(() => setSprints([]));
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

  const runStory = () => {
    setErr(null);
    setMsg("");
    // Pre-flight: if we already know the story has 0 approved, surface the
    // friendly message + deep link instead of opening an SSE that will 400.
    if (selectedStoryApproved === 0) {
      setErr({
        text: "This story has 0 approved test cases yet. Approve some, then come back.",
        storyId,
      });
      return;
    }
    if (!storyId || !orgId) return;
    // Building the URL is sync (just appends ?token=); the SSE actually opens
    // when BulkExecutionStream receives a non-null streamUrl prop. Setting
    // null first makes the component reset state on consecutive runs.
    setStreamUrl(null);
    setTimeout(() => {
      setStreamUrl(
        api.runs.userStoryStreamUrl(storyId, {
          org_id: orgId,
          persona_id: personaId || null,
          auto_heal: autoHeal,
        }),
      );
    }, 0);
  };

  const runTag = () => {
    setErr(null);
    setMsg("");
    if (!tagName || !projectId || !orgId) return;
    setStreamUrl(null);
    setTimeout(() => {
      setStreamUrl(
        api.runs.byTagStreamUrl(tagName, {
          project_id: projectId,
          org_id: orgId,
          persona_id: personaId || null,
          auto_heal: autoHeal,
        }),
      );
    }, 0);
  };

  const runSprint = () => {
    setErr(null);
    setMsg("");
    if (!sprintId || !orgId) return;
    if ((sprintApprovedCount[sprintId] ?? 0) === 0) {
      setErr({
        text: "This sprint has 0 approved test cases across its stories. Approve some first.",
      });
      return;
    }
    setStreamUrl(null);
    setTimeout(() => {
      setStreamUrl(
        api.runs.sprintStreamUrl(sprintId, {
          org_id: orgId,
          persona_id: personaId || null,
          auto_heal: autoHeal,
        }),
      );
    }, 0);
  };

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="glass p-5 mt-8 mb-6">
      <h3 className="text-sm font-semibold text-purple-300 mb-4 uppercase tracking-wider">Bulk execution</h3>

      {/* Shared selectors -- project / org / persona / auto-heal apply to
          all three "Run by ..." columns below. Pulled out of the columns
          so the user picks them once. */}
      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-5 pb-4 border-b border-white/10">
        <GlassSelect
          className="w-full"
          value={projectName}
          placeholder="Project…"
          onChange={(v) => { setProjectName(v); setStoryId(""); setSprintId(""); setTagName(""); setOrgId(""); }}
          options={[{ value: "", label: "Select project…" }, ...projects.map((p) => ({ value: p, label: p }))]}
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
        <label className="flex items-start gap-2 text-xs text-slate-300 select-none cursor-pointer">
          <input
            type="checkbox"
            className="mt-0.5 accent-fuchsia-500"
            checked={autoHeal}
            onChange={(e) => setAutoHeal(e.target.checked)}
          />
          <span>
            <span className="text-slate-200 font-medium">Auto-heal failures</span>
            <span className="text-slate-500"> -- on failure, feed output.xml + screenshot back to the LLM, rewrite the script, retry once.</span>
          </span>
        </label>
      </div>

      <div className="grid md:grid-cols-3 gap-5">
        {/* Column 1: Run by sprint */}
        <div>
          <p className="text-xs text-slate-500 mb-2">Run by sprint</p>
          <div className="space-y-2">
            <GlassSelect
              className="w-full"
              value={sprintId}
              placeholder="Sprint…"
              onChange={setSprintId}
              disabled={!projectId}
              options={[
                { value: "", label: "Select sprint…" },
                ...sprints
                  .filter((sp) => sp.state !== "cancelled")
                  .map((sp) => ({
                    value: sp.id,
                    label: `${sp.name} (${sp.state})${
                      sprintApprovedCount[sp.id] !== undefined
                        ? ` -- ${sprintApprovedCount[sp.id]} approved`
                        : ""
                    }`,
                  })),
              ]}
            />
            {sprintId && sprintApprovedCount[sprintId] !== undefined && (
              <p className={`text-xs ${sprintApprovedCount[sprintId] === 0 ? "text-amber-300" : "text-slate-500"}`}>
                {sprintApprovedCount[sprintId] === 0
                  ? "0 approved test cases across this sprint."
                  : `${sprintApprovedCount[sprintId]} test case(s) will run.`}
              </p>
            )}
            <button
              type="button"
              onClick={runSprint}
              disabled={!sprintId || !orgId}
              className="w-full py-2 rounded-xl bg-gradient-to-r from-fuchsia-600 to-cyan-600 text-white text-sm font-semibold disabled:opacity-40"
            >
              Run sprint{autoHeal ? " (with auto-heal)" : ""}
            </button>
          </div>
        </div>

        {/* Column 2: Run by user story */}
        <div>
          <p className="text-xs text-slate-500 mb-2">Run by user story</p>
          <div className="space-y-2">
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
            <button
              type="button"
              onClick={runStory}
              disabled={!storyId || !orgId}
              className="w-full py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-cyan-600 text-white text-sm font-semibold disabled:opacity-40"
            >
              Run story{autoHeal ? " (with auto-heal)" : ""}
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
              Tip: pick the <span className="text-slate-300">Smoke</span> tag to run the project&apos;s smoke suite.
            </p>
            <button
              type="button"
              onClick={runTag}
              disabled={!tagName || !orgId || !projectId}
              className="w-full py-2 rounded-xl bg-gradient-to-r from-purple-600 to-pink-600 text-white text-sm font-semibold disabled:opacity-40"
            >
              Run tag{autoHeal ? " (with auto-heal)" : ""}
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

      <BulkExecutionStream
        streamUrl={streamUrl}
        onClose={() => setStreamUrl(null)}
        healContext={orgId ? { org_id: orgId, persona_id: personaId || null } : undefined}
      />
    </motion.div>
  );
}
