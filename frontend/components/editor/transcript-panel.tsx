"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Scissors, Merge, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { EditSeg } from "./clip-editor";

// Tempo no formato M:SS.d (decimos) — preciso o bastante pra nudge de 0.1s dar feedback.
function fmtT(t: number): string {
  if (!isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  const d = Math.floor((t * 10) % 10);
  return `${m}:${String(s).padStart(2, "0")}.${d}`;
}

// Aceita "M:SS.d", "SS.d" ou segundos puros; virgula vira ponto (pt-BR). Retorna NaN se invalido.
function parseT(str: string): number {
  const t = str.trim().replace(",", ".");
  if (!t) return NaN;
  if (t.includes(":")) {
    const parts = t.split(":");
    if (parts.length !== 2) return NaN;
    const m = Number(parts[0]);
    const s = Number(parts[1]);
    if (!isFinite(m) || !isFinite(s) || s < 0) return NaN;
    return m * 60 + s;
  }
  const v = Number(t);
  return isFinite(v) ? v : NaN;
}

// Campo de tempo editavel: mostra fmtT(value) e, ao focar, vira um input livre. Confirma no
// blur/Enter (parse + commit); ESC ou valor invalido reverte. Seta cima/baixo ajusta +-0.1s
// (Shift = 1s). O draft local evita que o valor seja reformatado no meio da digitacao.
function TimeField({
  value,
  onCommit,
  ariaLabel,
}: {
  value: number;
  onCommit: (sec: number) => void;
  ariaLabel: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const display = draft ?? fmtT(value);

  function commit() {
    if (draft == null) return;
    const v = parseT(draft);
    if (!isNaN(v)) onCommit(v);
    setDraft(null);
  }

  return (
    <input
      className="editor-seg-tf"
      type="text"
      inputMode="decimal"
      value={display}
      aria-label={ariaLabel}
      onClick={(e) => e.stopPropagation()}
      onFocus={(e) => {
        setDraft(fmtT(value));
        e.currentTarget.select();
      }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
          e.currentTarget.blur();
        } else if (e.key === "Escape") {
          e.preventDefault();
          setDraft(null);
          e.currentTarget.blur();
        } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          const step = (e.shiftKey ? 1 : 0.1) * (e.key === "ArrowUp" ? 1 : -1);
          onCommit(value + step);
          setDraft(null);
        }
      }}
    />
  );
}

