"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import type { MeResponse, MembershipRow } from "@/lib/api";

const STORAGE_KEY = "ws.project";

function parseProjectFromPath(pathname: string | null): string | null {
  if (!pathname) return null;
  const projectRoute = pathname.match(/^\/projects\/([^/]+)/);
  if (projectRoute) return decodeURIComponent(projectRoute[1]);
  const scopedRoute = pathname.match(/^\/p\/([^/]+)/);
  if (scopedRoute) return decodeURIComponent(scopedRoute[1]);
  return null;
}

export function useWorkspaceProject(me?: MeResponse | null) {
  const pathname = usePathname();
  const projectFromPath = useMemo(() => parseProjectFromPath(pathname), [pathname]);
  const [selectedProject, setSelectedProject] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  });

  const currentProject = projectFromPath ?? selectedProject;
  const currentRole = useMemo<MembershipRow["role"] | null>(() => {
    if (!currentProject || !me) return null;
    if (me.is_admin || me.global_role === "admin") return "pm";
    const membership = (me.memberships || []).find((row) => row.project_slug === currentProject);
    return membership?.role ?? null;
  }, [currentProject, me]);

  useEffect(() => {
    if (!projectFromPath) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, projectFromPath);
    } catch {
      // Ignore localStorage failures.
    }
  }, [projectFromPath]);

  const setCurrentProject = useCallback((project: string | null) => {
    setSelectedProject(project);
    try {
      if (!project) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, project);
    } catch {
      // Ignore localStorage failures.
    }
  }, []);

  return { currentProject, projectFromPath, setCurrentProject, currentRole };
}
