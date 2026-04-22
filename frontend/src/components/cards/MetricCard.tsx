"use client";

import { motion } from "framer-motion";

interface MetricCardProps {
  label: string;
  value: string | number;
  icon: string;
  color?: "purple" | "cyan" | "pink" | "green";
  delay?: number;
}

const colorMap = {
  purple: "from-purple-500/20 to-purple-900/10 border-purple-500/30",
  cyan: "from-cyan-500/20 to-cyan-900/10 border-cyan-500/30",
  pink: "from-pink-500/20 to-pink-900/10 border-pink-500/30",
  green: "from-emerald-500/20 to-emerald-900/10 border-emerald-500/30",
};

const textColorMap = {
  purple: "text-purple-400",
  cyan: "text-cyan-400",
  pink: "text-pink-400",
  green: "text-emerald-400",
};

export default function MetricCard({ label, value, icon, color = "purple", delay = 0 }: MetricCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.5, delay, type: "spring" }}
      whileHover={{ scale: 1.03 }}
      className={`bg-gradient-to-br ${colorMap[color]} border rounded-2xl p-5 backdrop-blur-sm`}
    >
      <div className="text-2xl mb-2">{icon}</div>
      <div className={`text-3xl font-bold ${textColorMap[color]}`}>{value}</div>
      <div className="text-sm text-slate-400 mt-1">{label}</div>
    </motion.div>
  );
}
