"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { V3Card } from "../ui/v3-card";
import { V3GhostNumber } from "../ui/v3-ghost-number";

const STEPS = [
  { name: "Extrai o áudio", desc: "Separa a trilha de voz do vídeo enviado." },
  { name: "Transcreve a fala", desc: "Gera a transcrição com marcação de tempo." },
  { name: "Corta os momentos", desc: "Pontua e seleciona os melhores trechos." },
  { name: "Reformata 9:16", desc: "Ajusta enquadramento e aplica legendas." },
  { name: "Entrega os clipes", desc: "Prontos pra baixar e publicar." },
];

// Faixa "Como funciona": entra no scroll (IntersectionObserver) e destaca um passo por vez.
// Respeita prefers-reduced-motion (cards estaticos, sem reveal nem ciclo).
export function HowItWorks() {
  const rootRef = useRef<HTMLElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [reveal, setReveal] = useState(false);
  const [inView, setInView] = useState(false);
  const [active, setActive] = useState<number | null>(null);

  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const el = rootRef.current;
    if (!el) return;
    setReveal(true);
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setInView(true);
          io.disconnect();
        }
      },
      { threshold: 0.25 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!inView) return;
    let i = 0;
    const start = setTimeout(() => {
      setActive(0);
      intervalRef.current = setInterval(() => {
        i = (i + 1) % STEPS.length;
        setActive(i);
      }, 1100);
    }, 750);
    return () => {
      clearTimeout(start);
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [inView]);

  return (
    <section
      ref={rootRef}
      className="relative overflow-hidden bg-v3-surface-1 px-7 pb-24 pt-14"
      aria-label="Como funciona"
    >
      <div
        className={cn(
          "relative mx-auto max-w-[1280px] translate-y-0 opacity-100 transition-all duration-700 ease-out",
          reveal && !inView && "translate-y-6 opacity-0",
        )}
      >
        <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
          Como funciona
        </p>
        <h2 className="mt-3 max-w-xl font-v3-display text-3xl font-black uppercase leading-[1.05] text-white sm:text-4xl">
          Do upload ao clipe
          <br />
          em cinco passos
        </h2>
        <ol className="mt-14 flex flex-wrap items-stretch">
          {STEPS.map((s, i) => (
            <Fragment key={i}>
              {i > 0 && (
                <span
                  className={cn(
                    "flex items-center px-3 font-mono text-xl transition-colors duration-500",
                    active !== null && i - 1 === active ? "text-v3-accent" : "text-v3-text-disabled",
                  )}
                  aria-hidden="true"
                >
                  →
                </span>
              )}
              <li className="relative min-w-[170px] flex-1 basis-[170px]">
                <V3Card active={active === i} className="relative h-full overflow-hidden p-6">
                  <V3GhostNumber
                    value={String(i + 1).padStart(2, "0")}
                    className="absolute -right-2 -top-9 text-[7rem] sm:text-[8rem]"
                  />
                  <div className="relative">
                    <p className="font-mono text-xs text-v3-text-disabled">0{i + 1}</p>
                    <p className="mt-4 font-v3-display text-base font-black uppercase text-white">{s.name}</p>
                    <p className="mt-2 text-sm leading-relaxed text-v3-text-soft">{s.desc}</p>
                  </div>
                </V3Card>
              </li>
            </Fragment>
          ))}
        </ol>
      </div>
    </section>
  );
}
