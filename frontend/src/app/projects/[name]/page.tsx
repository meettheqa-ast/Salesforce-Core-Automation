"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import AnimatedCard from "@/components/cards/AnimatedCard";
import StatusDonut, { DONUT_COLORS } from "@/components/charts/StatusDonut";
import RobotCodeEditor from "@/components/editor/RobotCodeEditor";
import GlassSelect from "@/components/ui/GlassSelect";
import { api } from "@/lib/api";

/** Filter state for the test-cases panel. Mirrors a `?filter=<id>` URL
 *  param so a click-through from the project list page lands here on
 *  the right slice. */
type CaseFilter =
  | "all"
  | "approved"
  | "draft"
  | "rejected"
  | "stale"
  | "has-script"
  | "no-script";

const VALID_FILTERS: CaseFilter[] = [
  "all",
  "approved",
  "draft",
  "rejected",
  "stale",
  "has-script",
  "no-script",
];

const DEFAULT_PERSONA = "System Admin";

type CredFields = {
  sandbox_url: string;
  username: string;
  password: string;
  security_token: string;
  slack_webhook_url: string;
  /** Salesforce app this persona should land in by default. Becomes
   *  ${salesAutomationAppName} at run time so PO keywords pick the right
   *  app without test changes. Empty string falls back to the global "Sales". */
  default_app: string;
};

const EMPTY_CREDS: CredFields = {
  sandbox_url: "",
  username: "",
  password: "",
  security_token: "",
  slack_webhook_url: "",
  default_app: "",
};

type TestRow = { name: string; path: string; modified: string };
type AnalyticsSummary = { pass_rate?: number; total_runs?: number };
type ConfirmTarget =
  | { kind: "env"; env: string }
  | { kind: "persona"; env: string; persona: string };

type ProjectTestCases = Awaited<ReturnType<typeof api.projects.testCases>>;
type StoryGroup = ProjectTestCases["stories"][number];
type TCRow = StoryGroup["test_cases"][number];

