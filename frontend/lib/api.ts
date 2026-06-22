// Cliente de API tipado. Mesma origem (Nginx em prod, rewrite do Next em dev), entao o
// cookie de sessao flui sem CORS/token. Em 401, dispara um evento global pra casca derrubar
// pro login, e lanca AuthError.
import type {
  AdminUser,
  Captions,
  CaptionStyle,
  Clip,
  JobStatus,
  LoginResponse,
  Me,
  Music,
  Preferences,
  Project,
  Session,
  Segment,
  SignupResponse,
  Stats,
  UploadOptions,
  UploadResponse,
} from "./types";

export class AuthError extends Error {
  status = 401;
  constructor(message = "auth") {
    super(message);
    this.name = "AuthError";
  }
}

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

export const AUTH_EVENT = "opussb:auth-expired";

async function request(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (res.status === 401) {
    if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EVENT));
    throw new AuthError();
  }
  return res;
}

async function asJson<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg =
      (data && typeof data === "object" && "error" in data && (data as { error?: string }).error) ||
      "HTTP " + res.status;
    throw new ApiError(String(msg), res.status, data);
  }
  return data as T;
}

function getJson<T>(path: string): Promise<T> {
  return request(path, { headers: { Accept: "application/json" } }).then((r) => asJson<T>(r));
}

function postJson<T>(path: string, body?: unknown): Promise<T> {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  }).then((r) => asJson<T>(r));
}

// ── Auth ────────────────────────────────────────────────────────────────
export const me = () => getJson<Me>("/api/me");
export const login = (email: string, password: string) =>
  postJson<LoginResponse>("/api/login", { email, password });
export const logout = () => request("/api/logout", { method: "POST" }).then(() => undefined);
export const changePassword = (current_password: string, new_password: string) =>
  postJson<{ ok?: boolean }>("/api/change_password", { current_password, new_password });

// ── Upload / status ───────────────────────────────────────────────────────
export function upload(file: File, opts: UploadOptions): Promise<UploadResponse> {
  const fd = new FormData();
  fd.append("video", file);
  fd.append("min_duration", String(opts.minDuration));
  fd.append("max_duration", String(opts.maxDuration));
  if (opts.aspectRatio) fd.append("aspect_ratio", opts.aspectRatio);
  if (opts.language) fd.append("language", opts.language);
  fd.append("captions", opts.captions ? "true" : "false");
  return request("/api/upload", { method: "POST", body: fd }).then((r) => asJson<UploadResponse>(r));
}

// Mesma geração de clipes a partir de um link do YouTube (VOD). O servidor baixa via yt-dlp
// e cai no mesmo pipeline. Devolve o job_id + o título do vídeo (pro nome do projeto).
export function uploadYoutube(
  url: string,
  opts: UploadOptions,
): Promise<UploadResponse & { title?: string }> {
  return postJson<UploadResponse & { title?: string }>("/api/upload/youtube", {
    url,
    min_duration: opts.minDuration,
    max_duration: opts.maxDuration,
    aspect_ratio: opts.aspectRatio,
    language: opts.language,
    captions: opts.captions,
  });
}

// Upload com progresso real (XHR expõe upload.onprogress; o fetch não). Mesma origem,
// então o cookie de sessão vai junto. Mantém a semântica de erro/401 do resto da API.
export function uploadWithProgress(
  file: File,
  opts: UploadOptions,
  onProgress?: (p: { loaded: number; total: number }) => void,
): Promise<UploadResponse> {
  const fd = new FormData();
  fd.append("video", file);
  fd.append("min_duration", String(opts.minDuration));
  fd.append("max_duration", String(opts.maxDuration));
  if (opts.aspectRatio) fd.append("aspect_ratio", opts.aspectRatio);
  if (opts.language) fd.append("language", opts.language);
  fd.append("captions", opts.captions ? "true" : "false");

  return new Promise<UploadResponse>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress({ loaded: e.loaded, total: e.total });
    };
    xhr.onload = () => {
      let data: unknown = {};
      try {
        data = JSON.parse(xhr.responseText || "{}");
      } catch {
        /* resposta sem JSON */
      }
      const err = (data as { error?: string })?.error;
      if (xhr.status === 401) {
        if (typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EVENT));
        reject(new AuthError());
      } else if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data as UploadResponse);
      } else {
        reject(new ApiError(String(err || "HTTP " + xhr.status), xhr.status, data));
      }
    };
    xhr.onerror = () => reject(new ApiError("Falha de rede no upload.", 0));
    xhr.ontimeout = () => reject(new ApiError("Tempo esgotado no upload.", 0));
    xhr.send(fd);
  });
}

