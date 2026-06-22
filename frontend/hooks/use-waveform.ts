"use client";

import { useEffect, useState } from "react";

// Peaks do audio do clipe pra desenhar a waveform da timeline. Best-effort: faz fetch do
// arquivo (302 -> S3) e decodifica via WebAudio; se o bucket nao mandar CORS o fetch falha
// -> retorna null (a timeline mostra trilha neutra, sem waveform inventada).
export function useWaveform(src: string | null, buckets = 400): number[] | null {
  const [peaks, setPeaks] = useState<number[] | null>(null);

  useEffect(() => {
    if (!src) return;
    const url = src;
    let cancelled = false;

    async function run() {
      const res = await fetch(url);
      if (!res.ok) throw new Error("fetch");
      const buf = await res.arrayBuffer();
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const audio = await ctx.decodeAudioData(buf);
      ctx.close();
      const data = audio.getChannelData(0);
      const block = Math.floor(data.length / buckets) || 1;
      const out: number[] = [];
      let max = 0.0001;
      for (let i = 0; i < buckets; i++) {
        let peak = 0;
        const start = i * block;
        for (let j = 0; j < block; j++) {
          const v = Math.abs(data[start + j] || 0);
          if (v > peak) peak = v;
        }
        out.push(peak);
        if (peak > max) max = peak;
      }
      return out.map((p) => p / max);
    }

    run().then(
      (p) => {
        if (!cancelled) setPeaks(p);
      },
      () => {
        if (!cancelled) setPeaks(null);
      },
    );

    return () => {
      cancelled = true;
    };
  }, [src, buckets]);

  return peaks;
}
