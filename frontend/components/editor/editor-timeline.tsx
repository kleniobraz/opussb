"use client";

import { useEffect, useRef } from "react";
import { Pause, Play, SkipBack, SkipForward, ZoomIn, ZoomOut } from "lucide-react";
import type { Frame } from "@/hooks/use-filmstrip";
import type { Segment } from "@/lib/types";

function fmtClock(t: number): string {
  if (!isFinite(t)) t = 0;
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  const cs = Math.floor((t * 100) % 100);
  return `${m}:${String(s).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
}

// Imanta `value` ao alvo mais proximo dentro de `tol` (segundos). Facilita alinhar a
// legenda ao playhead e as bordas dos blocos vizinhos sem precisar de precisao no mouse.
function snap(value: number, targets: number[], tol: number): number {
  let best = value;
  let bestD = tol;
  for (const t of targets) {
    const d = Math.abs(t - value);
    if (d < bestD) {
      bestD = d;
      best = t;
    }
  }
  return best;
}

type DragState = {
  i: number;
  mode: "move" | "left" | "right";
  startX: number;
  start: number;
  end: number;
} | null;

export function EditorTimeline({
  duration,
  currentTime,
  playing,
  segments,
  selected,
  pxPerSec,
  frames,
  peaks,
  onSeek,
  onPlayPause,
  onSelect,
  onChangeSegment,
  onZoom,
}: {
  duration: number;
  currentTime: number;
  playing: boolean;
  segments: Segment[];
  selected: number | null;
  pxPerSec: number;
  frames: Frame[];
  peaks: number[] | null;
  onSeek: (t: number) => void;
  onPlayPause: () => void;
  onSelect: (i: number) => void;
  onChangeSegment: (i: number, patch: { start: number; end: number }) => void;
  onZoom: (px: number) => void;
}) {
  const tracksRef = useRef<HTMLDivElement>(null);
  const waveRef = useRef<HTMLCanvasElement>(null);
  const drag = useRef<DragState>(null);
  const dur = duration && isFinite(duration) ? duration : 0;
  const trackWidth = Math.max(dur * pxPerSec, 320);

  function timeFromClientX(clientX: number) {
    const el = tracksRef.current;
    if (!el) return 0;
    const rect = el.getBoundingClientRect();
    return Math.max(0, Math.min(dur, (clientX - rect.left) / pxPerSec));
  }

  // Scrub na area das trilhas (blocos param a propagacao).
  function onTracksPointerDown(e: React.PointerEvent) {
    onSeek(timeFromClientX(e.clientX));
    const move = (ev: PointerEvent) => onSeek(timeFromClientX(ev.clientX));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function startDrag(e: React.PointerEvent, i: number, mode: "move" | "left" | "right") {
    e.stopPropagation();
    const s = segments[i];
    drag.current = { i, mode, startX: e.clientX, start: s.start, end: s.end };
    const tol = 8 / pxPerSec; // ~8px de imã
    const targets = [currentTime, 0, dur];
    segments.forEach((seg, j) => {
      if (j !== i) targets.push(seg.start, seg.end);
    });
    const move = (ev: PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      const ds = (ev.clientX - d.startX) / pxPerSec;
      let start = d.start;
      let end = d.end;
      if (d.mode === "move") {
        const w = d.end - d.start;
        start = Math.max(0, Math.min(snap(d.start + ds, targets, tol), dur - w));
        end = start + w;
      } else if (d.mode === "left") {
        start = Math.max(0, Math.min(snap(d.start + ds, targets, tol), d.end - 0.1));
      } else {
        end = Math.min(dur, Math.max(snap(d.end + ds, targets, tol), d.start + 0.1));
      }
      onChangeSegment(d.i, { start: Math.round(start * 100) / 100, end: Math.round(end * 100) / 100 });
    };
    const up = () => {
      drag.current = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // Desenha a waveform (ou trilha neutra se nao houver peaks por CORS).
  useEffect(() => {
    const canvas = waveRef.current;
    if (!canvas) return;
    const w = Math.round(trackWidth);
    const h = canvas.clientHeight || 46;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    if (!peaks || peaks.length === 0) {
      ctx.fillStyle = "rgba(255,255,255,0.08)";
      ctx.fillRect(0, h / 2 - 1, w, 2);
      return;
    }
    ctx.fillStyle = "rgba(245,197,24,0.5)";
    const step = 2;
    for (let x = 0; x < w; x += step) {
      const idx = Math.floor((x / w) * peaks.length);
      const peak = peaks[idx] || 0;
      const bh = Math.max(1, peak * (h - 4));
      ctx.fillRect(x, (h - bh) / 2, 1.4, bh);
    }
  }, [peaks, trackWidth]);

  // Ticks da regua: intervalo conforme o zoom.
  const interval = pxPerSec >= 14 ? 1 : pxPerSec >= 7 ? 2 : 5;
  const ticks: number[] = [];
  for (let t = 0; t <= dur; t += interval) ticks.push(t);

  // Bloco sendo falado agora (destaque karaoke, distinto da selecao).
  const activeIdx = segments.findIndex((s) => currentTime >= s.start && currentTime < s.end);

  return (
    <div className="editor-timeline">
      <div className="editor-transport">
        <button className="editor-icon-btn" onClick={() => onSeek(0)} aria-label="Início" title="Ir pro início">
          <SkipBack className="size-4" />
        </button>
        <button
          className="editor-play-btn"
          onClick={onPlayPause}
          aria-label={playing ? "Pausar" : "Tocar"}
          title={playing ? "Pausar (espaço)" : "Tocar (espaço)"}
        >
          {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
        </button>
        <button className="editor-icon-btn" onClick={() => onSeek(dur)} aria-label="Fim" title="Ir pro fim">
          <SkipForward className="size-4" />
        </button>
        <span className="editor-transport-time">
          {fmtClock(currentTime)} / {fmtClock(dur)}
        </span>
        <div className="editor-transport-spacer" />
        <span className="editor-transport-hint">espaço: tocar · ← →: navegar (shift = 5s)</span>
        <ZoomOut className="size-4 text-ink-muted" />
        <input
          type="range"
          min={4}
          max={48}
          value={pxPerSec}
          onChange={(e) => onZoom(parseInt(e.target.value, 10))}
          aria-label="Zoom da timeline"
          className="w-32 accent-[var(--gold)]"
        />
        <ZoomIn className="size-4 text-ink-muted" />
      </div>

      <div className="editor-tracks-scroll">
        <div
          className="editor-tracks"
          ref={tracksRef}
          style={{ width: trackWidth }}
          onPointerDown={onTracksPointerDown}
        >
          <div className="editor-ruler">
            {ticks.map((t) => (
              <div key={t} className="editor-ruler-tick" style={{ left: t * pxPerSec }}>
                {Math.round(t)}
              </div>
            ))}
          </div>

          <div className="editor-track editor-track--film">
            {frames.length > 0 &&
              frames.map((f, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={i}
                  src={f.url}
                  alt=""
                  className="editor-film-frame"
                  style={{ left: (i / frames.length) * trackWidth, width: trackWidth / frames.length }}
                />
              ))}
          </div>

          <div className="editor-track editor-track--wave">
            <canvas ref={waveRef} className="editor-wave-canvas" style={{ width: trackWidth }} />
          </div>

          <div className="editor-track editor-track--caps">
            {segments.map((s, i) => (
              <div
                key={i}
                className={`editor-cap-block${selected === i ? " is-selected" : ""}${activeIdx === i ? " is-active" : ""}`}
                style={{ left: s.start * pxPerSec, width: Math.max(8, (s.end - s.start) * pxPerSec) }}
                onPointerDown={(e) => startDrag(e, i, "move")}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(i);
                }}
                title={s.text}
              >
                <span className="editor-cap-block-text">{s.text || "(vazio)"}</span>
                <div
                  className="editor-cap-handle left"
                  onPointerDown={(e) => startDrag(e, i, "left")}
                />
                <div
                  className="editor-cap-handle right"
                  onPointerDown={(e) => startDrag(e, i, "right")}
                />
              </div>
            ))}
          </div>

          <div className="editor-playhead" style={{ left: currentTime * pxPerSec }} />
        </div>
      </div>
    </div>
  );
}
