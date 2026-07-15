// Deteccao de silencio a partir do tempo por palavra (Word.start/end), pra marcar visualmente
// os gaps na timeline do editor (§ dor 1: achar; a remocao fica pra uma proxima rodada).

import type { Word } from "./types";

// Abaixo disso e so a pausa natural entre palavras faladas; acima, vira "silencio" digno de nota
// na timeline. 0.5s e um piso conservador (evita marcar toda respiracao entre palavras).
export const SILENCE_THRESHOLD_S = 0.5;

export interface SilenceGap {
  start: number;
  end: number;
}

// Calcula os gaps entre o fim de uma palavra e o inicio da proxima que sejam >= SILENCE_THRESHOLD_S.
// Degrada pra lista vazia se nao houver palavras (clipe legado/sem fala).
export function computeSilenceGaps(words: Word[] | null | undefined): SilenceGap[] {
  if (!words || words.length < 2) return [];
  const gaps: SilenceGap[] = [];
  for (let i = 1; i < words.length; i++) {
    const prevEnd = words[i - 1].end;
    const start = words[i].start;
    const gap = start - prevEnd;
    if (gap >= SILENCE_THRESHOLD_S) {
      gaps.push({ start: prevEnd, end: start });
    }
  }
  return gaps;
}
