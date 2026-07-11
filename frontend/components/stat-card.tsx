"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

// Card de estatistica com count-up (0 -> valor). Respeita prefers-reduced-motion.
// Estilo v3 (Fase 4, tela de Configuracoes): numero grande em JetBrains Mono, mesmo
// espirito da StatsStrip da Home (components/home/stats-strip.tsx).
export function StatCard({
  icon,
  label,
  value,
  decimals = false,
  accent = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | null | undefined;
  decimals?: boolean;
  accent?: boolean;
}) {
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (value == null) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      setDisplay(value);
      return;
    }
    let raf = 0;
    let t0: number | null = null;
    const dur = 1000;
    function step(ts: number) {
      if (t0 === null) t0 = ts;
      const p = Math.min(1, (ts - t0) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      setDisplay(value! * e);
      if (p < 1) raf = requestAnimationFrame(step);
      else setDisplay(value!);
    }
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value]);

  const fmt = (v: number) => (decimals ? v.toFixed(1) : String(Math.round(v)));

  return (
    <div className="flex min-w-[150px] flex-1 flex-col rounded-[var(--radius-md)] border border-v3-border bg-v3-surface-1 px-5 py-4">
      <span
        className={cn(
          "mb-3 grid size-9 place-items-center rounded-[var(--radius-sm)] border border-v3-border-hover text-v3-text-soft [&_svg]:size-[18px]",
          accent && "border-v3-accent/40 bg-v3-accent/10 text-v3-accent",
        )}
        aria-hidden="true"
      >
        {icon}
      </span>
      <p className={cn("font-mono text-4xl font-black tabular-nums text-white", accent && "text-v3-accent")}>
        {value == null ? "—" : fmt(display)}
      </p>
      <p className="mt-2 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-v3-text-soft">
        {label}
      </p>
    </div>
  );
}
