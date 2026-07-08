// Formatadores e helpers de exibicao (portados do antigo ui.js / app.js).

export function fmtBytes(bytes?: number | null): string {
  if (bytes == null || isNaN(bytes)) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  return (n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)) + " " + u[i];
}

export function fmtScore(score?: number | null): string {
  if (score == null) return "";
  const r = Math.round(score * 10) / 10;
  return r % 1 === 0 ? String(r) : r.toFixed(1);
}

export function scoreClass(score?: number | null): string {
  if (score == null) return "score-low";
  if (score >= 8) return "score-high";
  if (score >= 6) return "score-mid";
  return "score-low";
}

export function fmtEta(sec: number): string {
  if (!isFinite(sec) || sec < 0) return "—";
  const s = Math.round(sec);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m} min ${r} s` : `${m} min`;
}

export function fmtSeconds(s?: number | null): string {
  const sec = Math.round(s || 0);
  const m = Math.floor(sec / 60);
  const ss = sec % 60;
  return m + ":" + (ss < 10 ? "0" : "") + ss;
}

export function timeAgo(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const sec = Math.floor((Date.now() - t) / 1000);
  if (sec < 60) return "agora";
  const min = Math.floor(sec / 60);
  if (min < 60) return "há " + min + " min";
  const h = Math.floor(min / 60);
  if (h < 24) return "há " + h + " h";
  const days = Math.floor(h / 24);
  if (days < 30) return "há " + days + " d";
  return new Date(t).toLocaleDateString("pt-BR");
}

export function firstNameFromEmail(email?: string | null): string {
  if (!email) return "você";
  const local = email.split("@")[0] || "";
  const token = local.split(/[._-]+/).filter(Boolean)[0] || local;
  if (!token) return "você";
  return token.charAt(0).toUpperCase() + token.slice(1).toLowerCase();
}

// Nome de exibição a partir do email ("klenio.braz" -> "Klenio Braz"). Vazio se sem email.
export function nameFromEmail(email?: string | null): string {
  if (!email) return "";
  const local = email.split("@")[0] || "";
  const parts = local.split(/[._-]+/).filter(Boolean);
  if (!parts.length) return local;
  return parts.map((p) => p.charAt(0).toUpperCase() + p.slice(1).toLowerCase()).join(" ");
}

export function initialsFromEmail(email?: string | null): string {
  if (!email) return "SB";
  const local = email.split("@")[0] || "";
  const parts = local.split(/[._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return local.slice(0, 2).toUpperCase() || "SB";
}

export function greetingForHour(h: number): string {
  if (h >= 5 && h < 12) return "Bom dia";
  if (h >= 12 && h < 18) return "Boa tarde";
  return "Boa noite";
}

// Hash estavel (FNV-1a) p/ as capas generativas de projeto (onda + matiz).
export function hashStr(s?: string | null): number {
  let h = 2166136261;
  const str = String(s || "");
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = (h * 16777619) >>> 0;
  }
  return h >>> 0;
}