export default function ProjectDetailPage() {
  const params = useParams();
  const name = decodeURIComponent(params.name as string);
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const initialFilter = ((): CaseFilter => {
    const v = searchParams.get("filter");
    return v && (VALID_FILTERS as string[]).includes(v) ? (v as CaseFilter) : "all";
  })();
  const [activeFilter, setActiveFilter] = useState<CaseFilter>(initialFilter);

  /** Update both component state and the URL so the filter is shareable
   *  and survives a refresh. Uses `replace` to keep the back button clean
   *  -- the user's history shouldn't fill with filter toggles. */
  const setFilter = (next: CaseFilter) => {
    setActiveFilter(next);
    const sp = new URLSearchParams(searchParams.toString());
    if (next === "all") sp.delete("filter");
    else sp.set("filter", next);
    const qs = sp.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const [environments, setEnvironments] = useState<string[]>([]);
  const [selectedEnv, setSelectedEnv] = useState("");
  const [personas, setPersonas] = useState<string[]>([]);
  const [selectedPersona, setSelectedPersona] = useState(DEFAULT_PERSONA);

  const [creds, setCreds] = useState<CredFields>(EMPTY_CREDS);
  const [revealPassword, setRevealPassword] = useState(false);
  const [credLoading, setCredLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const [tests, setTests] = useState<TestRow[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
  const [viewingSource, setViewingSource] = useState<{ name: string; source: string } | null>(null);
  const [projectTCs, setProjectTCs] = useState<ProjectTestCases | null>(null);
  const [openStories, setOpenStories] = useState<Record<string, boolean>>({});
  const [scriptPreview, setScriptPreview] = useState<{ tcId: string; title: string; content: string } | null>(null);
  // Sprints summary for the project. Fetched once we resolve the
  // project slug -> portal UUID so /sprints?project_id=... works.
  const [sprintsForProject, setSprintsForProject] = useState<
    Array<{ id: string; name: string; state: string; story_count: number }>
  >([]);

  /** The Test cases panel renders this filtered slice of `projectTCs.stories`.
   *  When the filter is "all" we just pass the original stories through;
   *  otherwise each story is reduced to only the test cases matching the
   *  active filter, and stories with zero matches are dropped entirely. */
  const filteredStories = useMemo(() => {
    if (!projectTCs) return [];
    if (activeFilter === "all") return projectTCs.stories;
    const matchesFilter = (tc: TCRow): boolean => {
      switch (activeFilter) {
        case "approved":
          return tc.status === "approved" && !tc.stale;
        case "draft":
          return tc.status === "draft" && !tc.stale;
        case "rejected":
          return tc.status === "rejected";
        case "stale":
          return tc.stale;
        case "has-script":
          return !!tc.script_path;
        case "no-script":
          return !tc.script_path;
        default:
          return true;
      }
    };
    return projectTCs.stories
      .map((s) => ({ ...s, test_cases: s.test_cases.filter(matchesFilter) }))
      .filter((s) => s.test_cases.length > 0);
  }, [projectTCs, activeFilter]);

  const [showAddEnv, setShowAddEnv] = useState(false);
  const [newEnvName, setNewEnvName] = useState("");
  const [showAddPersona, setShowAddPersona] = useState(false);
  const [newPersonaName, setNewPersonaName] = useState("");

  const [confirm, setConfirm] = useState<ConfirmTarget | null>(null);

  const flash = useCallback((kind: "ok" | "err", text: string) => {
    setStatusMsg({ kind, text });
    window.setTimeout(() => setStatusMsg(null), 3000);
  }, []);

  const loadEnvironments = useCallback(async () => {
    try {
      const envs = await api.projects.environments(name);
      setEnvironments(envs);
      setSelectedEnv((cur) => (cur && envs.includes(cur) ? cur : envs[0] || ""));
    } catch {
      setEnvironments([]);
    }
  }, [name]);

  useEffect(() => {
    void Promise.resolve().then(() => {
      loadEnvironments();
      api.projects.tests(name).then(setTests).catch(() => setTests([]));
      api.analytics.summary(name).then(setAnalytics).catch(() => setAnalytics(null));
      api.projects.testCases(name).then(setProjectTCs).catch(() => setProjectTCs(null));
      // Sprint panel needs the portal project UUID, so resolve slug
      // first then fan out to sprints + stories (for per-sprint counts).
      api.projects.portalProjectId(name).then((r) => {
        const pid = r.project_id;
        return Promise.all([
          api.sprints.list(pid).catch(() => []),
          api.userStories.list(pid).catch(() => [] as Array<{ sprint_id: string | null }>),
        ]).then(([sprints, stories]) => {
          const counts: Record<string, number> = {};
          for (const st of stories) {
            const sid = st.sprint_id;
            if (sid) counts[sid] = (counts[sid] || 0) + 1;
          }
          setSprintsForProject(
            sprints.map((s: any) => ({
              id: s.id,
              name: s.name,
              state: s.state,
              story_count: counts[s.id] || 0,
            })),
          );
        });
      }).catch(() => setSprintsForProject([]));
    });
  }, [name, loadEnvironments]);

  useEffect(() => {
    if (!selectedEnv) {
      void Promise.resolve().then(() => {
        setPersonas([]);
        setSelectedPersona(DEFAULT_PERSONA);
        setCreds(EMPTY_CREDS);
      });
      return;
    }
    api.projects
      .personas(name, selectedEnv)
      .then((list) => {
        const items = list.length ? list : [DEFAULT_PERSONA];
        setPersonas(items);
        setSelectedPersona((cur) => (cur && items.includes(cur) ? cur : items[0]));
      })
      .catch(() => {
        setPersonas([DEFAULT_PERSONA]);
        setSelectedPersona(DEFAULT_PERSONA);
      });
  }, [name, selectedEnv]);

  useEffect(() => {
    if (!selectedEnv || !selectedPersona) {
      void Promise.resolve().then(() => setCreds(EMPTY_CREDS));
      return;
    }
    void Promise.resolve().then(() => {
      setCredLoading(true);
      setRevealPassword(false);
      api.projects
        .config(name, selectedEnv, selectedPersona)
        .then((cfg) =>
          setCreds({
            sandbox_url: cfg.sandbox_url || "",
            username: cfg.username || "",
            password: cfg.password || "",
            security_token: cfg.security_token || "",
            slack_webhook_url: cfg.slack_webhook_url || "",
            default_app: cfg.default_app || "",
          })
        )
        .catch(() => setCreds(EMPTY_CREDS))
        .finally(() => setCredLoading(false));
    });
  }, [name, selectedEnv, selectedPersona]);

  const viewSource = async (testName: string) => {
    try {
      const res = await api.projects.testSource(name, testName);
      setViewingSource({ name: testName, source: res.source });
    } catch {
      // no-op; modal stays closed
    }
  };

  const saveCreds = async () => {
    if (!selectedEnv || !selectedPersona) return;
    setSaving(true);
    try {
      await api.projects.saveCredentials(name, {
        environment: selectedEnv,
        persona: selectedPersona,
        ...creds,
      });
      flash("ok", "Credentials saved");
      loadEnvironments();
    } catch (e: unknown) {
      flash("err", e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const addEnvironment = async () => {
    const env = newEnvName.trim();
    if (!env) return;
    try {
      await api.projects.saveCredentials(name, {
        environment: env,
        persona: DEFAULT_PERSONA,
        sandbox_url: "",
        username: "",
        password: "",
      });
      setNewEnvName("");
      setShowAddEnv(false);
      const envs = await api.projects.environments(name);
      setEnvironments(envs);
      setSelectedEnv(env);
      setSelectedPersona(DEFAULT_PERSONA);
      flash("ok", `Environment '${env}' added`);
    } catch (e: unknown) {
      flash("err", e instanceof Error ? e.message : "Add environment failed");
    }
  };

  const addPersona = async () => {
    const persona = newPersonaName.trim();
    if (!persona || !selectedEnv) return;
    try {
      await api.projects.saveCredentials(name, {
        environment: selectedEnv,
        persona,
        sandbox_url: "",
        username: "",
        password: "",
      });
      setNewPersonaName("");
      setShowAddPersona(false);
      const list = await api.projects.personas(name, selectedEnv);
      setPersonas(list.length ? list : [DEFAULT_PERSONA]);
      setSelectedPersona(persona);
      flash("ok", `Persona '${persona}' added`);
    } catch (e: unknown) {
      flash("err", e instanceof Error ? e.message : "Add persona failed");
    }
  };

  const performDelete = async () => {
    if (!confirm) return;
    try {
      if (confirm.kind === "env") {
        await api.projects.deleteEnvironment(name, confirm.env);
        flash("ok", `Environment '${confirm.env}' deleted`);
        const envs = await api.projects.environments(name);
        setEnvironments(envs);
        if (selectedEnv === confirm.env) {
          setSelectedEnv(envs[0] || "");
          setSelectedPersona(DEFAULT_PERSONA);
        }
      } else {
        await api.projects.deletePersona(name, confirm.env, confirm.persona);
        flash("ok", `Persona '${confirm.persona}' deleted`);
        const list = await api.projects.personas(name, confirm.env);
        const items = list.length ? list : [DEFAULT_PERSONA];
        setPersonas(items);
        if (selectedPersona === confirm.persona) {
          setSelectedPersona(items[0]);
        }
      }
    } catch (e: unknown) {
      flash("err", e instanceof Error ? e.message : "Delete failed");
    } finally {
      setConfirm(null);
    }
  };

  const deletingEnv = personas.length === 1 && personas[0] === DEFAULT_PERSONA;

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <Link
          href="/projects"
          className="text-sm text-slate-500 hover:text-purple-400 transition-colors mb-3 inline-block"
        >
          ← Back to Projects
        </Link>
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <h1 className="text-4xl font-bold mb-2">
            <span className="bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
              {name}
            </span>
          </h1>
          <Link
            href={`/projects/${encodeURIComponent(name)}/members`}
            className="px-4 py-2 glass text-sm text-slate-300 hover:text-white rounded-xl transition-colors"
          >
            Manage members &rarr;
          </Link>
        </div>
      </motion.div>

      {/* Dashboard: counts row + 2 donuts + filter chips */}
      <ProjectDashboard
        environments={environments.length}
        tcs={projectTCs}
        analytics={analytics}
        activeFilter={activeFilter}
        onFilterChange={setFilter}
      />

      {/* Inline status */}
      <AnimatePresence>
        {statusMsg && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className={`mb-3 text-xs px-3 py-2 rounded-lg border ${
              statusMsg.kind === "ok"
                ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10"
                : "border-red-500/40 text-red-300 bg-red-500/10"
            }`}
          >
            {statusMsg.text}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="grid md:grid-cols-3 gap-6">
        {/* Environments */}
        <AnimatedCard glow="purple" delay={0.1}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold text-white">Environments</h3>
            <button
              type="button"
              onClick={() => setShowAddEnv((v) => !v)}
              className="text-xs text-purple-300 hover:text-purple-200"
            >
              {showAddEnv ? "Cancel" : "+ Add"}
            </button>
          </div>
          {showAddEnv && (
            <div className="flex gap-2 mb-3">
              <input
                value={newEnvName}
                onChange={(e) => setNewEnvName(e.target.value)}
                placeholder="e.g. UAT"
                className="flex-1 bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-purple-500"
              />
              <button
                type="button"
                onClick={addEnvironment}
                disabled={!newEnvName.trim()}
                className="px-3 py-1.5 text-xs rounded-lg bg-purple-600 text-white disabled:opacity-40"
              >
                Save
              </button>
            </div>
          )}
          <div className="space-y-1">
            {environments.map((env) => {
              const active = selectedEnv === env;
              return (
                <div
                  key={env}
                  className={`group flex items-center gap-1 px-2 py-1.5 rounded-lg transition-all ${
                    active ? "bg-purple-600/30 border border-purple-500/30" : "hover:bg-white/5 border border-transparent"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => setSelectedEnv(env)}
                    className={`flex-1 text-left text-sm ${active ? "text-purple-200" : "text-slate-300"}`}
                  >
                    {env}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirm({ kind: "env", env })}
                    className="opacity-0 group-hover:opacity-100 text-xs text-slate-500 hover:text-red-400 px-1"
                    title={`Delete environment ${env}`}
                  >
                    ✕
                  </button>
                </div>
              );
            })}
            {environments.length === 0 && (
              <p className="text-xs text-slate-500">No environments configured.</p>
            )}
          </div>
        </AnimatedCard>

        {/* Credentials */}
        <div className="md:col-span-2">
          <AnimatedCard glow="cyan" delay={0.15}>
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <h3 className="text-sm font-bold text-white">
                Credentials
                {selectedEnv && (
                  <span className="ml-2 text-xs text-slate-400">
                    {selectedEnv} / {selectedPersona}
                  </span>
                )}
                {/* Surface the per-persona default app at a glance so the
                    user knows which Salesforce app this persona will land
                    in without having to scroll into the form below. */}
                {selectedEnv && creds.default_app && (
                  <span
                    className="ml-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-purple-500/15 text-purple-200 text-[10px] uppercase tracking-wider"
                    title={`Will be injected as $\{salesAutomationAppName} = "${creds.default_app}" at run time.`}
                  >
                    app: {creds.default_app}
                  </span>
                )}
              </h3>
              <div className="flex items-center gap-2">
                <GlassSelect
                  className="min-w-[10rem]"
                  value={selectedPersona}
                  onChange={setSelectedPersona}
                  disabled={!selectedEnv || personas.length === 0}
                  placeholder="Persona…"
                  options={personas.map((p) => ({ value: p, label: p }))}
                />
                <button
                  type="button"
                  onClick={() => setShowAddPersona((v) => !v)}
                  disabled={!selectedEnv}
                  className="text-xs text-purple-300 hover:text-purple-200 disabled:opacity-40"
                >
                  {showAddPersona ? "Cancel" : "+ Persona"}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    selectedEnv && selectedPersona &&
                    setConfirm({ kind: "persona", env: selectedEnv, persona: selectedPersona })
                  }
                  disabled={!selectedEnv || !selectedPersona || deletingEnv}
                  title={deletingEnv ? "Add another persona before deleting the only one" : "Delete persona"}
                  className="text-xs text-slate-400 hover:text-red-400 disabled:opacity-40"
                >
                  Delete
                </button>
              </div>
            </div>

            {showAddPersona && (
              <div className="flex gap-2 mb-3">
                <input
                  value={newPersonaName}
                  onChange={(e) => setNewPersonaName(e.target.value)}
                  placeholder="e.g. Sales Manager"
                  className="flex-1 bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-purple-500"
                />
                <button
                  type="button"
                  onClick={addPersona}
                  disabled={!newPersonaName.trim()}
                  className="px-3 py-1.5 text-xs rounded-lg bg-purple-600 text-white disabled:opacity-40"
                >
                  Save
                </button>
              </div>
            )}

            {!selectedEnv ? (
              <p className="text-sm text-slate-500">Select or create an environment to manage credentials.</p>
            ) : (
              <div className="space-y-3">
                <Field label="Sandbox URL">
                  <input
                    value={creds.sandbox_url}
                    onChange={(e) => setCreds({ ...creds, sandbox_url: e.target.value })}
                    disabled={credLoading}
                    placeholder="https://yourorg--sbx.sandbox.my.salesforce.com/"
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  />
                </Field>
                <div className="grid sm:grid-cols-2 gap-3">
                  <Field label="Username">
                    <input
                      value={creds.username}
                      onChange={(e) => setCreds({ ...creds, username: e.target.value })}
                      disabled={credLoading}
                      autoComplete="off"
                      className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                    />
                  </Field>
                  <Field
                    label="Password"
                    action={
                      <button
                        type="button"
                        onClick={() => setRevealPassword((v) => !v)}
                        className="text-[10px] uppercase tracking-wider text-purple-300 hover:text-purple-200"
                      >
                        {revealPassword ? "Hide" : "Reveal"}
                      </button>
                    }
                  >
                    <input
                      type={revealPassword ? "text" : "password"}
                      value={creds.password}
                      onChange={(e) => setCreds({ ...creds, password: e.target.value })}
                      disabled={credLoading}
                      autoComplete="new-password"
                      className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                    />
                  </Field>
                </div>
                <div className="grid sm:grid-cols-2 gap-3">
                  <Field label="Security Token (optional)">
                    <input
                      value={creds.security_token}
                      onChange={(e) => setCreds({ ...creds, security_token: e.target.value })}
                      disabled={credLoading}
                      className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                    />
                  </Field>
                  <Field label="Slack Webhook (optional)">
                    <input
                      value={creds.slack_webhook_url}
                      onChange={(e) => setCreds({ ...creds, slack_webhook_url: e.target.value })}
                      disabled={credLoading}
                      placeholder="https://hooks.slack.com/services/..."
                      className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                    />
                  </Field>
                </div>
                {/* Default app -- per persona. Injected at run time as
                    ${salesAutomationAppName} so PO keywords land in the
                    right Salesforce app for this user's license. */}
                <Field label="Default app (optional)">
                  <input
                    value={creds.default_app}
                    onChange={(e) => setCreds({ ...creds, default_app: e.target.value })}
                    disabled={credLoading}
                    placeholder="e.g. Pentair Sales"
                    autoComplete="off"
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-200 outline-none focus:border-purple-500"
                  />
                  <p className="mt-1 text-[11px] text-slate-500">
                    Used as <code className="text-slate-400">${"{salesAutomationAppName}"}</code> when running tests as this persona.
                    Leave blank to use the project default (&quot;Sales&quot;).
                  </p>
                </Field>
                <div className="flex justify-end">
                  <motion.button
                    whileTap={{ scale: 0.97 }}
                    type="button"
                    onClick={saveCreds}
                    disabled={saving || credLoading}
                    className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50"
                  >
                    {saving ? "Saving…" : "Save credentials"}
                  </motion.button>
                </div>
              </div>
            )}
          </AnimatedCard>
        </div>
      </div>

      {/* Sprints panel */}
      <div className="mt-6">
        <AnimatedCard glow="purple" delay={0.12}>
          <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
            <h3 className="text-sm font-bold text-white">
              Sprints{" "}
              <span className="text-slate-500 font-normal">({sprintsForProject.length})</span>
            </h3>
            <Link
              href={`/sprints?project=${encodeURIComponent(name)}`}
              className="text-[11px] text-purple-300 hover:text-purple-200"
            >
              Manage sprints &rarr;
            </Link>
          </div>
          {sprintsForProject.length === 0 ? (
            <p className="text-xs text-slate-500">
              No sprints yet for this project.{" "}
              <Link
                href={`/sprints?project=${encodeURIComponent(name)}`}
                className="text-purple-400 hover:text-purple-300 underline underline-offset-2"
              >
                Create one
              </Link>{" "}
              to organise stories into iterations.
            </p>
          ) : (
            <div className="grid sm:grid-cols-2 md:grid-cols-3 gap-2">
              {sprintsForProject
                .sort((a, b) => {
                  const order = { active: 0, planned: 1, completed: 2, cancelled: 3 };
                  return (order[a.state as keyof typeof order] ?? 9) - (order[b.state as keyof typeof order] ?? 9);
                })
                .map((s) => (
                  <Link
                    key={s.id}
                    href={`/sprints/${encodeURIComponent(s.id)}`}
                    className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10 hover:border-purple-500/40 transition-colors"
                  >
                    <div className="min-w-0">
                      <p className="text-sm text-slate-100 truncate">{s.name}</p>
                      <p className="text-[10px] text-slate-500">
                        {s.story_count} stor{s.story_count === 1 ? "y" : "ies"}
                      </p>
                    </div>
                    <span
                      className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full ${
                        s.state === "active"
                          ? "bg-emerald-500/30 text-emerald-200"
                          : s.state === "planned"
                          ? "bg-purple-500/30 text-purple-200"
                          : s.state === "completed"
                          ? "bg-slate-500/30 text-slate-200"
                          : "bg-red-500/30 text-red-200"
                      }`}
                    >
                      {s.state}
                    </span>
                  </Link>
                ))}
            </div>
          )}
        </AnimatedCard>
      </div>

      {/* Test cases (project-wide, grouped by user story, filterable) */}
      {projectTCs && projectTCs.total > 0 && (
        <div className="mt-6">
          <AnimatedCard glow="cyan" delay={0.15}>
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <h3 className="text-sm font-bold text-white">
                Test cases{" "}
                <span className="text-slate-500 font-normal">
                  ({activeFilter === "all"
                    ? projectTCs.total
                    : `${filteredStories.reduce((n, s) => n + s.test_cases.length, 0)} of ${projectTCs.total}`})
                </span>
              </h3>
              <div className="flex flex-wrap gap-2 text-[11px]">
                {projectTCs.scripts_built > 0 && (
                  <span className="px-2 py-0.5 rounded-full bg-cyan-600/20 text-cyan-200">
                    {projectTCs.scripts_built} script(s) built
                  </span>
                )}
              </div>
            </div>

            {activeFilter !== "all" && filteredStories.length === 0 && (
              <div className="text-center py-6 text-sm text-slate-400">
                No test cases match the <span className="text-slate-200">{activeFilter}</span> filter.{" "}
                <button
                  type="button"
                  onClick={() => setFilter("all")}
                  className="text-purple-400 hover:text-purple-300 underline underline-offset-2"
                >
                  Clear filter
                </button>
              </div>
            )}

            <div className="space-y-3">
              {filteredStories.map((story: StoryGroup) => {
                const isOpen = openStories[story.id] ?? true;
                const approvedHere = story.test_cases.filter(
                  (t) => t.status === "approved" && !t.stale,
                ).length;
                return (
                  <div key={story.id} className="rounded-xl border border-white/10 bg-white/5">
                    <div className="flex items-center justify-between px-3 py-2">
                      <button
                        type="button"
                        onClick={() =>
                          setOpenStories((s) => ({ ...s, [story.id]: !(s[story.id] ?? true) }))
                        }
                        className="flex items-center gap-2 text-left flex-1 min-w-0"
                      >
                        <span className="text-slate-400 text-xs">{isOpen ? "▼" : "▶"}</span>
                        <span className="text-sm text-white truncate">{story.title}</span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-600/30 text-purple-200">
                          v{story.version}
                        </span>
                        <span className="text-[10px] text-slate-500 ml-1">
                          {story.test_cases.length} case(s) -- {approvedHere} approved
                        </span>
                      </button>
                      <Link
                        href={`/user-stories/${encodeURIComponent(story.id)}`}
                        className="text-[10px] text-purple-400 hover:text-purple-300 px-2"
                      >
                        Open story →
                      </Link>
                    </div>

                    {isOpen && (
                      <div className="border-t border-white/10 divide-y divide-white/5">
                        {story.test_cases.map((tc: TCRow) => (
                          <div
                            key={tc.id}
                            className="px-3 py-2 flex flex-wrap items-center gap-2"
                          >
                            <span
                              className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full ${
                                tc.status === "approved"
                                  ? "bg-emerald-600/25 text-emerald-200"
                                  : tc.status === "rejected"
                                    ? "bg-red-600/25 text-red-200"
                                    : "bg-slate-700/50 text-slate-300"
                              }`}
                            >
                              {tc.status}
                            </span>
                            {tc.stale && (
                              <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-600/25 text-amber-200">
                                stale
                              </span>
                            )}
                            <span className="text-sm text-slate-100 flex-1 min-w-0 truncate">
                              {tc.title}
                            </span>
                            {tc.tags.slice(0, 3).map((t) => (
                              <span
                                key={t}
                                className="text-[10px] px-1.5 py-0.5 rounded bg-purple-600/25 text-purple-200"
                              >
                                {t}
                              </span>
                            ))}
                            {tc.script_path ? (
                              <button
                                type="button"
                                onClick={() => {
                                  void api.testCases
                                    .script(tc.id)
                                    .then((r) =>
                                      setScriptPreview({
                                        tcId: tc.id,
                                        title: tc.title,
                                        content: r.content,
                                      }),
                                    )
                                    .catch((err) =>
                                      flash(
                                        "err",
                                        err instanceof Error ? err.message : "Could not load script",
                                      ),
                                    );
                                }}
                                className="text-[10px] px-2 py-0.5 rounded bg-cyan-600/30 text-cyan-100 hover:bg-cyan-600/50"
                                title={tc.script_path}
                              >
                                View script
                              </button>
                            ) : (
                              <span className="text-[10px] text-slate-500 italic">no script built</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </AnimatedCard>
        </div>
      )}

      {/* Saved Tests */}
      <div className="mt-6">
        <AnimatedCard glow="purple" delay={0.2}>
          <h3 className="text-sm font-bold text-white mb-3">Saved Tests</h3>
          {tests.length === 0 ? (
            <div className="text-center py-6">
              <p className="text-slate-500 text-sm">No tests saved yet.</p>
              <Link
                href="/generate"
                className="text-xs text-purple-400 hover:text-purple-300 mt-2 inline-block"
              >
                Generate your first test →
              </Link>
            </div>
          ) : (
            <div className="space-y-2">
              {tests.map((test) => (
                <motion.div
                  key={test.name}
                  whileHover={{ x: 2 }}
                  className="flex items-center justify-between p-3 glass rounded-xl group"
                >
                  <div>
                    <div className="text-sm font-medium text-white">{test.name}.robot</div>
                    <div className="text-xs text-slate-500">{new Date(test.modified).toLocaleString()}</div>
                  </div>
                  <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      type="button"
                      onClick={() => viewSource(test.name)}
                      className="text-xs px-2 py-1 bg-purple-500/20 text-purple-300 rounded-lg hover:bg-purple-500/30 transition-colors"
                    >
                      View
                    </button>
                  </div>
                </motion.div>
              ))}
            </div>
          )}
        </AnimatedCard>
      </div>

      {/* Confirm modal */}
      <AnimatePresence>
        {confirm && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
            onClick={() => setConfirm(null)}
          >
            <motion.div
              initial={{ scale: 0.94 }}
              animate={{ scale: 1 }}
              className="glass-strong p-5 w-full max-w-sm"
              onClick={(e) => e.stopPropagation()}
            >
              <h3 className="text-base font-bold text-white mb-2">
                {confirm.kind === "env"
                  ? `Delete environment '${confirm.env}'?`
                  : `Delete persona '${confirm.persona}' from '${confirm.env}'?`}
              </h3>
              <p className="text-xs text-slate-400 mb-4">
                {confirm.kind === "env"
                  ? "All personas and credentials in this environment will be removed. This cannot be undone."
                  : "Saved credentials for this persona will be removed."}
              </p>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setConfirm(null)}
                  className="px-3 py-1.5 rounded-lg glass text-xs text-slate-300 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={performDelete}
                  className="px-3 py-1.5 rounded-lg bg-red-600 text-white text-xs"
                >
                  Delete
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Test-case Robot script preview modal */}
      <AnimatePresence>
        {scriptPreview && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-8"
            onClick={() => setScriptPreview(null)}
          >
            <motion.div
              initial={{ scale: 0.9 }}
              animate={{ scale: 1 }}
              className="glass-strong p-4 w-full max-w-4xl max-h-[80vh] overflow-hidden flex flex-col"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-white truncate">{scriptPreview.title}</h3>
                <button
                  type="button"
                  onClick={() => setScriptPreview(null)}
                  className="text-slate-400 hover:text-white text-lg"
                >
                  ×
                </button>
              </div>
              <pre className="bg-black/40 border border-cyan-900/40 rounded p-3 text-[12px] text-cyan-100 overflow-auto whitespace-pre flex-1">
                {scriptPreview.content}
              </pre>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Source Viewer Modal */}
      <AnimatePresence>
        {viewingSource && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-8"
            onClick={() => setViewingSource(null)}
          >
            <motion.div
              initial={{ scale: 0.9 }}
              animate={{ scale: 1 }}
              className="glass-strong p-4 w-full max-w-4xl max-h-[80vh] overflow-hidden flex flex-col"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-white">{viewingSource.name}.robot</h3>
                <button
                  type="button"
                  onClick={() => setViewingSource(null)}
                  className="text-slate-400 hover:text-white text-lg"
                >
                  ✕
                </button>
              </div>
              <RobotCodeEditor value={viewingSource.source} readOnly height="500px" />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Field({
  label,
  action,
  children,
}: {
  label: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[11px] uppercase tracking-wider text-slate-500">{label}</span>
        {action}
      </div>
      {children}
    </label>
  );
}


/**
 * Project-detail dashboard. Replaces the old four-MetricCard row with a
 * richer view: top-line counts (stories / cases / pass rate / runs),
 * two donuts (status breakdown + scripts ready vs not), and a row of
 * filter chips that toggle which test cases the panel below shows.
 *
 * The chips are the only place the active filter is mutated -- they call
 * `onFilterChange` which writes both component state and the URL.
 */
function ProjectDashboard({
  environments,
  tcs,
  analytics,
  activeFilter,
  onFilterChange,
}: {
  environments: number;
  tcs: ProjectTestCases | null;
  analytics: AnalyticsSummary | null;
  activeFilter: CaseFilter;
  onFilterChange: (next: CaseFilter) => void;
}) {
  const total = tcs?.total ?? 0;
  const built = tcs?.scripts_built ?? 0;
  const noScript = total - built;
  const statusSlices = tcs
    ? [
        { id: "approved", label: "Approved", count: tcs.by_status.approved, color: DONUT_COLORS.approved },
        { id: "draft", label: "Draft", count: tcs.by_status.draft, color: DONUT_COLORS.draft },
        { id: "rejected", label: "Rejected", count: tcs.by_status.rejected, color: DONUT_COLORS.rejected },
        { id: "stale", label: "Stale", count: tcs.by_status.stale, color: DONUT_COLORS.stale },
      ]
    : [];
  const scriptSlices = tcs
    ? [
        { id: "built", label: "Scripts ready", count: built, color: DONUT_COLORS.built },
        { id: "no-script", label: "No script", count: noScript, color: DONUT_COLORS.noScript },
      ]
    : [];

  const chip = (
    id: CaseFilter,
    label: string,
    count: number,
    activeCls: string,
    inactiveCls: string,
  ) => {
    const isActive = activeFilter === id;
    return (
      <button
        type="button"
        key={id}
        onClick={() => onFilterChange(id)}
        className={`text-[11px] px-2.5 py-1 rounded-full transition-colors ${
          isActive ? activeCls : inactiveCls
        }`}
      >
        {label} {count}
      </button>
    );
  };

  return (
    <AnimatedCard glow="purple" delay={0.05} className="mt-6 mb-6">
      {/* Top-line counts */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Stat label="Stories" value={tcs?.stories.length ?? 0} accent="text-purple-200" />
        <Stat label="Test cases" value={total} accent="text-cyan-200" />
        <Stat
          label="Pass rate"
          value={analytics?.pass_rate != null ? `${analytics.pass_rate}%` : "--"}
          accent="text-emerald-200"
        />
        <Stat label="Total runs" value={analytics?.total_runs ?? 0} accent="text-pink-200" />
      </div>

      <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-3">
        {environments} environment{environments === 1 ? "" : "s"} configured
      </div>

      {/* Two donuts: status + scripts */}
      <div className="grid sm:grid-cols-2 gap-4 mb-4">
        <div className="flex items-center gap-4">
          <StatusDonut
            slices={statusSlices}
            size={120}
            thickness={18}
            centerSubtitle="cases"
            ariaLabel="Test case status breakdown"
          />
          <div className="text-[11px] space-y-1">
            <p className="uppercase tracking-wider text-slate-500 text-[10px] mb-1">Status</p>
            <Legend dot={DONUT_COLORS.approved} label="Approved" count={tcs?.by_status.approved ?? 0} />
            <Legend dot={DONUT_COLORS.draft} label="Draft" count={tcs?.by_status.draft ?? 0} />
            <Legend dot={DONUT_COLORS.rejected} label="Rejected" count={tcs?.by_status.rejected ?? 0} />
            <Legend dot={DONUT_COLORS.stale} label="Stale" count={tcs?.by_status.stale ?? 0} />
          </div>
        </div>
        <div className="flex items-center gap-4">
          <StatusDonut
            slices={scriptSlices}
            size={120}
            thickness={18}
            centerLabel={total > 0 ? `${Math.round((built / total) * 100)}%` : "—"}
            centerSubtitle="ready"
            ariaLabel="Scripts ready breakdown"
          />
          <div className="text-[11px] space-y-1">
            <p className="uppercase tracking-wider text-slate-500 text-[10px] mb-1">Scripts</p>
            <Legend dot={DONUT_COLORS.built} label="Scripts ready" count={built} />
            <Legend dot={DONUT_COLORS.noScript} label="No script (manual)" count={noScript} />
          </div>
        </div>
      </div>

      {/* Filter chips */}
      <div className="flex flex-wrap gap-1.5 items-center pt-2 border-t border-white/10">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 mr-1">Filter</span>
        {chip(
          "all",
          "All",
          total,
          "bg-white/15 text-white",
          "bg-white/5 text-slate-300 hover:bg-white/10",
        )}
        {chip(
          "approved",
          "Approved",
          tcs?.by_status.approved ?? 0,
          "bg-emerald-500/40 text-white",
          "bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25",
        )}
        {chip(
          "draft",
          "Draft",
          tcs?.by_status.draft ?? 0,
          "bg-slate-500/50 text-white",
          "bg-slate-500/20 text-slate-200 hover:bg-slate-500/30",
        )}
        {chip(
          "rejected",
          "Rejected",
          tcs?.by_status.rejected ?? 0,
          "bg-red-500/40 text-white",
          "bg-red-500/15 text-red-200 hover:bg-red-500/25",
        )}
        {chip(
          "stale",
          "Stale",
          tcs?.by_status.stale ?? 0,
          "bg-amber-500/40 text-white",
          "bg-amber-500/15 text-amber-200 hover:bg-amber-500/25",
        )}
        <span className="mx-2 text-slate-700">|</span>
        {chip(
          "has-script",
          "Has script",
          built,
          "bg-cyan-500/40 text-white",
          "bg-cyan-500/15 text-cyan-200 hover:bg-cyan-500/25",
        )}
        {chip(
          "no-script",
          "No script",
          noScript,
          "bg-purple-500/40 text-white",
          "bg-purple-500/15 text-purple-200 hover:bg-purple-500/25",
        )}
      </div>
    </AnimatedCard>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent: string;
}) {
  return (
    <div className="bg-white/5 border border-white/10 rounded-xl px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`text-2xl font-bold ${accent}`}>{value}</div>
    </div>
  );
}

function Legend({ dot, label, count }: { dot: string; label: string; count: number }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className="w-2 h-2 rounded-full inline-block"
        style={{ backgroundColor: dot }}
      />
      <span className="text-slate-300">{label}</span>
      <span className="text-slate-500 ml-auto">{count}</span>
    </div>
  );
}
