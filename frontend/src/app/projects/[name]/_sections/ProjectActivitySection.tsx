"use client";

/**
 * Project home -> Activity sidebar wrapper.
 *
 * First extraction following the `_sections/` decomposition pattern.
 * Trivial today (a one-line ActivityFeed pass-through) so that we
 * prove the import path + folder convention work cleanly before
 * extracting the larger panels (Environments, Credentials, Sprints,
 * Test Cases, Tags, SavedTests).
 *
 * Why split a one-liner: it lets the project home shed an import +
 * a layout decision, which adds up quickly across 7 sections. The
 * point of `_sections/` is consistency, not line-count.
 */

import ActivityFeed from "@/components/projects/ActivityFeed";

interface Props {
  projectSlug: string;
  limit?: number;
}

export default function ProjectActivitySection({ projectSlug, limit = 15 }: Props) {
  return <ActivityFeed projectSlug={projectSlug} limit={limit} />;
}
