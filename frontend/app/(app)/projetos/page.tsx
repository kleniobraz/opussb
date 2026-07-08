"use client";

import Link from "next/link";
import { useState } from "react";
import { useProjects } from "@/hooks/use-projects";
import { ProjectCard } from "@/components/project-card";
import { cn } from "@/lib/utils";

type Sort = "all" | "score" | "recent";

function SkelGrid() {
  return (
    <div className="skel-grid">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="skel skel-card" />
      ))}
    </div>
  );
}

export default function ProjetosPage() {
  const { data, isLoading, isError } = useProjects();
  const [sort, setSort] = useState<Sort>("all");
  const projects = (data?.projects ?? []).filter((p) => p.job_type !== "podcast");

  const sorted = [...projects].sort((a, b) => {
    if (sort === "score") return (b.avg_score || 0) - (a.avg_score || 0);
    if (sort === "recent")
      return (
        (new Date(b.created_at ?? 0).getTime() || 0) - (new Date(a.created_at ?? 0).getTime() || 0)
      );
    return 0;
  });

  const chips: { key: Sort; label: string }[] = [
    { key: "all", label: "Todos" },
    { key: "score", label: "Melhores notas" },
    { key: "recent", label: "Mais recentes" },
  ];

  return (
    <>
      <section className="page-hero">
        <div className="hero-glows" aria-hidden="true">
          <div className="glow glow-b" style={{ top: "-20%", left: "30%" }} />
        </div>
        <div className="page-hero-inner">
          <div className="page-hero-row">
            <div>
              <p className="eyebrow">Biblioteca</p>
              <h1 className="page-title">Projetos</h1>
            </div>
            <Link href="/" className="link-gold">
              + Novo vídeo
            </Link>
          </div>
        </div>
      </section>

      <div className="view-pad">
        {isLoading ? (
          <SkelGrid />
        ) : isError ? (
          <div className="empty">
            <p className="empty-title">Não consegui carregar</p>
            <p className="empty-text">Tente novamente em instantes.</p>
          </div>
        ) : !projects.length ? (
          <div className="empty">
            <p className="empty-title">Nenhum projeto ainda</p>
            <p className="empty-text">
              Envie um vídeo na tela inicial para gerar seus primeiros clipes.
            </p>
            <Link href="/" className="link-gold mt-4 inline-block">
              Enviar vídeo
            </Link>
          </div>
        ) : (
          <>
            <div className="projects-toolbar">
              <span className="projects-count">
                {projects.length} {projects.length === 1 ? "projeto" : "projetos"}
              </span>
              <div className="clip-bands">
                {chips.map((c) => (
                  <button
                    key={c.key}
                    type="button"
                    className={cn("chip", sort === c.key && "is-active")}
                    onClick={() => setSort(c.key)}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="projects-grid">
              {sorted.map((p) => (
                <ProjectCard key={p.id} p={p} />
              ))}
            </div>
          </>
        )}
      </div>
    </>
  );
}
