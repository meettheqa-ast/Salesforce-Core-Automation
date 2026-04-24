"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { api, type AdminProject } from "@/lib/api";
import { useMe } from "@/lib/useMe";

export default function AdminProjectsPage() {
  const { me } = useMe();
  const [projects, setProjects] = useState<AdminProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.admin.listProjects()
      .then(setProjects)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load projects"))
      .finally(() => setLoading(false));
  }, []);

  if (me && !me.is_admin) {
    return <div className="max-w-2xl mx-auto px-6 py-16 text-center text-slate-400">Admin only</div>;
  }

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="flex items-center gap-3 text-xs text-slate-500 mb-2">
        <Link href="/admin" className="hover:text-white">Admin</Link>
        <span>/</span>
        <span className="text-slate-300">Projects</span>
      </div>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-3xl font-bold">
          <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
            Projects
          </span>
        </h1>
        <p className="text-sm text-slate-400 mt-1">{projects.length} project{projects.length === 1 ? "" : "s"} in the org</p>
      </motion.div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-slate-500">Loading...</p>
      ) : (
        <div className="grid md:grid-cols-2 gap-4">
          {projects.map((p) => (
            <AnimatedCard key={p.name}>
              <div className="flex items-start gap-3">
                <div className="text-2xl">📁</div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-base font-bold text-white">{p.display_name || p.name}</h3>
                  {p.description && <p className="text-xs text-slate-400 mt-1 line-clamp-2">{p.description}</p>}
                  <div className="text-[10px] text-slate-500 mt-2">
                    {p.member_count} member{p.member_count === 1 ? "" : "s"} · {p.pm_count} PM{p.pm_count === 1 ? "" : "s"}
                  </div>
                  {p.pms.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {p.pms.map((pm) => (
                        <span key={pm.user_id} className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-200">
                          {pm.name || pm.email}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <Link
                  href={`/projects/${encodeURIComponent(p.name)}/members`}
                  className="text-xs text-cyan-300 hover:text-cyan-200 shrink-0"
                >
                  Manage &rarr;
                </Link>
              </div>
            </AnimatedCard>
          ))}
        </div>
      )}
    </div>
  );
}
