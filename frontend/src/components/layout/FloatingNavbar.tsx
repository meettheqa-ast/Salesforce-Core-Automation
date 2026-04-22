"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";

const NAV_ITEMS = [
  { href: "/", label: "Home", icon: "🏠" },
  { href: "/generate", label: "Generate", icon: "🧪" },
  { href: "/dashboard", label: "Dashboard", icon: "📊" },
  { href: "/projects", label: "Projects", icon: "📂" },
  { href: "/user-stories", label: "Stories", icon: "📖" },
  { href: "/sfdx", label: "SF DX", icon: "🔍" },
  { href: "/locators", label: "Locators", icon: "🔬" },
  { href: "/settings", label: "Settings", icon: "⚙️" },
  { href: "/about", label: "About", icon: "ℹ️" },
];

export default function FloatingNavbar() {
  const pathname = usePathname();

  return (
    <motion.nav
      initial={{ y: -100, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.6, ease: "easeOut" }}
      className="fixed top-4 left-1/2 -translate-x-1/2 z-50 glass-strong px-2 py-2 flex items-center gap-1"
    >
      <div className="flex items-center gap-2 px-3 mr-2">
        <div className="w-3 h-3 rounded-full bg-gradient-to-r from-purple-500 to-cyan-400 animate-pulse-glow" />
        <span className="text-sm font-bold text-white tracking-tight">AI QA Portal</span>
      </div>

      <div className="h-5 w-px bg-white/10" />

      {NAV_ITEMS.map((item) => {
        const isActive = pathname === item.href;
        return (
          <Link key={item.href} href={item.href}>
            <motion.div
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              className={`relative px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 flex items-center gap-2 ${
                isActive
                  ? "text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              {isActive && (
                <motion.div
                  layoutId="nav-active"
                  className="absolute inset-0 bg-gradient-to-r from-purple-600/30 to-cyan-600/30 rounded-xl border border-purple-500/30"
                  transition={{ type: "spring", stiffness: 300, damping: 30 }}
                />
              )}
              <span className="relative z-10">{item.icon}</span>
              <span className="relative z-10">{item.label}</span>
            </motion.div>
          </Link>
        );
      })}
    </motion.nav>
  );
}
