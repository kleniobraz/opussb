"use client";

// Listagem de podcasts (§39): mesma fonte de dados de Projetos (GET /api/projects), filtrada
// por job_type. O ProjectCard já sabe rotear pro /podcast/<id> e mostrar o pill de status certo.

import { useProjects } from "@/hooks/use-projects";
import { ProjectCard } from "@/components/project-card";

function SkelGrid() {
  return (
    <div className="skel-grid">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="skel skel-card" />
      ))}
    </div>
  );
}

export function PodcastList() {
  const { data, isLoading, isError } = useProjects();
  const podcasts = (data?.projects ?? []).filter((p) => p.job_type === "podcast");

  if (isLoading) return <SkelGrid />;

  if (isError) {
    return (
      <div className="empty">
        <p className="empty-title">Não consegui carregar</p>
        <p className="empty-text">Tente novamente em instantes.</p>
      </div>
    );
  }

  if (!podcasts.length) {
    return (
      <div className="empty">
        <p className="empty-title">Nenhum podcast ainda</p>
        <p className="empty-text">Envie um episódio acima pra começar.</p>
      </div>
    );
  }

  return (
    <div className="projects-grid">
      {podcasts.map((p) => (
        <ProjectCard key={p.id} p={p} />
      ))}
    </div>
  );
}
