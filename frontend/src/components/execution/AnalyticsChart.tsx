"use client";

import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";

interface AnalyticsChartProps {
  data: { run_name: string; passed: number; failed: number; skipped?: number; total?: number }[];
}

export default function AnalyticsChart({ data }: AnalyticsChartProps) {
  if (!data.length) return <p className="text-sm text-slate-500">No run history available.</p>;

  const totalAssertions = data.reduce(
    (sum, d) => sum + (d.passed || 0) + (d.failed || 0) + (d.skipped || 0),
    0,
  );
  if (totalAssertions === 0) {
    return (
      <p className="text-sm text-slate-500">
        Runs found, but no assertions were captured. Generate and run a test to see the trend populate.
      </p>
    );
  }

  const chartData = data.slice(-15).map((d) => ({
    name: d.run_name.length > 12 ? d.run_name.slice(0, 12) + "…" : d.run_name,
    Passed: d.passed,
    Failed: d.failed,
    Skipped: d.skipped ?? 0,
  }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={chartData} barGap={2}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
        <XAxis dataKey="name" tick={{ fill: "#64748b", fontSize: 10 }} />
        <YAxis tick={{ fill: "#64748b", fontSize: 11 }} allowDecimals={false} />
        <Tooltip
          contentStyle={{ background: "#1e293b", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 10, fontSize: 12 }}
          labelStyle={{ color: "#e2e8f0" }}
        />
        <Legend wrapperStyle={{ fontSize: 11, color: "#cbd5e1" }} />
        <Bar dataKey="Passed" stackId="x" fill="#10b981" radius={[4, 4, 0, 0]} />
        <Bar dataKey="Failed" stackId="x" fill="#ef4444" />
        <Bar dataKey="Skipped" stackId="x" fill="#f59e0b" />
      </BarChart>
    </ResponsiveContainer>
  );
}
