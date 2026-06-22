"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

const STEPS = [
  { name: "Extrai o áudio", desc: "Separa a trilha de voz do vídeo enviado." },
  { name: "Transcreve a fala", desc: "Gera a transcrição com marcação de tempo." },
  { name: "Corta os momentos", desc: "Pontua e seleciona os melhores trechos." },
  { name: "Reformata 9:16", desc: "Ajusta enquadramento e aplica legendas." },
  { name: "Entrega os clipes", desc: "Prontos pra baixar e publicar.", dark: true },
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
      className={cn("howb", reveal && "howb--reveal", inView && "howb--in")}
      aria-label="Como funciona"
    >
      <div className="howb-inner">
        <p className="howb-eyebrow">Como funciona</p>
        <h2 className="howb-title">
          Do upload ao clipe
          <br />
          em cinco passos
        </h2>
        <ol className="howb-track">
          {STEPS.map((s, i) => (
            <Fragment key={i}>
              {i > 0 && (
                <span className="howb-arrow" aria-hidden="true">
                  →
                </span>
              )}
              <li
                className={cn(
                  "howb-step",
                  s.dark && "howb-step--dark",
                  active === i && "is-active",
                )}
              >
                <div className="howb-num">{i + 1}</div>
                <div className="howb-name">{s.name}</div>
                <div className="howb-desc">{s.desc}</div>
              </li>
            </Fragment>
          ))}
        </ol>
      </div>
    </section>
  );
}
