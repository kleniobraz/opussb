"use client";

import Link from "next/link";
import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, RotateCcw } from "lucide-react";
import * as api from "@/lib/api";
import { useProject } from "@/hooks/use-projects";
import { useAuth } from "@/providers/auth-provider";
import { ClipGrid } from "@/components/clip-grid";
import { ReprocessDialog } from "@/components/reprocess-dialog";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { fmtScore, timeAgo, nameFromEmail } from "@/lib/format";

export default function ProjetoPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { user } = useAuth();
  const { data: p, isLoading, isError } = useProject(id);
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);

  async function downloadAll() {
    try {
      const r = await fetch(api.downloadAllUrl(id));
      if (!r.ok) {
        toast.error("Não consegui preparar os downloads.");
        return;
      }
      const d = await r.json();
      const clips: { url: string; filename?: string }[] = Array.isArray(d?.clips) ? d.clips : [];
      if (clips.length === 0) {
        toast.info("Esse projeto ainda não tem clipes pra baixar.");
        return;
      }
      toast.success("Baixando " + clips.length + (clips.length > 1 ? " clipes..." : " clipe..."));
      // Dispara um download por clipe, escalonado, pra o navegador não barrar a rajada. Cada link
      // bate na rota de vídeo (?dl=1), que entrega o clipe queimado já como anexo; a queima é
      // serializada pelo semáforo do backend, então não derruba o site.
      clips.forEach((c, i) => {
        window.setTimeout(() => {
          const a = document.createElement("a");
          a.href = c.url;
          if (c.filename) a.download = c.filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
        }, i * 700);
      });
    } catch {
      toast.error("Não consegui preparar os downloads.");
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
    timeAgo(p.created_at) +
    (p.owner_email ? " · enviado por " + nameFromEmail(p.owner_email) : "");

  // Biblioteca compartilhada: todos acessam, mas só o dono (quem subiu) ou um admin
  // excluem. O backend reforça isso (403); aqui é só pra não mostrar o botão à toa.
  const canDelete = !!user && (!!user.is_admin || user.email === p.owner_email);

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
          {canDelete && (
            <Button variant="destructive" onClick={() => setDelOpen(true)}>
              Excluir
            </Button>
          )}
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
