"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import * as api from "@/lib/api";
import { useAuth } from "@/providers/auth-provider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ConfirmDialog } from "@/components/confirm-dialog";
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
  const del = useMutation({
    mutationFn: (email: string) => api.adminDelete(email),
    onSuccess: () => {
      refresh();
      toast.success("Usuário excluído");
    },
    onError: (e) => toast.error((e as Error)?.message || "Erro ao excluir"),
  });

  if (isLoading) return <div className="skel" style={{ height: 200 }} />;
  if (isError) return <p className="editor-hint">Não consegui carregar os usuários.</p>;

  const users: AdminUser[] = data?.users ?? [];
  const busy = setActive.isPending || setAdmin.isPending || del.isPending;

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
              {(u.status === "rejected" || u.status === "inactive") && !isSelf && (
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={busy}
                  title="Excluir em definitivo (só recusado ou inativo)"
                  onClick={() => {
                    if (
                      confirm(
                        `Excluir ${u.email} em definitivo? Esta ação não pode ser desfeita. Os projetos compartilhados continuam.`,
                      )
                    )
                      del.mutate(u.email);
                  }}
                >
                  Excluir
                </Button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CreditsPanel() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-credits"],
    queryFn: () => api.adminCredits(),
  });
  const fmt = (n?: number | null) => (n == null ? "—" : "US$ " + n.toFixed(2));

  if (isLoading) return <p className="text-ink-muted">Carregando saldos…</p>;
  if (isError) return <p className="text-ink-muted">Não consegui buscar os saldos agora.</p>;

  const rp = data?.runpod;
  const lowRunpod = rp && rp.balance < 5;

  return (
    <>
      <div className="credits-grid">
        <div className="credits-card">
          <div className="credits-card-head">
            <span className="credits-name">RunPod</span>
            <span className="credits-sub">Whisper + reframe (GPU)</span>
          </div>
          {rp ? (
            <>
              <div className={"credits-balance" + (lowRunpod ? " is-low" : "")}>{fmt(rp.balance)}</div>
              <div className="credits-meta">gastando {fmt(rp.spend_per_hr)}/h agora</div>
            </>
          ) : (
            <div className="credits-meta">indisponível no momento</div>
          )}
        </div>

        <div className="credits-card">
          <div className="credits-card-head">
            <span className="credits-name">Claude (API)</span>
            <span className="credits-sub">pontuação dos clipes</span>
          </div>
          {data?.anthropic?.configured ? (
            <div className="credits-meta">Admin key configurada</div>
          ) : (
            <div className="credits-meta">
              saldo no{" "}
              <a className="link-gold" href="https://platform.claude.com" target="_blank" rel="noreferrer">
                Console
              </a>
              <br />
              (integração da API entra quando houver a Admin key)
            </div>
          )}
        </div>
      </div>

      <button
        type="button"
        className="chip mt-4"
        onClick={async () => {
          try {
            await api.adminCredits(true);
            qc.invalidateQueries({ queryKey: ["admin-credits"] });
            toast.success("Saldos atualizados");
          } catch {
            toast.error("Falha ao atualizar.");
          }
        }}
      >
        Atualizar
      </button>
      <p className="credits-note">Atualiza no máximo a cada 5 min (cache); use "Atualizar" pra forçar.</p>
    </>
  );
}


