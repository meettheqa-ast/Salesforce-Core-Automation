import { redirect } from "next/navigation";

export default async function ProjectScopedRunDetailPage({
  params,
}: {
  params: Promise<{ run: string }>;
}) {
  const { run } = await params;
  redirect(`/runs/${encodeURIComponent(run)}`);
}
