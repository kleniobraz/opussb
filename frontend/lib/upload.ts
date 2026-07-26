// Validação compartilhada do upload de vídeo (clipes e podcast usam o mesmo limite/extensões).
export const ACCEPT_EXT = ["mp4", "mov", "avi", "mkv", "webm"];
export const MAX_BYTES = 15 * 1024 * 1024 * 1024; // 15GB

export function extOk(name: string): boolean {
  const m = /\.([a-z0-9]+)$/i.exec(name || "");
  return !!m && ACCEPT_EXT.includes(m[1].toLowerCase());
}

export type MetaVideo = { width: number; height: number; duration: number };

/**
 * Lê largura, altura e duração do arquivo LOCAL, sem enviar byte nenhum.
 *
 * Vale porque o navegador só precisa do cabeçalho pra responder isso (é o mesmo moov que o
 * ffprobe lê por range no servidor), então custa milissegundos mesmo num arquivo de 8GB.
 *
 * Devolve null quando o navegador não sabe decodificar o container (mkv e avi são o caso
 * comum) ou quando o metadado não carrega. NULL NÃO É REPROVAÇÃO: quem valida de verdade é o
 * servidor, no /complete. Isto aqui é só pra poupar o usuário de descobrir depois de 40
 * minutos de envio que o arquivo não era 16:9.
 */
export function lerMetaVideo(file: File, timeoutMs = 8000): Promise<MetaVideo | null> {
  return new Promise((resolve) => {
    if (typeof document === "undefined") return resolve(null);
    const url = URL.createObjectURL(file);
    const v = document.createElement("video");
    let pronto = false;
    const fim = (r: MetaVideo | null) => {
      if (pronto) return;
      pronto = true;
      clearTimeout(t);
      v.removeAttribute("src");
      v.load();
      URL.revokeObjectURL(url);
      resolve(r);
    };
    const t = setTimeout(() => fim(null), timeoutMs);
    v.preload = "metadata";
    v.muted = true;
    v.onloadedmetadata = () =>
      fim(
        v.videoWidth && v.videoHeight
          ? { width: v.videoWidth, height: v.videoHeight, duration: v.duration || 0 }
          : null,
      );
    v.onerror = () => fim(null);
    v.src = url;
  });
}

// Mesma tolerância do servidor (app._probe_podcast_video usa 2% sobre 16/9): duas checagens
// com limiares diferentes rejeitariam aqui um arquivo que o servidor aceitaria, ou pior, o
// contrário, deixando o usuário subir 8GB pra ser recusado no fim.
export function ehDezesseisPorNove(m: MetaVideo): boolean {
  return Math.abs(m.width / m.height - 16 / 9) <= (16 / 9) * 0.02;
}
