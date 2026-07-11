"use client";

import Link from "next/link";
import { useState } from "react";
import { useProjects } from "@/hooks/use-projects";
import { ProjectCard } from "@/components/projetos/project-card";
import { ParticlesBackground } from "@/components/backgrounds/particles-background";
import { Button } from "@/components/ui/button";
import { V3Card } from "@/components/ui/v3-card";
import { cn } from "@/lib/utils";

type Sort = "all" | "score" | "recent";

function SkelGrid() {
  return (
    <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
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

function MessageCard({
  title,
  text,
  action,
}: {
  title: string;
  text: string;
  action?: React.ReactNode;
}) {
  return (
    <V3Card className="flex flex-col items-center gap-2 px-6 py-16 text-center">
      <p className="font-v3-sans text-lg font-bold text-white">{title}</p>
      <p className="max-w-md font-mono text-sm text-v3-text-soft">{text}</p>
      {action}
    </V3Card>
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

  const showToolbar = !isLoading && !isError && projects.length > 0;

  return (
    <div className="relative bg-v3-surface-1">
      <section className="relative overflow-hidden border-b border-v3-border pb-8 pt-8">
        <div className="v3-home-glow" aria-hidden="true" />
        <ParticlesBackground />
        <div className="relative z-[1] mx-auto max-w-[1280px] px-7 pt-8">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
                Biblioteca
              </p>
              <h1 className="mt-2 font-v3-display text-4xl font-black uppercase text-white sm:text-5xl lg:text-6xl">
                Projetos
              </h1>
            </div>
            <Button asChild variant="v3Ghost" size="sm">
              <Link href="/">+ Novo vídeo</Link>
            </Button>
          </div>

          {showToolbar && (
            <div className="mt-8 flex flex-wrap items-center justify-between gap-4 border-t border-v3-border pt-6">
              <span className="font-mono text-sm text-v3-text-soft">
                {projects.length} {projects.length === 1 ? "projeto" : "projetos"}
              </span>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Ordenar projetos">
                {chips.map((c) => (
                  <button
                    key={c.key}
                    type="button"
                    className={cn(
                      "rounded-full border px-3.5 py-1.5 font-mono text-xs font-semibold uppercase tracking-wide transition-colors",
                      sort === c.key
                        ? "border-v3-accent bg-v3-accent text-v3-on-accent"
                        : "border-v3-border-hover text-v3-text-soft hover:border-v3-accent hover:text-white",
                    )}
                    onClick={() => setSort(c.key)}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Grade fora da faixa de atmosfera (glow/particulas): a lista de projetos pode crescer
          bastante, e nao pode esticar o glow/particulas junto (mesmo raciocinio do JobProgress
          na Home, ver home-view.tsx). */}
      <div className="relative z-[1] mx-auto max-w-[1280px] px-7 py-10">
        {isLoading ? (
          <SkelGrid />
        ) : isError ? (
          <MessageCard title="Não consegui carregar" text="Tente novamente em instantes." />
        ) : !projects.length ? (
          <MessageCard
            title="Nenhum projeto ainda"
            text="Envie um vídeo na tela inicial para gerar seus primeiros clipes."
            action={
              <Button asChild variant="v3Cta" size="sm" className="mt-2">
                <Link href="/">Enviar vídeo</Link>
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4">
            {sorted.map((p) => (
              <ProjectCard key={p.id} p={p} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
