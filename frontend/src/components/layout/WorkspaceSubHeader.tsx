"use client";

import Link from "next/link";
import { useMemo } from "react";
import { usePathname } from "next/navigation";

type WorkspaceTab = {
  key: string;
  label: string;
  href: (project: string) => string;
};

const MODULE_LABELS: Record<string, string> = {
  generate: "Generate",
  sprints: "Sprints",
  stories: "Stories",
  runs: "Runs",
  analytics: "Analytics",
  members: "Members",
  integrations: "Integrations",
};

const TABS_BY_MODULE: Record<string, WorkspaceTab[]> = {
  generate: [{ key: "generate", label: "Generate", href: (project) => `/p/${encodeURIComponent(project)}/generate` }],
  sprints: [{ key: "sprints", label: "Sprints", href: (project) => `/p/${encodeURIComponent(project)}/sprints` }],
  stories: [{ key: "stories", label: "Stories", href: (project) => `/p/${encodeURIComponent(project)}/stories` }],
  runs: [{ key: "runs", label: "Runs", href: (project) => `/p/${encodeURIComponent(project)}/runs` }],
  analytics: [{ key: "analytics", label: "Analytics", href: (project) => `/p/${encodeURIComponent(project)}/analytics` }],
  members: [{ key: "members", label: "Members", href: (project) => `/p/${encodeURIComponent(project)}/members` }],
  integrations: [{ key: "integrations", label: "Integrations", href: (project) => `/p/${encodeURIComponent(project)}/integrations` }],
};

function normalizeLabel(raw: string): string {
  return raw.replace(/[-_]/g, " ").replace(/\b\w/g, (match) => match.toUpperCase());
}

export default function WorkspaceSubHeader() {
  const pathname = usePathname();

  const parsed = useMemo(() => {
    if (!pathname?.startsWith("/p/")) return null;
    const parts = pathname.split("/").filter(Boolean);
    if (parts.length < 2) return null;
    const project = decodeURIComponent(parts[1]);
    const moduleKey = parts[2] || "overview";
    const detail = parts[3] || null;
    const moduleLabel = moduleKey === "overview" ? "Overview" : (MODULE_LABELS[moduleKey] || normalizeLabel(moduleKey));
    const tabs = TABS_BY_MODULE[moduleKey] || [];
    const moduleHref = moduleKey === "overview"
      ? `/p/${encodeURIComponent(project)}`
      : `/p/${encodeURIComponent(project)}/${moduleKey}`;
    const crumbs = [
      { label: "Projects", href: "/projects" },
      { label: project, href: `/p/${encodeURIComponent(project)}` },
      ...(moduleKey === "overview" ? [] : [{ label: moduleLabel, href: moduleHref }]),
      ...(detail ? [{ label: "Detail", href: pathname }] : []),
    ];
    return { project, moduleKey, tabs, crumbs };
  }, [pathname]);

  if (!parsed) return null;

  return (
    <div className="border-b border-white/10 bg-slate-950/70 backdrop-blur px-4 sm:px-6">
      <div className="max-w-[1240px] mx-auto min-h-10 py-1 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-xs min-w-0">
          {parsed.crumbs.map((crumb, idx) => (
            <div key={`${crumb.href}-${idx}`} className="inline-flex items-center gap-2 min-w-0">
              {idx > 0 && <span className="text-slate-600">/</span>}
              <Link
                href={crumb.href}
                className={idx === parsed.crumbs.length - 1 ? "text-slate-200 truncate" : "text-slate-500 hover:text-slate-300 truncate"}
              >
                {crumb.label}
              </Link>
            </div>
          ))}
        </div>
        {parsed.tabs.length > 0 && (
          <div className="hidden md:flex items-center gap-1 rounded-lg border border-white/10 bg-white/[0.03] p-1">
            {parsed.tabs.map((tab) => {
              const href = tab.href(parsed.project);
              const active = pathname === href || pathname.startsWith(`${href}/`);
              return (
                <Link
                  key={tab.key}
                  href={href}
                  className={`px-2.5 py-1 rounded-md text-xs ${
                    active ? "bg-purple-500/20 text-purple-100 border border-purple-500/30" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {tab.label}
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
