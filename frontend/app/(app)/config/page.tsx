"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Film, Monitor, Scissors, Smartphone, Star } from "lucide-react";
import * as api from "@/lib/api";
import { useAuth } from "@/providers/auth-provider";
import { usePrefs } from "@/hooks/use-prefs";
import { useRevokeAllSessions, useRevokeSession, useSessions } from "@/hooks/use-sessions";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatCard } from "@/components/stat-card";
import { V3Card } from "@/components/ui/v3-card";
import { ParticlesBackground } from "@/components/backgrounds/particles-background";
import type { Session } from "@/lib/types";

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

function CtrlLabel({ htmlFor, children }: { htmlFor?: string; children: React.ReactNode }) {
  return (
    <Label htmlFor={htmlFor} className="mb-1.5 block font-mono text-xs uppercase tracking-wide text-v3-text-soft">
      {children}
    </Label>
  );
}

function PrefsPanel() {
  const { prefs, isLoaded, save } = usePrefs();
  const [min, setMin] = useState(prefs.defaultMin);
  const [max, setMax] = useState(prefs.defaultMax);
  const [aspect, setAspect] = useState(prefs.aspect);
  const [language, setLanguage] = useState(prefs.language);
  const [captions, setCaptions] = useState(prefs.captions);
  const [autoplay, setAutoplay] = useState(prefs.autoplayPreview);

  useEffect(() => {
    if (!isLoaded) return;
    setMin(prefs.defaultMin);
    setMax(prefs.defaultMax);
    setAspect(prefs.aspect);
    setLanguage(prefs.language);
    setCaptions(prefs.captions);
    setAutoplay(prefs.autoplayPreview);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoaded]);

  function onSave() {
    if (min >= max) {
      toast.error("A duração mínima deve ser menor que a máxima.");
      return;
    }
    save.mutate(
      {
        defaultMin: min,
        defaultMax: max,
        aspect,
        language,
        captions,
        autoplayPreview: autoplay,
      },
      { onSuccess: () => toast.success("Preferências salvas") },
    );
  }

  return (
    <V3Card className="p-7">
      <Eyebrow>Preferências de geração</Eyebrow>
      <ResultsTitle>Padrões de clipe</ResultsTitle>
      <div className="flex gap-3.5">
        <div className="flex-1">
          <CtrlLabel htmlFor="cfg-min">Duração mínima (s)</CtrlLabel>
          <Input
            id="cfg-min"
            type="number"
            min={5}
            max={300}
            value={min}
            onChange={(e) => setMin(parseInt(e.target.value, 10) || 0)}
          />
        </div>
        <div className="flex-1">
          <CtrlLabel htmlFor="cfg-max">Duração máxima (s)</CtrlLabel>
          <Input
            id="cfg-max"
            type="number"
            min={10}
            max={600}
            value={max}
            onChange={(e) => setMax(parseInt(e.target.value, 10) || 0)}
          />
        </div>
      </div>
      <div className="mt-4">
        <CtrlLabel>Formato padrão</CtrlLabel>
        <Select value={aspect} onValueChange={setAspect}>
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="9:16">9:16 (Reels/Shorts/TikTok)</SelectItem>
            <SelectItem value="1:1">1:1 (Feed)</SelectItem>
            <SelectItem value="16:9">16:9 (YouTube)</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="mt-4">
        <CtrlLabel>Idioma da transcrição</CtrlLabel>
        <Select value={language} onValueChange={setLanguage}>
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="pt">Português</SelectItem>
            <SelectItem value="en">Inglês</SelectItem>
            <SelectItem value="es">Espanhol</SelectItem>
            <SelectItem value="auto">Detectar automaticamente</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <label className="mt-4 flex cursor-pointer items-center gap-3 text-sm text-v3-text-soft">
        <Switch checked={captions} onCheckedChange={setCaptions} className="data-checked:bg-v3-accent" />
        <span>Gerar legendas por padrão</span>
      </label>
      <label className="mt-3 flex cursor-pointer items-center gap-3 text-sm text-v3-text-soft">
        <Switch checked={autoplay} onCheckedChange={setAutoplay} className="data-checked:bg-v3-accent" />
        <span>Tocar prévia automaticamente</span>
      </label>
      <div>
        <Button
          variant="v3Cta"
          size="brand"
          className="mt-6"
          onClick={onSave}
          disabled={save.isPending}
        >
          Salvar preferências
        </Button>
      </div>
    </V3Card>
  );
}

