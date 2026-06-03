"use client";

/**
 * Test case CSV / Excel import wizard.
 *
 * Three steps, all on a single page so the user keeps full context:
 *
 *   1. Upload      -- file picker + drop zone
 *   2. Map+Target  -- column mapping table + project/sprint/story
 *                     picker + duplicate strategy
 *   3. Preview+Commit -- 50-row table with validation badges; commit
 *                        button persists; final summary + links
 *
 * Pre-fill via the URL: open this page with
 *   /projects/{slug}/imports?story_id=<uuid>&project_id=<uuid>
 * and the wizard skips the picker on step 2 (still editable). The
 * "+ Import test cases" button on the story detail page uses this.
 */

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import type {
  ImportCommitResponse,
  ImportParseResponse,
} from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import GlassSelect from "@/components/ui/GlassSelect";

type WizardStep = "upload" | "map" | "preview" | "result";

type SprintLite = { id: string; name: string; state: string };
type StoryLite = { id: string; title: string; sprint_id?: string | null };

const DUP_STRATEGIES = [
  { value: "skip", label: "Skip duplicates" },
  { value: "overwrite", label: "Overwrite existing (clears built scripts)" },
  { value: "create_new", label: "Create new (append batch suffix)" },
] as const;

const DEFAULT_STATUS_OPTIONS = [
  { value: "draft", label: "Draft (review required)" },
  { value: "approved", label: "Approved (skip review)" },
] as const;

