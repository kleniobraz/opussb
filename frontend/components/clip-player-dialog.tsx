"use client";

import { Download, Copy, Heart } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "./ui/dialog";
import { Button } from "./ui/button";
import { ClipVideo } from "./clip-video";
import { fmtScore, scoreClass } from "@/lib/format";
import type { Clip } from "@/lib/types";

export function ClipPlayerDialog({
  clip,
  open,
  onOpenChange,
  fav,
  onToggleFav,
}: {
  clip: Clip;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  fav: boolean;
  onToggleFav: () => void;
}) {
  const url = clip.url || "";
  const title = clip.title || "Clipe";
  function copyLink() {
    if (!url) return;
    const abs = /^https?:/i.test(url) ? url : location.origin + url;
    navigator.clipboard.writeText(abs).then(
      () => toast.success("Link copiado — abrir exige login na sua conta."),
      () => toast.error("Não consegui copiar"),
    );
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[560px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-3">
            {title}
            {clip.score != null && (
              <span className={`score-badge ${scoreClass(clip.score)}`}>{fmtScore(clip.score)}</span>
            )}
          </DialogTitle>
          {clip.reason && (
            <DialogDescription className="text-ink-soft">{clip.reason}</DialogDescription>
          )}
        </DialogHeader>
        <div className="flex flex-col items-center gap-4">
          <ClipVideo
            src={url}
            autoPlay
            className="h-[68vh] max-h-[620px] w-auto rounded-[var(--radius-md)]"
          />
          <div className="flex flex-wrap justify-center gap-2.5">
            <Button asChild variant="gold" size="brand">
              <a href={url} download>
                <Download /> Baixar
              </a>
            </Button>
            <Button variant="brandGhost" size="brand" onClick={copyLink}>
              <Copy /> Copiar link
            </Button>
            <Button
              variant="brandGhost"
              size="brand"
              onClick={onToggleFav}
              className={fav ? "text-gold" : undefined}
            >
              <Heart /> {fav ? "Favorito" : "Favoritar"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
