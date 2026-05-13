import { redirect } from "next/navigation";

export default async function ProjectScopedRunsPage({
  params,
}: {
  params: Promise<{ project: string }>;
}) {
  const { project } = await params;
  redirect(`/runs?project=${encodeURIComponent(project)}`);
}
