"use client";

import { useEffect, useRef } from "react";
import { Maximize2, Minus, Pause, Play, Plus, SkipBack, SkipForward } from "lucide-react";
import type { EditSeg } from "./clip-editor";

const MIN_BLOCK_W = 26; // largura minima clicavel de um bloco, mesmo pra legenda curtissima
const ZOOM_MIN = 4;
const ZOOM_MAX = 48;
const DRAG_THRESHOLD = 4; // px que o ponteiro precisa andar antes de virar arraste (clique != mover)
const MIN_CUT = 1; // duracao minima do corte (s) ao arrastar as alcas; bate com _MIN_CUT_SECONDS no backend

function fmtClock(t: number): string {
  if (!isFinite(t)) t = 0;
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  const cs = Math.floor((t * 100) % 100);
  return `${m}:${String(s).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
}

const r2 = (x: number) => Math.round(x * 100) / 100;

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

export function EditorTimeline({
  duration,
  currentTime,
  playing,
  segments,
  selectedId,
  pxPerSec,
  filmstrip,
  peaks,
  hasHandles,
  cutIn,
  cutOut,
  endScreenLabel,
  onSeek,
  onPlayPause,
  onSelect,
  onChangeSegment,
  onChangeCut,
  onZoom,
}: {
  duration: number;
  currentTime: number;
  playing: boolean;
  segments: EditSeg[];
  selectedId: string | null;
  pxPerSec: number;
  filmstrip: string | null; // URL do sprite (filmstrip) gerado no servidor, ou null = sem frames
  peaks: number[] | null;
  hasHandles: boolean; // o clipe tem sobra (handles) editável? legado = false (sem alças)
  cutIn: number; // início do corte (s, relativo à janela)
  cutOut: number; // fim do corte (s, relativo à janela)
  endScreenLabel: string | null; // tela final anexada no fim (rótulo) ou null = nenhuma
  onSeek: (t: number) => void;
  onPlayPause: () => void;
  onSelect: (id: string) => void;
  onChangeSegment: (id: string, patch: { start: number; end: number }) => void;
  onChangeCut: (edge: "in" | "out", value: number) => void;
  onZoom: (px: number) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null); // container que rola (mede clientWidth, move scrollLeft)
  const tracksRef = useRef<HTMLDivElement>(null); // conteudo (largura = trackWidth)
  const waveRef = useRef<HTMLCanvasElement>(null);
  // Guarda contra brigar com o scroll do usuario: lembramos o ultimo scrollLeft que NOS setamos;
  // se o onScroll trouxer outro valor, foi o usuario -> pausa o auto-follow por um tempinho.
  const lastAuto = useRef(-1);
  const pauseFollowUntil = useRef(0);
  const didFit = useRef(false);

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

  function startDrag(e: React.PointerEvent, id: string, mode: "move" | "left" | "right") {
    e.stopPropagation();
    const s = segments.find((seg) => seg._id === id);
    if (!s) return;
    const startX = e.clientX;
    let moved = false;
    const d = { start: s.start, end: s.end };
    const tol = 8 / pxPerSec; // ~8px de ima
    const targets = [currentTime, 0, dur];
    segments.forEach((seg) => {
      if (seg._id !== id) targets.push(seg.start, seg.end);
    });
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      // So vira arraste depois de passar do limiar: um clique puro nao empurra o bloco.
      if (!moved && Math.abs(dx) < DRAG_THRESHOLD) return;
      moved = true;
      const ds = dx / pxPerSec;
      let start = d.start;
      let end = d.end;
      if (mode === "move") {
        const w = d.end - d.start;
        start = Math.max(0, Math.min(snap(d.start + ds, targets, tol), dur - w));
        end = start + w;
      } else if (mode === "left") {
        start = Math.max(0, Math.min(snap(d.start + ds, targets, tol), d.end - 0.1));
      } else {
        end = Math.min(dur, Math.max(snap(d.end + ds, targets, tol), d.start + 0.1));
      }
      onChangeSegment(id, { start: r2(start), end: r2(end) });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (!moved) onSelect(id); // clique sem mover = selecionar
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // Arrasta a alça de corte (in/out) dentro da janela. Imanta no playhead, nas bordas e nos
  // blocos de legenda; clampa pra nunca cruzar a outra alça (deixando ao menos MIN_CUT de corte).
  function startTrim(e: React.PointerEvent, edge: "in" | "out") {
    e.stopPropagation();
    const startX = e.clientX;
    const orig = edge === "in" ? cutIn : cutOut;
    const tol = 8 / pxPerSec;
    const targets = [currentTime, 0, dur];
    segments.forEach((s) => targets.push(s.start, s.end));
    const move = (ev: PointerEvent) => {
      let v = snap(orig + (ev.clientX - startX) / pxPerSec, targets, tol);
      if (edge === "in") v = Math.max(0, Math.min(v, cutOut - MIN_CUT));
      else v = Math.min(dur, Math.max(v, cutIn + MIN_CUT));
      onChangeCut(edge, r2(v));
    };
    const up = () => {
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

  // Ajusta o zoom pra que o clipe inteiro caiba na largura visivel (sem rolagem horizontal).
  function fitToClip() {
    const el = scrollRef.current;
    if (!el || dur <= 0) return;
    const w = el.clientWidth - 16;
    onZoom(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.floor(w / dur))));
  }

  // Abre o clipe ja cabendo inteiro na tela (uma vez, quando a duracao fica conhecida).
  useEffect(() => {
    if (didFit.current) return;
    const el = scrollRef.current;
    if (dur > 0 && el) {
      const w = el.clientWidth - 16;
      onZoom(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.floor(w / dur))));
      didFit.current = true;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dur]);

  // Playhead acompanha sozinho enquanto toca: rola pra manter o cursor ~no centro da janela.
  // So enquanto `playing`, e com um respiro se o usuario rolou na mao no meio do play.
  useEffect(() => {
    if (!playing) return;
    const el = scrollRef.current;
    if (!el) return;
    if (performance.now() < pauseFollowUntil.current) return;
    const target = currentTime * pxPerSec - el.clientWidth / 2;
    const max = Math.max(0, el.scrollWidth - el.clientWidth);
    const next = Math.max(0, Math.min(target, max));
    lastAuto.current = next;
    el.scrollLeft = next;
  }, [currentTime, playing, pxPerSec]);

  // Selecao vinda da lista (pausado): rola pra mostrar o bloco selecionado se estiver fora da vista.
  useEffect(() => {
    if (playing || selectedId == null) return;
    const el = scrollRef.current;
    const seg = segments.find((s) => s._id === selectedId);
    if (!el || !seg) return;
    const x = seg.start * pxPerSec;
    if (x < el.scrollLeft || x > el.scrollLeft + el.clientWidth) {
      el.scrollLeft = Math.max(0, x - el.clientWidth / 2);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  function onTracksScroll() {
    const el = scrollRef.current;
    if (!el) return;
    if (lastAuto.current >= 0 && Math.abs(el.scrollLeft - lastAuto.current) < 2) return; // foi o auto-follow
    pauseFollowUntil.current = performance.now() + 1500; // usuario rolou: pausa o follow um pouco
  }

  // Ticks da regua: intervalo conforme o zoom.
  const interval = pxPerSec >= 14 ? 1 : pxPerSec >= 7 ? 2 : 5;
  const ticks: number[] = [];
  for (let t = 0; t <= dur; t += interval) ticks.push(t);

  // Bloco sendo falado agora (destaque karaoke, distinto da selecao).
  const activeId = segments.find((s) => currentTime >= s.start && currentTime < s.end)?._id ?? null;

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
        <button
          className="editor-icon-btn"
          onClick={fitToClip}
          aria-label="Ajustar à tela"
          title="Ajustar o clipe inteiro à tela"
        >
          <Maximize2 className="size-4" />
        </button>
        <button
          className="editor-icon-btn"
          onClick={() => onZoom(Math.max(ZOOM_MIN, pxPerSec - 4))}
          aria-label="Diminuir zoom"
          title="Diminuir zoom"
        >
          <Minus className="size-4" />
        </button>
        <input
          type="range"
          min={ZOOM_MIN}
          max={ZOOM_MAX}
          value={pxPerSec}
          onChange={(e) => onZoom(parseInt(e.target.value, 10))}
          aria-label="Zoom da timeline"
          className="w-28 accent-[var(--gold)]"
        />
        <button
          className="editor-icon-btn"
          onClick={() => onZoom(Math.min(ZOOM_MAX, pxPerSec + 4))}
          aria-label="Aumentar zoom"
          title="Aumentar zoom"
        >
          <Plus className="size-4" />
        </button>
      </div>

      <div className="editor-tracks-scroll" ref={scrollRef} onScroll={onTracksScroll}>
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
            {filmstrip && (
              // O sprite (N frames lado a lado) e esticado na largura da trilha: distribui os frames
              // ao longo do tempo (o nº exato de frames nao importa). Gerado no servidor (CORS do S3).
              <div className="editor-film-strip" style={{ backgroundImage: `url(${filmstrip})` }} />
            )}
          </div>

          <div className="editor-track editor-track--wave">
            <canvas ref={waveRef} className="editor-wave-canvas" style={{ width: trackWidth }} />
          </div>

          <div className="editor-track editor-track--caps">
            {segments.map((s) => (
              <div
                key={s._id}
                className={`editor-cap-block${selectedId === s._id ? " is-selected" : ""}${activeId === s._id ? " is-active" : ""}${s.end - s.start < 0.7 ? " is-short" : ""}`}
                style={{ left: s.start * pxPerSec, width: Math.max(MIN_BLOCK_W, (s.end - s.start) * pxPerSec) }}
                onPointerDown={(e) => startDrag(e, s._id, "move")}
                title={s.text}
              >
                <span className="editor-cap-block-text">{s.text || "(vazio)"}</span>
                <div className="editor-cap-handle left" onPointerDown={(e) => startDrag(e, s._id, "left")} />
                <div className="editor-cap-handle right" onPointerDown={(e) => startDrag(e, s._id, "right")} />
              </div>
            ))}
          </div>

          {/* 4a camada: tela final. So visual (pointer-events:none, nao mexe no scrub). Ancorada no
              fim do corte; o bloco cresce pra esquerda (translateX -100% no CSS) pra ficar legivel. */}
          <div className="editor-track editor-track--end">
            {endScreenLabel ? (
              <div
                className="editor-end-block"
                style={{ left: (hasHandles ? cutOut : dur) * pxPerSec }}
                title={`${endScreenLabel} é anexada ~5s no fim do clipe`}
              >
                ▸ {endScreenLabel}
              </div>
            ) : (
              <span className="editor-end-empty">sem tela final</span>
            )}
          </div>

          {hasHandles && dur > 0 && (
            <>
              {cutIn > 0 && (
                <div className="editor-trim-mask is-head" style={{ width: cutIn * pxPerSec }} />
              )}
              {cutOut < dur && (
                <div className="editor-trim-mask is-tail" style={{ left: cutOut * pxPerSec }} />
              )}
              <div
                className="editor-trim-handle"
                style={{ left: cutIn * pxPerSec }}
                onPointerDown={(e) => startTrim(e, "in")}
                title="Início do corte (arraste)"
              />
              <div
                className="editor-trim-handle"
                style={{ left: cutOut * pxPerSec }}
                onPointerDown={(e) => startTrim(e, "out")}
                title="Fim do corte (arraste)"
              />
              <div className="editor-trim-dur" style={{ left: ((cutIn + cutOut) / 2) * pxPerSec }}>
                corte {fmtClock(cutOut - cutIn)}
              </div>
            </>
          )}

          <div className="editor-playhead" style={{ left: currentTime * pxPerSec }} />
        </div>
      </div>
    </div>
  );
}
