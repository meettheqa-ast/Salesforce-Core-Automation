"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";

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

// IA audit: breadcrumbs used to only render on /p/* paths. Most real
// pages live on flat routes (/sprints/[id], /user-stories/[id],
// /projects/[name], etc.) where ?project= or localStorage:ws.project
// is the source of project context. This map plus the fallback below
// makes the breadcrumb work on those routes too so users don't lose
// context when navigating away from the alias tree.
const FLAT_MODULE_BY_PREFIX: Array<{
  prefix: string;
  moduleKey: string;
  detail: (segments: string[]) => string | null;
}> = [
  { prefix: "/sprints", moduleKey: "sprints", detail: (s) => s[1] || null },
  { prefix: "/user-stories", moduleKey: "stories", detail: (s) => s[1] || null },
  { prefix: "/test-cases", moduleKey: "stories", detail: (s) => s[1] || null },
  { prefix: "/runs", moduleKey: "runs", detail: (s) => s[1] || null },
  { prefix: "/generate", moduleKey: "generate", detail: (s) => s[1] || null },
];

function readPersistedProject(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem("ws.project") || "";
  } catch {
    return "";
  }
}

export default function WorkspaceSubHeader() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // We re-read the sidebar's project selection on mount because it
  // lives in localStorage; useSession hooks aren't appropriate for
  // a non-React-state value. Re-render is triggered via the
  // pathname dependency so the breadcrumb refreshes on navigation.
  const [persistedProject, setPersistedProject] = useState<string>("");
  useEffect(() => {
    setPersistedProject(readPersistedProject());
  }, [pathname]);

  const parsed = useMemo(() => {
    if (!pathname) return null;

    // Path A: existing /p/* alias tree -- behaviour unchanged.
    if (pathname.startsWith("/p/")) {
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
    }

    // Path B: flat routes. Match a known prefix, then resolve project
    // from ?project=, then from localStorage:ws.project. Without a
    // project we still render a partial breadcrumb so the user has
    // a path back to /projects.
    const flat = FLAT_MODULE_BY_PREFIX.find((m) =>
      pathname === m.prefix || pathname.startsWith(`${m.prefix}/`),
    );
    if (!flat) return null;

    const project =
      searchParams?.get("project") || persistedProject || "";
    const segments = pathname.slice(flat.prefix.length).split("/").filter(Boolean);
    const detail = flat.detail([flat.prefix, ...segments]);
    const moduleLabel = MODULE_LABELS[flat.moduleKey] || normalizeLabel(flat.moduleKey);
    const tabs = project ? TABS_BY_MODULE[flat.moduleKey] || [] : [];
    const moduleHref = project
      ? `/p/${encodeURIComponent(project)}/${flat.moduleKey}`
      : flat.prefix;
    const crumbs = [
      { label: "Projects", href: "/projects" },
      ...(project
        ? [{ label: project, href: `/p/${encodeURIComponent(project)}` }]
        : []),
      { label: moduleLabel, href: moduleHref },
      ...(detail ? [{ label: "Detail", href: pathname }] : []),
    ];
    return { project: project || "", moduleKey: flat.moduleKey, tabs, crumbs };
  }, [pathname, searchParams, persistedProject]);

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
