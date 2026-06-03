"use client";

/**
 * Personal-settings page. Lives at /settings/profile so the user menu
 * has a real Profile link (closing the IA gap flagged in the audit).
 *
 * Phase 1 surface is intentionally minimal -- display name + email +
 * admin flag (read-only; managed in Google) + a theme-toggle stub
 * for Phase 3 + sign-out. Editable user fields land in Phase 2 once
 * the backend grows a PATCH /api/me endpoint.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { signOut } from "next-auth/react";
import { motion } from "framer-motion";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";
import { api, type MeResponse } from "@/lib/api";
import { usePersistedState } from "@/hooks/usePersistedState";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";
import StatusPill from "@/components/data/StatusPill";

export default function ProfilePage() {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  // Theme is dark-only today (see plan.md Phase 3); the toggle here
  // persists the preference so Phase 3 can pick it up without a
  // schema migration. The :root color-scheme stays "dark" until then.
  const [themePref, setThemePref] = usePersistedState<"dark" | "light" | "system">(
    "ui.themePref",
    "dark",
  );

  useEffect(() => {
    api
      .me()
      .then((m) => setMe(m))
      .catch((e) =>
        setErr(e instanceof Error ? e.message : "Could not load profile"),
      )
      .finally(() => setLoading(false));
  }, []);

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Settings"
          title="Profile"
          description="Your personal account + display preferences."
        />
        <p className="text-xs text-slate-500 mt-2">
          <Link href="/settings" className="hover:text-slate-300">
            ← Back to Settings
          </Link>
        </p>
      </motion.div>

      <ErrorBanner error={err} onDismiss={() => setErr(null)} />

      <div className="grid md:grid-cols-2 gap-6">
        {/* Identity */}
        <AnimatedCard glow="purple" delay={0}>
          <h3 className="text-sm font-bold text-white mb-4">Account</h3>
          {loading ? (
            <LoadingState variant="block" label="Loading account…" />
          ) : me ? (
            <div className="space-y-3">
              <Row label="Name" value={me.name || "—"} />
              <Row label="Email" value={me.email || "—"} />
              <Row
                label="Role"
                value={
                  <span className="flex items-center gap-2">
                    {me.is_admin ? (
                      <StatusPill tone="info" label="Admin" />
                    ) : (
                      <StatusPill tone="muted" label="Member" />
                    )}
                    <span className="text-[11px] text-slate-500">
                      (managed by your admin)
                    </span>
                  </span>
                }
              />
              <Row
                label="User ID"
                value={
                  <code className="text-[11px] text-slate-300">{me.id}</code>
                }
              />
            </div>
          ) : (
            <p className="text-xs text-slate-500">Profile unavailable.</p>
          )}
          <div className="mt-5 flex justify-end">
            <button
              type="button"
              onClick={() => void signOut({ redirectTo: "/login" })}
              className="px-3 py-1.5 rounded-lg border border-red-400/40 text-red-200 text-xs hover:bg-red-500/10"
            >
              Sign out
            </button>
          </div>
        </AnimatedCard>

        {/* Appearance (stub for Phase 3 theming) */}
        <AnimatedCard glow="cyan" delay={0.1}>
          <h3 className="text-sm font-bold text-white mb-4">Appearance</h3>
          <p className="text-xs text-slate-400 mb-3">
            Your choice is applied immediately via the data-theme
            attribute on the root element. The dedicated light-theme
            stylesheet is part of an upcoming design pass; today the
            UI still renders dark in all modes, but your preference is
            stored so it activates seamlessly when light styles ship.
          </p>
          <div className="flex gap-2">
            {(["dark", "light", "system"] as const).map((opt) => (
              <button
                key={opt}
                type="button"
                onClick={() => setThemePref(opt)}
                className={`px-3 py-1.5 rounded-lg text-xs ${
                  themePref === opt
                    ? "bg-cyan-500/20 text-cyan-100 border border-cyan-500/40"
                    : "glass text-slate-300 hover:text-white"
                }`}
              >
                {opt[0].toUpperCase() + opt.slice(1)}
              </button>
            ))}
          </div>
          <p className="mt-3 text-[11px] text-slate-600">
            Selected: {themePref} (saved · applies via data-theme attribute)
          </p>
        </AnimatedCard>
      </div>
    </PageScaffold>
  );
}

function Row({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[11px] uppercase tracking-wider text-slate-500">
        {label}
      </span>
      <span className="text-sm text-slate-200 text-right">{value}</span>
    </div>
  );
}
