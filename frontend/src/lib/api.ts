const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// --- Auth token plumbing -------------------------------------------------
// Fetched lazily from /api/auth/jwt the first time `apiFetch` runs in the
// browser, then cached. On 401 from the backend we drop the cache so the next
// call refetches a fresh token (covers the 1h JWT lifetime + login flips).

let _cachedToken: string | null = null;
let _inFlight: Promise<string | null> | null = null;

async function _fetchJwt(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  if (_cachedToken) return _cachedToken;
  if (_inFlight) return _inFlight;
  _inFlight = (async () => {
    try {
      const r = await fetch("/api/auth/jwt", { credentials: "include", cache: "no-store" });
      if (!r.ok) {
        // Do NOT cache null -- a 401 here usually means "not signed in yet" or
        // "session refresh pending". Caching null would deny every subsequent
        // call until the page reloads. Let the next caller retry.
        return null;
      }
      const text = (await r.text()).trim();
      if (!text) return null;
      _cachedToken = text;
      return _cachedToken;
    } catch {
      return null;
    } finally {
      _inFlight = null;
    }
  })();
  return _inFlight;
}

/** Build the canonical Authorization header value, or null if unauthenticated. */
async function authHeader(): Promise<Record<string, string>> {
  const tok = await _fetchJwt();
  return tok ? { Authorization: `Bearer ${tok}` } : {};
}

/** Append `?token=<jwt>` to a URL that will be opened by EventSource / <img>
 *  / direct download (anywhere we can't set a header). Synchronous: reads the
 *  cached token only. Returns the URL unchanged if the cache is cold -- pages
 *  that render such URLs at first paint should call `prepareAuth()` in a
 *  top-level useEffect to warm the cache before mount.
 *
 *  An async retry happens via `apiFetch` whenever a JSON call gets 401, so
 *  cold-cache renders self-heal on the next interaction.
 */
