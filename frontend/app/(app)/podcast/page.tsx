"use client";

import { PodcastUpload } from "@/components/podcast/podcast-upload";
import { PodcastList } from "@/components/podcast/podcast-list";
import { ParticlesBackground } from "@/components/backgrounds/particles-background";

export default function PodcastPage() {
  return (
    <div className="relative bg-v3-bg">
      <section className="relative -mt-20 overflow-hidden border-b border-v3-border pb-8 pt-20">
        <div className="v3-home-glow" aria-hidden="true" />
        <ParticlesBackground />
        <div className="relative z-[1] mx-auto max-w-[1280px] px-7 pt-8">
          <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
            Biblioteca
          </p>
          <h1 className="mt-2 font-v3-display text-4xl font-black uppercase text-white sm:text-5xl lg:text-6xl">
            Podcasts
          </h1>
        </div>
      </section>

      {/* Fora da faixa de atmosfera (glow/particulas), mesmo raciocinio de projetos/page.tsx:
          upload + grade sao conteudo variavel, nao podem esticar a secao do glow junto. */}
      <div className="relative z-[1] mx-auto max-w-[1280px] space-y-8 px-7 py-10">
        <PodcastUpload />
        <PodcastList />
      </div>
    </div>
  );
}
