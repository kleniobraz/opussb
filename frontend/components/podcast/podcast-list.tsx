"use client";

// Listagem de podcasts (§39): mesma fonte de dados de Projetos (GET /api/projects), filtrada
// por job_type. O ProjectCard já sabe rotear pro /podcast/<id> e mostrar o pill de status certo.
// Estados de carregando/vazio/erro no mesmo padrão v3 de app/(app)/projetos/page.tsx.

import { useProjects } from "@/hooks/use-projects";
import { ProjectCard } from "@/components/project-card";
import { V3Card } from "@/components/ui/v3-card";

function SkelGrid() {
  return (
    <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="animate-pulse overflow-hidden rounded-[var(--radius-md)] border border-v3-border bg-v3-surface-2"
        >
          <div className="aspect-[16/10] bg-v3-surface-1" />
          <div className="space-y-2 p-4">
            <div className="h-3 w-3/4 rounded bg-v3-surface-1" />
            <div className="h-3 w-1/2 rounded bg-v3-surface-1" />
          </div>
        </div>
      ))}
    </div>
  );
}

function MessageCard({ title, text }: { title: string; text: string }) {
  return (
    <V3Card className="flex flex-col items-center gap-2 px-6 py-16 text-center">
      <p className="font-v3-sans text-lg font-bold text-white">{title}</p>
      <p className="max-w-md font-mono text-sm text-v3-text-soft">{text}</p>
    </V3Card>
  );
}

export function PodcastList() {
  const { data, isLoading, isError } = useProjects();
  const podcasts = (data?.projects ?? []).filter((p) => p.job_type === "podcast");

  if (isLoading) return <SkelGrid />;

  if (isError) {
    return <MessageCard title="Não consegui carregar" text="Tente novamente em instantes." />;
  }

  if (!podcasts.length) {
    return <MessageCard title="Nenhum podcast ainda" text="Envie um episódio acima pra começar." />;
  }

  return (
    <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4">
      {podcasts.map((p) => (
        <ProjectCard key={p.id} p={p} />
      ))}
    </div>
  );
}
