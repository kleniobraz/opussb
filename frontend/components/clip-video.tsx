"use client";

import { useEffect, useRef, useState } from "react";
import { Expand, Maximize2, Pause, Play, Shrink, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";

// Tempo curto m:ss pra barra do player.
function fmtClock(t: number): string {
  if (!isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// Player do clipe com queima de legenda SOB DEMANDA (a 1a abertura faz 302 e demora alguns
// segundos; o overlay "preparando" fica por cima ate o video poder tocar).
//
// LAZY (anti-estampida): o `src` so e anexado ao <video> na PRIMEIRA intencao de tocar (clique no
// play), nao na renderizacao. Antes, a grade montava N players e cada <video> pedia
// /api/clips/<id>/video de cara -> N queimas de uma vez (estampida que sufocava a CPU). Agora cada
// clipe so dispara a queima quando o usuario o assiste, uma de cada vez. `preload="none"` reforca
// (nada de buffer ate o play). Quando `autoPlay` (dialogo), ja nasce ativado.
//
// Controles PROPRIOS (sem o chrome nativo do browser, que misturava play/volume/tela-cheia/PiP/
// menu de 3 pontos e confundia com os botoes do app): play/pause central grande + uma barra com
// play/pause, tempo, scrub, mudo e UM botao de expandir. `onExpand` define o expandir: no card
// abre o dialogo (passa onExpand); no dialogo, sem onExpand, ele faz tela cheia no proprio video.
export function ClipVideo({
  src,
  autoPlay = false,
  className,
  children,
  onExpand,
}: {
  src: string;
  autoPlay?: boolean;
  className?: string;
  children?: React.ReactNode;
  onExpand?: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // activated: o src ja foi anexado (a queima ja pode ter sido disparada). Em autoPlay nasce true.
  const [activated, setActivated] = useState(autoPlay);
  const wantPlayRef = useRef(false);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(0);
  const [isFs, setIsFs] = useState(false);

  // Acompanha o estado de tela cheia pra trocar o icone (expandir / recolher).
  useEffect(() => {
    const onFs = () => setIsFs(document.fullscreenElement === wrapRef.current);
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  // Quando o src e anexado por intencao do usuario (idle -> activated), toca assim que o elemento
  // remontar com o src. Em autoPlay o proprio atributo do <video> cuida (wantPlayRef fica false).
  useEffect(() => {
    if (activated && wantPlayRef.current) {
      wantPlayRef.current = false;
      videoRef.current?.play().catch(() => {});
    }
  }, [activated]);

  function toggle() {
    const v = videoRef.current;
    if (!activated) {
      // primeira intencao de tocar: anexa o src (so AGORA o backend queima) e toca quando puder.
      wantPlayRef.current = true;
      setActivated(true);
      return;
    }
    if (!v) return;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  }
  function toggleMute() {
    const v = videoRef.current;
    if (!v) return;
    v.muted = !v.muted;
    setMuted(v.muted);
  }
  function seekTo(t: number) {
    const v = videoRef.current;
    if (v) v.currentTime = t;
    setCur(t);
  }
  function expand() {
    if (onExpand) {
      onExpand();
      return;
    }
    const el = wrapRef.current;
    if (!el) return;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else el.requestFullscreen?.();
  }

  const pct = dur > 0 ? Math.min(100, (cur / dur) * 100) : 0;

  return (
    <div
      ref={wrapRef}
      className={cn("clip-video", ready && "is-ready", !playing && "is-paused", !activated && "is-idle", className)}
    >
      <video
        ref={videoRef}
        src={activated ? src : undefined}
        playsInline
        preload="none"
        autoPlay={autoPlay}
        onClick={toggle}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setCur(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => {
          setDur(e.currentTarget.duration || 0);
          setReady(true);
        }}
        onCanPlay={() => setReady(true)}
        onError={() => setReady(true)}
        onWaiting={() => setReady(false)}
        onVolumeChange={(e) => setMuted(e.currentTarget.muted)}
      />
      {children}

      {/* Play/pause central grande: o gesto principal, impossivel de errar. So aparece pausado. */}
      <button type="button" className="clip-bigplay" onClick={toggle} aria-label={playing ? "Pausar" : "Tocar"}>
        {playing ? <Pause /> : <Play />}
      </button>

      {/* Barra de controles propria. */}
      <div className="clip-controls">
        <input
          type="range"
          className="clip-seek"
          min={0}
          max={dur || 0}
          step={0.05}
          value={Math.min(cur, dur || 0)}
          onChange={(e) => seekTo(parseFloat(e.target.value))}
          style={{
            background: `linear-gradient(to right, var(--gold) ${pct}%, rgba(255,255,255,0.28) ${pct}%)`,
          }}
          aria-label="Avançar ou voltar"
        />
        <div className="clip-ctrl-row">
          <button type="button" className="clip-ctrl" onClick={toggle} aria-label={playing ? "Pausar" : "Tocar"}>
            {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
          </button>
          <span className="clip-time">
            {fmtClock(cur)} / {fmtClock(dur)}
          </span>
          <span className="clip-ctrl-spacer" />
          <button
            type="button"
            className="clip-ctrl"
            onClick={toggleMute}
            aria-label={muted ? "Ativar som" : "Silenciar"}
          >
            {muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
          </button>
          <button
            type="button"
            className="clip-ctrl"
            onClick={expand}
            aria-label={onExpand ? "Abrir clipe" : isFs ? "Sair da tela cheia" : "Tela cheia"}
            title={onExpand ? "Abrir clipe" : isFs ? "Sair da tela cheia" : "Tela cheia"}
          >
            {onExpand ? <Maximize2 className="size-4" /> : isFs ? <Shrink className="size-4" /> : <Expand className="size-4" />}
          </button>
        </div>
      </div>

      <div className="clip-prep" aria-hidden="true">
        <span className="clip-prep-spin" />
        <span className="clip-prep-note">preparando legenda…</span>
      </div>
    </div>
  );
}
