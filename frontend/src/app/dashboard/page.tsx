"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import MetricCard from "@/components/cards/MetricCard";
import AnimatedCard from "@/components/cards/AnimatedCard";
import AnalyticsChart from "@/components/execution/AnalyticsChart";
import { api } from "@/lib/api";
import GlassSelect from "@/components/ui/GlassSelect";

export default function DashboardPage() {
  const [projects, setProjects] = useState<string[]>([]);
  const [selectedProject, setSelectedProject] = useState("");
  const [mcpStatus, setMcpStatus] = useState<any>(null);
  const [latestRuns, setLatestRuns] = useState<any[]>([]);
  const [analytics, setAnalytics] = useState<any>(null);

  useEffect(() => {
    api.mcp.health().then(setMcpStatus).catch(() => setMcpStatus({ running: false }));
    api.runs.latest().then((d) => setLatestRuns(d.runs || [])).catch(() => {});
    api.projects.list().then((p) => { setProjects(p); if (p.length) setSelectedProject(p[0]); }).catch(() => {});
  }, []);

  useEffect(() => {
    if (selectedProject) {
      api.analytics.summary(selectedProject).then(setAnalytics).catch(() => setAnalytics(null));
    }
  }, [selectedProject]);

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-4xl font-bold">
            <span className="bg-gradient-to-r from-cyan-400 to-purple-400 bg-clip-text text-transparent">Dashboard</span>
          </h1>
          <p className="text-slate-400">Monitor test runs, system health, and performance.</p>
        </div>
        <div className="flex items-center gap-3">
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
          <Link href="/generate">
            <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}
              className="px-4 py-2 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-semibold rounded-xl text-sm">
              + New Test
            </motion.button>
          </Link>
        </div>
      </motion.div>

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
          <h3 className="text-sm font-bold text-white mb-4">Pass / Fail Trend</h3>
          <AnalyticsChart data={analytics?.history || []} />
        </AnimatedCard>

        {/* Recent Runs */}
        <AnimatedCard delay={0.4} glow="cyan">
          <h3 className="text-sm font-bold text-white mb-4">Recent Runs</h3>
          {latestRuns.length === 0 ? (
            <p className="text-slate-500 text-sm">No runs yet. Generate and run a test to see results.</p>
          ) : (
            <div className="space-y-2 max-h-72 overflow-y-auto">
              {latestRuns.map((run, i) => (
                <motion.div key={run.name} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.5 + i * 0.08 }}
                  className="flex items-center justify-between p-3 glass rounded-xl">
                  <div>
                    <div className="text-sm font-medium text-white">{run.name}</div>
                  </div>
                  <div className="flex gap-2">
                    {run.has_log && (
                      <a href={`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/results/${run.name}/log.html`}
                        target="_blank" className="text-xs px-2 py-1 bg-purple-500/20 text-purple-300 rounded-lg hover:bg-purple-500/30">
                        Log
                      </a>
                    )}
                    {run.has_report && (
                      <a href={`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/results/${run.name}/report.html`}
                        target="_blank" className="text-xs px-2 py-1 bg-cyan-500/20 text-cyan-300 rounded-lg hover:bg-cyan-500/30">
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
    </div>
  );
}
