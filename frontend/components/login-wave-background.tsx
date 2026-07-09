"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

// Waveform decorativa do fundo do /login: canvas 2D puro, poucas dezenas de barras
// (nada de Three.js aqui, rota publica nao vale o peso do chunk WebGL so pro fundo).
// Espelha o padrao do ShaderBackground (pausa em document.hidden, respeita
// prefers-reduced-motion) mas em CPU, bem mais barato.
const BAR_COUNT = 40;
const GOLD = "245, 197, 24"; // --gold em rgb, pra montar rgba() no canvas
const GOLD_2 = "255, 215, 94"; // --gold-2, usado só no brilho do "sweep"

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
  const barW = Math.max(2, gap * 0.4);
  const midY = h * 0.87; // faixa baixa da tela, pra nao brigar com o cartao central
  const maxBar = Math.min(h * 0.16, 120);
  const sweepX = animated ? ((t * 0.045) % (w + 400)) - 200 : -Infinity;

  for (let i = 0; i < BAR_COUNT; i++) {
    const x = i * gap + gap / 2;
    const phase = i * 0.4;
    const wave = 0.5 + 0.28 * Math.sin(t * 0.0011 + phase) + 0.22 * Math.sin(t * 0.0007 + phase * 1.7 + 1.2);
    const amp = Math.min(1, Math.max(0.1, wave));
    const barH = amp * maxBar;
    const dist = Math.abs(x - sweepX);
    const boost = animated ? Math.exp(-(dist * dist) / (2 * 200 * 200)) : 0;
    const alpha = Math.min(0.4, 0.05 + 0.14 * amp + boost * 0.28);
    const color = boost > 0.5 ? GOLD_2 : GOLD;
    ctx.fillStyle = `rgba(${color}, ${alpha.toFixed(3)})`;
    roundRect(ctx, x - barW / 2, midY - barH, barW, barH * 2, barW / 2);
    ctx.fill();
  }
}

export function LoginWaveBackground({ className }: { className?: string }) {
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
      const w = window.innerWidth;
      const h = window.innerHeight;
      canvas!.width = w * dpr;
      canvas!.height = h * dpr;
      canvas!.style.width = `${w}px`;
      canvas!.style.height = `${h}px`;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (reduced) draw(ctx!, w, h, 0, false);
    }

    resize();
    window.addEventListener("resize", resize);

    if (reduced) {
      return () => window.removeEventListener("resize", resize);
    }

    function loop(t: number) {
      if (stopped || paused) return;
      raf = requestAnimationFrame(loop);
      if (t - lastDraw < 33) return; // ~30fps, poupa CPU
      lastDraw = t;
      draw(ctx!, window.innerWidth, window.innerHeight, t, true);
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
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={ref} className={cn("login-wave", className)} aria-hidden="true" />;
}
