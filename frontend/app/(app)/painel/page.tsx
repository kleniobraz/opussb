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
import { V3Card } from "@/components/ui/v3-card";
import { ParticlesBackground } from "@/components/backgrounds/particles-background";
import { cn } from "@/lib/utils";
import type { AdminUser } from "@/lib/types";

// Reskin das abas pro estilo pill mono do v3 (mesmo espirito dos chips de ordenacao de
// projetos/page.tsx), sem editar tabs.tsx (componente generico usado em outras telas). O
// prefixo `!` garante que a cor vença o data-state=active padrao do Radix (que ja chega com
// bg/texto proprios) independente da ordem de geracao das classes no Tailwind.
const TAB_TRIGGER_CLS =
  "rounded-full border !border-v3-border-hover !bg-transparent px-3.5 py-1.5 font-mono text-xs font-semibold uppercase tracking-wide !text-v3-text-soft transition-colors hover:!border-v3-accent hover:!text-white data-active:!border-v3-accent data-active:!bg-v3-accent data-active:!text-v3-on-accent data-active:!shadow-none";
const TABS_LIST_CLS =
  "h-auto w-full flex-wrap items-center justify-start gap-2 rounded-none !bg-transparent !p-0";

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="font-mono text-xs font-semibold uppercase tracking-[0.2em] text-v3-text-soft">
      {children}
    </p>
  );
}

function ResultsTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-6 mt-2 font-v3-display text-xl font-black uppercase text-white">
      {children}
    </h2>
  );
}

const STATUS_LABEL: Record<string, string> = {
  active: "Ativo",
  pending: "Pendente",
  rejected: "Recusado",
  inactive: "Inativo",
};

// Pill mono com borda + fundo translucido, mesmo espirito do StatusPill de
// components/projetos/project-card.tsx. A diferenciacao semantica de cor se mantem:
// ativo=verde, pendente=acento, recusado=vermelho, inativo=cinza.
const STATUS_STYLE: Record<string, string> = {
  active: "border-[var(--green)]/40 text-[var(--green)]",
  pending: "border-v3-accent/50 text-v3-accent",
  rejected: "border-[var(--red)]/40 text-[var(--red)]",
  inactive: "border-v3-border-hover text-v3-text-soft",
};

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLE[status] ?? STATUS_STYLE.inactive;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border bg-v3-bg/60 px-2.5 py-0.5 font-mono text-[0.68rem] font-semibold uppercase tracking-wide",
        cls,
      )}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

function Tag({ accent = false, children }: { accent?: boolean; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 font-mono text-[0.62rem] font-bold uppercase tracking-wide",
        accent ? "bg-v3-accent/12 text-v3-accent" : "bg-v3-text-soft/15 text-v3-text-soft",
      )}
    >
      {children}
    </span>
  );
}

function AdminList({ children }: { children: React.ReactNode }) {
  return <div className="mt-5 flex flex-col gap-2.5">{children}</div>;
}

