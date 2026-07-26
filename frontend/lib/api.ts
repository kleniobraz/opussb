// Cliente de API tipado. Mesma origem (Nginx em prod, rewrite do Next em dev), entao o
// cookie de sessao flui sem CORS/token. Em 401, dispara um evento global pra casca derrubar
// pro login, e lanca AuthError.
import type {
  AdminCredits,
  AdminUser,
  CaptionReview,
  CaptionReviewSummary,
  Captions,
  CaptionStyle,
  Clip,
  GlossaryTerm,
  CropKeyframe,
  CropKeyframes,
  EndScreenOption,
  RerenderStatus,
  JobStatus,
  LoginResponse,
  Me,
  Music,
  PodcastHookChoice,
  PodcastHooksResponse,
  Preferences,
  Project,
  Session,
  Segment,
  SignupResponse,
  Stats,
  UploadOptions,
  UploadResponse,
  Vinheta,
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

// Devolve o JSON (e LEVANTA ApiError com a mensagem do backend em falha), em vez do
// booleano de antes: desde que a exclusão passou a abortar quando o S3 não responde, o
// usuário precisa saber que o projeto CONTINUA existindo. Com `.then(r => r.ok)` toda falha
// virava um "false" mudo e a tela mostrava um texto genérico.
export const deleteProject = (id: string) =>
  request("/api/projects/" + encodeURIComponent(id), { method: "DELETE" }).then((r) =>
    asJson<{ ok?: boolean }>(r),
  );

export const cancelJob = (id: string) =>
  request("/api/jobs/" + encodeURIComponent(id) + "/cancel", { method: "POST" }).then((r) => r.ok);

export const reprocess = (id: string, params: Record<string, unknown>) =>
  postJson<{ job_id?: string }>("/api/projects/" + encodeURIComponent(id) + "/reprocess", params);

export const downloadAllUrl = (id: string) =>
  "/api/projects/" + encodeURIComponent(id) + "/download_all";

// Tela final padrão do projeto: null = nenhuma, "<id>" = uma da biblioteca. Os clipes herdam.
export const putProjectEndScreen = (id: string, endScreen: string | null) =>
  request("/api/projects/" + encodeURIComponent(id) + "/end_screen", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ end_screen: endScreen }),
  }).then((r) => asJson<{ ok?: boolean; end_screen?: string | null }>(r));

// ── Editor: legenda + musica ────────────────────────────────────────────────
export const getCaptions = (id: string) =>
  getJson<Captions>("/api/clips/" + encodeURIComponent(id) + "/captions");
export const putCaptions = (
  id: string,
  segments: Segment[],
  captionY: number | null = null,
  style: CaptionStyle | null = null,
  cut: { in: number; out: number } | null = null, // pontos de corte (handles); omitido = não mexe
) =>
  request("/api/clips/" + encodeURIComponent(id) + "/captions", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      segments,
      caption_y: captionY,
      style,
      ...(cut ? { in: cut.in, out: cut.out } : {}),
    }),
  }).then((r) => asJson<{ ok?: boolean }>(r));

// Crop por keyframe: lê os keyframes salvos (mode/kf/auto) + o estado do último re-render.
export const getKeyframes = (id: string) =>
  getJson<{ keyframes: CropKeyframes | null; rerender: RerenderStatus }>(
    "/api/clips/" + encodeURIComponent(id) + "/keyframes",
  );
// Salva os keyframes do crop e dispara o re-render do base 9:16 (async). Manual: {kf}. Voltar ao
// automático: {reset:true}. Responde 202 com o estado inicial; faça poll no getKeyframes até done/error.
export const putKeyframes = (
  id: string,
  body: { kf: CropKeyframe[] } | { reset: true },
) =>
  request("/api/clips/" + encodeURIComponent(id) + "/keyframes", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => asJson<{ ok?: boolean; mode?: "auto" | "manual"; rerender?: RerenderStatus }>(r));

// Biblioteca de telas finais disponíveis (id + rótulo + duração).
export const getEndScreens = () =>
  getJson<{ end_screens: EndScreenOption[] }>("/api/end_screens");

