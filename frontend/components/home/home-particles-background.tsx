"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

// Fundo decorativo da faixa de saudacao + upload da Home: particulas conectadas (efeito
// "constellation"), no lugar da waveform anterior. Mesma arquitetura tecnica de sempre
// (canvas 2D puro, ResizeObserver com dpr capado, throttle ~30fps via diff de timestamp,
// pausa em document.hidden, frame estatico unico quando prefers-reduced-motion esta ativo),
// sem nenhuma lib externa.
const V3_ACCENT = "255, 212, 0"; // --v3-accent em rgb, unica cor do desenho

const MIN_COUNT = 24;
const MAX_COUNT = 90;
const MOBILE_BREAKPOINT = 640;
const MOBILE_SCALE = 0.55; // efeito mais leve no mobile

const LINK_DIST = 130; // distancia maxima (px) pra ligar duas particulas
const LINK_MAX_ALPHA = 0.25;
const CURSOR_DIST = 160; // distancia maxima (px) pra ligar particula ao cursor
const CURSOR_MAX_ALPHA = 0.4;

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  alpha: number;
}

// Quantidade de particulas em funcao da area do canvas, mais leve em telas pequenas.
function countFor(w: number, h: number) {
  const raw = Math.round((w * h) / 16000);
  let count = Math.min(MAX_COUNT, Math.max(MIN_COUNT, raw));
  if (w < MOBILE_BREAKPOINT) {
    count = Math.round(count * MOBILE_SCALE);
  }
  return count;
}

// Cada particula sorteia posicao/velocidade/raio/opacidade uma unica vez, na criacao; so
// x/y mudam depois, quadro a quadro.
function makeParticles(w: number, h: number): Particle[] {
  const count = countFor(w, h);
  const particles: Particle[] = [];
  for (let i = 0; i < count; i++) {
    particles.push({
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.5, // -0.25..0.25 px/frame
      vy: (Math.random() - 0.5) * 0.5,
      r: 1.2 + Math.random() * 1.2, // 1.2..2.4
      alpha: 0.35 + Math.random() * 0.4, // 0.35..0.75
    });
  }
  return particles;
}

// animated=false desenha um unico frame estatico (fallback do prefers-reduced-motion): so
// particulas e conexoes entre elas, sem avancar posicao nem reagir ao cursor.
function draw(
  ctx: CanvasRenderingContext2D,
  particles: Particle[],
  w: number,
  h: number,
  animated: boolean,
  cursor: { x: number; y: number } | null,
) {
  ctx.clearRect(0, 0, w, h);

  if (animated) {
    for (const p of particles) {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0 || p.x > w) p.vx *= -1;
      if (p.y < 0 || p.y > h) p.vy *= -1;
      p.x = Math.min(w, Math.max(0, p.x));
      p.y = Math.min(h, Math.max(0, p.y));
    }
  }

  for (let i = 0; i < particles.length; i++) {
    for (let j = i + 1; j < particles.length; j++) {
      const a = particles[i];
      const b = particles[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist >= LINK_DIST) continue;
      const alpha = LINK_MAX_ALPHA * (1 - dist / LINK_DIST);
      ctx.strokeStyle = `rgba(${V3_ACCENT}, ${alpha.toFixed(3)})`;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
  }

  if (animated && cursor) {
    for (const p of particles) {
      const dx = p.x - cursor.x;
      const dy = p.y - cursor.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist >= CURSOR_DIST) continue;
      const alpha = Math.min(CURSOR_MAX_ALPHA, LINK_MAX_ALPHA * 1.3 * (1 - dist / CURSOR_DIST));
      ctx.strokeStyle = `rgba(${V3_ACCENT}, ${alpha.toFixed(3)})`;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(cursor.x, cursor.y);
      ctx.stroke();
    }
  }

  for (const p of particles) {
    ctx.fillStyle = `rgba(${V3_ACCENT}, ${p.alpha.toFixed(3)})`;
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    ctx.fill();
  }
}

export function HomeParticlesBackground({ className }: { className?: string }) {
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
    let particles: Particle[] = [];
    // Posicao do cursor em coordenada local do canvas; null quando nunca moveu ou esta fora
    // (o canvas tem pointer-events:none, entao o listener fica na window).
    let cursor: { x: number; y: number } | null = null;

    function resize() {
      const rect = canvas!.getBoundingClientRect();
      const w = rect.width;
      const h = rect.height;
      if (!w || !h) return;
      canvas!.width = w * dpr;
      canvas!.height = h * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      particles = makeParticles(w, h);
      if (reduced) draw(ctx!, particles, w, h, false, null);
    }

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    if (reduced) {
      return () => ro.disconnect();
    }

    function onPointerMove(e: PointerEvent) {
      const rect = canvas!.getBoundingClientRect();
      const inside =
        e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;
      cursor = inside ? { x: e.clientX - rect.left, y: e.clientY - rect.top } : null;
    }

    function loop(t: number) {
      if (stopped || paused) return;
      raf = requestAnimationFrame(loop);
      if (t - lastDraw < 33) return; // ~30fps, poupa CPU
      lastDraw = t;
      const rect = canvas!.getBoundingClientRect();
      draw(ctx!, particles, rect.width, rect.height, true, cursor);
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

    window.addEventListener("pointermove", onPointerMove);
    document.addEventListener("visibilitychange", onVisibility);
    raf = requestAnimationFrame(loop);

    return () => {
      stopped = true;
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={ref} className={cn("v3-hero-canvas", className)} aria-hidden="true" />;
}
