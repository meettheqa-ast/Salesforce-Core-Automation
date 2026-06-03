"use client";

/**
 * Read-focused project-scoped Personas page. Promoted from the
 * project-home Credentials card per the IA audit so personas are
 * deep-linkable + scalable (search, notifications, integrations can
 * link straight here).
 *
 * Write operations (create / edit / delete) still happen on the
 * project home cards in Phase 2; this page lists the existing
 * personas with a CTA back to the project home for management. A
 * future iteration moves the editor here too.
 */

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api, type PersonaPublic } from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";
import EmptyState from "@/components/feedback/EmptyState";
import StatusPill from "@/components/data/StatusPill";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

export default function ProjectPersonasPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);
  const [personas, setPersonas] = useState<PersonaPublic[] | null>(null);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { project_id } = await api.projects.portalProjectId(name);
        const list = await api.personas.list(project_id);
        if (!cancelled) setPersonas(list);
      } catch (e) {
        if (!cancelled) setErr(e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [name]);

  const grouped = useMemo(() => {
    const out = new Map<string, PersonaPublic[]>();
    for (const p of personas || []) {
      const key = p.org_id || "default";
      const arr = out.get(key) || [];
      arr.push(p);
      out.set(key, arr);
    }
    return out;
  }, [personas]);

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Project"
          title="Personas"
          description="Salesforce user personas registered for this project. Manage credentials from the project home."
        />
        <p className="text-xs text-slate-500 mt-2">
          <Link href={`/projects/${encodeURIComponent(name)}`} className="hover:text-slate-300">
            ← Back to project home
          </Link>
        </p>
      </motion.div>

      <ErrorBanner error={err} onDismiss={() => setErr(null)} />

      <AnimatedCard glow="purple">
        {personas === null ? (
          <LoadingState variant="block" label="Loading personas…" />
        ) : personas.length === 0 ? (
          <EmptyState
            title="No personas yet"
            description="Add a persona on the project home so test runs have credentials to log in with."
            primary={{
              label: "Open project home",
              href: `/projects/${encodeURIComponent(name)}`,
            }}
          />
        ) : (
          <div className="space-y-4">
            {Array.from(grouped.entries()).map(([orgKey, list]) => (
              <div key={orgKey}>
                <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-2">
                  Org: {orgKey}
                </h3>
                <ul className="space-y-2">
                  {list.map((p) => (
                    <li
                      key={p.id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-white/10 px-3 py-2"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-white">{p.name}</p>
                        {p.role_profile && (
                          <p className="text-[11px] text-slate-400">{p.role_profile}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        {p.is_default && <StatusPill tone="success" label="Default" />}
                        {p.default_app && (
                          <StatusPill tone="info" label={p.default_app} />
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </AnimatedCard>
    </PageScaffold>
  );
}