function SessionRow({ s, onRevoke }: { s: Session; onRevoke: () => void }) {
  const mobile = /iOS|Android/i.test(s.os || "");
  const meta = [s.location, s.last_active].filter(Boolean).join(" · ");
  return (
    <div className="flex items-center gap-3.5 border-b border-v3-border py-3.5 last:border-b-0">
      <span
        className="grid size-9 shrink-0 place-items-center rounded-[var(--radius-sm)] border border-v3-border-hover text-v3-accent"
        aria-hidden="true"
      >
        {mobile ? <Smartphone className="size-4" /> : <Monitor className="size-4" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate font-v3-sans text-sm font-bold text-white">
          {(s.browser || "Navegador") + (s.os ? " · " + s.os : "")}
        </p>
        {meta && <p className="mt-0.5 truncate font-mono text-xs text-v3-text-soft">{meta}</p>}
      </div>
      {s.current ? (
        <span className="shrink-0 font-mono text-xs font-semibold uppercase tracking-wide text-v3-accent">
          esta sessão
        </span>
      ) : (
        <Button variant="v3Ghost" size="sm" onClick={onRevoke}>
          Encerrar
        </Button>
      )}
    </div>
  );
}

function SecurityPanel() {
  const { changePassword } = useAuth();
  const sessions = useSessions();
  const revoke = useRevokeSession();
  const revokeAll = useRevokeAllSessions();
  const [cur, setCur] = useState("");
  const [n1, setN1] = useState("");
  const [n2, setN2] = useState("");
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (n1 !== n2) return setErr("As senhas novas não coincidem.");
    if (n1.length < 8) return setErr("A nova senha precisa ter ao menos 8 caracteres.");
    setSaving(true);
    try {
      await changePassword(cur, n1);
      toast.success("Senha alterada");
      setCur("");
      setN1("");
      setN2("");
    } catch (ex) {
      setErr((ex as Error)?.message || "Falha ao trocar a senha.");
    } finally {
      setSaving(false);
    }
  }

  const list = sessions.data?.sessions ?? [];

  return (
    <div className="flex flex-col gap-6">
      <V3Card className="p-7">
        <Eyebrow>Segurança</Eyebrow>
        <ResultsTitle>Trocar senha</ResultsTitle>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div>
            <CtrlLabel htmlFor="cfg-cur">Senha atual</CtrlLabel>
            <Input
              id="cfg-cur"
              type="password"
              autoComplete="current-password"
              value={cur}
              onChange={(e) => setCur(e.target.value)}
              required
            />
          </div>
          <div>
            <CtrlLabel htmlFor="cfg-new">Nova senha (mín. 8)</CtrlLabel>
            <Input
              id="cfg-new"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={n1}
              onChange={(e) => setN1(e.target.value)}
              required
            />
          </div>
          <div>
            <CtrlLabel htmlFor="cfg-new2">Repita a nova senha</CtrlLabel>
            <Input
              id="cfg-new2"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={n2}
              onChange={(e) => setN2(e.target.value)}
              required
            />
          </div>
          <Button type="submit" variant="v3Cta" size="brand" className="w-full" disabled={saving}>
            {saving ? "Salvando…" : "Salvar nova senha"}
          </Button>
          {err && <p className="font-mono text-sm font-medium text-[var(--red)]">{err}</p>}
        </form>
      </V3Card>

      <V3Card className="p-7">
        <Eyebrow>Segurança</Eyebrow>
        <ResultsTitle>Sessões ativas</ResultsTitle>
        <p className="text-sm text-v3-text-soft">
          Onde sua conta está conectada. Encerre qualquer sessão que você não reconhece.
        </p>
        <div className="mt-4">
          {sessions.isLoading ? (
            <div className="h-14 animate-pulse rounded-[var(--radius-md)] bg-v3-surface-1" />
          ) : sessions.isError ? (
            <p className="font-mono text-xs text-v3-text-soft">Não consegui carregar as sessões agora.</p>
          ) : (
            list.map((s, i) => (
              <SessionRow
                key={s.id ?? i}
                s={s}
                onRevoke={() =>
                  revoke.mutate(s.id as string, {
                    onSuccess: () => toast.success("Sessão encerrada"),
                    onError: () =>
                      toast.info("Encerrar sessão aguarda o backend (DELETE /api/sessions/:id)."),
                  })
                }
              />
            ))
          )}
        </div>
        <Button
          variant="v3Ghost"
          className="mt-5"
          onClick={() =>
            revokeAll.mutate(undefined, {
              onSuccess: () => toast.success("Outras sessões encerradas"),
              onError: () =>
                toast.info("Sair de todos aguarda o backend (POST /api/sessions/revoke_all)."),
            })
          }
        >
          Sair de todos os outros dispositivos
        </Button>
      </V3Card>
    </div>
  );
}

