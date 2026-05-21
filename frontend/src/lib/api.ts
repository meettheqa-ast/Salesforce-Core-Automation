const _configuredApiBase = (process.env.NEXT_PUBLIC_API_URL || "").trim();
const _localhostHosts = new Set(["localhost", "127.0.0.1"]);
const _runtimeDefaultApiBase =
  typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://localhost:8000";

const API_BASE = (() => {
  const normalized = (v: string) => v.replace(/\/+$/, "");
  if (!_configuredApiBase) return normalized(_runtimeDefaultApiBase);
  if (typeof window === "undefined") return normalized(_configuredApiBase);
  try {
    const parsed = new URL(_configuredApiBase);
    const currentHost = window.location.hostname;
    // If env points to localhost but UI is opened via LAN host/IP, rewrite to
    // the current host so browser fetches the reachable backend origin.
    if (_localhostHosts.has(parsed.hostname) && !_localhostHosts.has(currentHost)) {
      parsed.hostname = currentHost;
      return normalized(parsed.toString());
    }
  } catch {
    // Keep original value on malformed URL; caller will surface a useful error.
  }
  return normalized(_configuredApiBase);
})();

// --- Auth token plumbing -------------------------------------------------
// Fetched lazily from /api/auth/jwt the first time `apiFetch` runs in the
// browser, then cached. The token is the **Google ID token** copied from
// NextAuth's session (see frontend/src/auth.ts); the FastAPI backend verifies
// it against Google's JWKS. On 401 we drop the cache and refetch from the
// session -- if the ID token is still expired (Google IDs live ~1h with no
// silent refresh wired up), we redirect to /login.

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

/** Best-effort toast for non-React callers. The ToastProvider listens on a
 *  module-level emitter; if it isn't mounted (e.g. during SSR or before
 *  layout hydrates), the call is a no-op rather than throwing. Imported
 *  lazily so we don't pull React component code into the API module's
 *  bundle graph at the top level. */
async function _maybeToast(message: string, kind: "error" | "success" | "info" = "info"): Promise<void> {
  if (typeof window === "undefined") return;
  try {
    const mod = await import("@/components/ui/ToastProvider");
    mod.pushToast(message, kind);
  } catch {
    // Toast module unavailable; keep silence rather than fail the request.
  }
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

// --- Delete lifecycle types ----------------------------------------------
// Shared shapes used by every delete + bulk-delete helper on this client.
// The backend returns 409 with a structured body when a hard delete is
// blocked by live children; we want the UI to render the blocker list
// cleanly without each page reparsing `error.message`.

/** One row that prevented a hard delete from succeeding. */
export interface DeleteBlocker {
  id: string;
  label: string;
  reason: string;
}

/** Per-id result returned by the bulk-delete endpoints. Mirrors the
 *  backend's pydantic model so the frontend can switch on `status`
 *  exhaustively. */
export interface BulkDeleteRowResult {
  id: string;
  status:
    | "soft_deleted"
    | "hard_deleted"
    | "skipped_blocked"
    | "skipped_forbidden"
    | "skipped_not_found"
    | "skipped_invalid_state";
  detail?: string;
  blockers?: DeleteBlocker[];
}

export interface BulkDeleteResponse {
  results: BulkDeleteRowResult[];
}

/** Pulls the structured 409 body out of an apiFetch Error. Returns
 *  null when the error isn't a 409 with the documented shape so
 *  callers can fall through to generic error handling. */
export function parseDeleteBlockersError(
  err: unknown,
): { detail: string; blockers: DeleteBlocker[] } | null {
  if (!(err instanceof Error)) return null;
  // apiFetch's error shape: `API 409: {"detail":"...","blockers":[...]}`
  // ``[\s\S]`` lets us span newlines without requiring the ``s`` (dotAll)
  // regex flag, which is only available in ES2018+; tsconfig targets a
  // lower revision on this project.
  const m = /^API\s+409:\s*([\s\S]*)$/.exec(err.message);
  if (!m) return null;
  let body: unknown;
  try {
    body = JSON.parse(m[1]);
  } catch {
    return null;
  }
  if (!body || typeof body !== "object") return null;
  // FastAPI wraps custom detail in {"detail": ...}; the inner detail
  // may itself be the structured object when we passed a dict.
  let inner: any = body;
  if ("detail" in inner) {
    inner = (inner as any).detail;
  }
  if (typeof inner === "string") {
    return { detail: inner, blockers: [] };
  }
  if (inner && typeof inner === "object" && Array.isArray((inner as any).blockers)) {
    return {
      detail: String((inner as any).detail || "Delete refused"),
      blockers: ((inner as any).blockers as any[]).map((b) => ({
        id: String(b.id),
        label: String(b.label || b.id),
        reason: String(b.reason || ""),
      })),
    };
  }
  return null;
}

export type RunStatus = "PASS" | "FAIL" | "EMPTY" | "VISUAL_DRIFT";

/** Phase 3: a single visual-regression baseline candidate. The "Pending
 *  baselines" panel renders a list of these per project. */
export interface PendingBaseline {
  test_case_id: string;
  step_label: string;
  baseline_path: string | null;
  current_path: string;
  diff_percent: number;
  is_new: boolean;
}

export interface RunHistoryRow {
  run_name: string;
  timestamp: string;
  project_slug?: string | null;
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
  /** Phase 4: Playwright trace files. Empty when the run didn't opt
   *  into ``--variable USE_PLAYWRIGHT_TRACE:1``. Each entry is a
   *  filename inside the run folder; the frontend opens
   *  https://trace.playwright.dev/?trace=<file-url> to view. */
  playwright_traces?: string[];
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
  return _runFetch(doFetch);
}

/** Multipart upload helper. Mirrors apiFetch's auth + 401 recovery but skips
 *  the JSON Content-Type so the browser populates the multipart boundary.
 *  Use for file uploads (context files, persona / test-data CSVs). */
async function apiFetchMultipart<T>(path: string, form: FormData, options?: Omit<RequestInit, "body" | "headers">): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    const auth = await authHeader();
    return fetch(`${API_BASE}${path}`, {
      method: "POST",
      ...options,
      headers: {
        ...auth,
      },
      body: form,
    });
  };
  return _runFetch(doFetch);
}

