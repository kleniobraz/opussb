"use client";

import { useEffect, useState } from "react";

export interface Frame {
  t: number;
  url: string;
}

// Extrai frames do clipe pro filmstrip da timeline. Best-effort: o video vem do S3 via
// 302; se o bucket nao mandar CORS, o canvas fica "tainted" e a captura falha -> retorna
// vazio (a timeline mostra uma trilha neutra, sem inventar frames).
export function useFilmstrip(src: string | null, duration: number, count = 12): Frame[] {
  const [frames, setFrames] = useState<Frame[]>([]);

  useEffect(() => {
    if (!src || !duration || !isFinite(duration) || duration <= 0) return;
    let cancelled = false;
    const video = document.createElement("video");
    video.src = src;
    video.muted = true;
    video.crossOrigin = "anonymous";
    video.preload = "auto";
    const canvas = document.createElement("canvas");
    const out: Frame[] = [];

    function seek(t: number) {
      return new Promise<void>((res) => {
        const on = () => {
          video.removeEventListener("seeked", on);
          res();
        };
        video.addEventListener("seeked", on);
        video.currentTime = Math.min(t, video.duration || t);
      });
    }

    async function run() {
      await new Promise<void>((res, rej) => {
        video.onloadedmetadata = () => res();
        video.onerror = () => rej(new Error("load"));
      });
      if (cancelled) return;
      const w = 64;
      const ratio = video.videoHeight && video.videoWidth ? video.videoHeight / video.videoWidth : 16 / 9;
      const h = Math.round(w * ratio);
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      for (let i = 0; i < count; i++) {
        if (cancelled) return;
        const t = ((i + 0.5) * duration) / count;
        await seek(t);
        if (cancelled) return;
        ctx.drawImage(video, 0, 0, w, h);
        out.push({ t, url: canvas.toDataURL("image/jpeg", 0.5) }); // lanca se tainted (sem CORS)
        setFrames([...out]);
      }
    }

    run().catch(() => {
      if (!cancelled) setFrames([]);
    });

    return () => {
      cancelled = true;
      video.src = "";
    };
  }, [src, duration, count]);

  return frames;
}
