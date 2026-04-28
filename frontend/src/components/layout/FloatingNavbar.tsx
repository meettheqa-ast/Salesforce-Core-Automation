"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import UserMenu from "./UserMenu";
import NotificationsBell from "./NotificationsBell";
import { useMe } from "@/lib/useMe";

const NAV_ITEMS = [
  { href: "/", label: "Home", icon: "🏠" },
  { href: "/generate", label: "Generate", icon: "🧪" },
  { href: "/dashboard", label: "Dashboard", icon: "📊" },
  { href: "/projects", label: "Projects", icon: "📂" },
  { href: "/runs", label: "Runs", icon: "🏁" },
  { href: "/sprints", label: "Sprints", icon: "🏃" },
  { href: "/user-stories", label: "Stories", icon: "📖" },
  // Hidden from the demo nav -- routes still live at /sfdx and /locators if
  // accessed directly. Re-enable once the features are demo-ready.
  // { href: "/sfdx", label: "SF DX", icon: "🔍" },
  // { href: "/locators", label: "Locators", icon: "🔬" },
  { href: "/settings", label: "Settings", icon: "⚙️" },
  { href: "/about", label: "About", icon: "ℹ️" },
];

const ADMIN_ITEM = { href: "/admin", label: "Admin", icon: "🛡️" };

export default function FloatingNavbar() {
  const pathname = usePathname();
  const { me } = useMe();
  const showAdmin = !!me?.is_admin;
  const items = showAdmin ? [...NAV_ITEMS, ADMIN_ITEM] : NAV_ITEMS;

  return (
    <motion.nav
      initial={{ y: -100, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.6, ease: "easeOut" }}
      aria-label="Primary"
      className="fixed top-5 left-1/2 -translate-x-1/2 z-50 nav-floating px-4 py-2.5 flex items-center gap-1.5 max-w-[calc(100vw-1.5rem)] overflow-x-auto"
    >
      <Link href="/" className="flex items-center gap-3 px-3 py-2 mr-1 shrink-0">
        <div className="w-3.5 h-3.5 rounded-full bg-gradient-to-r from-purple-500 to-cyan-400 animate-pulse-glow" />
        <span className="text-base font-bold text-white tracking-tight whitespace-nowrap">AI QA Portal</span>
      </Link>

      <div className="h-7 w-px bg-white/10 shrink-0" />

      {items.map((item) => {
        const isActive = pathname === item.href;
        return (
          <Link key={item.href} href={item.href} className="shrink-0">
            <motion.div
              whileHover={{ scale: 1.04 }}
              whileTap={{ scale: 0.96 }}
              className={`relative px-3.5 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 flex items-center gap-2 whitespace-nowrap ${
                isActive ? "text-white" : "text-slate-400 hover:text-white"
              }`}
            >
              {isActive && (
                <motion.div
                  layoutId="nav-active"
                  className="absolute inset-0 bg-gradient-to-r from-purple-600/30 to-cyan-600/30 rounded-lg border border-purple-500/30"
                  transition={{ type: "spring", stiffness: 300, damping: 30 }}
                />
              )}
              <span className="relative z-10 text-base">{item.icon}</span>
              <span className="relative z-10">{item.label}</span>
            </motion.div>
          </Link>
        );
      })}

      <div className="h-7 w-px bg-white/10 shrink-0 ml-1" />
      <NotificationsBell />
      <UserMenu />
    </motion.nav>
  );
}
