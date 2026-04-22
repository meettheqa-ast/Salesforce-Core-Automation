"use client";

import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

interface AnalyticsChartProps {
  data: { run_name: string; passed: number; failed: number }[];
}

export default function AnalyticsChart({ data }: AnalyticsChartProps) {
  if (!data.length) return <p className="text-sm text-slate-500">No run history available.</p>;

  const chartData = data.slice(-15).map((d) => ({
    name: d.run_name.length > 12 ? d.run_name.slice(0, 12) + "…" : d.run_name,
    Passed: d.passed,
    Failed: d.failed,
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
        <Bar dataKey="Passed" fill="#10b981" radius={[4, 4, 0, 0]} />
        <Bar dataKey="Failed" fill="#ef4444" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
