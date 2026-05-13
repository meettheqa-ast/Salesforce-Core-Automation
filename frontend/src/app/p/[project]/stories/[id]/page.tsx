import { redirect } from "next/navigation";

export default async function ProjectScopedStoryDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/user-stories/${encodeURIComponent(id)}`);
}
