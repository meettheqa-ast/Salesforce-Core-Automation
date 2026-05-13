"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, type JiraConnectionView } from "@/lib/api";

/** Admin-only org-wide integration settings.
 *
 *  Jira: a single org-wide connection acts as the default for any project
 *  that hasn't configured its own. GitHub App: shows the install URL and a
 *  field to finalise the installation id once the App is installed.
 */
export default function OrgIntegrationsPage() {
  const [me, setMe] = useState<{ is_admin: boolean } | null>(null);
  useEffect(() => { void api.me().then((m) => setMe({ is_admin: m.is_admin })).catch(() => setMe({ is_admin: false })); }, []);

  if (me === null) return <div className="p-10 text-slate-400">Loading...</div>;
  if (!me.is_admin) {
    return (
      <div className="max-w-2xl mx-auto px-6 py-16 text-center">
        <h1 className="text-2xl font-semibold mb-2">Admin only</h1>
        <p className="text-slate-400 mb-6">Org-wide integrations are managed by admins. Ask a portal admin to wire Jira / GitHub for everyone, or configure them per-project from inside a project page.</p>
        <Link href="/" className="text-indigo-300 hover:underline">Back to dashboard</Link>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="max-w-3xl mx-auto px-6 py-10">
        <h1 className="text-3xl font-semibold">Org integrations</h1>
        <p className="text-slate-400 mt-1">Defaults inherited by every project. Project-level overrides live in each project&apos;s Integrations tab.</p>

        <div className="mt-8 space-y-6">
          <JiraSection />
          <GitHubSection />
        </div>
      </div>
    </div>
  );
}

function JiraSection() {
  const [conn, setConn] = useState<JiraConnectionView | null>(null);
  const [form, setForm] = useState({ base_url: "", email: "", api_token: "", default_jira_project_key: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const c = await api.jira.getOrgConnection();
      setConn(c);
      if (c) setForm((f) => ({ ...f, base_url: c.base_url, email: c.email, default_jira_project_key: c.default_jira_project_key || "" }));
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function save() {
    setBusy("save"); setMsg(null);
    try {
      const c = await api.jira.upsertOrgConnection({
        base_url: form.base_url,
        email: form.email,
        api_token: form.api_token,
        default_jira_project_key: form.default_jira_project_key || undefined,
      });
      setConn(c);
      setMsg("Saved.");
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  async function remove() {
    setBusy("del"); setMsg(null);
    try { await api.jira.deleteOrgConnection(); setConn(null); setMsg("Removed."); }
    catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  return (
    <section className="bg-slate-900/50 border border-slate-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold">Jira</h2>
      <p className="text-sm text-slate-400 mt-1">Atlassian Cloud REST API. Generate a token at <a className="text-indigo-300 hover:underline" href="https://id.atlassian.com/manage-profile/security/api-tokens" target="_blank" rel="noreferrer">id.atlassian.com</a>.</p>
      <div className="grid sm:grid-cols-2 gap-4 mt-4">
        <Field label="Base URL" placeholder="https://your-org.atlassian.net" value={form.base_url} onChange={(v) => setForm({ ...form, base_url: v })} />
        <Field label="Email" placeholder="qa@yourco.com" value={form.email} onChange={(v) => setForm({ ...form, email: v })} />
        <Field label="API Token" placeholder={conn?.has_token ? "•••••••• (stored)" : ""} type="password" value={form.api_token} onChange={(v) => setForm({ ...form, api_token: v })} />
        <Field label="Default project key" placeholder="QA" value={form.default_jira_project_key} onChange={(v) => setForm({ ...form, default_jira_project_key: v })} />
      </div>
      <div className="flex gap-2 mt-4">
        <Btn onClick={save} loading={busy === "save"}>Save</Btn>
        {conn && <Btn onClick={remove} loading={busy === "del"} variant="ghost">Remove</Btn>}
      </div>
      {msg && <p className="text-amber-300 text-sm mt-3">{msg}</p>}
    </section>
  );
}

function GitHubSection() {
  const [installUrl, setInstallUrl] = useState<string>("");
  const [appId, setAppId] = useState<string>("");
  const [installationId, setInstallationId] = useState<string>("");
  const [ownerLogin, setOwnerLogin] = useState<string>("");
  const [secret, setSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    void api.github.getInstallUrl().then((r) => { setInstallUrl(r.install_url || ""); setAppId(r.app_id || ""); }).catch(() => {});
  }, []);

  async function finalize() {
    if (!installationId) { setMsg("Need an installation id."); return; }
    setBusy("save"); setMsg(null);
    try {
      const r = await api.github.createAppConnection({ installation_id: installationId, owner_login: ownerLogin });
      setSecret(r.webhook_secret_plaintext);
      setMsg("Connection saved.");
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  return (
    <section className="bg-slate-900/50 border border-slate-800 rounded-lg p-6">
      <h2 className="text-lg font-semibold">GitHub App</h2>
      <p className="text-sm text-slate-400 mt-1">
        Configure <span className="font-mono">GITHUB_APP_ID</span> and <span className="font-mono">GITHUB_APP_PRIVATE_KEY</span> in the backend env, then install the App on the org you want to manage.
      </p>
      <div className="mt-4 grid sm:grid-cols-2 gap-3 text-sm text-slate-300">
        <div><span className="text-slate-500">App ID:</span> <span className="font-mono">{appId || "(not configured)"}</span></div>
        <div>
          {installUrl ? <a className="text-indigo-300 hover:underline" href={installUrl} target="_blank" rel="noreferrer">Install GitHub App →</a> : <span className="text-slate-500">No install URL configured.</span>}
        </div>
      </div>
      <div className="grid sm:grid-cols-2 gap-4 mt-4">
        <Field label="Installation ID" placeholder="12345678" value={installationId} onChange={setInstallationId} />
        <Field label="Owner login (optional)" placeholder="acme-inc" value={ownerLogin} onChange={setOwnerLogin} />
      </div>
      <div className="mt-4"><Btn onClick={finalize} loading={busy === "save"} disabled={!appId}>Save installation</Btn></div>
      {secret && (
        <div className="bg-amber-900/30 border border-amber-700 rounded p-3 mt-4 text-sm">
          <div className="font-semibold mb-1">Webhook secret (shown once)</div>
          <div className="font-mono break-all">{secret}</div>
        </div>
      )}
      {msg && <p className="text-amber-300 text-sm mt-3">{msg}</p>}
    </section>
  );
}

function Field({ label, value, onChange, placeholder, type = "text" }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string }) {
  return (
    <label className="block">
      <span className="text-xs text-slate-500">{label}</span>
      <input type={type} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm focus:outline-none focus:border-indigo-500"
      />
    </label>
  );
}

function Btn({ children, onClick, loading, disabled, variant = "primary" }: { children: React.ReactNode; onClick?: () => void; loading?: boolean; disabled?: boolean; variant?: "primary" | "ghost"; }) {
  const palette = variant === "primary"
    ? "bg-indigo-500 hover:bg-indigo-400 text-white"
    : "border border-slate-700 hover:border-indigo-500 text-slate-200";
  return (
    <button onClick={onClick} disabled={disabled || loading} className={`rounded px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50 ${palette}`}>
      {loading ? "…" : children}
    </button>
  );
}
