// Contrato de dados do OpusSB (espelha o backend Flask; ver BACKEND_CONTEXT.md).

export interface Me {
  email: string;
  must_change_password?: boolean;
  is_admin?: boolean;
  status?: string; // 'active' | 'pending' | 'rejected' | 'inactive'
}

export interface LoginResponse {
  email: string;
  must_change_password?: boolean;
  is_admin?: boolean;
  status?: string;
}

export interface SignupResponse {
  ok?: boolean;
  message?: string;
}

export interface AdminUser {
  email: string;
  status: string; // 'active' | 'pending' | 'rejected' | 'inactive'
  is_admin?: boolean;
  auth_provider?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

// Saldos/custos pro painel admin. runpod = saldo real (USD) + gasto/hora; anthropic = se a Admin
// key está configurada (saldo da Claude fica no Console por enquanto). null = falha ao buscar.
export interface AdminCredits {
  runpod?: { balance: number; spend_per_hr: number } | null;
  anthropic?: { configured: boolean } | null;
  cached?: boolean;
}

export interface UploadResponse {
  job_id: string;
}

export type JobState =
  | "queued"
  | "downloading"
  | "extracting_audio"
  | "uploading_audio"
  | "transcribing"
  | "cutting"
  | "reframing"
  | "uploading_clips"
  | "done"
  | "error";

export interface JobStatus {
  status: JobState | string;
  progress?: number;
  clips?: Clip[];
  partial_clips?: Clip[];
  error?: string;
}

export interface Clip {
  id: string;
  project_id?: string | null;
  url?: string;
  thumbnail_url?: string | null;
  title?: string | null;
  reason?: string | null;
  score?: number | null;
  duration?: number | null;
  favorite?: boolean;
  index?: number;
}

export interface Project {
  id: string;
  title?: string | null;
  filename?: string | null;
  created_at?: string;
  duration?: number | null;
  clip_count?: number | null;
  avg_score?: number | null;
  status?: string;
  thumbnail_url?: string | null;
  owner_email?: string | null; // quem subiu o vídeo (biblioteca compartilhada); null = legado
  end_screen?: string | null; // tela final padrão do projeto (id) ou null = nenhuma; clipes herdam
  job_type?: string; // 'clips' (default) ou 'podcast' (§39)
  clips?: Clip[];
}

// ── Podcast: aprovação de gancho (§39 Parte 4) ──────────────────────────────
// Vinheta da biblioteca (GET /api/vinhetas), escolhida no upload do podcast.
export interface Vinheta {
  id: string;
  label: string;
  duration?: number | null;
}

// Trecho candidato a abertura sugerido pelo hooks.py (o texto vem do SRT, não do modelo).
export interface PodcastHook {
  start: number;
  end: number;
  text: string;
  reason: string;
}

// Escolha humana gravada em jobs.hook_choice: qual gancho + as pontas aparadas.
export interface PodcastHookChoice {
  index: number;
  start: number;
  end: number;
  approved_by?: string;
  approved_at?: string;
}

export interface PodcastHooksResponse {
  job_id: string;
  status: string;
  progress?: number | null; // avança durante a montagem (status 'montando')
  filename?: string | null;
  hooks: PodcastHook[];
  hook_choice: PodcastHookChoice | null;
  intro_cut_at: number | null; // segundos já cortados do início do corpo bruto (null = sem corte)
  fps: number | null; // fps detectado no upload; null em jobs antigos sem a coluna, front assume 30
  source_available: boolean; // 16:9 ainda no servidor (preview e aprovação dependem dele)
  final_available?: boolean; // episódio montado no S3 (status 'done')
  error?: string | null; // mensagem quando status 'error' (retry = montar de novo)
  audio_status?: string | null; // "extraindo" | "done" | "error" | ausente (nunca pedido)
  audio_available?: boolean; // MP3 já extraído e no S3
}

// Uma opção da biblioteca de telas finais (outro anexado ao fim do clipe).
export interface EndScreenOption {
  id: string;
  label: string;
  duration?: number | null; // duração total do asset (s); usada pra calibrar o "encurtar"
}

export interface Stats {
  videos_processed?: number;
  clips_generated?: number;
  avg_score?: number;
  processing_count?: number;
  recent?: Project[];
}

export interface Session {
  id?: string;
  current?: boolean;
  browser?: string;
  os?: string;
  location?: string | null;
  last_active?: string;
}

export interface Segment {
  start: number;
  end: number;
  text: string;
}

// Tempo por PALAVRA (clip_words, §26): start/end em segundos relativos à janela do clipe.
// SOMENTE LEITURA, vem do GET /captions. Usado pro karaokê por palavra (roteiro + overlay);
// nunca é editado nem entra no save (o contrato de save segue só {start,end,text}).
export interface Word {
  start: number;
  end: number;
  word: string;
}

export interface Music {
  filename?: string;
  volume?: number;
}

// Personalização básica da fonte da legenda por clipe. Campos ausentes/null = padrão SB.
export interface CaptionStyle {
  size?: number; // tamanho da fonte (px na resolução 1080x1920); padrão 70
  tracking?: number; // espaçamento entre letras (px, \fsp); padrão -3
  color?: string; // cor do texto em #RRGGBB; padrão #FFFFFF
  shadow?: number; // intensidade da sombra (0..1 = opacidade do halo); padrão 0.86
}

// Janela/handles do clipe: o vídeo base tem uma sobra (margem) antes e depois do corte. O
// corte "oficial" que o usuário vê fica em [in, out] (segundos, relativos à janela), e ele
// arrasta as alças na timeline pra abrir/fechar dentro da sobra. window_dur = duração da janela.
export interface CaptionBounds {
  window_dur: number;
  in: number;
  out: number;
}

// Crop por keyframe (enquadramento manual). Cada keyframe = um instante t (s, relativo à janela do
// clipe) + cx (CENTRO horizontal do recorte 9:16, fração 0..1 da largura do 16:9). Só pan, sem zoom.
export interface CropKeyframe {
  t: number;
  cx: number;
}
// Registro persistido por clipe: mode (auto = trajetória da máquina; manual = ajustada pelo usuário),
// kf (a trajetória em uso), auto (o seed da máquina, preservado pra "voltar ao automático").
export interface CropKeyframes {
  mode: "auto" | "manual";
  kf: CropKeyframe[];
  auto?: CropKeyframe[] | null;
}
// Estado do re-render assíncrono do crop (regenera o base 9:16 a partir do 16:9 retido).
export interface RerenderStatus {
  state: "idle" | "rendering" | "done" | "error";
  error?: string | null;
}

export interface Captions {
  editable: boolean;
  segments?: Segment[];
  words?: Word[] | null; // tempo por palavra (só leitura); null = clipe legado/sem fala
  music?: Music | null;
  caption_y?: number | null; // posição vertical (fração 0..1); null = padrão SB
  style?: CaptionStyle | null; // estilo da fonte; null = padrão SB
  bounds?: CaptionBounds | null; // handles do corte; null = clipe sem margem (legado)
  keyframes?: CropKeyframes | null; // crop por keyframe; null = sem crop manual (usa o auto do base)
  crop_editable?: boolean; // true quando há o 16:9 retido + base re-renderizável (independe de legenda)
  // Tela final (outro) anexada ao fim. end_screen = override do clipe (null=herda, "none"=removida,
  // "<id>"=forçada); end_screen_effective = o que de fato será anexado (id ou null após herança);
  // project_end_screen = default do projeto; end_screens = biblioteca de opções.
  end_screen?: string | null;
  end_screen_effective?: string | null;
  end_screen_dur?: number | null; // duração da tela final neste clipe (s); null = tela inteira
  project_end_screen?: string | null;
  end_screens?: EndScreenOption[];
}

export interface Preferences {
  defaultMin: number;
  defaultMax: number;
  aspect: string;
  language: string;
  captions: boolean;
  autoplayPreview: boolean;
}

export interface UploadOptions {
  minDuration: number;
  maxDuration: number;
  aspectRatio?: string;
  language?: string;
  captions?: boolean;
}

// ── Revisor de legendas ─────────────────────────────────────────────────────
// Glossário editável de nomes canônicos (marcas/produtos/pessoas). Add/edit por
// qualquer usuário ativo; DELETE só admin (regra no backend).
export interface GlossaryTerm {
  id: string;
  term: string;
  category?: string | null;
  notes?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type CaptionReviewStatus =
  | "queued"
  | "extracting"
  | "reading"
  | "reviewing"
  | "done"
  | "error";

// Um achado da revisão (trecho lido + tipo do erro + sugestão).
export interface CaptionReviewFinding {
  trecho: string;
  origem: "legenda" | "grafico" | string;
  tipo: "ortografia" | "gramatica" | "marca" | "pontuacao" | "maiuscula" | "outro" | string;
  sugestao: string;
  explicacao: string;
  confianca: number;
}

export interface CaptionReviewGraphic {
  texto: string;
  tipo?: string;
}

// Status/resultado de uma revisão (GET /api/caption-review/<id>).
export interface CaptionReview {
  id: string;
  status: CaptionReviewStatus | string;
  progress?: number;
  filename?: string;
  duration?: number | null;
  frames_count?: number | null;
  cost_usd?: number | null;
  created_at?: string | null;
  legenda_fala?: string;
  graficos?: CaptionReviewGraphic[];
  findings?: CaptionReviewFinding[];
  tokens?: { read_in?: number; read_out?: number; review_in?: number; review_out?: number };
  error?: string;
}

// Item do histórico (GET /api/caption-review).
export interface CaptionReviewSummary {
  id: string;
  filename?: string;
  status: string;
  progress?: number;
  frames_count?: number | null;
  cost_usd?: number | null;
  error?: string | null;
  created_at?: string | null;
}
