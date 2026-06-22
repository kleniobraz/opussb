"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Hero } from "./hero";
import { HowItWorks } from "./how-it-works";
import { Recent } from "./recent";
import { ShaderBackground } from "../shader-background";
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
    <div>
      <section className="hero">
        <ShaderBackground />
        <div className="hero-glows" aria-hidden="true">
          <div className="glow glow-a" style={{ top: "-10%", left: "8%" }} />
          <div className="glow glow-b" style={{ top: 0, right: "6%" }} />
        </div>
        <Hero />
        {!job && (
          <div className="upload-wrap">
            <UploadConsole onStarted={onStarted} />
          </div>
        )}
      </section>

      {job && <JobProgress jobId={job.id} filename={job.filename} onReset={onReset} />}

      <HowItWorks />
      <Recent />
    </div>
  );
}
