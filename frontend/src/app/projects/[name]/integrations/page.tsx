"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  api,
  type ContextFileRow,
  type GitHubConnectionView,
  type GitHubRepoListing,
  type GitHubRepoRow,
  type JiraConnectionView,
  type JiraProjectRow,
  type JiraSyncStats,
  type JiraSyncedIssue,
  type JiraSyncedSprint,
  type ScheduleRow,
  type ScheduleRunRow,
  type ScheduleRunner,
  type ScheduleTargetKind,
  type TestDataTableRow,
} from "@/lib/api";

type TabKey = "jira" | "github" | "context" | "testdata" | "schedules";

const TABS: { key: TabKey; label: string; sub: string }[] = [
  { key: "jira", label: "Jira", sub: "Import sprints, stories, comments" },
  { key: "github", label: "GitHub", sub: "Sync scripts + run on Actions" },
  { key: "context", label: "Context files", sub: "Docs the AI can read" },
  { key: "testdata", label: "Test data", sub: "Reference tables AI picks rows from" },
  { key: "schedules", label: "Schedules", sub: "Cron triggers, local or remote" },
];

export default function IntegrationsPage() {
  const params = useParams();
  const slug = decodeURIComponent(params.name as string);
  const [tab, setTab] = useState<TabKey>("jira");

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="max-w-6xl mx-auto px-6 py-10">
        <div className="flex items-baseline justify-between mb-6">
          <div>
            <p className="text-xs uppercase tracking-widest text-slate-500">
              <Link href={`/projects/${encodeURIComponent(slug)}`} className="hover:text-slate-300">
                {slug}
              </Link>
              <span className="mx-2">/</span>integrations
            </p>
            <h1 className="text-3xl font-semibold mt-2">Integrations &amp; automation</h1>
            <p className="text-slate-400 mt-1">
              Wire this project into Jira, GitHub, and your own data so the AI sees real context.
            </p>
          </div>
        </div>

        <nav className="flex gap-2 border-b border-slate-800 mb-6 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-3 text-sm border-b-2 transition-colors whitespace-nowrap ${
                tab === t.key
                  ? "border-indigo-400 text-white"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              <div className="font-medium">{t.label}</div>
              <div className="text-xs text-slate-500">{t.sub}</div>
            </button>
          ))}
        </nav>

        <div className="space-y-6">
          {tab === "jira" && <JiraTab slug={slug} />}
          {tab === "github" && <GitHubTab slug={slug} />}
          {tab === "context" && <ContextFilesTab slug={slug} />}
          {tab === "testdata" && <TestDataTab slug={slug} />}
          {tab === "schedules" && <SchedulesTab slug={slug} />}
        </div>
      </div>
    </div>
  );
}

// ---- Jira -----------------------------------------------------------------

