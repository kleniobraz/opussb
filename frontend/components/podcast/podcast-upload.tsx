"use client";

// Upload do episódio de podcast (§39): estaciona o job em 'uploaded' (POST /api/podcast/upload)
// e leva pra tela de aprovação do gancho, de onde o disparo da transcrição acontece. Reaproveita
// o padrão visual da dropzone do UploadConsole, mas sem os controles de clipe (aspecto/duração/
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
    <section className="upload-card">
      <form onSubmit={onSubmit}>
        <div className="console-head">
          <div className="console-head-text">
            <h3 className="console-title">Enviar episódio</h3>
            <p className="console-sub">
              Envie o episódio em 16:9 com frame rate constante e escolha a vinheta de abertura.
            </p>
          </div>
        </div>

        <div className="console-input">
          <label
            className={cn("dropzone", drag && "is-dragover")}
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
            <span className="dropzone-icon" aria-hidden="true">
              <DropIcon />
            </span>
            <span className="dropzone-title">Arraste seu vídeo ou clique para enviar</span>
            <span className="dropzone-hint">mp4, mov, avi, mkv, webm · 16:9 · até 15GB</span>
            {file && <span className="dropzone-meta">{file.name}</span>}
            {meta.text && (
              <span className={cn("dropzone-meta", meta.error && "meta-error")}>{meta.text}</span>
            )}
            <input
              type="file"
              id="podcast-file"
              name="video"
              accept=".mp4,.mov,.avi,.mkv,.webm"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />
          </label>
        </div>

        <div className="console-body">
          <p className="console-eyebrow">Vinheta de abertura</p>
          {vinhetasLoading ? (
            <p className="text-sm text-[var(--ink-muted)]">Carregando vinhetas...</p>
          ) : vinhetas.length === 0 ? (
            <p className="text-sm text-[var(--ink-muted)]">
              Não há vinheta cadastrada ainda. Fale com o admin pra cadastrar uma antes de enviar.
            </p>
          ) : (
            <div className="console-grid">
              <div>
                <label className="ctrl-label">Vinheta</label>
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

          <div className="console-foot">
            {submitting ? (
              <div className="upload-progress">
                <div className="upload-progress-head">
                  <span>{(prog?.pct ?? 0) >= 100 ? "Quase lá, preparando o processamento" : "Enviando episódio"}</span>
                  <strong>{prog?.pct ?? 0}%</strong>
                </div>
                <div className="upload-track">
                  <div className="upload-bar" style={{ width: `${prog?.pct ?? 0}%` }} />
                </div>
                <div className="upload-progress-meta">
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
              <Button type="submit" variant="gold" size="brand" disabled={vinhetas.length === 0}>
                <Sparkles className="size-4" aria-hidden="true" />
                Enviar episódio
              </Button>
            )}
          </div>
        </div>
      </form>
      {error && <div className="error px-[24px] pb-3.5">{error}</div>}
    </section>
  );
}
