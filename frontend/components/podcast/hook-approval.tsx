"use client";

// Tela de aprovação do gancho do podcast (§39 Parte 4): o gate humano. Lista os 3
// ganchos sugeridos, deixa aparar SÓ as duas pontas ouvindo o trecho (o preview toca o
// 16:9 original via /source com Range; nada é re-encodado) e grava a escolha no job.
// Deliberadamente NÃO reusa o editor de clipes 9:16: aqui é um trim de duas pontas.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Clapperboard, Download, Music, Play, Scissors, SkipForward, Square, Volume2 } from "lucide-react";
import * as api from "@/lib/api";
import { fmtHMS, fmtSeconds, nameFromEmail, timeAgo } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Slider } from "@/components/ui/slider";
import { SbdsBox } from "@/components/ui/sbds-box";
import { CorteInicio } from "@/components/podcast/corte-inicio";
import type { PodcastHook } from "@/lib/types";

// Folga pra cada lado do gancho: dá pra ESTENDER o início pra trás (o conserto típico
// de gancho que entra no meio da frase), não só cortar pra dentro.
const TRIM_MARGIN = 15;
const FIM_PREVIEW = 3; // "ouvir o fim": só os últimos segundos, sem esperar o minuto
const GAP_MIN = 1; // distância mínima entre as pontas (s)

const round1 = (x: number) => Math.round(x * 10) / 10;

// Aviso visual, não bloqueio: a faixa da casa é 30-60s; a guarda dura do backend é 10-90s.
function durTone(dur: number) {
  if (dur < 10 || dur > 90) return "text-red-400";
  if (dur < 30 || dur > 60) return "text-amber-400";
  return "text-emerald-400";
}

function PontaControle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (t: number) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground w-10">{label}</span>
      <Button variant="outline" size="sm" onClick={() => onChange(value - 0.5)}>
        -0,5s
      </Button>
      <Input
        type="number"
        step={0.1}
        className="w-24 text-center"
        value={value}
        onChange={(e) => {
          const t = parseFloat(e.target.value);
          if (!Number.isNaN(t)) onChange(t);
        }}
      />
      <Button variant="outline" size="sm" onClick={() => onChange(value + 0.5)}>
        +0,5s
      </Button>
      <span className="text-xs text-muted-foreground">{fmtSeconds(value)}</span>
    </div>
  );
}