function JiraTab({ slug }: { slug: string }) {
  const [conn, setConn] = useState<JiraConnectionView | null>(null);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ base_url: "", email: "", api_token: "", default_jira_project_key: "" });
  const [projects, setProjects] = useState<JiraProjectRow[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [syncStats, setSyncStats] = useState<JiraSyncStats | null>(null);
  const [sprints, setSprints] = useState<JiraSyncedSprint[]>([]);
  const [issues, setIssues] = useState<JiraSyncedIssue[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Mirror-row selection (separate sets for sprints + issues so they
  // don't visually interfere with each other). Each set holds the
  // row's UUID `id` -- not the Jira `jira_id` -- so we can target a
  // single connection's row without ambiguity. The "Remove from
  // mirror" verb is deliberate: this does NOT delete in Atlassian.
  const [selectedSprintRowIds, setSelectedSprintRowIds] = useState<string[]>([]);
  const [selectedIssueRowIds, setSelectedIssueRowIds] = useState<string[]>([]);

  const refreshConnection = useCallback(async () => {
    setLoading(true);
    try {
      const c = await api.jira.getProjectConnection(slug);
      // The backend may fall back to org-scoped credentials when there is no
      // project-scoped Jira connection yet. For this page we only prefill when
      // a real project-level connection exists; otherwise keep fields empty so
      // users don't accidentally save inherited org credentials as project ones.
      const isProjectScoped = !!c && c.scope === "project" && c.project_slug === slug;
      if (isProjectScoped && c) {
        setConn(c);
        setForm((f) => ({
          ...f,
          base_url: c.base_url,
          email: c.email,
          default_jira_project_key: c.default_jira_project_key || "",
          api_token: "",
        }));
      } else {
        setConn(null);
        setForm({ base_url: "", email: "", api_token: "", default_jira_project_key: "" });
      }
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => { void refreshConnection(); }, [refreshConnection]);
  useEffect(() => {
    void api.jira.listSyncedSprints(slug).then(setSprints).catch(() => setSprints([]));
    void api.jira.listSyncedIssues(slug, { limit: 50 }).then(setIssues).catch(() => setIssues([]));
  }, [slug]);

  async function save() {
    setBusy("save"); setError(null);
    try {
      const c = await api.jira.upsertProjectConnection(slug, {
        base_url: form.base_url,
        email: form.email,
        api_token: form.api_token,
        default_jira_project_key: form.default_jira_project_key || undefined,
      });
      setConn(c);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function testConn() {
    setBusy("test"); setError(null);
    try {
      const r = await api.jira.test(slug);
      setError(`Connected as ${r.display_name ?? r.email ?? "(unknown)"}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function loadProjects() {
    setBusy("projects"); setError(null);
    try {
      const list = await api.jira.listProjects(slug);
      setProjects(list);
      if (!selectedKey && list[0]) setSelectedKey(list[0].key);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function disconnect() {
    setBusy("disconnect"); setError(null); setSyncStats(null);
    try {
      await api.jira.deleteProjectConnection(slug);
      setConn(null);
      setProjects([]);
      setSelectedKey("");
      setSprints([]);
      setIssues([]);
      setForm({ base_url: "", email: "", api_token: "", default_jira_project_key: "" });
      setError("Jira connection removed.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function syncNow() {
    if (!selectedKey) return;
    setBusy("sync"); setError(null); setSyncStats(null);
    try {
      const stats = await api.jira.sync(slug, selectedKey, true);
      setSyncStats(stats);
      void api.jira.listSyncedSprints(slug, selectedKey).then(setSprints).catch(() => {});
      void api.jira.listSyncedIssues(slug, { jiraProjectKey: selectedKey, limit: 50 }).then(setIssues).catch(() => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  // --- Mirror-row delete helpers -----------------------------------
  function toggleSprintRow(id: string) {
    setSelectedSprintRowIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }
  function toggleAllSprintRows() {
    setSelectedSprintRowIds((prev) =>
      prev.length === sprints.length ? [] : sprints.map((s) => s.id),
    );
  }
  function toggleIssueRow(id: string) {
    setSelectedIssueRowIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }
  function toggleAllIssueRows() {
    setSelectedIssueRowIds((prev) =>
      prev.length === issues.length ? [] : issues.map((i) => i.id),
    );
  }
  async function removeSprintsFromMirror(ids: string[]) {
    if (ids.length === 0) return;
    setBusy("remove-sprints"); setError(null);
    try {
      const res = await api.jira.bulkDeleteSyncedSprints(slug, ids);
      setError(`Removed ${res.deleted} sprint(s) from the local mirror. Next "Sync now" will re-pull any rows still present in Atlassian.`);
      setSelectedSprintRowIds([]);
      void api.jira.listSyncedSprints(slug, selectedKey || undefined).then(setSprints).catch(() => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  async function removeIssuesFromMirror(ids: string[]) {
    if (ids.length === 0) return;
    setBusy("remove-issues"); setError(null);
    try {
      const res = await api.jira.bulkDeleteSyncedIssues(slug, ids);
      setError(`Removed ${res.deleted} issue(s) from the local mirror. Next "Sync now" will re-pull any rows still present in Atlassian.`);
      setSelectedIssueRowIds([]);
      void api.jira.listSyncedIssues(slug, { jiraProjectKey: selectedKey || undefined, limit: 50 }).then(setIssues).catch(() => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <Section title="Connection" subtitle="Per-project Jira credentials. Stored encrypted; never shown after save.">
        {loading ? <div className="text-slate-500 text-sm">Loading...</div> : (
          <div className="grid sm:grid-cols-2 gap-4">
            <Field label="Base URL" placeholder="https://your-org.atlassian.net" value={form.base_url} onChange={(v) => setForm({ ...form, base_url: v })} />
            <Field label="Email" placeholder="qa@yourco.com" value={form.email} onChange={(v) => setForm({ ...form, email: v })} />
            <Field label="API Token" placeholder={conn?.has_token ? "•••••••• (stored)" : "Generate at id.atlassian.com"} type="password" value={form.api_token} onChange={(v) => setForm({ ...form, api_token: v })} />
            <Field label="Default project key (optional)" placeholder="QA" value={form.default_jira_project_key} onChange={(v) => setForm({ ...form, default_jira_project_key: v })} />
          </div>
        )}
        <div className="flex gap-2 mt-4">
          <Btn onClick={save} loading={busy === "save"}>Save connection</Btn>
          {conn && <Btn onClick={testConn} loading={busy === "test"} variant="ghost">Test</Btn>}
          {conn && <Btn onClick={loadProjects} loading={busy === "projects"} variant="ghost">List Jira projects</Btn>}
          {conn && <Btn onClick={disconnect} loading={busy === "disconnect"} variant="ghost">Disconnect Jira</Btn>}
        </div>
        {error && <p className="text-amber-300 text-sm mt-3">{error}</p>}
      </Section>

      {projects.length > 0 && (
        <Section title="Import a Jira project" subtitle="Pick a project key, then click Sync now to pull sprints, issues, comments.">
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="text-xs text-slate-500">Jira project</label>
              <select className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 mt-1" value={selectedKey} onChange={(e) => setSelectedKey(e.target.value)}>
                {projects.map((p) => <option key={p.key} value={p.key}>{p.key} — {p.name}</option>)}
              </select>
            </div>
            <Btn onClick={syncNow} loading={busy === "sync"} disabled={!selectedKey}>Sync now</Btn>
          </div>
          {syncStats && (
            <div className="mt-4 space-y-2">
              <div className="text-sm text-slate-300 grid grid-cols-4 gap-3">
                <Stat label="Sprints" value={syncStats.sprints_upserted} />
                <Stat label="Issues" value={syncStats.issues_upserted} />
                <Stat label="Comments" value={syncStats.comments_upserted} />
                <Stat label="Errors" value={syncStats.errors.length} />
              </div>
              {syncStats.errors.length > 0 && (
                /* The provider already swallows expected "this board has no
                   sprints" 400s before they reach here, so anything in this
                   list is genuinely interesting (auth fault on Agile API,
                   per-issue comment fetch failure, etc.). Cap at 5 lines so
                   one runaway sync doesn't drown the panel. */
                <details className="text-xs bg-slate-900/60 border border-slate-800 rounded p-3">
                  <summary className="cursor-pointer text-amber-300">
                    {syncStats.errors.length} non-fatal issue
                    {syncStats.errors.length === 1 ? "" : "s"} during sync
                  </summary>
                  <ul className="mt-2 space-y-1 list-disc pl-5 text-slate-400">
                    {syncStats.errors.slice(0, 5).map((e, i) => (
                      <li key={i} className="break-words">{e}</li>
                    ))}
                    {syncStats.errors.length > 5 && (
                      <li className="text-slate-500 italic">
                        ...and {syncStats.errors.length - 5} more (check backend logs)
                      </li>
                    )}
                  </ul>
                </details>
              )}
            </div>
          )}
        </Section>
      )}

      <Section
        title={`Imported sprints (${sprints.length})`}
        subtitle="Mirror of /rest/agile/1.0/board sprints. 'Remove from mirror' deletes the local row only -- Atlassian is untouched, and the next 'Sync now' will re-pull anything still on the board."
      >
        {sprints.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
            <label className="inline-flex items-center gap-2 text-slate-300">
              <input
                type="checkbox"
                checked={selectedSprintRowIds.length === sprints.length && sprints.length > 0}
                onChange={toggleAllSprintRows}
                className="accent-cyan-500"
              />
              Select all ({sprints.length})
            </label>
            <button
              type="button"
              disabled={selectedSprintRowIds.length === 0 || busy === "remove-sprints"}
              onClick={() => void removeSprintsFromMirror(selectedSprintRowIds)}
              className="px-3 py-1.5 rounded-lg border border-red-400/40 bg-red-500/10 text-red-200 hover:bg-red-500/20 disabled:opacity-40"
            >
              {busy === "remove-sprints"
                ? "Working…"
                : `Remove from mirror (${selectedSprintRowIds.length})`}
            </button>
          </div>
        )}
        <Table
          headers={["", "Name", "State", "Start", "End", "Portal sprint", ""]}
          rows={sprints.map((s) => [
            <input
              key={`cb-${s.id}`}
              type="checkbox"
              checked={selectedSprintRowIds.includes(s.id)}
              onChange={() => toggleSprintRow(s.id)}
              className="accent-cyan-500"
              aria-label={`Select ${s.name}`}
            />,
            s.name,
            s.state,
            s.start_date?.slice(0, 10) ?? "—",
            s.end_date?.slice(0, 10) ?? "—",
            s.portal_sprint_id ? <Link className="text-indigo-300 hover:underline" key={s.id} href={`/sprints/${s.portal_sprint_id}`}>open</Link> : <span className="text-slate-500">not imported</span>,
            <button
              key={`rm-${s.id}`}
              type="button"
              onClick={() => void removeSprintsFromMirror([s.id])}
              className="text-[11px] text-slate-400 hover:text-red-300"
              title="Remove this row from the local mirror"
            >
              Remove
            </button>,
          ])}
          empty="No synced sprints yet."
        />
      </Section>

      <Section
        title={`Imported issues (${issues.length})`}
        subtitle="Mirror of /rest/api/3/search/jql results. Same 'mirror only' semantics as sprints."
      >
        {issues.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
            <label className="inline-flex items-center gap-2 text-slate-300">
              <input
                type="checkbox"
                checked={selectedIssueRowIds.length === issues.length && issues.length > 0}
                onChange={toggleAllIssueRows}
                className="accent-cyan-500"
              />
              Select all ({issues.length})
            </label>
            <button
              type="button"
              disabled={selectedIssueRowIds.length === 0 || busy === "remove-issues"}
              onClick={() => void removeIssuesFromMirror(selectedIssueRowIds)}
              className="px-3 py-1.5 rounded-lg border border-red-400/40 bg-red-500/10 text-red-200 hover:bg-red-500/20 disabled:opacity-40"
            >
              {busy === "remove-issues"
                ? "Working…"
                : `Remove from mirror (${selectedIssueRowIds.length})`}
            </button>
          </div>
        )}
        <Table
          headers={["", "Key", "Type", "Status", "Summary", "Portal story", ""]}
          rows={issues.map((i) => [
            <input
              key={`cb-${i.id}`}
              type="checkbox"
              checked={selectedIssueRowIds.includes(i.id)}
              onChange={() => toggleIssueRow(i.id)}
              className="accent-cyan-500"
              aria-label={`Select ${i.jira_key}`}
            />,
            <span key={i.id} className="font-mono text-xs">{i.jira_key}</span>,
            i.issue_type ?? "—",
            i.status ?? "—",
            <span key={`s-${i.id}`} className="truncate block max-w-md">{i.summary}</span>,
            i.portal_story_id ? <Link className="text-indigo-300 hover:underline" key={`p-${i.id}`} href={`/user-stories/${i.portal_story_id}`}>open</Link> : <span className="text-slate-500">not imported</span>,
            <button
              key={`rm-${i.id}`}
              type="button"
              onClick={() => void removeIssuesFromMirror([i.id])}
              className="text-[11px] text-slate-400 hover:text-red-300"
              title="Remove this row from the local mirror"
            >
              Remove
            </button>,
          ])}
          empty="No synced issues yet."
        />
      </Section>
    </div>
  );
}

// ---- GitHub ---------------------------------------------------------------

function GitHubTab({ slug }: { slug: string }) {
  const [conn, setConn] = useState<GitHubConnectionView | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [pat, setPat] = useState({ owner_login: "", access_token: "" });
  const [repos, setRepos] = useState<GitHubRepoListing[]>([]);
  const [connected, setConnected] = useState<GitHubRepoRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setConn(await api.github.getProjectConnection(slug)); } catch { setConn(null); }
    try { setConnected(await api.github.listConnectedRepos(slug)); } catch { setConnected([]); }
  }, [slug]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function savePat() {
    setBusy("pat"); setError(null);
    try {
      const r = await api.github.upsertProjectPAT(slug, pat);
      setConn(r);
      setSecret(r.webhook_secret_plaintext);
      setPat({ owner_login: "", access_token: "" });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  }
  async function loadRepos() {
    setBusy("repos"); setError(null);
    try { setRepos(await api.github.listRepos(slug)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  async function connectRepo(r: GitHubRepoListing) {
    setBusy(`connect:${r.full_name}`); setError(null);
    try {
      await api.github.connectRepo(slug, { owner: r.owner, name: r.name, default_branch: r.default_branch });
      void refresh();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  async function pushScripts(id: string) {
    setBusy(`push:${id}`); setError(null);
    try {
      const r = await api.github.pushScripts(slug, id);
      setError(`Pushed ${r.committed} file(s).`);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  async function refreshWorkflow(id: string) {
    setBusy(`workflow:${id}`); setError(null);
    try {
      const r = await api.github.refreshWorkflow(slug, id);
      setError(`Workflow written to ${r.path}.`);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  return (
    <div className="space-y-6">
      <Section title="Project connection" subtitle="Use a PAT for this project, or rely on the org-wide GitHub App configured in Settings → Integrations.">
        {conn ? (
          <div className="text-sm text-slate-300">
            Connected as <span className="font-mono">{conn.owner_login || "(unknown)"}</span> via <span className="font-mono">{conn.auth_kind}</span>.{" "}
            {conn.has_webhook_secret && <span className="text-slate-500">Webhook secret is set.</span>}
          </div>
        ) : (
          <div className="grid sm:grid-cols-2 gap-4">
            <Field label="GitHub org/owner" placeholder="acme-inc" value={pat.owner_login} onChange={(v) => setPat({ ...pat, owner_login: v })} />
            <Field label="Personal access token" type="password" placeholder="ghp_..." value={pat.access_token} onChange={(v) => setPat({ ...pat, access_token: v })} />
          </div>
        )}
        <div className="flex gap-2 mt-4">
          {!conn && <Btn onClick={savePat} loading={busy === "pat"}>Save PAT</Btn>}
          {conn && <Btn onClick={loadRepos} loading={busy === "repos"} variant="ghost">Browse repos</Btn>}
        </div>
        {secret && (
          <div className="bg-amber-900/30 border border-amber-700 rounded p-3 mt-4 text-sm">
            <div className="font-semibold mb-1">Webhook secret (shown once)</div>
            <div className="font-mono break-all">{secret}</div>
            <p className="text-amber-300 mt-2">Paste this into your repo&apos;s webhook settings (or the GitHub App webhook).</p>
          </div>
        )}
        {error && <p className="text-amber-300 text-sm mt-3">{error}</p>}
      </Section>

      {repos.length > 0 && (
        <Section title="Available repos" subtitle="Click connect to allow the portal to push scripts and run workflows in this repo.">
          <Table headers={["Repo", "Default branch", ""]} rows={repos.map((r) => [
            <a key={r.id} href={r.html_url} target="_blank" rel="noreferrer" className="text-indigo-300 hover:underline font-mono">{r.full_name}</a>,
            r.default_branch,
            <Btn key={`c-${r.id}`} onClick={() => connectRepo(r)} loading={busy === `connect:${r.full_name}`} variant="ghost" size="sm">Connect</Btn>,
          ])} empty="No repos visible." />
        </Section>
      )}

      <Section title={`Connected repos (${connected.length})`}>
        <Table headers={["Repo", "Branch", "Suites root", "Workflow", "Actions"]} rows={connected.map((r) => [
          <span key={r.id} className="font-mono">{r.full_name}</span>,
          r.default_branch, r.suites_root_path, r.workflow_path,
          <div key={`a-${r.id}`} className="flex gap-2">
            <Btn onClick={() => pushScripts(r.id)} loading={busy === `push:${r.id}`} size="sm" variant="ghost">Push scripts</Btn>
            <Btn onClick={() => refreshWorkflow(r.id)} loading={busy === `workflow:${r.id}`} size="sm" variant="ghost">Refresh workflow</Btn>
          </div>,
        ])} empty="No repos connected yet." />
      </Section>
    </div>
  );
}

// ---- Context files --------------------------------------------------------

function ContextFilesTab({ slug }: { slug: string }) {
  const [files, setFiles] = useState<ContextFileRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setFiles(await api.contextFiles.list(slug)); } catch { setFiles([]); }
  }, [slug]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function upload(ev: React.ChangeEvent<HTMLInputElement>) {
    const file = ev.target.files?.[0];
    if (!file) return;
    setBusy("upload"); setError(null);
    try {
      await api.contextFiles.upload(slug, file);
      ev.target.value = "";
      void refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  }
  async function remove(id: string) {
    setBusy(`del:${id}`);
    try { await api.contextFiles.delete(slug, id); void refresh(); } finally { setBusy(null); }
  }

  return (
    <div className="space-y-6">
      <Section title="Upload context file" subtitle="CSV / XLSX / PDF / DOCX / MD / TXT. Up to 32 MB. The AI uses these when generating new tests.">
        <label className="block w-fit cursor-pointer border border-dashed border-slate-700 rounded px-6 py-8 text-center hover:border-indigo-500">
          <span className="text-indigo-300 font-medium">Choose file</span>
          <span className="text-slate-500 ml-2">or drop here</span>
          <input type="file" className="hidden" onChange={upload} disabled={busy === "upload"} />
        </label>
        {busy === "upload" && <p className="text-slate-400 text-sm mt-2">Uploading and parsing...</p>}
        {error && <p className="text-amber-300 text-sm mt-3">{error}</p>}
      </Section>

      <Section title={`Files (${files.length})`}>
        <Table headers={["File", "Kind", "Rows", "Chunks", "Uploaded", ""]} rows={files.map((f) => [
          <span key={f.id} className="font-mono break-all">{f.filename}</span>,
          f.kind, f.row_count, f.chunk_count,
          f.uploaded_at?.replace("T", " ").slice(0, 16) ?? "—",
          <Btn key={`d-${f.id}`} onClick={() => remove(f.id)} loading={busy === `del:${f.id}`} variant="ghost" size="sm">Delete</Btn>,
        ])} empty="Upload a doc or CSV to start grounding the AI in your project's context." />
      </Section>
    </div>
  );
}

// ---- Test data tables -----------------------------------------------------

function TestDataTab({ slug }: { slug: string }) {
  const [tables, setTables] = useState<TestDataTableRow[]>([]);
  const [form, setForm] = useState({ name: "", description: "", kind: "generic" });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setTables(await api.testData.list(slug)); } catch { setTables([]); }
  }, [slug]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function create() {
    if (!file || !form.name) {
      setError("Pick a file and give the table a name."); return;
    }
    setBusy("create"); setError(null);
    try {
      await api.testData.create(slug, { ...form, file });
      setForm({ name: "", description: "", kind: "generic" });
      setFile(null);
      void refresh();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  return (
    <div className="space-y-6">
      <Section title="Reference tables" subtitle="Upload CSV/XLSX rows the AI can address by name. Need a starting point? Download a sample:">
        <div className="flex gap-2 flex-wrap mb-4">
          {(["user_types", "accounts", "opportunities", "leads"] as const).map((k) => (
            <a key={k} href={api.testData.sampleCsvUrl(slug, k)} className="px-3 py-2 text-xs rounded border border-slate-700 hover:border-indigo-500 hover:text-white text-slate-300">
              {k.replace("_", " ")} sample
            </a>
          ))}
        </div>
        <div className="grid sm:grid-cols-3 gap-3">
          <Field label="Table name" placeholder="User Types" value={form.name} onChange={(v) => setForm({ ...form, name: v })} />
          <Field label="Kind" placeholder="generic" value={form.kind} onChange={(v) => setForm({ ...form, kind: v })} />
          <Field label="Description" placeholder="optional" value={form.description} onChange={(v) => setForm({ ...form, description: v })} />
        </div>
        <input type="file" accept=".csv,.xlsx,.xls" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="mt-4 text-sm text-slate-300" />
        <div className="mt-3"><Btn onClick={create} loading={busy === "create"}>Create table</Btn></div>
        {error && <p className="text-amber-300 text-sm mt-3">{error}</p>}
      </Section>

      <Section title={`Tables (${tables.length})`}>
        <Table headers={["Name", "Kind", "Columns", "Description"]} rows={tables.map((t) => [
          t.name, t.kind,
          <span key={`c-${t.id}`} className="text-xs text-slate-400">{(t.columns || []).join(", ")}</span>,
          t.description ?? "—",
        ])} empty="No tables yet." />
      </Section>
    </div>
  );
}

// ---- Schedules ------------------------------------------------------------

function SchedulesTab({ slug }: { slug: string }) {
  const [rows, setRows] = useState<ScheduleRow[]>([]);
  const [runs, setRuns] = useState<Record<string, ScheduleRunRow[]>>({});
  const [form, setForm] = useState<{
    name: string; target_kind: ScheduleTargetKind; target_id: string;
    cron: string; runner: ScheduleRunner; github_repo_id: string;
    persona_id: string; org_id: string;
  }>({
    name: "", target_kind: "test_case", target_id: "", cron: "0 9 * * *",
    runner: "local", github_repo_id: "", persona_id: "", org_id: "",
  });
  const [repos, setRepos] = useState<GitHubRepoRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setRows(await api.schedules.list(slug)); } catch { setRows([]); }
    try { setRepos(await api.github.listConnectedRepos(slug)); } catch { setRepos([]); }
  }, [slug]);
  useEffect(() => { void refresh(); }, [refresh]);

  async function create() {
    if (!form.name || !form.target_id) { setError("Need a name and a target id."); return; }
    if (form.runner === "github_actions" && !form.github_repo_id) {
      setError("github_actions schedules need a connected repo."); return;
    }
    setBusy("create"); setError(null);
    try {
      await api.schedules.create(slug, {
        name: form.name,
        target_kind: form.target_kind,
        target_id: form.target_id,
        cron: form.cron,
        runner: form.runner,
        github_repo_id: form.github_repo_id || null,
        persona_id: form.persona_id || null,
        org_id: form.org_id || null,
      });
      setForm({ ...form, name: "", target_id: "" });
      void refresh();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  async function toggle(s: ScheduleRow) {
    await api.schedules.update(slug, s.id, { enabled: !s.enabled });
    void refresh();
  }
  async function runNow(s: ScheduleRow) {
    setBusy(`run:${s.id}`);
    try { await api.schedules.runNow(slug, s.id); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); void loadHistory(s.id); }
  }
  async function loadHistory(id: string) {
    try {
      const history = (await api.schedules.history(slug, id, 10)) ?? [];
      setRuns((r) => ({ ...r, [id]: history }));
    } catch {
      // noop
    }
  }
  async function remove(id: string) {
    setBusy(`del:${id}`);
    try { await api.schedules.delete(slug, id); void refresh(); } finally { setBusy(null); }
  }

  return (
    <div className="space-y-6">
      <Section title="Create a schedule">
        <div className="grid md:grid-cols-3 gap-3">
          <Field label="Name" placeholder="Nightly smoke" value={form.name} onChange={(v) => setForm({ ...form, name: v })} />
          <div>
            <label className="text-xs text-slate-500">Target kind</label>
            <select className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 mt-1" value={form.target_kind} onChange={(e) => setForm({ ...form, target_kind: e.target.value as ScheduleTargetKind })}>
              <option value="test_case">Test case</option>
              <option value="story">User story</option>
              <option value="sprint">Sprint</option>
              <option value="tag">Tag</option>
            </select>
          </div>
          <Field label="Target id" placeholder={form.target_kind === "tag" ? "smoke" : "uuid..."} value={form.target_id} onChange={(v) => setForm({ ...form, target_id: v })} />
          <Field label="Cron (UTC)" placeholder="0 9 * * *" value={form.cron} onChange={(v) => setForm({ ...form, cron: v })} />
          <div>
            <label className="text-xs text-slate-500">Runner</label>
            <select className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 mt-1" value={form.runner} onChange={(e) => setForm({ ...form, runner: e.target.value as ScheduleRunner })}>
              <option value="local">Local (APScheduler)</option>
              <option value="github_actions">GitHub Actions</option>
            </select>
          </div>
          {form.runner === "github_actions" && (
            <div>
              <label className="text-xs text-slate-500">Repo</label>
              <select className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 mt-1" value={form.github_repo_id} onChange={(e) => setForm({ ...form, github_repo_id: e.target.value })}>
                <option value="">(pick one)</option>
                {repos.map((r) => <option key={r.id} value={r.id}>{r.full_name}</option>)}
              </select>
            </div>
          )}
          <Field label="Persona id (optional)" placeholder="uuid..." value={form.persona_id} onChange={(v) => setForm({ ...form, persona_id: v })} />
          <Field label="Org id (optional)" placeholder="uuid..." value={form.org_id} onChange={(v) => setForm({ ...form, org_id: v })} />
        </div>
        <div className="mt-4"><Btn onClick={create} loading={busy === "create"}>Create schedule</Btn></div>
        {error && <p className="text-amber-300 text-sm mt-3">{error}</p>}
      </Section>

      <Section title={`Schedules (${rows.length})`}>
        <div className="space-y-3">
          {rows.length === 0 && <p className="text-slate-500 text-sm">No schedules yet.</p>}
          {rows.map((s) => (
            <div key={s.id} className="border border-slate-800 rounded p-4">
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-semibold">
                    {s.name} <span className={`ml-2 text-xs px-2 py-0.5 rounded ${s.runner === "github_actions" ? "bg-purple-900/40 text-purple-300" : "bg-slate-700 text-slate-300"}`}>{s.runner}</span>
                    {!s.enabled && <span className="ml-2 text-xs text-amber-300">disabled</span>}
                  </div>
                  <div className="text-xs text-slate-400 mt-1 font-mono">
                    {s.target_kind}={s.target_id.slice(0, 12)}… &middot; cron <span className="text-slate-300">{s.cron}</span> &middot; tz {s.timezone}
                  </div>
                  {s.next_run_at && <div className="text-xs text-slate-500 mt-1">Next: {s.next_run_at}</div>}
                </div>
                <div className="flex gap-2">
                  <Btn onClick={() => runNow(s)} loading={busy === `run:${s.id}`} size="sm">Run now</Btn>
                  <Btn onClick={() => toggle(s)} variant="ghost" size="sm">{s.enabled ? "Pause" : "Resume"}</Btn>
                  <Btn onClick={() => loadHistory(s.id)} variant="ghost" size="sm">History</Btn>
                  <Btn onClick={() => remove(s.id)} loading={busy === `del:${s.id}`} variant="ghost" size="sm">Delete</Btn>
                </div>
              </div>
              {runs[s.id]?.length ? (
                <div className="mt-3 border-t border-slate-800 pt-3">
                  <Table headers={["Started", "Status", "Runner", "Link"]} rows={runs[s.id].map((r) => [
                    r.started_at?.replace("T", " ").slice(0, 16) ?? "—",
                    <span key={`s-${r.id}`} className={r.status === "passed" ? "text-emerald-300" : r.status === "failed" ? "text-rose-300" : "text-slate-300"}>{r.status}</span>,
                    r.runner,
                    r.github_workflow_run_url ? <a key={`l-${r.id}`} href={r.github_workflow_run_url} target="_blank" rel="noreferrer" className="text-indigo-300 hover:underline">workflow run</a> : r.local_run_id ? <span key={`l-${r.id}`} className="font-mono text-xs">{r.local_run_id.slice(0, 8)}…</span> : "—",
                  ])} empty="" />
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}

// ---- Shared UI ------------------------------------------------------------

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="bg-slate-900/50 border border-slate-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold">{title}</h2>
      {subtitle && <p className="text-sm text-slate-400 mt-1 mb-4">{subtitle}</p>}
      <div className={subtitle ? "" : "mt-2"}>{children}</div>
    </section>
  );
}

function Field({ label, value, onChange, placeholder, type = "text" }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string }) {
  return (
    <label className="block">
      <span className="text-xs text-slate-500">{label}</span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm focus:outline-none focus:border-indigo-500"
      />
    </label>
  );
}

function Btn({ children, onClick, loading, disabled, variant = "primary", size = "md" }: {
  children: React.ReactNode; onClick?: () => void; loading?: boolean; disabled?: boolean;
  variant?: "primary" | "ghost"; size?: "sm" | "md";
}) {
  const base = "rounded font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const sizing = size === "sm" ? "px-2.5 py-1 text-xs" : "px-4 py-2 text-sm";
  const palette = variant === "primary"
    ? "bg-indigo-500 hover:bg-indigo-400 text-white"
    : "border border-slate-700 hover:border-indigo-500 text-slate-200 hover:text-white";
  return (
    <button onClick={onClick} disabled={disabled || loading} className={`${base} ${sizing} ${palette}`}>
      {loading ? "…" : children}
    </button>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-slate-900/60 border border-slate-800 rounded p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-2xl font-semibold">{value}</div>
    </div>
  );
}

function Table({ headers, rows, empty }: { headers: string[]; rows: React.ReactNode[][]; empty?: string }) {
  if (rows.length === 0) return <p className="text-slate-500 text-sm">{empty ?? "Nothing here yet."}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-slate-500 border-b border-slate-800">
            {headers.map((h) => <th key={h} className="py-2 pr-4 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-slate-900 hover:bg-slate-900/40">
              {row.map((cell, j) => <td key={j} className="py-2 pr-4 align-top">{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
