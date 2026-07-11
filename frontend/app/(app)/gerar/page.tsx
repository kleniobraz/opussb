"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ParticlesBackground } from "@/components/backgrounds/particles-background";
import { UploadConsole } from "@/components/upload-console";
import { JobProgress } from "@/components/job-progress";
import { StatsStrip } from "@/components/home/stats-strip";
import { HowItWorks } from "@/components/home/how-it-works";

// Tela do gerador de clipes (antes vivia na Home, ver home-view.tsx). O estado
// job/onStarted/onReset migrou 1:1 de la.
export default function GerarPage() {
  const [job, setJob] = useState<{ id: string; filename: string } | null>(null);
  const queryClient = useQueryClient();

  function onStarted(id: string, filename: string) {
    setJob({ id, filename });
  }
  function onReset() {
    setJob(null);
    // Ao terminar/voltar, atualiza os numeros da faixa de stats.
    queryClient.invalidateQueries({ queryKey: ["stats"] });
  }

  return (
    <div className="relative bg-v3-surface-1">
      <section className="relative overflow-hidden border-b border-v3-border pb-8 pt-8">
        <div className="v3-home-glow" aria-hidden="true" />
        <ParticlesBackground />
        <div className="relative z-[1] mx-auto max-w-[1280px] px-7 pt-8">
          <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
            Estúdio
          </p>
          <h1 className="mt-2 font-v3-display text-4xl font-black uppercase text-white sm:text-5xl lg:text-6xl">
            Gerar clipes
          </h1>
        </div>
      </section>

      {/* Gerador FORA da faixa de atmosfera (glow/particulas): o JobProgress inclui a grade
          de resultados quando o job termina, que pode crescer bastante, e nao pode esticar
          o glow/particulas junto (o centro do efeito ia deslizar pra dentro da grade). */}
      {!job ? (
        <div className="mx-auto max-w-[1280px] px-7 pb-4 pt-10">
          <UploadConsole onStarted={onStarted} />
        </div>
      ) : (
        <div className="pt-7">
          <JobProgress jobId={job.id} filename={job.filename} onReset={onReset} />
        </div>
      )}

      <StatsStrip />
      <HowItWorks />
    </div>
  );
}
