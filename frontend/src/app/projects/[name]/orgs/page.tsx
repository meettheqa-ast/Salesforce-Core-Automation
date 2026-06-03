"use client";

/**
 * Read-focused project-scoped Salesforce orgs page. Promoted from the
 * project-home Environments card per the IA audit -- deep-linkable
 * from notifications + integrations + future scheduled-run errors.
 *
 * Write operations stay on the project home in Phase 2; this page
 * provides a stable URL + readable list.
 */

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import LoadingState from "@/components/feedback/LoadingState";
import ErrorBanner from "@/components/feedback/ErrorBanner";
import EmptyState from "@/components/feedback/EmptyState";
import StatusPill from "@/components/data/StatusPill";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

interface OrgRow {
  id: string;
  name: string;
  login_url?: string;
  org_type?: string;
  created_at?: string | null;
}

export default function ProjectOrgsPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);
  const [orgs, setOrgs] = useState<OrgRow[] | null>(null);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { project_id } = await api.projects.portalProjectId(name);
        const list = (await api.orgs.list(project_id)) as OrgRow[];
        if (!cancelled) setOrgs(list);
      } catch (e) {
        if (!cancelled) setErr(e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [name]);

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Project"
          title="Salesforce Orgs"
          description="Connected Salesforce environments used by this project's runs and personas."
        />
        <p className="text-xs text-slate-500 mt-2">
          <Link href={`/projects/${encodeURIComponent(name)}`} className="hover:text-slate-300">
            ← Back to project home
          </Link>
        </p>
      </motion.div>

      <ErrorBanner error={err} onDismiss={() => setErr(null)} />

      <AnimatedCard glow="purple">
        {orgs === null ? (
          <LoadingState variant="block" label="Loading orgs…" />
        ) : orgs.length === 0 ? (
          <EmptyState
            title="No orgs yet"
            description="Add a Salesforce org on the project home so runs have somewhere to log into."
            primary={{
              label: "Open project home",
              href: `/projects/${encodeURIComponent(name)}`,
            }}
          />
        ) : (
          <ul className="space-y-2">
            {orgs.map((o) => (
              <li
                key={o.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-white/10 px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-white">{o.name || o.id}</p>
                  {o.login_url && (
                    <p className="text-[11px] text-slate-500 font-mono truncate">{o.login_url}</p>
                  )}
                </div>
                {o.org_type && <StatusPill tone="info" label={o.org_type} />}
              </li>
            ))}
          </ul>
        )}
      </AnimatedCard>
    </PageScaffold>
  );
}
