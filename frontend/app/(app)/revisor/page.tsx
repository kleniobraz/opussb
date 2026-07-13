"use client";

import { notFound } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import * as api from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import type { CaptionReview, CaptionReviewFinding, GlossaryTerm } from "@/lib/types";

// Feature desativada (Revisor IA): a pagina fica de codigo morto reversivel,
// mas qualquer acesso direto a /revisor cai no not-found do Next.
// Reverter: remover o notFound() abaixo.

const STATUS_LABEL: Record<string, string> = {
  queued: "Na fila",
  extracting: "Extraindo frames",
  reading: "Lendo o texto (visão)",
  reviewing: "Revisando",
  done: "Concluída",
  error: "Erro",
};

const TIPO_LABEL: Record<string, string> = {
  ortografia: "Ortografia",
  gramatica: "Gramática",
  marca: "Marca",
  pontuacao: "Pontuação",
  maiuscula: "Maiúscula",
  outro: "Outro",
};

const TIPO_COLOR: Record<string, { bg: string; fg: string }> = {
  ortografia: { bg: "rgba(239,68,68,0.14)", fg: "#dc2626" },
  gramatica: { bg: "rgba(249,115,22,0.16)", fg: "#c2410c" },
  marca: { bg: "rgba(245,197,24,0.18)", fg: "#b8860b" },
  pontuacao: { bg: "rgba(59,130,246,0.14)", fg: "#2563eb" },
  maiuscula: { bg: "rgba(168,85,247,0.16)", fg: "#7c3aed" },
  outro: { bg: "rgba(148,163,184,0.16)", fg: "#64748b" },
};

// ─── Aba Revisar ───────────────────────────────────────────────────────────

// Limites do backend (app.py caption_review_create + config.py): a validação de verdade
// é do servidor; aqui só recusamos cedo o óbvio pra não subir 500MB à toa.
const ACCEPT_EXT = ["mp4", "mov", "avi", "mkv", "webm"];
const MAX_REVIEW_BYTES = 500 * 1024 * 1024; // CAPTION_REVIEW_MAX_BYTES

function extOk(name: string) {
  const m = /\.([a-z0-9]+)$/i.exec(name || "");
  return !!m && ACCEPT_EXT.includes(m[1].toLowerCase());
}

// Mesmo glifo do dropzone do upload principal (upload-console.tsx).
const DropIcon = () => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M20 16.5A4.5 4.5 0 0 0 17.5 8h-1.3A7 7 0 1 0 4 14.9" />
    <polyline points="9 13 12 10 15 13" />
    <line x1="12" y1="10" x2="12" y2="20" />
  </svg>
);

function FindingRow({ f }: { f: CaptionReviewFinding }) {
  const c = TIPO_COLOR[f.tipo] ?? TIPO_COLOR.outro;
  return (
    <div className="admin-row">
      <div className="admin-row-info">
        <span className="admin-row-email" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ background: c.bg, color: c.fg, padding: "2px 10px", borderRadius: 999, fontSize: "0.72rem", fontWeight: 600 }}>
            {TIPO_LABEL[f.tipo] ?? f.tipo}
          </span>
          <span style={{ textDecoration: "line-through", opacity: 0.85 }}>{f.trecho}</span>
          <span style={{ color: "var(--gold)" }}>→ {f.sugestao}</span>
        </span>
        <span className="admin-row-meta">
          {f.origem === "grafico" ? "gráfico" : "legenda"} · {f.explicacao} · confiança{" "}
          {Math.round((f.confianca ?? 0) * 100)}%
        </span>
      </div>
    </div>
  );
}