export function withAuthQuery(url: string): string {
  if (!_cachedToken) {
    // Fire-and-forget refill so the next render gets it.
    if (typeof window !== "undefined") void _fetchJwt();
    return url;
  }
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(_cachedToken)}`;
}

/** Force a token refresh on next call (e.g. after sign-in / sign-out). */
export function clearAuthCache(): void {
  _cachedToken = null;
}

/** Pre-warm the token cache. Useful before rendering URLs that embed the
 *  token in the query string (downloads, EventSource, <img>). Safe to call
 *  multiple times -- in-flight requests are deduplicated. */
export async function prepareAuth(): Promise<void> {
  await _fetchJwt();
}

// Eagerly warm the cache on module load so the first React paint usually has
// a token available for sync URL builders below.
if (typeof window !== "undefined") {
  void _fetchJwt();
}

export type RunStatus = "PASS" | "FAIL" | "EMPTY";

export interface RunHistoryRow {
  run_name: string;
  timestamp: string;
  passed: number;
  failed: number;
  skipped: number;
  total: number;
  status: RunStatus;
  log_html: string | null;
  report_html: string | null;
}

export interface RunStat {
  name: string;
  passed: number;
  failed: number;
  skipped: number;
  total: number;
}

export interface RunTestRow {
  name: string;
  suite: string;
  status: "PASS" | "FAIL" | "SKIP";
  duration_s: number;
  message: string | null;
  tags: string[];
}

export interface RunArtefacts {
  log_html: string | null;
  report_html: string | null;
  output_xml: string | null;
  screenshots: string[];
}

export interface RunSummary {
  run_folder: string;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number;
  status: RunStatus;
  passed: number;
  failed: number;
  skipped: number;
  total: number;
  pass_rate: number;
  by_tag: RunStat[];
  by_suite: RunStat[];
  tests: RunTestRow[];
  artefacts: RunArtefacts;
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const auth = await authHeader();
    return fetch(`${API_BASE}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...auth,
        ...options?.headers,
      },
      ...options,
    });
  };

  let res: Response;
  try {
    res = await doFetch();
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "Network error";
    throw new Error(
      `${msg}. Is the API running at ${API_BASE}? (Set NEXT_PUBLIC_API_URL if needed.)`
    );
  }

  // Token may have expired; refresh once and retry.
  if (res.status === 401 && typeof window !== "undefined") {
    clearAuthCache();
    try {
      res = await doFetch();
    } catch {
      // fall through to error path below
    }
  }

  if (res.status === 401 && typeof window !== "undefined") {
    // Still 401 after refresh -- bounce to the login page.
    window.location.href = `/login?from=${encodeURIComponent(window.location.pathname)}`;
    throw new Error("Not signed in");
  }

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`API ${res.status}: ${detail}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  const text = await res.text();
  if (!text.trim()) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

export interface MembershipRow {
  id: string;
  project_slug: string;
  user_id: string;
  role: "pm" | "lead" | "member";
  created_at: string | null;
}

export interface MeResponse {
  id: string;
  email: string;
  name: string;
  picture: string;
  is_admin: boolean;
  global_role: "admin" | "pm" | "tl" | "user";
  is_active: boolean;
  created_at: string | null;
  last_login_at: string | null;
  memberships: MembershipRow[];
}

export interface MemberOut {
  user_id: string;
  email: string;
  name: string;
  picture: string;
  role: "pm" | "lead" | "member";
  joined_at: string | null;
}

export const api = {
  me: () => apiFetch<MeResponse>("/api/me"),
  projects: {
    list: () => apiFetch<string[]>("/api/projects"),
    get: (name: string) => apiFetch<any>(`/api/projects/${name}`),
    create: (data: any) => apiFetch<any>("/api/projects", { method: "POST", body: JSON.stringify(data) }),
    delete: (name: string) => apiFetch<any>(`/api/projects/${name}`, { method: "DELETE" }),
    tests: (name: string) => apiFetch<any[]>(`/api/projects/${name}/tests`),
    testSource: (name: string, test: string) => apiFetch<any>(`/api/projects/${name}/tests/${test}/source`),
    environments: (name: string) => apiFetch<string[]>(`/api/projects/${name}/environments`),
    config: (name: string, env: string, persona: string) =>
      apiFetch<Record<string, string>>(
        `/api/projects/${encodeURIComponent(name)}/config?environment=${encodeURIComponent(env)}&persona=${encodeURIComponent(persona)}`
      ),
    personas: (name: string, env: string) =>
      apiFetch<string[]>(
        `/api/projects/${encodeURIComponent(name)}/environments/${encodeURIComponent(env)}/personas`
      ),
    saveCredentials: (
      name: string,
      payload: {
        environment: string;
        persona: string;
        sandbox_url?: string;
        username?: string;
        password?: string;
        security_token?: string;
        slack_webhook_url?: string;
      }
    ) =>
      apiFetch<{ environment: string; persona: string; status: string }>(
        `/api/projects/${encodeURIComponent(name)}/credentials`,
        { method: "PUT", body: JSON.stringify(payload) }
      ),
    deleteEnvironment: (name: string, env: string) =>
      apiFetch<void>(
        `/api/projects/${encodeURIComponent(name)}/environments/${encodeURIComponent(env)}`,
        { method: "DELETE" }
      ),
    deletePersona: (name: string, env: string, persona: string) =>
      apiFetch<void>(
        `/api/projects/${encodeURIComponent(name)}/environments/${encodeURIComponent(env)}/personas/${encodeURIComponent(persona)}`,
        { method: "DELETE" }
      ),
    portalProjectId: (projectName: string) =>
      apiFetch<{ project_id: string; slug: string }>(`/api/projects/registry/${encodeURIComponent(projectName)}`),
    members: (name: string) =>
      apiFetch<MemberOut[]>(`/api/projects/${encodeURIComponent(name)}/members`),
    addMember: (name: string, body: { email: string; role: "pm" | "lead" | "member" }) =>
      apiFetch<MemberOut>(
        `/api/projects/${encodeURIComponent(name)}/members`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    updateMemberRole: (name: string, userId: string, role: "pm" | "lead" | "member") =>
      apiFetch<MemberOut>(
        `/api/projects/${encodeURIComponent(name)}/members/${encodeURIComponent(userId)}`,
        { method: "PATCH", body: JSON.stringify({ role }) },
      ),
    removeMember: (name: string, userId: string) =>
      apiFetch<void>(
        `/api/projects/${encodeURIComponent(name)}/members/${encodeURIComponent(userId)}`,
        { method: "DELETE" },
      ),
  },
  orgs: {
    list: (projectId?: string) =>
      apiFetch<any[]>(`/orgs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`),
  },
  personas: {
    list: (projectId?: string, orgId?: string) => {
      const q = new URLSearchParams();
      if (projectId) q.set("project_id", projectId);
      if (orgId) q.set("org_id", orgId);
      const s = q.toString();
      return apiFetch<any[]>(`/personas${s ? `?${s}` : ""}`);
    },
  },
  userStories: {
    create: (data: { project_id: string; title: string; description: string }) =>
      apiFetch<any>("/user-stories", { method: "POST", body: JSON.stringify(data) }),
    get: (id: string) => apiFetch<any>(`/user-stories/${encodeURIComponent(id)}`),
    list: (projectId: string) => apiFetch<any[]>(`/user-stories?project_id=${encodeURIComponent(projectId)}`),
    update: (id: string, data: { title?: string; description?: string }) =>
      apiFetch<any>(`/user-stories/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    generate: (id: string) =>
      apiFetch<any>(`/user-stories/${encodeURIComponent(id)}/generate`, {
        method: "POST",
        body: "{}",
      }),
  },
  testCases: {
    list: (userStoryId: string) =>
      apiFetch<any[]>(`/test-cases?user_story_id=${encodeURIComponent(userStoryId)}`),
    batchApprove: (data: {
      user_story_id: string;
      approved: Array<{
        test_case_id: string;
        title: string;
        steps: string[];
        expected_result: string;
        preconditions?: string | null;
        tags: string[];
      }>;
    }) => apiFetch<any>("/test-cases/batch-approve", { method: "POST", body: JSON.stringify(data) }),
  },
  tags: {
    list: (projectId: string) => apiFetch<any[]>(`/tags?project_id=${encodeURIComponent(projectId)}`),
  },
  generate: {
    quick: (data: any) => apiFetch<any>("/api/generate/robot-suite", { method: "POST", body: JSON.stringify(data) }),
    stepwise: (data: any) => apiFetch<any>("/api/generate/mcp-stepwise", { method: "POST", body: JSON.stringify(data) }),
    /** SSE -- the JWT is embedded in the query string because EventSource cannot set headers. */
    stepwiseStreamUrl: () => withAuthQuery(`${API_BASE}/api/generate/mcp-stepwise/stream`),
  },
  runs: {
    execute: (data: any) => apiFetch<any>("/api/runs/execute", { method: "POST", body: JSON.stringify(data) }),
    /** SSE -- the JWT is embedded in the query string because EventSource cannot set headers. */
    executeStreamUrl: (data: {
      test_path: string;
      sandbox_url: string;
      username: string;
      password: string;
      headless?: boolean;
    }) => {
      const q = new URLSearchParams({
        test_path: data.test_path,
        sandbox_url: data.sandbox_url,
        username: data.username,
        password: data.password,
        headless: String(data.headless ?? true),
      });
      return withAuthQuery(`${API_BASE}/api/runs/execute/stream?${q.toString()}`);
    },
    latest: (limit = 50) =>
      apiFetch<{ runs: Array<RunHistoryRow> }>(`/api/runs/latest?limit=${limit}`),
    summary: (runFolder: string) =>
      apiFetch<RunSummary>(`/api/runs/${encodeURIComponent(runFolder)}/summary`),
    /** Used by <a href> / <img src>; needs ?token= since browsers cannot
     *  attach Authorization on those tags. Sync; reads cached token. */
    fileUrl: (runFolder: string, filename: string, opts?: { download?: boolean }) =>
      withAuthQuery(
        `${API_BASE}/api/runs/${encodeURIComponent(runFolder)}/file/${encodeURIComponent(filename)}${
          opts?.download ? "?download=1" : ""
        }`,
      ),
    bundleUrl: (runFolder: string) =>
      withAuthQuery(`${API_BASE}/api/runs/${encodeURIComponent(runFolder)}/bundle.zip`),
    userStory: (storyId: string, body: { org_id: string; persona_id?: string | null }) =>
      apiFetch<any[]>(`/run/user-story/${encodeURIComponent(storyId)}`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    byTag: (tagName: string, body: { project_id: string; org_id: string; persona_id?: string | null }) =>
      apiFetch<any[]>(`/run/tag/${encodeURIComponent(tagName)}`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  llm: {
    chat: (data: any) => apiFetch<any>("/api/llm/chat", { method: "POST", body: JSON.stringify(data) }),
    providers: () => apiFetch<any>("/api/llm/providers"),
    analyzeFailure: (data: any) => apiFetch<any>("/api/llm/analyze-failure", { method: "POST", body: JSON.stringify(data) }),
  },
  mcp: {
    health: () => apiFetch<any>("/api/mcp/health"),
    start: () => apiFetch<any>("/api/mcp/server/start", { method: "POST" }),
    stop: () => apiFetch<any>("/api/mcp/server/stop", { method: "POST" }),
  },
  catalog: {
    keywords: () => apiFetch<any>("/api/catalog/keywords"),
    rebuild: () => apiFetch<any>("/api/catalog/rebuild", { method: "POST" }),
  },
  analytics: {
    summary: (project: string) => apiFetch<any>(`/api/analytics/projects/${project}/summary`),
  },
  locators: {
    scan: (data: any) => apiFetch<any>("/api/locators/scan", { method: "POST", body: JSON.stringify(data) }),
    list: () => apiFetch<any>("/api/locators/list"),
  },
  salesforce: {
    soql: (query: string) => apiFetch<any>("/api/salesforce/soql", { method: "POST", body: JSON.stringify({ query }) }),
    dxStatus: () => apiFetch<any>("/api/salesforce/dx/status"),
    describeFields: (obj: string) => apiFetch<any>(`/api/salesforce/dx/objects/${obj}/fields`),
  },
};
