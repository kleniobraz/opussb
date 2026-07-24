"use client";

// Corte manual do início do corpo bruto do podcast, ANTES da transcrição: o vídeo pode ter
// 1 a 3 horas, então o marcador não é um slider fino de 2 pontas (como o de hook-approval.tsx,
// que trabalha numa janela de segundos); é 1 ponto só, navegado em 3 níveis de zoom (episódio
// inteiro -> lupa de ~30s -> quadro a quadro), todos operando o MESMO marcador, sempre
// quantizado pro frame mais próximo do fps real do arquivo (fallback 30, jobs antigos sem a
// coluna). Regras de reprodução (ver togglePlay): a primeira vez que toca começa exatamente no
// corte; pausar e tocar de novo retoma de onde parou; só quando o marcador muda de novo é que a
// próxima reprodução volta a começar no corte.
//
// Casca visual: design system oficial SB Clips (SeuBone Design System, importado via MCP),
// tokens prefixo sbds (ver globals.css). Só esta tela usa esses tokens por enquanto.

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Pause, Play, Scissors } from "lucide-react";
import * as api from "@/lib/api";
import { fmtFrameTC, fmtHMS, parseHMS } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { SbdsBox } from "@/components/ui/sbds-box";
import { SbdsDivider } from "@/components/ui/sbds-divider";

const JANELA_LUPA = 30; // segundos visíveis no nível 2 (~15s pra cada lado do marcador)
const AMARELO = "#ffc107"; // --sbds-amarelo, usado direto no canvas (sem categoria de tema pra 2d context)

function SaltoBotao({ label, delta, onClick }: { label: string; delta: number; onClick: (d: number) => void }) {
  return (
    <Button type="button" variant="sbdsGhost" size="sbdsSm" onClick={() => onClick(delta)}>
      {label}
    </Button>
  );
}

// Nível 1: episódio inteiro. Clique ou arraste move o marcador (navegação grosseira, ideal
// pra chegar perto). Sem waveform de verdade (não existe esse dado pro vídeo bruto): a região
// antes do corte só é hachurada pra marcar "vai fora" visualmente, sem fingir picos de áudio.
function BarraEpisodio({
  dur,
  cutAt,
  onSeek,
}: {
  dur: number;
  cutAt: number;
  onSeek: (t: number) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<{ x: number; t: number } | null>(null);
  const [resizeTick, setResizeTick] = useState(0);

  useEffect(() => {
    const onResize = () => setResizeTick((n) => n + 1);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const w = Math.max(1, wrap.clientWidth);
    const h = 56;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    if (dur <= 0) {
      ctx.fillStyle = "rgba(255,255,255,0.03)";
      ctx.fillRect(0, 0, w, h);
      return;
    }
    const cutX = (cutAt / dur) * w;

    // fica (depois do corte): fundo destacado
    ctx.fillStyle = "rgba(255,193,7,0.12)";
    ctx.fillRect(cutX, 0, w - cutX, h);

    // vai fora (antes do corte): hachura diagonal, só geometria, sem waveform
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, cutX, h);
    ctx.clip();
    ctx.fillStyle = "rgba(255,255,255,0.04)";
    ctx.fillRect(0, 0, cutX, h);
    ctx.strokeStyle = "rgba(255,255,255,0.16)";
    ctx.lineWidth = 1;
    for (let x = -h; x < cutX + h; x += 9) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x + h, h);
      ctx.stroke();
    }
    ctx.restore();

    // marcador
    ctx.strokeStyle = AMARELO;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cutX, 0);
    ctx.lineTo(cutX, h);
    ctx.stroke();
    ctx.beginPath();
    ctx.fillStyle = AMARELO;
    ctx.arc(cutX, h / 2, 6, 0, Math.PI * 2);
    ctx.fill();
  }, [dur, cutAt, resizeTick]);

  function tempoNoX(clientX: number): number {
    const el = wrapRef.current;
    if (!el || dur <= 0) return 0;
    const rect = el.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return frac * dur;
  }

  function onPointerDown(e: React.PointerEvent) {
    onSeek(tempoNoX(e.clientX));
    const move = (ev: PointerEvent) => onSeek(tempoNoX(ev.clientX));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <div ref={wrapRef} className="relative select-none touch-none">
      <canvas
        ref={canvasRef}
        className="block w-full cursor-pointer rounded-sbds-sm border border-sbds-border-subtle"
        onPointerDown={onPointerDown}
        onPointerMove={(e) => setHover({ x: e.clientX - (wrapRef.current?.getBoundingClientRect().left ?? 0), t: tempoNoX(e.clientX) })}
        onPointerLeave={() => setHover(null)}
        aria-label="Linha do tempo do episódio inteiro"
      />
      {hover && (
        <div
          className="pointer-events-none absolute -top-7 -translate-x-1/2 rounded-sbds-sm border border-sbds-border-subtle bg-sbds-surface-card px-2 py-1 text-[12.5px] text-sbds-branco shadow-sbds-card"
          style={{ left: hover.x }}
        >
          {fmtHMS(hover.t)}
        </div>
      )}
      <div className="mt-1 flex justify-between text-[12.5px] text-sbds-text-muted">
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <span key={f}>{fmtHMS(dur * f)}</span>
        ))}
      </div>
    </div>
  );
}

