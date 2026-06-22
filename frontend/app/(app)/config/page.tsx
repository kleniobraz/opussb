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
import type { Session } from "@/lib/types";

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
    <section className="card-block">
      <p className="eyebrow">Preferências de geração</p>
      <h2 className="results-title">Padrões de clipe</h2>
      <div className="flex gap-3.5">
        <div className="flex-1">
          <Label className="ctrl-label" htmlFor="cfg-min">
            Duração mínima (s)
          </Label>
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
          <Label className="ctrl-label" htmlFor="cfg-max">
            Duração máxima (s)
          </Label>
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
        <Label className="ctrl-label">Formato padrão</Label>
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
        <Label className="ctrl-label">Idioma da transcrição</Label>
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
      <label className="mt-4 flex cursor-pointer items-center gap-3 text-[0.9rem] text-ink-soft">
        <Switch checked={captions} onCheckedChange={setCaptions} />
        <span>Gerar legendas por padrão</span>
      </label>
      <label className="mt-3 flex cursor-pointer items-center gap-3 text-[0.9rem] text-ink-soft">
        <Switch checked={autoplay} onCheckedChange={setAutoplay} />
        <span>Tocar prévia automaticamente</span>
      </label>
      <div>
        <Button
          variant="gold"
          size="brand"
          className="mt-6"
          onClick={onSave}
          disabled={save.isPending}
        >
          Salvar preferências
        </Button>
      </div>
    </section>
  );
}

function SessionRow({ s, onRevoke }: { s: Session; onRevoke: () => void }) {
  const mobile = /iOS|Android/i.test(s.os || "");
  const meta = [s.location, s.last_active].filter(Boolean).join(" · ");
  return (
    <div className="session-row">
      <span className="session-ic" aria-hidden="true">
        {mobile ? <Smartphone /> : <Monitor />}
      </span>
      <div className="session-info">
        <p className="session-dev">
          {(s.browser || "Navegador") + (s.os ? " · " + s.os : "")}
        </p>
        {meta && <p className="session-meta">{meta}</p>}
      </div>
      {s.current ? (
        <span className="session-current">esta sessão</span>
      ) : (
        <Button variant="brandGhost" size="sm" onClick={onRevoke}>
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
    <div className="config-cards">
      <section className="card-block">
        <p className="eyebrow">Segurança</p>
        <h2 className="results-title">Trocar senha</h2>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="grid gap-2">
            <Label htmlFor="cfg-cur">Senha atual</Label>
            <Input
              id="cfg-cur"
              type="password"
              autoComplete="current-password"
              value={cur}
              onChange={(e) => setCur(e.target.value)}
              required
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="cfg-new">Nova senha (mín. 8)</Label>
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
          <div className="grid gap-2">
            <Label htmlFor="cfg-new2">Repita a nova senha</Label>
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
          <Button type="submit" variant="gold" size="brand" className="w-full" disabled={saving}>
            {saving ? "Salvando…" : "Salvar nova senha"}
          </Button>
          {err && <div className="error">{err}</div>}
        </form>
      </section>

      <section className="card-block">
        <p className="eyebrow">Segurança</p>
        <h2 className="results-title">Sessões ativas</h2>
        <p className="modal-text">
          Onde sua conta está conectada. Encerre qualquer sessão que você não reconhece.
        </p>
        <div className="sessions">
          {sessions.isLoading ? (
            <div className="skel" style={{ height: 56 }} />
          ) : sessions.isError ? (
            <p className="editor-hint">Não consegui carregar as sessões agora.</p>
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
          variant="brandGhost"
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
      </section>
    </div>
  );
}

function AccountPanel() {
  const { user } = useAuth();
  const { data } = useQuery({ queryKey: ["stats"], queryFn: api.getStats });
  return (
    <div className="config-cards">
      <section className="card-block">
        <p className="eyebrow">Conta</p>
        <h2 className="results-title">Sua conta</h2>
        <p className="account-email">{user?.email ?? "—"}</p>
        <p className="eyebrow account-prod-eyebrow">Sua produção</p>
        <div className="stats-grid">
          <StatCard
            icon={<Film className="ic" />}
            label="Vídeos processados"
            value={data?.videos_processed ?? null}
          />
          <StatCard
            icon={<Scissors className="ic" />}
            label="Clipes gerados"
            value={data?.clips_generated ?? null}
          />
          <StatCard
            icon={<Star className="ic" />}
            label="Nota média"
            value={data?.avg_score ?? null}
            decimals
            accent
          />
        </div>
      </section>
      <section className="card-block">
        <p className="eyebrow">Equipe</p>
        <h2 className="results-title">Equipe</h2>
        <p className="modal-text">Em breve: convidar membros e definir papéis.</p>
      </section>
    </div>
  );
}

export default function ConfigPage() {
  return (
    <>
      <section className="page-hero">
        <div className="page-hero-inner">
          <p className="eyebrow">Conta</p>
          <h1 className="page-title">Configurações</h1>
        </div>
      </section>
      <div className="view-pad-narrow">
        <Tabs defaultValue="prefs">
          <TabsList className="mb-6">
            <TabsTrigger value="prefs">Padrões de clipe</TabsTrigger>
            <TabsTrigger value="security">Segurança</TabsTrigger>
            <TabsTrigger value="account">Conta e equipe</TabsTrigger>
          </TabsList>
          <TabsContent value="prefs">
            <PrefsPanel />
          </TabsContent>
          <TabsContent value="security">
            <SecurityPanel />
          </TabsContent>
          <TabsContent value="account">
            <AccountPanel />
          </TabsContent>
        </Tabs>
      </div>
    </>
  );
}
