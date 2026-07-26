"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download } from "lucide-react";
import * as api from "@/lib/api";
import { useProject } from "@/hooks/use-projects";
import { useAuth } from "@/providers/auth-provider";
import { ClipGrid } from "@/components/clip-grid";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { fmtScore, timeAgo, nameFromEmail } from "@/lib/format";

export default function ProjetoPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { user } = useAuth();
  const { data: p, isLoading, isError } = useProject(id);
  const { data: endScreens } = useQuery({ queryKey: ["endScreens"], queryFn: api.getEndScreens });
  const [delOpen, setDelOpen] = useState(false);
  const [delBusy, setDelBusy] = useState(false);
  const [endSaving, setEndSaving] = useState(false);
  const esFileRef = useRef<HTMLInputElement>(null);

  // Tela final PADRAO do projeto (os clipes herdam; removivel por clipe no editor). Acao imediata:
  // grava e recarrega o projeto + invalida as legendas (editores abertos releem a heranca).
  async function changeProjectEndScreen(value: string) {
    const v = value || null;
    setEndSaving(true);
    try {
      await api.putProjectEndScreen(id, v);
      toast.success(v ? "Tela final do projeto definida." : "Tela final do projeto removida.");
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: ["captions"] });
    } catch {
      toast.error("Falha ao definir a tela final do projeto.");
    } finally {
      setEndSaving(false);
    }
  }

  // Sobe uma tela final nova (mp4 até 10s) e já a define como padrão deste projeto.
  async function onUploadEndScreen(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite re-subir o mesmo arquivo depois
    if (!file) return;
    setEndSaving(true);
    toast.info("Enviando tela final…");
    try {
      const r = await api.uploadEndScreen(file);
      const newId = r.end_screen?.id;
      if (!newId) throw new Error("upload");
      await api.putProjectEndScreen(id, newId); // já usa no projeto
      toast.success("Tela final enviada e aplicada ao projeto.");
      qc.invalidateQueries({ queryKey: ["endScreens"] });
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: ["captions"] });
    } catch (err) {
      toast.error((err as Error)?.message || "Falha ao enviar a tela final (máx. 10s, .mp4).");
    } finally {
      setEndSaving(false);
    }
  }

  // Remove a tela final selecionada da BIBLIOTECA (some de todos os projetos que a usavam).
  async function deleteSelectedEndScreen() {
    const cur = p?.end_screen;
    if (!cur) return;
    const label = endScreens?.end_screens.find((o) => o.id === cur)?.label ?? "esta tela final";
    if (!window.confirm(`Excluir "${label}" da biblioteca? Ela sai de todos os projetos que a usam.`)) return;
    setEndSaving(true);
    try {
      await api.deleteEndScreen(cur);
      toast.success("Tela final excluída da biblioteca.");
      qc.invalidateQueries({ queryKey: ["endScreens"] });
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: ["captions"] });
    } catch {
      toast.error("Falha ao excluir a tela final.");
    } finally {
      setEndSaving(false);
    }
  }

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
    if (delBusy) return;
    setDelBusy(true);
    try {
      await api.deleteProject(id);
      toast.success("Projeto excluído");
      qc.invalidateQueries({ queryKey: ["projects"] });
      router.push("/projetos");
    } catch (e) {
      // Mensagem do backend, não texto genérico: a exclusão agora ABORTA quando o
      // armazenamento não responde, e o usuário precisa saber que o projeto continua aqui.
      toast.error(e instanceof api.ApiError ? e.message : "Não consegui excluir o projeto.",
        { duration: 8000 });
    } finally {
      setDelBusy(false);
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
          <div className="project-endscreen">
            <span>Tela final</span>
            <select
              value={p.end_screen ?? ""}
              disabled={endSaving}
              onChange={(e) => changeProjectEndScreen(e.target.value)}
            >
              <option value="">Nenhuma</option>
              {(endScreens?.end_screens ?? []).map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="project-es-btn"
              disabled={endSaving}
              onClick={() => esFileRef.current?.click()}
            >
              + Enviar
            </button>
            {p.end_screen && (
              <button
                type="button"
                className="project-es-btn project-es-del"
                disabled={endSaving}
                onClick={deleteSelectedEndScreen}
                title="Excluir esta tela final da biblioteca"
              >
                Excluir
              </button>
            )}
            <input
              ref={esFileRef}
              type="file"
              accept="video/mp4,video/quicktime"
              hidden
              onChange={onUploadEndScreen}
            />
            <span className="project-endscreen-hint">
              .mp4 até 10s · os clipes herdam (removível/encurtável por clipe no editor)
            </span>
          </div>
        </div>
        <div className="head-actions">
          <Button variant="gold" onClick={downloadAll}>
            <Download /> Baixar todos
          </Button>
          {canDelete && (
            <Button variant="destructive" disabled={delBusy} onClick={() => setDelOpen(true)}>
              {delBusy ? "Excluindo..." : "Excluir"}
            </Button>
          )}
        </div>
      </div>

      <ClipGrid clips={p.clips ?? []} />

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
