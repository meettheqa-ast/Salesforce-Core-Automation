import { redirect } from "next/navigation";

export default async function ProjectScopedAnalyticsPage({
  params,
}: {
  params: Promise<{ project: string }>;
}) {
  const { project } = await params;
  redirect(`/dashboard?project=${encodeURIComponent(project)}`);
}
