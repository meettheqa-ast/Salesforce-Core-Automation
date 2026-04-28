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
      if (!r.ok) return null;
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

/** Recover from a stuck 401 loop: clear NextAuth's session cookie so /login
 *  shows the sign-in button again instead of bouncing the user straight back
 *  to a protected page. Then hard-navigate to /login so the new request is
 *  evaluated by the (now-unauthenticated) middleware.
 *
 *  Guarded so we don't run it more than once per page load -- if multiple
 *  in-flight requests all 401, we only initiate sign-out once. */
let _signoutInFlight = false;
async function _recoverFromStuckAuth(fromPath: string): Promise<void> {
  if (typeof window === "undefined") return;
  if (_signoutInFlight) return;

  // CRITICAL: never trigger the recovery flow when we're already on /login.
  // Without this guard, the navbar's useMe() fires api.me() on every page
  // (including /login itself) -> gets 401 -> we'd redirect to /login ->
  // navbar mounts again -> useMe() fires -> infinite loop.
  // Anything that 401s on /login should just fail silently; the user is
  // already in the right place to recover by clicking Sign in.
  if (fromPath.startsWith("/login")) {
    clearAuthCache();
    return;
  }

  _signoutInFlight = true;
  try {
    const { signOut } = await import("next-auth/react");
    await signOut({ redirect: false });
  } catch {
    // If signOut blew up, fall through and bounce anyway -- worst case
    // /login renders the bouncer once, which is no worse than current state.
  }
  clearAuthCache();
  window.location.href = `/login?from=${encodeURIComponent(fromPath)}&reason=expired`;
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
    // Still 401 after a fresh-token retry. The NextAuth session cookie may
    // still be present (so /login would auto-redirect us back here, causing
    // a refresh loop), so clear it via signOut() before bouncing to /login.
    void _recoverFromStuckAuth(window.location.pathname);
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

export interface InvitationRow {
  id: string;
  project_slug: string;
  email: string;
  role: "pm" | "lead" | "member";
  direction: "invite" | "request";
  status: "pending" | "requested" | "accepted" | "approved" | "rejected" | "revoked" | "expired";
  invited_by_user_id: string | null;
  expires_at: string | null;
  created_at: string | null;
  resolved_at: string | null;
}

export interface NotificationRow {
  id: string;
  user_id: string;
  type: string;
  title: string;
  body: string;
  action_url: string;
  read_at: string | null;
  created_at: string | null;
}

export interface PersonaPublic {
  id: string;
  project_id: string;
  org_id: string;
  name: string;
  username: string;            // empty string if you don't have view rights
  role_profile: string | null;
  is_default: boolean;
  creator_user_id: string;
  visibility: "private" | "public";
  credential_version: number;
  credentials_updated_at: string | null;
  is_mine: boolean;
  can_edit_credentials: boolean;
  can_view_username: boolean;
}

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  picture: string;
  is_admin: boolean;
  global_role: "admin" | "pm" | "tl" | "user";
  is_active: boolean;
  created_at: string | null;
  last_login_at: string | null;
  membership_count: number;
}

export interface AdminUserDetail extends AdminUser {
  memberships: Array<{
    id: string;
    project_slug: string;
    user_id: string;
    role: "pm" | "lead" | "member";
    created_at: string | null;
  }>;
}

export interface AdminProject {
  name: string;
  display_name: string;
  description: string;
  member_count: number;
  pm_count: number;
  pms: Array<{ user_id: string; email: string; name: string }>;
}

export interface AuditLogRow {
  id: string;
  user_id: string | null;
  user_email?: string;
  user_name?: string;
  action: string;
  target_type: string;
  target_id: string;
  metadata_json: string;
  timestamp: string | null;
}

