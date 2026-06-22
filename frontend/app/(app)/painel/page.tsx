"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import * as api from "@/lib/api";
import { useAuth } from "@/providers/auth-provider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import type { AdminUser } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  active: "Ativo",
  pending: "Pendente",
  rejected: "Recusado",
  inactive: "Inativo",
};

const STATUS_COLOR: Record<string, { bg: string; fg: string }> = {
  active: { bg: "rgba(34,197,94,0.14)", fg: "#16a34a" },
  pending: { bg: "rgba(245,197,24,0.16)", fg: "#b8860b" },
  rejected: { bg: "rgba(239,68,68,0.14)", fg: "#dc2626" },
  inactive: { bg: "rgba(148,163,184,0.16)", fg: "#64748b" },
};

function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLOR[status] ?? STATUS_COLOR.inactive;
  return (
    <span
      style={{
        background: c.bg,
        color: c.fg,
        padding: "2px 10px",
        borderRadius: 999,
        fontSize: "0.74rem",
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

function useUsers() {
  return useQuery({ queryKey: ["admin-users"], queryFn: api.adminUsers });
}

function PendingPanel() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useUsers();
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin-users"] });

  const approve = useMutation({
    mutationFn: (email: string) => api.adminApprove(email),
    onSuccess: () => {
      refresh();
      toast.success("Conta aprovada");
    },
    onError: (e) => toast.error((e as Error)?.message || "Erro ao aprovar"),
  });
  const reject = useMutation({
    mutationFn: (email: string) => api.adminReject(email),
    onSuccess: () => {
      refresh();
      toast.success("Cadastro recusado");
    },
    onError: (e) => toast.error((e as Error)?.message || "Erro ao recusar"),
  });

  if (isLoading) return <div className="skel" style={{ height: 160 }} />;
  if (isError) return <p className="editor-hint">Não consegui carregar os cadastros.</p>;

  const pending = (data?.users ?? []).filter((u) => u.status === "pending");
  if (pending.length === 0)
    return <p className="editor-hint">Nenhum cadastro aguardando aprovação. 🎉</p>;

  const busy = approve.isPending || reject.isPending;
  return (
    <div className="admin-list">
      {pending.map((u) => (
        <div key={u.email} className="admin-row">
          <div className="admin-row-info">
            <span className="admin-row-email">{u.email}</span>
            <span className="admin-row-meta">
              {u.created_at ? "Cadastrado em " + new Date(u.created_at).toLocaleDateString("pt-BR") : "—"}
            </span>
          </div>
          <div className="admin-row-actions">
            <Button size="sm" variant="gold" disabled={busy} onClick={() => approve.mutate(u.email)}>
              Aprovar
            </Button>
            <Button
              size="sm"
              variant="brandGhost"
              disabled={busy}
              onClick={() => {
                if (confirm(`Recusar o cadastro de ${u.email}?`)) reject.mutate(u.email);
              }}
            >
              Recusar
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

function UsersPanel() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const { data, isLoading, isError } = useUsers();
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin-users"] });

  const setActive = useMutation({
    mutationFn: (v: { email: string; active: boolean }) => api.adminSetActive(v.email, v.active),
    onSuccess: () => {
      refresh();
      toast.success("Usuário atualizado");
    },
    onError: (e) => toast.error((e as Error)?.message || "Erro ao atualizar"),
  });
  const setAdmin = useMutation({
    mutationFn: (v: { email: string; is_admin: boolean }) => api.adminSetAdmin(v.email, v.is_admin),
    onSuccess: () => {
      refresh();
      toast.success("Permissão atualizada");
    },
    onError: (e) => toast.error((e as Error)?.message || "Erro ao atualizar"),
  });

  if (isLoading) return <div className="skel" style={{ height: 200 }} />;
  if (isError) return <p className="editor-hint">Não consegui carregar os usuários.</p>;

  const users: AdminUser[] = data?.users ?? [];
  const busy = setActive.isPending || setAdmin.isPending;

  return (
    <div className="admin-list">
      {users.map((u) => {
        const isSelf = u.email === user?.email;
        const isActive = u.status === "active";
        return (
          <div key={u.email} className="admin-row">
            <div className="admin-row-info">
              <span className="admin-row-email">
                {u.email}
                {u.is_admin && <span className="admin-tag">admin</span>}
                {isSelf && <span className="admin-tag admin-tag-self">você</span>}
              </span>
              <span className="admin-row-meta">
                <StatusBadge status={u.status} /> · {u.auth_provider || "—"}
              </span>
            </div>
            <div className="admin-row-actions">
              {u.status === "pending" ? (
                <Button
                  size="sm"
                  variant="gold"
                  disabled={busy}
                  onClick={() => setActive.mutate({ email: u.email, active: true })}
                >
                  Aprovar
                </Button>
              ) : isActive ? (
                <Button
                  size="sm"
                  variant="brandGhost"
                  disabled={busy || isSelf}
                  title={isSelf ? "Você não pode desativar a si mesmo" : ""}
                  onClick={() => setActive.mutate({ email: u.email, active: false })}
                >
                  Desativar
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="brandGhost"
                  disabled={busy}
                  onClick={() => setActive.mutate({ email: u.email, active: true })}
                >
                  Ativar
                </Button>
              )}
              <Button
                size="sm"
                variant={u.is_admin ? "secondary" : "outline"}
                disabled={busy || (isSelf && u.is_admin)}
                title={isSelf && u.is_admin ? "Você não pode remover seu próprio admin" : ""}
                onClick={() => setAdmin.mutate({ email: u.email, is_admin: !u.is_admin })}
              >
                {u.is_admin ? "Remover admin" : "Tornar admin"}
              </Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function PainelPage() {
  const router = useRouter();
  const { user } = useAuth();

  // Gate de admin no cliente (UX). O backend também valida em todo /api/admin/*.
  useEffect(() => {
    if (user && !user.is_admin) router.replace("/");
  }, [user, router]);

  if (!user?.is_admin) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <span className="clip-prep-spin" aria-label="Carregando" />
      </div>
    );
  }

  return (
    <>
      <section className="page-hero">
        <div className="page-hero-inner">
          <p className="eyebrow">Administração</p>
          <h1 className="page-title">Painel</h1>
        </div>
      </section>
      <div className="view-pad-narrow">
        <Tabs defaultValue="pending">
          <TabsList className="mb-6">
            <TabsTrigger value="pending">Pendentes</TabsTrigger>
            <TabsTrigger value="users">Usuários</TabsTrigger>
          </TabsList>
          <TabsContent value="pending">
            <section className="card-block">
              <p className="eyebrow">Aprovação</p>
              <h2 className="results-title">Cadastros aguardando aprovação</h2>
              <PendingPanel />
            </section>
          </TabsContent>
          <TabsContent value="users">
            <section className="card-block">
              <p className="eyebrow">Gerenciamento</p>
              <h2 className="results-title">Todos os usuários</h2>
              <UsersPanel />
            </section>
          </TabsContent>
        </Tabs>
      </div>
    </>
  );
}
