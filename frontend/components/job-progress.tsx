"use client";

import { toast } from "sonner";
import * as api from "@/lib/api";
import { useJobStatus } from "@/hooks/use-job-status";
import { ClipGrid } from "./clip-grid";
import { Button } from "./ui/button";
import { cn } from "@/lib/utils";

const STATUS_LABELS: Record<string, string> = {
  queued: "Na fila...",
  extracting_audio: "Extraindo áudio...",
  uploading_audio: "Enviando áudio para transcrição...",
  transcribing: "Transcrevendo... (pode levar alguns minutos)",
  cutting: "Cortando clipes...",
  reframing: "Reformatando para 9:16...",
  uploading_clips: "Enviando clipes...",
  done: "Pronto!",
  error: "Erro no processamento.",
};

export function JobProgress({
  jobId,
  filename,
  onReset,
}: {
  jobId: string;
  filename: string;
  onReset: () => void;
}) {
  const { data } = useJobStatus(jobId);
  const status = data?.status ?? "queued";
  const progress = data?.progress ?? 5;
  const done = status === "done";
  const errored = status === "error";

  async function cancel() {
    const ok = await api.cancelJob(jobId);
    if (ok) {
      toast.success("Processamento cancelado");
      onReset();
    } else {
      toast.info("Cancelar aguarda o backend (POST /api/jobs/:id/cancel).");
    }
  }

  return (
    <>
      <section className="progress-card" style={{ paddingTop: 40 }}>
        <div className="progress-inner">
          <p className="eyebrow">Processando</p>
          <p className="filename">{filename}</p>
          <div className="progress-bar-track">
            <div className="progress-bar-fill" style={{ width: `${progress}%` }} />
          </div>
          <div className="progress-meta">
            <span className={cn("status-label", errored && "error-text")}>
              {errored
                ? "Erro: " + (data?.error || "Falha desconhecida.")
                : STATUS_LABELS[status] || status}
            </span>
            <span className="progress-pct">{progress}%</span>
          </div>
          <div className="progress-actions">
            {!done && !errored && (
              <Button variant="destructive" onClick={cancel}>
                Cancelar
              </Button>
            )}
            {(done || errored) && (
              <Button variant="brandGhost" onClick={onReset}>
                Processar outro vídeo
              </Button>
            )}
          </div>
        </div>
      </section>

      {done && data?.clips && data.clips.length > 0 && (
        <section className="mx-auto max-w-[1280px] px-7 pt-10">
          <div className="results-head">
            <div>
              <p className="eyebrow">Resultado</p>
              <h2 className="results-title">Clipes prontos</h2>
            </div>
          </div>
          <ClipGrid clips={data.clips} />
        </section>
      )}

      {errored && data?.partial_clips && data.partial_clips.length > 0 && (
        <section className="mx-auto max-w-[1280px] px-7 pt-10">
          <ClipGrid
            clips={data.partial_clips}
            notice="Clipes parciais (o processamento falhou após estes)."
          />
        </section>
      )}
    </>
  );
}
