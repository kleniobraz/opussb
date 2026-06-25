"use client";

import { useEffect, useState } from "react";
import { Input } from "./ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import { ClipCard } from "./clip-card";
import { cn } from "@/lib/utils";
import type { Clip } from "@/lib/types";

type Band = "all" | "high" | "mid" | "low";
type Sort = "score_desc" | "score_asc" | "dur_desc" | "order";

const BANDS: { key: Band; label: string }[] = [
  { key: "all", label: "Todas" },
  { key: "high", label: "Nota ≥ 8" },
  { key: "mid", label: "6 a 8" },
  { key: "low", label: "< 6" },
];

// Quantos clipes mostrar por vez; o "Ver mais" revela mais. Evita renderizar e baixar o poster de
// TODOS de uma vez em projetos grandes (economia de recursos).
const PAGE_SIZE = 9;

export function ClipGrid({
  clips,
  toolbar = true,
  notice,
}: {
  clips: Clip[];
  toolbar?: boolean;
  notice?: string | null;
}) {
  const [q, setQ] = useState("");
  const [band, setBand] = useState<Band>("all");
  const [sort, setSort] = useState<Sort>("score_desc");
  const [shown, setShown] = useState(PAGE_SIZE);
  const showToolbar = toolbar && clips.length > 1;

  // Busca ou filtro mudou: volta a mostrar a primeira página.
  useEffect(() => setShown(PAGE_SIZE), [q, band]);

  let list = clips.map((c, i) => ({ c, i }));
  if (q) {
    const s = q.toLowerCase();
    list = list.filter(({ c }) =>
      ((c.title || "") + " " + (c.reason || "")).toLowerCase().includes(s),
    );
  }
  if (band !== "all") {
    list = list.filter(({ c }) => {
      if (c.score == null) return false;
      if (band === "high") return c.score >= 8;
      if (band === "mid") return c.score >= 6 && c.score < 8;
      return c.score < 6;
    });
  }
  list = [...list].sort((a, b) => {
    if (sort === "score_desc") return (b.c.score || 0) - (a.c.score || 0);
    if (sort === "score_asc") return (a.c.score || 0) - (b.c.score || 0);
    if (sort === "dur_desc") return (b.c.duration || 0) - (a.c.duration || 0);
    return a.i - b.i;
  });

  return (
    <div>
      {notice && <p className="partial-notice mb-4">{notice}</p>}
      {showToolbar && (
        <div className="clip-toolbar">
          <Input
            type="search"
            className="clip-search"
            placeholder="Buscar por título ou motivo"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Buscar clipes"
          />
          <div className="clip-bands">
            {BANDS.map((b) => (
              <button
                key={b.key}
                type="button"
                className={cn("chip", band === b.key && "is-active")}
                onClick={() => setBand(b.key)}
              >
                {b.label}
              </button>
            ))}
          </div>
          <Select value={sort} onValueChange={(v) => setSort(v as Sort)}>
            <SelectTrigger className="w-auto min-w-[150px]" aria-label="Ordenar clipes">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="score_desc">Maior nota</SelectItem>
              <SelectItem value="score_asc">Menor nota</SelectItem>
              <SelectItem value="dur_desc">Mais longos</SelectItem>
              <SelectItem value="order">Ordem original</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}

      {list.length === 0 ? (
        <div className="empty">
          <p className="empty-title">Nenhum clipe com esse filtro</p>
          <p className="empty-text">Ajuste a busca ou o filtro de nota.</p>
        </div>
      ) : (
        <>
          <div className="clips-grid">
            {list.slice(0, shown).map(({ c, i }) => (
              <ClipCard key={c.id ?? i} clip={c} index={i} />
            ))}
          </div>
          {list.length > shown && (
            <div className="clip-more">
              <button
                type="button"
                className="clip-more-btn"
                onClick={() => setShown((s) => s + PAGE_SIZE)}
              >
                Ver mais ({list.length - shown})
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
