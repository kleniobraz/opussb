"use client";

import { toast } from "sonner";
import * as api from "@/lib/api";
import { useJobStatus } from "@/hooks/use-job-status";
import { ClipGrid } from "./clip-grid";
import { Button } from "./ui/button";
import { V3Card } from "./ui/v3-card";
import { V3Progress } from "./ui/v3-progress";
import { cn } from "@/lib/utils";

const STATUS_LABELS: Record<string, string> = {
  queued: "Na fila...",
  downloading: "Baixando do YouTube...",
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
      <section className="mx-auto max-w-[1280px] px-7 pb-11 pt-3">
        <V3Card className="p-7">
          <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
            Processando
          </p>
          <p className="mt-3 break-all font-mono text-sm text-white">{filename}</p>
          <div className="mt-5">
            <V3Progress value={progress} />
          </div>
          <div className="mt-3 flex items-center justify-between">
            <span className={cn("font-mono text-sm", errored ? "text-[var(--red)]" : "text-v3-text-soft")}>
              {errored
                ? "Erro: " + (data?.error || "Falha desconhecida.")
                : STATUS_LABELS[status] || status}
            </span>
            <span className="font-mono text-sm font-bold text-v3-accent">{progress}%</span>
          </div>
          <div className="mt-6 flex flex-wrap gap-2.5">
            {!done && !errored && (
              <Button variant="destructive" onClick={cancel}>
                Cancelar
              </Button>
            )}
            {(done || errored) && (
              <Button variant="v3Ghost" onClick={onReset}>
                Processar outro vídeo
              </Button>
            )}
          </div>
        </V3Card>
      </section>

      {done && data?.clips && data.clips.length > 0 && (
        <section className="mx-auto max-w-[1280px] px-7 pt-4">
          <div className="mb-6">
            <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
              Resultado
            </p>
            <h2 className="mt-2 font-v3-display text-2xl font-black uppercase text-white">Clipes prontos</h2>
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
