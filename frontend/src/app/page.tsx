"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";

const ParticleField = dynamic(() => import("@/components/three/ParticleField"), { ssr: false });

const FEATURES = [
  { icon: "🧪", title: "AI Test Generation", desc: "Describe tests in plain English. AI generates Robot Framework scripts instantly.", color: "purple" as const },
  { icon: "⚡", title: "MCP Stepwise", desc: "Step-by-step verified generation with live keyword validation.", color: "cyan" as const },
  { icon: "🔧", title: "Self-Healing", desc: "Smart keywords handle org differences, fix locators, and auto-heal missing fields.", color: "pink" as const },
  { icon: "🚀", title: "Parallel Execution", desc: "Run full suites with Pabot. Auto-retry flaky tests before reporting.", color: "purple" as const },
  { icon: "📊", title: "Analytics Dashboard", desc: "Track pass/fail trends, execution history, and test coverage across projects.", color: "cyan" as const },
  { icon: "🔍", title: "Locator Scanner", desc: "Scan locators against the live DOM. Catch stale selectors before they fail.", color: "pink" as const },
];

export default function LandingPage() {
  return (
    <div className="relative min-h-screen overflow-hidden">
      <ParticleField />

      {/* Hero Section */}
      <section className="relative z-10 flex flex-col items-center justify-center min-h-[85vh] px-6 text-center">
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        >
          <motion.div
            className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full glass-strong text-xs font-semibold text-purple-300 mb-6"
            animate={{ boxShadow: ["0 0 20px rgba(139,92,246,0.2)", "0 0 40px rgba(139,92,246,0.4)", "0 0 20px rgba(139,92,246,0.2)"] }}
            transition={{ duration: 2, repeat: Infinity }}
          >
            <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            Test Intelligence Platform v2.0
          </motion.div>

          <h1 className="text-6xl md:text-7xl font-extrabold tracking-tight mb-4">
            <span className="bg-gradient-to-r from-purple-400 via-cyan-400 to-pink-400 bg-clip-text text-transparent">
              AI QA Portal
            </span>
          </h1>

          <p className="text-xl text-slate-400 max-w-2xl mx-auto mb-10 leading-relaxed">
            Generate Salesforce test automation scripts from natural language.
            No coding required. Powered by AI.
          </p>

          <div className="flex items-center gap-4 justify-center">
            <Link href="/generate">
              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                className="px-8 py-3.5 bg-gradient-to-r from-purple-600 to-cyan-500 text-white font-bold rounded-2xl text-lg animate-pulse-glow"
              >
                Generate Tests with AI
              </motion.button>
            </Link>
            <Link href="/dashboard">
              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                className="px-8 py-3.5 glass-strong text-slate-300 font-semibold rounded-2xl text-lg hover:text-white transition-colors"
              >
                View Dashboard
              </motion.button>
            </Link>
          </div>
        </motion.div>

        {/* Floating cards */}
        <motion.div
          className="absolute -right-10 top-1/3 glass p-4 rounded-2xl w-48 animate-float"
          initial={{ opacity: 0, x: 100 }}
          animate={{ opacity: 0.6, x: 0 }}
          transition={{ delay: 1, duration: 1 }}
        >
          <div className="text-xs text-purple-300 font-semibold mb-1">Test Created</div>
          <div className="text-sm text-slate-400">Lead_CRUD_Verify.robot</div>
          <div className="text-xs text-emerald-400 mt-1">✓ PASSED</div>
        </motion.div>

        <motion.div
          className="absolute -left-5 top-1/2 glass p-4 rounded-2xl w-44 animate-float"
          style={{ animationDelay: "1s" }}
          initial={{ opacity: 0, x: -100 }}
          animate={{ opacity: 0.5, x: 0 }}
          transition={{ delay: 1.3, duration: 1 }}
        >
          <div className="text-xs text-cyan-300 font-semibold mb-1">MCP Stepwise</div>
          <div className="text-sm text-slate-400">Step 4/6 ✓</div>
          <div className="w-full bg-slate-700 rounded-full h-1 mt-2">
            <div className="bg-cyan-400 h-1 rounded-full w-2/3" />
          </div>
        </motion.div>
      </section>

      {/* Features Grid */}
      <section className="relative z-10 max-w-6xl mx-auto px-6 pb-20">
        <motion.h2
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          className="text-3xl font-bold text-center mb-12"
        >
          <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
            Platform Capabilities
          </span>
        </motion.h2>

        <div className="grid md:grid-cols-3 gap-5">
          {FEATURES.map((f, i) => (
            <AnimatedCard key={f.title} delay={i * 0.1} glow={f.color}>
              <div className="text-3xl mb-3">{f.icon}</div>
              <h3 className="text-lg font-bold text-white mb-2">{f.title}</h3>
              <p className="text-sm text-slate-400 leading-relaxed">{f.desc}</p>
            </AnimatedCard>
          ))}
        </div>
      </section>

      {/* Footer */}
      <footer className="relative z-10 border-t border-white/5 py-8 text-center">
        <p className="text-sm text-slate-500">
          {"Created with "}<span className="text-red-400">{"♥"}</span>{" by Meet · Astound Digital · 2026"}
        </p>
      </footer>
    </div>
  );
}
