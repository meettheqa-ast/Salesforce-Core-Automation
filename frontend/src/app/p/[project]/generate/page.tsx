import { redirect } from "next/navigation";

export default async function ProjectScopedGeneratePage({
  params,
}: {
  params: Promise<{ project: string }>;
}) {
  const { project } = await params;
  redirect(`/generate?project=${encodeURIComponent(project)}`);
}
