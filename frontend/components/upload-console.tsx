"use client";

import { useEffect, useRef, useState } from "react";
import { Upload, Sparkles } from "lucide-react";
import * as api from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { usePrefs } from "@/hooks/use-prefs";
import { cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Switch } from "./ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";

const ACCEPT_EXT = ["mp4", "mov", "avi", "mkv", "webm"];
const MAX_BYTES = 15 * 1024 * 1024 * 1024; // 15GB

const PRESETS = [
  { name: "Shorts", dur: "15-30s", min: 15, max: 30, aspect: "9:16" },
  { name: "Reels", dur: "20-60s", min: 20, max: 60, aspect: "9:16" },
  { name: "TikTok", dur: "30-90s", min: 30, max: 90, aspect: "9:16" },
  { name: "Longo", dur: "1-3min", min: 60, max: 180, aspect: "16:9" },
];

const ASPECTS = [
  { value: "9:16", label: "9:16 · Vertical" },
  { value: "1:1", label: "1:1 · Quadrado" },
  { value: "16:9", label: "16:9 · Horizontal" },
];

// YouTube temporariamente OFF (bloqueio anti-robô intermitente do IP de datacenter; ver
// BACKEND §17). A stack funciona, mas não é confiável ainda. Flip pra true reativa tudo.
const YOUTUBE_ENABLED = false;

function extOk(name: string) {
  const m = /\.([a-z0-9]+)$/i.exec(name || "");
  return !!m && ACCEPT_EXT.includes(m[1].toLowerCase());
}

function fmtEta(sec: number): string {
  if (!isFinite(sec) || sec < 0) return "—";
  const s = Math.round(sec);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m} min ${r} s` : `${m} min`;
}

const DropIcon = () => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M20 16.5A4.5 4.5 0 0 0 17.5 8h-1.3A7 7 0 1 0 4 14.9" />
    <polyline points="9 13 12 10 15 13" />
    <line x1="12" y1="10" x2="12" y2="20" />
  </svg>
);

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
    <section className="upload-card">
      <form onSubmit={onSubmit}>
        <div className="console-head">
          <div className="console-head-text">
            <h3 className="console-title">Gerar novos clipes</h3>
            <p className="console-sub">Escolha a origem do vídeo e ajuste o formato de saída.</p>
          </div>
          <div className="src-seg" role="group" aria-label="Fonte do vídeo" data-mode={mode}>
            <span className="src-seg-thumb" aria-hidden="true" />
            <button
              type="button"
              className="src-seg-btn"
              data-on={mode === "file"}
              aria-pressed={mode === "file"}
              onClick={() => {
                setMode("file");
                setError("");
              }}
            >
              <Upload className="src-seg-ico" aria-hidden="true" />
              Enviar arquivo
            </button>
            <button
              type="button"
              className="src-seg-btn"
              data-on={mode === "youtube"}
              aria-pressed={mode === "youtube"}
              disabled={!YOUTUBE_ENABLED}
              title={!YOUTUBE_ENABLED ? "Em breve" : undefined}
              onClick={() => {
                if (!YOUTUBE_ENABLED) return;
                setMode("youtube");
                setError("");
              }}
            >
              <YoutubeGlyph className="src-seg-ico" />
              Link do YouTube
              {!YOUTUBE_ENABLED && <span className="src-seg-soon">Em breve</span>}
            </button>
          </div>
        </div>

        <div className="console-input">
          {mode === "file" ? (
            <label
              className={cn("dropzone", drag && "is-dragover")}
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
              <span className="dropzone-icon" aria-hidden="true">
                <DropIcon />
              </span>
              <span className="dropzone-title">Arraste seu vídeo ou clique para enviar</span>
              <span className="dropzone-hint">mp4, mov, avi, mkv, webm · até 15GB</span>
              {file && <span className="dropzone-meta">{file.name}</span>}
              {meta.text && (
                <span className={cn("dropzone-meta", meta.error && "meta-error")}>{meta.text}</span>
              )}
              <input
                type="file"
                id="video-file"
                name="video"
                accept=".mp4,.mov,.avi,.mkv,.webm"
                onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
              />
            </label>
          ) : (
            <div className="yt-input-wrap">
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
              <span className="dropzone-hint">
                Vídeos e lives já encerradas. O servidor baixa e gera os clipes automaticamente.
              </span>
            </div>
          )}
        </div>

        <div className="console-body">
          <p className="console-eyebrow">Predefinição</p>
          <div className="presets" role="group" aria-label="Predefinições de formato">
            {PRESETS.map((p, i) => (
              <button
                key={p.name}
                type="button"
                className={cn("chip", "chip-preset", activePreset === i && "is-active")}
                onClick={() => applyPreset(i)}
              >
                <span>{p.name}</span>
                <span className="chip-sub">{p.dur}</span>
              </button>
            ))}
          </div>

          <div className="console-grid">
            <div>
              <label className="ctrl-label" htmlFor="min-duration">
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
              <label className="ctrl-label" htmlFor="max-duration">
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
              <label className="ctrl-label">Formato</label>
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
              <label className="ctrl-label">Idioma</label>
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

          <div className="console-foot">
            {submitting ? (
              mode === "youtube" ? (
                <div className="upload-progress">
                  <div className="upload-progress-head">
                    <span>Validando o link e baixando do YouTube…</span>
                    <span className="clip-prep-spin" aria-hidden="true" />
                  </div>
                </div>
              ) : (
                <div className="upload-progress">
                  <div className="upload-progress-head">
                    <span>{(prog?.pct ?? 0) >= 100 ? "Quase lá, preparando o processamento" : "Enviando vídeo"}</span>
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
              )
            ) : (
              <>
                <label className="flex cursor-pointer items-center gap-3 text-[0.9rem] text-ink-soft">
                  <Switch checked={captions} onCheckedChange={setCaptions} />
                  <span>Gerar legendas embutidas</span>
                </label>
                <Button type="submit" variant="gold" size="brand">
                  <Sparkles className="size-4" aria-hidden="true" />
                  Gerar Clipes
                </Button>
              </>
            )}
          </div>
        </div>
      </form>
      {error && <div className="error px-[24px] pb-3.5">{error}</div>}
    </section>
  );
}
