const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...options?.headers },
      ...options,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "Network error";
    throw new Error(
      `${msg}. Is the API running at ${API_BASE}? (Set NEXT_PUBLIC_API_URL if needed.)`
    );
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

export const api = {
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
    stepwiseStreamUrl: () => `${API_BASE}/api/generate/mcp-stepwise/stream`,
  },
  runs: {
    execute: (data: any) => apiFetch<any>("/api/runs/execute", { method: "POST", body: JSON.stringify(data) }),
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
      return `${API_BASE}/api/runs/execute/stream?${q.toString()}`;
    },
    latest: (limit = 50) =>
      apiFetch<{ runs: Array<RunHistoryRow> }>(`/api/runs/latest?limit=${limit}`),
    summary: (runFolder: string) =>
      apiFetch<RunSummary>(`/api/runs/${encodeURIComponent(runFolder)}/summary`),
    fileUrl: (runFolder: string, filename: string, opts?: { download?: boolean }) =>
      `${API_BASE}/api/runs/${encodeURIComponent(runFolder)}/file/${encodeURIComponent(filename)}${
        opts?.download ? "?download=1" : ""
      }`,
    bundleUrl: (runFolder: string) =>
      `${API_BASE}/api/runs/${encodeURIComponent(runFolder)}/bundle.zip`,
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
