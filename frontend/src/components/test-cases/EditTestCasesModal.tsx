"use client";

/**
 * Bulk editor modal for a single user story's test cases.
 *
 * Two entry points share this component:
 *   - "+ Add case manually" -- opens with one blank case prefilled.
 *   - "Edit all" -- opens preloaded with every existing case for the story.
 *
 * On Save the modal walks each form row and decides what to send:
 *   - Existing case with edits          -> PATCH /test-cases/{id}
 *   - New case                          -> POST /test-cases
 *   - Existing case marked "Remove"     -> PATCH /test-cases/{id} {status: "rejected"}
 *
 * We never hard-delete -- "rejected" keeps the audit trail and lets the user
 * recover via the Re-draft button on the story detail page if they
 * change their mind.
 *
 * Optimistic UX is intentionally kept simple: Save All disables the modal
 * while in flight, then closes it and signals the parent to refetch the
 * canonical test-cases list. No partial-success UI; if any one request
 * fails we surface the error and keep the modal open so the user can
 * retry without losing edits.
 */

import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";

export type TestCaseDraft = {
  /** Server id when this row maps to an existing TestCase row. Null for
   *  new-from-scratch rows (the "+ Add another case" output and the
   *  initial blank when the modal is opened in "manual create" mode). */
  id: string | null;
  title: string;
  steps: string[];
  expected_result: string;
  preconditions: string;
  tags: string[];
  status: "draft" | "approved" | "rejected";
  /** Local-only flags. Not sent to the server. */
  removed: boolean;
  dirty: boolean;
};

type Snapshot = Pick<
  TestCaseDraft,
  "title" | "steps" | "expected_result" | "preconditions" | "tags" | "status"
>;

interface Props {
  open: boolean;
  storyId: string;
  storyTitle: string;
  /** Existing cases to seed the modal. Empty array for the "Add manually"
   *  flow; the modal will inject one blank row in that case. */
  existing: TestCaseDraft[];
  /** Optional set of suggested tags from the project; lets the user
   *  click a tag rather than type it. Always free-text capable too. */
  knownTags: string[];
  onClose: () => void;
  /** Called after Save All resolves successfully. Parent should refetch
   *  the test-cases list. */
  onSaved: () => void;
}

const BLANK_DRAFT = (): TestCaseDraft => ({
  id: null,
  title: "",
  steps: [""],
  expected_result: "",
  preconditions: "",
  tags: [],
  status: "draft",
  removed: false,
  dirty: true,
});

function snapshot(d: TestCaseDraft): Snapshot {
  return {
    title: d.title,
    steps: [...d.steps],
    expected_result: d.expected_result,
    preconditions: d.preconditions,
    tags: [...d.tags],
    status: d.status,
  };
}

function snapshotsEqual(a: Snapshot, b: Snapshot): boolean {
  if (a.title !== b.title) return false;
  if (a.expected_result !== b.expected_result) return false;
  if (a.preconditions !== b.preconditions) return false;
  if (a.status !== b.status) return false;
  if (a.steps.length !== b.steps.length) return false;
  if (a.steps.some((s, i) => s !== b.steps[i])) return false;
  if (a.tags.length !== b.tags.length) return false;
  if ([...a.tags].sort().join("|") !== [...b.tags].sort().join("|")) return false;
  return true;
}

