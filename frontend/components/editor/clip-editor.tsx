"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Download } from "lucide-react";
import * as api from "@/lib/api";
import { useFilmstrip } from "@/hooks/use-filmstrip";
import { useWaveform } from "@/hooks/use-waveform";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { PreviewStage } from "./preview-stage";
import { TranscriptPanel } from "./transcript-panel";
import { EditorTimeline } from "./editor-timeline";
import type { Music, Segment } from "@/lib/types";

function MusicControl({
  music,
  volume,
  onUpload,
  onRemove,
  onVolume,
}: {
  music: Music | null;
  volume: number;
  onUpload: (f: File) => void;
  onRemove: () => void;
  onVolume: (v: number) => void;
}) {
  return (
    <div className="mt-3 border-t border-[var(--border)] pt-3">
      <div className="editor-sec-head">
        <strong className="text-[0.85rem]">Música de fundo</strong>
      </div>
      {music?.filename ? (
        <>
          <div className="editor-music-has">
            <span className="editor-music-name">🎵 {music.filename}</span>
            <Button variant="brandGhost" size="sm" onClick={onRemove}>
              Remover
            </Button>
          </div>
          <label className="editor-vol">
            Volume
            <Slider
              min={0}
              max={100}
              value={[volume]}
              onValueChange={(v) => onVolume(v[0])}
              className="flex-1"
            />
          </label>
        </>
      ) : (
        <label className="editor-music-add">
          + Adicionar música
          <input
            type="file"
            accept="audio/*,.mp3,.m4a,.aac,.wav,.ogg"
            className="sr-only"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUpload(f);
            }}
          />
        </label>
      )}
    </div>
  );
}

// Posição vertical padrão da legenda (fração do centro, do topo). Aproxima o padrão
// do estilo SB no backend (alignment 2 + MARGIN_V 580 de 1920 => legenda na parte baixa).
const SB_DEFAULT_Y = 0.67;

