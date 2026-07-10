"use client";

// Upload do episódio de podcast (§39): estaciona o job em 'uploaded' (POST /api/podcast/upload)
// e leva pra tela de aprovação do gancho, de onde o disparo da transcrição acontece. Mesmo
// entorno visual v3 do UploadConsole da Home, sem os controles de clipe (aspecto/duração/
// idioma/legenda não existem aqui; o único ajuste é a vinheta que abre o episódio).

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import * as api from "@/lib/api";
import { fmtBytes, fmtEta, fmtSeconds } from "@/lib/format";
import { MAX_BYTES, extOk } from "@/lib/upload";
import { cn } from "@/lib/utils";
import { DropIcon } from "@/components/drop-icon";
import { Button } from "@/components/ui/button";
import { V3Progress } from "@/components/ui/v3-progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function PodcastUpload() {
  const router = useRouter();
  const { data: vinhetasData, isLoading: vinhetasLoading } = useQuery({
    queryKey: ["vinhetas"],
    queryFn: api.getVinhetas,
  });
  const vinhetas = vinhetasData?.vinhetas ?? [];

  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState<{ text: string; error: boolean }>({ text: "", error: false });
  const [drag, setDrag] = useState(false);
  const [vinhetaId, setVinhetaId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [prog, setProg] = useState<{ pct: number; loaded: number; total: number; eta: number | null } | null>(
    null,
  );

  function pickFile(f: File | null) {
    setError("");
    if (!f) {
      setFile(null);
      setMeta({ text: "", error: false });
      return;
    }
    if (!extOk(f.name)) {
      setFile(null);
      setMeta({ text: "Formato não suportado. Use mp4, mov, avi, mkv ou webm.", error: true });
      return;
    }
    if (f.size > MAX_BYTES) {
      setFile(null);
      setMeta({ text: `Arquivo acima de 15GB (${fmtBytes(f.size)}).`, error: true });
      return;
    }
    setFile(f);
    setMeta({ text: fmtBytes(f.size), error: false });
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!file) return setError("Selecione um vídeo.");
    if (!vinhetaId) return setError("Escolha uma vinheta.");

    setSubmitting(true);
    setProg({ pct: 0, loaded: 0, total: file.size, eta: null });
    const start = Date.now();
    try {
      const res = await api.podcastUploadWithProgress(file, vinhetaId, ({ loaded, total }) => {
        const elapsed = (Date.now() - start) / 1000;
        const speed = elapsed > 0 ? loaded / elapsed : 0;
        const eta = speed > 0 && total > loaded ? (total - loaded) / speed : null;
        setProg({ pct: total ? Math.round((loaded / total) * 100) : 0, loaded, total, eta });
      });
      router.push("/podcast/" + res.job_id);
    } catch (ex) {
      setError(ex instanceof api.ApiError ? ex.message : "Erro ao enviar o episódio.");
      setSubmitting(false);
      setProg(null);
    }
  }

  return (
    <section className="overflow-hidden rounded-[var(--radius-md)] border border-v3-border bg-v3-surface-2">
      <form onSubmit={onSubmit}>
        <div className="border-b border-v3-border p-6">
          <h3 className="font-v3-display text-lg font-black uppercase text-white">Enviar episódio</h3>
          <p className="mt-1 text-sm text-v3-text-soft">
            Envie o episódio em 16:9 com frame rate constante e escolha a vinheta de abertura.
          </p>
        </div>

        <div className="p-6">
          <label
            className={cn(
              "group flex cursor-pointer flex-col items-center gap-1.5 rounded-[var(--radius-md)] border border-dashed border-v3-border-hover px-6 py-11 text-center transition-colors",
              drag && "border-v3-accent bg-v3-accent/5",
            )}
            htmlFor="podcast-file"
            onDragEnter={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              pickFile(e.dataTransfer.files?.[0] ?? null);
            }}
          >
            <span
              className={cn(
                "mb-2.5 grid size-14 place-items-center rounded-[var(--radius-md)] border border-v3-border-hover text-v3-text-soft transition-colors group-hover:border-v3-accent group-hover:text-v3-accent",
                drag && "border-v3-accent text-v3-accent",
              )}
              aria-hidden="true"
            >
              <DropIcon />
            </span>
            <span className="font-v3-sans text-lg font-bold text-white">
              Arraste seu vídeo ou clique para enviar
            </span>
            <span className="font-mono text-xs text-v3-text-soft">mp4, mov, avi, mkv, webm · 16:9 · até 15GB</span>
            {file && <span className="mt-1 font-mono text-xs font-semibold text-v3-accent">{file.name}</span>}
            {meta.text && (
              <span
                className={cn(
                  "mt-1 font-mono text-xs font-semibold",
                  meta.error ? "text-[var(--red)]" : "text-v3-accent",
                )}
              >
                {meta.text}
              </span>
            )}
            <input
              type="file"
              id="podcast-file"
              name="video"
              accept=".mp4,.mov,.avi,.mkv,.webm"
              className="sr-only"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />
          </label>
        </div>

        <div className="p-6 pt-0">
          <p className="mb-3 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-v3-text-soft">
            Vinheta de abertura
          </p>
          {vinhetasLoading ? (
            <p className="font-mono text-sm text-v3-text-soft">Carregando vinhetas...</p>
          ) : vinhetas.length === 0 ? (
            <p className="font-mono text-sm text-v3-text-soft">
              Não há vinheta cadastrada ainda. Fale com o admin pra cadastrar uma antes de enviar.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-4">
              <div>
                <label className="mb-1.5 block font-mono text-xs uppercase tracking-wide text-v3-text-soft">
                  Vinheta
                </label>
                <Select value={vinhetaId} onValueChange={setVinhetaId}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Escolha a vinheta" />
                  </SelectTrigger>
                  <SelectContent>
                    {vinhetas.map((v) => (
                      <SelectItem key={v.id} value={v.id}>
                        {v.label}
                        {v.duration != null ? ` · ${fmtSeconds(v.duration)}` : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          <div className="mt-6 flex flex-wrap items-center justify-between gap-5">
            {submitting ? (
              <div className="flex w-full flex-col gap-2.5">
                <div className="flex items-baseline justify-between font-mono text-sm text-v3-text-soft">
                  <span>{(prog?.pct ?? 0) >= 100 ? "Quase lá, preparando o processamento" : "Enviando episódio"}</span>
                  <strong className="text-base font-bold text-v3-accent">{prog?.pct ?? 0}%</strong>
                </div>
                <V3Progress value={prog?.pct ?? 0} />
                <div className="flex items-center justify-between font-mono text-xs text-v3-text-soft">
                  <span>{prog ? `${fmtBytes(prog.loaded)} / ${fmtBytes(prog.total)}` : ""}</span>
                  <span>
                    {(prog?.pct ?? 0) >= 100
                      ? "processando…"
                      : prog?.eta != null
                        ? `~${fmtEta(prog.eta)} restante`
                        : "calculando tempo…"}
                  </span>
                </div>
              </div>
            ) : (
              <Button type="submit" variant="v3Cta" size="brand" disabled={vinhetas.length === 0}>
                <Sparkles className="size-4" aria-hidden="true" />
                Enviar episódio
              </Button>
            )}
          </div>
        </div>
      </form>
      {error && <div className="px-6 pb-4 font-mono text-sm font-medium text-[var(--red)]">{error}</div>}
    </section>
  );
}
