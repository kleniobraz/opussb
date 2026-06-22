"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Download, Copy, Heart, Maximize2, Pencil, Play } from "lucide-react";
import { toast } from "sonner";
import { fmtScore, scoreClass, fmtSeconds } from "@/lib/format";
import { useToggleFavorite } from "@/hooks/use-favorites";
import { ClipVideo } from "./clip-video";
import { ClipPlayerDialog } from "./clip-player-dialog";
import type { Clip } from "@/lib/types";

export function ClipCard({ clip, index = 0 }: { clip: Clip; index?: number }) {
  const [fav, setFav] = useState(!!clip.favorite);
  const [open, setOpen] = useState(false);
  const toggle = useToggleFavorite();
  const router = useRouter();

  const title = clip.title || `Clipe ${index + 1}`;
  const url = clip.url || "";
  const playUrl = url;

  function openEditor() {
    router.push(
      `/editar/${encodeURIComponent(clip.id)}?t=${encodeURIComponent(title)}`,
    );
  }

  function toggleFav() {
    const next = !fav;
    setFav(next);
    toggle.mutate({ id: clip.id, on: next });
    toast.success(next ? "Adicionado aos favoritos" : "Removido dos favoritos");
  }

  function copyLink() {
    if (!url) return;
    const abs = /^https?:/i.test(url) ? url : location.origin + url;
    navigator.clipboard.writeText(abs).then(
      () => toast.success("Link copiado — abrir exige login na sua conta."),
      () => toast.error("Não consegui copiar"),
    );
  }

  return (
    <article className="clip-card" data-clip-id={clip.id}>
      {url ? (
        <ClipVideo src={playUrl}>
          {clip.score != null && (
            <span className={`score-badge ${scoreClass(clip.score)}`}>{fmtScore(clip.score)}</span>
          )}
          {clip.duration != null && <span className="clip-dur">{fmtSeconds(clip.duration)}</span>}
          <button
            type="button"
            className={`clip-fav${fav ? " is-on" : ""}`}
            onClick={toggleFav}
            aria-pressed={fav}
            title="Favoritar"
          >
            <Heart className="size-4" />
          </button>
          <button
            type="button"
            className="clip-max"
            onClick={() => setOpen(true)}
            aria-label="Abrir clipe"
          >
            <Maximize2 className="size-4" />
          </button>
        </ClipVideo>
      ) : (
        <div className="clip-video">
          <div className="clip-ph">
            <span className="clip-ph-glyph">
              <Play className="size-4" />
            </span>
            <span className="clip-ph-note">prévia indisponível</span>
          </div>
        </div>
      )}

      <div className="clip-body">
        <h3 className="clip-title">{title}</h3>
        {clip.reason && <p className="clip-reason">{clip.reason}</p>}
      </div>

      <div className="clip-actions">
        {url ? (
          <a className="clip-download" href={url} download>
            <Download className="ic size-4" />
            Baixar
          </a>
        ) : (
          <button className="clip-download" type="button" disabled>
            <Download className="ic size-4" />
            Baixar
          </button>
        )}
        <button
          type="button"
          className="clip-copy"
          onClick={copyLink}
          disabled={!url}
          title="Copiar link"
        >
          <Copy className="ic size-4" />
        </button>
        {url && (
          <button
            type="button"
            className="clip-edit"
            onClick={openEditor}
            title="Editar legenda e música"
            aria-label="Editar legenda e música"
          >
            <Pencil className="ic size-4" />
          </button>
        )}
      </div>

      {url && (
        <ClipPlayerDialog
          clip={clip}
          open={open}
          onOpenChange={setOpen}
          fav={fav}
          onToggleFav={toggleFav}
        />
      )}
    </article>
  );
}
