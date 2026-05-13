"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import AnimatedCard from "@/components/cards/AnimatedCard";
import StatusDonut, { DONUT_COLORS } from "@/components/charts/StatusDonut";
import CreateProjectModal from "@/components/projects/CreateProjectModal";
import { api, type InvitationRow } from "@/lib/api";
import { notifyTreeRefresh } from "@/lib/useTreeRefresh";
import { useMe, membershipFor } from "@/lib/useMe";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

/** Minimal per-project stats fetched on the list page so each tile can
 *  show its own donut + counts without drilling in. Both fields default
 *  to safe shapes so an in-flight tile renders an empty donut, not crash. */
type ProjectStats = {
  total: number;
  by_status: { draft: number; approved: number; rejected: number; stale: number };
  scripts_built: number;
  story_count: number;
  pass_rate?: number;
  total_runs?: number;
};

const AUTH_DISABLED =
  (process.env.NEXT_PUBLIC_AUTH_DISABLED || "").toLowerCase() === "true";

export default function ProjectsPage() {
  const { me } = useMe();
  const [projects, setProjects] = useState<string[]>([]);
  const [otherProjects, setOtherProjects] = useState<Array<{ name: string; display_name: string; description: string; member_count: number }>>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState("");
  const [requesting, setRequesting] = useState<string | null>(null);
  const [requestSuccess, setRequestSuccess] = useState<string | null>(null);
  const [openInvites, setOpenInvites] = useState<InvitationRow[]>([]);
  /** Stats keyed by project slug. Populated lazily after the project list
   *  loads -- two requests per project (testCases + analytics) fan out in
   *  parallel so the tiles fill in within one round-trip burst. */
  const [stats, setStats] = useState<Record<string, ProjectStats>>({});

  // Anyone in the org can create a project (becomes PM of the new one).
  // Phase 2c will add stricter "TM cannot create" toggle if you want.
  const canCreateProject = AUTH_DISABLED || !!me;

  const loadProjects = () => {
    setLoading(true);
    Promise.all([
      api.projects.list().then(setProjects).catch(() => {}),
      // Discoverable list -- only fetched when auth is on; in disabled mode
      // the synthetic admin sees everything in `projects` already.
      AUTH_DISABLED
        ? Promise.resolve()
        : api.projects.discoverable().then(setOtherProjects).catch(() => {}),
      AUTH_DISABLED
        ? Promise.resolve()
        : api.invitations.mine().then(setOpenInvites).catch(() => {}),
    ]).finally(() => setLoading(false));
  };

  useEffect(loadProjects, []);

  // Fan-out per-project stats fetch. Two requests per project run in
  // parallel; results land into the `stats` map keyed by slug as they
  // arrive so each tile renders its donut as soon as its row is ready.
  useEffect(() => {
    if (projects.length === 0) return;
    let cancelled = false;
    projects.forEach((name) => {
      void Promise.all([
        api.projects.testCases(name).catch(() => null),
        api.analytics.summary(name).catch(() => null),
      ]).then(([tcs, summary]) => {
        if (cancelled) return;
        if (!tcs) return;
        setStats((prev) => ({
          ...prev,
          [name]: {
            total: tcs.total,
            by_status: tcs.by_status,
            scripts_built: tcs.scripts_built,
            story_count: tcs.stories.length,
            pass_rate: summary?.pass_rate,
            total_runs: summary?.total_runs,
          },
        }));
      });
    });
    return () => {
      cancelled = true;
    };
  }, [projects]);

  const handleAcceptInvite = async (id: string) => {
    try {
      await api.invitations.accept(id);
      setOpenInvites((prev) => prev.filter((i) => i.id !== id));
      loadProjects();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to accept invitation");
    }
  };

  const handleDeclineInvite = async (id: string) => {
    if (!confirm("Decline this invitation?")) return;
    try {
      await api.invitations.reject(id);
      setOpenInvites((prev) => prev.filter((i) => i.id !== id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to decline");
    }
  };

  const handleRequestAccess = async (slug: string) => {
    setRequesting(slug); setError(""); setRequestSuccess(null);
    try {
      await api.projects.requestAccess(slug);
      setRequestSuccess(`Access request sent to the project's PMs and Team Leads.`);
      // Remove from "other" list since it's now in flight.
      setOtherProjects((prev) => prev.filter((p) => p.name !== slug));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to request access");
    } finally {
      setRequesting(null);
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete project "${name}"?`)) return;
    await api.projects.delete(name).catch(() => {});
    loadProjects();
  };

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Delivery"
          title="Projects"
          description="Manage project scope, ownership, environments, and access."
          actions={
            canCreateProject ? (
              <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }} onClick={() => setShowCreate(true)}
                className="px-5 py-2.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm">
                + New Project
              </motion.button>
            ) : undefined
          }
        />
      </motion.div>

      {/* Pending invitations awaiting accept/decline */}
      {openInvites.length > 0 && (
        <div className="mb-6 space-y-2">
          {openInvites.map((inv) => (
            <div
              key={inv.id}
              className="flex items-center gap-3 rounded-xl border border-purple-500/30 bg-purple-500/10 px-4 py-3"
            >
              <div className="text-xl">📨</div>
              <div className="flex-1 min-w-0 text-sm text-white">
                You&apos;ve been invited to <span className="font-semibold">{inv.project_slug}</span>
                <span className="ml-2 text-[11px] text-slate-400">as {inv.role}</span>
              </div>
              <button
                onClick={() => handleAcceptInvite(inv.id)}
                className="text-xs px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-500"
              >
                Accept
              </button>
              <button
                onClick={() => handleDeclineInvite(inv.id)}
                className="text-xs px-3 py-1.5 rounded text-slate-400 hover:text-red-400"
              >
                Decline
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Create Modal -- lifted to the shared CreateProjectModal so the
          same form is also reachable from the top-nav Quick create
          menu, /admin/projects, /dashboard, and the stacked
          "+ Create new project..." options inside the sprint and story
          create modals. */}
      <CreateProjectModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={() => {
          loadProjects();
          // Also poke the global sidebar + Cmd+K palette so the new
          // project shows up everywhere without a hard reload.
          notifyTreeRefresh({ kind: "project" });
        }}
      />

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
            {projects.map((name, i) => {
              const role = membershipFor(me, name);
              const isPm = role === "pm";
              const roleLabel = role
                ? role === "pm" ? "Project Manager" : role === "lead" ? "Team Lead" : "Team Member"
                : null;
              return (
              <motion.div key={name} initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }} transition={{ delay: i * 0.08 }}>
                <ProjectTile
                  name={name}
                  roleLabel={roleLabel}
                  canDelete={isPm || AUTH_DISABLED}
                  stats={stats[name]}
                  onDelete={() => handleDelete(name)}
                />
              </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      )}

      {/* Other projects in the org (Phase 2c -- self-service access requests) */}
      {!AUTH_DISABLED && otherProjects.length > 0 && (
        <div className="mt-10">
          <h2 className="text-sm uppercase tracking-wider text-slate-500 font-semibold mb-3">
            Other projects in the org
          </h2>
          {requestSuccess && (
            <div className="mb-4 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-2.5 text-xs text-emerald-200">
              {requestSuccess}
            </div>
          )}
          <div className="grid md:grid-cols-3 gap-5">
            {otherProjects.map((p) => (
              <AnimatedCard key={p.name}>
                <div className="text-2xl mb-2">📁</div>
                <h3 className="text-base font-bold text-white mb-1">{p.display_name || p.name}</h3>
                {p.description && (
                  <p className="text-xs text-slate-400 mb-3 line-clamp-2">{p.description}</p>
                )}
                <div className="text-[10px] text-slate-500 mb-3">
                  {p.member_count} member{p.member_count === 1 ? "" : "s"}
                </div>
                <button
                  type="button"
                  disabled={requesting === p.name}
                  onClick={() => handleRequestAccess(p.name)}
                  className="w-full px-3 py-2 rounded-lg glass text-sm text-slate-200 hover:text-white hover:bg-purple-500/15 transition disabled:opacity-50"
                >
                  {requesting === p.name ? "Requesting..." : "Request access"}
                </button>
              </AnimatedCard>
            ))}
          </div>
        </div>
      )}
    </PageScaffold>
  );
}


/**
 * Per-project tile on the project list. Shows a mini status donut + counts
 * the moment its stats arrive. Status chips link to
 * `/projects/<name>?filter=<id>` so a click drills straight into the
 * detail page already filtered to that slice.
 */
function ProjectTile({
  name,
  roleLabel,
  canDelete,
  stats,
  onDelete,
}: {
  name: string;
  roleLabel: string | null;
  canDelete: boolean;
  stats: ProjectStats | undefined;
  onDelete: () => void;
}) {
  const slices = stats
    ? [
        { id: "approved", label: "Approved", count: stats.by_status.approved, color: DONUT_COLORS.approved },
        { id: "draft", label: "Draft", count: stats.by_status.draft, color: DONUT_COLORS.draft },
        { id: "rejected", label: "Rejected", count: stats.by_status.rejected, color: DONUT_COLORS.rejected },
        { id: "stale", label: "Stale", count: stats.by_status.stale, color: DONUT_COLORS.stale },
      ]
    : [];

  return (
    <div className="relative group">
      <Link href={`/projects/${encodeURIComponent(name)}`}>
        <AnimatedCard glow="purple" className="cursor-pointer">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <h3 className="text-lg font-bold text-white mb-1 truncate">{name}</h3>
              {roleLabel && (
                <span className="inline-block text-[10px] uppercase tracking-wider px-2 py-0.5 rounded bg-purple-500/20 text-purple-200 font-semibold mb-2">
                  {roleLabel}
                </span>
              )}
              {stats ? (
                <div className="text-[11px] text-slate-400 flex flex-wrap gap-x-3">
                  <span>{stats.story_count} {stats.story_count === 1 ? "story" : "stories"}</span>
                  <span>{stats.total} test {stats.total === 1 ? "case" : "cases"}</span>
                  {typeof stats.pass_rate === "number" && (
                    <span className="text-emerald-300">{stats.pass_rate.toFixed(1)}% pass</span>
                  )}
                </div>
              ) : (
                <div className="text-[11px] text-slate-500">Loading stats…</div>
              )}
            </div>
            <div className="shrink-0">
              <StatusDonut
                slices={slices}
                size={72}
                thickness={10}
                centerSubtitle="cases"
                ariaLabel={`${name} test case status`}
              />
            </div>
          </div>

          {stats && stats.total > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {(
                [
                  { id: "approved", label: "Approved", count: stats.by_status.approved, cls: "bg-emerald-500/20 text-emerald-200 hover:bg-emerald-500/30" },
                  { id: "draft", label: "Draft", count: stats.by_status.draft, cls: "bg-slate-500/30 text-slate-200 hover:bg-slate-500/40" },
                  { id: "stale", label: "Stale", count: stats.by_status.stale, cls: "bg-amber-500/20 text-amber-200 hover:bg-amber-500/30" },
                  { id: "rejected", label: "Rejected", count: stats.by_status.rejected, cls: "bg-red-500/20 text-red-200 hover:bg-red-500/30" },
                ] as const
              )
                .filter((c) => c.count > 0)
                .map((c) => (
                  <Link
                    key={c.id}
                    href={`/projects/${encodeURIComponent(name)}?filter=${c.id}`}
                    onClick={(e) => e.stopPropagation()}
                    className={`text-[10px] px-2 py-0.5 rounded-full transition-colors ${c.cls}`}
                  >
                    {c.label} {c.count}
                  </Link>
                ))}
              {stats.scripts_built > 0 && (
                <Link
                  href={`/projects/${encodeURIComponent(name)}?filter=has-script`}
                  onClick={(e) => e.stopPropagation()}
                  className="text-[10px] px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-200 hover:bg-cyan-500/30 transition-colors"
                >
                  Scripts {stats.scripts_built}/{stats.total}
                </Link>
              )}
            </div>
          )}
        </AnimatedCard>
      </Link>
      {canDelete && (
        <button
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDelete(); }}
          title="Delete project (PM/Admin only)"
          className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 text-slate-500 hover:text-red-400 transition-all text-sm p-1 z-10"
        >
          ✕
        </button>
      )}
    </div>
  );
}
