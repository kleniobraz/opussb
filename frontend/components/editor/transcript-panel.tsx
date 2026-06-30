"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Scissors, Merge, Trash2, Search, Plus } from "lucide-react";
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

// Colapsa espacos e da trim (commit do texto editado inline).
function collapse(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

// Normaliza pra busca: minusculas + sem acento (busca "voce" acha "você").
function norm(s: string): string {
  return s.toLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "");
}

// Estado teleprompter de cada segmento (cor): tocando agora / ja passou / ainda vai tocar.
type SegState = "active" | "past" | "future";

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

// Um segmento do roteiro corrido: <span> contentEditable NAO-CONTROLADO.
// Armadilha do contentEditable (amarra #4): durante o play o painel re-renderiza a cada frame
// (currentTime muda). Se o React reescrevesse o texto do no em edicao, o cursor se perderia. Por
// isso os filhos ficam num valor INICIAL congelado (React nunca toca o no de texto) e mudancas
// EXTERNAS (juntar/dividir/outra linha) so entram no DOM quando o no NAO esta em foco. Durante o
// play so muda className/data-active (cor), nunca o texto. Commit do texto no onBlur.
function RoteiroSeg({
  seg,
  state,
  selected,
  onSelect,
  onCommit,
  onEditStart,
}: {
  seg: EditSeg;
  state: SegState;
  selected: boolean;
  onSelect: (id: string) => void;
  onCommit: (id: string, text: string) => void;
  onEditStart?: () => void;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const initial = useRef(seg.text).current; // valor congelado: vai como filho UMA vez

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // So sincroniza o DOM quando NAO esta sendo editado (senao mexeria no cursor do usuario).
    if (document.activeElement !== el && el.textContent !== seg.text) {
      el.textContent = seg.text;
    }
  }, [seg.text]);

  return (
    <span
      ref={ref}
      data-active={state === "active" ? "true" : undefined}
      className={cn("roteiro-seg", `is-${state}`, selected && "is-selected")}
      contentEditable
      suppressContentEditableWarning
      spellCheck={false}
      role="textbox"
      tabIndex={0}
      onClick={() => onSelect(seg._id)}
      onFocus={() => {
        onEditStart?.(); // foco pausa o play (e o auto-scroll respeita o foco)
        onSelect(seg._id);
      }}
      onBlur={(e) => {
        const v = collapse(e.currentTarget.textContent || "");
        if (v !== seg.text) onCommit(seg._id, v);
        if (e.currentTarget.textContent !== v) e.currentTarget.textContent = v; // normaliza o DOM
      }}
      onKeyDown={(e) => {
        // Enter confirma (blur). Quebra de linha real/estrutura fica nas ferramentas do segmento.
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          e.currentTarget.blur();
        } else if (e.key === "Escape") {
          e.preventDefault();
          e.currentTarget.blur();
        }
      }}
    >
      {initial}
    </span>
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
  onSetTime,
  onSplit,
  onMerge,
  onEditStart,
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
  onSetTime: (id: string, edge: "start" | "end", seconds: number) => void;
  onSplit: (id: string, caret?: number) => void;
  onMerge: (id: string, dir: "prev" | "next") => void;
  onEditStart?: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");

  // Busca no roteiro: filtra os segmentos por texto (case/acento-insensitive). Vazio = roteiro inteiro.
  const shown = useMemo(() => {
    const q = norm(query.trim());
    if (!q) return segments;
    return segments.filter((s) => norm(s.text).includes(q));
  }, [segments, query]);

  // Auto-scroll do roteiro (amarra #5): ajusta scrollTop (NAO scrollIntoView) pra trazer o
  // segmento ativo pra vista durante o play. NAO mexe quando o foco esta num campo editavel
  // (senao "puxaria" a tela no meio da correcao). Acha o ativo por [data-active].
  useEffect(() => {
    if (!playing || activeId == null) return;
    const ae = document.activeElement as HTMLElement | null;
    if (ae && (ae.isContentEditable || ae.tagName === "INPUT" || ae.tagName === "TEXTAREA")) return;
    const box = scrollRef.current;
    if (!box) return;
    const el = box.querySelector<HTMLElement>('[data-active="true"]');
    if (!el) return;
    const top = el.offsetTop;
    const h = el.offsetHeight;
    const st = box.scrollTop;
    const vh = box.clientHeight;
    if (top < st + 8) box.scrollTop = Math.max(0, top - 16);
    else if (top + h > st + vh - 8) box.scrollTop = top + h - vh + 16;
  }, [activeId, playing]);

  const sel = selectedId ? segments.find((s) => s._id === selectedId) ?? null : null;

  return (
    <div className="editor-roteiro-panel">
      <div className="editor-roteiro-head">
        <strong className="editor-roteiro-title">Roteiro</strong>
        <span className="editor-roteiro-count">
          {query.trim() ? `${shown.length} de ${segments.length}` : `${segments.length} linhas`}
        </span>
        <div className="flex-1" />
        <div className="editor-roteiro-search">
          <Search className="size-3.5 shrink-0" aria-hidden />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar no roteiro…"
            aria-label="Buscar no roteiro"
          />
        </div>
        <button
          type="button"
          className="editor-roteiro-add"
          onClick={onAdd}
          title="Adicionar trecho"
          aria-label="Adicionar trecho"
        >
          <Plus className="size-4" />
        </button>
      </div>

      <div className="editor-roteiro" ref={scrollRef}>
        {segments.length === 0 ? (
          <p className="editor-roteiro-empty">Sem linhas de legenda. Use “+” para adicionar.</p>
        ) : shown.length === 0 ? (
          <p className="editor-roteiro-empty">Nada encontrado para “{query.trim()}”.</p>
        ) : (
          shown.map((s) => {
            const st: SegState =
              s._id === activeId ? "active" : currentTime >= s.end ? "past" : "future";
            return (
              <RoteiroSeg
                key={s._id}
                seg={s}
                state={st}
                selected={s._id === selectedId}
                onSelect={onSelect}
                onCommit={onChangeText}
                onEditStart={onEditStart}
              />
            );
          })
        )}
      </div>

      {sel && (
        <div className="editor-seg-ctx" onClick={(e) => e.stopPropagation()}>
          <div className="editor-seg-times">
            <TimeField
              value={sel.start}
              ariaLabel="Início do trecho"
              onCommit={(sec) => onSetTime(sel._id, "start", sec)}
            />
            <span className="editor-seg-arrow">→</span>
            <TimeField
              value={sel.end}
              ariaLabel="Fim do trecho"
              onCommit={(sec) => onSetTime(sel._id, "end", sec)}
            />
            {sel.end - sel.start < 0.7 && (
              <span className="editor-seg-warn" title="Trecho muito curto (pisca rápido)">
                curta
              </span>
            )}
            <div className="flex-1" />
            <button
              type="button"
              className="editor-seg-mark"
              onClick={() => onSplit(sel._id)}
              title="Dividir este trecho em dois (no ponto do player)"
            >
              <Scissors className="size-3" /> Dividir
            </button>
            <button
              type="button"
              className="editor-seg-mark"
              onClick={() => onMerge(sel._id, "next")}
              title="Juntar com o próximo trecho"
            >
              <Merge className="size-3" /> Juntar
            </button>
            <button
              className="editor-seg-del"
              type="button"
              onClick={() => onDelete(sel._id)}
              aria-label="Remover trecho"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
