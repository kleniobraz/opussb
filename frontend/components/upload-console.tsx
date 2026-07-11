"use client";

import { useEffect, useRef, useState } from "react";
import { Upload, Sparkles } from "lucide-react";
import * as api from "@/lib/api";
import { fmtBytes, fmtEta } from "@/lib/format";
import { MAX_BYTES, extOk } from "@/lib/upload";
import { usePrefs } from "@/hooks/use-prefs";
import { cn } from "@/lib/utils";
import { DropIcon } from "./drop-icon";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Switch } from "./ui/switch";
import { V3Progress } from "./ui/v3-progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";

// Logos das redes nas predefinicoes (simple-icons, CC0). Monocromaticos: herdam a cor do chip
// (cinza quando inativo, dourado quando ativo), no mesmo padrao do glifo do Zoho/YouTube.
function InstagramGlyph({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M7.0301.084c-1.2768.0602-2.1487.264-2.911.5634-.7888.3075-1.4575.72-2.1228 1.3877-.6652.6677-1.075 1.3368-1.3802 2.127-.2954.7638-.4956 1.6365-.552 2.914-.0564 1.2775-.0689 1.6882-.0626 4.947.0062 3.2586.0206 3.6671.0825 4.9473.061 1.2765.264 2.1482.5635 2.9107.308.7889.72 1.4573 1.388 2.1228.6679.6655 1.3365 1.0743 2.1285 1.38.7632.295 1.6361.4961 2.9134.552 1.2773.056 1.6884.069 4.9462.0627 3.2578-.0062 3.668-.0207 4.9478-.0814 1.28-.0607 2.147-.2652 2.9098-.5633.7889-.3086 1.4578-.72 2.1228-1.3881.665-.6682 1.0745-1.3378 1.3795-2.1284.2957-.7632.4966-1.636.552-2.9124.056-1.2809.0692-1.6898.063-4.948-.0063-3.2583-.021-3.6668-.0817-4.9465-.0607-1.2797-.264-2.1487-.5633-2.9117-.3084-.7889-.72-1.4568-1.3876-2.1228C21.2982 1.33 20.628.9208 19.8378.6165 19.074.321 18.2017.1197 16.9244.0645 15.6471.0093 15.236-.005 11.977.0014 8.718.0076 8.31.0215 7.0301.0839m.1402 21.6932c-1.17-.0509-1.8053-.2453-2.2287-.408-.5606-.216-.96-.4771-1.3819-.895-.422-.4178-.6811-.8186-.9-1.378-.1644-.4234-.3624-1.058-.4171-2.228-.0595-1.2645-.072-1.6442-.079-4.848-.007-3.2037.0053-3.583.0607-4.848.05-1.169.2456-1.805.408-2.2282.216-.5613.4762-.96.895-1.3816.4188-.4217.8184-.6814 1.3783-.9003.423-.1651 1.0575-.3614 2.227-.4171 1.2655-.06 1.6447-.072 4.848-.079 3.2033-.007 3.5835.005 4.8495.0608 1.169.0508 1.8053.2445 2.228.408.5608.216.96.4754 1.3816.895.4217.4194.6816.8176.9005 1.3787.1653.4217.3617 1.056.4169 2.2263.0602 1.2655.0739 1.645.0796 4.848.0058 3.203-.0055 3.5834-.061 4.848-.051 1.17-.245 1.8055-.408 2.2294-.216.5604-.4763.96-.8954 1.3814-.419.4215-.8181.6811-1.3783.9-.4224.1649-1.0577.3617-2.2262.4174-1.2656.0595-1.6448.072-4.8493.079-3.2045.007-3.5825-.006-4.848-.0608M16.953 5.5864A1.44 1.44 0 1 0 18.39 4.144a1.44 1.44 0 0 0-1.437 1.4424M5.8385 12.012c.0067 3.4032 2.7706 6.1557 6.173 6.1493 3.4026-.0065 6.157-2.7701 6.1506-6.1733-.0065-3.4032-2.771-6.1565-6.174-6.1498-3.403.0067-6.156 2.771-6.1496 6.1738M8 12.0077a4 4 0 1 1 4.008 3.9921A3.9996 3.9996 0 0 1 8 12.0077" />
    </svg>
  );
}

function TiktokGlyph({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z" />
    </svg>
  );
}

