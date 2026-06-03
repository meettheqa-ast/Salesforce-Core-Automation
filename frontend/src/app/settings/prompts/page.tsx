"use client";

/**
 * AI Prompt Management list page.
 *
 * Lists every prompt template in the registry grouped by category, with
 * a source-scope badge (System / User / Project / Org) on each row.
 * Actions per row:
 *
 *   * Edit       -- open the editor at /settings/prompts/[id]
 *   * Clone      -- create a user-scoped copy for editing (system rows
 *                   are immutable; cloning is the path into Settings)
 *   * Activate   -- set this template as the active version for the
 *                   current user's scope (or org, for admins)
 *   * Reset      -- remove a scope override and fall back to the system
 *                   default
 *
 * The "active for me" badge is computed by joining each row's existing
 * `overrides[]` against the current user id; the source-scope badge on
 * each row reflects the same join so users can see at a glance which
 * prompt is actually running for them.
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  api,
  type PromptActiveOverridesMap,
  type PromptCategory,
  type PromptTemplateSummary,
} from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { PageHeader, PageScaffold } from "@/components/layout/PageScaffold";
import StatusPill from "@/components/data/StatusPill";

interface Me {
  id: string;
  is_admin?: boolean;
}

export default function PromptsSettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [categories, setCategories] = useState<PromptCategory[]>([]);
  const [templates, setTemplates] = useState<PromptTemplateSummary[]>([]);
  /** Map of category -> template_id active for the current user / org.
   *  Used to badge rows ("Active for me" / "Org default") without N+1
   *  detail fetches. Falls back to empty maps when the endpoint is
   *  unreachable -- badges just don't render in that case. */
  const [overrides, setOverrides] = useState<PromptActiveOverridesMap>({
    user: {},
    org: {},
  });
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      const [cats, tpls, who, ovr] = await Promise.all([
        api.prompts.categories(),
        api.prompts.list(),
        api.me().catch(() => null),
        api.prompts.activeOverrides().catch(() => ({ user: {}, org: {} } as PromptActiveOverridesMap)),
      ]);
      setCategories(cats);
      setTemplates(tpls);
      setMe(who as Me | null);
      setOverrides(ovr);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not load prompts");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // Group templates by category for the list view.
  const byCategory = useMemo(() => {
    const groups = new Map<string, PromptTemplateSummary[]>();
    for (const tpl of templates) {
      const arr = groups.get(tpl.category) || [];
      arr.push(tpl);
      groups.set(tpl.category, arr);
    }
    // Sort each group: system seeds first, then alphabetical.
    for (const arr of groups.values()) {
      arr.sort((a, b) => {
        if (a.is_system !== b.is_system) return a.is_system ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
    }
    return groups;
  }, [templates]);

  async function handleClone(tpl: PromptTemplateSummary) {
    setBusyId(tpl.id);
    setErr(null);
    try {
      // Fetch the source body so the new clone starts from the same
      // content. The detail endpoint returns the version list; we
      // then load the latest version body.
      const detail = await api.prompts.get(tpl.id);
      const latest = detail.versions[0];
      const body = latest
        ? await api.prompts.getVersion(tpl.id, latest.version_number)
        : { body: "" };
      const cloneName = `${tpl.name} (my copy)`;
      const created = await api.prompts.create({
        category: tpl.category,
        name: cloneName,
        description: `Cloned from ${tpl.name}`,
        body: body.body,
        output_format: tpl.output_format,
        model_hint: tpl.model_hint,
        placeholders_declared: tpl.placeholders_declared,
        source_template_id: tpl.id,
        scope: "user",
        scope_id: me?.id ?? null,
        change_note: "clone",
      });
      // Send the user straight into the editor on success.
      window.location.assign(`/settings/prompts/${encodeURIComponent(created.id)}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Clone failed");
    } finally {
      setBusyId(null);
    }
  }

  async function handleActivate(tpl: PromptTemplateSummary, scope: "user" | "org") {
    setBusyId(tpl.id);
    setErr(null);
    try {
      const detail = await api.prompts.get(tpl.id);
      const latestNumber = detail.versions[0]?.version_number ?? 1;
      await api.prompts.activate(tpl.id, {
        version_number: latestNumber,
        scope,
        scope_id: scope === "user" ? me?.id ?? null : null,
      });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Activate failed");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReset(tpl: PromptTemplateSummary, scope: "user" | "org") {
    setBusyId(tpl.id);
    setErr(null);
    try {
      await api.prompts.reset(tpl.id, {
        scope,
        scope_id: scope === "user" ? me?.id ?? null : null,
      });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Reset failed");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <PageScaffold>
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <PageHeader
          eyebrow="Settings"
          title="AI Prompts"
          description={
            "Manage the system prompts that drive test case generation, script " +
            "building, healing, and stepwise planning. Edits are per-user unless " +
            "you have admin / project-lead permissions for org / project scope."
          }
        />
        <p className="text-xs text-slate-500 mt-2">
          <Link href="/settings" className="hover:text-slate-300">← Back to Settings</Link>
        </p>
      </motion.div>

      {err && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
          {err}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-slate-400">Loading prompts…</p>
      ) : (
        <div className="space-y-6">
          {categories.map((cat) => {
            const rows = byCategory.get(cat.category) || [];
            return (
              <AnimatedCard glow="purple" delay={0.05} key={cat.category}>
                <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
                  <h3 className="text-sm font-bold text-white">
                    {cat.default_name}{" "}
                    <span className="text-[11px] text-slate-500 font-normal">
                      ({cat.category})
                    </span>
                  </h3>
                  <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">
                    {cat.output_format}
                  </span>
                </div>
                <p className="text-xs text-slate-500 mb-3">{cat.description}</p>

                {rows.length === 0 ? (
                  <p className="text-xs text-slate-500 italic">
                    No templates registered yet for this category.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {rows.map((tpl) => {
                      // Badges are computed from the lightweight
                      // /api/prompts/active map (no N+1 detail fetches).
                      // Active for me wins visually over Org default
                      // because the user's override is what their next
                      // generation will actually use.
                      const activeForMe = overrides.user[tpl.category] === tpl.id;
                      const activeForOrg = overrides.org[tpl.category] === tpl.id;
                      return (
                        <div
                          key={tpl.id}
                          className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 ${
                            activeForMe
                              ? "border-emerald-500/40 bg-emerald-500/5"
                              : activeForOrg
                                ? "border-amber-500/30 bg-amber-500/5"
                                : "border-white/10"
                          }`}
                        >
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 flex-wrap">
                              <Link
                                href={`/settings/prompts/${encodeURIComponent(tpl.id)}`}
                                className="text-sm font-semibold text-white hover:text-purple-200 break-words"
                              >
                                {tpl.name}
                              </Link>
                              {activeForMe && (
                                <StatusPill tone="success" label="Active for me" />
                              )}
                              {!activeForMe && activeForOrg && (
                                <StatusPill tone="warning" label="Org default" />
                              )}
                              {tpl.is_system && (
                                <StatusPill tone="info" label="System default" />
                              )}
                              {!tpl.is_system && tpl.owner_user_id === me?.id && (
                                <StatusPill tone="muted" label="Mine" />
                              )}
                              {tpl.deleted_at && (
                                <StatusPill tone="danger" label="Deleted" />
                              )}
                            </div>
                            {tpl.description && (
                              <p className="text-[11px] text-slate-400 mt-0.5">{tpl.description}</p>
                            )}
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {tpl.is_system ? (
                              <button
                                type="button"
                                disabled={busyId === tpl.id}
                                onClick={() => handleClone(tpl)}
                                className="px-2.5 py-1 text-xs rounded bg-purple-600/40 text-purple-100 hover:bg-purple-600/60 disabled:opacity-40"
                                title="Create a personal copy of this system prompt that you can edit."
                              >
                                Clone & edit
                              </button>
                            ) : (
                              <Link
                                href={`/settings/prompts/${encodeURIComponent(tpl.id)}`}
                                className="px-2.5 py-1 text-xs rounded bg-purple-600/40 text-purple-100 hover:bg-purple-600/60"
                              >
                                Edit
                              </Link>
                            )}
                            <button
                              type="button"
                              disabled={busyId === tpl.id}
                              onClick={() => handleActivate(tpl, "user")}
                              className="px-2.5 py-1 text-xs rounded bg-emerald-600/30 text-emerald-100 hover:bg-emerald-600/50 disabled:opacity-40"
                              title="Pin the latest version of this template as your personal active prompt for this category."
                            >
                              Activate for me
                            </button>
                            {me?.is_admin && (
                              <button
                                type="button"
                                disabled={busyId === tpl.id}
                                onClick={() => handleActivate(tpl, "org")}
                                className="px-2.5 py-1 text-xs rounded bg-amber-600/30 text-amber-100 hover:bg-amber-600/50 disabled:opacity-40"
                                title="Set this template as the org-wide default. Affects everyone without their own user override."
                              >
                                Set as org default
                              </button>
                            )}
                            <button
                              type="button"
                              disabled={busyId === tpl.id}
                              onClick={() => handleReset(tpl, "user")}
                              className="px-2.5 py-1 text-xs rounded border border-slate-500/40 text-slate-300 hover:bg-slate-500/10 disabled:opacity-40"
                              title="Remove your personal override and fall back to the next layer (project / org / system)."
                            >
                              Reset mine
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </AnimatedCard>
            );
          })}
        </div>
      )}
    </PageScaffold>
  );
}
