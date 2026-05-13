"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import CreateProjectModal from "@/components/projects/CreateProjectModal";
import { api, type AdminProject } from "@/lib/api";
import { notifyTreeRefresh } from "@/lib/useTreeRefresh";
import { useMe } from "@/lib/useMe";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

export default function AdminProjectsPage() {
  const { me } = useMe();
  const [projects, setProjects] = useState<AdminProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    api.admin.listProjects()
      .then(setProjects)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load projects"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  if (me && !me.is_admin) {
    return (
      <PageScaffold>
        <div className="py-12 text-center text-slate-400">Admin only</div>
      </PageScaffold>
    );
  }

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
        <PageHeader
          eyebrow="Admin"
          title="Projects"
          description={`${projects.length} project${projects.length === 1 ? "" : "s"} in the org.`}
          actions={
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={() => setShowCreate(true)}
              className="px-5 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm"
            >
              + New project
            </motion.button>
          }
        />

        <CreateProjectModal
          open={showCreate}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            reload();
            notifyTreeRefresh({ kind: "project" });
          }}
        />

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
      </motion.div>
    </PageScaffold>
  );
}
