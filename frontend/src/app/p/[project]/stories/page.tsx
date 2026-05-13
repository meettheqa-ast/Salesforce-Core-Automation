import { redirect } from "next/navigation";

export default async function ProjectScopedStoriesPage({
  params,
}: {
  params: Promise<{ project: string }>;
}) {
  const { project } = await params;
  redirect(`/user-stories?project=${encodeURIComponent(project)}`);
}
