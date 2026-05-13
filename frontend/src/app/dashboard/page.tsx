"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import MetricCard from "@/components/cards/MetricCard";
import AnimatedCard from "@/components/cards/AnimatedCard";
import AnalyticsChart from "@/components/execution/AnalyticsChart";
import CreateProjectModal from "@/components/projects/CreateProjectModal";
import CreateSprintModal from "@/components/sprints/CreateSprintModal";
import CreateStoryModal from "@/components/user-stories/CreateStoryModal";
import { api, type RunHistoryRow } from "@/lib/api";
import { notifyTreeRefresh } from "@/lib/useTreeRefresh";
import GlassSelect from "@/components/ui/GlassSelect";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

interface McpStatus { running: boolean; url?: string }
interface AnalyticsSummary {
  total_runs: number;
  total_passed: number;
  total_failed: number;
  pass_rate: number;
  avg_duration_s: number;
  history: { run_name: string; timestamp: string; passed: number; failed: number; total: number; duration_s: number }[];
}

function pillCls(status: string): string {
  switch (status) {
    case "PASS": return "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
    case "FAIL": return "bg-red-500/15 text-red-300 border-red-500/30";
    default:     return "bg-slate-700/40 text-slate-300 border-white/10";
  }
}

export default function DashboardPage() {
  const [projects, setProjects] = useState<string[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [mcpStatus, setMcpStatus] = useState<McpStatus | null>(null);
  const [latestRuns, setLatestRuns] = useState<RunHistoryRow[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
  const [timeRangeDays, setTimeRangeDays] = useState<7 | 14 | 30>(14);
  // Quick-create modal toggles. Same modals the top-nav Quick create
  // menu uses, so users get a consistent flow regardless of entry.
  const [showCreateProject, setShowCreateProject] = useState(false);
  const [showCreateSprint, setShowCreateSprint] = useState(false);
  const [showCreateStory, setShowCreateStory] = useState(false);

  useEffect(() => {
    api.mcp.health().then(setMcpStatus).catch(() => setMcpStatus({ running: false }));
    api.runs.latest(15).then((d) => setLatestRuns(d.runs || [])).catch(() => {});
    api.projects.list().then((p) => { setProjects(p); if (p.length) setSelectedProject(p[0]); }).catch(() => {});
  }, []);

  useEffect(() => {
    if (selectedProject) {
      api.analytics.summary(selectedProject).then(setAnalytics).catch(() => setAnalytics(null));
    }
  }, [selectedProject]);

  const filteredHistory = (analytics?.history || []).filter((h: any) => {
    if (!h.timestamp) return true;
    const dt = new Date(h.timestamp);
    if (Number.isNaN(dt.getTime())) return true;
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - timeRangeDays);
    return dt >= cutoff;
  });

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Operations"
          title="Dashboard"
          description="Monitor test runs, system health, and performance."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <GlassSelect
                className="min-w-[10rem]"
                value={selectedProject}
                onChange={setSelectedProject}
                placeholder="All Projects"
                options={[
                  { value: "", label: "All Projects" },
                  ...projects.map((p) => ({ value: p, label: p })),
                ]}
              />
              <button
                type="button"
                onClick={() => setShowCreateProject(true)}
                className="px-3 py-2 rounded-xl glass text-sm text-slate-200 hover:text-white"
              >
                + Project
              </button>
              <button
                type="button"
                onClick={() => setShowCreateSprint(true)}
                className="px-3 py-2 rounded-xl glass text-sm text-slate-200 hover:text-white"
              >
                + Sprint
              </button>
              <button
                type="button"
                onClick={() => setShowCreateStory(true)}
                className="px-3 py-2 rounded-xl glass text-sm text-slate-200 hover:text-white"
              >
                + Story
              </button>
              <Link href={selectedProject ? `/generate?project=${encodeURIComponent(selectedProject)}` : "/generate"}>
                <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}
                  className="px-4 py-2 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm">
                  + New Test
                </motion.button>
              </Link>
            </div>
          }
        />
      </motion.div>

      <CreateProjectModal
        open={showCreateProject}
        onClose={() => setShowCreateProject(false)}
        onCreated={() => {
          api.projects.list().then(setProjects).catch(() => {});
          notifyTreeRefresh({ kind: "project" });
        }}
      />
      <CreateSprintModal
        open={showCreateSprint}
        onClose={() => setShowCreateSprint(false)}
        onCreated={(sprint) => {
          notifyTreeRefresh({ kind: "sprint", projectId: sprint.project_id });
        }}
      />
      <CreateStoryModal
        open={showCreateStory}
        onClose={() => setShowCreateStory(false)}
        onCreated={(story) => {
          notifyTreeRefresh({
            kind: "story",
            projectId: story.project_id,
            sprintId: story.sprint_id || undefined,
          });
        }}
      />

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <MetricCard icon="🔄" label="Total Runs" value={analytics?.total_runs ?? latestRuns.length} color="purple" delay={0} />
        <MetricCard icon="✅" label="Pass Rate" value={analytics ? `${analytics.pass_rate}%` : "--"} color="green" delay={0.1} />
        <MetricCard icon="🤖" label="MCP Server" value={mcpStatus?.running ? "Online" : "Offline"}
          color={mcpStatus?.running ? "green" : "pink"} delay={0.2} />
        <MetricCard icon="⚡" label="Avg Duration" value={analytics ? `${analytics.avg_duration_s}s` : "--"} color="cyan" delay={0.3} />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        {/* Analytics Chart */}
        <AnimatedCard delay={0.3} glow="purple">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-bold text-white">Pass / Fail Trend</h3>
            <GlassSelect
              className="min-w-[9rem]"
              value={String(timeRangeDays)}
              onChange={(v) => setTimeRangeDays(Number(v) as 7 | 14 | 30)}
              options={[
                { value: "7", label: "Last 7 days" },
                { value: "14", label: "Last 14 days" },
                { value: "30", label: "Last 30 days" },
              ]}
            />
          </div>
          <AnalyticsChart data={filteredHistory as any} />
          <div className="mt-3 text-xs text-slate-500">
            {filteredHistory.length} run(s) in selected window. Click any run in "Recent Runs" to drill into detail.
          </div>
        </AnimatedCard>

        {/* Recent Runs */}
        <AnimatedCard delay={0.4} glow="cyan">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-bold text-white">Recent Runs</h3>
            <Link
              href={selectedProject ? `/runs?project=${encodeURIComponent(selectedProject)}` : "/runs"}
              className="text-[11px] text-slate-400 hover:text-white"
            >
              All runs →
            </Link>
          </div>
          {latestRuns.length === 0 ? (
            <p className="text-slate-500 text-sm">No runs yet. Generate and run a test to see results.</p>
          ) : (
            <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
              {latestRuns.map((run, i) => (
                <motion.div
                  key={run.run_name}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.4 + i * 0.04 }}
                  className="flex items-center justify-between gap-2 p-3 glass rounded-xl"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${pillCls(run.status)}`}>
                        {run.status}
                      </span>
                      <span className="text-xs font-mono text-slate-200 truncate" title={run.run_name}>
                        {run.run_name}
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-500">
                      <span className="text-emerald-300">{run.passed} passed</span>
                      <span className="text-slate-600"> · </span>
                      <span className="text-red-300">{run.failed} failed</span>
                      {run.skipped > 0 && (
                        <>
                          <span className="text-slate-600"> · </span>
                          <span className="text-amber-300">{run.skipped} skipped</span>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <Link
                      href={`/runs/${encodeURIComponent(run.run_name)}${
                        selectedProject ? `?project=${encodeURIComponent(selectedProject)}` : ""
                      }`}
                      className="text-[11px] px-2.5 py-1 rounded-md bg-purple-600/30 text-purple-200 border border-purple-500/30 hover:bg-purple-600/40 transition-colors"
                    >
                      Open
                    </Link>
                    {run.report_html && (
                      <a
                        href={api.runs.fileUrl(run.run_name, "report.html")}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[11px] px-2.5 py-1 rounded-md glass text-slate-300 hover:text-white hover:bg-white/10 transition-colors"
                        title="Open Robot's HTML report in a new tab"
                      >
                        Report
                      </a>
                    )}
                  </div>
                </motion.div>
              ))}
            </div>
          )}
        </AnimatedCard>
      </div>
    </PageScaffold>
  );
}
