"use client";

import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { Scissors, Folder, Podcast, Sparkles, Heart } from "lucide-react";
import { Hero } from "./hero";
import { HowItWorks } from "./how-it-works";
import { ParticlesBackground } from "../backgrounds/particles-background";
import { V3Card } from "../ui/v3-card";

// A Home virou uma tela de boas-vindas com atalhos: o gerador de clipes migrou pra
// rota /gerar (app/(app)/gerar/page.tsx). Os "recentes" sairam da Home nesta fase;
// components/home/recent.tsx continua pronto pra voltar se sentirmos falta.

// Mesmos icones do menu (nav-items.ts) pra manter a coerencia visual.
const SHORTCUTS: { label: string; href: string; icon: LucideIcon; desc: string }[] = [
  { label: "Projetos", href: "/projetos", icon: Folder, desc: "Sua biblioteca de clipes gerados." },
  { label: "Podcasts", href: "/podcast", icon: Podcast, desc: "Monte episódios completos com vinheta." },
  { label: "Revisor IA", href: "/revisor", icon: Sparkles, desc: "Revisão de legendas queimadas no vídeo." },
  { label: "Favoritos", href: "/favoritos", icon: Heart, desc: "Os clipes que você salvou pra depois." },
];

export function HomeView() {
  return (
    <div className="relative bg-v3-surface-1">
      <section className="relative overflow-hidden border-b border-v3-border pb-8 pt-10">
        <div className="v3-home-glow" aria-hidden="true" />
        <ParticlesBackground />
        <Hero />
      </section>

      <section className="mx-auto max-w-[1280px] px-7 py-12" aria-label="Acesso rápido">
        <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
          Acesso rápido
        </p>

        <div className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
          <Link href="/gerar" className="group block sm:col-span-2 lg:col-span-4">
            <V3Card active className="flex flex-col gap-5 p-8 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-5">
                <span className="grid size-12 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-v3-accent/14 text-v3-accent">
                  <Scissors className="size-6" strokeWidth={1.5} />
                </span>
                <div>
                  <p className="font-v3-display text-2xl font-black uppercase text-white sm:text-3xl">
                    Gerar clipes
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-v3-text-soft">
                    Envie um vídeo longo e receba os melhores momentos prontos.
                  </p>
                </div>
              </div>
              <span className="inline-flex shrink-0 items-center gap-1 font-mono text-xs font-semibold uppercase tracking-wide text-v3-accent transition-transform duration-150 group-hover:translate-x-1">
                Começar <span aria-hidden="true">→</span>
              </span>
            </V3Card>
          </Link>

          {SHORTCUTS.map((s) => {
            const Icon = s.icon;
            return (
              <Link key={s.href} href={s.href} className="group block">
                <V3Card className="flex h-full flex-col p-6 transition-colors group-hover:border-v3-border-hover">
                  <span className="grid size-10 place-items-center rounded-[var(--radius-sm)] bg-v3-ghost text-v3-text-soft transition-colors group-hover:text-v3-accent">
                    <Icon className="size-5" strokeWidth={1.5} />
                  </span>
                  <p className="mt-4 font-v3-sans text-base font-bold text-white">{s.label}</p>
                  <p className="mt-1 flex-1 text-sm leading-relaxed text-v3-text-soft">{s.desc}</p>
                  <span className="mt-4 inline-flex translate-y-1 items-center gap-1 font-mono text-xs font-semibold uppercase tracking-wide text-v3-accent opacity-0 transition-all duration-150 group-hover:translate-y-0 group-hover:opacity-100">
                    Abrir <span aria-hidden="true">→</span>
                  </span>
                </V3Card>
              </Link>
            );
          })}
        </div>
      </section>

      <HowItWorks />
    </div>
  );
}