// Sobe uma tela final nova (mp4 até 10s) pra a biblioteca. Retorna a opção criada.
export function uploadEndScreen(file: File, label?: string): Promise<{ ok?: boolean; end_screen?: EndScreenOption }> {
  const fd = new FormData();
  fd.append("file", file);
  if (label) fd.append("label", label);
  return request("/api/end_screens", { method: "POST", body: fd }).then((r) =>
    asJson<{ ok?: boolean; end_screen?: EndScreenOption }>(r),
  );
}

// Remove uma tela final da biblioteca (e o asset). Projetos/clipes que a usavam degradam pra nenhuma.
export const deleteEndScreen = (id: string) =>
  request("/api/end_screens/" + encodeURIComponent(id), { method: "DELETE" }).then((r) => r.ok);

// Override da tela final do clipe: end_screen (null=herda, "none"=remove, "<id>"=força) e/ou dur
// (encurtar: s, ou null = tela inteira). Não re-queima a legenda (só refaz o anexo).
export const putClipEndScreen = (
  id: string,
  patch: { end_screen?: string | null; dur?: number | null },
) =>
  request("/api/clips/" + encodeURIComponent(id) + "/end_screen", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }).then((r) =>
    asJson<{ ok?: boolean; end_screen?: string | null; end_screen_effective?: string | null; end_screen_dur?: number | null }>(r),
  );

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
export const adminCredits = (refresh = false) =>
  getJson<AdminCredits>("/api/admin/credits" + (refresh ? "?refresh=1" : ""));
export const adminApprove = (email: string) =>
  postJson<{ ok?: boolean }>("/api/admin/approve", { email });
export const adminReject = (email: string) =>
  postJson<{ ok?: boolean }>("/api/admin/reject", { email });
export const adminSetActive = (email: string, active: boolean) =>
  postJson<{ ok?: boolean }>("/api/admin/set_active", { email, active });
export const adminSetAdmin = (email: string, is_admin: boolean) =>
  postJson<{ ok?: boolean }>("/api/admin/set_admin", { email, is_admin });
export const adminDelete = (email: string) =>
  postJson<{ ok?: boolean }>("/api/admin/delete", { email });
export const adminMaintenance = () =>
  getJson<{ on: boolean; client_ip: string | null; ip_allowed: boolean; allowlist: string[] }>(
    "/api/admin/maintenance",
  );
export const adminSetMaintenance = (on: boolean) =>
  postJson<{ ok?: boolean; on: boolean }>("/api/admin/maintenance", { on });

// ── Revisor de legendas ────────────────────────────────────────────────────
// Glossário: list/create/update p/ qualquer usuário ativo; delete só admin (403 no back).
export const glossaryList = () => getJson<{ terms: GlossaryTerm[] }>("/api/glossary");
export const glossaryCreate = (t: { term: string; category?: string; notes?: string }) =>
  postJson<{ ok?: boolean; term: GlossaryTerm }>("/api/glossary", t);
export const glossaryUpdate = (
  id: string,
  t: { term: string; category?: string; notes?: string },
) =>
  request("/api/glossary/" + encodeURIComponent(id), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(t),
  }).then((r) => asJson<{ ok?: boolean; term: GlossaryTerm }>(r));
export const glossaryDelete = (id: string) =>
  request("/api/glossary/" + encodeURIComponent(id), { method: "DELETE" }).then((r) =>
    asJson<{ ok?: boolean }>(r),
  );

// ── Podcast: aprovação de gancho (§39 Parte 4) ──────────────────────────────
// Biblioteca de vinhetas (id + rótulo + duração), escolhida no upload do podcast.
export const getVinhetas = () => getJson<{ vinhetas: Vinheta[] }>("/api/vinhetas");