export function ClipEditor({ clipId, title }: { clipId: string; title?: string }) {
  const qc = useQueryClient();
  const router = useRouter();
  const enc = encodeURIComponent(clipId);
  const baseSrc = `/api/clips/${enc}/base`;
  const burnedSrc = `/api/clips/${enc}/video`;
  const jobId = clipId.slice(0, clipId.lastIndexOf(":")) || clipId;
  const projectHref = `/projetos/${jobId}`;

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["captions", clipId],
    queryFn: () => api.getCaptions(clipId),
    enabled: !!clipId,
  });

  const [segments, setSegments] = useState<Segment[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [music, setMusic] = useState<Music | null>(null);
  const [volume, setVolume] = useState(15);
  const [captionsOn, setCaptionsOn] = useState(true);
  const [captionY, setCaptionY] = useState(SB_DEFAULT_Y);
  const [captionMoved, setCaptionMoved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [usingBurned, setUsingBurned] = useState(false);
  // Destino pendente quando o usuario tenta sair com alteracoes nao salvas (null = sem dialogo).
  const [leaveTo, setLeaveTo] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [pxPerSec, setPxPerSec] = useState(14);

  const editable = data?.editable ?? false;
  const previewSrc = usingBurned ? burnedSrc : baseSrc;
  const frames = useFilmstrip(usingBurned ? null : baseSrc, duration, 12);
  const peaks = useWaveform(usingBurned ? null : baseSrc);

  useEffect(() => {
    if (!data) return;
    setSegments(data.segments?.length ? data.segments.map((s) => ({ ...s })) : []);
    setMusic(data.music ?? null);
    setVolume(Math.round((data.music?.volume ?? 0.15) * 100));
    if (data.caption_y != null) {
      setCaptionY(data.caption_y);
      setCaptionMoved(true);
    } else {
      setCaptionY(SB_DEFAULT_Y);
      setCaptionMoved(false);
    }
  }, [data]);

  // Aviso nativo do navegador ao recarregar / fechar a aba / sair pra fora do app
  // com alteracoes nao salvas. (Navegacao SPA pelo botao Voltar do editor e tratada
  // pelo onNavigate do Link + ConfirmDialog abaixo.)
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  // Atalhos: espaco = play/pause, setas = seek (Shift = 5s). Ignora quando digitando
  // num campo de texto ou com um dialogo aberto. Usa videoRef direto (sem stale closure).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || el?.isContentEditable) return;
      if (el?.closest?.('[role="dialog"]')) return;
      const v = videoRef.current;
      if (e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if (e.code === "ArrowLeft" && v) {
        e.preventDefault();
        seek(Math.max(0, v.currentTime - (e.shiftKey ? 5 : 1)));
      } else if (e.code === "ArrowRight" && v) {
        e.preventDefault();
        seek(v.currentTime + (e.shiftKey ? 5 : 1));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Eventos do video + playhead suave (rAF enquanto toca).
  // `editable` entra nas deps porque o <video> so monta quando o clipe e editavel (a query
  // resolveu). Sem isso, o efeito roda no 1o render (data carregando -> video ainda nao
  // montado -> videoRef.current null -> sai no early-return) e nunca reanexa os listeners,
  // deixando currentTime/duration travados em 0 (a legenda nao acompanha o player).
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onMeta = () => setDuration(v.duration || 0);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onTime = () => setCurrentTime(v.currentTime);
    v.addEventListener("loadedmetadata", onMeta);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("ended", onPause);
    v.addEventListener("timeupdate", onTime);
    // Corrida: se os metadados ja carregaram antes do listener anexar, sincroniza na hora.
    if (v.readyState >= 1) {
      setDuration(v.duration || 0);
      setCurrentTime(v.currentTime);
    }
    return () => {
      v.removeEventListener("loadedmetadata", onMeta);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("ended", onPause);
      v.removeEventListener("timeupdate", onTime);
    };
  }, [previewSrc, editable]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) setCurrentTime(v.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  function seek(t: number) {
    const v = videoRef.current;
    if (v) v.currentTime = t;
    setCurrentTime(t);
  }
  function togglePlay() {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  }

  function patchSegment(i: number, patch: Partial<Segment>) {
    setSegments((s) => s.map((row, j) => (j === i ? { ...row, ...patch } : row)));
    setDirty(true);
  }

  const r2 = (x: number) => Math.round(x * 100) / 100;

  // Marca o inicio/fim da linha no instante atual do player (sem precisar arrastar blocos).
  function setSegEdge(i: number, edge: "start" | "end") {
    const t = r2(currentTime);
    setSegments((arr) =>
      arr.map((row, j) => {
        if (j !== i) return row;
        return edge === "start"
          ? { ...row, start: Math.max(0, Math.min(t, r2(row.end - 0.1))) }
          : { ...row, end: Math.max(r2(row.start + 0.1), t) };
      }),
    );
    setDirty(true);
  }

  // Ajuste fino de +-0.1s no inicio/fim da linha.
  function nudgeSeg(i: number, edge: "start" | "end", delta: number) {
    setSegments((arr) =>
      arr.map((row, j) => {
        if (j !== i) return row;
        return edge === "start"
          ? { ...row, start: r2(Math.max(0, Math.min(row.start + delta, row.end - 0.1))) }
          : { ...row, end: r2(Math.max(row.start + 0.1, row.end + delta)) };
      }),
    );
    setDirty(true);
  }

  const activeIndex = (() => {
    const i = segments.findIndex((s) => currentTime >= s.start && currentTime < s.end);
    return i >= 0 ? i : null;
  })();

  const activeText = captionsOn && !usingBurned && activeIndex != null ? segments[activeIndex].text : null;

  async function onUploadMusic(f: File) {
    toast.info("Enviando música…");
    try {
      const res = await api.uploadMusic(clipId, f, 0.15);
      setMusic(res.music ?? { filename: f.name, volume: 0.15 });
      setVolume(Math.round((res.music?.volume ?? 0.15) * 100));
      toast.success("Música adicionada. Salve e o vídeo re-renderiza no próximo play.");
    } catch (e) {
      toast.error((e as Error)?.message || "Falha ao enviar música.");
    }
  }
  async function onRemoveMusic() {
    try {
      await api.removeMusic(clipId);
      setMusic(null);
      toast.success("Música removida.");
    } catch {
      toast.error("Falha ao remover música.");
    }
  }

  async function save() {
    const out: Segment[] = [];
    let bad: number | null = null;
    segments.forEach((r, i) => {
      const st = Number(r.start);
      const en = Number(r.end);
      const tx = (r.text || "").trim();
      if (isNaN(st) || isNaN(en) || en <= st) {
        if (bad === null) bad = i + 1;
        return;
      }
      if (!tx) return;
      out.push({ start: st, end: en, text: tx });
    });
    if (bad !== null) {
      toast.error(`Linha ${bad}: o fim precisa ser maior que o início.`);
      return;
    }
    setSaving(true);
    try {
      const tasks: Promise<unknown>[] = [
        api.putCaptions(clipId, out, captionMoved ? captionY : null),
      ];
      if (music) tasks.push(api.setMusicVolume(clipId, Math.max(0, Math.min(1, volume / 100))));
      await Promise.all(tasks);
      qc.invalidateQueries({ queryKey: ["captions", clipId] });
      setDirty(false);
      toast.success("Salvo! O vídeo re-renderiza no próximo play.");
    } catch (e) {
      toast.error((e as Error)?.message || "Falha ao salvar.");
    } finally {
      setSaving(false);
    }
  }

  function exportClip() {
    // Com legenda -> versao queimada; Sem legenda -> base limpa.
    const url = captionsOn ? burnedSrc : baseSrc;
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    a.target = "_blank";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  return (
    <div className="editor-shell">
      <header className="editor-topbar">
        <Link
          href={projectHref}
          className="editor-icon-btn"
          aria-label="Voltar"
          onNavigate={(e) => {
            if (dirty) {
              e.preventDefault();
              setLeaveTo(projectHref);
            }
          }}
        >
          <ArrowLeft className="size-4" />
        </Link>
        <span className="editor-title">{title || "Editar clipe"}</span>

        <div className="ml-2 flex items-center gap-1 rounded-full border border-[var(--border)] p-1">
          <button
            type="button"
            className={cn("chip !rounded-full !border-0 !px-3 !py-1", captionsOn && "is-active")}
            onClick={() => setCaptionsOn(true)}
          >
            Com legenda
          </button>
          <button
            type="button"
            className={cn("chip !rounded-full !border-0 !px-3 !py-1", !captionsOn && "is-active")}
            onClick={() => setCaptionsOn(false)}
          >
            Sem legenda
          </button>
        </div>

        <div className="flex-1" />
        <Button variant="brandGhost" onClick={save} disabled={saving || !editable}>
          {saving ? "Salvando…" : "Salvar alterações"}
        </Button>
        <Button variant="gold" onClick={exportClip}>
          <Download className="size-4" /> Exportar
        </Button>
      </header>

      {isLoading ? (
        <div className="flex flex-1 items-center justify-center">
          <span className="clip-prep-spin" aria-label="Carregando" />
        </div>
      ) : isError ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <div className="empty max-w-md">
            <p className="empty-title">Não consegui carregar este clipe</p>
            <p className="empty-text">{(error as Error)?.message || "Tente novamente em instantes."}</p>
            <Link href={`/projetos/${jobId}`} className="link-gold mt-4 inline-block">
              ← Voltar ao projeto
            </Link>
          </div>
        </div>
      ) : !editable ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <div className="empty max-w-md">
            <p className="empty-title">Edição indisponível</p>
            <p className="empty-text">
              Este clipe foi gerado antes do editor (legenda fixa no vídeo) ou não tem fala.
              Reprocessar o projeto gera clipes editáveis. Você ainda pode exportar pela página
              do projeto.
            </p>
            <Link href={`/projetos/${jobId}`} className="link-gold mt-4 inline-block">
              ← Voltar ao projeto
            </Link>
          </div>
        </div>
      ) : (
        <>
          <div className="editor-main">
            <div className="editor-transcript">
              <TranscriptPanel
                segments={segments}
                selected={selected}
                activeIndex={activeIndex}
                currentTime={currentTime}
                playing={playing}
                onSelect={(i) => {
                  setSelected(i);
                  seek(segments[i].start);
                }}
                onChangeText={(i, text) => patchSegment(i, { text })}
                onDelete={(i) => {
                  setSegments((s) => s.filter((_, j) => j !== i));
                  setSelected((cur) => (cur === i ? null : cur != null && cur > i ? cur - 1 : cur));
                  setDirty(true);
                }}
                onAdd={() => {
                  const at = r2(currentTime);
                  setSegments((s) => [...s, { start: at, end: at + 2, text: "" }]);
                  setDirty(true);
                }}
                onSetEdge={setSegEdge}
                onNudge={nudgeSeg}
              />
              <MusicControl
                music={music}
                volume={volume}
                onUpload={onUploadMusic}
                onRemove={onRemoveMusic}
                onVolume={(v) => {
                  setVolume(v);
                  setDirty(true);
                }}
              />
            </div>

            <PreviewStage
              videoRef={videoRef}
              src={previewSrc}
              captionText={activeText}
              captionY={captionY}
              onChangeY={
                captionsOn && !usingBurned
                  ? (y) => {
                      setCaptionY(y);
                      setCaptionMoved(true);
                      setDirty(true);
                    }
                  : undefined
              }
              barExtra={
                captionsOn && !usingBurned ? (
                  <>
                    <span className="text-ink-muted">arraste a legenda p/ posicionar</span>
                    {captionMoved && (
                      <button
                        type="button"
                        className="text-gold underline-offset-2 hover:underline"
                        onClick={() => {
                          setCaptionY(SB_DEFAULT_Y);
                          setCaptionMoved(false);
                          setDirty(true);
                        }}
                      >
                        Resetar posição
                      </button>
                    )}
                  </>
                ) : null
              }
              onError={() => {
                if (!usingBurned) setUsingBurned(true);
              }}
              onTogglePlay={togglePlay}
            />
          </div>

          <EditorTimeline
            duration={duration}
            currentTime={currentTime}
            playing={playing}
            segments={segments}
            selected={selected}
            pxPerSec={pxPerSec}
            frames={frames}
            peaks={peaks}
            onSeek={seek}
            onPlayPause={togglePlay}
            onSelect={setSelected}
            onChangeSegment={(i, patch) => patchSegment(i, patch)}
            onZoom={setPxPerSec}
          />
        </>
      )}

      {usingBurned && (
        <p className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full bg-[var(--surface-2)] px-3 py-1 text-[0.72rem] text-ink-muted">
          Preview ao vivo indisponível (reinicie o backend p/ habilitar a rota do vídeo base).
        </p>
      )}

      <ConfirmDialog
        open={leaveTo !== null}
        onOpenChange={(v) => {
          if (!v) setLeaveTo(null);
        }}
        title="Sair sem salvar?"
        message="Você tem alterações não salvas neste clipe. Se sair agora, elas serão perdidas."
        okLabel="Sair sem salvar"
        cancelLabel="Continuar editando"
        danger
        onConfirm={() => {
          const dest = leaveTo;
          setLeaveTo(null);
          setDirty(false); // libera o onNavigate p/ a navegacao programatica abaixo
          if (dest) router.push(dest);
        }}
      />
    </div>
  );
}