export default function EditTestCasesModal({
  open,
  storyId,
  storyTitle,
  existing,
  knownTags,
  onClose,
  onSaved,
}: Props) {
  const [drafts, setDrafts] = useState<TestCaseDraft[]>([]);
  const [originalById, setOriginalById] = useState<Record<string, Snapshot>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset drafts whenever the modal is (re)opened. The parent can change
  // `existing` between opens (e.g. opening for a different story).
  useEffect(() => {
    if (!open) return;
    if (existing.length === 0) {
      setDrafts([BLANK_DRAFT()]);
      setOriginalById({});
    } else {
      setDrafts(existing.map((d) => ({ ...d, dirty: false, removed: false })));
      const orig: Record<string, Snapshot> = {};
      for (const d of existing) {
        if (d.id) orig[d.id] = snapshot(d);
      }
      setOriginalById(orig);
    }
    setError(null);
    setSaving(false);
  }, [open, existing]);

  const dirtyExistingCount = useMemo(
    () => drafts.filter((d) => d.id && !d.removed && d.dirty).length,
    [drafts],
  );
  const newCount = useMemo(() => drafts.filter((d) => !d.id && !d.removed).length, [drafts]);
  const removedCount = useMemo(() => drafts.filter((d) => d.id && d.removed).length, [drafts]);
  const hasChanges = dirtyExistingCount + newCount + removedCount > 0;

  const setDraft = (idx: number, patch: Partial<TestCaseDraft>) => {
    setDrafts((prev) =>
      prev.map((d, i) => {
        if (i !== idx) return d;
        const next = { ...d, ...patch };
        // Recompute dirty for existing rows by comparing against the
        // snapshot we took when the modal opened. New rows (id === null)
        // are always considered dirty.
        if (next.id) {
          const orig = originalById[next.id];
          next.dirty = orig ? !snapshotsEqual(orig, snapshot(next)) : true;
        } else {
          next.dirty = true;
        }
        return next;
      }),
    );
  };

  const addBlank = () => {
    setDrafts((prev) => [...prev, BLANK_DRAFT()]);
  };

  const toggleRemove = (idx: number) => {
    setDrafts((prev) => {
      const cur = prev[idx];
      if (!cur.id) {
        // A row with no id has never been saved; "remove" just drops it
        // from the list rather than scheduling a PATCH.
        return prev.filter((_, i) => i !== idx);
      }
      return prev.map((d, i) => (i === idx ? { ...d, removed: !d.removed } : d));
    });
  };

  const updateStep = (idx: number, stepIdx: number, value: string) => {
    setDraft(idx, {
      steps: drafts[idx].steps.map((s, j) => (j === stepIdx ? value : s)),
    });
  };

  const addStep = (idx: number) => {
    setDraft(idx, { steps: [...drafts[idx].steps, ""] });
  };

  const removeStep = (idx: number, stepIdx: number) => {
    setDraft(idx, {
      steps: drafts[idx].steps.filter((_, j) => j !== stepIdx),
    });
  };

  const toggleTag = (idx: number, tagName: string) => {
    const cur = drafts[idx];
    const next = cur.tags.includes(tagName)
      ? cur.tags.filter((t) => t !== tagName)
      : [...cur.tags, tagName];
    setDraft(idx, { tags: next });
  };

  const addCustomTag = (idx: number, value: string) => {
    const tag = value.trim();
    if (!tag) return;
    if (drafts[idx].tags.includes(tag)) return;
    setDraft(idx, { tags: [...drafts[idx].tags, tag] });
  };

  const handleSave = async () => {
    setError(null);
    setSaving(true);
    try {
      const tasks: Promise<unknown>[] = [];
      for (const d of drafts) {
        // Drop empty new rows silently rather than POSTing garbage.
        if (!d.id && !d.removed) {
          if (!d.title.trim() && d.steps.every((s) => !s.trim()) && !d.expected_result.trim()) {
            continue;
          }
        }
        if (d.id && d.removed) {
          tasks.push(
            api.testCases.patch(d.id, { status: "rejected" }),
          );
          continue;
        }
        if (d.id && d.dirty) {
          // Send only the fields that actually change so PATCH stays minimal.
          const orig = originalById[d.id];
          const cur = snapshot(d);
          const body: Parameters<typeof api.testCases.patch>[1] = {};
          if (!orig || orig.title !== cur.title) body.title = cur.title.trim() || "Untitled";
          if (!orig || orig.expected_result !== cur.expected_result) body.expected_result = cur.expected_result;
          if (!orig || orig.preconditions !== cur.preconditions)
            body.preconditions = cur.preconditions.trim() || null;
          if (!orig || orig.steps.length !== cur.steps.length || orig.steps.some((s, i) => s !== cur.steps[i]))
            body.steps = cur.steps.filter((s) => s !== null);
          if (
            !orig ||
            orig.tags.length !== cur.tags.length ||
            [...orig.tags].sort().join("|") !== [...cur.tags].sort().join("|")
          )
            body.tags = cur.tags;
          if (!orig || orig.status !== cur.status) body.status = cur.status;
          if (Object.keys(body).length > 0) {
            tasks.push(api.testCases.patch(d.id, body));
          }
          continue;
        }
        if (!d.id) {
          tasks.push(
            api.testCases.create({
              user_story_id: storyId,
              title: d.title.trim() || "Untitled",
              steps: d.steps.filter((s) => s.trim()),
              expected_result: d.expected_result.trim(),
              preconditions: d.preconditions.trim() || null,
              tags: d.tags,
              status: d.status,
            }),
          );
        }
      }

      await Promise.all(tasks);
      onSaved();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed -- nothing was discarded, you can retry.");
      setSaving(false);
    }
  };

  if (!open) return null;

  const visibleDrafts = drafts.map((d, idx) => ({ d, idx }));
  const totalShown = visibleDrafts.length;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={() => !saving && onClose()}
    >
      <motion.div
        initial={{ scale: 0.94 }}
        animate={{ scale: 1 }}
        className="glass-strong w-full max-w-3xl max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10">
          <div>
            <h2 className="text-base font-bold text-white">Edit test cases</h2>
            <p className="text-xs text-slate-400 truncate">{storyTitle}</p>
          </div>
          <button
            type="button"
            onClick={() => !saving && onClose()}
            className="text-slate-400 hover:text-white text-xl leading-none"
            disabled={saving}
          >
            ×
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4 space-y-5 flex-1">
          {visibleDrafts.map(({ d, idx }, displayIdx) => (
            <div
              key={`${d.id ?? "new"}-${idx}`}
              className={`rounded-xl border p-4 ${
                d.removed
                  ? "border-red-500/30 bg-red-500/5 opacity-60"
                  : d.id
                    ? "border-white/10 bg-white/5"
                    : "border-cyan-500/30 bg-cyan-500/5"
              }`}
            >
              <div className="flex items-center justify-between mb-3">
                <p className="text-[10px] uppercase tracking-wider text-slate-400">
                  Test case {displayIdx + 1} of {totalShown}
                  {!d.id && (
                    <span className="ml-2 px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-200 text-[9px]">
                      NEW
                    </span>
                  )}
                  {d.id && d.dirty && !d.removed && (
                    <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-200 text-[9px]">
                      EDITED
                    </span>
                  )}
                  {d.removed && (
                    <span className="ml-2 px-1.5 py-0.5 rounded bg-red-500/20 text-red-200 text-[9px]">
                      WILL REJECT
                    </span>
                  )}
                </p>
                <button
                  type="button"
                  onClick={() => toggleRemove(idx)}
                  disabled={saving}
                  className="text-[11px] text-red-300 hover:text-red-200 disabled:opacity-50"
                >
                  {d.removed ? "Undo remove" : d.id ? "Remove" : "Discard"}
                </button>
              </div>

              <div className="space-y-3">
                <FormRow label="Title">
                  <input
                    value={d.title}
                    onChange={(e) => setDraft(idx, { title: e.target.value })}
                    disabled={saving || d.removed}
                    placeholder="What does this case verify?"
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-100 outline-none focus:border-purple-500"
                  />
                </FormRow>

                <FormRow label="Status">
                  <select
                    value={d.status}
                    onChange={(e) =>
                      setDraft(idx, { status: e.target.value as TestCaseDraft["status"] })
                    }
                    disabled={saving || d.removed}
                    className="bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-xs text-slate-200"
                  >
                    <option value="draft">Draft</option>
                    <option value="approved">Approved</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </FormRow>

                <FormRow label="Preconditions">
                  <textarea
                    value={d.preconditions}
                    onChange={(e) => setDraft(idx, { preconditions: e.target.value })}
                    disabled={saving || d.removed}
                    placeholder="Optional"
                    rows={2}
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-100 outline-none focus:border-purple-500"
                  />
                </FormRow>

                <FormRow label="Steps">
                  <div className="space-y-2">
                    {d.steps.map((step, sIdx) => (
                      <div key={sIdx} className="flex items-start gap-2">
                        <span className="text-[10px] text-slate-500 pt-2 w-5 text-right">{sIdx + 1}.</span>
                        <textarea
                          value={step}
                          onChange={(e) => updateStep(idx, sIdx, e.target.value)}
                          disabled={saving || d.removed}
                          rows={1}
                          className="flex-1 bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-slate-100 outline-none focus:border-purple-500 resize-y"
                        />
                        <button
                          type="button"
                          onClick={() => removeStep(idx, sIdx)}
                          disabled={saving || d.removed}
                          className="text-slate-500 hover:text-red-400 text-sm pt-1.5 disabled:opacity-50"
                        >
                          ×
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={() => addStep(idx)}
                      disabled={saving || d.removed}
                      className="text-[11px] text-purple-300 hover:text-purple-200 disabled:opacity-50"
                    >
                      + Add step
                    </button>
                  </div>
                </FormRow>

                <FormRow label="Expected result">
                  <textarea
                    value={d.expected_result}
                    onChange={(e) => setDraft(idx, { expected_result: e.target.value })}
                    disabled={saving || d.removed}
                    rows={2}
                    className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-slate-100 outline-none focus:border-purple-500"
                  />
                </FormRow>

                <FormRow label="Tags">
                  <div className="flex flex-wrap gap-1.5">
                    {knownTags.map((t) => {
                      const on = d.tags.includes(t);
                      return (
                        <button
                          key={t}
                          type="button"
                          onClick={() => toggleTag(idx, t)}
                          disabled={saving || d.removed}
                          className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                            on
                              ? "bg-purple-500/30 text-purple-100 border-purple-400"
                              : "bg-white/5 text-slate-300 border-white/10 hover:border-purple-400"
                          }`}
                        >
                          {t}
                        </button>
                      );
                    })}
                    {d.tags
                      .filter((t) => !knownTags.includes(t))
                      .map((t) => (
                        <button
                          key={t}
                          type="button"
                          onClick={() => toggleTag(idx, t)}
                          disabled={saving || d.removed}
                          className="text-[11px] px-2 py-0.5 rounded-full bg-purple-500/30 text-purple-100 border border-purple-400"
                        >
                          {t} ×
                        </button>
                      ))}
                    <input
                      type="text"
                      placeholder="+ tag"
                      disabled={saving || d.removed}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addCustomTag(idx, (e.target as HTMLInputElement).value);
                          (e.target as HTMLInputElement).value = "";
                        }
                      }}
                      className="bg-white/5 border border-white/10 rounded-full px-2 py-0.5 text-[11px] text-slate-200 outline-none focus:border-purple-500 w-20"
                    />
                  </div>
                </FormRow>
              </div>
            </div>
          ))}

          <button
            type="button"
            onClick={addBlank}
            disabled={saving}
            className="w-full py-3 rounded-xl border border-dashed border-white/15 hover:border-cyan-400 text-sm text-slate-400 hover:text-cyan-200 transition-colors disabled:opacity-50"
          >
            + Add another case
          </button>
        </div>

        <div className="px-5 py-3 border-t border-white/10 flex items-center justify-between flex-wrap gap-2">
          <div className="text-[11px] text-slate-400">
            {hasChanges ? (
              <>
                {dirtyExistingCount > 0 && <span className="text-amber-300">{dirtyExistingCount} edited</span>}
                {dirtyExistingCount > 0 && (newCount > 0 || removedCount > 0) && <span className="mx-1">·</span>}
                {newCount > 0 && <span className="text-cyan-300">{newCount} new</span>}
                {newCount > 0 && removedCount > 0 && <span className="mx-1">·</span>}
                {removedCount > 0 && <span className="text-red-300">{removedCount} to reject</span>}
              </>
            ) : (
              <span>No changes yet.</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {error && <span className="text-[11px] text-red-300">{error}</span>}
            <button
              type="button"
              onClick={onClose}
              disabled={saving}
              className="px-3 py-1.5 rounded-lg glass text-xs text-slate-300 hover:text-white disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !hasChanges}
              className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-xs font-semibold disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save all changes"}
            </button>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">{label}</div>
      {children}
    </label>
  );
}