function MaintenancePanel() {
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["admin-maintenance"],
    queryFn: api.adminMaintenance,
    // O flag também muda por fora (break-glass via SSH, outro admin): re-checa
    // periodicamente pra o switch não mentir.
    refetchInterval: 30_000,
  });
  // Congela a intenção (ligar/desligar) no clique. O `on` vivo muda por baixo do
  // dialog aberto (refetch de 30s, foco da janela, outro admin, break-glass SSH)
  // e inverteria a ação — o POST é estado-desejado idempotente, então basta o
  // front preservar o alvo. null = dialog fechado.
  const [target, setTarget] = useState<boolean | null>(null);
  const setMaint = useMutation({
    mutationFn: (on: boolean) => api.adminSetMaintenance(on),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["admin-maintenance"] });
      toast.success(r.on ? "Modo manutenção LIGADO" : "Modo manutenção desligado");
    },
    onError: (e) => toast.error((e as Error)?.message || "Erro ao alterar o modo manutenção"),
  });

  // Só o primeiro load (sem cache) mostra skeleton/erro cheio; havendo dado em
  // cache, mantém o switch de pé mesmo num refetch que falhe (ex.: durante o
  // restart do gunicorn no deploy) — é quando mais se precisa do controle.
  if (isLoading && !data) return <div className="skel" style={{ height: 120 }} />;
  if (isError && !data)
    return (
      <div className="admin-list">
        <p className="editor-hint">Não consegui carregar o estado da manutenção.</p>
        <Button size="sm" variant="brandGhost" onClick={() => refetch()}>
          Tentar de novo
        </Button>
      </div>
    );

  const on = !!data?.on;
  const ipOk = !!data?.ip_allowed;
  const ip = data?.client_ip || "desconhecido";
  return (
    <div className="admin-list">
      <div className="admin-row">
        <div className="admin-row-info">
          <span className="admin-row-email">
            Modo manutenção
            {on && <span className="admin-tag">ligado</span>}
          </span>
          <span className="admin-row-meta">
            {on
              ? "Quem está fora da lista de IPs recebe 503 nas páginas E na API — o app fica inacessível durante a manutenção."
              : "Site aberto pra todo mundo."}
          </span>
          <span className="admin-row-meta">
            Seu IP: {ip} · {ipOk ? "liberado durante a manutenção" : "fora da lista de liberados"}
          </span>
        </div>
        <div className="admin-row-actions">
          <Switch
            checked={on}
            disabled={setMaint.isPending}
            onCheckedChange={(next) => setTarget(next)}
          />
        </div>
      </div>
      <p className="credits-note">
        A manutenção bloqueia as páginas e a /api de quem está fora da lista de IPs; só /health segue no ar.
        Break-glass: se você estiver fora da lista, a própria API de admin fica bloqueada — o comando
        “maintenance off” por SSH é o jeito de desligar.
      </p>
      <ConfirmDialog
        open={target !== null}
        onOpenChange={(v) => { if (!v) setTarget(null); }}
        title={target ? "Ligar o modo manutenção?" : "Desligar o modo manutenção?"}
        message={
          target
            ? (ipOk
                ? `Quem está fora da lista de IPs passa a receber 503 nas páginas E na API imediatamente — ninguém grava no app durante a janela. Seu IP (${ip}) está liberado, você continua acessando normal.`
                : `ATENÇÃO: seu IP (${ip}) NÃO está na lista de liberados. No modo hard a /api também é bloqueada — esta aba (inclusive este botão) para de funcionar no próximo request e só o break-glass por SSH (maintenance off) desliga. Libere seu IP no geo do nginx antes, ou ligue ciente disso.`)
            : "O site volta a abrir pra todo mundo imediatamente."
        }
        okLabel={target ? "Ligar manutenção" : "Desligar"}
        danger={!!target && !ipOk}
        onConfirm={() => { if (target !== null) setMaint.mutate(target); }}
      />
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
            <TabsTrigger value="credits">Saldos</TabsTrigger>
            <TabsTrigger value="system">Sistema</TabsTrigger>
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
          <TabsContent value="credits">
            <section className="card-block">
              <p className="eyebrow">Custos</p>
              <h2 className="results-title">Saldos de crédito</h2>
              <CreditsPanel />
            </section>
          </TabsContent>
          <TabsContent value="system">
            <section className="card-block">
              <p className="eyebrow">Operação</p>
              <h2 className="results-title">Modo manutenção</h2>
              <MaintenancePanel />
            </section>
          </TabsContent>
        </Tabs>
      </div>
    </>
  );
}
