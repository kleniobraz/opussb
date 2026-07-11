"use client";

import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/api";
import { cn } from "@/lib/utils";
import { V3GhostNumber } from "../ui/v3-ghost-number";

const ITEMS = [
  { key: "processing_count", label: "Processando" },
  { key: "videos_processed", label: "Prontos" },
  { key: "clips_generated", label: "Clipes gerados" },
] as const;

// Faixa de numeros da tela Gerar clipes (nasceu na Home, migrou junto com o gerador).
// A queryKey ["stats"] e a mesma que o onReset do gerador invalida ao terminar um job,
// entao os numeros atualizam sozinhos.
export function StatsStrip() {
  const { data } = useQuery({ queryKey: ["stats"], queryFn: api.getStats });

  return (
    <section className="mx-auto max-w-[1280px] overflow-hidden px-7 py-6">
      <div className="grid grid-cols-1 gap-6 rounded-[var(--radius-md)] border border-v3-border bg-v3-surface-2 px-8 py-7 sm:grid-cols-3 sm:gap-0">
        {ITEMS.map((item, i) => {
          const value = data?.[item.key] ?? 0;
          return (
            <div
              key={item.key}
              className={cn(
                "relative flex flex-col items-start",
                i > 0 && "sm:border-l sm:border-v3-border sm:pl-8",
              )}
            >
              <V3GhostNumber
                value={String(value)}
                className="pointer-events-none absolute -top-4 left-0 hidden text-[6rem] leading-none sm:block"
              />
              <p className="relative font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
                {item.label}
              </p>
              <p className="relative mt-2 font-mono text-4xl font-black text-white sm:text-5xl">{value}</p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
