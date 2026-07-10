"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Hero } from "./hero";
import { HowItWorks } from "./how-it-works";
import { Recent } from "./recent";
import { StatsStrip } from "./stats-strip";
import { HomeWaveBackground } from "./home-wave-background";
import { UploadConsole } from "../upload-console";
import { JobProgress } from "../job-progress";

export function HomeView() {
  const [job, setJob] = useState<{ id: string; filename: string } | null>(null);
  const queryClient = useQueryClient();

  function onStarted(id: string, filename: string) {
    setJob({ id, filename });
  }
  function onReset() {
    setJob(null);
    // Ao terminar/voltar, atualiza os recentes da Home.
    queryClient.invalidateQueries({ queryKey: ["stats"] });
  }

  return (
    <div className="relative bg-v3-bg">
      <section className="relative -mt-20 overflow-hidden border-b border-v3-border pt-20">
        <div className="v3-home-glow" aria-hidden="true" />
        <HomeWaveBackground />
        <Hero />
        {!job && (
          <div className="relative z-[1] mx-auto max-w-[1280px] px-7 pb-20 pt-6">
            <UploadConsole onStarted={onStarted} />
          </div>
        )}
      </section>

      {/* JobProgress fica FORA da faixa de atmosfera (glow/waveform): ele inclui a grade
          de resultados quando o job termina, que pode crescer bastante, e nao pode esticar
          o glow/waveform junto (o centro deles ia deslizar pra dentro da grade de clipes). */}
      {job && <JobProgress jobId={job.id} filename={job.filename} onReset={onReset} />}

      <StatsStrip />
      <Recent />
      <HowItWorks />
    </div>
  );
}