// Upload do episódio (multipart: video + vinheta_id) com progresso real (mesmo padrão do
// uploadWithProgress de clipes, mas sem os campos de min/max/aspect/idioma/legenda, que não
// existem no fluxo de podcast). Estaciona o job em status 'uploaded'.
export function podcastUploadWithProgress(
  file: File,
  vinhetaId: string,
  onProgress?: (p: { loaded: number; total: number }) => void,
): Promise<UploadResponse & { status?: string }> {
  const fd = new FormData();
  fd.append("video", file);
  fd.append("vinheta_id", vinhetaId);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/podcast/upload");
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
        resolve(data as UploadResponse & { status?: string });
      } else {
        reject(new ApiError(String(err || "HTTP " + xhr.status), xhr.status, data));
      }
    };
    xhr.onerror = () => reject(new ApiError("Falha de rede no upload.", 0));
    xhr.ontimeout = () => reject(new ApiError("Tempo esgotado no upload.", 0));
    xhr.send(fd);
  });
}

// Dispara a transcrição do job estacionado (pode devolver 429 se a fila estiver ocupada).
export const podcastTranscribe = (id: string) =>
  postJson<{ job_id: string; status: string }>(
    "/api/podcast/" + encodeURIComponent(id) + "/transcribe",
  );

// Corte manual do início do corpo bruto, ANTES da transcrição (cut_at em segundos, corpo
// original preservado até o corte terminar com sucesso). 202 e o job vai pra
// 'cortando_inicio'; acompanhar pelo podcastHooks (polling).
export const podcastCutIntro = (id: string, cutAt: number) =>
  postJson<{ job_id: string; status: string }>(
    "/api/podcast/" + encodeURIComponent(id) + "/cut-intro",
    { cut_at: cutAt },
  );

export const podcastHooks = (id: string) =>
  getJson<PodcastHooksResponse>("/api/podcast/" + encodeURIComponent(id) + "/hooks");
// URL do 16:9 original servido com HTTP Range (preview do trim toca direto no <video>).
export const podcastSourceUrl = (id: string) =>
  "/api/podcast/" + encodeURIComponent(id) + "/source";
export const podcastApprove = (id: string, choice: { index: number; start: number; end: number }) =>
  postJson<{ job_id: string; status: string; hook_choice: PodcastHookChoice }>(
    "/api/podcast/" + encodeURIComponent(id) + "/approve",
    choice,
  );
// Dispara a montagem (gancho + vinheta + podcast). 202; acompanhar pelo podcastHooks (polling).
export const podcastCompose = (id: string) =>
  postJson<{ job_id: string; status: string }>(
    "/api/podcast/" + encodeURIComponent(id) + "/compose",
  );
// URL do episódio montado (302 presignado). download=true força attachment com nome amigável.
export const podcastFinalUrl = (id: string, download = false) =>
  "/api/podcast/" + encodeURIComponent(id) + "/final" + (download ? "?download=1" : "");

// Dispara a extração assíncrona do MP3 do episódio já montado (idempotente: 202 se
// disparou/já está rodando, 200 se o cache já está pronto). Acompanhar pelo audio_status
// que vem junto do podcastHooks (mesma query, sem endpoint novo pro front consultar).
export const podcastAudioExtract = (id: string) =>
  postJson<{ job_id: string; audio_status: string }>(
    "/api/podcast/" + encodeURIComponent(id) + "/audio",
  );
// URL do MP3 do episódio (302 presignado), mesmo padrão de podcastFinalUrl.
export const podcastAudioUrl = (id: string, download = false) =>
  "/api/podcast/" + encodeURIComponent(id) + "/audio" + (download ? "?download=1" : "");

// Revisão: sobe o vídeo (FormData), depois faz polling do status até done/error.
export function captionReviewCreate(file: File): Promise<{ review_id: string }> {
  const fd = new FormData();
  fd.append("video", file);
  return request("/api/caption-review", { method: "POST", body: fd }).then((r) =>
    asJson<{ review_id: string }>(r),
  );
}
export const captionReviewStatus = (id: string) =>
  getJson<CaptionReview>("/api/caption-review/" + encodeURIComponent(id));
export const captionReviewList = () =>
  getJson<{ reviews: CaptionReviewSummary[] }>("/api/caption-review");
