"use client";

/**
 * Adaptive first-run onboarding checklist (Phase 3 IA audit).
 *
 * Renders on the home page in place of the static workflow cards
 * when the user has at least one project AND that project is missing
 * a step (no sprint, no story, no test case, no run). The checklist
 * dismisses itself once every step is satisfied OR the user clicks
 * Hide -- the dismissal persists per project so users don't see it
 * forever.
 *
 * State derives from the API (api.projects.testCases for TC + sprint
 * counts, api.runs.latest for first-run detection) so the steps
 * always reflect reality rather than a cached "user has done X".
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";
import AnimatedCard from "@/components/cards/AnimatedCard";
import { usePersistedState } from "@/hooks/usePersistedState";

interface Step {
  id: string;
  label: string;
  description: string;
  done: boolean;
  href: string;
  cta: string;
}

interface Props {
  projectSlug: string;
}

export default function OnboardingChecklist({ projectSlug }: Props) {
  // Step gates derived from API counts. Set initial values to a
  // pessimistic "done = false" so the checklist is visible while we
  // load; that's preferable to flashing it on then dismissing.
  const [hasSprint, setHasSprint] = useState<boolean | null>(null);
  const [hasStory, setHasStory] = useState<boolean | null>(null);
  const [hasTC, setHasTC] = useState<boolean | null>(null);
  const [hasScript, setHasScript] = useState<boolean | null>(null);
  const [hasRun, setHasRun] = useState<boolean | null>(null);
  const [dismissed, setDismissed] = usePersistedState<Record<string, boolean>>(
    "onboarding.dismissed",
    {},
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Project rollup gives us TC counts, story coverage, and the
        // sprint count via the existing endpoint.
        const tcs = await api.projects.testCases(projectSlug).catch(() => null);
        if (cancelled) return;
        setHasStory(!!tcs && tcs.stories.length > 0);
        setHasTC(!!tcs && tcs.total > 0);
        setHasScript(!!tcs && tcs.scripts_built > 0);

        // Sprint count -- portal UUID then list.
        const reg = await api.projects.portalProjectId(projectSlug).catch(() => null);
        if (!cancelled && reg) {
          const sprints = await api.sprints.list(reg.project_id).catch(() => []);
          setHasSprint(Array.isArray(sprints) && sprints.length > 0);
        }

        // Any run history for this project counts as "first run done".
        const recent = await api.runs.latest(20).catch(() => ({ runs: [] }));
        if (!cancelled) {
          setHasRun(
            (recent.runs || []).some((r) => r.project_slug === projectSlug),
          );
        }
      } catch {
        // Soft failure -- checklist just stays in loading state.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectSlug]);

  const steps: Step[] = [
    {
      id: "sprint",
      label: "Create a sprint",
      description: "Sprints group stories and let you run a coordinated suite.",
      done: hasSprint === true,
      href: `/projects/${encodeURIComponent(projectSlug)}`,
      cta: "Open project home",
    },
    {
      id: "story",
      label: "Create a user story",
      description: "Stories live under sprints (or the backlog) and own test cases.",
      done: hasStory === true,
      href: `/user-stories?project=${encodeURIComponent(projectSlug)}`,
      cta: "Open stories",
    },
    {
      id: "tc",
      label: "Generate or import test cases",
      description: "Use AI to draft from a story, or import a CSV / Excel file.",
      done: hasTC === true,
      href: `/projects/${encodeURIComponent(projectSlug)}/imports`,
      cta: "Open import wizard",
    },
    {
      id: "script",
      label: "Build a Robot script",
      description: "Approved test cases compile to runnable .robot files.",
      done: hasScript === true,
      href: `/user-stories?project=${encodeURIComponent(projectSlug)}`,
      cta: "Open stories",
    },
    {
      id: "run",
      label: "Run your first test",
      description: "Execute a built script against a registered Salesforce org.",
      done: hasRun === true,
      href: `/generate?project=${encodeURIComponent(projectSlug)}`,
      cta: "Open Generate",
    },
  ];

  const remaining = steps.filter((s) => !s.done);
  const allDone = remaining.length === 0 && steps.every((s) => s.done !== false || s.done === true);
  const isDismissed = !!dismissed[projectSlug];
  // Hide once every step is done OR the user dismissed.
  if (isDismissed || allDone) return null;
  // While we're still loading the API counts, render a minimal version
  // so the layout doesn't pop in/out.
  const loading = hasSprint === null || hasStory === null || hasTC === null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0 }}
      >
        <AnimatedCard glow="purple" className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 className="text-sm font-bold text-white">
                {loading
                  ? "Checking your project…"
                  : `Get this project running (${steps.filter((s) => s.done).length}/${steps.length})`}
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">
                Each step jumps you to the right surface. Dismiss any time.
              </p>
            </div>
            <button
              type="button"
              onClick={() =>
                setDismissed((prev) => ({ ...prev, [projectSlug]: true }))
              }
              className="text-[11px] text-slate-500 hover:text-white px-2"
              aria-label="Dismiss onboarding checklist"
            >
              Hide
            </button>
          </div>
          <ol className="space-y-2">
            {steps.map((s, i) => (
              <li
                key={s.id}
                className={`flex items-start gap-3 rounded-lg border px-3 py-2 ${
                  s.done
                    ? "border-emerald-500/30 bg-emerald-500/[0.04]"
                    : "border-white/10 bg-white/[0.02]"
                }`}
              >
                <span
                  className={`mt-0.5 w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0 ${
                    s.done
                      ? "bg-emerald-500 text-white"
                      : "bg-slate-700 text-slate-300"
                  }`}
                >
                  {s.done ? "✓" : i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p
                    className={`text-sm font-medium ${
                      s.done ? "text-emerald-200 line-through" : "text-slate-100"
                    }`}
                  >
                    {s.label}
                  </p>
                  <p className="text-[11px] text-slate-500 mt-0.5">{s.description}</p>
                </div>
                {!s.done && (
                  <Link
                    href={s.href}
                    className="text-xs px-2.5 py-1 rounded bg-purple-600/40 text-purple-100 hover:bg-purple-600/60 shrink-0"
                  >
                    {s.cta} →
                  </Link>
                )}
              </li>
            ))}
          </ol>
        </AnimatedCard>
      </motion.div>
    </AnimatePresence>
  );
}
