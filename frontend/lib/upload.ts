// Validação compartilhada do upload de vídeo (clipes e podcast usam o mesmo limite/extensões).
export const ACCEPT_EXT = ["mp4", "mov", "avi", "mkv", "webm"];
export const MAX_BYTES = 15 * 1024 * 1024 * 1024; // 15GB

export function extOk(name: string): boolean {
  const m = /\.([a-z0-9]+)$/i.exec(name || "");
  return !!m && ACCEPT_EXT.includes(m[1].toLowerCase());
}