function ShortsGlyph({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="m18.931 9.99-1.441-.601 1.717-.913a4.48 4.48 0 0 0 1.874-6.078 4.506 4.506 0 0 0-6.09-1.874L4.792 5.929a4.504 4.504 0 0 0-2.402 4.193 4.521 4.521 0 0 0 2.666 3.904c.036.012 1.442.6 1.442.6l-1.706.901a4.51 4.51 0 0 0-2.369 3.967A4.528 4.528 0 0 0 6.93 24c.725 0 1.437-.174 2.08-.508l10.21-5.406a4.494 4.494 0 0 0 2.39-4.192 4.525 4.525 0 0 0-2.678-3.904ZM9.597 15.19V8.824l6.007 3.184z" />
    </svg>
  );
}

const PRESETS = [
  { name: "Shorts", dur: "15-30s", min: 15, max: 30, aspect: "9:16", Icon: ShortsGlyph },
  { name: "Reels", dur: "20-60s", min: 20, max: 60, aspect: "9:16", Icon: InstagramGlyph },
  { name: "TikTok", dur: "30-90s", min: 30, max: 90, aspect: "9:16", Icon: TiktokGlyph },
  { name: "Longo", dur: "1-3min", min: 60, max: 180, aspect: "16:9", Icon: YoutubeGlyph },
];

const ASPECTS = [
  { value: "9:16", label: "9:16 · Vertical" },
  { value: "1:1", label: "1:1 · Quadrado" },
  { value: "16:9", label: "16:9 · Horizontal" },
];

// YouTube temporariamente OFF (bloqueio anti-robô intermitente do IP de datacenter; ver
// BACKEND §17). A stack funciona, mas não é confiável ainda. Flip pra true reativa tudo.
const YOUTUBE_ENABLED = false;

// Glifo do YouTube (lucide desta versão não exporta Youtube). Monocromático: herda a cor
// do segmento (escuro sobre o thumb dourado quando ativo, cinza quando inativo).
function YoutubeGlyph({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
    </svg>
  );
}

