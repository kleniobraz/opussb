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
  clips?: Clip[];
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

export interface Captions {
  editable: boolean;
  segments?: Segment[];
  music?: Music | null;
  caption_y?: number | null; // posição vertical (fração 0..1); null = padrão SB
  style?: CaptionStyle | null; // estilo da fonte; null = padrão SB
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
