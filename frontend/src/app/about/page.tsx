"use client";

import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";

export default function AboutPage() {
  return (
    <div className="max-w-3xl mx-auto px-6 py-16">
      <motion.div
        initial={{ opacity: 0, y: 40 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
        className="text-center"
      >
        <div className="text-6xl mb-6">🧪</div>
        <h1 className="text-4xl font-extrabold mb-4">
          <span className="bg-gradient-to-r from-purple-400 via-cyan-400 to-pink-400 bg-clip-text text-transparent">
            About AI QA Portal
          </span>
        </h1>
      </motion.div>

      <AnimatedCard delay={0.2} glow="purple" className="mt-8">
        <p className="text-slate-300 leading-relaxed mb-6">
          AI QA Portal is a test intelligence platform that allows users to generate, execute,
          and manage automated test scripts using natural language. It integrates with Salesforce
          environments and simplifies QA workflows using AI-driven automation.
        </p>

        <div className="grid grid-cols-2 gap-4 mb-6">
          {[
            { label: "AI Providers", value: "Gemini, GPT-4, Claude, Cohere" },
            { label: "Test Framework", value: "Robot Framework + Selenium" },
            { label: "Platform", value: "Salesforce Lightning" },
            { label: "Generation", value: "MCP Stepwise + Quick Generate" },
          ].map((item) => (
            <div key={item.label} className="glass p-3 rounded-xl">
              <div className="text-xs text-slate-500 mb-1">{item.label}</div>
              <div className="text-sm text-slate-300 font-medium">{item.value}</div>
            </div>
          ))}
        </div>

        <div className="border-t border-white/5 pt-4 text-center">
          <p className="text-sm text-slate-500">
            Created with <span className="text-red-400">❤️</span> by Meet
          </p>
          <p className="text-xs text-slate-600 mt-1">Astound Digital &middot; 2026</p>
        </div>
      </AnimatedCard>
    </div>
  );
}
