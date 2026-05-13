import { redirect } from "next/navigation";

export default async function ProjectScopedSprintDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/sprints/${encodeURIComponent(id)}`);
}