function AdminRow({ children }: { children: React.ReactNode }) {
  return (
    <V3Card className="flex flex-wrap items-center justify-between gap-3.5 px-4 py-3.5">{children}</V3Card>
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

  if (isLoading) return <div className="h-40 animate-pulse rounded-[var(--radius-md)] bg-v3-surface-1" />;
  if (isError) return <p className="font-mono text-xs text-v3-text-soft">Não consegui carregar os cadastros.</p>;

  const pending = (data?.users ?? []).filter((u) => u.status === "pending");
  if (pending.length === 0)
    return <p className="font-mono text-xs text-v3-text-soft">Nenhum cadastro aguardando aprovação. 🎉</p>;

  const busy = approve.isPending || reject.isPending;
  return (
    <AdminList>
      {pending.map((u) => (
        <AdminRow key={u.email}>
          <div className="flex min-w-0 flex-col gap-1">
            <span className="truncate font-v3-sans text-sm font-bold text-white">{u.email}</span>
            <span className="font-mono text-xs text-v3-text-soft">
              {u.created_at ? "Cadastrado em " + new Date(u.created_at).toLocaleDateString("pt-BR") : "—"}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="v3Cta" disabled={busy} onClick={() => approve.mutate(u.email)}>
              Aprovar
            </Button>
            <Button
              size="sm"
              variant="v3Ghost"
              disabled={busy}
              onClick={() => {
                if (confirm(`Recusar o cadastro de ${u.email}?`)) reject.mutate(u.email);
              }}
            >
              Recusar
            </Button>
          </div>
        </AdminRow>
      ))}
    </AdminList>
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

  if (isLoading) return <div className="h-52 animate-pulse rounded-[var(--radius-md)] bg-v3-surface-1" />;
  if (isError) return <p className="font-mono text-xs text-v3-text-soft">Não consegui carregar os usuários.</p>;

  const users: AdminUser[] = data?.users ?? [];
  const busy = setActive.isPending || setAdmin.isPending || del.isPending;

  return (
    <AdminList>
      {users.map((u) => {
        const isSelf = u.email === user?.email;
        const isActive = u.status === "active";
        return (
          <AdminRow key={u.email}>
            <div className="flex min-w-0 flex-col gap-1">
              <span className="flex flex-wrap items-center gap-2 truncate font-v3-sans text-sm font-bold text-white">
                {u.email}
                {u.is_admin && <Tag accent>admin</Tag>}
                {isSelf && <Tag>você</Tag>}
              </span>
              <span className="flex items-center gap-2 font-mono text-xs text-v3-text-soft">
                <StatusBadge status={u.status} /> {u.auth_provider || "—"}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {u.status === "pending" ? (
                <Button
                  size="sm"
                  variant="v3Cta"
                  disabled={busy}
                  onClick={() => setActive.mutate({ email: u.email, active: true })}
                >
                  Aprovar
                </Button>
              ) : isActive ? (
                <Button
                  size="sm"
                  variant="v3Ghost"
                  disabled={busy || isSelf}
                  title={isSelf ? "Você não pode desativar a si mesmo" : ""}
                  onClick={() => setActive.mutate({ email: u.email, active: false })}
                >
                  Desativar
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="v3Ghost"
                  disabled={busy}
                  onClick={() => setActive.mutate({ email: u.email, active: true })}
                >
                  Ativar
                </Button>
              )}
              <Button
                size="sm"
                variant="v3Ghost"
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
          </AdminRow>
        );
      })}
    </AdminList>
  );
}

function CreditsPanel() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-credits"],
    queryFn: () => api.adminCredits(),
  });
  const fmt = (n?: number | null) => (n == null ? "—" : "US$ " + n.toFixed(2));

  if (isLoading) return <p className="font-mono text-xs text-v3-text-soft">Carregando saldos…</p>;
  if (isError) return <p className="font-mono text-xs text-v3-text-soft">Não consegui buscar os saldos agora.</p>;

  const rp = data?.runpod;
  const lowRunpod = rp && rp.balance < 5;

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <V3Card className="p-5">
          <div className="mb-3 flex flex-col gap-0.5">
            <span className="font-v3-sans text-sm font-bold text-white">RunPod</span>
            <span className="font-mono text-xs text-v3-text-soft">Whisper + reframe (GPU)</span>
          </div>
          {rp ? (
            <>
              <div
                className={cn(
                  "font-mono text-3xl font-black tabular-nums",
                  lowRunpod ? "text-[var(--red)]" : "text-v3-accent",
                )}
              >
                {fmt(rp.balance)}
              </div>
              <div className="mt-1 font-mono text-xs text-v3-text-soft">
                gastando {fmt(rp.spend_per_hr)}/h agora
              </div>
            </>
          ) : (
            <div className="font-mono text-xs text-v3-text-soft">indisponível no momento</div>
          )}
        </V3Card>

        <V3Card className="p-5">
          <div className="mb-3 flex flex-col gap-0.5">
            <span className="font-v3-sans text-sm font-bold text-white">Claude (API)</span>
            <span className="font-mono text-xs text-v3-text-soft">pontuação dos clipes</span>
          </div>
          {data?.anthropic?.configured ? (
            <div className="font-mono text-xs text-v3-text-soft">Admin key configurada</div>
          ) : (
            <div className="font-mono text-xs text-v3-text-soft">
              saldo no{" "}
              <a
                className="text-v3-accent underline-offset-2 hover:underline"
                href="https://platform.claude.com"
                target="_blank"
                rel="noreferrer"
              >
                Console
              </a>
              <br />
              (integração da API entra quando houver a Admin key)
            </div>
          )}
        </V3Card>
      </div>

      <Button
        variant="v3Ghost"
        size="sm"
        className="mt-4"
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
      </Button>
      <p className="mt-3 font-mono text-xs text-v3-text-soft">
        Atualiza no máximo a cada 5 min (cache); use &quot;Atualizar&quot; pra forçar.
      </p>
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
  // e inverteria a ação. O POST é estado-desejado idempotente, então basta o
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
  // restart do gunicorn no deploy), é quando mais se precisa do controle.
  if (isLoading && !data) return <div className="h-32 animate-pulse rounded-[var(--radius-md)] bg-v3-surface-1" />;
  if (isError && !data)
    return (
      <div className="flex flex-col gap-3">
        <p className="font-mono text-xs text-v3-text-soft">Não consegui carregar o estado da manutenção.</p>
        <Button size="sm" variant="v3Ghost" onClick={() => refetch()}>
          Tentar de novo
        </Button>
      </div>
    );

  const on = !!data?.on;
  const ipOk = !!data?.ip_allowed;
  const ip = data?.client_ip || "desconhecido";
  return (
    <div className="flex flex-col gap-4">
      <V3Card className="flex flex-wrap items-center justify-between gap-3.5 px-4 py-3.5" active={on}>
        <div className="flex min-w-0 flex-col gap-1.5">
          <span className="flex flex-wrap items-center gap-2 font-v3-sans text-sm font-bold text-white">
            Modo manutenção
            {on && <Tag accent>ligado</Tag>}
          </span>
          <span className="font-mono text-xs text-v3-text-soft">
            {on
              ? "Quem está fora da lista de IPs recebe 503 nas páginas E na API: o app fica inacessível durante a manutenção."
              : "Site aberto pra todo mundo."}
          </span>
          <span className="font-mono text-xs text-v3-text-soft">
            Seu IP: {ip} · {ipOk ? "liberado durante a manutenção" : "fora da lista de liberados"}
          </span>
        </div>
        <Switch
          checked={on}
          disabled={setMaint.isPending}
          onCheckedChange={(next) => setTarget(next)}
          className="data-checked:bg-v3-accent"
        />
      </V3Card>
      <p className="font-mono text-xs text-v3-text-soft">
        A manutenção bloqueia as páginas e a /api de quem está fora da lista de IPs; só /health segue no ar.
        Break-glass: se você estiver fora da lista, a própria API de admin fica bloqueada, e o comando
        &quot;maintenance off&quot; por SSH é o jeito de desligar.
      </p>
      <ConfirmDialog
        open={target !== null}
        onOpenChange={(v) => { if (!v) setTarget(null); }}
        title={target ? "Ligar o modo manutenção?" : "Desligar o modo manutenção?"}
        message={
          target
            ? (ipOk
                ? `Quem está fora da lista de IPs passa a receber 503 nas páginas E na API imediatamente: ninguém grava no app durante a janela. Seu IP (${ip}) está liberado, você continua acessando normal.`
                : `ATENÇÃO: seu IP (${ip}) NÃO está na lista de liberados. No modo hard a /api também é bloqueada, e esta aba (inclusive este botão) para de funcionar no próximo request, e só o break-glass por SSH (maintenance off) desliga. Libere seu IP no geo do nginx antes, ou ligue ciente disso.`)
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
    <div className="relative bg-v3-surface-1">
      <section className="relative overflow-hidden border-b border-v3-border pb-8 pt-8">
        <div className="v3-home-glow" aria-hidden="true" />
        <ParticlesBackground />
        <div className="relative z-[1] mx-auto max-w-[1180px] px-7 pt-8">
          <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
            Administração
          </p>
          <h1 className="mt-2 font-v3-display text-4xl font-black uppercase text-white sm:text-5xl lg:text-6xl">
            Painel
          </h1>
        </div>
      </section>

      {/* Fora da faixa de atmosfera (glow/particulas), mesmo raciocinio de projetos/page.tsx:
          o conteudo das abas e variavel, nao pode esticar a secao do glow junto. */}
      <div className="relative z-[1] mx-auto max-w-[1180px] px-7 py-10">
        <Tabs defaultValue="pending">
          <TabsList className={TABS_LIST_CLS}>
            <TabsTrigger value="pending" className={TAB_TRIGGER_CLS}>
              Pendentes
            </TabsTrigger>
            <TabsTrigger value="users" className={TAB_TRIGGER_CLS}>
              Usuários
            </TabsTrigger>
            <TabsTrigger value="credits" className={TAB_TRIGGER_CLS}>
              Saldos
            </TabsTrigger>
            <TabsTrigger value="system" className={TAB_TRIGGER_CLS}>
              Sistema
            </TabsTrigger>
          </TabsList>
          <TabsContent value="pending" className="mt-6">
            <V3Card className="p-7">
              <Eyebrow>Aprovação</Eyebrow>
              <ResultsTitle>Cadastros aguardando aprovação</ResultsTitle>
              <PendingPanel />
            </V3Card>
          </TabsContent>
          <TabsContent value="users" className="mt-6">
            <V3Card className="p-7">
              <Eyebrow>Gerenciamento</Eyebrow>
              <ResultsTitle>Todos os usuários</ResultsTitle>
              <UsersPanel />
            </V3Card>
          </TabsContent>
          <TabsContent value="credits" className="mt-6">
            <V3Card className="p-7">
              <Eyebrow>Custos</Eyebrow>
              <ResultsTitle>Saldos de crédito</ResultsTitle>
              <CreditsPanel />
            </V3Card>
          </TabsContent>
          <TabsContent value="system" className="mt-6">
            <V3Card className="p-7">
              <Eyebrow>Operação</Eyebrow>
              <ResultsTitle>Modo manutenção</ResultsTitle>
              <MaintenancePanel />
            </V3Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