// Tela de escolha inicial (Cortar o início / Pular), fiel ao conceito importado do Claude
// Design (SeuBoné Design System): card de vídeo com o preview real do arquivo (o conceito
// simula um timer de estúdio, aqui mostramos o frame de verdade, é o mesmo princípio já
// usado na ferramenta de corte: dado real no lugar de dramatização de protótipo), headline
// com "ponto certo" em amarelo, os dois cards de ação com ícone + descrição, e a marca
// d'água do traçado da marca sangrando pra fora da coluna de 900px (igual ao conceito:
// section inteira position:relative, coluna de conteúdo mais estreita que a página).
function EscolhaInicio({
  jobId,
  sourceAvailable,
  pulando,
  onPular,
  onCortar,
}: {
  jobId: string;
  sourceAvailable: boolean;
  pulando: boolean;
  onPular: () => void;
  onCortar: () => void;
}) {
  const [dur, setDur] = useState<number | null>(null);

  return (
    <div className="relative">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/design-system/tracado.png"
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute -right-20 -top-24 w-[430px] opacity-[0.06]"
      />
      <div className="relative max-w-[900px] space-y-6">
        <div className="flex flex-wrap items-center gap-7">
          <div className="relative aspect-video w-full flex-none overflow-hidden rounded-sbds-md border border-sbds-border-subtle bg-black sm:w-[380px]">
            {sourceAvailable && (
              <video
                src={api.podcastSourceUrl(jobId)}
                preload="metadata"
                muted
                playsInline
                onLoadedMetadata={(e) => setDur(e.currentTarget.duration || null)}
                className="size-full object-cover"
              />
            )}
            <div className="absolute left-2.5 top-2.5 rounded-sbds-pill border border-sbds-border-subtle bg-black/60 px-2.5 py-1 text-[9.5px] font-bold tracking-[0.08em] text-sbds-text-muted uppercase">
              Começo do arquivo
            </div>
            {dur != null && (
              <div className="absolute right-2.5 bottom-2.5 rounded-sbds-sm border border-sbds-border-subtle bg-black/65 px-2 py-1 text-xs font-semibold text-sbds-branco tabular-nums">
                {fmtHMS(dur)}
              </div>
            )}
          </div>
          <div className="min-w-[280px] flex-1 space-y-2.5">
            <h2 className="text-[27px] leading-[1.1] font-black tracking-[-0.02em] text-sbds-branco">
              O episódio começa no <span className="text-sbds-amarelo">ponto certo?</span>
            </h2>
            <p className="max-w-[420px] text-sm leading-[1.45] text-sbds-text-muted">
              Antes de transcrever, confira o comecinho do arquivo. Timer, vinheta ou
              silêncio antes da conversa? Corta fora e o episódio começa limpo.
            </p>
          </div>
        </div>

        <div>
          <div className="grid gap-4 sm:grid-cols-2">
            <SbdsBox
              role="button"
              tabIndex={0}
              onClick={onCortar}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onCortar();
                }
              }}
              className="flex cursor-pointer flex-col gap-2.5 border-transparent bg-sbds-amarelo text-sbds-ink-on-amarelo shadow-none transition-[filter,transform] duration-[var(--sbds-dur-fast)] ease-[var(--sbds-ease-brand)] hover:brightness-[1.08] active:scale-[0.98]"
            >
              <div className="flex size-10 items-center justify-center rounded-sbds-pill bg-black/12">
                <Scissors className="size-[19px]" />
              </div>
              <div className="text-lg font-black tracking-[-0.02em]">Cortar o início</div>
              <div className="text-[13px] leading-[1.45] text-sbds-ink-on-amarelo/70">
                Tem timer, vinheta ou conversa fiada antes do episódio começar. Marque o
                ponto certo e o resto vai fora.
              </div>
            </SbdsBox>

            <SbdsBox
              role="button"
              tabIndex={0}
              onClick={pulando ? undefined : onPular}
              onKeyDown={(e) => {
                if (!pulando && (e.key === "Enter" || e.key === " ")) {
                  e.preventDefault();
                  onPular();
                }
              }}
              aria-disabled={pulando}
              className={`flex flex-col gap-2.5 transition-[filter,transform] duration-[var(--sbds-dur-fast)] ease-[var(--sbds-ease-brand)] ${
                pulando
                  ? "pointer-events-none opacity-60"
                  : "cursor-pointer hover:brightness-[1.08] active:scale-[0.98]"
              }`}
            >
              <div className="flex size-10 items-center justify-center rounded-sbds-pill bg-white/6 text-sbds-amarelo">
                <SkipForward className="size-[18px]" />
              </div>
              <div className="text-lg font-bold tracking-[-0.02em]">
                {pulando ? "Disparando..." : "Pular, já começa certo"}
              </div>
              <div className="text-[13px] leading-[1.45] text-sbds-text-muted">
                A conversa começa direto, sem abertura pra tirar. Segue inteiro pra
                transcrição.
              </div>
            </SbdsBox>
          </div>
          <p className="mt-3.5 text-xs text-sbds-text-muted">
            Nada é apagado: as duas escolhas podem ser desfeitas antes da transcrição.
          </p>
        </div>
      </div>
    </div>
  );
}

