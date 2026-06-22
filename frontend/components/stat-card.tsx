"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

// Card de estatistica com count-up (0 -> valor). Respeita prefers-reduced-motion.
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
    <div className="stat-card">
      <span className={cn("stat-ic", accent && "is-gold")} aria-hidden="true">
        {icon}
      </span>
      <p className={cn("stat-value", accent && "is-gold")}>
        {value == null ? "—" : fmt(display)}
      </p>
      <p className="stat-label">{label}</p>
    </div>
  );
}
