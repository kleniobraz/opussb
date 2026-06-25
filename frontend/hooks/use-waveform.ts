"use client";

import { useEffect, useState } from "react";

// Picos da forma de onda do clipe pra a faixa de audio do editor. O calculo roda no SERVIDOR
// (rota /api/clips/<id>/waveform, MESMA ORIGEM), porque decodificar o audio no navegador e
// bloqueado pelo CORS do S3. O servidor gera 1x (ffmpeg) e cacheia; aqui so buscamos o JSON.
// Best-effort: erro/vazio -> null (a faixa fica neutra, sem quebrar o editor).
export function useWaveform(clipId: string | null): number[] | null {
  const [peaks, setPeaks] = useState<number[] | null>(null);

  useEffect(() => {
    if (!clipId) {
      setPeaks(null);
      return;
    }
    let cancelled = false;
    fetch(`/api/clips/${encodeURIComponent(clipId)}/waveform`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("waveform"))))
      .then((d) => {
        if (!cancelled) setPeaks(Array.isArray(d?.peaks) && d.peaks.length ? d.peaks : null);
      })
      .catch(() => {
        if (!cancelled) setPeaks(null);
      });
    return () => {
      cancelled = true;
    };
  }, [clipId]);

  return peaks;
}
