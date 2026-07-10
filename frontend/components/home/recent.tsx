"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api";
import { Button } from "../ui/button";
import { HomeFeaturedCard, HomeVideoCard } from "./recent-cards";

export function Recent() {
  const { data } = useQuery({ queryKey: ["stats"], queryFn: api.getStats });
  const recent = data?.recent ?? [];
  if (!recent.length) return null;
  const [first, ...rest] = recent;

  return (
    <section className="mx-auto max-w-[1280px] px-7 py-20">
      <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
            Recentes
          </p>
          <h2 className="mt-2 font-v3-display text-2xl font-black uppercase text-white">Seus projetos</h2>
        </div>
        <Button asChild variant="v3Ghost" size="sm">
          <Link href="/projetos">Ver todos →</Link>
        </Button>
      </div>
      <HomeFeaturedCard p={first} />
      {rest.length > 0 && (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4">
          {rest.map((p) => (
            <HomeVideoCard key={p.id} p={p} />
          ))}
        </div>
      )}
    </section>
  );
}
