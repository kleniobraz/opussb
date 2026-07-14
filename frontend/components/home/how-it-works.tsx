"use client";

import { Fragment, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";
import { CLIP_GLOW } from "./hero";

// prefers-reduced-motion via useSyncExternalStore: leitura sincrona no effect com
// setState dispararia react-hooks/set-state-in-effect. No servidor assume false.
const REDUCE_QUERY = "(prefers-reduced-motion: reduce)";
function subscribeReduce(onChange: () => void) {
  const mq = window.matchMedia(REDUCE_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
function usePrefersReducedMotion() {
  return useSyncExternalStore(
    subscribeReduce,
    () => window.matchMedia(REDUCE_QUERY).matches,
    () => false,
  );
}

// Tempo de cada estacao no ciclo da esteira.
const STEP_MS = 2000;

// Alturas fixas (nao aleatorias), mesma linguagem do Eq do hero.
const EQ_HEIGHTS = [40, 72, 52, 95, 60, 80, 45];
const CLIP_EQ_HEIGHTS = [45, 80, 55, 95, 60];

type VisualProps = { on: boolean };

// Estacao 1 (FFMPEG): equalizador pulsando quando ativa, barras paradas quando nao.
function EqVisual({ on }: VisualProps) {
  return (
    <div className="flex h-10 items-end gap-[3px]" aria-hidden="true">
      {EQ_HEIGHTS.map((h, i) => (
        <span
          key={i}
          className={cn(
            "w-1 rounded-[2px] transition-colors duration-300",
            on ? "bg-v3-accent v3-pipe-eq" : "bg-v3-text-disabled",
          )}
          // Delay negativo: as barras ja nascem animando, cada uma em outra fase (stagger).
          style={{ height: `${h}%`, animationDelay: `${i * -130}ms` }}
        />
      ))}
    </div>
  );
}

// Estacao 2 (WHISPER): timecode digitando com cursor. Container de largura FIXA (14ch)
// pra tipagem nao causar layout shift; inativa mostra o texto completo esmaecido.
function TypeVisual({ on }: VisualProps) {
  return (
    <div className="flex w-[14ch] items-center font-mono text-xs font-semibold" aria-hidden="true">
      <span
        className={cn(
          "overflow-hidden whitespace-nowrap transition-colors duration-300",
          on ? "v3-pipe-type text-white" : "text-v3-text-disabled",
        )}
      >
        00:12 → 00:37
      </span>
      <span
        className={cn("ml-[3px] h-[1.1em] w-[2px] shrink-0 bg-v3-accent", on ? "v3-pipe-caret" : "opacity-0")}
      />
    </div>
  );
}

// Estacao 3 (CLAUDE): timeline segmentada; o meio acende em acento com badge de nota
// e as pontas se afastam (corte). Tudo por transition, sem keyframe.
function CutVisual({ on }: VisualProps) {
  return (
    <div className="relative flex items-center" aria-hidden="true">
      <span
        className={cn(
          "h-2 w-6 rounded-[2px] bg-v3-border-hover transition-transform duration-500",
          on && "-translate-x-1",
        )}
      />
      <span
        className={cn(
          "mx-[3px] h-2 w-8 rounded-[2px] transition-colors duration-500",
          on ? "bg-v3-accent" : "bg-v3-border-hover",
        )}
      />
      <span
        className={cn(
          "h-2 w-6 rounded-[2px] bg-v3-border-hover transition-transform duration-500",
          on && "translate-x-1",
        )}
      />
      <span
        className={cn(
          "absolute -top-4 left-1/2 -translate-x-1/2 font-mono text-[9px] font-bold transition-opacity duration-300",
          on ? "text-v3-accent opacity-100" : "opacity-0",
        )}
      >
        9.2
      </span>
    </div>
  );
}

// Estacao 4 (YOLO): contorno que morfa 16:9 <-> 9:16 enquanto ativa; em repouso
// descansa como 9:16 (o resultado do reframe).
function MorphVisual({ on }: VisualProps) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "block h-[58px] w-[33px] rounded-[5px] border-2 transition-all duration-300",
        on ? "border-v3-accent v3-pipe-morph" : "border-v3-text-disabled",
      )}
    />
  );
}

// Estacao 5 (S3): mini cartao 9:16 (mesma cara dos cartoes do hero) com pop de entrada.
function ClipVisual({ on }: VisualProps) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "relative flex h-16 w-9 items-center justify-center overflow-hidden rounded-[6px] border transition-all duration-300",
        on ? "border-v3-accent v3-pipe-pop" : "border-v3-border-hover opacity-60",
      )}
      style={{ background: CLIP_GLOW }}
    >
      <span className="flex h-[36%] w-[64%] items-center gap-[2px]">
        {CLIP_EQ_HEIGHTS.map((h, i) => (
          <span key={i} className="flex-1 rounded-[1px] bg-v3-accent" style={{ height: `${h}%` }} />
        ))}
      </span>
      <span className="absolute right-[3px] top-[3px] rounded-full bg-v3-bg/80 px-1 py-px font-mono text-[0.5rem] font-bold text-v3-accent">
        9.2
      </span>
    </div>
  );
}

