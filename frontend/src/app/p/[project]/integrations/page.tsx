import { redirect } from "next/navigation";

export default async function ProjectScopedIntegrationsPage({
  params,
}: {
  params: Promise<{ project: string }>;
}) {
  const { project } = await params;
  redirect(`/projects/${encodeURIComponent(project)}/integrations`);
}
