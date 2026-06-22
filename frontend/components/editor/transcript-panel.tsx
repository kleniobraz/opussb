"use client";

import { useEffect, useRef } from "react";
import { ChevronLeft, ChevronRight, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Segment } from "@/lib/types";

// Tempo no formato M:SS.d (decimos) — preciso o bastante pra nudge de 0.1s dar feedback.
function fmtT(t: number): string {
  if (!isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  const d = Math.floor((t * 10) % 10);
  return `${m}:${String(s).padStart(2, "0")}.${d}`;
}

export function TranscriptPanel({
  segments,
  selected,
  activeIndex,
  currentTime,
  playing,
  onSelect,
  onChangeText,
  onDelete,
  onAdd,
  onSetEdge,
  onNudge,
}: {
  segments: Segment[];
  selected: number | null;
  activeIndex: number | null;
  currentTime: number;
  playing: boolean;
  onSelect: (i: number) => void;
  onChangeText: (i: number, text: string) => void;
  onDelete: (i: number) => void;
  onAdd: () => void;
  onSetEdge: (i: number, edge: "start" | "end") => void;
  onNudge: (i: number, edge: "start" | "end", delta: number) => void;
}) {
  const activeRowRef = useRef<HTMLDivElement>(null);

  // Karaoke: enquanto toca, traz a linha falada pra vista (sem brigar com scroll manual
  // quando pausado).
  useEffect(() => {
    if (playing && activeIndex != null) {
      activeRowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [activeIndex, playing]);

  return (
    <>
      <div className="editor-transcript-head">
        <strong className="text-sm text-ink-soft">Transcrição / legenda</strong>
        <Button variant="brandGhost" size="sm" onClick={onAdd}>
          + Linha
        </Button>
      </div>
      {segments.length === 0 && (
        <p className="editor-hint">Sem linhas de legenda. Use “+ Linha” para adicionar.</p>
      )}
      {segments.map((s, i) => (
        <div
          key={i}
          ref={i === activeIndex ? activeRowRef : undefined}
          className={cn(
            "editor-seg-row",
            selected === i && "is-selected",
            activeIndex === i && "is-active",
          )}
          onClick={() => onSelect(i)}
        >
          <div className="editor-seg-rowhead">
            <span className="editor-seg-time">
              {fmtT(s.start)} → {fmtT(s.end)}
            </span>
            <button
              className="editor-seg-del"
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(i);
              }}
              aria-label="Remover linha"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
          <textarea
            className="editor-seg-text"
            rows={2}
            value={s.text}
            onChange={(e) => onChangeText(i, e.target.value)}
            onFocus={() => onSelect(i)}
            placeholder="Texto da legenda"
          />

          {selected === i && (
            <div className="editor-seg-tools" onClick={(e) => e.stopPropagation()}>
              <span className="editor-seg-tools-lbl">No player ({fmtT(currentTime)}):</span>
              <button type="button" className="editor-seg-mark" onClick={() => onSetEdge(i, "start")}>
                Início
              </button>
              <button type="button" className="editor-seg-mark" onClick={() => onSetEdge(i, "end")}>
                Fim
              </button>
              <span className="editor-seg-tools-sep" />
              <span className="editor-seg-nudge">
                início
                <button type="button" onClick={() => onNudge(i, "start", -0.1)} aria-label="Início −0,1s">
                  <ChevronLeft className="size-3" />
                </button>
                <button type="button" onClick={() => onNudge(i, "start", 0.1)} aria-label="Início +0,1s">
                  <ChevronRight className="size-3" />
                </button>
              </span>
              <span className="editor-seg-nudge">
                fim
                <button type="button" onClick={() => onNudge(i, "end", -0.1)} aria-label="Fim −0,1s">
                  <ChevronLeft className="size-3" />
                </button>
                <button type="button" onClick={() => onNudge(i, "end", 0.1)} aria-label="Fim +0,1s">
                  <ChevronRight className="size-3" />
                </button>
              </span>
            </div>
          )}
        </div>
      ))}
    </>
  );
}
