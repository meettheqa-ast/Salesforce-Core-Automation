"use client";

/**
 * CreateProjectModal
 *
 * Reusable creation surface for new projects. Lifted from the inline
 * modal that used to live in `frontend/src/app/projects/page.tsx` so the
 * same form can be used from /admin/projects, /dashboard, the top-nav
 * Quick create menu, and "+ Create new project..." entries inside the
 * sprint / story create modals.
 *
 * The Salesforce sandbox username/password fields use the same
 * decoy-input pattern as the original (Chrome ignores autoComplete="off"
 * on credential-shaped inputs but stops autofilling once it finds an
 * earlier match), so users don't accidentally save their Google login
 * credentials into the sandbox config.
 */

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import GlassSelect from "@/components/ui/GlassSelect";
import { api } from "@/lib/api";

export type CreatedProject = {
  name: string;
  display_name: string;
  path: string;
  project_id: string;
};

const EMPTY_FORM = {
  name: "",
  description: "",
  environment: "Dev",
  sandbox_url: "",
  username: "",
  password: "",
};

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated?: (project: CreatedProject) => void;
}

export default function CreateProjectModal({ open, onClose, onCreated }: Props) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  // Reset on open so a previous attempt's values don't bleed through.
  useEffect(() => {
    if (open) {
      setForm(EMPTY_FORM);
      setError("");
    }
  }, [open]);

  const handleCreate = async () => {
    if (!form.name.trim()) {
      setError("Name is required");
      return;
    }
    setCreating(true);
    setError("");
    try {
      const created = (await api.projects.create(form)) as CreatedProject;
      onCreated?.(created);
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not create project");
    } finally {
      setCreating(false);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={onClose}
        >
          <motion.div
            initial={{ scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.9, opacity: 0 }}
            className="glass-strong p-6 w-full max-w-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-xl font-bold text-white mb-4">Create Project</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleCreate();
              }}
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
              {/* Decoy username/password inputs absorb Chrome's autofill so the
                  Salesforce credential fields below stay clean. See the
                  original /projects/page.tsx for the full rationale. */}
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
              <motion.button
                whileTap={{ scale: 0.95 }}
                onClick={handleCreate}
                disabled={creating}
                className="flex-1 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm disabled:opacity-50"
              >
                {creating ? "Creating..." : "Create Project"}
              </motion.button>
              <button
                onClick={onClose}
                className="px-4 py-2.5 glass text-slate-400 rounded-xl text-sm hover:text-white transition-colors"
              >
                Cancel
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
