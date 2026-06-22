"use client";

import Link from "next/link";
import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, RotateCcw } from "lucide-react";
import * as api from "@/lib/api";
import { useProject } from "@/hooks/use-projects";
import { ClipGrid } from "@/components/clip-grid";
import { ReprocessDialog } from "@/components/reprocess-dialog";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { fmtScore, timeAgo } from "@/lib/format";

export default function ProjetoPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { data: p, isLoading, isError } = useProject(id);
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);

  async function downloadAll() {
    const url = api.downloadAllUrl(id);
    toast.info("Preparando download...");
    try {
      const r = await fetch(url);
      if (r.ok) {
        const ct = r.headers.get("Content-Type") || "";
        if (ct.includes("application/json")) {
          const d = await r.json();
          if (d?.url) window.location.href = d.url;
          else toast.error("Resposta inesperada do servidor.");
        } else {
          window.location.href = url;
        }
      } else {
        toast.info("Baixar todos aguarda o backend (GET /api/projects/:id/download_all).", {
          duration: 6000,
        });
      }
    } catch {
      toast.info("Baixar todos aguarda o backend (GET /api/projects/:id/download_all).", {
        duration: 6000,
      });
    }
  }

  async function del() {
    const ok = await api.deleteProject(id);
    if (ok) {
      toast.success("Projeto excluído");
      qc.invalidateQueries({ queryKey: ["projects"] });
      router.push("/projetos");
    } else {
      toast.info("Excluir aguarda o backend (DELETE /api/projects/:id).", { duration: 6000 });
    }
  }

  if (isLoading) {
    return (
      <div className="view-pad">
        <div className="skel-grid">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="skel skel-card" />
          ))}
        </div>
      </div>
    );
  }

  if (isError || !p) {
    return (
      <div className="view-pad">
        <Link href="/projetos" className="back-link">
          ← Projetos
        </Link>
        <div className="empty">
          <p className="empty-title">Projeto não encontrado</p>
        </div>
      </div>
    );
  }

  const meta =
    (p.clip_count != null ? p.clip_count + " clipes · " : "") +
    (p.avg_score != null ? "nota média " + fmtScore(p.avg_score) + " · " : "") +
    timeAgo(p.created_at);

  return (
    <div className="view-pad">
      <Link href="/projetos" className="back-link">
        ← Projetos
      </Link>
      <div className="section-head">
        <div>
          <p className="eyebrow">Projeto</p>
          <h2 className="results-title">{p.title || p.filename || "Projeto"}</h2>
          <p className="project-meta">{meta}</p>
        </div>
        <div className="head-actions">
          <Button variant="gold" onClick={downloadAll}>
            <Download /> Baixar todos
          </Button>
          <Button variant="brandGhost" onClick={() => setReprocessOpen(true)}>
            <RotateCcw /> Reprocessar
          </Button>
          <Button variant="destructive" onClick={() => setDelOpen(true)}>
            Excluir
          </Button>
        </div>
      </div>

      <ClipGrid clips={p.clips ?? []} />

      <ReprocessDialog projectId={id} open={reprocessOpen} onOpenChange={setReprocessOpen} />
      <ConfirmDialog
        open={delOpen}
        onOpenChange={setDelOpen}
        title="Excluir projeto?"
        message="Isso remove o projeto e seus clipes."
        okLabel="Excluir"
        danger
        onConfirm={del}
      />
    </div>
  );
}