function AccountPanel() {
  const { user } = useAuth();
  const { data } = useQuery({ queryKey: ["stats"], queryFn: api.getStats });
  return (
    <div className="flex flex-col gap-6">
      <V3Card className="p-7">
        <Eyebrow>Conta</Eyebrow>
        <ResultsTitle>Sua conta</ResultsTitle>
        <p className="font-mono text-sm text-white">{user?.email ?? "—"}</p>
        <p className="mb-4 mt-7 font-mono text-xs font-semibold uppercase tracking-[0.2em] text-v3-text-soft">
          Sua produção
        </p>
        <div className="flex flex-wrap gap-4">
          <StatCard
            icon={<Film />}
            label="Vídeos processados"
            value={data?.videos_processed ?? null}
          />
          <StatCard
            icon={<Scissors />}
            label="Clipes gerados"
            value={data?.clips_generated ?? null}
          />
          <StatCard
            icon={<Star />}
            label="Nota média"
            value={data?.avg_score ?? null}
            decimals
            accent
          />
        </div>
      </V3Card>
      <V3Card className="p-7">
        <Eyebrow>Equipe</Eyebrow>
        <ResultsTitle>Equipe</ResultsTitle>
        <p className="text-sm text-v3-text-soft">Em breve: convidar membros e definir papéis.</p>
      </V3Card>
    </div>
  );
}

export default function ConfigPage() {
  return (
    <div className="relative bg-v3-bg">
      <section className="relative -mt-20 overflow-hidden border-b border-v3-border pb-8 pt-20">
        <div className="v3-home-glow" aria-hidden="true" />
        <ParticlesBackground />
        <div className="relative z-[1] mx-auto max-w-[1180px] px-7 pt-8">
          <p className="font-mono text-xs font-semibold uppercase tracking-[0.24em] text-v3-text-soft">
            Conta
          </p>
          <h1 className="mt-2 font-v3-display text-4xl font-black uppercase text-white sm:text-5xl lg:text-6xl">
            Configurações
          </h1>
        </div>
      </section>

      {/* Fora da faixa de atmosfera (glow/particulas), mesmo raciocinio de projetos/page.tsx:
          o conteudo das abas e variavel, nao pode esticar a secao do glow junto. */}
      <div className="relative z-[1] mx-auto max-w-[1180px] px-7 py-10">
        <Tabs defaultValue="prefs">
          <TabsList className={TABS_LIST_CLS}>
            <TabsTrigger value="prefs" className={TAB_TRIGGER_CLS}>
              Padrões de clipe
            </TabsTrigger>
            <TabsTrigger value="security" className={TAB_TRIGGER_CLS}>
              Segurança
            </TabsTrigger>
            <TabsTrigger value="account" className={TAB_TRIGGER_CLS}>
              Conta e equipe
            </TabsTrigger>
          </TabsList>
          <TabsContent value="prefs" className="mt-6">
            <PrefsPanel />
          </TabsContent>
          <TabsContent value="security" className="mt-6">
            <SecurityPanel />
          </TabsContent>
          <TabsContent value="account" className="mt-6">
            <AccountPanel />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
