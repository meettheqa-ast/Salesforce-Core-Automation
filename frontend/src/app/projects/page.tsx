"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { api } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", environment: "Dev", sandbox_url: "", username: "", password: "" });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const loadProjects = () => {
    api.projects.list().then(setProjects).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(loadProjects, []);

  const handleCreate = async () => {
    if (!form.name.trim()) { setError("Name is required"); return; }
    setCreating(true); setError("");
    try {
      await api.projects.create(form);
      setShowCreate(false);
      setForm({ name: "", description: "", environment: "Dev", sandbox_url: "", username: "", password: "" });
      loadProjects();
    } catch (err: any) { setError(err.message); }
    finally { setCreating(false); }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete project "${name}"?`)) return;
    await api.projects.delete(name).catch(() => {});
    loadProjects();
  };

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-4xl font-bold">
            <span className="bg-gradient-to-r from-pink-400 to-purple-400 bg-clip-text text-transparent">Projects</span>
          </h1>
          <p className="text-slate-400">Manage test projects, environments, and credentials.</p>
        </div>
        <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }} onClick={() => setShowCreate(true)}
          className="px-5 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm">
          + New Project
        </motion.button>
      </motion.div>

      {/* Create Modal */}
      <AnimatePresence>
        {showCreate && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowCreate(false)}>
            <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.9, opacity: 0 }}
              className="glass-strong p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
              <h2 className="text-xl font-bold text-white mb-4">Create Project</h2>
              {/* The autoComplete="new-password" + name="...-randomToken" pattern stops
                  Chrome/Edge from autofilling the user's saved Google credentials into
                  the Salesforce sandbox username/password fields. Plain `autoComplete="off"`
                  is largely ignored by Chrome on credential-shaped inputs. */}
              <form
                onSubmit={(e) => { e.preventDefault(); handleCreate(); }}
                autoComplete="off"
                className="space-y-3"
              >
                <input
                  name="project-name"
                  autoComplete="off"
                  placeholder="Project Name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 outline-none focus:border-purple-500"
                />
                <input
                  name="project-description"
                  autoComplete="off"
                  placeholder="Description (optional)"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 outline-none focus:border-purple-500"
                />
                <GlassSelect
                  className="w-full"
                  value={form.environment}
                  onChange={(v) => setForm({ ...form, environment: v })}
                  placeholder="Environment"
                  options={["Dev", "QA", "UAT", "Prod"].map((e) => ({ value: e, label: e }))}
                />
                <input
                  name="sandbox-url"
                  autoComplete="off"
                  placeholder="Sandbox URL"
                  value={form.sandbox_url}
                  onChange={(e) => setForm({ ...form, sandbox_url: e.target.value })}
                  className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 outline-none focus:border-purple-500"
                />
                {/* Decoy fields hidden off-screen: Chrome ignores autoComplete="off"
                    on credential-shaped inputs but WILL stop autofilling if it sees
                    a "match" earlier in the form. These hidden inputs absorb the
                    autofill instead of leaking it into the Salesforce fields below. */}
                <input
                  type="text"
                  name="username"
                  autoComplete="username"
                  tabIndex={-1}
                  aria-hidden="true"
                  style={{ position: "absolute", left: "-9999px", width: 1, height: 1, opacity: 0 }}
                  readOnly
                />
                <input
                  type="password"
                  name="password"
                  autoComplete="current-password"
                  tabIndex={-1}
                  aria-hidden="true"
                  style={{ position: "absolute", left: "-9999px", width: 1, height: 1, opacity: 0 }}
                  readOnly
                />
                <div className="grid grid-cols-2 gap-3">
                  <input
                    name="sf-sandbox-username"
                    autoComplete="off"
                    placeholder="Sandbox Username"
                    value={form.username}
                    onChange={(e) => setForm({ ...form, username: e.target.value })}
                    className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 outline-none focus:border-purple-500"
                  />
                  <input
                    type="password"
                    name="sf-sandbox-password"
                    autoComplete="new-password"
                    placeholder="Sandbox Password"
                    value={form.password}
                    onChange={(e) => setForm({ ...form, password: e.target.value })}
                    className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-300 outline-none focus:border-purple-500"
                  />
                </div>
              </form>
              {error && <p className="text-red-400 text-xs mt-2">{error}</p>}
              <div className="flex gap-3 mt-5">
                <motion.button whileTap={{ scale: 0.95 }} onClick={handleCreate} disabled={creating}
                  className="flex-1 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm disabled:opacity-50">
                  {creating ? "Creating..." : "Create Project"}
                </motion.button>
                <button onClick={() => setShowCreate(false)} className="px-4 py-2.5 glass text-slate-400 rounded-xl text-sm hover:text-white transition-colors">
                  Cancel
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Project Grid */}
      {loading ? (
        <div className="flex justify-center py-20">
          <div className="flex gap-1">
            {[0, 1, 2].map((i) => (
              <motion.div key={i} className="w-3 h-3 rounded-full bg-purple-400"
                animate={{ y: [0, -10, 0] }} transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.15 }} />
            ))}
          </div>
        </div>
      ) : projects.length === 0 && !showCreate ? (
        <AnimatedCard glow="purple" className="text-center py-12">
          <div className="text-4xl mb-4">📂</div>
          <h3 className="text-xl font-bold text-white mb-2">No Projects Yet</h3>
          <p className="text-slate-400 mb-6">Create your first project to organize tests and credentials.</p>
          <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }} onClick={() => setShowCreate(true)}
            className="px-6 py-3 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl">
            Create First Project
          </motion.button>
        </AnimatedCard>
      ) : (
        <div className="grid md:grid-cols-3 gap-5">
          <AnimatePresence>
            {projects.map((name, i) => (
              <motion.div key={name} initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }} transition={{ delay: i * 0.08 }}>
                <Link href={`/projects/${encodeURIComponent(name)}`}>
                  <AnimatedCard glow="purple" className="cursor-pointer group">
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="text-2xl mb-2">📁</div>
                        <h3 className="text-lg font-bold text-white mb-1">{name}</h3>
                        <p className="text-xs text-slate-500">Click to manage</p>
                      </div>
                      <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(name); }}
                        className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-400 transition-all text-sm p-1">
                        ✕
                      </button>
                    </div>
                  </AnimatedCard>
                </Link>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
