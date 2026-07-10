"use client";

import { useAuth } from "@/providers/auth-provider";
import { firstNameFromEmail, greetingForHour } from "@/lib/format";
import { cn } from "@/lib/utils";
import { V3Card } from "../ui/v3-card";
import { V3Divider } from "../ui/v3-divider";

// Alturas fixas (nao aleatorias) pro equalizador da capa de cada miniatura de clipe.
const EQ_HEIGHTS = [34, 60, 46, 82, 54, 100, 50, 76, 40, 64, 30];

function Eq() {
  return (
    <div className="flex h-[42%] w-[70%] items-center gap-[3px]" aria-hidden="true">
      {EQ_HEIGHTS.map((h, i) => (
        <span key={i} className="flex-1 rounded-[3px] bg-v3-accent" style={{ height: `${h}%` }} />
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
    <V3Card
      active={active}
      className={cn(
        "absolute aspect-[9/16] w-[132px] overflow-hidden rounded-[var(--radius-lg)] motion-reduce:animate-none",
        animationClassName,
        className,
      )}
    >
      <div className="relative flex h-full w-full items-center justify-center bg-v3-surface-1">
        <Eq />
        <span className="absolute right-2 top-2 rounded-full bg-v3-bg/80 px-2 py-0.5 font-mono text-[0.65rem] font-bold text-v3-accent">
          {score}
        </span>
        <span className="absolute bottom-2 left-2 rounded-md bg-black/60 px-1.5 py-0.5 font-mono text-[0.6rem] font-semibold text-white">
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
    <div className="relative z-[1] mx-auto flex max-w-[1280px] flex-wrap items-center justify-between gap-8 px-7 pb-9 pt-16">
      <div className="min-w-[320px] flex-1">
        <div className="mb-5 flex items-center gap-3">
          <V3Divider className="w-6" />
          <span className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
            Central de clipes
          </span>
        </div>
        <h1
          className="font-v3-display font-black leading-[1.02] text-white"
          style={{ fontSize: "clamp(40px,5.6vw,72px)", letterSpacing: "-0.035em" }}
        >
          {greeting},
          <br />
          {name}
          <span className="text-v3-accent">.</span>
        </h1>
        <p className="mt-4 max-w-[480px] text-base leading-relaxed text-v3-text-soft">
          Solte um vídeo longo abaixo e o SB Clips devolve os melhores momentos cortados,
          reformatados e legendados.
        </p>
      </div>
      <div
        className="relative hidden h-[290px] w-[300px] flex-none animate-[heroVisualIn_0.8s_var(--ease-out)_both] motion-reduce:animate-none lg:block"
        aria-hidden="true"
      >
        <HeroClip
          className="left-1 top-[14px] z-[2]"
          animationClassName="animate-[v3HeroFloatMid_7s_var(--ease)_infinite_0.5s]"
          score="8.1"
          dur="0:42"
        />
        <HeroClip
          className="left-[168px] top-1 z-[1]"
          animationClassName="animate-[v3HeroFloatBack_7.6s_var(--ease)_infinite_1s]"
          score="7.3"
          dur="1:05"
        />
        <HeroClip
          className="left-[86px] top-[36px] z-[3]"
          animationClassName="animate-[v3HeroFloatFront_6s_var(--ease)_infinite]"
          score="9.2"
          dur="0:28"
          active
        />
      </div>
    </div>
  );
}
