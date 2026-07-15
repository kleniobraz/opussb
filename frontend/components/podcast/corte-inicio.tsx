"use client";

// Corte manual do início do corpo bruto do podcast, ANTES da transcrição: o vídeo pode ter
// 1 a 3 horas, então o marcador não é um slider fino de 2 pontas (como o de hook-approval.tsx,
// que trabalha numa janela de segundos); é 1 ponto só, navegado por saltos + campo de tempo,
// com o slider servindo só de navegação grosseira (onValueCommit, não onValueChange, pra não
// disparar um seek a cada pixel arrastado num arquivo de vários GB).

import { useEffect, useRef, useState } from "react";
import { Pause, Play, Scissors } from "lucide-react";
import * as api from "@/lib/api";
import { fmtHMS, parseHMS } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";

const round1 = (x: number) => Math.round(x * 10) / 10;

// Segundos ouvidos antes do ponto marcado ao apertar "play": dá contexto da transição sem
// exigir uma janela de início/fim como o editor de gancho (aqui o corte é só 1 ponta).
const PREVIEW_LOOKBACK = 2;

function SaltoBotao({ label, delta, onClick }: { label: string; delta: number; onClick: (d: number) => void }) {
  return (
    <Button type="button" variant="ghost" size="sm" onClick={() => onClick(delta)}>
      {label}
    </Button>
  );
}

export function CorteInicio({
  jobId,
  onConfirm,
  confirming,
}: {
  jobId: string;
  onConfirm: (cutAt: number) => void;
  confirming: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [dur, setDur] = useState<number | null>(null);
  const [cutAt, setCutAt] = useState(0);
  const [sliderPos, setSliderPos] = useState(0);
  const [texto, setTexto] = useState("0:00");
  const [tocando, setTocando] = useState(false);
  const [tocado, setTocado] = useState(false); // vira true assim que o usuário mexe no marcador

  // Refs pra sempre ler o valor atual dentro do atalho de teclado (registrado uma vez só,
  // ver useEffect abaixo) sem reconstruir o listener a cada mudança de estado.
  const durRef = useRef<number | null>(null);
  useEffect(() => {
    durRef.current = dur;
  }, [dur]);
  const cutAtRef = useRef(0);
  useEffect(() => {
    cutAtRef.current = cutAt;
  }, [cutAt]);

  function moverPara(alvoBruto: number) {
    const teto = durRef.current;
    const alvo = round1(Math.max(0, teto != null ? Math.min(teto, alvoBruto) : alvoBruto));
    setTocado(true);
    setCutAt(alvo);
    setSliderPos(alvo);
    setTexto(fmtHMS(alvo));
    const v = videoRef.current;
    if (v) v.currentTime = alvo;
  }

  function saltar(delta: number) {
    moverPara(cutAtRef.current + delta);
  }

  function aplicarTexto() {
    const t = parseHMS(texto);
    if (t == null) {
      setTexto(fmtHMS(cutAtRef.current)); // entrada inválida: desfaz pro último valor válido
      return;
    }
    moverPara(t);
  }

  function togglePlay() {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      v.currentTime = Math.max(0, cutAtRef.current - PREVIEW_LOOKBACK);
      v.play().catch(() => {});
    } else {
      v.pause();
    }
  }

  // Atalhos: espaço toca/pausa, setas saltam 1s (Shift 5s, Ctrl/Cmd 1min). Mesmo padrão de
  // ignorar campo de texto/dialog aberto usado no editor de clipes (clip-editor.tsx).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || el?.isContentEditable) return;
      if (el?.getAttribute?.("role") === "slider") return; // deixa o slider tratar as próprias setas
      if (e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
        e.preventDefault();
        const passo = e.ctrlKey || e.metaKey ? 60 : e.shiftKey ? 5 : 1;
        saltar(e.code === "ArrowLeft" ? -passo : passo);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const restante = dur != null ? Math.max(dur - cutAt, 0) : null;
  const semCorte = cutAt <= 0; // armadilha do 0:00: confirmar aqui não corta nada

  return (
    <div className="space-y-4">
      <video
        ref={videoRef}
        src={api.podcastSourceUrl(jobId)}
        preload="metadata"
        playsInline
        onLoadedMetadata={(e) => setDur(e.currentTarget.duration || null)}
        onPlay={() => setTocando(true)}
        onPause={() => setTocando(false)}
        className="aspect-video w-full max-w-2xl rounded-xl border border-border bg-black"
      />

      <div className="max-w-2xl space-y-3">
        <Slider
          min={0}
          max={dur ?? 0}
          step={1}
          disabled={dur == null}
          value={[sliderPos]}
          onValueChange={(v) => setSliderPos(v[0])}
          onValueCommit={(v) => moverPara(v[0])}
        />
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>0:00</span>
          <span>arraste pra chegar perto do ponto certo</span>
          <span>{dur != null ? fmtHMS(dur) : "..."}</span>
        </div>

        {/* Feedback + confirmar logo depois do slider: é a ação principal, não pode ficar
            perdida atrás dos controles secundários de ajuste fino. */}
        <p className="text-sm">
          {dur != null && restante != null
            ? `Vou cortar os primeiros ${fmtHMS(cutAt)} e manter os ${fmtHMS(restante)} restantes do episódio.`
            : "Carregando o vídeo..."}
        </p>

        <div className="space-y-1">
          <Button disabled={!tocado || confirming || semCorte} onClick={() => onConfirm(cutAt)}>
            <Scissors className="size-4" />
            {confirming ? "Cortando..." : `Confirmar corte em ${fmtHMS(cutAt)}`}
          </Button>
          {tocado && semCorte && (
            <p className="text-xs text-amber-400">
              Marque um ponto depois do início pra cortar alguma coisa.
            </p>
          )}
        </div>

        {/* Ajuste fino: secundário de propósito (variant ghost, texto pequeno), a ação
            principal é arrastar o marcador acima. */}
        <div className="space-y-2 border-t border-border/50 pt-3">
          <p className="text-xs text-muted-foreground">Ajuste fino</p>
          <div className="flex flex-wrap items-center gap-1.5">
            <SaltoBotao label="-1min" delta={-60} onClick={saltar} />
            <SaltoBotao label="-10s" delta={-10} onClick={saltar} />
            <Input
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              onBlur={aplicarTexto}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
              }}
              className="w-24 text-center"
              aria-label="Ponto de corte (H:MM:SS)"
            />
            <SaltoBotao label="+10s" delta={10} onClick={saltar} />
            <SaltoBotao label="+1min" delta={60} onClick={saltar} />
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Button type="button" variant="ghost" size="sm" onClick={togglePlay} disabled={dur == null}>
              {tocando ? <Pause className="size-4" /> : <Play className="size-4" />}
              {tocando ? "Pausar" : `Ouvir a partir de ${PREVIEW_LOOKBACK}s antes`}
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => moverPara(0)}>
              Voltar pro início
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
