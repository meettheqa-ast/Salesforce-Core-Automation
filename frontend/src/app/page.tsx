"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { PageHeader, PageScaffold, PageSection } from "@/components/layout/PageScaffold";
import OnboardingChecklist from "@/components/onboarding/OnboardingChecklist";

const ParticleField = dynamic(() => import("@/components/three/ParticleField"), { ssr: false });

const QUICK_START = [
  { href: "/generate", icon: "🧪", title: "Generate a test", desc: "Create scripts from plain-English scenarios with stepwise AI validation.", color: "purple" as const },
  { href: "/projects", icon: "📂", title: "Manage projects", desc: "Organize workspaces, environments, members, and credentials.", color: "cyan" as const },
  { href: "/dashboard", icon: "📊", title: "Track health", desc: "Monitor run quality, pass rate, and execution trends by project.", color: "pink" as const },
];

const WORKFLOW = [
  { title: "1. Project", body: "Define workspace scope, environments, and team ownership." },
  { title: "2. Sprint & Story", body: "Plan delivery and convert story intent into testable outcomes." },
  { title: "3. Test Cases", body: "Generate or author cases, review, then approve execution-ready coverage." },
  { title: "4. Run & Analyze", body: "Execute suites, inspect failures, and improve scenario quality iteratively." },
];

export default function LandingPage() {
  // Read the sidebar's persisted project slug so the onboarding
  // checklist below points at the user's active workspace. The
  // sidebar writes this on every project-picker change.
  const [activeProject, setActiveProject] = useState<string>("");
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      setActiveProject(window.localStorage.getItem("ws.project") || "");
    } catch {
      // localStorage disabled; checklist just doesn't render.
    }
  }, []);

  return (
    <div className="relative min-h-screen overflow-hidden">
      <ParticleField />
      <div className="relative z-10">
        <PageScaffold>
          {/* Adaptive first-run checklist. Renders only when the user
              has an active workspace AND that project has unfinished
              steps (sprint / story / TC / script / run). Dismisses
              forever per-project via localStorage. */}
          {activeProject && <OnboardingChecklist projectSlug={activeProject} />}

          <motion.div initial={{ opacity: 0, y: 22 }} animate={{ opacity: 1, y: 0 }}>
            <PageHeader
              eyebrow="Test Intelligence Platform"
              title="AI QA Portal"
              description="Enterprise workspace for Salesforce QA teams to plan, generate, execute, and improve automated testing."
              actions={
                <div className="flex gap-2">
                  <Link href="/generate">
                    <motion.button
                      whileHover={{ scale: 1.04 }}
                      whileTap={{ scale: 0.97 }}
                      className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold"
                    >
                      Generate test
                    </motion.button>
                  </Link>
                  <Link href="/dashboard">
                    <button className="px-4 py-2 rounded-xl glass text-sm text-slate-200 hover:text-white">
                      Open dashboard
                    </button>
                  </Link>
                </div>
              }
            />
          </motion.div>

          <PageSection title="Quick start" description="Jump to the most common workflows.">
            <div className="grid md:grid-cols-3 gap-4">
              {QUICK_START.map((item, i) => (
                <Link key={item.title} href={item.href}>
                  <AnimatedCard delay={i * 0.08} glow={item.color} className="h-full cursor-pointer">
                    <div className="text-2xl mb-2">{item.icon}</div>
                    <h3 className="text-base font-semibold text-white mb-1">{item.title}</h3>
                    <p className="text-sm text-slate-400">{item.desc}</p>
                  </AnimatedCard>
                </Link>
              ))}
            </div>
          </PageSection>

          <PageSection title="Workflow" description="Recommended flow from planning to validated execution.">
            <div className="grid md:grid-cols-2 gap-3">
              {WORKFLOW.map((step) => (
                <div key={step.title} className="rounded-xl border border-white/10 bg-white/5 p-3">
                  <p className="text-sm font-semibold text-cyan-200">{step.title}</p>
                  <p className="text-sm text-slate-400 mt-1">{step.body}</p>
                </div>
              ))}
            </div>
          </PageSection>
        </PageScaffold>
      </div>
    </div>
  );
}
