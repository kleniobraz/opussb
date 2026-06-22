import Link from "next/link";
import { Play } from "lucide-react";
import { fmtScore, scoreClass, timeAgo, hashStr } from "@/lib/format";
import type { Project } from "@/lib/types";

// Onda de audio generativa (capa quando nao ha thumbnail real). Determinística pelo seed.
function ProjectWave({ seed }: { seed: number }) {
  let h = seed || 1;
  const bars = [];
  for (let i = 0; i < 32; i++) {
    h = (h * 1103515245 + 12345) >>> 0;
    const amp = 0.16 + ((h % 1000) / 1000) * 0.84;
    const bh = Math.max(2, Math.round(amp * 46));
    const y = ((60 - bh) / 2).toFixed(1);
    bars.push(<rect key={i} x={4 + i * 6} y={y} width={3} height={bh} rx={1.5} />);
  }
  return (
    <svg className="project-wave" viewBox="0 0 200 60" preserveAspectRatio="none" aria-hidden="true">
      {bars}
    </svg>
  );
}

function ProjectPill({ p }: { p: Project }) {
  if (p.status === "processing")
    return (
      <span className="project-pill is-proc">
        <span className="pill-dot" />
        processando
      </span>
    );
  if (p.status === "error") return <span className="project-pill is-err">falhou</span>;
  if (p.clip_count != null)
    return (
      <span className="project-pill">
        <Play className="size-3" /> {p.clip_count} {p.clip_count === 1 ? "clipe" : "clipes"}
      </span>
    );
  return null;
}

function GenCover({ p }: { p: Project }) {
  const seed = hashStr(p.id || p.title || p.filename);
  const hue = seed % 360;
  const hue2 = (hue + 38) % 360;
  const bg =
    `radial-gradient(115% 120% at 82% 12%, hsla(${hue},62%,56%,0.20), transparent 58%),` +
    `radial-gradient(120% 120% at 12% 92%, hsla(${hue2},55%,50%,0.13), transparent 55%),` +
    `linear-gradient(150deg,#17171b,#0f1012)`;
  return (
    <div className="project-thumb project-thumb-gen" style={{ background: bg }}>
      <ProjectWave seed={seed} />
      <span className="project-mark-sm" aria-hidden="true">
        SB Clips
      </span>
    </div>
  );
}

export function ProjectCard({ p }: { p: Project }) {
  return (
    <Link className="project-card" href={`/projetos/${p.id}`}>
      {p.thumbnail_url ? (
        <div className="project-thumb">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="project-thumb-img" src={p.thumbnail_url} alt="" />
          {p.avg_score != null && (
            <span className={`score-badge ${scoreClass(p.avg_score)}`}>{fmtScore(p.avg_score)}</span>
          )}
          <ProjectPill p={p} />
        </div>
      ) : (
        <div className="relative">
          <GenCover p={p} />
          {p.avg_score != null && (
            <span
              className={`score-badge ${scoreClass(p.avg_score)}`}
              style={{ position: "absolute", top: 11, right: 11, zIndex: 2 }}
            >
              {fmtScore(p.avg_score)}
            </span>
          )}
          <ProjectPill p={p} />
        </div>
      )}
      <div className="project-body">
        <p className="project-title">{p.title || p.filename || "Projeto"}</p>
        <p className="project-meta">{timeAgo(p.created_at)}</p>
        <span className="project-open">
          Abrir <span aria-hidden="true">→</span>
        </span>
      </div>
    </Link>
  );
}

function FeaturedReel({ p }: { p: Project }) {
  const n = Math.max(2, Math.min(p.clip_count || 3, 4));
  const tiles = [];
  for (let i = 0; i < n; i++) {
    const seed = hashStr((p.id || "p") + ":" + i);
    const hue = seed % 360;
    const hue2 = (hue + 38) % 360;
    const bg =
      `radial-gradient(120% 90% at 50% 12%, hsla(${hue},62%,56%,0.22), transparent 60%),` +
      `radial-gradient(120% 120% at 12% 92%, hsla(${hue2},55%,50%,0.13), transparent 55%),` +
      `linear-gradient(150deg,#17171b,#0f1012)`;
    tiles.push(
      <div className="featured-tile" key={i} style={{ background: bg }}>
        <ProjectWave seed={seed} />
      </div>,
    );
  }
  return <div className="featured-reel">{tiles}</div>;
}

export function FeaturedCard({ p }: { p: Project }) {
  const good = p.avg_score != null && p.avg_score >= 8;
  const score = p.avg_score != null ? fmtScore(p.avg_score) : "—";
  const meta = (p.clip_count != null ? p.clip_count + " clipes · " : "") + timeAgo(p.created_at);
  const href = `/projetos/${p.id}`;
  return (
    <div className="featured-card">
      <Link className="featured-thumb" href={href}>
        {p.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img className="project-thumb-img" src={p.thumbnail_url} alt="" />
        ) : (
          <FeaturedReel p={p} />
        )}
        <span className="featured-tag">Último projeto</span>
      </Link>
      <div className="featured-body">
        <div className="featured-score-row">
          <span
            className="featured-score"
            style={{ color: good ? "var(--green)" : "var(--gold)" }}
          >
            {score}
          </span>
          <span className="featured-score-label">nota média dos clipes</span>
        </div>
        <div className="featured-title">{p.title || p.filename || "Projeto"}</div>
        <div className="featured-meta">{meta}</div>
        <Link href={href} className="link-gold btn-cta mt-[22px] inline-block">
          Abrir projeto →
        </Link>
      </div>
    </div>
  );
}
