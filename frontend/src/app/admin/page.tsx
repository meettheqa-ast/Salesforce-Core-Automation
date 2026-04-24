"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { useMe } from "@/lib/useMe";

const TILES = [
  { href: "/admin/users", title: "Users", desc: "Manage all users in the org. Promote, demote, deactivate.", icon: "👥", color: "purple" as const },
  { href: "/admin/projects", title: "Projects", desc: "All projects in the org with member counts and PMs.", icon: "📂", color: "cyan" as const },
  { href: "/admin/audit", title: "Audit log", desc: "Append-only trail of who did what, when.", icon: "📜", color: "pink" as const },
];

export default function AdminLandingPage() {
  const { me, isLoading } = useMe();

  if (isLoading) {
    return <div className="max-w-6xl mx-auto px-6 py-8"><p className="text-slate-500">Loading...</p></div>;
  }
  if (!me?.is_admin) {
    return (
      <div className="max-w-2xl mx-auto px-6 py-16 text-center">
        <h1 className="text-3xl font-bold mb-3">
          <span className="bg-gradient-to-r from-pink-400 to-red-400 bg-clip-text text-transparent">
            Admin only
          </span>
        </h1>
        <p className="text-slate-400">
          You don't have admin privileges on this portal. Contact an admin if you believe this is wrong.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="mb-8">
        <h1 className="text-4xl font-bold">
          <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
            Admin Console
          </span>
        </h1>
        <p className="text-slate-400 mt-1">System-wide oversight: users, projects, audit log.</p>
      </motion.div>

      <div className="grid md:grid-cols-3 gap-5">
        {TILES.map((t, i) => (
          <Link key={t.href} href={t.href}>
            <AnimatedCard glow={t.color} delay={i * 0.08} className="h-full cursor-pointer">
              <div className="text-3xl mb-3">{t.icon}</div>
              <h3 className="text-lg font-bold text-white mb-2">{t.title}</h3>
              <p className="text-sm text-slate-400">{t.desc}</p>
            </AnimatedCard>
          </Link>
        ))}
      </div>
    </div>
  );
}
