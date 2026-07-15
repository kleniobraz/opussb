"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Download, SlidersHorizontal, TriangleAlert } from "lucide-react";
import * as api from "@/lib/api";
import { useFilmstrip } from "@/hooks/use-filmstrip";
import { useWaveform } from "@/hooks/use-waveform";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { PreviewStage } from "./preview-stage";
import { CropStage } from "./crop-stage";
import { TranscriptPanel } from "./transcript-panel";
import { EditorTimeline, ZOOM_MIN, ZOOM_MAX } from "./editor-timeline";
import type { CropKeyframe, EndScreenOption, Music, Segment } from "@/lib/types";
import { computeSilenceGaps } from "@/lib/silence";

// Segmento como vive no estado do editor: o {start,end,text} do contrato + um id de
// cliente NAO persistido. O id deixa a selecao, o karaoke e o split/merge estaveis mesmo
// quando a lista se reordena por tempo; ele e removido antes do PUT (save monta um array
// limpo {start,end,text}). A ordem do array e cosmetica pro backend (cada segmento vira um
// evento .ass com seu proprio tempo), entao ordenar/inserir em ordem/dividir/juntar e seguro.
export type EditSeg = { _id: string } & Segment;

let __segCounter = 0;
function genId() {
  return "s" + ++__segCounter;
}

// Ordena por inicio (desempate pelo fim). Usado pra renderizar a lista e os blocos sempre
// em ordem cronologica, e pra achar vizinhos no juntar.
const byStart = (a: EditSeg, b: EditSeg) => a.start - b.start || a.end - b.end;

// Acha o espaco mais proximo de `idx` (dentro de ~16 chars) pra cortar o texto numa fronteira
// de palavra ao dividir pelo playhead. No split pelo cursor do texto respeitamos o caret cru.
function nearestSpace(text: string, idx: number): number {
  const max = text.length;
  if (idx <= 0 || idx >= max) return idx;
  let left = -1;
  let right = -1;
  for (let i = idx; i >= 0 && idx - i <= 16; i--) {
    if (text[i] === " ") {
      left = i;
      break;
    }
  }
  for (let i = idx; i < max && i - idx <= 16; i++) {
    if (text[i] === " ") {
      right = i;
      break;
    }
  }
  if (left < 0 && right < 0) return idx;
  if (left < 0) return right;
  if (right < 0) return left;
  return idx - left <= right - idx ? left : right;
}

function MusicControl({
  music,
  volume,
  onUpload,
  onRemove,
  onVolume,
}: {
  music: Music | null;
  volume: number;
  onUpload: (f: File) => void;
  onRemove: () => void;
  onVolume: (v: number) => void;
}) {
  return (
    <div className="mt-3 border-t border-[var(--border)] pt-3">
      <div className="editor-sec-head">
        <strong className="text-[0.85rem]">Música de fundo</strong>
      </div>
      {music?.filename ? (
        <>
          <div className="editor-music-has">
            <span className="editor-music-name">🎵 {music.filename}</span>
            <Button variant="brandGhost" size="sm" onClick={onRemove}>
              Remover
            </Button>
          </div>
          <label className="editor-vol">
            Volume
            <Slider
              min={0}
              max={100}
              value={[volume]}
              onValueChange={(v) => onVolume(v[0])}
              className="flex-1"
            />
          </label>
        </>
      ) : (
        <label className="editor-music-add">
          + Adicionar música
          <input
            type="file"
            accept="audio/*,.mp3,.m4a,.aac,.wav,.ogg"
            className="sr-only"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUpload(f);
            }}
          />
        </label>
      )}
    </div>
  );
}

// Posição vertical padrão da legenda (fração do centro, do topo). Aproxima o padrão
// do estilo SB no backend (alignment 2 + MARGIN_V 580 de 1920 => legenda na parte baixa).
const SB_DEFAULT_Y = 0.67;

// ── Crop por keyframe (enquadramento) ──────────────────────────────────────
// Interpola o centro do recorte (cx, fração 0..1) num instante t a partir dos keyframes esparsos
// (linear entre eles; segura as pontas). Espelha o trajectory_from_keyframes do backend, em fração.
function cxAt(kf: CropKeyframe[], t: number): number {
  if (!kf.length) return 0.5;
  const s = [...kf].sort((a, b) => a.t - b.t);
  if (t <= s[0].t) return s[0].cx;
  if (t >= s[s.length - 1].t) return s[s.length - 1].cx;
  for (let i = 0; i < s.length - 1; i++) {
    const a = s[i];
    const b = s[i + 1];
    if (t >= a.t && t <= b.t) {
      const f = b.t === a.t ? 0 : (t - a.t) / (b.t - a.t);
      return a.cx + (b.cx - a.cx) * f;
    }
  }
  return s[s.length - 1].cx;
}
// Insere/atualiza um keyframe no tempo t (substitui o que estiver a < EPS s). Lista nova ordenada.
const KF_MERGE_EPS = 0.08;
function upsertKf(kf: CropKeyframe[], t: number, cx: number): CropKeyframe[] {
  const out = kf.filter((k) => Math.abs(k.t - t) > KF_MERGE_EPS);
  out.push({
    t: Math.max(0, Math.round(t * 1000) / 1000),
    cx: Math.max(0, Math.min(1, Math.round(cx * 1e4) / 1e4)),
  });
  return out.sort((a, b) => a.t - b.t);
}

// Estilo padrão SB da fonte (espelha as constantes do captions.py). No backend, null = isto.
const SB_STYLE = { size: 70, tracking: -3, color: "#FFFFFF", shadow: 0.86 };
type CapStyle = typeof SB_STYLE;

