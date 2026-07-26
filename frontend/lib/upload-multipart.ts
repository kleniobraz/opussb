// Upload DIRETO do navegador pro S3, em multipart presignado.
//
// POR QUE EXISTE: pelo caminho antigo (POST multipart/form-data pro Flask) o episódio de até
// 15GB atravessava a VPS, prendendo uma das 4 threads do gunicorn por até 75min. Taxa de
// sucesso MEDIDA em 14 dias de log do nginx: 21% (8 de 37). Aqui os bytes vão do navegador
// direto pro bucket, e o servidor só assina URLs.
//
// O QUE ESTE ARQUIVO GARANTE, e é o que o backend não tem como garantir sozinho:
//   - retry POR PARTE, não do arquivo inteiro. É a diferença entre perder 16MiB e perder 8GB.
//   - renovação de presignada expirada no meio do caminho (403), que acontece de verdade num
//     upload de horas com URLs de 2h.
//   - cancelamento que chama AbortMultipartUpload no servidor. Sem isso, cancelar deixaria GB
//     de partes COBRADAS e invisíveis no list_objects_v2 (só list_multipart_uploads as vê).
//
// XHR e não fetch, de propósito: fetch não reporta progresso de upload (não há
// request.onprogress no padrão), e sem barra de progresso um envio de 40min parece travado.

import { ApiError } from "@/lib/api";
import * as api from "@/lib/api";

export type ProgressoUpload = {
  enviado: number;
  total: number;
  pct: number;
  partesConcluidas: number;
  partesTotal: number;
};

export class UploadCancelado extends Error {
  constructor() {
    super("Envio cancelado.");
    this.name = "UploadCancelado";
  }
}

// Orçamento de retry por parte. O bloqueio de TCP do provedor dura de 8 a 20 minutos, e o
// backoff antigo (4 tentativas somando 15s) morria dentro da janela quando a falha era rápida
// (conexão recusada esgota as 4 quase instantaneamente). Aqui a soma passa de 8min, que cobre
// a janela típica. NÃO cobre os 20min do pior caso, e isso é deliberado: passar disso é deixar
// o usuário meia hora olhando uma barra parada, pior que falhar com uma mensagem.
const TENTATIVAS_POR_PARTE = 6;
const BACKOFF_MS = [2000, 10000, 30000, 90000, 180000];
// Sem NENHUM byte novo por este tempo, a parte é abortada e cai no retry. Existe porque a
// falha característica desta rede NÃO é erro: é a conexão pendurada, que não dispara onerror
// nem status ruim. Sem watchdog o worker fica parado até o RTO do TCP, que são minutos, e a
// barra congela sem ninguém entender por quê. Mesma ideia do s3_client._com_teto no backend.
const SEM_PROGRESSO_MS = 90000;
// Teto de segurança pro valor que vem do SERVIDOR. O número é ajustável no .env justamente
// pra ser mexido à mão mais de uma vez, e um zero a mais (1000 em vez de 100) travaria a aba
// do usuário e dispararia mil PUTs simultâneos por causa de uma linha de config.
const CONCORRENCIA_MAX = 8;

type ParteFeita = { number: number; etag: string };