// Nível 2: lupa de ~30s. A agulha fica FIXA no centro; quem se move é o conteúdo por baixo
// (régua + regiões vai-fora/fica), como um carretel de filme: arrastar pra esquerda avança o
// tempo. Clique sem arrastar pula o corte pro ponto sob o clique. Roda do mouse anda 1 quadro.
function BarraLupa({
  dur,
  cutAt,
  onSeek,
  onStep,
}: {
  dur: number;
  cutAt: number;
  onSeek: (t: number) => void;
  onStep: (deltaFrames: number) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [resizeTick, setResizeTick] = useState(0);

  useEffect(() => {
    const onResize = () => setResizeTick((n) => n + 1);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // addEventListener nativo (não onWheel do React): o listener sintético do React é passivo
  // por padrão, e preventDefault() dentro de um handler passivo não impede o scroll da
  // página. Só assim a roda do mouse anda o quadro em vez de rolar a tela inteira.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    function onWheel(e: WheelEvent) {
      e.preventDefault();
      onStep(e.deltaY < 0 ? 1 : -1);
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [onStep]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const w = Math.max(1, wrap.clientWidth);
    const h = 76;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    if (dur <= 0) {
      ctx.fillStyle = "rgba(255,255,255,0.03)";
      ctx.fillRect(0, 0, w, h);
      return;
    }
    const pxPerSec = w / JANELA_LUPA;
    const windowStart = cutAt - JANELA_LUPA / 2;
    const xForT = (t: number) => (t - windowStart) * pxPerSec;
    const half = w / 2;

    // a fronteira vai-fora/fica é SEMPRE o centro (é a própria definição do corte): metade
    // direita clara (fica), metade esquerda hachurada (vai fora), o que desliza é a régua.
    ctx.fillStyle = "rgba(255,193,7,0.12)";
    ctx.fillRect(half, 0, w - half, h);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, half, h);
    ctx.clip();
    ctx.strokeStyle = "rgba(255,255,255,0.16)";
    ctx.lineWidth = 1;
    for (let x = -h; x < half + h; x += 9) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x + h, h);
      ctx.stroke();
    }
    ctx.restore();

    // régua de segundos (só dentro do vídeo de verdade)
    ctx.textAlign = "center";
    ctx.font = "10px sans-serif";
    const t0 = Math.max(0, Math.floor(windowStart));
    const t1 = Math.min(dur, Math.ceil(windowStart + JANELA_LUPA));
    for (let t = t0; t <= t1; t++) {
      const x = xForT(t);
      const marcaGrande = t % 5 === 0;
      ctx.strokeStyle = "rgba(255,255,255,0.3)";
      ctx.lineWidth = marcaGrande ? 1.5 : 1;
      ctx.beginPath();
      ctx.moveTo(x, h - (marcaGrande ? 16 : 10));
      ctx.lineTo(x, h);
      ctx.stroke();
      if (marcaGrande) {
        ctx.fillStyle = "rgba(255,255,255,0.6)";
        ctx.fillText(fmtHMS(t), x, h - 20);
      }
    }

    // véu escuro fora do vídeo (antes de 0 ou depois da duração real)
    const xInicio = xForT(0);
    const xFim = xForT(dur);
    ctx.fillStyle = "rgba(0,0,0,0.55)";
    if (xInicio > 0) ctx.fillRect(0, 0, Math.min(xInicio, w), h);
    if (xFim < w) ctx.fillRect(Math.max(xFim, 0), 0, w - Math.max(xFim, 0), h);

    // agulha fixa no centro
    ctx.strokeStyle = AMARELO;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(half, 0);
    ctx.lineTo(half, h);
    ctx.stroke();
    ctx.fillStyle = AMARELO;
    ctx.beginPath();
    ctx.moveTo(half - 6, 0);
    ctx.lineTo(half + 6, 0);
    ctx.lineTo(half, 9);
    ctx.closePath();
    ctx.fill();
  }, [dur, cutAt, resizeTick]);

  function onPointerDown(e: React.PointerEvent) {
    const startX = e.clientX;
    const startCutAt = cutAt;
    const w = wrapRef.current?.clientWidth || 1;
    const pxPerSec = w / JANELA_LUPA;
    let moved = false;
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      if (!moved && Math.abs(dx) < 4) return;
      moved = true;
      // arrastar pra esquerda avança o tempo (carretel de filme: o conteúdo desliza sob a agulha)
      onSeek(startCutAt - dx / pxPerSec);
    };
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (!moved) {
        const rect = wrapRef.current?.getBoundingClientRect();
        if (rect) onSeek(startCutAt - JANELA_LUPA / 2 + (ev.clientX - rect.left) / pxPerSec);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <div ref={wrapRef} className="relative select-none touch-none">
      <canvas
        ref={canvasRef}
        className="block w-full cursor-ew-resize rounded-sbds-sm border border-sbds-border-subtle"
        onPointerDown={onPointerDown}
        aria-label="Lupa de aproximadamente 30 segundos ao redor do ponto de corte"
      />
    </div>
  );
}