export const status = (jobId: string) =>
  getJson<JobStatus>("/api/status/" + encodeURIComponent(jobId));

// ── Projetos / stats / favoritos ────────────────────────────────────────────
export const getStats = () => getJson<Stats>("/api/stats");
export const getProjects = () => getJson<{ projects: Project[]; total?: number }>("/api/projects");
export const getProject = (id: string) =>
  getJson<Project>("/api/projects/" + encodeURIComponent(id));
export const getFavorites = () => getJson<{ clips: Clip[] }>("/api/clips/favorites");

export const favoriteClip = (id: string, on: boolean) =>
  request("/api/clips/" + encodeURIComponent(id) + "/favorite", {
    method: on ? "POST" : "DELETE",
  }).then((r) => r.ok);

export const deleteProject = (id: string) =>
  request("/api/projects/" + encodeURIComponent(id), { method: "DELETE" }).then((r) => r.ok);

export const cancelJob = (id: string) =>
  request("/api/jobs/" + encodeURIComponent(id) + "/cancel", { method: "POST" }).then((r) => r.ok);

export const reprocess = (id: string, params: Record<string, unknown>) =>
  postJson<{ job_id?: string }>("/api/projects/" + encodeURIComponent(id) + "/reprocess", params);

export const downloadAllUrl = (id: string) =>
  "/api/projects/" + encodeURIComponent(id) + "/download_all";

// ── Editor: legenda + musica ────────────────────────────────────────────────
export const getCaptions = (id: string) =>
  getJson<Captions>("/api/clips/" + encodeURIComponent(id) + "/captions");
export const putCaptions = (
  id: string,
  segments: Segment[],
  captionY: number | null = null,
  style: CaptionStyle | null = null,
) =>
  request("/api/clips/" + encodeURIComponent(id) + "/captions", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segments, caption_y: captionY, style }),
  }).then((r) => asJson<{ ok?: boolean }>(r));

export function uploadMusic(id: string, file: File, volume?: number): Promise<{ music?: Music }> {
  const fd = new FormData();
  fd.append("music", file);
  if (volume != null) fd.append("volume", String(volume));
  return request("/api/clips/" + encodeURIComponent(id) + "/music", {
    method: "POST",
    body: fd,
  }).then((r) => asJson<{ music?: Music }>(r));
}
export const setMusicVolume = (id: string, volume: number) =>
  request("/api/clips/" + encodeURIComponent(id) + "/music", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ volume }),
  }).then((r) => asJson<{ ok?: boolean }>(r));
export const removeMusic = (id: string) =>
  request("/api/clips/" + encodeURIComponent(id) + "/music", { method: "DELETE" }).then((r) =>
    asJson<{ ok?: boolean }>(r),
  );

// ── Preferencias / sessoes ──────────────────────────────────────────────────
export const getPreferences = () => getJson<Partial<Preferences>>("/api/preferences");
export const putPreferences = (prefs: Partial<Preferences>) =>
  request("/api/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(prefs),
  }).then((r) => r.ok);

export const getSessions = () => getJson<{ sessions: Session[] }>("/api/sessions");
export const revokeSession = (id: string) =>
  request("/api/sessions/" + encodeURIComponent(id), { method: "DELETE" }).then((r) =>
    asJson<{ ok?: boolean }>(r),
  );
export const revokeAllSessions = () => postJson<{ ok?: boolean }>("/api/sessions/revoke_all");

// ── Cadastro / providers de login ─────────────────────────────────────────────
export const authProviders = () => getJson<{ zoho: boolean }>("/api/auth/providers");
export const signup = (email: string, password: string) =>
  postJson<SignupResponse>("/api/signup", { email, password });

// ── Admin (gated por is_admin no servidor) ────────────────────────────────────
export const adminUsers = () => getJson<{ users: AdminUser[] }>("/api/admin/users");
export const adminApprove = (email: string) =>
  postJson<{ ok?: boolean }>("/api/admin/approve", { email });
export const adminReject = (email: string) =>
  postJson<{ ok?: boolean }>("/api/admin/reject", { email });
export const adminSetActive = (email: string, active: boolean) =>
  postJson<{ ok?: boolean }>("/api/admin/set_active", { email, active });
export const adminSetAdmin = (email: string, is_admin: boolean) =>
  postJson<{ ok?: boolean }>("/api/admin/set_admin", { email, is_admin });