export function HookApproval({ jobId }: { jobId: string }) {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["podcastHooks", jobId],
    queryFn: () => api.podcastHooks(jobId),
    // Todo estado que roda sozinho no servidor (fila, transcrição, análise de gancho,
    // montagem) precisa de polling; só os que dependem de ação humana (uploaded,
    // aguardando_aprovacao, gancho_aprovado) ou já terminaram (done, error) ficam parados.
    // A extração do áudio usa a MESMA query (não abre um segundo polling pro /hooks):
    // enquanto audio_status for 'extraindo', mantém o intervalo ligado mesmo com o job
    // principal já parado (ex.: episódio 'done' esperando só o MP3 terminar).
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      const parado = ["uploaded", "aguardando_aprovacao", "gancho_aprovado", "done", "error"];
      if (q.state.data?.audio_status === "extraindo") return 3000;
      return s && !parado.includes(s) ? 3000 : false;
    },
  });

  const [selected, setSelected] = useState<number | null>(null);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [videoDur, setVideoDur] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [approving, setApproving] = useState(false);
  const [composing, setComposing] = useState(false);
  const [extractingAudio, setExtractingAudio] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [cutting, setCutting] = useState(false);
  // Compartilhado pelos dois branches que oferecem o corte do início (uploaded e
  // error-retry): só um deles renderiza por vez, então um estado só basta. Começa
  // fechado (opção B): a ferramenta de corte só aparece quando o produtor escolhe cortar.
  const [cortando, setCortando] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const stopAtRef = useRef<number | null>(null);
  const rafRef = useRef<number>(0);

  const hook: PodcastHook | null = selected != null ? (data?.hooks[selected] ?? null) : null;

  // Janela do trim: gancho ±15s, clampada ao vídeo quando a duração real é conhecida.
  const win = useMemo(() => {
    if (!hook) return null;
    return {
      min: Math.max(0, hook.start - TRIM_MARGIN),
      max: videoDur != null ? Math.min(videoDur, hook.end + TRIM_MARGIN) : hook.end + TRIM_MARGIN,
    };
  }, [hook, videoDur]);

  function stop() {
    cancelAnimationFrame(rafRef.current);
    stopAtRef.current = null;
    videoRef.current?.pause();
    setPlaying(false);
  }

  function select(i: number) {
    if (!data) return;
    stop();
    setSelected(i);
    const h = data.hooks[i];
    setRange([h.start, h.end]);
    const v = videoRef.current;
    if (v) v.currentTime = h.start;
  }

  // Para EXATO na ponta escolhida: rAF em vez do timeupdate (que dispara ~4x/s e
  // estouraria a fronteira em até 250ms, justamente o que esta tela existe pra conferir).
  function playFrom(t0: number, t1: number) {
    const v = videoRef.current;
    if (!v) return;
    cancelAnimationFrame(rafRef.current);
    v.currentTime = t0;
    stopAtRef.current = t1;
    v.play()
      .then(() => {
        setPlaying(true);
        const tick = () => {
          const alvo = stopAtRef.current;
          if (alvo == null) return;
          if (v.currentTime >= alvo || v.ended) {
            v.pause();
            setPlaying(false);
            stopAtRef.current = null;
            return;
          }
          rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);
      })
      .catch(() => setPlaying(false));
  }

  useEffect(() => () => cancelAnimationFrame(rafRef.current), []);

  function setStart(t: number) {
    if (!win) return;
    setRange(([, e]) => [round1(Math.min(Math.max(t, win.min), e - GAP_MIN)), e]);
  }
  function setEnd(t: number) {
    if (!win) return;
    setRange(([s]) => [s, round1(Math.max(Math.min(t, win.max), s + GAP_MIN))]);
  }

  async function compose() {
    setComposing(true);
    try {
      await api.podcastCompose(jobId);
      toast.success("Montagem iniciada.");
      qc.invalidateQueries({ queryKey: ["podcastHooks", jobId] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    } catch (e) {
      toast.error(e instanceof api.ApiError ? e.message : "Falha ao iniciar a montagem.");
    } finally {
      setComposing(false);
    }
  }

  // Dispara a extração do MP3 (idempotente no servidor). O acompanhamento é pelo
  // audio_status que já vem no polling do /hooks, sem query nova.
  async function extractAudio() {
    setExtractingAudio(true);
    try {
      await api.podcastAudioExtract(jobId);
      toast.success("Extração do áudio iniciada.");
      qc.invalidateQueries({ queryKey: ["podcastHooks", jobId] });
    } catch (e) {
      toast.error(e instanceof api.ApiError ? e.message : "Falha ao iniciar a extração do áudio.");
    } finally {
      setExtractingAudio(false);
    }
  }

  async function startTranscribe() {
    setTranscribing(true);
    try {
      await api.podcastTranscribe(jobId);
      toast.success("Transcrição iniciada.");
      qc.invalidateQueries({ queryKey: ["podcastHooks", jobId] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    } catch (e) {
      toast.error(e instanceof api.ApiError ? e.message : "Falha ao iniciar a transcrição.");
    } finally {
      setTranscribing(false);
    }
  }

  // Corte manual do início do corpo bruto (ANTES da transcrição): dispara o corte no
  // servidor (202) e o job vai pra 'cortando_inicio'; o polling do refetchInterval
  // acompanha até voltar pra 'uploaded' (sucesso) ou virar 'error' (falha).
  async function confirmCut(cutAt: number) {
    setCutting(true);
    try {
      await api.podcastCutIntro(jobId, cutAt);
      toast.success("Corte do início iniciado.");
      qc.invalidateQueries({ queryKey: ["podcastHooks", jobId] });
    } catch (e) {
      toast.error(e instanceof api.ApiError ? e.message : "Falha ao iniciar o corte do início.");
    } finally {
      setCutting(false);
    }
  }

  async function approve() {
    if (selected == null) return;
    setApproving(true);
    try {
      await api.podcastApprove(jobId, {
        index: selected,
        start: round1(range[0]),
        end: round1(range[1]),
      });
      toast.success("Gancho aprovado. A montagem (próxima etapa) parte dessa escolha.");
      stop();
      qc.invalidateQueries({ queryKey: ["podcastHooks", jobId] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    } catch (e) {
      toast.error(e instanceof api.ApiError ? e.message : "Falha ao aprovar o gancho.");
    } finally {
      setApproving(false);
    }
  }

  if (isLoading) {
    return (
      <div className="view-pad">
        <div className="skel-grid">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skel skel-card" />
          ))}
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="view-pad">
        <Link href="/projetos" className="back-link">
          ← Projetos
        </Link>
        <div className="empty">
          <p className="empty-title">Podcast não encontrado</p>
        </div>
      </div>
    );
  }

  const cabecalho = (rotulo: string) => (
    <>
      <Link href="/projetos" className="back-link">
        ← Projetos
      </Link>
      <div className="section-head">
        <div>
          <p className="eyebrow">Podcast · {rotulo}</p>
          <h1 className="section-title">{data.filename || "Episódio"}</h1>
        </div>
      </div>
    </>
  );

  // Episódio montado: player do final + download. Fim da linha do pipeline.
  if (data.status === "done" && data.final_available) {
    const c = data.hook_choice;
    return (
      <div className="view-pad">
        {cabecalho("episódio montado")}
        <div className="max-w-2xl space-y-3">
          <video
            controls
            preload="metadata"
            src={api.podcastFinalUrl(jobId)}
            className="aspect-video w-full rounded-xl border border-border bg-black"
          />
          {c && (
            <p className="text-sm text-muted-foreground">
              Abertura: gancho {c.index + 1}, {fmtSeconds(c.start)} a {fmtSeconds(c.end)} (
              {round1(c.end - c.start)}s), seguido da vinheta e do episódio completo.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button asChild>
              <a href={api.podcastFinalUrl(jobId, true)}>
                <Download className="size-4" /> Baixar o episódio
              </a>
            </Button>

            {data.audio_status === "extraindo" ? (
              <div className="w-48 space-y-1.5">
                <Progress value={50} />
                <p className="text-xs text-muted-foreground">Extraindo áudio...</p>
              </div>
            ) : data.audio_status === "done" ? (
              <Button asChild variant="outline">
                <a href={api.podcastAudioUrl(jobId, true)}>
                  <Music className="size-4" /> Baixar áudio (MP3)
                </a>
              </Button>
            ) : (
              <div className="space-y-1">
                {data.audio_status === "error" && (
                  <p className="text-xs text-red-400">Falha ao extrair o áudio.</p>
                )}
                <Button variant="outline" disabled={extractingAudio} onClick={extractAudio}>
                  <Music className="size-4" />
                  {extractingAudio
                    ? "Iniciando..."
                    : data.audio_status === "error"
                      ? "Tentar de novo"
                      : "Baixar áudio (MP3)"}
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Montagem rodando no servidor (polling do refetchInterval acompanha).
  if (data.status === "montando") {
    return (
      <div className="view-pad">
        {cabecalho("montando")}
        <div className="max-w-xl space-y-3">
          <Progress value={data.progress ?? 10} />
          <p className="text-sm text-muted-foreground">
            Montando o episódio (gancho + vinheta + podcast completo). O corpo não é
            re-encodado, então isso leva poucos minutos.
          </p>
        </div>
      </div>
    );
  }

  // Corte do início rodando no servidor (polling do refetchInterval acompanha).
  if (data.status === "cortando_inicio") {
    return (
      <div className="view-pad">
        {cabecalho("cortando o início")}
        <div className="max-w-xl space-y-3">
          <Progress value={data.progress ?? 10} />
          <p className="text-sm text-muted-foreground">
            Cortando o início do episódio. Só o pedacinho até o próximo corte possível é
            reencodado; o resto do corpo é copiado, então isso deve ser rápido.
          </p>
        </div>
      </div>
    );
  }

  // Montagem falhou mas a escolha do gancho persiste: mostra o erro e deixa re-tentar.
  if (data.status === "error" && data.hook_choice) {
    return (
      <div className="view-pad">
        {cabecalho("montagem falhou")}
        <div className="max-w-xl space-y-3">
          <p className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-400">
            {data.error || "A montagem falhou."}
          </p>
          <Button disabled={composing} onClick={compose}>
            <Clapperboard className="size-4" />
            {composing ? "Disparando..." : "Tentar a montagem de novo"}
          </Button>
        </div>
      </div>
    );
  }

  // O corte do início falhou (distingue de "falhou antes da aprovação" abaixo por ter
  // intro_cut_at gravado e NÃO ter hook_choice): o arquivo original está intacto, então
  // dá pra marcar o corte de novo ou pular direto pra transcrição, sem reenviar o vídeo.
  if (data.status === "error" && data.intro_cut_at != null && !data.hook_choice) {
    return (
      <div className="view-pad">
        {cabecalho("corte de início falhou")}
        {/* mesma largura de .view-pad-narrow (1180px), igual ao wrapper do CorteInicio */}
        <div className="mx-auto max-w-[1180px] space-y-4">
          <p className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-400">
            O corte do início falhou: {data.error || "erro desconhecido"}. O vídeo original
            continua íntegro no servidor.
          </p>

          {!cortando ? (
            <>
              <p className="text-sm text-muted-foreground">
                O ponto marcado pode ter caído perto de um trecho sem keyframe. Marque de
                novo um pouco antes ou depois, ou pule sem cortar.
              </p>

              <EscolhaInicio
                jobId={jobId}
                sourceAvailable={data.source_available}
                pulando={transcribing}
                onPular={startTranscribe}
                onCortar={() => setCortando(true)}
              />
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" onClick={() => setCortando(false)}>
                ← Escolher outra opção
              </Button>

              {data.source_available ? (
                <CorteInicio jobId={jobId} onConfirm={confirmCut} confirming={cutting} fps={data.fps} />
              ) : (
                <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-400">
                  O vídeo original não está mais no servidor; não dá pra marcar o corte de
                  novo. Envie o episódio de novo.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    );
  }

  // Falhou antes da aprovação (transcrição ou análise de gancho): a rota de disparo só
  // aceita job em 'uploaded', então não dá pra re-tentar este mesmo job; orienta a reenviar.
  if (data.status === "error" && !data.hook_choice) {
    return (
      <div className="view-pad">
        {cabecalho("falhou")}
        <div className="max-w-xl space-y-3">
          <p className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-400">
            {data.error || "O processamento falhou antes de gerar os ganchos."}
          </p>
          <p className="text-sm text-muted-foreground">
            Não dá pra re-tentar este envio; envie o episódio de novo pra tentar outra vez.
          </p>
          <Button asChild variant="outline">
            <Link href="/podcast">Enviar de novo</Link>
          </Button>
        </div>
      </div>
    );
  }

  // Já aprovado: resumo da escolha + disparo manual da montagem (padrão do /transcribe:
  // trabalho pronto não toma 429; o produtor aciona com a fila livre).
  if (data.status === "gancho_aprovado" && data.hook_choice) {
    const c = data.hook_choice;
    return (
      <div className="view-pad">
        {cabecalho("aprovado")}
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-5 max-w-xl">
          <p className="flex items-center gap-2 font-medium text-emerald-400">
            <Check className="size-4" /> Gancho {c.index + 1} aprovado
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            {fmtSeconds(c.start)} a {fmtSeconds(c.end)} ({round1(c.end - c.start)}s)
            {c.approved_by ? ` · por ${nameFromEmail(c.approved_by)}` : ""}
            {c.approved_at ? ` · ${timeAgo(c.approved_at)}` : ""}
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            Falta montar: gancho + vinheta + episódio completo, sem re-encodar o corpo.
          </p>
          <Button className="mt-4" disabled={composing || !data.source_available} onClick={compose}>
            <Clapperboard className="size-4" />
            {composing ? "Disparando..." : "Montar episódio"}
          </Button>
          {!data.source_available && (
            <p className="mt-2 text-xs text-amber-400">
              O vídeo original não está mais no servidor; envie o episódio de novo pra montar.
            </p>
          )}
        </div>
      </div>
    );
  }

  // Job estacionado: o vídeo subiu e passou na validação (16:9 + frame rate constante), mas
  // a transcrição ainda não foi disparada. O produtor decide aqui se o episódio precisa de
  // um corte manual do início (timer, "em instantes", vinheta do estúdio de origem etc.,
  // de duração variável) antes de transcrever; é OPCIONAL, então "pular" fica sempre visível
  // e não depende de mexer no vídeo.
  if (data.status === "uploaded") {
    return (
      <div className="view-pad">
        {cabecalho("aguardando transcrição")}
        {/* mesma largura de .view-pad-narrow (1180px), igual ao wrapper do CorteInicio */}
        <div className="mx-auto max-w-[1180px] space-y-4">
          {!cortando ? (
            <>
              {data.intro_cut_at != null && (
                <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm text-emerald-400">
                  Já foram cortados {fmtHMS(data.intro_cut_at)} do início deste episódio. Dá
                  pra cortar de novo se precisar ajustar, ou seguir direto pra transcrição.
                </p>
              )}

              <EscolhaInicio
                jobId={jobId}
                sourceAvailable={data.source_available}
                pulando={transcribing}
                onPular={startTranscribe}
                onCortar={() => setCortando(true)}
              />
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" onClick={() => setCortando(false)}>
                ← Escolher outra opção
              </Button>

              {data.source_available ? (
                <CorteInicio jobId={jobId} onConfirm={confirmCut} confirming={cutting} fps={data.fps} />
              ) : (
                <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-400">
                  O vídeo original não está mais no servidor; não dá pra marcar o corte. Pule
                  direto pra transcrição ou envie o episódio de novo.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    );
  }

  if (data.status !== "aguardando_aprovacao" || data.hooks.length === 0) {
    return (
      <div className="view-pad">
        <Link href="/projetos" className="back-link">
          ← Projetos
        </Link>
        <div className="empty">
          <p className="empty-title">Sem ganchos pra aprovar</p>
          <p className="text-sm text-muted-foreground">
            Este podcast está em &quot;{data.status}&quot;. A tela de aprovação abre quando a
            análise de gancho termina.
          </p>
        </div>
      </div>
    );
  }

  const dur = round1(range[1] - range[0]);

  return (
    <div className="view-pad">
      <Link href="/projetos" className="back-link">
        ← Projetos
      </Link>
      <div className="section-head">
        <div>
          <p className="eyebrow">Podcast · aprovação do gancho</p>
          <h1 className="section-title">{data.filename || "Episódio"}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Escolha um dos ganchos, ouça o trecho, apare as pontas se precisar e aprove. O
            gancho abre o episódio antes da vinheta.
          </p>
        </div>
      </div>

      {!data.source_available && (
        <p className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-400">
          O vídeo original não está mais no servidor; o preview e a aprovação estão
          indisponíveis. Envie o episódio de novo.
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        <div className="space-y-3">
          {data.hooks.map((h, i) => (
            <button
              key={i}
              type="button"
              onClick={() => select(i)}
              className={`w-full rounded-xl border p-4 text-left transition-colors ${
                selected === i
                  ? "border-primary bg-primary/5"
                  : "border-border hover:border-muted-foreground/40"
              }`}
            >
              <p className="flex items-center justify-between font-medium">
                <span>Gancho {i + 1}</span>
                <span className="text-xs text-muted-foreground">
                  {fmtSeconds(h.start)} a {fmtSeconds(h.end)} · {round1(h.end - h.start)}s
                </span>
              </p>
              <p className="mt-1 text-xs italic text-muted-foreground">{h.reason}</p>
              <p className="mt-2 text-sm text-muted-foreground">{h.text}</p>
            </button>
          ))}
        </div>

        <div>
          {hook && win ? (
            <div className="space-y-4">
              {data.source_available && (
                <video
                  ref={videoRef}
                  src={api.podcastSourceUrl(jobId)}
                  preload="metadata"
                  playsInline
                  onClick={stop}
                  onLoadedMetadata={(e) => setVideoDur(e.currentTarget.duration || null)}
                  className="aspect-video w-full max-w-2xl rounded-xl border border-border bg-black"
                />
              )}

              <div className="max-w-2xl space-y-3">
                <Slider
                  min={win.min}
                  max={win.max}
                  step={0.1}
                  minStepsBetweenThumbs={GAP_MIN * 10}
                  value={range}
                  onValueChange={(v) => setRange([round1(v[0]), round1(v[1])])}
                />
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>{fmtSeconds(win.min)}</span>
                  <span>
                    janela de ajuste: gancho ±{TRIM_MARGIN}s (dá pra estender, não só cortar)
                  </span>
                  <span>{fmtSeconds(win.max)}</span>
                </div>

                <PontaControle label="Início" value={range[0]} onChange={setStart} />
                <PontaControle label="Fim" value={range[1]} onChange={setEnd} />

                <p className="text-sm">
                  Duração: <span className={`font-medium ${durTone(dur)}`}>{dur}s</span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    (a casa usa 30 a 60s; fora de 10 a 90s não dá pra aprovar)
                  </span>
                </p>

                <div className="flex flex-wrap items-center gap-2">
                  {playing ? (
                    <Button variant="outline" onClick={stop}>
                      <Square className="size-4" /> Parar
                    </Button>
                  ) : (
                    <>
                      <Button
                        variant="outline"
                        disabled={!data.source_available}
                        onClick={() => playFrom(range[0], range[1])}
                      >
                        <Play className="size-4" /> Ouvir o trecho
                      </Button>
                      <Button
                        variant="outline"
                        disabled={!data.source_available}
                        onClick={() => playFrom(Math.max(range[0], range[1] - FIM_PREVIEW), range[1])}
                      >
                        <Volume2 className="size-4" /> Ouvir o fim ({FIM_PREVIEW}s)
                      </Button>
                    </>
                  )}
                  <Button
                    disabled={approving || !data.source_available || dur < 10 || dur > 90}
                    onClick={approve}
                  >
                    <Check className="size-4" />
                    {approving ? "Aprovando..." : `Aprovar gancho ${selected! + 1}`}
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <div className="empty">
              <p className="empty-title">Escolha um gancho ao lado</p>
              <p className="text-sm text-muted-foreground">
                O preview toca só o trecho escolhido, com áudio, direto do vídeo original.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