export function CorteInicio({
  jobId,
  onConfirm,
  confirming,
  fps,
}: {
  jobId: string;
  onConfirm: (cutAt: number) => void;
  confirming: boolean;
  fps: number | null;
}) {
  // Mesma convenção do backend (reframe_core.py): fps ausente (job antigo, sem a coluna) cai
  // pra 30.
  const effectiveFps = fps && fps > 0 ? fps : 30;

  const videoRef = useRef<HTMLVideoElement>(null);
  const [dur, setDur] = useState<number | null>(null);
  const [cutAt, setCutAt] = useState(0);
  const [texto, setTexto] = useState("0:00");
  const [tocando, setTocando] = useState(false);
  const [playbackTime, setPlaybackTime] = useState(0);

  const durRef = useRef<number | null>(null);
  useEffect(() => {
    durRef.current = dur;
  }, [dur]);
  const cutAtRef = useRef(0);
  useEffect(() => {
    cutAtRef.current = cutAt;
  }, [cutAt]);

  // null = próxima vez que apertar play começa no marcador (1a vez, ou marcador mudou desde
  // a última pausa); com valor = retoma exatamente dali (usuário só pausou pra ouvir mais).
  const pausedPositionRef = useRef<number | null>(null);

  // Seek jogado num rAF: arrastar nos níveis 1/2 dispara muitos eventos de ponteiro por
  // segundo, e cada um viraria uma requisição Range no arquivo de origem (que pode ter vários
  // GB). Aqui o desenho do marcador já responde na hora (é só canvas), mas o <video> só é
  // de fato re-posicionado a no máximo 1x por frame de tela.
  const seekRafRef = useRef(0);
  const pendingSeekRef = useRef<number | null>(null);
  const seekVideo = useCallback((t: number) => {
    pendingSeekRef.current = t;
    if (seekRafRef.current) return;
    seekRafRef.current = requestAnimationFrame(() => {
      seekRafRef.current = 0;
      const v = videoRef.current;
      if (v && pendingSeekRef.current != null) v.currentTime = pendingSeekRef.current;
    });
  }, []);
  useEffect(() => () => cancelAnimationFrame(seekRafRef.current), []);

  // Move o marcador pra qualquer alvo (nível 1, nível 2, teclado, stepper, chip ou texto):
  // sempre quantiza pro frame mais próximo do fps real e clampa em [0, duração]. É o único
  // funil que os 3 níveis de navegação usam, garantindo que até o arraste grosso do episódio
  // inteiro já cai exatamente num quadro.
  const moverPara = useCallback(
    (alvoBruto: number) => {
      const teto = durRef.current;
      let alvo = Math.max(0, alvoBruto);
      if (teto != null) alvo = Math.min(teto, alvo);
      alvo = Math.round(alvo * effectiveFps) / effectiveFps;
      if (teto != null) alvo = Math.min(teto, alvo);
      alvo = Math.max(0, alvo);
      setCutAt(alvo);
      setTexto(fmtHMS(alvo));
      pausedPositionRef.current = null; // marcador mudou: a próxima reprodução recomeça daqui
      seekVideo(alvo);
    },
    [effectiveFps, seekVideo],
  );

  const saltar = useCallback((delta: number) => moverPara(cutAtRef.current + delta), [moverPara]);
  const saltarQuadros = useCallback((deltaFrames: number) => saltar(deltaFrames / effectiveFps), [saltar, effectiveFps]);

  function aplicarTexto() {
    const t = parseHMS(texto);
    if (t == null) {
      setTexto(fmtHMS(cutAtRef.current)); // entrada inválida: desfaz pro último valor válido
      return;
    }
    moverPara(t);
  }

  // Play sempre parte do marcador na 1a vez (ou depois que ele mudou); pausar e tocar de novo
  // SEM mexer no marcador retoma de onde parou, deixando ouvir além do corte pra achar o
  // ponto certo (ver chip "Marcar corte aqui" abaixo).
  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      const startAt = pausedPositionRef.current != null ? pausedPositionRef.current : cutAtRef.current;
      v.currentTime = startAt;
      v.play().catch(() => {});
    } else {
      v.pause();
    }
  }, []);

  // Atalhos: espaço toca/pausa, setas andam 1 quadro (shift = 1s, ctrl/cmd = 1min), home volta
  // pro 0:00. Mesmo padrão de ignorar campo de texto usado no editor de clipes.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || el?.isContentEditable) return;
      if (e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
        e.preventDefault();
        const passo = e.ctrlKey || e.metaKey ? 60 : e.shiftKey ? 1 : 1 / effectiveFps;
        saltar(e.code === "ArrowLeft" ? -passo : passo);
      } else if (e.code === "Home") {
        e.preventDefault();
        moverPara(0);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay, saltar, moverPara, effectiveFps]);

  const restante = dur != null ? Math.max(dur - cutAt, 0) : null;
  const semCorte = cutAt <= 0; // armadilha do 0:00: confirmar aqui não corta nada
  const tc = fmtFrameTC(cutAt, effectiveFps);
  const fpsLabel = Number.isInteger(effectiveFps) ? String(effectiveFps) : effectiveFps.toFixed(2);
  // Chip "marcar aqui": só quando o usuário ouviu além (ou aquém) do ponto marcado por mais
  // de 1 quadro, sinal de que ele achou um lugar melhor enquanto tocava.
  const mostrarChip = dur != null && Math.abs(playbackTime - cutAt) > 1 / effectiveFps;

  return (
    // Mesma largura de .view-pad-narrow (1180px): preenche o espaço que .view-pad (1280px)
    // já abre em vez de espremer o conteúdo numa coluna estreita.
    <div className="mx-auto max-w-[1180px] space-y-6">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div
          className="group relative aspect-video w-full cursor-pointer overflow-hidden rounded-sbds-lg border border-sbds-border-subtle bg-sbds-preto shadow-sbds-card"
          onClick={togglePlay}
        >
          <video
            ref={videoRef}
            src={api.podcastSourceUrl(jobId)}
            preload="metadata"
            playsInline
            controls={false}
            onLoadedMetadata={(e) => setDur(e.currentTarget.duration || null)}
            onPlay={() => setTocando(true)}
            onPause={() => {
              setTocando(false);
              pausedPositionRef.current = videoRef.current?.currentTime ?? null;
            }}
            onEnded={() => setTocando(false)}
            onTimeUpdate={(e) => setPlaybackTime(e.currentTarget.currentTime)}
            className="size-full"
          />

          {!tocando ? (
            <div className="absolute inset-0 flex items-center justify-center bg-sbds-preto/25 transition-opacity duration-[var(--sbds-dur-base)]">
              <div className="flex size-16 items-center justify-center rounded-full bg-sbds-amarelo text-sbds-ink-on-amarelo">
                <Play className="size-7 translate-x-0.5" />
              </div>
            </div>
          ) : (
            <div className="absolute inset-0 flex items-center justify-center bg-sbds-preto/10 opacity-0 transition-opacity duration-[var(--sbds-dur-base)] group-hover:opacity-100">
              <div className="flex size-14 items-center justify-center rounded-full bg-sbds-preto/60 text-sbds-branco">
                <Pause className="size-6" />
              </div>
            </div>
          )}

          {mostrarChip && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                moverPara(playbackTime);
              }}
              className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-sbds-pill bg-sbds-amarelo px-3 py-1.5 text-[13px] font-bold text-sbds-ink-on-amarelo shadow-sbds-card transition-all duration-[var(--sbds-dur-base)]"
            >
              <Scissors className="size-3.5" />
              Marcar corte aqui ({fmtHMS(playbackTime)})
            </button>
          )}
        </div>

        <SbdsBox className="flex flex-col gap-4">
          <div>
            <Badge variant="sbdsOutline">Ponto de corte</Badge>
            <div className="mt-3 flex items-baseline gap-1 font-mono text-[44px] leading-none font-bold tabular-nums">
              <span>{tc.m}</span>
              <span className="text-sbds-text-muted">:</span>
              <span>{tc.s}</span>
              <span className="text-sbds-text-muted">:</span>
              <span className="text-sbds-amarelo">{tc.f}</span>
            </div>
            <Badge variant="sbdsNeutro" className="mt-3">
              quadro {tc.totalFrames.toLocaleString("pt-BR")} · {fpsLabel} fps
            </Badge>
          </div>

          <SbdsDivider fina />

          <div className="space-y-2 text-[15px] leading-[1.45]">
            {dur == null ? (
              <p className="text-sbds-text-muted">Carregando o vídeo...</p>
            ) : (
              <>
                <p className="flex items-center gap-1.5 text-sbds-text-muted">
                  <Scissors className="size-4" />
                  Vai fora <span className="font-bold text-sbds-branco">{fmtHMS(cutAt)}</span>
                </p>
                <p className="flex items-center gap-1.5 font-bold text-sbds-amarelo">
                  <Check className="size-4" />
                  Fica no episódio <span>{restante != null ? fmtHMS(restante) : "..."}</span>
                </p>
              </>
            )}
          </div>
        </SbdsBox>
      </div>

      <div className="space-y-5">
        <div>
          <p className="mb-1.5 text-[12.5px] text-sbds-text-muted">
            Episódio inteiro · clique ou arraste pra chegar perto
          </p>
          {dur != null ? (
            <BarraEpisodio dur={dur} cutAt={cutAt} onSeek={moverPara} />
          ) : (
            <div className="skel h-14 rounded-sbds-sm" />
          )}
        </div>

        <div>
          <p className="mb-1.5 text-[12.5px] text-sbds-text-muted">
            Lupa · janela de {JANELA_LUPA}s · roda do mouse anda 1 quadro
          </p>
          {dur != null ? (
            <BarraLupa dur={dur} cutAt={cutAt} onSeek={moverPara} onStep={saltarQuadros} />
          ) : (
            <div className="skel h-[76px] rounded-sbds-sm" />
          )}
        </div>

        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center justify-center gap-1.5">
            <SaltoBotao label="« 1s" delta={-1} onClick={saltar} />
            <SaltoBotao label="‹ 1 quadro" delta={-1 / effectiveFps} onClick={saltar} />
            <Input
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              onBlur={aplicarTexto}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
              }}
              className="w-28 border-[var(--sbds-border-subtle)] bg-transparent text-center font-mono text-sbds-branco"
              aria-label="Ponto de corte (H:MM:SS)"
            />
            <SaltoBotao label="1 quadro ›" delta={1 / effectiveFps} onClick={saltar} />
            <SaltoBotao label="1s »" delta={1} onClick={saltar} />
          </div>
          <p className="text-center text-[12.5px] text-sbds-text-muted">
            espaço: tocar/pausar · ← →: 1 quadro · shift + ← →: 1s · ctrl/cmd + ← →: 1min · home: 0:00
          </p>
        </div>

        <div className="space-y-1 border-t border-sbds-border-subtle pt-4">
          <Button variant="sbdsPrimary" size="sbdsLg" disabled={confirming || semCorte} onClick={() => onConfirm(cutAt)}>
            <Scissors className="size-4" />
            {confirming ? "Cortando..." : `Confirmar corte em ${fmtHMS(cutAt)}`}
          </Button>
          {semCorte && (
            <p className="text-[12.5px] text-sbds-amarelo">
              O ponto de corte está em 0:00: mova o marcador pra depois do início do episódio
              antes de confirmar.
            </p>
          )}
        </div>

        {!semCorte && (
          <Button type="button" variant="sbdsGhost" size="sbdsSm" onClick={() => moverPara(0)}>
            Voltar pro início
          </Button>
        )}
      </div>
    </div>
  );
}