export function UploadConsole({ onStarted }: { onStarted: (jobId: string, filename: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState<{ text: string; error: boolean }>({ text: "", error: false });
  const [drag, setDrag] = useState(false);
  const [mode, setMode] = useState<"file" | "youtube">("file");
  const [ytUrl, setYtUrl] = useState("");
  const [minD, setMinD] = useState(30);
  const [maxD, setMaxD] = useState(90);
  const [aspect, setAspect] = useState("9:16");
  const [language, setLanguage] = useState("pt");
  const [captions, setCaptions] = useState(true);
  const [activePreset, setActivePreset] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [prog, setProg] = useState<{ pct: number; loaded: number; total: number; eta: number | null } | null>(null);

  // Aplica os padroes salvos do usuario (uma vez, quando carregam).
  const { prefs, isLoaded } = usePrefs();
  const appliedPrefs = useRef(false);
  useEffect(() => {
    if (!isLoaded || appliedPrefs.current) return;
    appliedPrefs.current = true;
    setMinD(prefs.defaultMin);
    setMaxD(prefs.defaultMax);
    setAspect(prefs.aspect);
    setLanguage(prefs.language);
    setCaptions(prefs.captions);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoaded]);

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

  function applyPreset(i: number) {
    const p = PRESETS[i];
    setMinD(p.min);
    setMaxD(p.max);
    setAspect(p.aspect);
    setActivePreset(i);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (minD >= maxD) return setError("A duração mínima deve ser menor que a máxima.");

    if (mode === "youtube") {
      const url = ytUrl.trim();
      if (!url) return setError("Cole um link do YouTube.");
      setSubmitting(true);
      try {
        const res = await api.uploadYoutube(url, {
          minDuration: minD,
          maxDuration: maxD,
          aspectRatio: aspect,
          language,
          captions,
        });
        onStarted(res.job_id, res.title || "Vídeo do YouTube");
      } catch (ex) {
        setError((ex as Error)?.message || "Erro ao iniciar o link do YouTube.");
        setSubmitting(false);
      }
      return;
    }

    if (!file) return setError("Selecione um vídeo.");
    setSubmitting(true);
    setProg({ pct: 0, loaded: 0, total: file.size, eta: null });
    const start = Date.now();
    try {
      const res = await api.uploadWithProgress(
        file,
        { minDuration: minD, maxDuration: maxD, aspectRatio: aspect, language, captions },
        ({ loaded, total }) => {
          const elapsed = (Date.now() - start) / 1000;
          const speed = elapsed > 0 ? loaded / elapsed : 0; // bytes/s médios
          const eta = speed > 0 && total > loaded ? (total - loaded) / speed : null;
          setProg({ pct: total ? Math.round((loaded / total) * 100) : 0, loaded, total, eta });
        },
      );
      onStarted(res.job_id, file.name);
    } catch (ex) {
      setError((ex as Error)?.message || "Erro ao enviar vídeo.");
      setSubmitting(false);
      setProg(null);
    }
  }

  return (
    <section className="overflow-hidden rounded-[var(--radius-md)] border border-v3-border bg-v3-surface-2">
      <form onSubmit={onSubmit}>
        <div className="flex flex-wrap items-start justify-between gap-5 border-b border-v3-border p-6">
          <div className="min-w-0">
            <h3 className="font-v3-display text-lg font-black uppercase text-white">Gerar novos clipes</h3>
            <p className="mt-1 text-sm text-v3-text-soft">Escolha a origem do vídeo e ajuste o formato de saída.</p>
          </div>
          <div
            className="relative grid w-full grid-cols-2 rounded-full border border-v3-border-hover bg-v3-surface-1 p-1 sm:inline-grid sm:w-auto"
            role="group"
            aria-label="Fonte do vídeo"
          >
            <span
              aria-hidden="true"
              data-mode={mode}
              className="absolute inset-y-1 left-1 w-[calc(50%-4px)] rounded-full bg-v3-accent transition-transform duration-300 ease-out data-[mode=youtube]:translate-x-full"
            />
            <button
              type="button"
              className={cn(
                "relative z-[1] inline-flex min-w-0 items-center justify-center gap-1.5 rounded-full px-2.5 py-2 font-mono text-[0.68rem] font-bold uppercase tracking-wide transition-colors sm:gap-2 sm:px-4 sm:text-xs",
                mode === "file" ? "text-v3-on-accent" : "text-v3-text-soft hover:text-white",
              )}
              aria-pressed={mode === "file"}
              onClick={() => {
                setMode("file");
                setError("");
              }}
            >
              <Upload className="size-4 shrink-0" aria-hidden="true" />
              <span className="truncate">Enviar arquivo</span>
            </button>
            <button
              type="button"
              className={cn(
                "relative z-[1] inline-flex min-w-0 items-center justify-center gap-1.5 rounded-full px-2.5 py-2 font-mono text-[0.68rem] font-bold uppercase tracking-wide transition-colors sm:gap-2 sm:px-4 sm:text-xs",
                mode === "youtube" ? "text-v3-on-accent" : "text-v3-text-soft hover:text-white",
                !YOUTUBE_ENABLED && "cursor-not-allowed opacity-50",
              )}
              aria-pressed={mode === "youtube"}
              disabled={!YOUTUBE_ENABLED}
              title={!YOUTUBE_ENABLED ? "Em breve" : undefined}
              onClick={() => {
                if (!YOUTUBE_ENABLED) return;
                setMode("youtube");
                setError("");
              }}
            >
              <YoutubeGlyph className="size-4 shrink-0" />
              <span className="truncate">Link do YouTube</span>
              {!YOUTUBE_ENABLED && (
                <span className="hidden shrink-0 rounded-full border border-v3-accent/40 bg-v3-accent/15 px-1.5 py-0.5 font-mono text-[0.58rem] font-bold uppercase text-v3-accent sm:inline-flex">
                  Em breve
                </span>
              )}
            </button>
          </div>
        </div>

        <div className="p-6">
          {mode === "file" ? (
            <label
              className={cn(
                "group flex cursor-pointer flex-col items-center gap-1.5 rounded-[var(--radius-md)] border border-dashed border-v3-border-hover px-6 py-11 text-center transition-colors",
                drag && "border-v3-accent bg-v3-accent/5",
              )}
              htmlFor="video-file"
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
              <span className="font-mono text-xs text-v3-text-soft">mp4, mov, avi, mkv, webm · até 15GB</span>
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
                id="video-file"
                name="video"
                accept=".mp4,.mov,.avi,.mkv,.webm"
                className="sr-only"
                onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
              />
            </label>
          ) : (
            <div className="flex flex-col gap-2">
              <Input
                type="url"
                inputMode="url"
                placeholder="Cole o link do YouTube (vídeo ou live já encerrada)"
                value={ytUrl}
                onChange={(e) => {
                  setYtUrl(e.target.value);
                  setError("");
                }}
                aria-label="Link do YouTube"
              />
              <span className="font-mono text-xs text-v3-text-soft">
                Vídeos e lives já encerradas. O servidor baixa e gera os clipes automaticamente.
              </span>
            </div>
          )}
        </div>

        <div className="p-6 pt-0">
          <p className="mb-3 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-v3-text-soft">
            Predefinição
          </p>
          <div className="mb-6 flex flex-wrap gap-2.5" role="group" aria-label="Predefinições de formato">
            {PRESETS.map((p, i) => (
              <button
                key={p.name}
                type="button"
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-4 py-2 font-mono text-xs font-semibold uppercase tracking-wide transition-colors",
                  activePreset === i
                    ? "border-v3-accent bg-v3-accent text-v3-on-accent"
                    : "border-v3-border-hover text-v3-text-soft hover:border-v3-accent hover:text-white",
                )}
                onClick={() => applyPreset(i)}
              >
                <p.Icon className="size-3.5" />
                <span>{p.name}</span>
                <span className={cn("font-normal normal-case", activePreset === i ? "text-v3-on-accent/70" : "text-v3-text-soft")}>
                  {p.dur}
                </span>
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-4">
            <div>
              <label className="mb-2 block font-mono text-xs uppercase tracking-wide text-v3-text-soft" htmlFor="min-duration">
                Duração mín. (s)
              </label>
              <Input
                type="number"
                id="min-duration"
                value={minD}
                min={5}
                max={300}
                onChange={(e) => setMinD(parseInt(e.target.value, 10) || 0)}
              />
            </div>
            <div>
              <label className="mb-2 block font-mono text-xs uppercase tracking-wide text-v3-text-soft" htmlFor="max-duration">
                Duração máx. (s)
              </label>
              <Input
                type="number"
                id="max-duration"
                value={maxD}
                min={10}
                max={600}
                onChange={(e) => setMaxD(parseInt(e.target.value, 10) || 0)}
              />
            </div>
            <div>
              <label className="mb-2 block font-mono text-xs uppercase tracking-wide text-v3-text-soft">Formato</label>
              <Select value={aspect} onValueChange={setAspect}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ASPECTS.map((a) => (
                    <SelectItem key={a.value} value={a.value}>
                      {a.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="mb-2 block font-mono text-xs uppercase tracking-wide text-v3-text-soft">Idioma</label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="pt">Português</SelectItem>
                  <SelectItem value="en">Inglês</SelectItem>
                  <SelectItem value="es">Espanhol</SelectItem>
                  <SelectItem value="auto">Detectar</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="mt-6 flex flex-wrap items-center justify-between gap-5">
            {submitting ? (
              mode === "youtube" ? (
                <div className="flex w-full items-center gap-3 font-mono text-sm text-v3-text-soft">
                  <span>Validando o link e baixando do YouTube…</span>
                  <span className="clip-prep-spin" aria-hidden="true" />
                </div>
              ) : (
                <div className="flex w-full flex-col gap-2.5">
                  <div className="flex items-baseline justify-between font-mono text-sm text-v3-text-soft">
                    <span>{(prog?.pct ?? 0) >= 100 ? "Quase lá, preparando o processamento" : "Enviando vídeo"}</span>
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
              )
            ) : (
              <>
                <label className="flex cursor-pointer items-center gap-3 text-sm text-v3-text-soft">
                  <Switch checked={captions} onCheckedChange={setCaptions} className="data-checked:bg-v3-accent" />
                  <span>Gerar legendas embutidas</span>
                </label>
                <Button type="submit" variant="v3Cta" size="brand">
                  <Sparkles className="size-4" aria-hidden="true" />
                  Gerar Clipes
                </Button>
              </>
            )}
          </div>
        </div>
      </form>
      {error && <div className="px-6 pb-4 font-mono text-sm font-medium text-[var(--red)]">{error}</div>}
    </section>
  );
}
