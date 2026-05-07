"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import GlassSelect from "@/components/ui/GlassSelect";
import { api } from "@/lib/api";

const DEFAULT_PERSONA = "System Admin";

export type WorkspaceCreds = {
  sandboxUrl: string;
  username: string;
  password: string;
  project: string;
  environment: string;
  persona: string;
  /** Persona's default Salesforce app (e.g. "Pentair Sales"). Threaded
   *  to the generate endpoints so the LLM can frame the script around the
   *  right app instead of falling back to the global "Sales" default. */
  defaultApp: string;
};

interface WorkspaceBarProps {
  onChange: (creds: WorkspaceCreds) => void;
}

export default function WorkspaceBar({ onChange }: WorkspaceBarProps) {
  const onChangeRef = useRef(onChange);
  useEffect(() => { onChangeRef.current = onChange; });

  const [projects, setProjects] = useState<string[]>([]);
  const [project, setProject] = useState("");
  const [environments, setEnvironments] = useState<string[]>([]);
  const [environment, setEnvironment] = useState("");
  const [personas, setPersonas] = useState<string[]>([DEFAULT_PERSONA]);
  const [persona, setPersona] = useState(DEFAULT_PERSONA);
  const [sandboxUrl, setSandboxUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  // Initial project list
  useEffect(() => {
    api.projects.list().then((rows) => {
      setProjects(rows);
      try {
        const last = window.localStorage.getItem("ws.project");
        if (last && rows.includes(last)) {
          setProject(last);
        } else if (rows.length) {
          setProject(rows[0]);
        }
      } catch {
        if (rows.length) setProject(rows[0]);
      }
    }).catch(() => setProjects([]));
  }, []);

  // Project -> environments
  useEffect(() => {
    if (!project) {
      void Promise.resolve().then(() => {
        setEnvironments([]);
        setEnvironment("");
      });
      return;
    }
    try { window.localStorage.setItem("ws.project", project); } catch {}
    api.projects.environments(project).then((envs) => {
      setEnvironments(envs);
      try {
        const last = window.localStorage.getItem(`ws.env:${project}`);
        if (last && envs.includes(last)) setEnvironment(last);
        else setEnvironment(envs[0] || "");
      } catch {
        setEnvironment(envs[0] || "");
      }
    }).catch(() => setEnvironments([]));
  }, [project]);

  // Environment -> personas
  useEffect(() => {
    if (!project || !environment) {
      void Promise.resolve().then(() => {
        setPersonas([DEFAULT_PERSONA]);
        setPersona(DEFAULT_PERSONA);
      });
      return;
    }
    try { window.localStorage.setItem(`ws.env:${project}`, environment); } catch {}
    api.projects.personas(project, environment).then((list) => {
      const items = list.length ? list : [DEFAULT_PERSONA];
      setPersonas(items);
      try {
        const last = window.localStorage.getItem(`ws.persona:${project}:${environment}`);
        if (last && items.includes(last)) setPersona(last);
        else setPersona(items[0]);
      } catch {
        setPersona(items[0]);
      }
    }).catch(() => {
      setPersonas([DEFAULT_PERSONA]);
      setPersona(DEFAULT_PERSONA);
    });
  }, [project, environment]);

  // env+persona -> creds
  useEffect(() => {
    if (!project || !environment || !persona) return;
    try { window.localStorage.setItem(`ws.persona:${project}:${environment}`, persona); } catch {}
    api.projects.config(project, environment, persona).then((cfg) => {
      const u = cfg.username || "";
      const p = cfg.password || "";
      const s = cfg.sandbox_url || "";
      // default_app is a string field on the persona config (config.json
      // -> environments.<env>.personas.<name>.default_app). Carry it on
      // every onChange emit so consumers don't have to re-fetch.
      const da = cfg.default_app || "";
      setUsername(u);
      setPassword(p);
      setSandboxUrl(s);
      onChangeRef.current({
        sandboxUrl: s, username: u, password: p,
        project, environment, persona,
        defaultApp: da,
      });
    }).catch(() => {
      setUsername(""); setPassword(""); setSandboxUrl("");
      onChangeRef.current({
        sandboxUrl: "", username: "", password: "",
        project, environment, persona,
        defaultApp: "",
      });
    });
  }, [project, environment, persona]);

  const ready = Boolean(sandboxUrl && username && password);

  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass p-3 mb-4"
    >
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">Workspace</span>
        <span className="text-[10px] text-slate-600">configure who you log in as before generating</span>
      </div>
      <div className="grid grid-cols-12 gap-2 items-center">
        <div className="col-span-12 sm:col-span-3">
          <Label>Project</Label>
          <GlassSelect
            value={project}
            onChange={setProject}
            placeholder="Select project…"
            options={[
              { value: "", label: "Select project…" },
              ...projects.map((p) => ({ value: p, label: p })),
            ]}
          />
        </div>
        <div className="col-span-6 sm:col-span-2">
          <Label>Environment</Label>
          <GlassSelect
            value={environment}
            onChange={setEnvironment}
            disabled={!project}
            placeholder="Env…"
            options={[
              { value: "", label: "Env…" },
              ...environments.map((e) => ({ value: e, label: e })),
            ]}
          />
        </div>
        <div className="col-span-6 sm:col-span-2">
          <Label>User</Label>
          <GlassSelect
            value={persona}
            onChange={setPersona}
            disabled={!environment}
            placeholder="User…"
            options={personas.map((p) => ({ value: p, label: p }))}
          />
        </div>
        <div className="col-span-9 sm:col-span-4">
          <Label>Sandbox</Label>
          <input
            value={sandboxUrl}
            readOnly
            placeholder="sandbox URL will populate from project"
            className="w-full bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-300 outline-none truncate"
            title={sandboxUrl}
          />
        </div>
        <div className="col-span-3 sm:col-span-1 flex items-end justify-end">
          <span
            title={ready ? `Ready: ${username}` : "Pick project / env / user with credentials"}
            className={
              "inline-flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full " +
              (ready
                ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                : "bg-slate-700/40 text-slate-400 border border-white/10")
            }
          >
            <span className={"h-1.5 w-1.5 rounded-full " + (ready ? "bg-emerald-400" : "bg-slate-500")} />
            {ready ? "Login set" : "No login"}
          </span>
        </div>
      </div>
    </motion.div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">{children}</div>;
}
