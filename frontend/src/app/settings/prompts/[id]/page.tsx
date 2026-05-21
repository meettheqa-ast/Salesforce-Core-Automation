"use client";

/**
 * AI Prompt Template editor.
 *
 * Tabs:
 *   * Edit     -- Monaco markdown editor with placeholder reference rail
 *   * Preview  -- POST /preview against a small mock context, no LLM call
 *   * Versions -- table of immutable history with "view body" + "activate"
 *
 * Unsaved-changes guard via `beforeunload` so a misclicked tab change /
 * back-button doesn't quietly drop the user's draft.
 *
 * Save behaviour: every save appends a NEW version (never mutates).
 * After save, the editor refreshes the detail view so the new version
 * appears in the Versions tab and becomes selectable as the active
 * override.
 */

import { use, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  api,
  type PromptCategory,
  type PromptDryRunResponse,
  type PromptPreviewResponse,
  type PromptTemplateDetail,
  type PromptVersionBody,
} from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import PromptDiff from "@/components/prompts/PromptDiff";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
});

type Tab = "edit" | "preview" | "versions";

interface Me {
  id: string;
  is_admin?: boolean;
}

export default function PromptEditorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const [me, setMe] = useState<Me | null>(null);
  const [detail, setDetail] = useState<PromptTemplateDetail | null>(null);
  const [category, setCategory] = useState<PromptCategory | null>(null);
  const [latestBody, setLatestBody] = useState<string>("");
  const [draft, setDraft] = useState<string>("");
  const [changeNote, setChangeNote] = useState<string>("");
  const [tab, setTab] = useState<Tab>("edit");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Preview state
  const [previewCtx, setPreviewCtx] = useState<string>(
    JSON.stringify(
      {
        story: { title: "Sample story", description: "User can log in." },
        project: { name: "Demo", slug: "demo" },
        qa_mode: "salesforce",
      },
      null,
      2,
    ),
  );
  const [preview, setPreview] = useState<PromptPreviewResponse | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [dryRun, setDryRun] = useState<PromptDryRunResponse | null>(null);
  const [dryRunErr, setDryRunErr] = useState<string | null>(null);
  const [dryRunUserMsg, setDryRunUserMsg] = useState<string>(
    "Generate test cases for: a customer signs in, adds an item to cart, and checks out.",
  );

  // Versions tab state
  const [openVersionBody, setOpenVersionBody] = useState<PromptVersionBody | null>(null);
  // Diff viewer state (Phase 5). When `diffSource` is set, the editor
  // tab renders a Monaco DiffEditor underneath the editor itself,
  // comparing the draft to either the system default OR a chosen
  // earlier version of the same template.
  const [diffSource, setDiffSource] = useState<string | null>(null);
  const [diffCaption, setDiffCaption] = useState<string>("");

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const [d, cats, who] = await Promise.all([
        api.prompts.get(id),
        api.prompts.categories(),
        api.me().catch(() => null),
      ]);
      setDetail(d);
      const cat = cats.find((c) => c.category === d.category) || null;
      setCategory(cat);
      setMe(who as Me | null);
      const latest = d.versions[0];
      if (latest) {
        const body = await api.prompts.getVersion(d.id, latest.version_number);
        setLatestBody(body.body);
        setDraft(body.body);
      } else {
        setLatestBody("");
        setDraft("");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not load template");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [id]);

  const dirty = draft !== latestBody;

  // Beforeunload guard. The user shouldn't lose 5 minutes of editing
  // by hitting back accidentally; this nudges them.
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const tokenEstimate = useMemo(() => Math.ceil(draft.length / 4), [draft]);
  const tooBig = draft.length > 200 * 1024;

  const isSystem = detail?.is_system ?? false;
  const isMine = !!(detail && me && detail.owner_user_id === me.id);
  const canEdit = detail && !isSystem && (isMine || me?.is_admin);

  async function handleSave() {
    if (!detail) return;
    if (!dirty) {
      setErr("No changes to save.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.prompts.appendVersion(detail.id, {
        body: draft,
        change_note: changeNote || undefined,
      });
      setChangeNote("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function handlePreview() {
    if (!detail) return;
    setBusy(true);
    setPreviewErr(null);
    try {
      let ctx: Record<string, unknown> = {};
      try {
        ctx = JSON.parse(previewCtx || "{}");
      } catch {
        throw new Error("Preview context must be valid JSON.");
      }
      const r = await api.prompts.preview(detail.id, { context: ctx, strict: false });
      setPreview(r);
    } catch (e) {
      setPreviewErr(e instanceof Error ? e.message : "Preview failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleDryRun() {
    if (!detail) return;
    setBusy(true);
    setDryRunErr(null);
    try {
      let ctx: Record<string, unknown> = {};
      try {
        ctx = JSON.parse(previewCtx || "{}");
      } catch {
        throw new Error("Context must be valid JSON.");
      }
      const r = await api.prompts.dryRun(detail.id, {
        context: ctx,
        user_message: dryRunUserMsg,
        qa_mode: (ctx.qa_mode as string) || "salesforce",
      });
      setDryRun(r);
    } catch (e) {
      setDryRunErr(e instanceof Error ? e.message : "Dry-run failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleActivateVersion(versionNumber: number) {
    if (!detail) return;
    setBusy(true);
    setErr(null);
    try {
      await api.prompts.activate(detail.id, {
        version_number: versionNumber,
        scope: "user",
        scope_id: me?.id ?? null,
      });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Activate failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleOpenVersion(versionNumber: number) {
    if (!detail) return;
    try {
      const body = await api.prompts.getVersion(detail.id, versionNumber);
      setOpenVersionBody(body);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not load version");
    }
  }

  if (loading || !detail) {
    return (
      <PageScaffold>
        <p className="text-sm text-slate-400">Loading…</p>
      </PageScaffold>
    );
  }

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Settings -- AI Prompts"
          title={detail.name}
          description={
            detail.description ||
            `Category: ${detail.category}. Output format: ${detail.output_format}.`
          }
        />
        <p className="text-xs text-slate-500 mt-2">
          <Link href="/settings/prompts" className="hover:text-slate-300">← Back to prompts list</Link>
        </p>
      </motion.div>

      {err && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
          {err}
        </div>
      )}

      {/* Status banner */}
      <div className="flex flex-wrap gap-2 text-xs">
        {isSystem && (
          <span className="px-2 py-1 rounded-full bg-cyan-600/30 text-cyan-100">
            System template (read-only -- clone to edit)
          </span>
        )}
        {!isSystem && isMine && (
          <span className="px-2 py-1 rounded-full bg-emerald-600/30 text-emerald-100">
            Owned by you
          </span>
        )}
        {dirty && (
          <span className="px-2 py-1 rounded-full bg-amber-600/30 text-amber-100">
            Unsaved changes
          </span>
        )}
        <span className="px-2 py-1 rounded-full bg-slate-800 text-slate-300">
          {detail.output_format}
        </span>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-white/10">
        {(["edit", "preview", "versions"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm border-b-2 -mb-px ${
              tab === t
                ? "border-purple-500 text-white"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {t === "edit" ? "Edit" : t === "preview" ? "Preview" : "Versions"}
          </button>
        ))}
      </div>

      {tab === "edit" && (
        <div className="grid lg:grid-cols-[1fr_280px] gap-4">
          <AnimatedCard glow="purple" className="p-2">
            <MonacoEditor
              height="640px"
              language="markdown"
              theme="vs-dark"
              value={draft}
              onChange={(v) => setDraft(v || "")}
              options={{
                readOnly: !canEdit,
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                padding: { top: 12 },
              }}
            />
          </AnimatedCard>

          {/* Right rail: variable reference + save controls */}
          <div className="space-y-3">
            <AnimatedCard glow="purple" className="p-3">
              <h4 className="text-xs uppercase tracking-wider text-cyan-300 mb-2">
                Available placeholders
              </h4>
              {category && category.placeholders.length > 0 ? (
                <ul className="space-y-1">
                  {category.placeholders.map((p) => (
                    <li key={p} className="text-xs text-slate-300">
                      <code className="px-1 py-0.5 rounded bg-slate-900 text-purple-200">
                        {"{{ "}{p}{" }}"}
                      </code>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-slate-500">No declared placeholders for this category.</p>
              )}
              <p className="text-[11px] text-slate-500 mt-2">
                Using an undeclared name will fail validation on save.
              </p>
            </AnimatedCard>

            <AnimatedCard glow="purple" className="p-3 space-y-2">
              <h4 className="text-xs uppercase tracking-wider text-cyan-300">Save</h4>
              <p className="text-[11px] text-slate-400">
                {draft.length.toLocaleString()} chars · ~{tokenEstimate.toLocaleString()} tokens
                {tooBig && (
                  <span className="block text-red-300 mt-1">
                    Above the 200 KB cap. Trim before saving.
                  </span>
                )}
              </p>
              <textarea
                value={changeNote}
                onChange={(e) => setChangeNote(e.target.value)}
                placeholder="What changed in this version?"
                rows={2}
                className="w-full bg-white/5 border border-white/10 rounded px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-purple-500"
              />
              <button
                type="button"
                disabled={!canEdit || !dirty || busy || tooBig}
                onClick={handleSave}
                className="w-full py-2 rounded-lg bg-purple-500/20 text-purple-200 border border-purple-500/30 text-sm font-semibold hover:bg-purple-500/30 disabled:opacity-40"
              >
                {busy ? "Saving…" : "Save as new version"}
              </button>
              {!canEdit && (
                <p className="text-[11px] text-slate-500">
                  This template is read-only for you. Go back and use{" "}
                  <span className="text-purple-300">Clone &amp; edit</span> to make a
                  personal copy.
                </p>
              )}
              <button
                type="button"
                disabled={!dirty || busy}
                onClick={() => setDraft(latestBody)}
                className="w-full py-2 rounded-lg border border-white/10 text-slate-300 text-xs hover:bg-white/5 disabled:opacity-40"
              >
                Revert to last saved
              </button>
              <button
                type="button"
                onClick={() => {
                  setDiffSource(latestBody);
                  setDiffCaption(
                    `Last saved (v${detail.versions[0]?.version_number ?? 1}) ⟷ your draft`,
                  );
                }}
                className="w-full py-2 rounded-lg border border-cyan-500/30 text-cyan-200 text-xs hover:bg-cyan-500/10"
              >
                Diff vs last saved
              </button>
              {detail.source_template_id && (
                <button
                  type="button"
                  onClick={async () => {
                    if (!detail.source_template_id) return;
                    setBusy(true);
                    try {
                      const src = await api.prompts.get(detail.source_template_id);
                      const srcLatest = src.versions[0];
                      const body = srcLatest
                        ? await api.prompts.getVersion(src.id, srcLatest.version_number)
                        : { body: "" };
                      setDiffSource(body.body);
                      setDiffCaption(`System default (${src.name}) ⟷ your draft`);
                    } catch (e) {
                      setErr(e instanceof Error ? e.message : "Could not load source for diff");
                    } finally {
                      setBusy(false);
                    }
                  }}
                  className="w-full py-2 rounded-lg border border-cyan-500/30 text-cyan-200 text-xs hover:bg-cyan-500/10"
                >
                  Diff vs system default
                </button>
              )}
            </AnimatedCard>
          </div>

          {diffSource !== null && (
            <div className="lg:col-span-2">
              <PromptDiff
                original={diffSource}
                modified={draft}
                caption={diffCaption}
                onClose={() => setDiffSource(null)}
              />
            </div>
          )}
        </div>
      )}

      {tab === "preview" && (
        <div className="grid lg:grid-cols-2 gap-4">
          <AnimatedCard glow="cyan" className="p-3 space-y-2">
            <h4 className="text-xs uppercase tracking-wider text-cyan-300">
              Mock context (JSON)
            </h4>
            <p className="text-[11px] text-slate-500">
              Used only for /preview render. No LLM call is made. Undefined
              placeholders render as empty string (lenient mode).
            </p>
            <textarea
              value={previewCtx}
              onChange={(e) => setPreviewCtx(e.target.value)}
              rows={16}
              className="w-full bg-slate-950 border border-white/10 rounded p-2 font-mono text-xs text-slate-200 outline-none focus:border-cyan-500"
            />
            <button
              type="button"
              disabled={busy}
              onClick={handlePreview}
              className="w-full py-2 rounded-lg bg-cyan-500/20 text-cyan-200 border border-cyan-500/30 text-sm font-semibold hover:bg-cyan-500/30 disabled:opacity-40"
            >
              {busy ? "Rendering…" : "Render preview"}
            </button>
            {previewErr && (
              <p className="text-xs text-red-300">{previewErr}</p>
            )}
          </AnimatedCard>

          <AnimatedCard glow="cyan" className="p-3">
            <h4 className="text-xs uppercase tracking-wider text-cyan-300 mb-2">
              Rendered prompt
            </h4>
            {preview ? (
              <>
                <p className="text-[11px] text-slate-500 mb-1">
                  {preview.bytes.toLocaleString()} bytes ·
                  placeholders used: {preview.placeholders_used.join(", ") || "(none)"}
                </p>
                <pre className="bg-slate-950 border border-white/10 rounded p-3 text-xs text-slate-200 whitespace-pre-wrap max-h-[480px] overflow-auto">
                  {preview.text}
                </pre>
              </>
            ) : (
              <p className="text-xs text-slate-500">Click Render preview to see the compiled output.</p>
            )}
          </AnimatedCard>

          {/* Full dry-run: actually call the LLM and parse the
              response so the user can confirm a markdown_table
              template still produces parseable rows. Spans both
              columns so the result table has room to breathe. */}
          <div className="lg:col-span-2">
            <AnimatedCard glow="purple" className="p-3 space-y-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h4 className="text-xs uppercase tracking-wider text-purple-300">
                  Dry run (live LLM, not persisted)
                </h4>
                <p className="text-[11px] text-slate-500">
                  Sends the rendered prompt + this user message to the LLM
                  and runs the response through the configured output
                  parser. No TestCase rows are created.
                </p>
              </div>
              <textarea
                value={dryRunUserMsg}
                onChange={(e) => setDryRunUserMsg(e.target.value)}
                rows={2}
                placeholder="What should the model generate?"
                className="w-full bg-white/5 border border-white/10 rounded px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-purple-500"
              />
              <button
                type="button"
                disabled={busy}
                onClick={handleDryRun}
                className="px-3 py-1.5 rounded-lg bg-purple-500/20 text-purple-200 border border-purple-500/30 text-xs font-semibold hover:bg-purple-500/30 disabled:opacity-40"
              >
                {busy ? "Running…" : "Run against LLM"}
              </button>
              {dryRunErr && <p className="text-xs text-red-300">{dryRunErr}</p>}
              {dryRun && (
                <div className="mt-2 space-y-2">
                  <p className="text-[11px] text-slate-400">
                    Model: <span className="text-slate-200">{dryRun.model ?? "?"}</span> ·
                    Provider: <span className="text-slate-200">{dryRun.provider ?? "?"}</span> ·
                    {dryRun.latency_ms}ms · output_format: {dryRun.output_format} ·
                    parsed {dryRun.test_cases.length} test case(s)
                  </p>
                  {dryRun.warnings.length > 0 && (
                    <ul className="text-[11px] text-amber-300 list-disc list-inside">
                      {dryRun.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  )}
                  {dryRun.test_cases.length > 0 && (
                    <div className="rounded border border-emerald-500/30 bg-emerald-500/5 p-2">
                      <p className="text-[11px] text-emerald-200 mb-1">
                        First {Math.min(3, dryRun.test_cases.length)} of {dryRun.test_cases.length} parsed test case(s):
                      </p>
                      <ul className="text-xs text-slate-200 space-y-1">
                        {dryRun.test_cases.slice(0, 3).map((tc, i) => (
                          <li key={i}>
                            <span className="text-white font-semibold">{tc.title}</span>
                            <span className="text-slate-500"> -- {tc.steps.length} step(s)</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <details className="text-[11px]">
                    <summary className="cursor-pointer text-slate-400">
                      Raw LLM response ({dryRun.raw.length.toLocaleString()} chars)
                    </summary>
                    <pre className="mt-1 bg-slate-950 border border-white/10 rounded p-2 text-xs text-slate-300 whitespace-pre-wrap max-h-[320px] overflow-auto">
                      {dryRun.raw}
                    </pre>
                  </details>
                </div>
              )}
            </AnimatedCard>
          </div>
        </div>
      )}

      {tab === "versions" && (
        <AnimatedCard glow="purple" className="p-3">
          <h4 className="text-xs uppercase tracking-wider text-cyan-300 mb-3">
            Version history
          </h4>
          <div className="space-y-2">
            {detail.versions.map((v) => (
              <div
                key={v.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/10 px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <span className="text-sm font-semibold text-white">
                    v{v.version_number}
                  </span>
                  <span className="ml-3 text-xs text-slate-400">
                    {v.body_bytes.toLocaleString()} bytes
                  </span>
                  {v.change_note && (
                    <p className="text-[11px] text-slate-400 mt-0.5">
                      “{v.change_note}”
                    </p>
                  )}
                  {v.created_at && (
                    <p className="text-[11px] text-slate-500 mt-0.5">
                      saved {new Date(v.created_at).toLocaleString()}
                    </p>
                  )}
                </div>
                <div className="flex gap-1">
                  <button
                    type="button"
                    onClick={() => handleOpenVersion(v.version_number)}
                    className="px-2.5 py-1 text-xs rounded glass text-slate-200 hover:text-white"
                  >
                    View body
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleActivateVersion(v.version_number)}
                    className="px-2.5 py-1 text-xs rounded bg-emerald-600/30 text-emerald-100 hover:bg-emerald-600/50 disabled:opacity-40"
                    title="Pin this version as your active override for the category."
                  >
                    Activate for me
                  </button>
                </div>
              </div>
            ))}
          </div>

          {openVersionBody && (
            <div className="mt-4 rounded-lg border border-white/10 bg-slate-950 p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-slate-400">
                  v{openVersionBody.version_number} body ({openVersionBody.body_bytes.toLocaleString()} bytes)
                </span>
                <button
                  type="button"
                  onClick={() => setOpenVersionBody(null)}
                  className="text-xs text-slate-500 hover:text-white"
                >
                  Close
                </button>
              </div>
              <pre className="text-xs text-slate-200 whitespace-pre-wrap max-h-[400px] overflow-auto">
                {openVersionBody.body}
              </pre>
            </div>
          )}
        </AnimatedCard>
      )}
    </PageScaffold>
  );
}
