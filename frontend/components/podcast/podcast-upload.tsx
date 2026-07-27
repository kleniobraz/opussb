"use client";

// Upload do episódio de podcast (§39): estaciona o job em 'uploaded' (POST /api/podcast/upload)
// e leva pra tela de aprovação do gancho, de onde o disparo da transcrição acontece. Mesmo
// entorno visual v3 do UploadConsole de /gerar, sem os controles de clipe (aspecto/duração/
// idioma/legenda não existem aqui; o único ajuste é a vinheta que abre o episódio).

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Sparkles, X } from "lucide-react";
import * as api from "@/lib/api";
import { fmtBytes, fmtEta, fmtGB, fmtMB, fmtSeconds, fmtVelocidade } from "@/lib/format";
import { MAX_BYTES, ehDezesseisPorNove, extOk, lerMetaVideo } from "@/lib/upload";
import { UploadCancelado, uploadDireto } from "@/lib/upload-multipart";
import { cn } from "@/lib/utils";
import { DropIcon } from "@/components/drop-icon";
import { Button } from "@/components/ui/button";
import { V3Progress } from "@/components/ui/v3-progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function PodcastUpload() {
  const router = useRouter();
  const { data: vinhetasData, isLoading: vinhetasLoading } = useQuery({
    queryKey: ["vinhetas"],
    queryFn: api.getVinhetas,
  });
  const vinhetas = vinhetasData?.vinhetas ?? [];

  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState<{ text: string; error: boolean }>({ text: "", error: false });
  const [drag, setDrag] = useState(false);
  const [vinhetaId, setVinhetaId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [prog, setProg] = useState<{
    pct: number; loaded: number; total: number; eta: number | null; vel: number | null;
  } | null>(null);
  // Amostras (instante, bytes) pra média móvel da velocidade. Ref e não state: elas mudam a
  // cada evento de progresso (dezenas por segundo) e não devem provocar render por si.
  const amostrasRef = useRef<{ t: number; b: number }[]>([]);
  // O par (controller, token) é o que torna o Cancelar real: o controller para os PUTs em voo
  // e o token deixa o servidor chamar AbortMultipartUpload. Sem o segundo, cancelar pararia a
  // barra mas deixaria as partes já enviadas COBRADAS e invisíveis no bucket.
  const fileRef = useRef<File | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const tokenRef = useRef<string | null>(null);
  const [cancelando, setCancelando] = useState(false);
  const [partes, setPartes] = useState<{ feitas: number; total: number } | null>(null);

  function pickFile(f: File | null) {
    setError("");
    fileRef.current = f;
    if (!f) {
      setFile(null);
      setMeta({ text: "", error: false });
      return;
    }
    if (!extOk(f.name)) {
      setFile(null);
      fileRef.current = null;
      setMeta({ text: "Formato não suportado. Use mp4, mov, avi, mkv ou webm.", error: true });
      return;
    }
    if (f.size > MAX_BYTES) {
      setFile(null);
      fileRef.current = null;
      setMeta({ text: `Arquivo acima de 15GB (${fmtBytes(f.size)}).`, error: true });
      return;
    }
    setFile(f);
    setMeta({ text: fmtBytes(f.size), error: false });

    // Checa o 16:9 AQUI, lendo só o cabeçalho do arquivo local. O servidor revalida no fim
    // (é ele quem manda), mas descobrir que o vídeo não é 16:9 depois de 40min de envio é o
    // pior jeito possível de receber essa notícia. Metadado ilegível (mkv, avi) não reprova:
    // segue o envio e o servidor decide.
    void lerMetaVideo(f).then((m) => {
      // Carimbado com o arquivo: lerMetaVideo leva até 8s, e sem esta comparação o resultado
      // de um arquivo já substituído sobrescreveria o do arquivo novo (trocar A por B e ver a
      // seleção de B sumir sozinha três segundos depois).
      if (fileRef.current !== f || !m) return;
      const detalhe = `${m.width}x${m.height}`;
      if (!ehDezesseisPorNove(m)) {
        // AVISA, não bloqueia. Aqui só dá pra comparar pixel cru; o servidor aplica o SAR, e
        // material anamórfico (1440x1080 com SAR 4:3 é 16:9 de verdade) seria reprovado aqui e
        // aceito lá. Quem manda é o servidor: bloquear no cliente deixaria o usuário sem saída
        // nenhuma pela tela. O ganho de avisar continua de pé: ele sabe ANTES dos 40min.
        setMeta({
          text: `${detalhe}: isto não parece 16:9. O envio segue, mas o servidor pode recusar.`,
          error: true,
        });
      } else {
        setMeta({ text: `${fmtBytes(f.size)} · ${detalhe}`, error: false });
      }
    });
  }

  useEffect(() => {
    // pagehide e não beforeunload: beforeunload não dispara em navegador móvel nem em aba
    // descartada, e este é o caso comum de abandono de um envio de 40 minutos. O fetch do
    // abort usa keepalive justamente pra sobreviver ao descarte da página.
    const aoSair = () => {
      if (tokenRef.current) void api.podcastUploadAbort(tokenRef.current);
    };
    window.addEventListener("pagehide", aoSair);
    return () => {
      window.removeEventListener("pagehide", aoSair);
      aoSair(); // desmontar no meio do envio é abandono igual (o usuário navegou pra outra tela)
    };
  }, []);

  async function cancelar() {
    setCancelando(true);
    abortRef.current?.abort();
    if (tokenRef.current) await api.podcastUploadAbort(tokenRef.current);
    tokenRef.current = null;
    setCancelando(false);
    setSubmitting(false);
    setProg(null);
    setError("Envio cancelado.");
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!file) return setError("Selecione um vídeo.");
    if (!vinhetaId) return setError("Escolha uma vinheta.");

    setSubmitting(true);
    setProg({ pct: 0, loaded: 0, total: file.size, eta: null, vel: null });
    amostrasRef.current = [];
    const avancar = (loaded: number, total: number) => {
      // MÉDIA MÓVEL de 5s, não média desde o início. A média acumulada fica cada vez mais
      // surda: num envio de 40min, se a rede cair pela metade nos últimos 5, o número mal se
      // mexe e o tempo restante mente. A janela curta responde, e é longa o bastante pra não
      // pular a cada evento.
      const JANELA_MS = 5000;
      const agora = Date.now();
      const am = amostrasRef.current;
      // Um retry reenvia a parte do zero, então `loaded` PODE diminuir. Sem este reset a
      // média móvel calcularia velocidade negativa e o tempo restante viraria absurdo.
      if (am.length && loaded < am[am.length - 1].b) am.length = 0;
      am.push({ t: agora, b: loaded });
      while (am.length > 2 && agora - am[0].t > JANELA_MS) am.shift();

      const dt = (agora - am[0].t) / 1000;
      const db = loaded - am[0].b;
      // Só publica velocidade com pelo menos 1s de janela: abaixo disso o número dança tanto
      // que atrapalha mais do que informa.
      const vel = dt >= 1 && db > 0 ? db / dt : null;
      const eta = vel && total > loaded ? (total - loaded) / vel : null;
      setProg({ pct: total ? Math.round((loaded / total) * 100) : 0, loaded, total, eta, vel });
    };

    const ac = new AbortController();
    abortRef.current = ac;
    try {
      // Caminho NOVO primeiro. Ele devolve null quando o kill switch PODCAST_UPLOAD_DIRETO
      // está desligado no servidor (404 no /init), e aí caímos no caminho antigo. Usar o
      // próprio 404 como sinal evita uma rota só pra publicar o valor de uma flag, e faz o
      // rollback ser uma linha no .env, sem rebuild do Next.
      const direto = await uploadDireto({
        file,
        vinhetaId,
        sinal: ac.signal,
        onToken: (t) => (tokenRef.current = t),
        onProgresso: (p) => {
          avancar(p.enviado, p.total);
          setPartes({ feitas: p.partesConcluidas, total: p.partesTotal });
        },
      });
      const res =
        direto ??
        (await api.podcastUploadWithProgress(file, vinhetaId, ({ loaded, total }) =>
          avancar(loaded, total),
        ));
      tokenRef.current = null;
      router.push("/podcast/" + res.job_id);
    } catch (ex) {
      if (ex instanceof UploadCancelado) return; // o cancelar() já cuidou do estado
      setError(ex instanceof api.ApiError ? ex.message : "Erro ao enviar o episódio.");
      setSubmitting(false);
      setProg(null);
    }
  }

  return (
    <section className="overflow-hidden rounded-[var(--radius-md)] border border-v3-border bg-v3-surface-2">
      <form onSubmit={onSubmit}>
        <div className="border-b border-v3-border p-6">
          <h3 className="font-v3-display text-lg font-black uppercase text-white">Enviar episódio</h3>
          <p className="mt-1 text-sm text-v3-text-soft">
            Envie o episódio em 16:9 com frame rate constante e escolha a vinheta de abertura.
          </p>
        </div>

        <div className="p-6">
          <label
            className={cn(
              "group flex cursor-pointer flex-col items-center gap-1.5 rounded-[var(--radius-md)] border border-dashed border-v3-border-hover px-6 py-11 text-center transition-colors",
              drag && "border-v3-accent bg-v3-accent/5",
            )}
            htmlFor="podcast-file"
            onDragEnter={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              pickFile(e.dataTransfer.files?.[0] ?? null);
            }}
          >
            <span
              className={cn(
                "mb-2.5 grid size-14 place-items-center rounded-[var(--radius-md)] border border-v3-border-hover text-v3-text-soft transition-colors group-hover:border-v3-accent group-hover:text-v3-accent",
                drag && "border-v3-accent text-v3-accent",
              )}
              aria-hidden="true"
            >
              <DropIcon />
            </span>
            <span className="font-v3-sans text-lg font-bold text-white">
              Arraste seu vídeo ou clique para enviar
            </span>
            <span className="font-mono text-xs text-v3-text-soft">mp4, mov, avi, mkv, webm · 16:9 · até 15GB</span>
            {file && <span className="mt-1 font-mono text-xs font-semibold text-v3-accent">{file.name}</span>}
            {meta.text && (
              <span
                className={cn(
                  "mt-1 font-mono text-xs font-semibold",
                  meta.error ? "text-[var(--red)]" : "text-v3-accent",
                )}
              >
                {meta.text}
              </span>
            )}
            <input
              type="file"
              id="podcast-file"
              name="video"
              accept=".mp4,.mov,.avi,.mkv,.webm"
              className="sr-only"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />
          </label>
        </div>

        <div className="p-6 pt-0">
          <p className="mb-3 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-v3-text-soft">
            Vinheta de abertura
          </p>
          {vinhetasLoading ? (
            <p className="font-mono text-sm text-v3-text-soft">Carregando vinhetas...</p>
          ) : vinhetas.length === 0 ? (
            <p className="font-mono text-sm text-v3-text-soft">
              Não há vinheta cadastrada ainda. Fale com o admin pra cadastrar uma antes de enviar.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-4">
              <div>
                <label className="mb-2 block font-mono text-xs uppercase tracking-wide text-v3-text-soft">
                  Vinheta
                </label>
                <Select value={vinhetaId} onValueChange={setVinhetaId}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Escolha a vinheta" />
                  </SelectTrigger>
                  <SelectContent>
                    {vinhetas.map((v) => (
                      <SelectItem key={v.id} value={v.id}>
                        {v.label}
                        {v.duration != null ? ` · ${fmtSeconds(v.duration)}` : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          <div className="mt-6 flex flex-wrap items-center justify-between gap-5">
            {submitting ? (
              <div className="flex w-full flex-col gap-2.5">
                <div className="flex items-baseline justify-between font-mono text-sm text-v3-text-soft">
                  <span>{(prog?.pct ?? 0) >= 100 ? "Quase lá, preparando o processamento" : "Enviando episódio"}</span>
                  <strong className="text-base font-bold text-v3-accent">{prog?.pct ?? 0}%</strong>
                </div>
                <V3Progress value={prog?.pct ?? 0} />
                {/* Enviado em MB e total em GB: o número que CORRE precisa de casas finas pra
                    andar contínuo (em GB com 2 casas o passo visível é ~10MB, e com 1 casa,
                    107MB), enquanto o total é fixo e fica mais legível curto. */}
                <div className="flex items-center justify-between font-mono text-xs text-v3-text-soft">
                  <span className="tabular-nums">
                    {prog ? `${fmtMB(prog.loaded)} de ${fmtGB(prog.total)}` : ""}
                  </span>
                  <span className="tabular-nums">
                    {(prog?.pct ?? 0) >= 100
                      ? "processando…"
                      : prog?.eta != null
                        ? `~${fmtEta(prog.eta)} restante`
                        : "calculando tempo…"}
                  </span>
                </div>
                <div className="flex items-center justify-between font-mono text-xs text-v3-text-soft">
                  <span className="tabular-nums">
                    {(prog?.pct ?? 0) < 100 ? fmtVelocidade(prog?.vel) : ""}
                  </span>
                  {partes && partes.total > 1 && (
                    <span className="tabular-nums">
                      parte {Math.min(partes.feitas + 1, partes.total)} de {partes.total}
                    </span>
                  )}
                </div>
                {/* Só aparece enquanto ainda há bytes subindo: depois de 100% o servidor já
                    está montando o objeto, e cancelar ali seria pedir pra desfazer um envio
                    que deu certo. */}
                {/* Critério por PARTES, não por porcentagem: num arquivo de 15GB o pct vira
                    100 com até 75MB ainda por subir, e se justo a última parte travasse o
                    usuário ficaria sem botão nenhum, só com F5 (que deixa o multipart órfão). */}
                {(partes ? partes.feitas < partes.total : (prog?.pct ?? 0) < 100) && (
                  <button
                    type="button"
                    onClick={cancelar}
                    disabled={cancelando}
                    className="mt-1 inline-flex w-fit items-center gap-1.5 font-mono text-xs font-semibold text-v3-text-soft underline-offset-4 transition-colors hover:text-[var(--red)] hover:underline disabled:opacity-50"
                  >
                    <X className="size-3.5" aria-hidden="true" />
                    {cancelando ? "cancelando…" : "cancelar envio"}
                  </button>
                )}
              </div>
            ) : (
              <Button type="submit" variant="v3Cta" size="brand" disabled={vinhetas.length === 0}>
                <Sparkles className="size-4" aria-hidden="true" />
                Enviar episódio
              </Button>
            )}
          </div>
        </div>
      </form>
      {error && <div className="px-6 pb-4 font-mono text-sm font-medium text-[var(--red)]">{error}</div>}
    </section>
  );
}
