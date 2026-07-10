"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

// Waveform decorativa da faixa de saudacao da Home: mesmo espirito tecnico do
// LoginWaveBackground (canvas 2D puro, pausa em document.hidden, respeita
// prefers-reduced-motion), mas dimensionada pelo container (via ResizeObserver, nao pela
// janela inteira) e recolorida no acento v3. A faixa e compacta e de altura fixa (upload,
// numeros e recentes viraram secoes proprias abaixo, fora da area do hero).
const BAR_COUNT = 48;
const V3_ACCENT = "255, 212, 0"; // --v3-accent em rgb
const V3_ACCENT_SOFT = "255, 232, 130"; // realce do "sweep" que percorre as barras

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
  ctx.lineTo(x + w, y + h - rr);
  ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
  ctx.lineTo(x + rr, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - rr);
  ctx.lineTo(x, y + rr);
  ctx.quadraticCurveTo(x, y, x + rr, y);
  ctx.closePath();
}

// animated=false desenha um unico frame estatico (fallback do prefers-reduced-motion).
function draw(ctx: CanvasRenderingContext2D, w: number, h: number, t: number, animated: boolean) {
  ctx.clearRect(0, 0, w, h);
  const gap = w / BAR_COUNT;
  const barW = Math.max(2, gap * 0.38);
  const midY = h * 0.74; // faixa baixa da saudacao, atras do texto (um pouco acima da borda)
  const maxBar = Math.min(h * 0.22, 46); // teto baixo: a faixa agora e compacta, nao full-screen
  const sweepX = animated ? ((t * 0.05) % (w + 400)) - 200 : -Infinity;

  for (let i = 0; i < BAR_COUNT; i++) {
    const x = i * gap + gap / 2;
    const phase = i * 0.36;
    const wave = 0.5 + 0.28 * Math.sin(t * 0.0012 + phase) + 0.22 * Math.sin(t * 0.0008 + phase * 1.6 + 1.1);
    const amp = Math.min(1, Math.max(0.08, wave));
    const barH = amp * maxBar;
    const dist = Math.abs(x - sweepX);
    const boost = animated ? Math.exp(-(dist * dist) / (2 * 200 * 200)) : 0;
    const alpha = Math.min(0.35, 0.04 + 0.12 * amp + boost * 0.24);
    const color = boost > 0.5 ? V3_ACCENT_SOFT : V3_ACCENT;
    ctx.fillStyle = `rgba(${color}, ${alpha.toFixed(3)})`;
    roundRect(ctx, x - barW / 2, midY - barH, barW, barH * 2, barW / 2);
    ctx.fill();
  }
}

export function HomeWaveBackground({ className }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    let raf = 0;
    let stopped = false;
    let paused = false;
    let lastDraw = 0;

    function resize() {
      const rect = canvas!.getBoundingClientRect();
      const w = rect.width;
      const h = rect.height;
      if (!w || !h) return;
      canvas!.width = w * dpr;
      canvas!.height = h * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (reduced) draw(ctx!, w, h, 0, false);
    }

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    if (reduced) {
      return () => ro.disconnect();
    }

    function loop(t: number) {
      if (stopped || paused) return;
      raf = requestAnimationFrame(loop);
      if (t - lastDraw < 33) return; // ~30fps, poupa CPU
      lastDraw = t;
      const rect = canvas!.getBoundingClientRect();
      draw(ctx!, rect.width, rect.height, t, true);
    }

    function onVisibility() {
      if (document.hidden) {
        paused = true;
        if (raf) cancelAnimationFrame(raf);
      } else if (!stopped && paused) {
        paused = false;
        raf = requestAnimationFrame(loop);
      }
    }
    document.addEventListener("visibilitychange", onVisibility);
    raf = requestAnimationFrame(loop);

    return () => {
      stopped = true;
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={ref} className={cn("v3-hero-wave", className)} aria-hidden="true" />;
}
