"use client";

import Link from "next/link";
import { useFavorites } from "@/hooks/use-favorites";
import { ClipGrid } from "@/components/clip-grid";
import { ArrowBurst } from "@/components/arrow-burst";

export default function FavoritosPage() {
  const { data, isLoading } = useFavorites();
  const clips = data?.clips ?? [];

  return (
    <>
      <section className="page-hero">
        <div className="page-hero-inner">
          <p className="eyebrow">Salvos</p>
          <h1 className="page-title">Favoritos</h1>
        </div>
      </section>

      {isLoading ? (
        <div className="view-pad">
          <div className="skel-grid">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="skel skel-card" />
            ))}
          </div>
        </div>
      ) : !clips.length ? (
        <section className="fav-empty">
          <div className="hero-glows" aria-hidden="true">
            <div
              className="glow glow-b"
              style={{ top: "20%", left: "50%", transform: "translateX(-50%)" }}
            />
          </div>
          <div className="fav-empty-inner">
            <ArrowBurst size={120} gradId="favArrow" baseFill="#1F1F24" />
            <h2 className="fav-empty-title">Sem favoritos ainda</h2>
            <p className="fav-empty-text">
              Clique no coração de um clipe para guardá-lo aqui e achar rapidinho depois.
            </p>
            <Link href="/projetos" className="link-gold mt-7 inline-block">
              Explorar projetos →
            </Link>
          </div>
        </section>
      ) : (
        <div className="view-pad">
          <ClipGrid clips={clips} toolbar={clips.length > 1} />
        </div>
      )}
    </>
  );
}