// Personalização básica da fonte. Reflete AO VIVO no preview e é queimada no Exportar/play.
function StyleControl({
  value,
  changed,
  onChange,
  onReset,
}: {
  value: CapStyle;
  changed: boolean;
  onChange: (patch: Partial<CapStyle>) => void;
  onReset: () => void;
}) {
  return (
    <div className="editor-style">
      <div className="editor-sec-head">
        <strong className="text-[0.85rem]">Estilo da legenda</strong>
        {changed && (
          <button
            type="button"
            className="text-gold text-[0.78rem] underline-offset-2 hover:underline"
            onClick={onReset}
          >
            Resetar estilo
          </button>
        )}
      </div>
      <label className="editor-style-row">
        <span>Tamanho</span>
        <Slider
          min={40}
          max={110}
          step={1}
          value={[value.size]}
          onValueChange={(v) => onChange({ size: v[0] })}
          className="flex-1"
        />
        <span className="editor-style-val">{value.size}</span>
      </label>
      <label className="editor-style-row">
        <span>Espaçamento</span>
        <Slider
          min={-8}
          max={14}
          step={1}
          value={[value.tracking]}
          onValueChange={(v) => onChange({ tracking: v[0] })}
          className="flex-1"
        />
        <span className="editor-style-val">{value.tracking}</span>
      </label>
      <label className="editor-style-row">
        <span>Sombra</span>
        <Slider
          min={0}
          max={100}
          step={1}
          value={[Math.round(value.shadow * 100)]}
          onValueChange={(v) => onChange({ shadow: v[0] / 100 })}
          className="flex-1"
        />
        <span className="editor-style-val">{Math.round(value.shadow * 100)}%</span>
      </label>
      <label className="editor-style-row">
        <span>Cor</span>
        <input
          type="color"
          className="editor-color-input"
          value={value.color.toLowerCase()}
          onChange={(e) => onChange({ color: e.target.value.toUpperCase() })}
          aria-label="Cor da legenda"
        />
        <span className="editor-style-val" style={{ width: "auto" }}>
          {value.color.toUpperCase()}
        </span>
      </label>
    </div>
  );
}

// 4a camada do clipe: a tela final (outro) anexada ~5s no fim. Estado efetivo herda o default do
// projeto; aqui o usuario liga/desliga POR CLIPE (override). Acao imediata (rota dedicada, nao
// re-queima a legenda); refaz so o anexo no proximo play.
function EndScreenControl({
  options,
  effectiveId,
  override,
  projectDefault,
  busy,
  onChange,
}: {
  options: EndScreenOption[];
  effectiveId: string | null; // o que sera anexado (id) ou null
  override: string | null; // null = herda | "none" = removida | "<id>" = forcada
  projectDefault: string | null;
  busy: boolean;
  onChange: (value: string | null) => void;
}) {
  const labelOf = (id: string | null) =>
    options.find((o) => o.id === id)?.label ?? id ?? "tela final";
  const on = effectiveId != null;
  const hasLibrary = options.length > 0;
  const toggle = () => {
    if (on) onChange(projectDefault ? "none" : null); // remove neste clipe
    else onChange(projectDefault ? null : options[0]?.id ?? null); // herda ou forca a 1a
  };
  return (
    <div className="editor-style">
      <div className="editor-sec-head">
        <strong className="text-[0.85rem]">Tela final</strong>
        <button
          type="button"
          className={cn("chip !rounded-full !border-0 !px-3 !py-1", on && "is-active")}
          onClick={toggle}
          disabled={busy || (!on && !hasLibrary)}
        >
          {busy ? "…" : on ? "Remover" : "Adicionar"}
        </button>
      </div>
      <p className="editor-endscreen-note">
        {on
          ? `${labelOf(effectiveId)} entra ~5s no fim` +
            (override && override !== "none"
              ? " (definida neste clipe)."
              : projectDefault
                ? " (padrão do projeto)."
                : ".")
          : override === "none" && projectDefault
            ? `Removida neste clipe (o projeto usa ${labelOf(projectDefault)}).`
            : hasLibrary
              ? "Sem tela final neste clipe."
              : "Nenhuma tela final disponível."}
      </p>
    </div>
  );
}