/** PUT de UMA parte, com progresso e cancelamento. Resolve com o ETag. */
function enviarParte(
  url: string,
  corpo: Blob,
  sinal: AbortSignal,
  onBytes: (enviados: number) => void,
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    // SEM withCredentials: a presignada já carrega a autorização na query string, e mandar
    // cookie faria o preflight exigir Access-Control-Allow-Credentials do S3, que ele não
    // manda. O upload quebraria em toda parte.
    // Teto de PROGRESSO, não de duração: uma parte de 16MiB num uplink ruim leva minutos
    // legítimos, então o que se mede é o tempo desde o último byte.
    let vigia: ReturnType<typeof setTimeout>;
    let estolou = false;
    const rearmar = () => {
      clearTimeout(vigia);
      vigia = setTimeout(() => {
        estolou = true;
        xhr.abort();
      }, SEM_PROGRESSO_MS);
    };
    rearmar();
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        rearmar();
        onBytes(e.loaded);
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        // O ETag só é legível porque o CORS do bucket declara ExposeHeaders: ["ETag"].
        // Sem isso o navegador esconde o header, o complete vai sem ETag e o S3 recusa o
        // objeto inteiro no fim, depois de todos os bytes já terem subido.
        const etag = xhr.getResponseHeader("ETag");
        if (!etag) {
          reject(new ApiError("O navegador não conseguiu ler a confirmação da parte (ETag).", 0));
          return;
        }
        resolve(etag);
      } else {
        reject(new ApiError("Falha ao enviar parte (HTTP " + xhr.status + ").", xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError("Falha de rede ao enviar parte.", 0));
    xhr.onabort = () =>
      reject(estolou ? new ApiError("A parte parou de progredir.", 0) : new UploadCancelado());

    // Cancelar ANTES do send() precisa de reject explícito: abort() num XHR que ainda não foi
    // enviado não dispara onabort (é o que a spec manda), então confiar nele penduraria esta
    // promise pra sempre, e com ela o worker e o Promise.all do upload inteiro.
    if (sinal.aborted) {
      reject(new UploadCancelado());
      return;
    }

    // O listener é REMOVIDO no fim, e não só marcado {once:true}: um episódio de 15GB são 960
    // partes penduradas no mesmo AbortController, e {once:true} só limpa quem chega a
    // disparar. No caminho feliz, que é o comum, nenhuma dispara e as 960 ficam acumuladas.
    const cancelar = () => xhr.abort();
    sinal.addEventListener("abort", cancelar, { once: true });
    xhr.onloadend = () => {
      clearTimeout(vigia);
      sinal.removeEventListener("abort", cancelar);
    };
    xhr.send(corpo);
  });
}

const dorme = (ms: number, sinal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (sinal.aborted) return reject(new UploadCancelado());
    const aoAbortar = () => {
      clearTimeout(t);
      reject(new UploadCancelado());
    };
    // Mesma razão do enviarParte: o listener sai quando o backoff termina, senão cada retry
    // deixa um pendurado no controller pelo resto do upload.
    const t = setTimeout(() => {
      sinal.removeEventListener("abort", aoAbortar);
      resolve();
    }, ms);
    sinal.addEventListener("abort", aoAbortar, { once: true });
  });

export type OpcoesUploadDireto = {
  file: File;
  vinhetaId: string;
  sinal: AbortSignal;
  onProgresso?: (p: ProgressoUpload) => void;
  /** Chamado assim que o servidor devolve o token, pra a UI poder cancelar de verdade. */
  onToken?: (token: string) => void;
};

/**
 * Sobe o arquivo e devolve o job criado. Lança UploadCancelado se o sinal abortar.
 *
 * Devolve null (em vez de lançar) quando o servidor responde 404 no /init: é o kill switch
 * PODCAST_UPLOAD_DIRETO desligado, e aí o chamador cai no caminho antigo. Usar o próprio 404
 * como sinal evita uma rota nova só pra publicar o valor de uma flag.
 */
