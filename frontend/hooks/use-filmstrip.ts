"use client";

import { useEffect, useState } from "react";

// URL do sprite (filmstrip) do clipe pra a CAMADA DE VIDEO do editor. O sprite (N frames lado a
// lado) e gerado no SERVIDOR (rota /api/clips/<id>/filmstrip, MESMA ORIGEM), porque capturar frames
// no navegador e bloqueado pelo CORS do S3. O servidor gera 1x (ffmpeg) e cacheia; aqui so buscamos
// a URL presignada do sprite. Best-effort: erro/vazio -> null (a faixa fica neutra, sem quebrar).
export function useFilmstrip(clipId: string | null): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!clipId) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    fetch(`/api/clips/${encodeURIComponent(clipId)}/filmstrip`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("filmstrip"))))
      .then((d) => {
        if (!cancelled) setUrl(typeof d?.url === "string" && d.url ? d.url : null);
      })
      .catch(() => {
        if (!cancelled) setUrl(null);
      });
    return () => {
      cancelled = true;
    };
  }, [clipId]);

  return url;
}
