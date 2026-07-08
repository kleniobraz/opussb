"use client";

// Tela de aprovação do gancho do podcast (§39 Parte 4): o gate humano. Lista os 3
// ganchos sugeridos, deixa aparar SÓ as duas pontas ouvindo o trecho (o preview toca o
// 16:9 original via /source com Range; nada é re-encodado) e grava a escolha no job.
// Deliberadamente NÃO reusa o editor de clipes 9:16: aqui é um trim de duas pontas.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Clapperboard, Download, Play, Square, Volume2 } from "lucide-react";
import * as api from "@/lib/api";
import { fmtSeconds, nameFromEmail, timeAgo } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Slider } from "@/components/ui/slider";
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

export function HookApproval({ jobId }: { jobId: string }) {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["podcastHooks", jobId],
    queryFn: () => api.podcastHooks(jobId),
    // Todo estado que roda sozinho no servidor (fila, transcrição, análise de gancho,
    // montagem) precisa de polling; só os que dependem de ação humana (uploaded,
    // aguardando_aprovacao, gancho_aprovado) ou já terminaram (done, error) ficam parados.
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      const parado = ["uploaded", "aguardando_aprovacao", "gancho_aprovado", "done", "error"];
      return s && !parado.includes(s) ? 3000 : false;
    },
  });

  const [selected, setSelected] = useState<number | null>(null);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [videoDur, setVideoDur] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [approving, setApproving] = useState(false);
  const [composing, setComposing] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
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
          <Button asChild>
            <a href={api.podcastFinalUrl(jobId, true)}>
              <Download className="size-4" /> Baixar o episódio
            </a>
          </Button>
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
  // a transcrição ainda não foi disparada. O produtor aciona quando a fila estiver livre.
  if (data.status === "uploaded") {
    return (
      <div className="view-pad">
        {cabecalho("aguardando transcrição")}
        <div className="max-w-xl space-y-3">
          <p className="text-sm text-muted-foreground">
            O episódio foi enviado e está pronto pra transcrever. Dispare quando a fila
            estiver livre; a análise de ganchos abre logo em seguida.
          </p>
          <Button disabled={transcribing} onClick={startTranscribe}>
            <Clapperboard className="size-4" />
            {transcribing ? "Disparando..." : "Iniciar transcrição"}
          </Button>
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