async function _runFetch<T>(doFetch: () => Promise<Response>): Promise<T> {

  let res: Response;
  try {
    res = await doFetch();
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "Network error";
    const wrapped = `${msg}. Is the API running at ${API_BASE}? (Set NEXT_PUBLIC_API_URL if needed.)`;
    // Surface network failures in a global toast so callers using
    // `.catch(() => setX([]))` don't silently swallow connectivity loss.
    void _maybeToast(wrapped, "error");
    throw new Error(wrapped);
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
  /** Salesforce app this persona should land in by default. Injected at run
   *  time as ${salesAutomationAppName} so PO keywords pick the right app. */
  default_app: string | null;
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

/** Result row from `GET /api/users/search`. The optional project flags let
 *  the PeerCombobox render "Already a member" / "Invite pending" badges
 *  without a second fetch. */
export interface UserSearchHit {
  id: string;
  email: string;
  name: string;
  picture: string;
  is_member: boolean;
  pending_invite_id: string | null;
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
        /** Optional ${salesAutomationAppName} override for this persona. */
        default_app?: string;
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
      /** Optional: ${salesAutomationAppName} override for this persona's runs. */
      default_app?: string;
    }) =>
      apiFetch<PersonaPublic>("/personas", { method: "POST", body: JSON.stringify(body) }),
    update: (
      id: string,
      body: {
        name?: string;
        role_profile?: string;
        is_default?: boolean;
        visibility?: "private" | "public";
        /** Pass empty string to clear, omit to leave unchanged. */
        default_app?: string;
      },
    ) =>
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
    assign: (id: string, owner_user_id?: string) =>
      apiFetch<any>(`/user-stories/${encodeURIComponent(id)}/assign`, {
        method: "POST",
        body: JSON.stringify({ owner_user_id: owner_user_id || null }),
      }),
    comments: (id: string) =>
      apiFetch<Array<{
        id: string;
        story_id: string;
        author_user_id: string;
        author_email: string;
        body: string;
        mentions: string[];
        created_at: string;
      }>>(`/user-stories/${encodeURIComponent(id)}/comments`),
    addComment: (id: string, body: string) =>
      apiFetch<any>(`/user-stories/${encodeURIComponent(id)}/comments`, {
        method: "POST",
        body: JSON.stringify({ body }),
      }),
    activity: (id: string, limit = 30) =>
      apiFetch<{ story_id: string; items: Array<any> }>(
        `/user-stories/${encodeURIComponent(id)}/activity?limit=${limit}`,
      ),
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
    /** Two-step delete. Default soft-archives (`status -> archived`);
     *  `permanent=true` hard-deletes the JSON row + indexes once every
     *  test case under the story is rejected. The 409 body lists the
     *  live test cases as blockers. */
    delete: (id: string, permanent: boolean = false) => {
      const sp = permanent ? "?permanent=true" : "";
      return apiFetch<any>(`/user-stories/${encodeURIComponent(id)}${sp}`, { method: "DELETE" });
    },
    bulkDelete: (ids: string[], permanent: boolean = false) =>
      apiFetch<BulkDeleteResponse>(`/user-stories/bulk-delete`, {
        method: "POST",
        body: JSON.stringify({ ids, permanent }),
      }),
  },
  testCases: {
    get: (id: string) => apiFetch<any>(`/test-cases/${encodeURIComponent(id)}`),
    /** List test cases for a story. Pass `includeArchived: true` to
     *  surface rows whose status is `rejected` (the Archived bucket).
     *  Default is false; the backend filters them out so list views
     *  don't accidentally show stale work. */
    list: (
      userStoryId: string,
      opts?: { includeArchived?: boolean },
    ) => {
      const qs = new URLSearchParams({ user_story_id: userStoryId });
      if (opts?.includeArchived) qs.set("include_archived", "true");
      return apiFetch<any[]>(`/test-cases?${qs.toString()}`);
    },
    /** Restore a previously-archived test case (status: rejected -> draft).
     *  Dedicated endpoint instead of a generic patch so the audit log
     *  has a distinct `tc_restored` action. */
    restore: (id: string) =>
      apiFetch<any>(`/test-cases/${encodeURIComponent(id)}/restore`, {
        method: "POST",
        body: "{}",
      }),
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
    saveScript: (id: string, content: string) =>
      apiFetch<{ ok: boolean; test_case_id: string; path: string; bytes_written: number }>(
        `/test-cases/${encodeURIComponent(id)}/script`,
        {
          method: "PUT",
          body: JSON.stringify({ content }),
        },
      ),
    scriptHistory: (id: string) =>
      apiFetch<{
        test_case_id: string;
        items: Array<{ name: string; path: string; modified_at: string; size: number }>;
      }>(`/test-cases/${encodeURIComponent(id)}/script/history`),
    scriptHistoryItem: (id: string, name: string) =>
      apiFetch<{ test_case_id: string; name: string; content: string }>(
        `/test-cases/${encodeURIComponent(id)}/script/history/${encodeURIComponent(name)}`,
      ),
    /** Build/refresh one test case's Robot script on demand. */
    buildScript: (id: string) =>
      apiFetch<{
        ok: boolean;
        test_case_id: string;
        script_path: string;
        built_at: string;
      }>(`/test-cases/${encodeURIComponent(id)}/build-script`, {
        method: "POST",
        body: "{}",
      }),
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
    /** Two-step delete. Default soft-rejects (`status -> rejected`,
     *  matches the existing "Reject" button via `patch`); `permanent=
     *  true` purges the JSON row + on-disk Robot script + history.
     *  No children to check, so no 409 path -- the only refusal is
     *  "test case is not yet rejected" (409 with detail message). */
    delete: (id: string, permanent: boolean = false) => {
      const sp = permanent ? "?permanent=true" : "";
      return apiFetch<any>(`/test-cases/${encodeURIComponent(id)}${sp}`, { method: "DELETE" });
    },
    bulkDelete: (ids: string[], permanent: boolean = false) =>
      apiFetch<BulkDeleteResponse>(`/test-cases/bulk-delete`, {
        method: "POST",
        body: JSON.stringify({ ids, permanent }),
      }),
  },
  tags: {
    list: (projectId: string) => apiFetch<any[]>(`/tags?project_id=${encodeURIComponent(projectId)}`),
    create: (body: { project_id: string; name: string; color?: string }) =>
      apiFetch<any>("/tags", { method: "POST", body: JSON.stringify(body) }),
    delete: (tagId: string, projectId: string) =>
      apiFetch<{ ok: boolean; deleted: string }>(
        `/tags/${encodeURIComponent(tagId)}?project_id=${encodeURIComponent(projectId)}`,
        { method: "DELETE" },
      ),
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
    /** Two-step lifecycle delete. Default (`permanent=false`)
     *  soft-cancels the sprint and unlinks its stories; the row stays
     *  in the JSON store so it can be permanently deleted later. With
     *  `permanent=true` the backend hard-deletes the row, refusing
     *  with 409 + a blocker list when live stories remain (caller
     *  archives them first). The 409 body is parsed by
     *  ``parseDeleteBlockersError`` so the UI can render the blocker
     *  list without bespoke string slicing. */
    delete: (id: string, permanent: boolean = false) => {
      const sp = permanent ? "?permanent=true" : "";
      return apiFetch<any>(`/sprints/${encodeURIComponent(id)}${sp}`, { method: "DELETE" });
    },
    /** Same lifecycle, applied to a list of ids. Always returns 200
     *  with per-id results so the UI can render mixed outcomes
     *  (some soft-deleted, some hard-deleted, some blocked). */
    bulkDelete: (ids: string[], permanent: boolean = false) =>
      apiFetch<BulkDeleteResponse>(`/sprints/bulk-delete`, {
        method: "POST",
        body: JSON.stringify({ ids, permanent }),
      }),
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
  /** Phase 3: visual regression endpoints. Per-project baseline
   *  management. Server-side gated by ``settings.pw_visual_regression``;
   *  callers should catch 403 to handle "feature not enabled" cleanly. */
  visualRegression: {
    pending: (projectSlug: string) =>
      apiFetch<PendingBaseline[]>(
        `/api/visual-regression/${encodeURIComponent(projectSlug)}/pending-baselines`,
      ),
    promote: (projectSlug: string, body: { test_case_id: string; step_label?: string }) =>
      apiFetch<{ ok: boolean; test_case_id: string; step_label: string }>(
        `/api/visual-regression/${encodeURIComponent(projectSlug)}/promote-baseline`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    /** Returns a sync URL for embedding via <img> -- includes the JWT. */
    screenshotUrl: (
      projectSlug: string,
      kind: "baseline" | "current" | "diff",
      filename: string,
      runFolder?: string,
    ) => {
      const sp = new URLSearchParams({ kind, filename });
      if (runFolder) sp.set("run_folder", runFolder);
      return withAuthQuery(
        `${API_BASE}/api/visual-regression/${encodeURIComponent(projectSlug)}/screenshot?${sp.toString()}`,
      );
    },
  },
  generate: {
    quick: (data: any) => apiFetch<any>("/api/generate/robot-suite", { method: "POST", body: JSON.stringify(data) }),
    stepwise: (data: any) => apiFetch<any>("/api/generate/mcp-stepwise", { method: "POST", body: JSON.stringify(data) }),
    /** SSE -- the JWT is embedded in the query string because EventSource cannot set headers. */
    stepwiseStreamUrl: () => withAuthQuery(`${API_BASE}/api/generate/mcp-stepwise/stream`),
    jobs: {
      create: (data: any) =>
        apiFetch<{ job_id: string }>("/api/generate/jobs", {
          method: "POST",
          body: JSON.stringify(data),
        }),
      status: (jobId: string) =>
        apiFetch<GenerationJobSnapshot>(`/api/generate/jobs/${encodeURIComponent(jobId)}`),
      inFlight: () =>
        apiFetch<{ job: { id: string; status: string; mode: string; current_phase: string; event_seq: number } | null }>(
          "/api/generate/jobs/in-flight",
        ),
      cancel: (jobId: string) =>
        apiFetch<{ ok: boolean; status: string; cancellation_requested_at?: string }>(
          `/api/generate/jobs/${encodeURIComponent(jobId)}/cancel`,
          { method: "POST", body: "{}" },
        ),
      eventsUrl: (jobId: string, fromSeq = 0) =>
        withAuthQuery(
          `${API_BASE}/api/generate/jobs/${encodeURIComponent(jobId)}/events?from=${fromSeq}`,
        ),
    },
    /** Deterministic recipe registry. Drives the "Recipes" panel in /generate. */
    recipes: () => apiFetch<Array<{ name: string; description: string; sample_prompt: string }>>("/api/generate/recipes"),
    /** Save edited generated script to disk. */
    saveScript: (data: { robot_code: string; test_path?: string }) =>
      apiFetch<{ ok: boolean; test_path: string; bytes_written: number }>("/api/generate/save", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    /** Phase 2: Recording mode. Three endpoints orchestrate a session:
     *    record.start -> launches Playwright + auto-login, returns id
     *    record.actions -> polls captured actions while recording
     *    record.stop -> closes browser, returns LLM-translated .robot
     *
     *  Gated server-side by ``settings.pw_recording``; clients should
     *  catch 403 / 503 and surface "feature not enabled" gracefully. */
    record: {
      start: (data: {
        sandbox_url: string;
        username: string;
        password: string;
        persona_id?: string;
        test_name?: string;
      }) =>
        apiFetch<{ session_id: string }>("/api/generate/record/start", {
          method: "POST",
          body: JSON.stringify(data),
        }),
      actions: (sessionId: string) =>
        apiFetch<{ session_id: string; actions: any[]; count: number }>(
          `/api/generate/record/${encodeURIComponent(sessionId)}/actions`,
        ),
      stop: (sessionId: string) =>
        apiFetch<any>(
          `/api/generate/record/${encodeURIComponent(sessionId)}/stop`,
          { method: "POST", body: "{}" },
        ),
    },
  },
  heal: {
    save: (data: {
      session_id: string;
      sobject: string;
      save_action?: string;
      duplicate_strategy?: string;
      project_slug?: string;
      run_id?: string;
      job_id?: string;
      step_index?: number;
      sandbox_url?: string;
      username?: string;
      password?: string;
    }) =>
      apiFetch<any>("/api/heal/save", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    events: (params: { run_id?: string; job_id?: string; project_slug?: string; limit?: number }) => {
      const q = new URLSearchParams();
      if (params.run_id) q.set("run_id", params.run_id);
      if (params.job_id) q.set("job_id", params.job_id);
      if (params.project_slug) q.set("project_slug", params.project_slug);
      q.set("limit", String(params.limit ?? 100));
      return apiFetch<{ events: HealEventRow[] }>(`/api/heal/events?${q.toString()}`);
    },
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
      /** Persona's default Salesforce app. Backend maps to
       *  ${salesAutomationAppName} so PO keywords pick the right app. */
      default_app?: string;
    }) => {
      const q = new URLSearchParams({
        test_path: data.test_path,
        sandbox_url: data.sandbox_url,
        username: data.username,
        password: data.password,
        headless: String(data.headless ?? true),
      });
      if (data.default_app) q.set("default_app", data.default_app);
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
  users: {
    /** Search the same-domain user directory for the Add Member combobox.
     *  Empty `q` returns []. When `projectName` (slug) is set, each hit is
     *  annotated with `is_member` and `pending_invite_id` so the UI can
     *  badge & disable rows that are already on the project or already
     *  invited. */
    search: (q: string, opts?: { projectName?: string; limit?: number }) => {
      const sp = new URLSearchParams({ q });
      if (opts?.projectName) sp.set("project_name", opts.projectName);
      if (opts?.limit) sp.set("limit", String(opts.limit));
      return apiFetch<UserSearchHit[]>(`/api/users/search?${sp.toString()}`);
    },
  },

  // ---- Jira integration (per-project) ----
  jira: {
    getOrgConnection: () => apiFetch<JiraConnectionView | null>(`/integrations/jira/connection`),
    upsertOrgConnection: (body: JiraConnectionWrite) =>
      apiFetch<JiraConnectionView>(`/integrations/jira/connection`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    deleteOrgConnection: () =>
      apiFetch<void>(`/integrations/jira/connection`, { method: "DELETE" }),
    getProjectConnection: (slug: string) =>
      apiFetch<JiraConnectionView | null>(`/projects/${encodeURIComponent(slug)}/integrations/jira/connection`),
    upsertProjectConnection: (slug: string, body: JiraConnectionWrite) =>
      apiFetch<JiraConnectionView>(`/projects/${encodeURIComponent(slug)}/integrations/jira/connection`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    deleteProjectConnection: (slug: string) =>
      apiFetch<void>(`/projects/${encodeURIComponent(slug)}/integrations/jira/connection`, { method: "DELETE" }),
    test: (slug: string) =>
      apiFetch<{ ok: boolean; account_id?: string; display_name?: string; email?: string }>(
        `/projects/${encodeURIComponent(slug)}/integrations/jira/test`,
        { method: "POST" },
      ),
    listProjects: (slug: string) =>
      apiFetch<JiraProjectRow[]>(`/projects/${encodeURIComponent(slug)}/integrations/jira/projects`),
    sync: (slug: string, jiraProjectKey: string, includeComments = true) => {
      const sp = new URLSearchParams({ jira_project_key: jiraProjectKey, include_comments: String(includeComments) });
      return apiFetch<JiraSyncStats>(
        `/projects/${encodeURIComponent(slug)}/integrations/jira/sync?${sp.toString()}`,
        { method: "POST" },
      );
    },
    listSyncedSprints: (slug: string, jiraProjectKey?: string) => {
      const qs = jiraProjectKey ? `?jira_project_key=${encodeURIComponent(jiraProjectKey)}` : "";
      return apiFetch<JiraSyncedSprint[]>(`/projects/${encodeURIComponent(slug)}/integrations/jira/sprints${qs}`);
    },
    listSyncedIssues: (slug: string, opts?: { jiraProjectKey?: string; sprintJiraId?: string; limit?: number; offset?: number }) => {
      const sp = new URLSearchParams();
      if (opts?.jiraProjectKey) sp.set("jira_project_key", opts.jiraProjectKey);
      if (opts?.sprintJiraId) sp.set("sprint_jira_id", opts.sprintJiraId);
      if (opts?.limit) sp.set("limit", String(opts.limit));
      if (opts?.offset) sp.set("offset", String(opts.offset));
      const qs = sp.toString();
      return apiFetch<JiraSyncedIssue[]>(`/projects/${encodeURIComponent(slug)}/integrations/jira/issues${qs ? `?${qs}` : ""}`);
    },
    importToPortal: (slug: string, body: { sprint_jira_ids: string[]; issue_jira_ids: string[] }) =>
      apiFetch<{ imported_sprint_ids: string[]; imported_story_ids: string[] }>(
        `/projects/${encodeURIComponent(slug)}/integrations/jira/import`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    /** Remove a synced sprint from the local mirror (Atlassian is
     *  untouched -- the next "Sync now" will re-pull it unless you
     *  also filter it out on the Atlassian side). */
    deleteSyncedSprint: (slug: string, rowId: string) =>
      apiFetch<{ id: string; status: string }>(
        `/projects/${encodeURIComponent(slug)}/integrations/jira/sprints/${encodeURIComponent(rowId)}`,
        { method: "DELETE" },
      ),
    bulkDeleteSyncedSprints: (slug: string, ids: string[]) =>
      apiFetch<{ deleted: number; deleted_ids: string[]; skipped: string[] }>(
        `/projects/${encodeURIComponent(slug)}/integrations/jira/sprints/bulk-delete`,
        { method: "POST", body: JSON.stringify({ ids }) },
      ),
    /** Remove a synced issue from the local mirror (cascades to its
     *  jira_comments rows). Atlassian is untouched. */
    deleteSyncedIssue: (slug: string, rowId: string) =>
      apiFetch<{ id: string; status: string }>(
        `/projects/${encodeURIComponent(slug)}/integrations/jira/issues/${encodeURIComponent(rowId)}`,
        { method: "DELETE" },
      ),
    bulkDeleteSyncedIssues: (slug: string, ids: string[]) =>
      apiFetch<{ deleted: number; deleted_ids: string[]; skipped: string[] }>(
        `/projects/${encodeURIComponent(slug)}/integrations/jira/issues/bulk-delete`,
        { method: "POST", body: JSON.stringify({ ids }) },
      ),
  },

  // ---- GitHub integration ----
  github: {
    getInstallUrl: () => apiFetch<{ install_url: string; app_id: string }>(`/integrations/github/app-install-url`),
    createAppConnection: (body: { installation_id: string; owner_login?: string }) =>
      apiFetch<GitHubConnectionView & { webhook_secret_plaintext: string }>(
        `/integrations/github/connections/app`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    getProjectConnection: (slug: string) =>
      apiFetch<GitHubConnectionView | null>(`/projects/${encodeURIComponent(slug)}/integrations/github/connection`),
    upsertProjectPAT: (slug: string, body: { access_token: string; owner_login: string }) =>
      apiFetch<GitHubConnectionView & { webhook_secret_plaintext: string }>(
        `/projects/${encodeURIComponent(slug)}/integrations/github/connection/pat`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    deleteProjectConnection: (slug: string) =>
      apiFetch<void>(`/projects/${encodeURIComponent(slug)}/integrations/github/connection`, { method: "DELETE" }),
    test: (slug: string) =>
      apiFetch<{ ok: boolean; result: unknown }>(
        `/projects/${encodeURIComponent(slug)}/integrations/github/test`,
        { method: "POST" },
      ),
    listRepos: (slug: string) =>
      apiFetch<GitHubRepoListing[]>(`/projects/${encodeURIComponent(slug)}/integrations/github/repos`),
    connectRepo: (slug: string, body: { owner: string; name: string; default_branch?: string; suites_root_path?: string }) =>
      apiFetch<GitHubRepoRow>(`/projects/${encodeURIComponent(slug)}/integrations/github/repos`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    listConnectedRepos: (slug: string) =>
      apiFetch<GitHubRepoRow[]>(`/projects/${encodeURIComponent(slug)}/integrations/github/connected-repos`),
    pushScripts: (slug: string, repoId: string) =>
      apiFetch<{ committed: number; files: string[] }>(
        `/projects/${encodeURIComponent(slug)}/integrations/github/repos/${encodeURIComponent(repoId)}/push`,
        { method: "POST" },
      ),
    refreshWorkflow: (slug: string, repoId: string) =>
      apiFetch<{ path: string; yaml: string }>(
        `/projects/${encodeURIComponent(slug)}/integrations/github/repos/${encodeURIComponent(repoId)}/workflow`,
        { method: "POST" },
      ),
  },

  // ---- Context files ----
  contextFiles: {
    list: (slug: string) =>
      apiFetch<ContextFileRow[]>(`/projects/${encodeURIComponent(slug)}/context-files`),
    upload: (slug: string, file: File, description?: string) => {
      const form = new FormData();
      form.append("file", file);
      const qs = description ? `?description=${encodeURIComponent(description)}` : "";
      return apiFetchMultipart<ContextFileRow & { columns: string[] }>(
        `/projects/${encodeURIComponent(slug)}/context-files${qs}`,
        form,
      );
    },
    preview: (slug: string, fileId: string, limit = 20) =>
      apiFetch<{ file: ContextFileRow; rows: { row_index: number; data: Record<string, string>; searchable_text: string }[] }>(
        `/projects/${encodeURIComponent(slug)}/context-files/${encodeURIComponent(fileId)}/preview?limit=${limit}`,
      ),
    delete: (slug: string, fileId: string) =>
      apiFetch<void>(`/projects/${encodeURIComponent(slug)}/context-files/${encodeURIComponent(fileId)}`, {
        method: "DELETE",
      }),
  },

  // ---- AI Prompt Management ----
  //
  // Backend lives at /api/prompts. The Settings -> AI Prompts page
  // walks the resolver UI through:
  //   list() -> get(id) -> appendVersion / activate / reset / preview
  //
  // Resolution chain is sparse user -> project -> org -> system seed,
  // so the list view labels each row with its `source_scope` (see
  // PromptActiveOverride.scope) and lets admins flip the org default.
  prompts: {
    categories: () =>
      apiFetch<PromptCategory[]>("/api/prompts/categories"),
    list: (opts?: { category?: string; includeDeleted?: boolean; mineOnly?: boolean }) => {
      const qs = new URLSearchParams();
      if (opts?.category) qs.set("category", opts.category);
      if (opts?.includeDeleted) qs.set("include_deleted", "true");
      if (opts?.mineOnly) qs.set("mine_only", "true");
      const q = qs.toString();
      return apiFetch<PromptTemplateSummary[]>(`/api/prompts${q ? `?${q}` : ""}`);
    },
    get: (id: string, versionsLimit = 50) =>
      apiFetch<PromptTemplateDetail>(
        `/api/prompts/${encodeURIComponent(id)}?versions_limit=${versionsLimit}`,
      ),
    /** Fetch a single version's full body (versions listed on get()
     *  only carry metadata; the body is heavy and lazy-loaded). */
    getVersion: (id: string, versionNumber: number) =>
      apiFetch<PromptVersionBody>(
        `/api/prompts/${encodeURIComponent(id)}/versions/${versionNumber}`,
      ),
    /** Create a user / project / org clone (or a blank template) and
     *  return the detail shape. */
    create: (body: PromptCreateRequest) =>
      apiFetch<PromptTemplateDetail>("/api/prompts", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    /** Append a new immutable version (the "save" action in the
     *  editor). */
    appendVersion: (id: string, body: { body: string; change_note?: string }) =>
      apiFetch<PromptVersionSummary>(
        `/api/prompts/${encodeURIComponent(id)}/versions`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    /** Pin a specific version as the active override for a scope.
     *  Replaces any existing override row for (scope, scope_id, category). */
    activate: (
      id: string,
      body: { version_number: number; scope: "user" | "project" | "org"; scope_id?: string | null },
    ) =>
      apiFetch<PromptActiveOverride>(
        `/api/prompts/${encodeURIComponent(id)}/activate`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    /** Remove a scope's override row -- the resolver falls back to the
     *  next layer (project -> org -> system seed). */
    reset: (
      id: string,
      body: { scope: "user" | "project" | "org"; scope_id?: string | null },
    ) =>
      apiFetch<{ removed_override: boolean; category: string }>(
        `/api/prompts/${encodeURIComponent(id)}/reset`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    /** Render the latest version body against a context dict without
     *  hitting the LLM. ``strict=false`` lets the user preview even
     *  when context fields are missing (rendered as ""). */
    preview: (id: string, body: { context: Record<string, unknown>; strict?: boolean }) =>
      apiFetch<PromptPreviewResponse>(
        `/api/prompts/${encodeURIComponent(id)}/preview`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    /** Render + send to LLM + parse the response, without persisting.
     *  Used by the editor's Preview tab to verify a draft prompt
     *  produces parseable output for the template's output_format
     *  before the user activates it for real generation runs. */
    dryRun: (
      id: string,
      body: { context: Record<string, unknown>; user_message?: string; qa_mode?: string },
    ) =>
      apiFetch<PromptDryRunResponse>(
        `/api/prompts/${encodeURIComponent(id)}/dry-run`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    /** Soft delete (sets deleted_at) by default; ?permanent=true is
     *  admin-only and drops the row + cascades to its versions. */
    delete: (id: string, opts?: { permanent?: boolean }) =>
      apiFetch<{ id: string; status: "soft" | "permanent" }>(
        `/api/prompts/${encodeURIComponent(id)}${opts?.permanent ? "?permanent=true" : ""}`,
        { method: "DELETE" },
      ),
    /** prompt_usage_audit feed. Non-admins are scoped to their own
     *  rows by the backend. */
    audit: (opts?: { category?: string; userId?: string; limit?: number }) => {
      const qs = new URLSearchParams();
      if (opts?.category) qs.set("category", opts.category);
      if (opts?.userId) qs.set("user_id", opts.userId);
      if (opts?.limit) qs.set("limit", String(opts.limit));
      const q = qs.toString();
      return apiFetch<PromptAuditRow[]>(`/api/prompts/audit${q ? `?${q}` : ""}`);
    },
  },

  // ---- Test case import (CSV / Excel) ----
  //
  // Two-step wizard:
  //   1. imports.parse(slug, file) -> { batch_id, columns, suggested_mapping, preview, ... }
  //   2. imports.commit(body) -> { imported, skipped, failed, ... }
  //
  // The wizard at /projects/[name]/imports calls these in order and
  // shows the user the suggested_mapping for confirmation in between.
  imports: {
    /** Step 1: upload + preview. Returns the parse response + a
     *  batch_id the wizard threads into the subsequent commit call. */
    parse: (slug: string, file: File) => {
      const form = new FormData();
      form.append("project_slug", slug);
      form.append("file", file);
      return apiFetchMultipart<ImportParseResponse>("/api/imports/test-cases/parse", form);
    },
    /** Step 2: persist. body.mapping is the user-confirmed
     *  column-to-canonical-field mapping; body.target picks the
     *  destination story (or null when story_id is in the mapping). */
    commit: (body: {
      batch_id: string;
      mapping: Record<string, string | null>;
      target: { project_id: string; sprint_id?: string | null; story_id?: string | null };
      duplicate_strategy: "skip" | "overwrite" | "create_new";
      default_status: "draft" | "approved";
    }) =>
      apiFetch<ImportCommitResponse>("/api/imports/test-cases/commit", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    /** List historical import batches under a project. */
    list: (slug: string, limit = 50) =>
      apiFetch<{ batches: ImportBatchSummary[] }>(
        `/api/imports?project_slug=${encodeURIComponent(slug)}&limit=${limit}`,
      ),
    /** Single batch detail (includes failed_rows for the error
     *  download). */
    get: (batchId: string) =>
      apiFetch<ImportBatchDetail>(`/api/imports/${encodeURIComponent(batchId)}`),
    /** Hard-delete every TC this batch created. Admin / lead only. */
    rollback: (batchId: string) =>
      apiFetch<{ batch_id: string; reverted_count: number; status: string }>(
        `/api/imports/${encodeURIComponent(batchId)}/rollback`,
        { method: "POST", body: "{}" },
      ),
  },

  // ---- Test data tables ----
  testData: {
    list: (slug: string) =>
      apiFetch<TestDataTableRow[]>(`/projects/${encodeURIComponent(slug)}/test-data-tables`),
    sampleCsvUrl: (slug: string, kind: "user_types" | "accounts" | "opportunities" | "leads") =>
      withAuthQuery(
        `${API_BASE}/projects/${encodeURIComponent(slug)}/test-data-tables/sample-csv?kind=${kind}`,
      ),
    create: (slug: string, params: { name: string; description?: string; kind?: string; file: File }) => {
      const form = new FormData();
      form.append("name", params.name);
      if (params.description) form.append("description", params.description);
      if (params.kind) form.append("kind", params.kind);
      form.append("file", params.file);
      return apiFetchMultipart<TestDataTableRow>(`/projects/${encodeURIComponent(slug)}/test-data-tables`, form);
    },
    get: (slug: string, tableId: string, limit = 200) =>
      apiFetch<{ table: TestDataTableRow; rows: { row_index: number; data: Record<string, string> }[] }>(
        `/projects/${encodeURIComponent(slug)}/test-data-tables/${encodeURIComponent(tableId)}?limit=${limit}`,
      ),
    delete: (slug: string, tableId: string) =>
      apiFetch<void>(`/projects/${encodeURIComponent(slug)}/test-data-tables/${encodeURIComponent(tableId)}`, {
        method: "DELETE",
      }),
  },

  // ---- Schedules ----
  schedules: {
    list: (slug: string) =>
      apiFetch<ScheduleRow[]>(`/projects/${encodeURIComponent(slug)}/schedules`),
    create: (slug: string, body: ScheduleWrite) =>
      apiFetch<ScheduleRow>(`/projects/${encodeURIComponent(slug)}/schedules`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (slug: string, id: string, body: Partial<ScheduleWrite>) =>
      apiFetch<ScheduleRow>(`/projects/${encodeURIComponent(slug)}/schedules/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    delete: (slug: string, id: string) =>
      apiFetch<void>(`/projects/${encodeURIComponent(slug)}/schedules/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),
    runNow: (slug: string, id: string) =>
      apiFetch<ScheduleRunRow>(`/projects/${encodeURIComponent(slug)}/schedules/${encodeURIComponent(id)}/run-now`, {
        method: "POST",
        body: "{}",
      }),
    history: (slug: string, id: string, limit = 50) =>
      apiFetch<ScheduleRunRow[]>(
        `/projects/${encodeURIComponent(slug)}/schedules/${encodeURIComponent(id)}/runs?limit=${limit}`,
      ),
  },

  // ---- Personas: bulk import + sample CSV (extension surface) ----
  personasBulk: {
    sampleCsvUrl: () => withAuthQuery(`${API_BASE}/personas/sample-csv`),
    bulkImport: (projectId: string, orgId: string, file: File, dryRun = false) => {
      const form = new FormData();
      form.append("project_id", projectId);
      form.append("org_id", orgId);
      form.append("file", file);
      const qs = dryRun ? "?dry_run=true" : "";
      return apiFetchMultipart<PersonaBulkImportResult>(`/personas/bulk-import${qs}`, form);
    },
  },
};

// ---- Type contracts for new endpoints --------------------------------

export interface JiraConnectionView {
  id: string;
  scope: "org" | "project";
  project_slug: string | null;
  base_url: string;
  email: string;
  default_jira_project_key: string | null;
  has_token: boolean;
  created_at: string | null;
  updated_at: string | null;
}
export interface JiraConnectionWrite {
  base_url: string;
  email: string;
  api_token: string;
  default_jira_project_key?: string;
}
export interface JiraProjectRow {
  id: string;
  key: string;
  name: string;
  project_type_key?: string | null;
  lead?: string | null;
}
export interface JiraSyncStats {
  project_key: string;
  projects_seen: number;
  sprints_upserted: number;
  issues_upserted: number;
  comments_upserted: number;
  errors: string[];
}
export interface JiraSyncedSprint {
  id: string;
  jira_id: string;
  name: string;
  state: string;
  start_date: string | null;
  end_date: string | null;
  portal_sprint_id: string | null;
}
export interface JiraSyncedIssue {
  id: string;
  jira_id: string;
  jira_key: string;
  issue_type: string | null;
  status: string | null;
  summary: string | null;
  assignee: string | null;
  sprint_jira_id: string | null;
  portal_story_id: string | null;
  jira_updated_at: string | null;
}

export interface GitHubConnectionView {
  id: string;
  scope: "org" | "project";
  project_slug: string | null;
  auth_kind: "app" | "pat";
  owner_login: string | null;
  app_id: string | null;
  installation_id: string | null;
  has_private_key: boolean;
  has_access_token: boolean;
  has_webhook_secret: boolean;
  created_at: string | null;
  updated_at: string | null;
}
export interface GitHubRepoListing {
  id: number;
  name: string;
  owner: string;
  full_name: string;
  default_branch: string;
  private: boolean;
  html_url: string;
}
export interface GitHubRepoRow {
  id: string;
  connection_id: string;
  project_slug: string;
  owner: string;
  name: string;
  full_name: string;
  default_branch: string;
  is_connected: boolean;
  workflow_path: string;
  suites_root_path: string;
  last_pushed_at: string | null;
  last_workflow_run_id: string | null;
  created_at: string | null;
}

export interface ContextFileRow {
  id: string;
  project_slug: string;
  filename: string;
  mime: string;
  kind: "csv" | "xlsx" | "pdf" | "docx" | "md" | "txt";
  size: number;
  sha256: string;
  row_count: number;
  chunk_count: number;
  description: string | null;
  uploaded_by_user_id: string | null;
  uploaded_at: string | null;
}

export interface TestDataTableRow {
  id: string;
  project_slug: string;
  name: string;
  description: string | null;
  kind: string;
  columns: string[];
  source_file_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

// ---- Test case import (CSV/Excel) ----

/** Response of POST /api/imports/test-cases/parse. The wizard renders
 *  the preview + suggested_mapping side-by-side for the user to
 *  confirm before calling commit. */
export interface ImportParseResponse {
  batch_id: string;
  source_filename: string;
  source_kind: "csv" | "xlsx" | "xls" | string;
  columns: string[];
  row_count: number;
  sheet_name: string | null;
  encoding: string | null;
  /** Pre-filled mapping; null means "we couldn't guess; user must
   *  pick a canonical field or leave the column out". */
  suggested_mapping: Record<string, string | null>;
  /** First 50 rows. Cell values are pre-coerced to strings. */
  preview: Record<string, string>[];
  /** The canonical fields the wizard's <select> dropdowns should
   *  expose. Stable across batches; sent so the frontend doesn't
   *  hard-code the list. */
  canonical_fields: string[];
}

export interface ImportFailedRow {
  row_index: number;
  title: string | null;
  message: string;
}

export interface ImportCommitResponse {
  batch_id: string;
  status: "committed" | "partial" | "failed";
  imported_count: number;
  skipped_count: number;
  failed_count: number;
  created_test_case_ids: string[];
  failed_rows: ImportFailedRow[];
  duration_ms: number;
}

export interface ImportBatchSummary {
  id: string;
  project_slug: string;
  user_id: string | null;
  source_filename: string;
  source_kind: string;
  target_project_id: string;
  target_sprint_id: string | null;
  target_story_id: string | null;
  mapping: Record<string, string | null>;
  duplicate_strategy: string;
  total_rows: number;
  imported_count: number;
  skipped_count: number;
  failed_count: number;
  status: "pending" | "committed" | "partial" | "failed" | "rolled_back" | string;
  error_message: string | null;
  failed_rows_count?: number;
  created_at: string | null;
  committed_at: string | null;
}

export interface ImportBatchDetail extends ImportBatchSummary {
  /** Full failed-rows array (only present on the detail endpoint). */
  failed_rows: ImportFailedRow[];
}

// ---- AI Prompt Management ----

export type PromptOutputFormat =
  | "json_array"
  | "markdown_table"
  | "robot_script"
  | "freeform"
  | string;

export type PromptScope = "user" | "project" | "org" | "system";

/** /api/prompts/categories row. Drives the editor's variable-reference
 *  right rail + the create-form category dropdown. */
export interface PromptCategory {
  category: string;
  default_name: string;
  description: string;
  output_format: PromptOutputFormat;
  placeholders: string[];
  compose_with_playbook: boolean;
}

export interface PromptTemplateSummary {
  id: string;
  category: string;
  name: string;
  description: string | null;
  is_system: boolean;
  is_active: boolean;
  output_format: PromptOutputFormat;
  model_hint: string | null;
  placeholders_declared: string[];
  owner_user_id: string | null;
  source_template_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  deleted_at: string | null;
}

export interface PromptVersionSummary {
  id: string;
  version_number: number;
  change_note: string | null;
  body_bytes: number;
  created_by_user_id: string | null;
  created_at: string | null;
}

export interface PromptVersionBody extends PromptVersionSummary {
  template_id: string;
  body: string;
}

export interface PromptActiveOverride {
  scope: PromptScope;
  scope_id: string | null;
  template_id: string;
  template_name: string;
  active_version_id: string;
  active_version_number: number;
}

export interface PromptTemplateDetail extends PromptTemplateSummary {
  versions: PromptVersionSummary[];
  overrides: PromptActiveOverride[];
}

export interface PromptCreateRequest {
  category: string;
  name: string;
  description?: string | null;
  body: string;
  output_format: PromptOutputFormat;
  model_hint?: string | null;
  placeholders_declared?: string[] | null;
  source_template_id?: string | null;
  scope: "user" | "project" | "org";
  scope_id?: string | null;
  change_note?: string | null;
}

export interface PromptPreviewResponse {
  text: string;
  bytes: number;
  placeholders_used: string[];
  output_format: PromptOutputFormat;
  template_id: string;
  template_name: string;
}

export interface PromptDryRunResponse {
  template_id: string;
  template_name: string;
  output_format: PromptOutputFormat;
  model: string | null;
  provider: string | null;
  latency_ms: number;
  raw: string;
  test_cases: Array<{
    title: string;
    steps: string[];
    expected_result: string;
    preconditions: string | null;
    suggested_tags: string[];
  }>;
  warnings: string[];
}

export interface PromptAuditRow {
  id: string;
  template_version_id: string | null;
  category: string;
  user_id: string | null;
  project_id: string | null;
  model: string | null;
  provider: string | null;
  qa_mode: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  target_type: string | null;
  target_id: string | null;
  created_at: string | null;
}

export type ScheduleRunner = "local" | "github_actions";
export type ScheduleTargetKind = "sprint" | "story" | "test_case" | "tag";
export interface ScheduleRow {
  id: string;
  project_slug: string;
  name: string;
  target_kind: ScheduleTargetKind;
  target_id: string;
  cron: string;
  timezone: string;
  runner: ScheduleRunner;
  github_repo_id: string | null;
  persona_id: string | null;
  org_id: string | null;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}
export interface ScheduleWrite {
  name: string;
  target_kind: ScheduleTargetKind;
  target_id: string;
  cron: string;
  timezone?: string;
  runner?: ScheduleRunner;
  github_repo_id?: string | null;
  persona_id?: string | null;
  org_id?: string | null;
  enabled?: boolean;
}
export interface ScheduleRunRow {
  id: string;
  schedule_id: string;
  started_at: string | null;
  finished_at: string | null;
  status: "queued" | "running" | "passed" | "failed" | "error" | "cancelled";
  runner: ScheduleRunner;
  github_workflow_run_id: string | null;
  github_workflow_run_url: string | null;
  local_run_id: string | null;
  result_summary: unknown;
  error_message: string | null;
}

export interface PersonaBulkImportResult {
  dry_run: boolean;
  created_count: number;
  preview: Array<{
    name: string;
    username: string;
    visibility: string;
    is_default: boolean;
    role_profile: string | null;
    default_app: string | null;
    credentials_pending: boolean;
  }>;
  skipped: Array<{ row_index: number; reason: string; row: Record<string, string> }>;
}

export interface GenerationJobSnapshot {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  mode: string;
  current_phase: string;
  event_seq: number;
  events: Array<{ seq: number; ts: string; event: string; payload: any }>;
  robot_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  cancellation_requested_at: string | null;
}

export interface HealEventRow {
  id: string;
  project_slug: string | null;
  run_id: string | null;
  generation_job_id: string | null;
  sobject: string;
  step_index: number | null;
  attempt_number: number;
  error_type: string;
  field_label: string | null;
  strategy: string;
  outcome: string;
  latency_ms: number;
  payload: any;
  created_at: string | null;
}