export async function uploadDireto(
  opts: OpcoesUploadDireto,
): Promise<{ job_id: string } | null> {
  const { file, vinhetaId, sinal, onProgresso, onToken } = opts;
  const t0 = Date.now();

  // Sinal INTERNO: o externo é o do usuário (botão Cancelar); este também dispara quando um
  // worker desiste, pra derrubar os outros junto. Os PUTs escutam o interno, que é encadeado
  // no externo, então cancelar de fora continua parando tudo.
  const interno = new AbortController();
  const propagar = () => interno.abort();
  sinal.addEventListener("abort", propagar, { once: true });

  let plano;
  try {
    plano = await api.podcastUploadInit({
      filename: file.name,
      size: file.size,
      vinheta_id: vinhetaId,
    });
  } catch (e) {
    // 404 = kill switch desligado (o backend marca `upload_direto: false` no corpo). Sem esse
    // marcador, um 404 de outra causa (deploy fora de ordem, com o front novo e o backend
    // antigo) rebaixaria todo mundo em silêncio pro caminho de 21% de sucesso.
    if (e instanceof ApiError && e.status === 404 && (e.data as { upload_direto?: boolean })
        ?.upload_direto === false) {
      return null;
    }
    throw e;
  }
  onToken?.(plano.token);

  const { token, part_size, part_count, concurrency, lote } = plano;

  // Bytes enviados POR PARTE, não um acumulador: um retry reenvia a parte do zero, e somar
  // direto faria a barra passar de 100% e a estimativa de tempo mentir.
  const enviadoPorParte = new Map<number, number>();
  const feitas: ParteFeita[] = [];
  let retries = 0;

  const publicar = () => {
    let enviado = 0;
    enviadoPorParte.forEach((v) => (enviado += v));
    onProgresso?.({
      enviado: Math.min(enviado, file.size),
      total: file.size,
      pct: file.size ? Math.min(100, Math.round((enviado / file.size) * 100)) : 0,
      partesConcluidas: feitas.length,
      partesTotal: part_count,
    });
  };

  // Cache de presignadas, preenchido em LOTES. Emitir as 960 de uma vez daria centenas de KB
  // de JSON e obrigaria validade longa demais numa credencial de escrita.
  const urls = new Map<number, string>();
  let pedindo: Promise<void> | null = null;

  async function urlDaParte(n: number, renovar = false): Promise<string> {
    if (!renovar) {
      const cache = urls.get(n);
      if (cache) return cache;
    }
    // Uma requisição de lote por vez: com concorrência > 1, várias partes descobrem a falta
    // da URL ao mesmo tempo e sem isto o cliente dispararia N pedidos idênticos.
    while (pedindo) await pedindo;
    if (!renovar && urls.has(n)) return urls.get(n)!;

    const numeros: number[] = [];
    for (let i = n; i < Math.min(n + lote, part_count + 1); i++) numeros.push(i);
    // Retry próprio, e não emprestado do retry da parte: um /parts que falha é esperado por
    // TODOS os workers parados no mutex, e sem isto uma única falha de lote consumiria de uma
    // vez uma tentativa de cada parte em espera, derrubando o upload inteiro em segundos.
    const pedirLote = async () => {
      let erro: unknown;
      for (let i = 0; i < 3; i++) {
        try {
          const r = await api.podcastUploadParts(token, numeros);
          Object.entries(r.urls).forEach(([k, v]) => urls.set(Number(k), v as string));
          return;
        } catch (e) {
          if (e instanceof ApiError && e.status >= 400 && e.status < 500) throw e;
          erro = e;
          if (i < 2) await dorme(BACKOFF_MS[i], interno.signal);
        }
      }
      throw erro;
    };
    pedindo = pedirLote().finally(() => {
      pedindo = null;
    });
    await pedindo;
    const u = urls.get(n);
    if (!u) throw new ApiError("O servidor não devolveu a URL da parte " + n + ".", 0);
    return u;
  }

  async function processarParte(n: number) {
    const inicio = (n - 1) * part_size;
    const corpo = file.slice(inicio, Math.min(inicio + part_size, file.size));
    let ultimoErro: unknown;

    for (let tentativa = 0; tentativa < TENTATIVAS_POR_PARTE; tentativa++) {
      if (interno.signal.aborted) {
        // Distingue cancelamento do usuário de derrubada interna por falha de outro worker:
        // no segundo caso o erro que interessa ao usuário é o do worker que falhou.
        if (sinal.aborted) throw new UploadCancelado();
        throw new ApiError("Envio interrompido.", 0);
      }
      try {
        // A partir da 2a tentativa a presignada é RENOVADA: num upload de horas com URLs de
        // 2h, a causa mais provável de 403 numa parte é a URL ter expirado enquanto ela
        // esperava a vez na fila. Reusar a mesma URL faria as 4 tentativas falharem igual.
        const url = await urlDaParte(n, tentativa > 0);
        const etag = await enviarParte(url, corpo, interno.signal, (b) => {
          enviadoPorParte.set(n, b);
          publicar();
        });
        enviadoPorParte.set(n, corpo.size);
        feitas.push({ number: n, etag });
        publicar();
        return;
      } catch (e) {
        if (e instanceof UploadCancelado) throw e;
        ultimoErro = e;
        retries++;
        enviadoPorParte.set(n, 0); // o retry reenvia do começo; não conte o que se perdeu
        publicar();
        if (tentativa < TENTATIVAS_POR_PARTE - 1) {
          await dorme(BACKOFF_MS[Math.min(tentativa, BACKOFF_MS.length - 1)], interno.signal);
        }
      }
    }
    throw ultimoErro instanceof Error
      ? ultimoErro
      : new ApiError("Não consegui enviar a parte " + n + ".", 0);
  }

  // Pool de workers: cada um puxa o próximo número da fila. A concorrência vem do SERVIDOR
  // (config.PODCAST_UPLOAD_CONCURRENCY), começando em 1, pra dar pra ajustar sem rebuild do
  // Next: o número certo se descobre medindo.
  const n_workers = Math.min(Math.max(1, concurrency), CONCORRENCIA_MAX);
  let proxima = 1;
  let falhou = false;
  const trabalhadores = Array.from({ length: n_workers }, async () => {
    for (;;) {
      if (falhou || interno.signal.aborted) return;
      const n = proxima++;
      if (n > part_count) return;
      await processarParte(n);
    }
  });

  try {
    await Promise.all(trabalhadores);
  } catch (e) {
    // Promise.all rejeita no PRIMEIRO erro, mas os outros workers continuariam empurrando GB
    // pro bucket depois de a tela já ter mostrado a falha, e as rejeições deles virariam
    // unhandled rejections. O abort interno para o pool inteiro de uma vez.
    falhou = true;
    interno.abort();
    // E o multipart morre junto. Sem isto, cada falha deixava as partes já enviadas COBRADAS e
    // invisíveis no list_objects_v2 até o lifecycle de 7 dias, e o usuário que clicasse em
    // enviar de novo tornava o upload anterior inalcançável (o token era sobrescrito). Só o
    // botão Cancelar limpava, e o cabeçalho deste arquivo prometia que o erro limpava também.
    // Cancelamento do usuário NÃO passa por aqui: quem cancelou já chamou o /abort.
    if (!sinal.aborted) await api.podcastUploadAbort(token);
    throw e;
  }

  if (sinal.aborted) throw new UploadCancelado();

  // O complete TEM retry, e não é simetria gratuita com as partes: aqui todos os bytes já
  // subiram. Perder a resposta (a janela de instabilidade de TCP desta VPS, um 502 do nginx,
  // um restart) deixaria um objeto de até 15GB montado no bucket pago SEM linha no banco, e
  // nada o ceifa: o lifecycle de 7 dias só coleta multipart INCOMPLETO, e o delete de projeto
  // só chega por job_id. O servidor documenta esta rota como idempotente exatamente pra que
  // este retry seja seguro; sem ele a idempotência nunca é exercitada.
  let ultimo: unknown;
  for (let tentativa = 0; tentativa < 3; tentativa++) {
    try {
      const res = await api.podcastUploadComplete(token, feitas, {
        ms: Date.now() - t0,
        concorrencia: n_workers,
        retries,
      });
      return { job_id: res.job_id };
    } catch (e) {
      // 4xx é veredito do servidor (não é 16:9, tamanho divergente, envio expirado): repetir
      // só atrasa a mensagem que o usuário precisa ler.
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) throw e;
      ultimo = e;
      if (tentativa < 2) await dorme(BACKOFF_MS[tentativa], sinal);
    }
  }
  // Sem abort aqui de propósito: o complete PODE ter dado certo no S3 e só a resposta ter se
  // perdido. Abortar destruiria um upload bem-sucedido. Se ele não foi montado, o lifecycle
  // de 7 dias coleta; se foi, o objeto fica órfão e é o preço de não arriscar o pior erro.
  throw ultimo instanceof Error ? ultimo : new ApiError("Não consegui finalizar o envio.", 0);
}