export const api = {
  me: () => apiFetch<MeResponse>("/api/me"),
  projects: {
    list: () => apiFetch<string[]>("/api/projects"),
    discoverable: () =>
      apiFetch<Array<{ name: string; display_name: string; description: string; member_count: number }>>(
        "/api/projects/discoverable",
      ),
    get: (name: string) => apiFetch<any>(`/api/projects/${name}`),
    create: (data: any) => apiFetch<any>("/api/projects", { method: "POST", body: JSON.stringify(data) }),
    delete: (name: string) => apiFetch<any>(`/api/projects/${name}`, { method: "DELETE" }),
    tests: (name: string) => apiFetch<any[]>(`/api/projects/${name}/tests`),
    testSource: (name: string, test: string) => apiFetch<any>(`/api/projects/${name}/tests/${test}/source`),
    /** All test cases for this project, grouped by user story.
     *  Uses the JSON store (test_case_ids_project:<UUID> index), not the
     *  Robot files on disk -- that's the `tests` endpoint. */
    testCases: (name: string) =>
      apiFetch<{
        project_id: string;
        total: number;
        by_status: { draft: number; approved: number; rejected: number; stale: number };
        scripts_built: number;
        stories: Array<{
          id: string;
          title: string;
          version: number;
          test_cases: Array<{
            id: string;
            title: string;
            status: "draft" | "approved" | "rejected";
            stale: boolean;
            tags: string[];
            script_path: string | null;
            script_built_at: string | null;
            heal_attempts: number;
          }>;
        }>;
      }>(`/api/projects/${encodeURIComponent(name)}/test-cases`),
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
    listInvitations: (name: string) =>
      apiFetch<InvitationRow[]>(`/api/projects/${encodeURIComponent(name)}/invitations`),
    invite: (name: string, body: { email: string; role: "pm" | "lead" | "member" }) =>
      apiFetch<InvitationRow>(
        `/api/projects/${encodeURIComponent(name)}/invitations`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    requestAccess: (name: string) =>
      apiFetch<InvitationRow>(
        `/api/projects/${encodeURIComponent(name)}/invitations/request-access`,
        { method: "POST" },
      ),
  },
  invitations: {
    accept: (id: string) => apiFetch<InvitationRow>(`/api/invitations/${id}/accept`, { method: "POST" }),
    reject: (id: string) => apiFetch<InvitationRow>(`/api/invitations/${id}/reject`, { method: "POST" }),
    approve: (id: string) => apiFetch<InvitationRow>(`/api/invitations/${id}/approve`, { method: "POST" }),
    revoke: (id: string) => apiFetch<InvitationRow>(`/api/invitations/${id}/revoke`, { method: "POST" }),
    mine: () => apiFetch<InvitationRow[]>("/api/me/invitations"),
  },
  notifications: {
    list: (unread = false) =>
      apiFetch<NotificationRow[]>(`/api/me/notifications${unread ? "?unread=true" : ""}`),
    unreadCount: () => apiFetch<{ count: number }>("/api/me/notifications/unread-count"),
    markRead: (id: string) =>
      apiFetch<NotificationRow>(`/api/me/notifications/${id}/read`, { method: "POST" }),
    markAllRead: () =>
      apiFetch<{ marked_read: number }>("/api/me/notifications/read-all", { method: "POST" }),
  },
  admin: {
    listUsers: (q?: string) =>
      apiFetch<AdminUser[]>(`/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    getUser: (id: string) =>
      apiFetch<AdminUserDetail>(`/api/admin/users/${encodeURIComponent(id)}`),
    patchUser: (id: string, body: { global_role?: string; is_active?: boolean }) =>
      apiFetch<AdminUser>(`/api/admin/users/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    revokeSession: (id: string) =>
      apiFetch<{ user_id: string; session_revoked_at: string }>(
        `/api/admin/users/${encodeURIComponent(id)}/revoke-session`,
        { method: "POST" },
      ),
    transferProjects: (id: string, toUserId: string) =>
      apiFetch<{ transferred: string[]; count: number }>(
        `/api/admin/users/${encodeURIComponent(id)}/transfer-projects`,
        { method: "POST", body: JSON.stringify({ to_user_id: toUserId }) },
      ),
    listProjects: () => apiFetch<AdminProject[]>("/api/admin/projects"),
    membershipMatrix: () =>
      apiFetch<Array<{ user_id: string; email: string; name: string; project_slug: string; role: string }>>(
        "/api/admin/membership-matrix",
      ),
    audit: (params?: { user_id?: string; action?: string; target_type?: string; limit?: number }) => {
      const q = new URLSearchParams();
      if (params?.user_id) q.set("user_id", params.user_id);
      if (params?.action) q.set("action", params.action);
      if (params?.target_type) q.set("target_type", params.target_type);
      if (params?.limit) q.set("limit", String(params.limit));
      const s = q.toString();
      return apiFetch<AuditLogRow[]>(`/api/admin/audit${s ? `?${s}` : ""}`);
    },
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
      return apiFetch<PersonaPublic[]>(`/personas${s ? `?${s}` : ""}`);
    },
    get: (id: string) => apiFetch<PersonaPublic>(`/personas/${encodeURIComponent(id)}`),
    create: (body: {
      project_id: string;
      org_id: string;
      name: string;
      username: string;
      password: string;
      role_profile?: string;
      is_default?: boolean;
      visibility?: "private" | "public";
    }) =>
      apiFetch<PersonaPublic>("/personas", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: { name?: string; role_profile?: string; is_default?: boolean; visibility?: "private" | "public" }) =>
      apiFetch<PersonaPublic>(`/personas/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    rotate: (id: string, body: { username: string; password: string }) =>
      apiFetch<PersonaPublic>(`/personas/${encodeURIComponent(id)}/rotate`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    /** Reveal plaintext credentials. Caller must obtain a fresh Google ID token
     *  via NextAuth.signIn("google", { prompt: "login" }) and pass it here. */
    reveal: (id: string, reAuthToken: string) =>
      apiFetch<{ username: string; password: string; revealed_at: string }>(
        `/personas/${encodeURIComponent(id)}/reveal`,
        { method: "POST", body: JSON.stringify({ re_auth_token: reAuthToken }) },
      ),
    delete: (id: string) =>
      apiFetch<void>(`/personas/${encodeURIComponent(id)}`, { method: "DELETE" }),
  },
  userStories: {
    create: (data: {
      project_id: string;
      title: string;
      description: string;
      sprint_id?: string;
    }) =>
      apiFetch<any>("/user-stories", { method: "POST", body: JSON.stringify(data) }),
    get: (id: string) => apiFetch<any>(`/user-stories/${encodeURIComponent(id)}`),
    list: (projectId: string) => apiFetch<any[]>(`/user-stories?project_id=${encodeURIComponent(projectId)}`),
    update: (id: string, data: { title?: string; description?: string }) =>
      apiFetch<any>(`/user-stories/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    /** AI-generates draft test cases AND persists them as status=draft.
     *  Frontend should refetch testCases.list(id) afterwards to get the
     *  canonical rows with their server-assigned ids. */
    generate: (id: string) =>
      apiFetch<any>(`/user-stories/${encodeURIComponent(id)}/generate`, {
        method: "POST",
        body: "{}",
      }),
    /** Materialises one Robot script per approved non-stale test case under
     *  Saved_Projects/<slug>/Tests/Generated/story_<short>/. */
    buildScripts: (id: string) =>
      apiFetch<{
        story_id: string;
        project_slug: string | null;
        output_dir: string;
        built: Array<{ test_case_id: string; script_path: string; bytes_written: number }>;
        skipped: Array<{ test_case_id: string; title: string; reason: string }>;
      }>(`/user-stories/${encodeURIComponent(id)}/build-scripts`, {
        method: "POST",
        body: "{}",
      }),
  },
  testCases: {
    list: (userStoryId: string) =>
      apiFetch<any[]>(`/test-cases?user_story_id=${encodeURIComponent(userStoryId)}`),
    /** Per-case patch. Every field is optional -- the backend applies only
     *  what's sent. Editing any of title / steps / expected_result /
     *  preconditions clears `script_path` so the next bulk run re-builds. */
    patch: (
      id: string,
      body: {
        status?: "approved" | "rejected" | "draft";
        title?: string;
        steps?: string[];
        expected_result?: string;
        preconditions?: string | null;
        tags?: string[];
      },
    ) =>
      apiFetch<any>(`/test-cases/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    /** Manual create -- companion to the "Add case manually" button on
     *  the story detail page. Distinct from /user-stories/{id}/generate
     *  which goes through the LLM. */
    create: (body: {
      user_story_id: string;
      title: string;
      steps: string[];
      expected_result: string;
      preconditions?: string | null;
      tags: string[];
      status?: "draft" | "approved" | "rejected";
    }) =>
      apiFetch<any>(`/test-cases`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    /** Returns the saved Robot script content (404 if not built yet). */
    script: (id: string) =>
      apiFetch<{
        test_case_id: string;
        path: string;
        content: string;
        built_at: string | null;
      }>(`/test-cases/${encodeURIComponent(id)}/script`),
    /** Self-heal a failed test case: feed its output.xml + screenshot back to
     *  the LLM and rewrite the saved Robot script. The frontend can then open
     *  api.runs.testCaseStreamUrl to verify the rewrite. Capped at 2
     *  attempts/hour by the backend. */
    heal: (id: string, runFolder: string) =>
      apiFetch<{
        ok: boolean;
        test_case_id: string;
        script_path: string | null;
        diagnosis: {
          test_name: string;
          test_message: string;
          first_failure: { keyword_name: string; error_message: string } | null;
        };
        attempts: number;
        message: string;
      }>(`/test-cases/${encodeURIComponent(id)}/heal`, {
        method: "POST",
        body: JSON.stringify({ run_folder: runFolder }),
      }),
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
  sprints: {
    /** Create a sprint under a project. Owner check is enforced by the
     *  backend via the parent project. */
    create: (body: {
      project_id: string;
      name: string;
      goal?: string | null;
      state?: "planned" | "active" | "completed" | "cancelled";
      start_date?: string | null;
      end_date?: string | null;
    }) =>
      apiFetch<any>("/sprints", { method: "POST", body: JSON.stringify(body) }),
    list: (projectId: string, state?: "planned" | "active" | "completed" | "cancelled") => {
      const q = new URLSearchParams({ project_id: projectId });
      if (state) q.append("state", state);
      return apiFetch<any[]>(`/sprints?${q.toString()}`);
    },
    get: (id: string) => apiFetch<any>(`/sprints/${encodeURIComponent(id)}`),
    update: (
      id: string,
      body: {
        name?: string;
        goal?: string | null;
        state?: "planned" | "active" | "completed" | "cancelled";
        start_date?: string | null;
        end_date?: string | null;
      },
    ) => apiFetch<any>(`/sprints/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
    /** Soft-delete: state -> cancelled and every assigned story has
     *  sprint_id cleared back to null. */
    delete: (id: string) =>
      apiFetch<any>(`/sprints/${encodeURIComponent(id)}`, { method: "DELETE" }),
    assignStory: (sprintId: string, storyId: string) =>
      apiFetch<any>(
        `/sprints/${encodeURIComponent(sprintId)}/stories/${encodeURIComponent(storyId)}/assign`,
        { method: "POST", body: "{}" },
      ),
    unassignStory: (sprintId: string, storyId: string) =>
      apiFetch<any>(
        `/sprints/${encodeURIComponent(sprintId)}/stories/${encodeURIComponent(storyId)}`,
        { method: "DELETE" },
      ),
    /** Same response shape as `api.projects.testCases` so the existing
     *  test-cases panel rendering can be reused unchanged. */
    testCases: (id: string) =>
      apiFetch<{
        sprint_id: string;
        project_id: string;
        total: number;
        by_status: { draft: number; approved: number; rejected: number; stale: number };
        scripts_built: number;
        stories: Array<{
          id: string;
          title: string;
          version: number;
          test_cases: Array<{
            id: string;
            title: string;
            status: "draft" | "approved" | "rejected";
            stale: boolean;
            tags: string[];
            script_path: string | null;
            script_built_at: string | null;
            heal_attempts: number;
          }>;
        }>;
      }>(`/sprints/${encodeURIComponent(id)}/test-cases`),
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
    /** Live SSE for parallel bulk runs of all approved test cases in a story.
     *  Events emitted: start, queued (per tc), running, log (line per tc),
     *  done (per tc outcome), summary (final aggregate). When `auto_heal`
     *  is true the engine adds healing/healed/heal_failed events between
     *  attempts on a failed test. */
    userStoryStreamUrl: (
      storyId: string,
      body: { org_id: string; persona_id?: string | null; auto_heal?: boolean },
    ) => {
      const q = new URLSearchParams({ org_id: body.org_id });
      if (body.persona_id) q.append("persona_id", body.persona_id);
      if (body.auto_heal) q.append("auto_heal", "true");
      return withAuthQuery(
        `${API_BASE}/run/user-story/${encodeURIComponent(storyId)}/stream?${q.toString()}`,
      );
    },
    byTagStreamUrl: (
      tagName: string,
      body: {
        project_id: string;
        org_id: string;
        persona_id?: string | null;
        auto_heal?: boolean;
      },
    ) => {
      const q = new URLSearchParams({ project_id: body.project_id, org_id: body.org_id });
      if (body.persona_id) q.append("persona_id", body.persona_id);
      if (body.auto_heal) q.append("auto_heal", "true");
      return withAuthQuery(
        `${API_BASE}/run/tag/${encodeURIComponent(tagName)}/stream?${q.toString()}`,
      );
    },
    /** SSE stream for a sprint-scoped bulk run. Resolves to all approved
     *  non-stale test cases across every story in the sprint. */
    sprintStreamUrl: (
      sprintId: string,
      body: { org_id: string; persona_id?: string | null; auto_heal?: boolean },
    ) => {
      const q = new URLSearchParams({ org_id: body.org_id });
      if (body.persona_id) q.append("persona_id", body.persona_id);
      if (body.auto_heal) q.append("auto_heal", "true");
      return withAuthQuery(
        `${API_BASE}/run/sprint/${encodeURIComponent(sprintId)}/stream?${q.toString()}`,
      );
    },
    /** SSE stream that re-runs a single test case (used by Heal & retry). */
    testCaseStreamUrl: (
      testCaseId: string,
      body: { org_id: string; persona_id?: string | null },
    ) => {
      const q = new URLSearchParams({ org_id: body.org_id });
      if (body.persona_id) q.append("persona_id", body.persona_id);
      return withAuthQuery(
        `${API_BASE}/run/test-case/${encodeURIComponent(testCaseId)}/stream?${q.toString()}`,
      );
    },
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
