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
    <div className="bg-v3-bg">
      <section className="relative -mt-20 overflow-hidden border-b border-v3-border pt-20">
        <HomeWaveBackground />
        <Hero />
      </section>

      {job ? (
        <JobProgress jobId={job.id} filename={job.filename} onReset={onReset} />
      ) : (
        <div className="mx-auto max-w-[1280px] px-7 pb-11 pt-3">
          <UploadConsole onStarted={onStarted} />
        </div>
      )}

      <StatsStrip />
      <Recent />
      <HowItWorks />
    </div>
  );
}
