import Link from "next/link";
import { Play } from "lucide-react";
import { fmtScore, timeAgo, hashStr, nameFromEmail } from "@/lib/format";
import type { Project } from "@/lib/types";
import { cn } from "@/lib/utils";
import { V3Card } from "../ui/v3-card";

// Cartao exclusivo da tela de Projetos (redesign v3, Fase 2). Nao reusa o ProjectCard antigo
// (components/project-card.tsx, que continua servindo Podcast) nem HomeVideoCard (Home tem o
// conceito de destaque, Projetos mostra a grade toda uniforme). Espirito visual identico ao
// HomeVideoCard de recent-cards.tsx, por isso duplica capa gerada/status/rota.

// Podcast (§39) abre a tela propria (aprovacao/montagem/player), nao a pagina de clipes. Projetos
// filtra job_type !== "podcast", mas mantem a funcao completa por consistencia com o resto do app.
function projectHref(p: Project) {
  return p.job_type === "podcast" || p.status === "aguardando_aprovacao" || p.status === "gancho_aprovado"
    ? `/podcast/${p.id}`
    : `/projetos/${p.id}`;
}

function coverGradient(seed: number) {
  const hue = seed % 360;
  const hue2 = (hue + 38) % 360;
  return (
    `radial-gradient(115% 120% at 82% 12%, hsla(${hue},62%,56%,0.16), transparent 58%),` +
    `radial-gradient(120% 120% at 12% 92%, hsla(${hue2},55%,50%,0.12), transparent 55%),` +
    `linear-gradient(150deg, var(--v3-surface-2), var(--v3-surface-1))`
  );
}

// Onda de audio generativa (capa quando nao ha thumbnail real), deterministica pelo seed.
function CoverWave({ seed }: { seed: number }) {
  let h = seed || 1;
  const bars: number[] = [];
  for (let i = 0; i < 26; i++) {
    h = (h * 1103515245 + 12345) >>> 0;
    bars.push(0.16 + ((h % 1000) / 1000) * 0.84);
  }
  return (
    <div className="flex h-full w-full items-center justify-center gap-[3px] px-5" aria-hidden="true">
      {bars.map((amp, i) => (
        <span
          key={i}
          className="w-[3px] flex-1 rounded-full bg-v3-accent/50"
          style={{ height: `${10 + amp * 60}%` }}
        />
      ))}
    </div>
  );
}

function StatusPill({ p }: { p: Project }) {
  const base =
    "absolute left-2 top-2 z-[2] inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[0.62rem] font-semibold uppercase tracking-wide backdrop-blur-sm";
  if (p.status === "processing")
    return (
      <span className={cn(base, "border-v3-accent/50 bg-v3-bg/70 text-v3-accent")}>
        <span className="size-1.5 animate-pulse rounded-full bg-v3-accent" />
        processando
      </span>
    );
  if (p.status === "error")
    return <span className={cn(base, "border-[var(--red)]/40 bg-v3-bg/70 text-[var(--red)]")}>falhou</span>;
  if (p.status === "uploaded")
    return (
      <span className={cn(base, "border-v3-border-hover bg-v3-bg/70 text-v3-text-soft")}>
        aguardando disparo
      </span>
    );
  if (p.status === "aguardando_aprovacao")
    return (
      <span className={cn(base, "border-v3-accent/50 bg-v3-bg/70 text-v3-accent")}>
        <span className="size-1.5 animate-pulse rounded-full bg-v3-accent" />
        aprovar gancho
      </span>
    );
  if (p.status === "gancho_aprovado")
    return <span className={cn(base, "border-v3-border-hover bg-v3-bg/70 text-v3-text-soft")}>gancho aprovado</span>;
  if (p.job_type === "podcast" && p.status === "done")
    return <span className={cn(base, "border-v3-border-hover bg-v3-bg/70 text-v3-text-soft")}>episódio montado</span>;
  if (p.clip_count != null)
    return (
      <span className={cn(base, "border-v3-border-hover bg-v3-bg/70 text-v3-text-soft")}>
        <Play className="size-3" /> {p.clip_count} {p.clip_count === 1 ? "clipe" : "clipes"}
      </span>
    );
  return null;
}

export function ProjectCard({ p }: { p: Project }) {
  const href = projectHref(p);
  const seed = hashStr(p.id || p.title || p.filename);
  return (
    <Link href={href} className="group block h-full">
      <V3Card className="flex h-full flex-col overflow-hidden transition-colors group-hover:border-v3-border-hover">
        <div className="relative aspect-[16/10] overflow-hidden border-b border-v3-border">
          {p.thumbnail_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={p.thumbnail_url} alt="" loading="lazy" className="h-full w-full object-cover" />
          ) : (
            <div className="h-full w-full" style={{ background: coverGradient(seed) }}>
              <CoverWave seed={seed} />
            </div>
          )}
          {p.avg_score != null && (
            <span className="absolute right-2 top-2 z-[2] rounded-full bg-v3-bg/80 px-2 py-0.5 font-mono text-[0.7rem] font-bold text-v3-accent">
              {fmtScore(p.avg_score)}
            </span>
          )}
          <StatusPill p={p} />
        </div>
        <div className="flex flex-1 flex-col p-4">
          <p className="line-clamp-2 min-h-[2.5em] font-v3-sans text-sm font-bold leading-snug text-white">
            {p.title || p.filename || "Projeto"}
          </p>
          <p className="mt-2 font-mono text-xs text-v3-text-soft">
            {timeAgo(p.created_at)}
            {p.owner_email ? ` · por ${nameFromEmail(p.owner_email)}` : ""}
          </p>
          <span className="mt-3 inline-flex translate-y-1 items-center gap-1 font-mono text-xs font-semibold uppercase tracking-wide text-v3-accent opacity-0 transition-all duration-150 group-hover:translate-y-0 group-hover:opacity-100">
            Abrir <span aria-hidden="true">→</span>
          </span>
        </div>
      </V3Card>
    </Link>
  );
}
