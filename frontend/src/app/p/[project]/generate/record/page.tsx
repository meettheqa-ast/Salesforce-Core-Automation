import { redirect } from "next/navigation";

export default async function ProjectScopedGenerateRecordPage({
  params,
}: {
  params: Promise<{ project: string }>;
}) {
  const { project } = await params;
  redirect(`/generate/record?project=${encodeURIComponent(project)}`);
}
