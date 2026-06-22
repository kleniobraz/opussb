"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api";
import { FeaturedCard, ProjectCard } from "../project-card";

export function Recent() {
  const { data } = useQuery({ queryKey: ["stats"], queryFn: api.getStats });
  const recent = data?.recent ?? [];
  if (!recent.length) return null;
  const [first, ...rest] = recent;

  return (
    <section className="home-recent">
      <div className="section-head">
        <div>
          <p className="eyebrow">Recentes</p>
          <h2 className="results-title">Seus projetos</h2>
        </div>
        <Link className="link-gold" href="/projetos">
          Ver todos →
        </Link>
      </div>
      <FeaturedCard p={first} />
      {rest.length > 0 && (
        <div className="projects-grid">
          {rest.map((p) => (
            <ProjectCard key={p.id} p={p} />
          ))}
        </div>
      )}
    </section>
  );
}
