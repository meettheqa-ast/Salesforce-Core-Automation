"use client";

import { useState, useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";

interface CredentialBarProps {
  onCredentialsChange: (creds: { sandboxUrl: string; username: string; password: string }) => void;
}

export default function CredentialBar({ onCredentialsChange }: CredentialBarProps) {
  const onChangeRef = useRef(onCredentialsChange);
  useEffect(() => { onChangeRef.current = onCredentialsChange; });
  const [mode, setMode] = useState<"manual" | "project">("manual");
  const [projects, setProjects] = useState<string[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [environments, setEnvironments] = useState<string[]>([]);
  const [selectedEnv, setSelectedEnv] = useState("");
  const [sandboxUrl, setSandboxUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    api.projects.list().then(setProjects).catch(() => {});
  }, []);

  useEffect(() => {
    if (mode === "manual") {
      onChangeRef.current({ sandboxUrl, username, password });
    }
  }, [sandboxUrl, username, password, mode]);

  useEffect(() => {
    if (selectedProject) {
      api.projects.environments(selectedProject).then(setEnvironments).catch(() => {});
    }
  }, [selectedProject]);

  useEffect(() => {
    if (selectedProject && selectedEnv) {
      api.projects.config(selectedProject, selectedEnv, "System Admin").then((cfg: Record<string, string>) => {
        const u = cfg.username || "";
        const p = cfg.password || "";
        const s = cfg.sandbox_url || "";
        setSandboxUrl(s);
        setUsername(u);
        setPassword(p);
        onChangeRef.current({ sandboxUrl: s, username: u, password: p });
      }).catch(() => {});
    }
  }, [selectedProject, selectedEnv]);

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass p-4 mb-6"
    >
      <div className="flex items-center gap-4 mb-3">
        <span className="text-xs text-slate-500 uppercase tracking-wider font-semibold">Credentials</span>
        <div className="flex gap-1 glass rounded-lg p-0.5">
          {(["manual", "project"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${
                mode === m ? "bg-purple-600/30 text-purple-300" : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {m === "manual" ? "Manual" : "From Project"}
            </button>
          ))}
        </div>
      </div>

      {mode === "project" ? (
        <div className="grid grid-cols-2 gap-3">
          <GlassSelect
            value={selectedProject}
            placeholder="Select project…"
            onChange={(v) => { setSelectedProject(v); setSelectedEnv(""); }}
            options={[
              { value: "", label: "Select project…" },
              ...projects.map((p) => ({ value: p, label: p })),
            ]}
          />
          <GlassSelect
            value={selectedEnv}
            placeholder="Select environment…"
            onChange={setSelectedEnv}
            disabled={!selectedProject}
            options={[
              { value: "", label: "Select environment…" },
              ...environments.map((e) => ({ value: e, label: e })),
            ]}
          />
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          <input
            type="text"
            placeholder="Sandbox URL"
            value={sandboxUrl}
            onChange={(e) => setSandboxUrl(e.target.value)}
            className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-600 outline-none focus:border-purple-500"
          />
          <input
            type="text"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-600 outline-none focus:border-purple-500"
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-600 outline-none focus:border-purple-500"
          />
        </div>
      )}
    </motion.div>
  );
}
