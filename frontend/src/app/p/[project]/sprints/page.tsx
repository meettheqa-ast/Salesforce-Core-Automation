import { redirect } from "next/navigation";

export default async function ProjectScopedSprintsPage({
  params,
}: {
  params: Promise<{ project: string }>;
}) {
  const { project } = await params;
  redirect(`/sprints?project=${encodeURIComponent(project)}`);
}
