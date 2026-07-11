"use client";

import { useAuth } from "@/providers/auth-provider";
import { firstNameFromEmail, greetingForHour } from "@/lib/format";
import { cn } from "@/lib/utils";
import { BrandMark } from "../brand-mark";
import { V3Card } from "../ui/v3-card";

// Alturas fixas (nao aleatorias) pro equalizador da capa de cada miniatura de clipe.
const EQ_HEIGHTS = [34, 60, 46, 82, 54, 100, 50, 76, 40, 64, 30];

// Glow radial (acento v3) atras do equalizador, no lugar do preenchimento chapado de antes.
// color-mix (nao rgba fixo) pra seguir --v3-accent se a paleta mudar.
// Exportado: o mini cartao da esteira "Como funciona" (how-it-works.tsx) usa o mesmo fundo.
export const CLIP_GLOW =
  "radial-gradient(120% 85% at 50% 10%, color-mix(in srgb, var(--v3-accent) 18%, transparent), transparent 60%)," +
  "linear-gradient(160deg, var(--v3-surface-2), var(--v3-surface-1))";

// Versao translucida do CLIP_GLOW pros cartoes de vidro do hero: o linear-gradient
// opaco cobriria o veu do .v3-glass; com color-mix o desenho continua o mesmo, mas
// deixa as particulas vazarem desfocadas atras do cartao.
const CLIP_GLOW_GLASS =
  "radial-gradient(120% 85% at 50% 10%, color-mix(in srgb, var(--v3-accent) 18%, transparent), transparent 60%)," +
  "linear-gradient(160deg, color-mix(in srgb, var(--v3-surface-2) 55%, transparent), color-mix(in srgb, var(--v3-surface-1) 40%, transparent))";

function Eq() {
  return (
    <div className="flex h-[42%] w-[70%] items-center gap-[2px]" aria-hidden="true">
      {EQ_HEIGHTS.map((h, i) => (
        <span key={i} className="flex-1 rounded-[2px] bg-v3-accent" style={{ height: `${h}%` }} />
      ))}
    </div>
  );
}

function HeroClip({
  className,
  animationClassName,
  score,
  dur,
  active,
}: {
  className: string;
  animationClassName: string;
  score: string;
  dur: string;
  active?: boolean;
}) {
  return (
    // Sem shadow-[var(--shadow-hero)]: sob o vidro o utilitario vira codigo morto
    // (o box-shadow do .v3-glass, fora de @layer, sobrepoe e ja inclui sombra).
    <V3Card
      glass
      active={active}
      className={cn(
        "absolute aspect-[9/16] w-12 overflow-hidden rounded-[var(--radius-md)] motion-reduce:animate-none",
        animationClassName,
        className,
      )}
    >
      <div className="relative flex h-full w-full items-center justify-center" style={{ background: CLIP_GLOW_GLASS }}>
        <Eq />
        {active && (
          // Agulha de progresso (scrub), so no cartao da frente: reforca a leitura de "video tocando".
          <span
            className="absolute inset-y-[14%] left-1/2 w-[2px] rounded-full bg-v3-accent motion-reduce:hidden animate-[v3HeroScrub_2.6s_var(--ease)_infinite]"
            style={{ boxShadow: "0 0 6px 1px var(--v3-glow)" }}
            aria-hidden="true"
          />
        )}
        <span className="absolute right-1 top-1 rounded-full bg-v3-bg/80 px-1 py-px font-mono text-[0.5rem] font-bold text-v3-accent">
          {score}
        </span>
        <span className="absolute bottom-1 left-1 rounded-[3px] bg-black/60 px-1 py-px font-mono text-[0.45rem] font-semibold text-white">
          {dur}
        </span>
      </div>
    </V3Card>
  );
}

export function Hero() {
  const { user } = useAuth();
  const name = firstNameFromEmail(user?.email);
  const greeting = greetingForHour(new Date().getHours());

  return (
    <div className="relative z-[1] mx-auto flex max-w-[1280px] items-center justify-between gap-6 px-7 py-5">
      <div className="v3-hero-mark hidden lg:block" aria-hidden="true">
        <BrandMark />
      </div>
      <div className="min-w-[240px]">
        <p className="font-v3-sans text-lg font-bold leading-snug text-white sm:text-xl">
          {greeting}, {name}
          <span className="text-v3-accent">.</span>
        </p>
      </div>
      <div
        className="relative hidden h-[104px] w-[108px] flex-none animate-[heroVisualIn_0.8s_var(--ease-out)_both] motion-reduce:animate-none lg:block"
        aria-hidden="true"
      >
        <HeroClip
          className="left-0 top-[5px] z-[2]"
          animationClassName="animate-[v3HeroFloatMid_7s_var(--ease)_infinite_0.5s]"
          score="8.1"
          dur="0:42"
        />
        <HeroClip
          className="left-[60px] top-0 z-[1]"
          animationClassName="animate-[v3HeroFloatBack_7.6s_var(--ease)_infinite_1s]"
          score="7.3"
          dur="1:05"
        />
        <HeroClip
          className="left-[31px] top-[13px] z-[3]"
          animationClassName="animate-[v3HeroFloatFront_6s_var(--ease)_infinite]"
          score="9.2"
          dur="0:28"
          active
        />
      </div>
    </div>
  );
}