function ReviewReport({ rd }: { rd: CaptionReview }) {
  const findings = rd.findings ?? [];
  const tk = rd.tokens;
  const totalTok = tk
    ? (tk.read_in || 0) + (tk.read_out || 0) + (tk.review_in || 0) + (tk.review_out || 0)
    : null;
  const cost = rd.cost_usd != null ? "US$ " + rd.cost_usd.toFixed(4) : "—";
  return (
    <div>
      <div className="admin-row-meta mb-4">
        {rd.frames_count ?? "—"} frames · custo {cost}
        {totalTok != null ? ` · ${totalTok.toLocaleString("pt-BR")} tokens` : ""}
      </div>

      <h3 className="results-title">Achados ({findings.length})</h3>
      {findings.length === 0 ? (
        <p className="editor-hint">Nenhum erro encontrado. 🎉</p>
      ) : (
        <div className="admin-list">
          {findings.map((f, i) => (
            <FindingRow key={i} f={f} />
          ))}
        </div>
      )}

      <details className="mt-6">
        <summary className="eyebrow cursor-pointer">Texto lido — legenda da fala</summary>
        <p className="mt-2 whitespace-pre-wrap text-sm">{rd.legenda_fala || "—"}</p>
      </details>
      {rd.graficos && rd.graficos.length > 0 && (
        <details className="mt-3">
          <summary className="eyebrow cursor-pointer">Textos gráficos lidos ({rd.graficos.length})</summary>
          <ul className="mt-2 text-sm">
            {rd.graficos.map((g, i) => (
              <li key={i}>• {g.texto}{g.tipo ? ` — ${g.tipo}` : ""}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ReviewPanel() {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [fileMeta, setFileMeta] = useState<{ text: string; error: boolean }>({ text: "", error: false });
  const [drag, setDrag] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Espelho do pickFile do upload principal, com o teto de 500MB da revisão.
  function pickFile(f: File | null) {
    if (fileRef.current) fileRef.current.value = "";
    if (!f) {
      setFile(null);
      setFileMeta({ text: "", error: false });
      return;
    }
    if (!extOk(f.name)) {
      setFile(null);
      setFileMeta({ text: "Formato não suportado. Use mp4, mov, avi, mkv ou webm.", error: true });
      return;
    }
    if (f.size > MAX_REVIEW_BYTES) {
      setFile(null);
      setFileMeta({ text: `Arquivo acima de 500 MB (${fmtBytes(f.size)}).`, error: true });
      return;
    }
    setFile(f);
    setFileMeta({ text: fmtBytes(f.size), error: false });
  }

  const history = useQuery({ queryKey: ["caption-reviews"], queryFn: api.captionReviewList });

  const review = useQuery({
    queryKey: ["caption-review", reviewId],
    queryFn: () => api.captionReviewStatus(reviewId as string),
    enabled: !!reviewId,
    // Poll até terminar; o backend não empurra, então checamos a cada 2s.
    refetchInterval: (q) => {
      const s = (q.state.data as CaptionReview | undefined)?.status;
      return s === "done" || s === "error" ? false : 2000;
    },
  });

  const rd = review.data;
  const active = !!reviewId && rd?.status !== "done" && rd?.status !== "error";

  // Ao terminar, atualiza o histórico.
  useEffect(() => {
    if (rd?.status === "done" || rd?.status === "error") {
      qc.invalidateQueries({ queryKey: ["caption-reviews"] });
    }
  }, [rd?.status, qc]);

  const create = useMutation({
    mutationFn: (f: File) => api.captionReviewCreate(f),
    onSuccess: (r) => {
      setReviewId(r.review_id);
      pickFile(null);
      qc.invalidateQueries({ queryKey: ["caption-reviews"] });
      toast.success("Vídeo enviado, revisando…");
    },
    onError: (e) => toast.error((e as Error)?.message || "Falha ao enviar o vídeo"),
  });

  const reviews = history.data?.reviews ?? [];

  return (
    <div>
      <label
        className={cn("dropzone", drag && "is-dragover")}
        htmlFor="review-video-file"
        onDragEnter={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          pickFile(e.dataTransfer.files?.[0] ?? null);
        }}
      >
        <span className="dropzone-icon" aria-hidden="true">
          <DropIcon />
        </span>
        <span className="dropzone-title">
          {file ? file.name : "Arraste o Reels aqui ou clique para enviar"}
        </span>
        <span className="dropzone-hint">MP4, MOV, AVI, MKV ou WEBM · até 500 MB · até 5 min</span>
        {fileMeta.text && (
          <span className={cn("dropzone-meta", fileMeta.error && "meta-error")}>{fileMeta.text}</span>
        )}
        {file && (
          <span className="dropzone-hint">
            Clique para trocar ou{" "}
            <button
              type="button"
              className="cursor-pointer underline hover:text-[var(--ink)]"
              onClick={(e) => {
                e.preventDefault();
                pickFile(null);
              }}
            >
              remova o arquivo
            </button>
          </span>
        )}
        <input
          ref={fileRef}
          type="file"
          id="review-video-file"
          accept=".mp4,.mov,.avi,.mkv,.webm"
          onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
        />
      </label>

      <Button
        variant="gold"
        size="brand"
        className="mt-4 w-full sm:w-auto"
        disabled={!file || create.isPending || active}
        onClick={() => file && create.mutate(file)}
      >
        {create.isPending ? "Enviando…" : "Revisar legenda"}
      </Button>

      <p className="editor-hint mt-3">
        A leitura do texto queimado é feita por IA (Sonnet 5) e revisada contra o glossário. O
        relatório é um guia: confira e corrija no Premiere.
      </p>

      {reviewId && rd && (
        <div className="mt-6">
          {active ? (
            <div>
              <div className="flex items-center justify-between text-sm">
                <span>{STATUS_LABEL[rd.status] ?? rd.status}…</span>
                <span className="text-[var(--ink-soft)]">{rd.progress ?? 0}%</span>
              </div>
              <Progress value={rd.progress ?? 0} className="mt-2" />
            </div>
          ) : rd.status === "error" ? (
            <p className="editor-hint">Erro: {rd.error || "falha na revisão."}</p>
          ) : (
            <ReviewReport rd={rd} />
          )}
        </div>
      )}

      {reviews.length > 0 && (
        <div className="mt-8">
          <h3 className="results-title">Histórico</h3>
          <div className="admin-list">
            {reviews.map((r) => (
              <div key={r.id} className="admin-row">
                <div className="admin-row-info">
                  <span className="admin-row-email">{r.filename || r.id}</span>
                  <span className="admin-row-meta">
                    {STATUS_LABEL[r.status] ?? r.status}
                    {r.created_at ? " · " + new Date(r.created_at).toLocaleString("pt-BR") : ""}
                    {r.cost_usd != null ? " · US$ " + r.cost_usd.toFixed(4) : ""}
                  </span>
                </div>
                <div className="admin-row-actions">
                  {r.status === "done" && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={reviewId === r.id}
                      onClick={() => setReviewId(r.id)}
                    >
                      Ver relatório
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Aba Glossário ───────────────────────────────────────────────────────────

function GlossaryPanel() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const isAdmin = !!user?.is_admin;
  const { data, isLoading, isError } = useQuery({ queryKey: ["glossary"], queryFn: api.glossaryList });
  const refresh = () => qc.invalidateQueries({ queryKey: ["glossary"] });

  const [editing, setEditing] = useState<string | null>(null);
  const [term, setTerm] = useState("");
  const [category, setCategory] = useState("");
  const [notes, setNotes] = useState("");
  const reset = () => {
    setEditing(null);
    setTerm("");
    setCategory("");
    setNotes("");
  };

  const save = useMutation({
    mutationFn: () => {
      const payload = {
        term: term.trim(),
        category: category.trim() || undefined,
        notes: notes.trim() || undefined,
      };
      return editing ? api.glossaryUpdate(editing, payload) : api.glossaryCreate(payload);
    },
    onSuccess: () => {
      refresh();
      reset();
      toast.success("Glossário atualizado");
    },
    onError: (e) => toast.error((e as Error)?.message || "Erro ao salvar"),
  });
  const del = useMutation({
    mutationFn: (id: string) => api.glossaryDelete(id),
    onSuccess: () => {
      refresh();
      toast.success("Termo excluído");
    },
    onError: (e) => toast.error((e as Error)?.message || "Erro ao excluir"),
  });

  const startEdit = (t: GlossaryTerm) => {
    setEditing(t.id);
    setTerm(t.term);
    setCategory(t.category || "");
    setNotes(t.notes || "");
  };

  if (isLoading) return <div className="skel" style={{ height: 200 }} />;
  if (isError) return <p className="editor-hint">Não consegui carregar o glossário.</p>;
  const terms = data?.terms ?? [];

  return (
    <div>
      <p className="editor-hint mb-4">
        Nomes canônicos de marcas, produtos e pessoas. A revisão usa esta lista pra flagrar grafias
        divergentes na tela. Qualquer usuário adiciona/edita; excluir é restrito a admin.
      </p>

      <div className="mb-6 flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex-1">
          <label className="eyebrow mb-1 block">Termo</label>
          <Input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Ex.: SeuBoné" />
        </div>
        <div className="w-full sm:w-40">
          <label className="eyebrow mb-1 block">Categoria</label>
          <Input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="marca / pessoa" />
        </div>
        <div className="flex-1">
          <label className="eyebrow mb-1 block">Nota (opcional)</label>
          <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="observação" />
        </div>
        <div className="flex gap-2">
          <Button variant="gold" disabled={!term.trim() || save.isPending} onClick={() => save.mutate()}>
            {editing ? "Salvar" : "Adicionar"}
          </Button>
          {editing && (
            <Button variant="outline" onClick={reset}>
              Cancelar
            </Button>
          )}
        </div>
      </div>

      <div className="admin-list">
        {terms.map((t) => (
          <div key={t.id} className="admin-row">
            <div className="admin-row-info">
              <span className="admin-row-email">
                {t.term}
                {t.category && <span className="admin-tag">{t.category}</span>}
              </span>
              {t.notes && <span className="admin-row-meta">{t.notes}</span>}
            </div>
            <div className="admin-row-actions">
              <Button size="sm" variant="outline" onClick={() => startEdit(t)}>
                Editar
              </Button>
              {isAdmin && (
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={del.isPending}
                  onClick={() => {
                    if (confirm(`Excluir "${t.term}" do glossário?`)) del.mutate(t.id);
                  }}
                >
                  Excluir
                </Button>
              )}
            </div>
          </div>
        ))}
        {terms.length === 0 && <p className="editor-hint">Glossário vazio.</p>}
      </div>
    </div>
  );
}

export default function RevisorPage() {
  notFound();

  // eslint-disable-next-line no-unreachable
  return (
    <>
      <section className="page-hero">
        <div className="page-hero-inner">
          <p className="eyebrow">IA</p>
          <h1 className="page-title">Revisor IA</h1>
        </div>
      </section>
      <div className="view-pad-narrow">
        <Tabs defaultValue="revisar">
          <TabsList className="mb-6">
            <TabsTrigger value="revisar">Revisar</TabsTrigger>
            <TabsTrigger value="glossario">Glossário</TabsTrigger>
          </TabsList>
          <TabsContent value="revisar">
            <section className="card-block">
              <p className="eyebrow">Revisão</p>
              <h2 className="results-title">Revisar legendas de um Reels</h2>
              <ReviewPanel />
            </section>
          </TabsContent>
          <TabsContent value="glossario">
            <section className="card-block">
              <p className="eyebrow">Referência</p>
              <h2 className="results-title">Glossário de nomes</h2>
              <GlossaryPanel />
            </section>
          </TabsContent>
        </Tabs>
      </div>
    </>
  );
}