export function TranscriptPanel({
  segments,
  selectedId,
  activeId,
  currentTime,
  playing,
  onSelect,
  onChangeText,
  onDelete,
  onAdd,
  onSetEdge,
  onNudge,
  onSetTime,
  onSplit,
  onMerge,
}: {
  segments: EditSeg[];
  selectedId: string | null;
  activeId: string | null;
  currentTime: number;
  playing: boolean;
  onSelect: (id: string) => void;
  onChangeText: (id: string, text: string) => void;
  onDelete: (id: string) => void;
  onAdd: () => void;
  onSetEdge: (id: string, edge: "start" | "end") => void;
  onNudge: (id: string, edge: "start" | "end", delta: number) => void;
  onSetTime: (id: string, edge: "start" | "end", seconds: number) => void;
  onSplit: (id: string, caret?: number) => void;
  onMerge: (id: string, dir: "prev" | "next") => void;
}) {
  const activeRowRef = useRef<HTMLDivElement>(null);

  // Karaoke: enquanto toca, traz a linha falada pra vista. Mas NAO arrasta o painel
  // quando o usuario esta editando (cursor num campo de texto/tempo): o auto-follow nao
  // pode "puxar" a tela no meio da correcao. Era o "painel fica acompanhando o video"
  // que atrapalhava corrigir legenda. Ao parar de editar (campo perde o foco) o follow volta.
  useEffect(() => {
    if (!playing || activeId == null) return;
    const ae = document.activeElement as HTMLElement | null;
    if (ae && (ae.tagName === "TEXTAREA" || ae.tagName === "INPUT" || ae.isContentEditable)) return;
    activeRowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeId, playing]);

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
      {segments.map((s) => {
        const short = s.end - s.start < 0.7;
        return (
          <div
            key={s._id}
            ref={s._id === activeId ? activeRowRef : undefined}
            className={cn(
              "editor-seg-row",
              selectedId === s._id && "is-selected",
              activeId === s._id && "is-active",
            )}
            onClick={() => onSelect(s._id)}
          >
            <div className="editor-seg-rowhead">
              <div className="editor-seg-times">
                <TimeField
                  value={s.start}
                  ariaLabel="Início da linha"
                  onCommit={(sec) => onSetTime(s._id, "start", sec)}
                />
                <span className="editor-seg-arrow">→</span>
                <TimeField
                  value={s.end}
                  ariaLabel="Fim da linha"
                  onCommit={(sec) => onSetTime(s._id, "end", sec)}
                />
                {short && (
                  <span className="editor-seg-warn" title="Legenda muito curta (pisca rápido)">
                    curta
                  </span>
                )}
              </div>
              <button
                className="editor-seg-del"
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(s._id);
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
              onChange={(e) => onChangeText(s._id, e.target.value)}
              onFocus={() => onSelect(s._id)}
              onKeyDown={(e) => {
                // Enter divide a linha no ponto do cursor; Backspace no inicio junta com a anterior.
                const ta = e.currentTarget;
                if (e.key === "Enter" && !e.shiftKey) {
                  const caret = ta.selectionStart ?? 0;
                  if (caret > 0 && caret < (s.text?.length ?? 0)) {
                    e.preventDefault();
                    onSplit(s._id, caret);
                  } else {
                    e.preventDefault();
                  }
                } else if (
                  e.key === "Backspace" &&
                  ta.selectionStart === 0 &&
                  ta.selectionEnd === 0
                ) {
                  e.preventDefault();
                  onMerge(s._id, "prev");
                }
              }}
              placeholder="Texto da legenda"
            />

            {selectedId === s._id && (
              <div className="editor-seg-tools" onClick={(e) => e.stopPropagation()}>
                <span className="editor-seg-tools-lbl">No player ({fmtT(currentTime)}):</span>
                <button type="button" className="editor-seg-mark" onClick={() => onSetEdge(s._id, "start")}>
                  Início
                </button>
                <button type="button" className="editor-seg-mark" onClick={() => onSetEdge(s._id, "end")}>
                  Fim
                </button>
                <button
                  type="button"
                  className="editor-seg-mark"
                  onClick={() => onSplit(s._id)}
                  title="Dividir esta linha em duas (no ponto do player)"
                >
                  <Scissors className="size-3" /> Dividir
                </button>
                <button
                  type="button"
                  className="editor-seg-mark"
                  onClick={() => onMerge(s._id, "next")}
                  title="Juntar com a próxima linha"
                >
                  <Merge className="size-3" /> Juntar
                </button>
                <span className="editor-seg-tools-sep" />
                <span className="editor-seg-nudge">
                  início
                  <button type="button" onClick={() => onNudge(s._id, "start", -0.1)} aria-label="Início −0,1s">
                    <ChevronLeft className="size-3" />
                  </button>
                  <button type="button" onClick={() => onNudge(s._id, "start", 0.1)} aria-label="Início +0,1s">
                    <ChevronRight className="size-3" />
                  </button>
                </span>
                <span className="editor-seg-nudge">
                  fim
                  <button type="button" onClick={() => onNudge(s._id, "end", -0.1)} aria-label="Fim −0,1s">
                    <ChevronLeft className="size-3" />
                  </button>
                  <button type="button" onClick={() => onNudge(s._id, "end", 0.1)} aria-label="Fim +0,1s">
                    <ChevronRight className="size-3" />
                  </button>
                </span>
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