export function ClipEditor({ clipId, title }: { clipId: string; title?: string }) {
  const qc = useQueryClient();
  const router = useRouter();
  const enc = encodeURIComponent(clipId);
  const baseSrc = `/api/clips/${enc}/base`;
  const burnedSrc = `/api/clips/${enc}/video`;
  const jobId = clipId.slice(0, clipId.lastIndexOf(":")) || clipId;
  const projectHref = `/projetos/${jobId}`;

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["captions", clipId],
    queryFn: () => api.getCaptions(clipId),
    enabled: !!clipId,
  });

  const [segments, setSegments] = useState<EditSeg[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false); // painel lateral "Ajustes" (musica/estilo/tela final)
  // Ref pro save() mais recente: o efeito de atalhos tem deps vazias; chamar save direto la
  // pegaria uma closure velha (salvaria estado obsoleto). O efeito abaixo mantem a ref atual.
  const saveRef = useRef<(() => void) | null>(null);
  // Layout: previa sozinha a direita; a esquerda empilha roteiro (cima) + timeline (baixo) com
  // divisoria arrastavel. timelineH = altura da timeline (px), persistida em localStorage.
  const leftRef = useRef<HTMLDivElement>(null);
  const [timelineH, setTimelineH] = useState(380);
  // <audio> paralelo: toca a musica no preview AO VIVO, sincronizada com o video (sem re-queimar).
  const musicAudioRef = useRef<HTMLAudioElement>(null);
  const [music, setMusic] = useState<Music | null>(null);
  const [volume, setVolume] = useState(15);
  const [captionsOn, setCaptionsOn] = useState(true);
  const [captionY, setCaptionY] = useState(SB_DEFAULT_Y);
  const [captionMoved, setCaptionMoved] = useState(false);
  const [capStyle, setCapStyle] = useState<CapStyle>(SB_STYLE);
  const [styleChanged, setStyleChanged] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [usingBurned, setUsingBurned] = useState(false);
  // Destino pendente quando o usuario tenta sair com alteracoes nao salvas (null = sem dialogo).
  const [leaveTo, setLeaveTo] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [pxPerSec, setPxPerSec] = useState(14);
  // Corte (handles): a janela do base tem sobra antes/depois; o corte oficial fica em
  // [cutIn, cutOut] (s, relativos à janela). hasHandles=false em clipe legado (sem sobra).
  const [hasHandles, setHasHandles] = useState(false);
  const [cutIn, setCutIn] = useState(0);
  const [cutOut, setCutOut] = useState(0);
  // Tela final (outro) por clipe: override (null=herda, "none"=removida, id=forçada), o efetivo
  // (id ou null), o default do projeto, e a biblioteca de opções.
  const [endOverride, setEndOverride] = useState<string | null>(null);
  const [endEffective, setEndEffective] = useState<string | null>(null);
  const [endDur, setEndDur] = useState<number | null>(null); // duração da tela neste clipe; null = inteira
  const [projectEnd, setProjectEnd] = useState<string | null>(null);
  const [endScreens, setEndScreens] = useState<EndScreenOption[]>([]);
  const [endBusy, setEndBusy] = useState(false);
  const didSeekInitial = useRef(false);

  // Crop por keyframe (enquadramento). mode = qual superficie de edicao (legenda x enquadramento).
  const [mode, setMode] = useState<"caption" | "crop">("caption");
  const [cropEditable, setCropEditable] = useState(false);
  const [cropKf, setCropKf] = useState<CropKeyframe[]>([]);
  const [cropAuto, setCropAuto] = useState<CropKeyframe[] | null>(null);
  const [cropKfMode, setCropKfMode] = useState<"auto" | "manual">("auto");
  const [cropDragCx, setCropDragCx] = useState<number | null>(null); // cx durante o arraste (visual)
  const [cropDirty, setCropDirty] = useState(false);
  const [cropBusy, setCropBusy] = useState(false); // re-render do enquadramento em andamento
  const [cropVersion, setCropVersion] = useState(0); // cache-bust do preview 9:16 apos re-render
  const [selectedKf, setSelectedKf] = useState<number | null>(null); // tempo do keyframe selecionado
  const restoreTimeRef = useRef<number | null>(null); // preserva o playhead ao trocar de modo/fonte

  const editable = data?.editable ?? false;
  // Gaps de silencio (so leitura, so marcacao visual): calculado a partir do tempo por palavra;
  // clipe sem `words` (legado/sem fala) devolve lista vazia e a trilha nem aparece.
  const silenceGaps = useMemo(() => computeSilenceGaps(data?.words), [data?.words]);
  const bust = cropVersion ? `?v=${cropVersion}` : "";
  const previewSrc = (usingBurned ? burnedSrc : baseSrc) + bust;
  const src169 = `/api/clips/${enc}/src169`;
  const stageSrc = mode === "crop" ? src169 : previewSrc;
  // Rota do arquivo de musica (302 -> presignado) pro <audio> do preview. null = clipe sem musica.
  const musicSrc = music ? `/api/clips/${enc}/music/audio` : null;
  // cx exibido na caixa: durante o arraste, o cx arrastado; senao, interpolado pelo tempo atual
  // (a caixa "segue" a trajetoria enquanto o video toca).
  const displayCx = cropDragCx != null ? cropDragCx : cxAt(cropKf, currentTime);
  const filmstrip = useFilmstrip(editable ? clipId : null); // sprite gerado no servidor (CORS do S3)
  const peaks = useWaveform(editable ? clipId : null);

  // Vista sempre em ordem cronologica (a lista e os blocos leem por aqui). O estado bruto
  // pode estar em qualquer ordem; a identidade vem do _id, entao reordenar nunca perde a selecao.
  const ordered = useMemo(() => [...segments].sort(byStart), [segments]);

  useEffect(() => {
    if (!data) return;
    setSegments(
      data.segments?.length
        ? data.segments.map((s) => ({ _id: genId(), start: s.start, end: s.end, text: s.text }))
        : [],
    );
    setMusic(data.music ?? null);
    setVolume(Math.round((data.music?.volume ?? 0.15) * 100));
    if (data.caption_y != null) {
      setCaptionY(data.caption_y);
      setCaptionMoved(true);
    } else {
      setCaptionY(SB_DEFAULT_Y);
      setCaptionMoved(false);
    }
    if (data.style) {
      setCapStyle({
        size: data.style.size ?? SB_STYLE.size,
        tracking: data.style.tracking ?? SB_STYLE.tracking,
        color: data.style.color ?? SB_STYLE.color,
        shadow: data.style.shadow ?? SB_STYLE.shadow,
      });
      setStyleChanged(true);
    } else {
      setCapStyle(SB_STYLE);
      setStyleChanged(false);
    }
    if (data.bounds) {
      setHasHandles(true);
      setCutIn(data.bounds.in);
      setCutOut(data.bounds.out);
    } else {
      setHasHandles(false);
      setCutIn(0);
      setCutOut(0);
    }
    setEndOverride(data.end_screen ?? null);
    setEndEffective(data.end_screen_effective ?? null);
    setEndDur(data.end_screen_dur ?? null);
    setProjectEnd(data.project_end_screen ?? null);
    setEndScreens(data.end_screens ?? []);
    // Crop por keyframe: carrega a trajetoria salva (mode/kf/auto) e se o clipe e cropavel (16:9 retido).
    setCropEditable(!!data.crop_editable);
    const kfRec = data.keyframes;
    setCropKf(kfRec?.kf ?? []);
    setCropAuto(kfRec?.auto ?? (kfRec?.mode === "auto" ? kfRec?.kf ?? null : null));
    setCropKfMode(kfRec?.mode ?? "auto");
    setCropDirty(false);
    setCropDragCx(null);
    setSelectedKf(null);
    if (!data.crop_editable) setMode("caption");
  }, [data]);

  // Aviso nativo do navegador ao recarregar / fechar a aba / sair pra fora do app
  // com alteracoes nao salvas. (Navegacao SPA pelo botao Voltar do editor e tratada
  // pelo onNavigate do Link + o pop-up dedicado "Sair sem salvar?" abaixo.)
  useEffect(() => {
    if (!dirty && !cropDirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty, cropDirty]);

  // Atalhos: espaco = play/pause, setas = seek (Shift = 5s). Ignora quando digitando
  // num campo de texto ou com um dialogo aberto. Usa videoRef direto (sem stale closure).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // Ctrl/Cmd+S salva (mesmo comportamento do botao Salvar) e impede o "salvar pagina" do
      // navegador. Funciona ate com foco num campo de texto: faz blur pra comitar a edicao inline
      // em andamento, e salva no proximo tick (apos o commit do estado).
      if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        (document.activeElement as HTMLElement | null)?.blur?.();
        setTimeout(() => saveRef.current?.(), 0);
        return;
      }
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || el?.isContentEditable) return;
      if (el?.closest?.('[role="dialog"]')) return;
      const v = videoRef.current;
      if (e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if (e.code === "ArrowLeft" && v) {
        e.preventDefault();
        seek(Math.max(0, v.currentTime - (e.shiftKey ? 5 : 1)));
      } else if (e.code === "ArrowRight" && v) {
        e.preventDefault();
        seek(v.currentTime + (e.shiftKey ? 5 : 1));
      } else if ((e.key === "+" || e.key === "=") && !e.ctrlKey && !e.metaKey) {
        e.preventDefault(); // zoom da timeline: + aproxima
        setPxPerSec((p) => Math.min(ZOOM_MAX, Math.round(p * 1.25)));
      } else if ((e.key === "-" || e.key === "_") && !e.ctrlKey && !e.metaKey) {
        e.preventDefault(); // zoom da timeline: - afasta
        setPxPerSec((p) => Math.max(ZOOM_MIN, Math.round(p / 1.25)));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Restaura a altura salva da timeline (divisoria arrastavel), clampada ao tamanho da janela.
  useEffect(() => {
    try {
      const v = localStorage.getItem("opussb_editor_timelineH");
      if (v) {
        const n = parseInt(v, 10);
        if (!isNaN(n)) setTimelineH(Math.max(160, Math.min(n, window.innerHeight - 220)));
      }
    } catch {
      // localStorage indisponivel: mantem o default
    }
  }, []);

  // Eventos do video + playhead suave (rAF enquanto toca).
  // `editable` entra nas deps porque o <video> so monta quando o clipe e editavel (a query
  // resolveu). Sem isso, o efeito roda no 1o render (data carregando -> video ainda nao
  // montado -> videoRef.current null -> sai no early-return) e nunca reanexa os listeners,
  // deixando currentTime/duration travados em 0 (a legenda nao acompanha o player).
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onMeta = () => {
      setDuration(v.duration || 0);
      // Ao trocar de modo (9:16 <-> 16:9, mesma janela/duracao) o video remonta e volta pro 0;
      // restaura o playhead onde estava pra a edicao nao perder o contexto.
      if (restoreTimeRef.current != null) {
        v.currentTime = restoreTimeRef.current;
        setCurrentTime(restoreTimeRef.current);
        restoreTimeRef.current = null;
      }
    };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onTime = () => setCurrentTime(v.currentTime);
    v.addEventListener("loadedmetadata", onMeta);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("ended", onPause);
    v.addEventListener("timeupdate", onTime);
    // Corrida: se os metadados ja carregaram antes do listener anexar, sincroniza na hora.
    if (v.readyState >= 1) {
      setDuration(v.duration || 0);
      setCurrentTime(v.currentTime);
    }
    return () => {
      v.removeEventListener("loadedmetadata", onMeta);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("ended", onPause);
      v.removeEventListener("timeupdate", onTime);
    };
  }, [stageSrc, editable]);

  // Preview da musica: <audio> paralelo sincronizado com o video (toca/pausa/seek juntos), em loop.
  // So ativa quando ha musica e o preview NAO e o video ja queimado (senao a musica dobraria).
  // Nao re-queima nada; e so pra OUVIR a musica enquanto edita.
  useEffect(() => {
    const v = videoRef.current;
    const a = musicAudioRef.current;
    if (!v || !a) return;
    if (!music || usingBurned) {
      a.pause();
      return;
    }
    const syncTime = () => {
      const d = a.duration;
      a.currentTime = d && isFinite(d) && d > 0 ? v.currentTime % d : v.currentTime;
    };
    const onPlay = () => {
      syncTime();
      a.play().catch(() => {});
    };
    const onPause = () => a.pause();
    const onSeeked = () => syncTime();
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("seeked", onSeeked);
    if (!v.paused) {
      syncTime();
      a.play().catch(() => {});
    }
    return () => {
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("seeked", onSeeked);
      a.pause();
    };
  }, [music, usingBurned, musicSrc, stageSrc]);

  // Volume da musica no preview = volume configurado (0..100 -> 0..1).
  useEffect(() => {
    const a = musicAudioRef.current;
    if (a) a.volume = Math.max(0, Math.min(1, volume / 100));
  }, [volume]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) setCurrentTime(v.currentTime);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  // Play respeita o corte: ao atingir cutOut, pausa (prévia do clipe final). O scrub manual
  // continua livre pra inspecionar as sobras antes de decidir onde abrir/fechar o corte.
  useEffect(() => {
    if (!hasHandles || !playing) return;
    if (currentTime >= cutOut - 0.03) videoRef.current?.pause();
  }, [currentTime, playing, hasHandles, cutOut]);

  // Ao abrir um clipe com sobra, posiciona o playhead no início do corte (não na sobra-cabeça).
  useEffect(() => {
    if (!hasHandles || didSeekInitial.current) return;
    const v = videoRef.current;
    if (v && duration > 0 && cutIn > 0) {
      v.currentTime = cutIn;
      setCurrentTime(cutIn);
      didSeekInitial.current = true;
    }
  }, [hasHandles, duration, cutIn]);

  function seek(t: number) {
    const v = videoRef.current;
    if (v) v.currentTime = t;
    setCurrentTime(t);
  }
  function togglePlay() {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      // Play = prévia do corte: se está fora de [cutIn, cutOut], recomeça no início do corte.
      if (hasHandles && (v.currentTime < cutIn - 0.05 || v.currentTime >= cutOut - 0.05)) {
        v.currentTime = cutIn;
        setCurrentTime(cutIn);
      }
      v.play().catch(() => {});
    } else {
      v.pause();
    }
  }

  function patchStyle(patch: Partial<CapStyle>) {
    setCapStyle((s) => ({ ...s, ...patch }));
    setStyleChanged(true);
    setDirty(true);
  }
  function resetStyle() {
    setCapStyle(SB_STYLE);
    setStyleChanged(false);
    setDirty(true);
  }

  const r2 = (x: number) => Math.round(x * 100) / 100;

  function patchSegment(id: string, patch: Partial<Segment>) {
    setSegments((s) => s.map((row) => (row._id === id ? { ...row, ...patch } : row)));
    setDirty(true);
  }

  // Define um tempo digitado (vindo do campo editavel). Clampa pra nunca cruzar a outra borda
  // nem ficar negativo. NaN e ignorado (o campo reverte sozinho pro valor anterior).
  function setSegTime(id: string, edge: "start" | "end", seconds: number) {
    if (!isFinite(seconds)) return;
    setSegments((arr) =>
      arr.map((row) => {
        if (row._id !== id) return row;
        return edge === "start"
          ? { ...row, start: r2(Math.max(0, Math.min(seconds, row.end - 0.1))) }
          : { ...row, end: r2(Math.max(row.start + 0.1, seconds)) };
      }),
    );
    setDirty(true);
  }

  function addSeg() {
    const at = r2(currentTime);
    const id = genId();
    setSegments((s) => [...s, { _id: id, start: at, end: r2(at + 2), text: "" }]);
    setSelectedId(id);
    setDirty(true);
  }

  function deleteSeg(id: string) {
    setSegments((s) => s.filter((row) => row._id !== id));
    setSelectedId((cur) => (cur === id ? null : cur));
    setDirty(true);
  }

  // Divide uma linha em duas. Se um caret (posicao no texto) e passado (Enter no textarea),
  // corta exatamente ali; senao corta no playhead (se dentro do trecho) ou no meio, alinhando
  // o texto na palavra mais proxima. O tempo da divisao segue o playhead ou a proporcao do corte.
  function splitSeg(id: string, caret?: number) {
    const seg = segments.find((s) => s._id === id);
    if (!seg) return;
    const span = seg.end - seg.start;
    if (span < 0.3) {
      toast.error("Linha curta demais para dividir.");
      return;
    }
    const len = seg.text.length;
    const inside = currentTime > seg.start + 0.05 && currentTime < seg.end - 0.05;
    let cut: number;
    if (caret != null) {
      cut = Math.max(0, Math.min(caret, len));
    } else if (inside && len > 0) {
      cut = nearestSpace(seg.text, Math.round(((currentTime - seg.start) / span) * len));
    } else {
      cut = nearestSpace(seg.text, Math.round(len / 2));
    }
    const left = seg.text.slice(0, cut).trim();
    const right = seg.text.slice(cut).trim();
    let mid = inside ? r2(currentTime) : r2(seg.start + span * (len ? cut / len : 0.5));
    mid = Math.min(r2(seg.end - 0.05), Math.max(r2(seg.start + 0.05), mid));
    setSegments((prev) => {
      const idx = prev.findIndex((s) => s._id === id);
      if (idx < 0) return prev;
      const a: EditSeg = { _id: seg._id, start: seg.start, end: mid, text: left };
      const b: EditSeg = { _id: genId(), start: mid, end: seg.end, text: right };
      const copy = prev.slice();
      copy.splice(idx, 1, a, b);
      return copy;
    });
    setSelectedId(seg._id);
    setDirty(true);
  }

  // Junta a linha com a vizinha (cronologica) anterior ou seguinte: une os tempos e o texto.
  function mergeSeg(id: string, dir: "prev" | "next") {
    const sorted = [...segments].sort(byStart);
    const si = sorted.findIndex((s) => s._id === id);
    if (si < 0) return;
    const other = dir === "prev" ? sorted[si - 1] : sorted[si + 1];
    if (!other) {
      toast.info(dir === "prev" ? "Não há linha antes desta." : "Não há linha depois desta.");
      return;
    }
    const cur = sorted[si];
    const a = cur.start <= other.start ? cur : other; // mais cedo
    const b = cur.start <= other.start ? other : cur; // mais tarde
    const merged: EditSeg = {
      _id: a._id,
      start: Math.min(a.start, b.start),
      end: Math.max(a.end, b.end),
      text: `${a.text} ${b.text}`.trim(),
    };
    setSegments((prev) => prev.filter((s) => s._id !== a._id && s._id !== b._id).concat(merged));
    setSelectedId(merged._id);
    setDirty(true);
  }

  const activeSeg = segments.find((s) => currentTime >= s.start && currentTime < s.end) ?? null;
  const activeId = activeSeg?._id ?? null;
  const activeText = captionsOn && !usingBurned && activeSeg ? activeSeg.text : null;

  async function onUploadMusic(f: File) {
    toast.info("Enviando música…");
    try {
      const res = await api.uploadMusic(clipId, f, 0.15);
      setMusic(res.music ?? { filename: f.name, volume: 0.15 });
      setVolume(Math.round((res.music?.volume ?? 0.15) * 100));
      toast.success("Música adicionada. Salve e o vídeo re-renderiza no próximo play.");
    } catch (e) {
      toast.error((e as Error)?.message || "Falha ao enviar música.");
    }
  }
  async function onRemoveMusic() {
    try {
      await api.removeMusic(clipId);
      setMusic(null);
      toast.success("Música removida.");
    } catch {
      toast.error("Falha ao remover música.");
    }
  }

  // Liga/desliga a tela final POR CLIPE (ação imediata, fora do Salvar geral: rota dedicada que
  // não re-queima a legenda). O backend já invalida o cache do anexo; refaz no próximo play.
  async function applyEndScreen(value: string | null) {
    setEndBusy(true);
    try {
      const r = await api.putClipEndScreen(clipId, { end_screen: value });
      setEndOverride(r.end_screen ?? value);
      setEndEffective(r.end_screen_effective ?? null);
      toast.success(
        (r.end_screen_effective ? "Tela final ligada" : "Tela final removida") +
          " (re-renderiza no próximo play).",
      );
    } catch (e) {
      toast.error((e as Error)?.message || "Falha ao atualizar a tela final.");
    } finally {
      setEndBusy(false);
    }
  }

  // Encurtar a tela final arrastando a alça na timeline: durante o arraste só atualiza o visual;
  // ao soltar, persiste (rota dedicada, não re-queima a legenda).
  function onChangeEndDur(d: number) {
    setEndDur(d);
  }
  async function commitEndDur(d: number) {
    setEndDur(d);
    try {
      await api.putClipEndScreen(clipId, { dur: d });
    } catch {
      toast.error("Falha ao ajustar a duração da tela final.");
    }
  }

  async function save() {
    if (saving || !editable) return; // re-entrancia / clipe nao editavel (Ctrl+S tambem cai aqui)
    // Monta um array limpo {start,end,text}, em ordem cronologica, sem o _id de cliente.
    const out: Segment[] = [];
    let bad: number | null = null;
    [...segments].sort(byStart).forEach((r, i) => {
      const st = Number(r.start);
      const en = Number(r.end);
      const tx = (r.text || "").trim();
      if (isNaN(st) || isNaN(en) || en <= st) {
        if (bad === null) bad = i + 1;
        return;
      }
      if (!tx) return;
      out.push({ start: st, end: en, text: tx });
    });
    if (bad !== null) {
      toast.error(`Linha ${bad}: o fim precisa ser maior que o início.`);
      return;
    }
    setSaving(true);
    try {
      const tasks: Promise<unknown>[] = [
        api.putCaptions(
          clipId,
          out,
          captionMoved ? captionY : null,
          styleChanged ? capStyle : null,
          hasHandles ? { in: cutIn, out: cutOut } : null,
        ),
      ];
      if (music) tasks.push(api.setMusicVolume(clipId, Math.max(0, Math.min(1, volume / 100))));
      await Promise.all(tasks);
      qc.invalidateQueries({ queryKey: ["captions", clipId] });
      setDirty(false);
      toast.success("Salvo! O vídeo re-renderiza no próximo play.");
    } catch (e) {
      toast.error((e as Error)?.message || "Falha ao salvar.");
    } finally {
      setSaving(false);
    }
  }
  // Mantem saveRef apontando pro save() mais recente (pro atalho Ctrl+S nao usar closure velha).
  useEffect(() => {
    saveRef.current = save;
  });

  // ── Crop por keyframe (enquadramento) ──────────────────────────────────────
  function toggleMode(next: "caption" | "crop") {
    if (next === mode) return;
    restoreTimeRef.current = videoRef.current?.currentTime ?? currentTime; // preserva o playhead
    videoRef.current?.pause();
    setMode(next);
  }
  function onCropDrag(c: number) {
    setCropDragCx(c);
  }
  function onCropCommit(c: number) {
    const t = videoRef.current?.currentTime ?? currentTime; // tempo VIVO (pode estar tocando)
    setCropKf((kf) => upsertKf(kf, t, c));
    setCropKfMode("manual");
    setCropDirty(true);
    setCropDragCx(null);
    setSelectedKf(Math.round(t * 1000) / 1000);
  }
  function addKfHere() {
    const t = videoRef.current?.currentTime ?? currentTime;
    setCropKf((kf) => upsertKf(kf, t, cxAt(kf, t))); // keyframe no tempo atual com o cx interpolado
    setCropKfMode("manual");
    setCropDirty(true);
  }
  function deleteKf(t: number) {
    setCropKf((kf) => kf.filter((k) => Math.abs(k.t - t) > 1e-6));
    setSelectedKf(null);
    setCropKfMode("manual");
    setCropDirty(true);
  }
  // Poll do estado do re-render assincrono ate done/error (teto ~3min).
  async function pollRerender() {
    for (let i = 0; i < 180; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      let st;
      try {
        st = (await api.getKeyframes(clipId)).rerender;
      } catch {
        continue;
      }
      if (st?.state === "done") return;
      if (st?.state === "error") throw new Error(st.error || "re-render falhou");
    }
    throw new Error("o re-render demorou demais");
  }
  async function applyCrop() {
    if (!cropKf.length) {
      toast.error("Arraste a caixa para criar ao menos um keyframe.");
      return;
    }
    setCropBusy(true);
    try {
      await api.putKeyframes(clipId, { kf: cropKf });
      toast.info("Re-renderizando o enquadramento…");
      await pollRerender();
      setCropDirty(false);
      setCropKfMode("manual");
      setCropVersion((v) => v + 1); // recarrega o preview 9:16 com o novo enquadramento
      qc.invalidateQueries({ queryKey: ["captions", clipId] });
      toast.success("Enquadramento aplicado.");
    } catch (e) {
      toast.error((e as Error)?.message || "Falha ao aplicar o enquadramento.");
    } finally {
      setCropBusy(false);
    }
  }
  async function resetCrop() {
    setCropBusy(true);
    try {
      await api.putKeyframes(clipId, { reset: true });
      toast.info("Voltando ao enquadramento automático…");
      await pollRerender();
      if (cropAuto) setCropKf(cropAuto);
      setCropKfMode("auto");
      setCropDirty(false);
      setCropVersion((v) => v + 1);
      qc.invalidateQueries({ queryKey: ["captions", clipId] });
      toast.success("Enquadramento automático restaurado.");
    } catch (e) {
      toast.error((e as Error)?.message || "Falha ao restaurar o automático.");
    } finally {
      setCropBusy(false);
    }
  }

  function exportClip() {
    // Com legenda -> versao queimada (com trim); Sem legenda -> corte cru (com trim) via ?nocap=1.
    // (baseSrc e a JANELA inteira com sobras, usada so pelo preview ao vivo; nao serve pra exportar.)
    const url = captionsOn ? burnedSrc : `${burnedSrc}?nocap=1`;
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    a.target = "_blank";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  // Arrasta a divisoria entre roteiro (cima) e timeline (baixo) na coluna esquerda: ajusta a altura
  // da timeline e persiste no fim. Clampa pra os dois manterem um minimo.
  function startTimelineResize(e: React.PointerEvent) {
    e.preventDefault();
    const startY = e.clientY;
    const startH = timelineH;
    const leftH = leftRef.current?.getBoundingClientRect().height ?? window.innerHeight;
    let finalH = startH;
    const move = (ev: PointerEvent) => {
      finalH = Math.max(160, Math.min(startH - (ev.clientY - startY), leftH - 200));
      setTimelineH(finalH);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      try {
        localStorage.setItem("opussb_editor_timelineH", String(Math.round(finalH)));
      } catch {
        // ignora
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function selectAndSeek(id: string) {
    setSelectedId(id);
    const seg = segments.find((s) => s._id === id);
    if (seg) seek(seg.start);
  }

  return (
    <div className="editor-shell">
      <header className="editor-topbar">
        <Link
          href={projectHref}
          className="editor-icon-btn"
          aria-label="Voltar"
          onNavigate={(e) => {
            if (dirty || cropDirty) {
              e.preventDefault();
              setLeaveTo(projectHref);
            }
          }}
        >
          <ArrowLeft className="size-4" />
        </Link>
        <span className="editor-title">{title || "Editar clipe"}</span>

        {cropEditable && (
          <div className="ml-2 flex items-center gap-1 rounded-full border border-[var(--border)] p-1">
            <button
              type="button"
              className={cn("chip !rounded-full !border-0 !px-3 !py-1", mode === "caption" && "is-active")}
              onClick={() => toggleMode("caption")}
            >
              Legenda
            </button>
            <button
              type="button"
              className={cn("chip !rounded-full !border-0 !px-3 !py-1", mode === "crop" && "is-active")}
              onClick={() => toggleMode("crop")}
            >
              Enquadramento
            </button>
          </div>
        )}

        {mode === "caption" && (
          <div className="ml-2 flex items-center gap-1 rounded-full border border-[var(--border)] p-1">
            <button
              type="button"
              className={cn("chip !rounded-full !border-0 !px-3 !py-1", captionsOn && "is-active")}
              onClick={() => setCaptionsOn(true)}
            >
              Com legenda
            </button>
            <button
              type="button"
              className={cn("chip !rounded-full !border-0 !px-3 !py-1", !captionsOn && "is-active")}
              onClick={() => setCaptionsOn(false)}
            >
              Sem legenda
            </button>
          </div>
        )}

        <div className="flex-1" />
        {editable && (
          <Button variant="brandGhost" onClick={() => setSettingsOpen(true)}>
            <SlidersHorizontal className="size-4" /> Ajustes
          </Button>
        )}
        <Button variant="brandGhost" onClick={save} disabled={saving || !editable}>
          {saving ? "Salvando…" : "Salvar alterações"}
        </Button>
        <Button variant="gold" onClick={exportClip}>
          <Download className="size-4" /> Exportar
        </Button>
      </header>

      {isLoading ? (
        <div className="flex flex-1 items-center justify-center">
          <span className="clip-prep-spin" aria-label="Carregando" />
        </div>
      ) : isError ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <div className="empty max-w-md">
            <p className="empty-title">Não consegui carregar este clipe</p>
            <p className="empty-text">{(error as Error)?.message || "Tente novamente em instantes."}</p>
            <Link href={`/projetos/${jobId}`} className="link-gold mt-4 inline-block">
              ← Voltar ao projeto
            </Link>
          </div>
        </div>
      ) : !editable ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <div className="empty max-w-md">
            <p className="empty-title">Edição indisponível</p>
            <p className="empty-text">
              Este clipe foi gerado antes do editor (legenda fixa no vídeo) ou não tem fala.
              Você ainda pode exportar pela página do projeto.
            </p>
            <Link href={`/projetos/${jobId}`} className="link-gold mt-4 inline-block">
              ← Voltar ao projeto
            </Link>
          </div>
        </div>
      ) : (
        <>
          <div className="editor-body">
            <div className="editor-left" ref={leftRef}>
              <div className="editor-roteiro-col">
              <TranscriptPanel
                segments={ordered}
                selectedId={selectedId}
                activeId={activeId}
                currentTime={currentTime}
                playing={playing}
                onSelect={selectAndSeek}
                onChangeText={(id, text) => patchSegment(id, { text })}
                onDelete={deleteSeg}
                onAdd={addSeg}
                onSetTime={setSegTime}
                onSplit={splitSeg}
                onMerge={mergeSeg}
                onEditStart={() => videoRef.current?.pause()}
              />
              </div>

              <div
                className="editor-split"
                onPointerDown={startTimelineResize}
                role="separator"
                aria-orientation="horizontal"
                aria-label="Ajustar altura da timeline"
                title="Arraste para ajustar a altura da timeline"
              />

              <div className="editor-timeline-wrap" style={{ height: timelineH }}>
                <EditorTimeline
                  duration={duration}
                  currentTime={currentTime}
                  playing={playing}
                  segments={ordered}
                  selectedId={selectedId}
                  pxPerSec={pxPerSec}
                  filmstrip={filmstrip}
                  peaks={peaks}
                  hasHandles={hasHandles}
                  cutIn={cutIn}
                  cutOut={cutOut}
                  silenceGaps={silenceGaps}
                  endScreenLabel={
                    endEffective ? (endScreens.find((o) => o.id === endEffective)?.label ?? "Tela final") : null
                  }
                  endScreenDur={
                    endEffective
                      ? endDur ?? endScreens.find((o) => o.id === endEffective)?.duration ?? 0
                      : 0
                  }
                  endScreenAssetDur={endScreens.find((o) => o.id === endEffective)?.duration ?? 0}
                  onChangeEndDur={onChangeEndDur}
                  onCommitEndDur={commitEndDur}
                  onSeek={seek}
                  onPlayPause={togglePlay}
                  onSelect={setSelectedId}
                  onChangeSegment={(id, patch) => patchSegment(id, patch)}
                  onChangeCut={(edge, value) => {
                    if (edge === "in") setCutIn(value);
                    else setCutOut(value);
                    setDirty(true);
                  }}
                  cropActive={mode === "crop"}
                  keyframes={cropKf}
                  selectedKf={selectedKf}
                  onSelectKf={(t) => {
                    setSelectedKf(t);
                    seek(t);
                  }}
                  onDeleteKf={deleteKf}
                  onZoom={setPxPerSec}
                />
              </div>
            </div>

            <div className="editor-right">
            {mode === "crop" ? (
              <CropStage
                videoRef={videoRef}
                src={src169}
                cx={displayCx}
                onDragCx={cropBusy ? undefined : onCropDrag}
                onCommitCx={cropBusy ? undefined : onCropCommit}
                onError={() => {}}
                onTogglePlay={togglePlay}
                barExtra={
                  <div className="editor-crop-bar">
                    <span className="text-ink-muted">
                      {cropKfMode === "auto" ? "Automático" : "Manual"} · {cropKf.length}{" "}
                      {cropKf.length === 1 ? "keyframe" : "keyframes"}
                    </span>
                    <button
                      type="button"
                      className="text-gold underline-offset-2 hover:underline disabled:opacity-50"
                      onClick={addKfHere}
                      disabled={cropBusy}
                    >
                      + keyframe aqui
                    </button>
                    {cropAuto && cropKfMode === "manual" && (
                      <button
                        type="button"
                        className="text-ink-muted hover:text-ink disabled:opacity-50"
                        onClick={resetCrop}
                        disabled={cropBusy}
                      >
                        voltar ao automático
                      </button>
                    )}
                    <Button variant="gold" size="sm" onClick={applyCrop} disabled={cropBusy || !cropDirty}>
                      {cropBusy ? "Aplicando…" : "Aplicar enquadramento"}
                    </Button>
                  </div>
                }
              />
            ) : (
              <PreviewStage
                videoRef={videoRef}
                src={previewSrc}
                captionText={activeText}
                captionY={captionY}
                captionStyle={capStyle}
                onChangeY={
                  captionsOn && !usingBurned
                    ? (y) => {
                        setCaptionY(y);
                        setCaptionMoved(true);
                        setDirty(true);
                      }
                    : undefined
                }
                barExtra={
                  captionsOn && !usingBurned ? (
                    <>
                      <span className="text-ink-muted">arraste a legenda p/ posicionar</span>
                      {captionMoved && (
                        <button
                          type="button"
                          className="text-gold underline-offset-2 hover:underline"
                          onClick={() => {
                            setCaptionY(SB_DEFAULT_Y);
                            setCaptionMoved(false);
                            setDirty(true);
                          }}
                        >
                          Resetar posição
                        </button>
                      )}
                    </>
                  ) : null
                }
                onError={() => {
                  if (!usingBurned) setUsingBurned(true);
                }}
                onTogglePlay={togglePlay}
              />
            )}
            </div>
          </div>
        </>
      )}

      {usingBurned && (
        <p className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full bg-[var(--surface-2)] px-3 py-1 text-[0.72rem] text-ink-muted">
          Preview ao vivo indisponível (reinicie o backend p/ habilitar a rota do vídeo base).
        </p>
      )}

      {/* Musica do preview (oculto): toca em paralelo ao video base, sincronizado, sem re-queimar. */}
      <audio ref={musicAudioRef} src={musicSrc ?? undefined} loop preload="auto" className="hidden" />

      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        {/* solid: abre sobre o video em playback; vidro perderia contraste em cena
            clara e re-filtraria cada frame do preview (custo continuo a toa). */}
        <SheetContent side="right" solid className="editor-settings-sheet">
          <SheetHeader>
            <SheetTitle>Ajustes do clipe</SheetTitle>
          </SheetHeader>
          <div className="editor-settings-body">
            <MusicControl
              music={music}
              volume={volume}
              onUpload={onUploadMusic}
              onRemove={onRemoveMusic}
              onVolume={(v) => {
                setVolume(v);
                setDirty(true);
              }}
            />
            <StyleControl
              value={capStyle}
              changed={styleChanged}
              onChange={patchStyle}
              onReset={resetStyle}
            />
            <EndScreenControl
              options={endScreens}
              effectiveId={endEffective}
              override={endOverride}
              projectDefault={projectEnd}
              busy={endBusy}
              onChange={applyEndScreen}
            />
          </div>
        </SheetContent>
      </Sheet>

      {/* Pop-up "Sair sem salvar?" do editor (dedicado, NAO usa o ConfirmDialog compartilhado).
          Acao principal = Continuar editando (dourado); Sair sem salvar = secundaria/perigo. */}
      <Dialog
        open={leaveTo !== null}
        onOpenChange={(v) => {
          if (!v) setLeaveTo(null);
        }}
      >
        <DialogContent showCloseButton={false} className="editor-leave-dialog duration-200">
          <div className="editor-leave-icon" aria-hidden>
            <TriangleAlert className="size-5" />
          </div>
          <DialogHeader className="editor-leave-head">
            <DialogTitle className="editor-leave-title">Sair sem salvar?</DialogTitle>
            <DialogDescription className="editor-leave-text">
              Você tem alterações não salvas neste clipe. Se sair agora, elas serão perdidas.
            </DialogDescription>
          </DialogHeader>
          <div className="editor-leave-actions">
            <Button
              variant="destructive"
              onClick={() => {
                const dest = leaveTo;
                setLeaveTo(null);
                setDirty(false); // libera o onNavigate p/ a navegacao programatica abaixo
                setCropDirty(false);
                if (dest) router.push(dest);
              }}
            >
              Sair sem salvar
            </Button>
            <Button variant="gold" onClick={() => setLeaveTo(null)}>
              Continuar editando
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
