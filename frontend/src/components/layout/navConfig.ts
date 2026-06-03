export type NavItem = {
  href: string;
  label: string;
  icon: string;
  match?: "exact" | "prefix";
  requiresProject?: boolean;
  section?: "plan" | "build" | "run" | "operate";
  disabled?: boolean;
};

export type NavGroup = {
  id: string;
  label: string;
  items: NavItem[];
};

export const CORE_NAV_GROUPS: NavGroup[] = [
  {
    id: "global",
    label: "Global",
    items: [
      { href: "/", label: "Home", icon: "🏠", match: "exact" },
      { href: "/projects", label: "Projects", icon: "📂", match: "prefix" },
      { href: "/runs", label: "All Runs", icon: "🏁", match: "prefix" },
      { href: "/notifications", label: "Notifications", icon: "🔔", match: "prefix" },
    ],
  },
  {
    id: "workspace",
    label: "Workspace",
    items: [
      { href: "/p/:project", label: "Overview", icon: "🧭", match: "prefix", requiresProject: true, section: "plan" },
      { href: "/p/:project/sprints", label: "Sprints", icon: "🏃", match: "prefix", requiresProject: true, section: "plan" },
      { href: "/p/:project/stories", label: "Stories", icon: "📖", match: "prefix", requiresProject: true, section: "plan" },
      { href: "/p/:project/generate", label: "Generate", icon: "🧪", match: "prefix", requiresProject: true, section: "build" },
      { href: "/p/:project/runs", label: "Runs", icon: "🏁", match: "prefix", requiresProject: true, section: "run" },
      { href: "/p/:project/analytics", label: "Analytics", icon: "📈", match: "prefix", requiresProject: true, section: "run" },
      { href: "/p/:project/members", label: "Members", icon: "👥", match: "prefix", requiresProject: true, section: "operate" },
      { href: "/p/:project/integrations", label: "Integrations", icon: "🔌", match: "prefix", requiresProject: true, section: "operate" },
    ],
  },
  {
    id: "platform",
    label: "Platform",
    items: [
      { href: "/settings", label: "Settings", icon: "⚙️", match: "prefix" },
      { href: "/about", label: "About", icon: "ℹ️", match: "prefix" },
    ],
  },
];

export const ADMIN_NAV_GROUP: NavGroup = {
  id: "admin",
  label: "Admin",
  items: [{ href: "/admin", label: "Admin Console", icon: "🛡️", match: "prefix" }],
};

export function isNavItemActive(pathname: string, item: NavItem): boolean {
  const normalizedHref = item.href.includes(":project")
    ? item.href.replace(":project", "[^/]+")
    : item.href;
  if (item.match === "exact") {
    if (item.href.includes(":project")) return new RegExp(`^${normalizedHref}$`).test(pathname);
    return pathname === item.href;
  }
  if (item.href === "/") return pathname === "/";
  if (item.href.includes(":project")) return new RegExp(`^${normalizedHref}(?:/.*)?$`).test(pathname);
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}