const STEPS = [
  { title: "Extrai o áudio", Visual: EqVisual },
  { title: "Transcreve", Visual: TypeVisual },
  { title: "Corta os momentos", Visual: CutVisual },
  { title: "Reformata 9:16", Visual: MorphVisual },
  { title: "Entrega os clipes", Visual: ClipVisual },
] as const;

// "Como funciona" na Home: esteira de producao. Cinco estacoes abertas sobre a faixa
// (sem card por estacao) ligadas por uma linha de pipeline; um timer avanca a estacao
// ativa enquanto a secao esta visivel e o preenchimento de acento flui pela linha.
// Com prefers-reduced-motion: sem ciclo nem reveal, tudo aceso e a linha preenchida
// (as animacoes CSS tambem desligam via media query em brand-v3.css).
export function HowItWorks() {
  const rootRef = useRef<HTMLElement>(null);
  const [reveal, setReveal] = useState(false);
  const [inView, setInView] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    if (reduced) return;
    const el = rootRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setInView(true);
          io.disconnect();
        } else {
          // Primeiro callback ainda fora da viewport: arma o reveal (esconde pra
          // revelar em stagger). Se a secao ja nasce visivel, nem esconde.
          setReveal(true);
        }
      },
      { threshold: 0.3 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [reduced]);

  useEffect(() => {
    if (!inView || reduced) return;
    let i = 0;
    let interval: ReturnType<typeof setInterval> | null = null;
    // Espera o reveal em stagger assentar antes de ligar a esteira.
    const start = setTimeout(() => {
      setActive(0);
      interval = setInterval(() => {
        i = (i + 1) % STEPS.length;
        setActive(i);
      }, STEP_MS);
    }, 900);
    return () => {
      clearTimeout(start);
      if (interval) clearInterval(interval);
    };
  }, [inView, reduced]);

  return (
    <section ref={rootRef} aria-label="Como funciona" className="border-y border-v3-border bg-v3-surface-2">
      <div className="mx-auto max-w-[1280px] px-7 py-16 sm:py-20">
        <div
          className={cn(
            "transition-all duration-700 ease-out",
            reveal && !inView && "translate-y-5 opacity-0",
          )}
        >
          <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
            Como funciona
          </p>
          <h2 className="mt-3 font-v3-display text-3xl font-black uppercase leading-[1.05] text-white sm:text-4xl">
            Do bruto ao clipe
          </h2>
        </div>

        <ol className="mt-12 flex flex-col md:mt-16 md:flex-row md:items-start">
          {STEPS.map((s, i) => {
            const Visual = s.Visual;
            const on = reduced || active === i;
            // Trecho de linha ANTES da estacao i: preenchido quando o dado ja passou por ela.
            const filled = reduced || (active !== null && active >= i);
            return (
              <Fragment key={s.title}>
                {i > 0 && (
                  <span
                    aria-hidden="true"
                    className={cn(
                      // Mobile: segmento vertical alinhado ao centro da coluna do visual (w-24).
                      // Desktop: linha horizontal na altura do meio da area do visual (h-20).
                      "relative mb-1 ml-[47px] mt-1 h-9 w-[2px] shrink-0 overflow-hidden bg-v3-border",
                      "md:mb-0 md:ml-0 md:mt-[39px] md:h-[2px] md:w-auto md:flex-[2]",
                      "transition-all duration-700 ease-out",
                      reveal && !inView && "translate-y-5 opacity-0",
                    )}
                    style={{ transitionDelay: `${i * 100 - 50}ms` }}
                  >
                    <span
                      className={cn(
                        "absolute inset-0 origin-top-left bg-v3-accent transition-transform",
                        filled ? "scale-100 duration-500" : "scale-0 duration-200",
                      )}
                    />
                  </span>
                )}
                <li
                  className={cn(
                    "flex items-center gap-5 transition-all duration-700 ease-out",
                    "md:min-w-0 md:flex-[3] md:flex-col md:gap-0",
                    reveal && !inView && "translate-y-5 opacity-0",
                  )}
                  style={{ transitionDelay: `${i * 100}ms` }}
                >
                  <div className="flex h-20 w-24 shrink-0 items-center justify-center md:w-full">
                    <Visual on={on} />
                  </div>
                  <div className="min-w-0 md:mt-4 md:text-center">
                    <p
                      className={cn(
                        "font-v3-sans text-[15px] font-bold transition-colors duration-300",
                        on ? "text-white" : "text-v3-text-soft",
                      )}
                    >
                      {s.title}
                    </p>
                  </div>
                </li>
              </Fragment>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
