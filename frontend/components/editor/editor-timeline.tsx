"use client";

import { useEffect, useRef } from "react";
import { Maximize2, Minus, Pause, Play, Plus, SkipBack, SkipForward } from "lucide-react";
import type { EditSeg } from "./clip-editor";
import type { CropKeyframe } from "@/lib/types";

const MIN_BLOCK_W = 34; // largura minima clicavel de um bloco, mesmo pra legenda curtissima
const ZOOM_MIN = 6;
const ZOOM_MAX = 160;
const TARGET_BLOCK_PX = 46; // largura-alvo do bloco medio: define o zoom default por DENSIDADE
const ZOOM_DEFAULT_MIN = 28; // piso/teto do zoom default (clipes esparsos x cheios de trechos curtos)
const ZOOM_DEFAULT_MAX = 120;
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
  endScreenDur,
  endScreenAssetDur,
  onChangeEndDur,
  onCommitEndDur,
  onSeek,
  onPlayPause,
  onSelect,
  onChangeSegment,
  onChangeCut,
  cropActive,
  keyframes,
  selectedKf,
  onSelectKf,
  onDeleteKf,
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
  endScreenDur: number; // duração da tela final usada neste clipe (s); 0 = sem tela
  endScreenAssetDur: number; // duração total do asset da tela (s); teto do encurtar
  onChangeEndDur: (dur: number) => void; // durante o arraste (visual)
  onCommitEndDur: (dur: number) => void; // ao soltar (persiste)
  onSeek: (t: number) => void;
  onPlayPause: () => void;
  onSelect: (id: string) => void;
  onChangeSegment: (id: string, patch: { start: number; end: number }) => void;
  onChangeCut: (edge: "in" | "out", value: number) => void;
  cropActive: boolean; // modo enquadramento ativo: mostra a trilha de keyframes
  keyframes: CropKeyframe[]; // keyframes do crop (t em s, cx fração 0..1)
  selectedKf: number | null; // tempo (s) do keyframe selecionado, ou null
  onSelectKf: (t: number) => void; // seleciona + posiciona o playhead no keyframe
  onDeleteKf: (t: number) => void; // remove o keyframe daquele tempo
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

  // Arrasta a borda direita do bloco da tela final pra ENCURTAR (usar menos que o asset inteiro).
  // Clampa a [0,5s .. duração do asset]. Atualiza visual no move; persiste no up.
  function startEndDur(e: React.PointerEvent) {
    e.stopPropagation();
    const startX = e.clientX;
    const orig = endScreenDur;
    const maxDur = endScreenAssetDur || endScreenDur;
    let last = orig;
    const move = (ev: PointerEvent) => {
      last = Math.max(0.5, Math.min(maxDur, orig + (ev.clientX - startX) / pxPerSec));
      onChangeEndDur(r2(last));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      onCommitEndDur(r2(last));
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
    // Barras na cor "cheia" (tocado). O trecho AINDA NAO tocado e escurecido por um overlay
    // posicionado no playhead (editor-wave-future), pra nao redesenhar o canvas a cada frame.
    ctx.fillStyle = "rgba(245,197,24,0.85)";
    const step = 2;
    for (let x = 0; x < w; x += step) {
      const idx = Math.floor((x / w) * peaks.length);
      const peak = peaks[idx] || 0;
      const bh = Math.max(1, peak * (h - 4));
      ctx.fillRect(x, (h - bh) / 2, 1.6, bh);
    }
  }, [peaks, trackWidth]);

  // Ajusta o zoom pra que o clipe inteiro caiba na largura visivel (sem rolagem horizontal).
  function fitToClip() {
    const el = scrollRef.current;
    if (!el || dur <= 0) return;
    const w = el.clientWidth - 16;
    onZoom(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.floor(w / dur))));
  }

  // Zoom default por DENSIDADE (uma vez, quando duracao E trechos sao conhecidos): mira ~TARGET_BLOCK_PX
  // por bloco medio, pra os blocos nascerem pegaveis em vez de espremidos. Melhor sobrar e rolar na
  // horizontal do que colar tudo. O "ajustar a tela" (fitToClip) continua no botao Maximize.
  useEffect(() => {
    if (didFit.current) return;
    if (dur <= 0 || segments.length === 0) return;
    const px = Math.max(
      ZOOM_DEFAULT_MIN,
      Math.min(ZOOM_DEFAULT_MAX, Math.round((TARGET_BLOCK_PX * segments.length) / dur)),
    );
    onZoom(px);
    didFit.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dur, segments.length]);

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

      <div className="editor-legend">
        <span className="editor-legend-item"><i style={{ background: "var(--gold)" }} />Legendas</span>
        <span className="editor-legend-item"><i style={{ background: "rgba(245,197,24,0.45)" }} />Áudio</span>
        {hasHandles && (
          <span className="editor-legend-item"><i style={{ background: "#2fe0bf" }} />Aparar</span>
        )}
        {endScreenLabel && (
          <span className="editor-legend-item"><i style={{ background: "rgba(47,224,191,0.7)" }} />Tela final</span>
        )}
        {cropActive && (
          <span className="editor-legend-item"><i style={{ background: "var(--gold)" }} />Keyframes</span>
        )}
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
              // Sprite (N frames lado a lado) gerado no servidor (CORS do S3). O CSS repete os frames
              // na PROPORCAO certa (background-size:auto 100% + repeat-x), sem esticar/achatar a imagem.
              <div className="editor-film-strip" style={{ backgroundImage: `url(${filmstrip})` }} />
            )}
          </div>

          <div className="editor-track editor-track--wave">
            <canvas ref={waveRef} className="editor-wave-canvas" style={{ width: trackWidth }} />
            {/* Escurece o trecho ainda nao tocado (do playhead pra frente): da o "antes/depois". */}
            <div className="editor-wave-future" style={{ left: currentTime * pxPerSec }} />
          </div>

          <div className="editor-track editor-track--caps">
            {segments.map((s) => (
              <div
                key={s._id}
                className={`editor-cap-block${selectedId === s._id ? " is-selected" : ""}${activeId === s._id ? " is-active" : ""}${s.end - s.start < 0.7 ? " is-short" : ""}`}
                style={{ left: s.start * pxPerSec, width: Math.max(MIN_BLOCK_W, (s.end - s.start) * pxPerSec - 2) }}
                onPointerDown={(e) => startDrag(e, s._id, "move")}
                title={s.text}
              >
                <span className="editor-cap-block-text">{s.text || "(vazio)"}</span>
                <div className="editor-cap-handle left" onPointerDown={(e) => startDrag(e, s._id, "left")} />
                <div className="editor-cap-handle right" onPointerDown={(e) => startDrag(e, s._id, "right")} />
              </div>
            ))}
          </div>

          {/* Trilha de keyframes do crop (só no modo enquadramento): losangos no tempo de cada
              keyframe. Clicar seleciona + leva o playhead; o selecionado mostra um × pra remover.
              A criação/ajuste de keyframe é arrastando a CAIXA no preview (estilo OpusClip). */}
          {cropActive && (
            <div className="editor-track editor-track--key">
              {keyframes.length === 0 ? (
                <span className="editor-end-empty">sem keyframes — arraste a caixa no preview</span>
              ) : (
                keyframes.map((k) => {
                  const sel = selectedKf != null && Math.abs(selectedKf - k.t) < 1e-6;
                  return (
                    <button
                      key={k.t}
                      type="button"
                      className={"editor-kf" + (sel ? " is-sel" : "")}
                      style={{ left: k.t * pxPerSec }}
                      onClick={() => onSelectKf(k.t)}
                      title={`Keyframe em ${k.t.toFixed(2)}s`}
                    >
                      {sel && (
                        <span
                          className="editor-kf-del"
                          role="button"
                          aria-label="Remover keyframe"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteKf(k.t);
                          }}
                        >
                          ×
                        </span>
                      )}
                    </button>
                  );
                })
              )}
            </div>
          )}

          {/* 4a camada: tela final. Bloco a partir do fim do corte, com largura = duração usada;
              arraste a alça direita pra encurtar. So o bloco e clicavel (resto pointer-events:none). */}
          <div className="editor-track editor-track--end">
            {endScreenLabel ? (
              <div
                className="editor-end-block"
                style={{
                  left: (hasHandles ? cutOut : dur) * pxPerSec,
                  width: Math.max(MIN_BLOCK_W, endScreenDur * pxPerSec),
                }}
                title={`${endScreenLabel} · ${endScreenDur.toFixed(1)}s no fim (arraste a alça pra encurtar)`}
              >
                <span className="editor-end-block-text">▸ {endScreenLabel} · {endScreenDur.toFixed(1)}s</span>
                {endScreenAssetDur > 0.6 && (
                  <div
                    className="editor-end-handle"
                    onPointerDown={startEndDur}
                    title="Arraste pra encurtar a tela final"
                  />
                )}
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
