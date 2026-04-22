"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import AnimatedCard from "@/components/cards/AnimatedCard";
import MetricCard from "@/components/cards/MetricCard";
import RobotCodeEditor from "@/components/editor/RobotCodeEditor";
import GlassSelect from "@/components/ui/GlassSelect";
import { api } from "@/lib/api";

const DEFAULT_PERSONA = "System Admin";

type CredFields = {
  sandbox_url: string;
  username: string;
  password: string;
  security_token: string;
  slack_webhook_url: string;
};

const EMPTY_CREDS: CredFields = {
  sandbox_url: "",
  username: "",
  password: "",
  security_token: "",
  slack_webhook_url: "",
};

type TestRow = { name: string; path: string; modified: string };
type AnalyticsSummary = { pass_rate?: number; total_runs?: number };
type ConfirmTarget =
  | { kind: "env"; env: string }
  | { kind: "persona"; env: string; persona: string };

export default function ProjectDetailPage() {
  const params = useParams();
  const name = decodeURIComponent(params.name as string);

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
        <h1 className="text-4xl font-bold mb-2">
          <span className="bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
            {name}
          </span>
        </h1>
      </motion.div>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6 mb-6">
        <MetricCard icon="🌐" label="Environments" value={environments.length} color="purple" delay={0} />
        <MetricCard icon="📄" label="Saved Tests" value={tests.length} color="cyan" delay={0.1} />
        <MetricCard
          icon="✅"
          label="Pass Rate"
          value={analytics?.pass_rate != null ? `${analytics.pass_rate}%` : "--"}
          color="green"
          delay={0.2}
        />
        <MetricCard icon="🔄" label="Total Runs" value={analytics?.total_runs ?? 0} color="pink" delay={0.3} />
      </div>

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