export default function ImportTestCasesPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name: slug } = use(params);
  const search = useSearchParams();

  const [step, setStep] = useState<WizardStep>("upload");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Step 1
  const [file, setFile] = useState<File | null>(null);
  const [parse, setParse] = useState<ImportParseResponse | null>(null);

  // Step 2
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [projectId, setProjectId] = useState<string>("");
  const [sprints, setSprints] = useState<SprintLite[]>([]);
  const [sprintId, setSprintId] = useState<string>("");
  const [stories, setStories] = useState<StoryLite[]>([]);
  const [storyId, setStoryId] = useState<string>("");
  const [perRowStory, setPerRowStory] = useState(false);
  const [duplicateStrategy, setDuplicateStrategy] =
    useState<"skip" | "overwrite" | "create_new">("skip");
  const [defaultStatus, setDefaultStatus] = useState<"draft" | "approved">("draft");

  // Step 4
  const [commit, setCommit] = useState<ImportCommitResponse | null>(null);

  // Resolve slug -> portal UUID once.
  useEffect(() => {
    api.projects.portalProjectId(slug)
      .then((r) => {
        setProjectId(r.project_id);
        // Pre-fill from query string when the user came via the "+ Import"
        // button on the story detail page.
        const qsProject = search.get("project_id");
        const qsStory = search.get("story_id");
        const qsSprint = search.get("sprint_id");
        if (qsProject && qsProject !== r.project_id) {
          // The wizard is locked to the project the user is browsing;
          // ignore a mismatched query param rather than confusing them.
        }
        if (qsStory) setStoryId(qsStory);
        if (qsSprint) setSprintId(qsSprint);
      })
      .catch(() => setErr("Could not resolve project."));
  }, [slug, search]);

  // Load sprints + stories once we have a project id.
  useEffect(() => {
    if (!projectId) return;
    api.sprints.list(projectId).then((rows) =>
      setSprints((rows as SprintLite[]).filter((s) => s.state !== "cancelled")),
    ).catch(() => setSprints([]));
    api.userStories.list(projectId).then((rows) =>
      setStories((rows as StoryLite[])),
    ).catch(() => setStories([]));
  }, [projectId]);

  // Stories filtered by sprint when one is selected, otherwise all.
  const visibleStories = useMemo(() => {
    if (!sprintId) return stories;
    return stories.filter((s) => s.sprint_id === sprintId);
  }, [sprintId, stories]);

  // ---- Step actions ---------------------------------------------

  async function handleUpload() {
    if (!file) {
      setErr("Pick a file to upload first.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await api.imports.parse(slug, file);
      setParse(res);
      setMapping({ ...res.suggested_mapping });
      // If the source has a story_id column auto-mapped, default to
      // per-row mode so the user notices.
      const hasStoryColumn = Object.values(res.suggested_mapping).some(
        (v) => v === "story_id",
      );
      setPerRowStory(hasStoryColumn);
      setStep("map");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Parse failed");
    } finally {
      setBusy(false);
    }
  }

  function handleAdvanceToPreview() {
    if (!projectId) {
      setErr("Project not resolved.");
      return;
    }
    if (!perRowStory && !storyId) {
      setErr("Pick a target story (or check 'Use story_id column from the file').");
      return;
    }
    if (perRowStory) {
      const hasStoryCol = Object.values(mapping).includes("story_id");
      if (!hasStoryCol) {
        setErr("Per-row mode needs the source file to have a 'story_id' column mapped.");
        return;
      }
    }
    // Require at least a title mapping; nothing else is mandatory.
    const hasTitle = Object.values(mapping).includes("title");
    if (!hasTitle) {
      setErr("Map at least one column to 'title' -- it's the only required field.");
      return;
    }
    setErr(null);
    setStep("preview");
  }

  async function handleCommit() {
    if (!parse) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await api.imports.commit({
        batch_id: parse.batch_id,
        mapping,
        target: {
          project_id: projectId,
          sprint_id: sprintId || null,
          story_id: perRowStory ? null : storyId,
        },
        duplicate_strategy: duplicateStrategy,
        default_status: defaultStatus,
      });
      setCommit(res);
      setStep("result");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Commit failed");
    } finally {
      setBusy(false);
    }
  }

  function downloadFailedRows() {
    if (!commit || commit.failed_rows.length === 0) return;
    const csv = [
      "row_index,title,message",
      ...commit.failed_rows.map(
        (r) =>
          `${r.row_index},"${(r.title || "").replace(/"/g, '""')}","${r.message.replace(/"/g, '""')}"`,
      ),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `import_${commit.batch_id}_failures.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function resetWizard() {
    setStep("upload");
    setFile(null);
    setParse(null);
    setMapping({});
    setCommit(null);
    setErr(null);
  }

  // ---- Render ---------------------------------------------------

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="max-w-5xl mx-auto px-6 py-10 space-y-6">
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
          <p className="text-xs uppercase tracking-widest text-slate-500">
            <Link href={`/projects/${encodeURIComponent(slug)}`} className="hover:text-slate-300">
              {slug}
            </Link>
            <span className="mx-2">/</span>imports
          </p>
          <h1 className="text-3xl font-semibold mt-2">Import test cases</h1>
          <p className="text-slate-400 mt-1">
            Upload a CSV or Excel file of test cases. The wizard maps your columns
            to the portal&apos;s canonical fields (Title, Steps, Expected Result,
            Tags, ...), lets you pick a target story, and commits.
          </p>
        </motion.div>

        {/* Stepper */}
        <div className="flex items-center gap-2 text-xs">
          {(["upload", "map", "preview", "result"] as WizardStep[]).map((s, i) => {
            const stepIndex = (["upload", "map", "preview", "result"] as WizardStep[]).indexOf(step);
            const isActive = step === s;
            const isDone = i < stepIndex;
            return (
              <div key={s} className="flex items-center gap-2">
                <div
                  className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold border ${
                    isActive
                      ? "bg-purple-600 border-purple-400 text-white"
                      : isDone
                        ? "bg-emerald-700/60 border-emerald-500 text-emerald-100"
                        : "bg-slate-800 border-slate-700 text-slate-400"
                  }`}
                >
                  {i + 1}
                </div>
                <span className={isActive ? "text-white" : "text-slate-500"}>
                  {s === "upload" ? "Upload" : s === "map" ? "Map columns" : s === "preview" ? "Preview" : "Result"}
                </span>
                {i < 3 && <span className="text-slate-700 mx-1">→</span>}
              </div>
            );
          })}
        </div>

        {err && (
          <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {err}
          </div>
        )}

        {step === "upload" && (
          <AnimatedCard glow="purple" className="p-6">
            <h2 className="text-sm font-semibold text-cyan-300 uppercase tracking-wider mb-3">
              Step 1 -- Upload file
            </h2>
            <p className="text-sm text-slate-400 mb-4">
              Accepted formats: <code>.csv</code>, <code>.xlsx</code>, <code>.xls</code>.
              Up to {10000} rows / 32 MB. The first row must contain column headers.
            </p>
            <input
              type="file"
              accept=".csv,.xlsx,.xls"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              className="block w-full text-sm text-slate-300 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-purple-600 file:text-white file:cursor-pointer hover:file:bg-purple-500"
            />
            {file && (
              <p className="text-xs text-slate-500 mt-2">
                Selected: <span className="text-slate-300">{file.name}</span> ({Math.round(file.size / 1024)} KB)
              </p>
            )}
            <div className="mt-5 flex gap-2">
              <button
                type="button"
                disabled={!file || busy}
                onClick={handleUpload}
                className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50"
              >
                {busy ? "Parsing..." : "Upload + Parse"}
              </button>
            </div>
          </AnimatedCard>
        )}

        {step === "map" && parse && (
          <AnimatedCard glow="purple" className="p-6">
            <h2 className="text-sm font-semibold text-cyan-300 uppercase tracking-wider mb-3">
              Step 2 -- Map columns + pick target
            </h2>
            <p className="text-sm text-slate-400 mb-4">
              File: <code>{parse.source_filename}</code> ({parse.row_count} row{parse.row_count === 1 ? "" : "s"}
              {parse.sheet_name ? ` from sheet "${parse.sheet_name}"` : ""}). Pre-filled mapping below is our best guess --
              override anything that looks wrong.
            </p>

            <div className="grid md:grid-cols-2 gap-6">
              {/* Column mapping */}
              <div>
                <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-2">Column mapping</h3>
                <div className="space-y-2 max-h-[28rem] overflow-y-auto pr-2">
                  {parse.columns.map((col) => (
                    <div key={col} className="grid grid-cols-2 gap-2 items-center">
                      <span className="text-xs text-slate-300 truncate" title={col}>{col}</span>
                      <GlassSelect
                        value={mapping[col] ?? ""}
                        onChange={(v) =>
                          setMapping((m) => ({ ...m, [col]: v === "" ? null : v }))
                        }
                        options={[
                          { value: "", label: "— skip this column —" },
                          ...parse.canonical_fields.map((f) => ({ value: f, label: f })),
                        ]}
                      />
                    </div>
                  ))}
                </div>
              </div>

              {/* Target picker */}
              <div className="space-y-3">
                <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-1">Target</h3>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Sprint (optional)</label>
                  <GlassSelect
                    value={sprintId}
                    onChange={(v) => { setSprintId(v); setStoryId(""); }}
                    options={[
                      { value: "", label: "Any (filter stories by sprint)" },
                      ...sprints.map((s) => ({ value: s.id, label: `${s.name} (${s.state})` })),
                    ]}
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">
                    Story {perRowStory ? "(disabled -- per-row mode)" : "(required)"}
                  </label>
                  <GlassSelect
                    value={storyId}
                    onChange={setStoryId}
                    disabled={perRowStory}
                    options={[
                      { value: "", label: "Pick a story..." },
                      ...visibleStories.map((s) => ({ value: s.id, label: s.title })),
                    ]}
                  />
                </div>
                <label className="inline-flex items-start gap-2 text-xs text-slate-300">
                  <input
                    type="checkbox"
                    checked={perRowStory}
                    onChange={(e) => setPerRowStory(e.target.checked)}
                    className="mt-0.5 accent-cyan-500"
                  />
                  <span>
                    Per-row mode: use a <code>story_id</code> column from the file
                    instead of picking one target story. Requires the column to be
                    mapped above.
                  </span>
                </label>

                <div className="pt-2">
                  <label className="block text-xs text-slate-400 mb-1">Duplicate strategy</label>
                  <GlassSelect
                    value={duplicateStrategy}
                    onChange={(v) => setDuplicateStrategy(v as typeof duplicateStrategy)}
                    options={DUP_STRATEGIES.map((s) => ({ value: s.value, label: s.label }))}
                  />
                  <p className="text-[11px] text-slate-500 mt-1">
                    Duplicates are detected by external_id first, then by title within
                    the target story / project.
                  </p>
                </div>

                <div>
                  <label className="block text-xs text-slate-400 mb-1">Default status for imported cases</label>
                  <GlassSelect
                    value={defaultStatus}
                    onChange={(v) => setDefaultStatus(v as typeof defaultStatus)}
                    options={DEFAULT_STATUS_OPTIONS.map((s) => ({ value: s.value, label: s.label }))}
                  />
                </div>
              </div>
            </div>

            <div className="mt-6 flex gap-2 justify-between">
              <button
                type="button"
                onClick={() => setStep("upload")}
                className="px-4 py-2 rounded-xl glass text-sm text-slate-300 hover:text-white"
              >
                ← Back
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={handleAdvanceToPreview}
                className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold disabled:opacity-50"
              >
                Preview →
              </button>
            </div>
          </AnimatedCard>
        )}

        {step === "preview" && parse && (
          <AnimatedCard glow="purple" className="p-6">
            <h2 className="text-sm font-semibold text-cyan-300 uppercase tracking-wider mb-3">
              Step 3 -- Preview + commit
            </h2>
            <p className="text-sm text-slate-400 mb-4">
              First {Math.min(50, parse.preview.length)} of {parse.row_count} rows shown.
              The full file will be processed on commit (capped at 1,000 rows per
              import).
            </p>
            <div className="overflow-x-auto rounded-lg border border-white/10">
              <table className="w-full text-xs">
                <thead className="bg-slate-900/60 text-slate-400">
                  <tr>
                    <th className="px-2 py-1.5 text-left">#</th>
                    {Object.entries(mapping)
                      .filter(([, canonical]) => !!canonical)
                      .map(([source, canonical]) => (
                        <th key={source} className="px-2 py-1.5 text-left">
                          <div className="text-slate-200">{canonical}</div>
                          <div className="text-slate-600 font-normal">← {source}</div>
                        </th>
                      ))}
                  </tr>
                </thead>
                <tbody>
                  {parse.preview.map((row, i) => {
                    const hasTitle = Object.entries(mapping).some(
                      ([src, can]) => can === "title" && row[src]?.trim(),
                    );
                    return (
                      <tr
                        key={i}
                        className={`border-t border-white/5 ${hasTitle ? "" : "bg-red-900/20"}`}
                      >
                        <td className="px-2 py-1.5 text-slate-500">{i + 1}</td>
                        {Object.entries(mapping)
                          .filter(([, canonical]) => !!canonical)
                          .map(([source]) => (
                            <td key={source} className="px-2 py-1.5 text-slate-300 truncate max-w-[14rem]">
                              {row[source] || <span className="text-slate-600 italic">—</span>}
                            </td>
                          ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="mt-6 flex gap-2 justify-between">
              <button
                type="button"
                onClick={() => setStep("map")}
                className="px-4 py-2 rounded-xl glass text-sm text-slate-300 hover:text-white"
              >
                ← Back
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={handleCommit}
                className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold disabled:opacity-50"
              >
                {busy ? "Committing..." : `Import ${parse.row_count} row${parse.row_count === 1 ? "" : "s"}`}
              </button>
            </div>
          </AnimatedCard>
        )}

        {step === "result" && commit && (
          <AnimatedCard glow="purple" className="p-6">
            <h2 className="text-sm font-semibold text-cyan-300 uppercase tracking-wider mb-3">
              Step 4 -- Result
            </h2>
            <div className="grid grid-cols-4 gap-3 text-sm">
              <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3">
                <div className="text-[11px] uppercase tracking-wider text-emerald-300">Imported</div>
                <div className="text-2xl font-bold text-emerald-100">{commit.imported_count}</div>
              </div>
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
                <div className="text-[11px] uppercase tracking-wider text-amber-300">Skipped</div>
                <div className="text-2xl font-bold text-amber-100">{commit.skipped_count}</div>
              </div>
              <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3">
                <div className="text-[11px] uppercase tracking-wider text-red-300">Failed</div>
                <div className="text-2xl font-bold text-red-100">{commit.failed_count}</div>
              </div>
              <div className="rounded-lg border border-slate-700 bg-slate-900/40 p-3">
                <div className="text-[11px] uppercase tracking-wider text-slate-400">Time</div>
                <div className="text-2xl font-bold text-slate-200">{commit.duration_ms}ms</div>
              </div>
            </div>

            <p className="mt-4 text-sm text-slate-400">
              Status: <span className="text-white font-semibold">{commit.status}</span>{" "}
              · Batch id: <code className="text-xs">{commit.batch_id}</code>
            </p>

            {commit.failed_rows.length > 0 && (
              <details className="mt-4 rounded-lg border border-red-400/30 bg-red-500/5 p-3">
                <summary className="cursor-pointer text-xs text-red-300">
                  {commit.failed_rows.length} failed row{commit.failed_rows.length === 1 ? "" : "s"} (click to expand)
                </summary>
                <ul className="mt-2 space-y-1 max-h-48 overflow-auto">
                  {commit.failed_rows.slice(0, 50).map((r) => (
                    <li key={r.row_index} className="text-[11px] text-slate-300">
                      <span className="text-slate-500">row {r.row_index}:</span>{" "}
                      <span className="text-red-200">{r.message}</span>
                      {r.title && <span className="text-slate-500"> -- {r.title}</span>}
                    </li>
                  ))}
                  {commit.failed_rows.length > 50 && (
                    <li className="text-[11px] italic text-slate-500">
                      +{commit.failed_rows.length - 50} more (use download below)
                    </li>
                  )}
                </ul>
                <button
                  type="button"
                  onClick={downloadFailedRows}
                  className="mt-2 px-3 py-1 rounded text-xs bg-slate-800 hover:bg-slate-700 text-slate-200"
                >
                  Download failed rows as CSV
                </button>
              </details>
            )}

            {/* Touched-stories deep links. Single-story mode lists
                one; per-row mode lists every touched story so the
                user has a path forward instead of having to find
                each one manually. */}
            {commit.touched_story_ids && commit.touched_story_ids.length > 0 && (
              <div className="mt-4 rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-3">
                <p className="text-[11px] uppercase tracking-wider text-cyan-300 mb-2">
                  Touched {commit.touched_story_ids.length} story{commit.touched_story_ids.length === 1 ? "" : "ies"}
                </p>
                <div className="flex flex-wrap gap-2">
                  {commit.touched_story_ids.slice(0, 12).map((sid) => {
                    const title = stories.find((s) => s.id === sid)?.title;
                    return (
                      <Link
                        key={sid}
                        href={`/user-stories/${encodeURIComponent(sid)}?project=${encodeURIComponent(slug)}`}
                        className="text-xs px-2.5 py-1 rounded bg-cyan-600/30 text-cyan-100 hover:bg-cyan-600/50 truncate max-w-[18rem]"
                        title={title || sid}
                      >
                        {title ? `${title} →` : `Story ${sid.slice(0, 8)} →`}
                      </Link>
                    );
                  })}
                  {commit.touched_story_ids.length > 12 && (
                    <span className="text-[11px] text-slate-500 italic self-center">
                      +{commit.touched_story_ids.length - 12} more
                    </span>
                  )}
                </div>
              </div>
            )}

            <div className="mt-6 flex gap-2 justify-between">
              <button
                type="button"
                onClick={resetWizard}
                className="px-4 py-2 rounded-xl glass text-sm text-slate-300 hover:text-white"
              >
                Import another file
              </button>
              {/* Primary CTA: when one story was targeted, deep-link
                  straight to it. Per-row imports show the touched-
                  stories list above instead so users can pick where
                  to go next. */}
              {commit.created_test_case_ids.length > 0 && storyId && (
                <Link
                  href={`/user-stories/${encodeURIComponent(storyId)}?project=${encodeURIComponent(slug)}`}
                  className="px-4 py-2 rounded-xl bg-gradient-to-r from-purple-600 to-cyan-500 text-white text-sm font-semibold"
                >
                  Go to story →
                </Link>
              )}
            </div>
          </AnimatedCard>
        )}
      </div>
    </div>
  );
}
