"""
config.py — Single source of configuration for OpusSB.
Reads from .env via python-dotenv. Variables without defaults
raise KeyError if missing (os.environ["VAR"] semantics).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# S3 / Hetzner Object Storage
S3_ENDPOINT: str = os.environ["S3_ENDPOINT"]
S3_BUCKET: str = os.environ["S3_BUCKET"]
S3_ACCESS_KEY: str = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY: str = os.environ["S3_SECRET_KEY"]
# Prefixo de namespace pras keys do S3. Default "" = producao (keys em jobs/..., assets/...).
# O staging seta S3_KEY_PREFIX=staging/ pra ler/escrever TUDO sob staging/ no MESMO bucket, sem
# nunca tocar os objetos de producao (isolamento leve). Aplicado so na borda do s3_client, entao
# app.py/processor.py seguem construindo keys SEM prefixo. Se setado, deve terminar em "/".
S3_KEY_PREFIX: str = os.environ.get("S3_KEY_PREFIX", "")

# RunPod
RUNPOD_API_KEY: str = os.environ["RUNPOD_API_KEY"]
RUNPOD_ENDPOINT_ID: str = os.environ["RUNPOD_ENDPOINT_ID"]

# Claude / Anthropic
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
# Admin API key (sk-ant-admin...), OPCIONAL e diferente da ANTHROPIC_API_KEY: so pra o painel de
# admin puxar o relatorio de custo da org. Vazio = o painel mostra so a RunPod (e o saldo da Claude
# fica no Console). Quando o dono gerar a key, e so por aqui no .env.
ANTHROPIC_ADMIN_KEY: str = os.environ.get("ANTHROPIC_ADMIN_KEY", "")

# N8N
N8N_WEBHOOK_URL: str = os.environ["N8N_WEBHOOK_URL"]

# Sessão / login. SECRET_KEY é obrigatória de propósito: sem ela o app não sobe,
# evita rodar com chave default insegura e invalidar sessões a cada restart.
FLASK_SECRET_KEY: str = os.environ["FLASK_SECRET_KEY"]
SESSION_DAYS: int = int(os.environ.get("SESSION_DAYS", "7"))

# Flask
FLASK_PORT: int = int(os.environ.get("FLASK_PORT", "5001"))
MAX_CONCURRENT_JOBS: int = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
JOB_DIR: str = os.environ.get("JOB_DIR", "/opt/opussb/jobs")
# Acima da janela do Whisper (5-15min) com folga. Job preso passa disso vira error.
JOB_STALE_MINUTES: int = int(os.environ.get("JOB_STALE_MINUTES", "30"))

# Teto de queimas de legenda SIMULTÂNEAS no processo. A queima sob demanda (GET
# /api/clips/<id>/video) roda um ffmpeg libx264 que usa TODOS os núcleos; sem limite,
# abrir vários clipes de um job recém-terminado disparava N queimas em paralelo (uma por
# thread do gunicorn), saturava a VPS (3 vCPU) + RAM no swap e o /health timava (o site
# parecia fora do ar). Numa máquina de poucos núcleos, 1 por vez roda rápido e mantém a
# máquina viva. Env-tunável (subir só faz sentido em box com mais núcleos).
BURN_CONCURRENCY: int = int(os.environ.get("BURN_CONCURRENCY", "1"))
# Quanto uma requisição de vídeo espera por um slot de queima antes de desistir e servir a
# base SEM legenda (o clipe sempre toca; a próxima abertura re-tenta). Evita prender a
# thread do gunicorn indefinidamente quando a fila de queima está cheia.
BURN_QUEUE_TIMEOUT: float = float(os.environ.get("BURN_QUEUE_TIMEOUT", "120"))
# A queima usa ffmpeg/libx264, que SOZINHO satura todos os núcleos (300% numa VPS de 3 vCPU) e
# sufoca o web app quando um job termina e vários clipes são abertos (CPU a 300% por minutos -> site
# sem CPU pra responder). Pra o site continuar de pé DURANTE a queima: limita as threads do encoder
# (deixa >=1 núcleo livre) E roda o ffmpeg com baixa prioridade (nice), pra ele ceder CPU às
# requisições web. O semáforo (BURN_CONCURRENCY) já evita N queimas juntas; isto evita que UMA
# queima monopolize a CPU.
BURN_FFMPEG_THREADS: int = int(os.environ.get("BURN_FFMPEG_THREADS", "2"))   # default herdado da Hetzner (3 vCPU); na VPS atual (4 vCPU) sobra ~1 nucleo na queima — ver BACKEND §42
BURN_NICE: int = int(os.environ.get("BURN_NICE", "15"))                       # 0-19; maior = menor prioridade

# Poster (thumbnail) por clipe: 1 frame extraido sob demanda da base (seek por range, ~2s,
# cacheado no S3) pra o card do clipe nao ficar preto antes do play. Semaforo PROPRIO, leve (1
# frame, NAO um encode), separado do BURN; deixa o site de pe se varios cards pedirem poster de uma
# vez. THUMB_QUEUE_TIMEOUT = quanto a requisicao espera o slot antes de desistir (404 -> o card cai
# no fundo atual, sem prender a thread). NAO dispara a queima da legenda (essa so acontece no play).
THUMB_CONCURRENCY: int = int(os.environ.get("THUMB_CONCURRENCY", "2"))
THUMB_QUEUE_TIMEOUT: float = float(os.environ.get("THUMB_QUEUE_TIMEOUT", "20"))

# Tela final (outro) anexada ao fim do clipe sob demanda. Escopo POR PROJETO (jobs.end_screen =
# id da tela ou NULL=nenhuma) com override por clipe (clip_captions.end_screen: NULL herda, "none"
# remove, "<id>" forca). Default do projeto = NULL: a marca so entra nos projetos que o usuario
# escolher (nao estampa cliente errado). Biblioteca = id -> {chave no S3, rotulo}. O anexo e
# concat-copy com o outro re-encodado pra CASAR os params do clipe (validado empiricamente:
# concat-copy cru corrompe os timestamps com fps diferente; concat filter explode em frames
# duplicados sem normalizar fps; casar so o outro e concat-copy do longo e o caminho certo).
END_SCREEN_PREFIX: str = "assets/end_screens/"
# Seed inicial da biblioteca (a fonte de verdade passou a ser a tabela end_screens no DB; isto so
# semeia no boot se a tela ainda nao existir). O usuario sobe/troca/exclui pelo sistema.
END_SCREENS: dict = {
    "clubcast": {"key": END_SCREEN_PREFIX + "clubcast.mp4", "label": "Clubcast (Carbone)", "duration": 4.586667},
}
END_SCREEN_TIMEOUT: int = int(os.environ.get("END_SCREEN_TIMEOUT", "120"))  # teto do ffmpeg do anexo (s)
END_SCREEN_MAX_SECONDS: float = float(os.environ.get("END_SCREEN_MAX_SECONDS", "10"))  # teto da tela final
END_SCREEN_MAX_BYTES: int = int(os.environ.get("END_SCREEN_MAX_BYTES", str(60 * 1024 * 1024)))  # upload

# Pipeline de podcast 16:9 (BACKEND_CONTEXT §39). Master switch: com false as rotas
# /api/vinhetas e /api/podcast respondem 404, então o código pode ser mergeado inerte
# em produção e só liga quando o dono decidir (o staging liga no .env).
PODCAST_ENABLED: bool = os.environ.get("PODCAST_ENABLED", "false").lower() in ("1", "true", "yes")
VINHETA_PREFIX: str = "assets/vinhetas/"
VINHETA_MAX_SECONDS: float = float(os.environ.get("VINHETA_MAX_SECONDS", "30"))
VINHETA_MAX_BYTES: int = int(os.environ.get("VINHETA_MAX_BYTES", str(200 * 1024 * 1024)))
# Teto do ffprobe que varre TODOS os pacotes do podcast no upload (detecção de VFR).
# É só demux, sem decode (~30s pra 8GB medido); 600 cobre um 2h/20GB com folga.
PODCAST_PROBE_TIMEOUT: int = int(os.environ.get("PODCAST_PROBE_TIMEOUT", "600"))
# Modelo do scoring de gancho (hooks.py). Haiku pela regra 5 (mesmo tier do scoring de
# clipes); o alias casa o preço por prefixo em CLAUDE_PRICING. Env-tunável só se um
# teste de qualidade MEDIDO justificar subir de tier.
HOOK_MODEL: str = os.environ.get("HOOK_MODEL", "claude-haiku-4-5")

# Sobra (handles) cortada de cada lado do clipe, em segundos. O clip_NN_base.mp4 passa a ser
# a janela [start-H, end+H] (clamped ao vídeo) reenquadrada, e o editor abre/fecha o corte
# dentro dessa sobra (ajuste fino quando a IA erra o ponto de corte). 0 desliga (corte exato,
# comportamento antigo). Custo: alonga o reframe (YOLO, frame a frame) por clipe de origem
# horizontal e sobe o storage por clipe; origem já vertical pula o reframe (sobra ~grátis).
CLIP_HANDLE_SECONDS: float = float(os.environ.get("CLIP_HANDLE_SECONDS", "10"))

# Reframe 9:16: estabilidade do enquadramento (feedback dos cortes: "se mexe muito, o ideal é
# ficar fixo no rosto"). O corte fica TRAVADO no sujeito e só anda quando ele sai de uma zona
# central (deadzone), e devagar (teto de pan). Tudo em fração da LARGURA do corte; env-tunável
# pra dialar "quão travado": deadzone maior + pan menor = mais fixo.
REFRAME_DEADZONE_FRAC: float = float(os.environ.get("REFRAME_DEADZONE_FRAC", "0.35"))  # zona morta = 35% da largura do corte
REFRAME_MAX_PAN_FRAC: float = float(os.environ.get("REFRAME_MAX_PAN_FRAC", "0.18"))    # pan máx por SEGUNDO (fração da largura)
REFRAME_MIN_CONF: float = float(os.environ.get("REFRAME_MIN_CONF", "0.30"))            # confiança mínima do YOLO pra contar a pessoa
# Troca de câmera por GEOMETRIA (multicam same-set): a contagem/posição das pessoas que o YOLO já
# entrega (1=close, enquadra nela; 2+=plano aberto host-esq/convidado-dir, segue o lado do último
# close; 0=vazio, cai pro centro) define a troca de câmera e o enquadramento. Substitui a detecção
# por cor (que falhava: as 3 câmeras são do mesmo cenário). True liga a segmentação por plano;
# False = um plano só (rollback). Env REFRAME_CUT_DETECT=false desliga.
REFRAME_CUT_DETECT: bool = os.environ.get("REFRAME_CUT_DETECT", "true").lower() in ("1", "true", "yes")

# Offload do reframe pra GPU (2º endpoint RunPod serverless, ver BACKEND_CONTEXT §20). Sem o
# endpoint id, fica DESLIGADO e o reframe roda na CPU da VPS (comportamento atual). A flag exige
# tanto REFRAME_GPU_ENABLED=true QUANTO o endpoint id (não liga sem destino). O fallback pra CPU
# por clipe é sempre ativo, então a GPU falhar nunca derruba o job.
RUNPOD_REFRAME_ENDPOINT_ID: str = os.environ.get("RUNPOD_REFRAME_ENDPOINT_ID", "")
REFRAME_GPU_ENABLED: bool = (
    os.environ.get("REFRAME_GPU_ENABLED", "false").lower() in ("1", "true", "yes")
    and bool(RUNPOD_REFRAME_ENDPOINT_ID)
)
REFRAME_GPU_TIMEOUT: int = int(os.environ.get("REFRAME_GPU_TIMEOUT", "1800"))  # teto por clipe (s); > 600s default do RunPod
REFRAME_PUT_CONTENT_TYPE: str = os.environ.get("REFRAME_PUT_CONTENT_TYPE", "video/mp4")
# Re-render do crop por keyframe NA GPU (BACKEND §33, Fase 2b). INDEPENDENTE do REFRAME_GPU_ENABLED e
# default FALSE de proposito: se o app NOVO mandar re-render pro worker VELHO, o worker velho ignora os
# keyframes e devolve um auto-reframe ERRADO. Entao o re-render fica na CPU (ja validado) ate o dono
# confirmar workers novos no ar e LIGAR este flag. So tem efeito com REFRAME_GPU_ENABLED + endpoint id.
RERENDER_GPU_ENABLED: bool = (
    os.environ.get("RERENDER_GPU_ENABLED", "false").lower() in ("1", "true", "yes")
    and REFRAME_GPU_ENABLED
)

# Retencao do corte 16:9 por clipe (fonte de verdade do crop por keyframe, BACKEND §33). O corte
# 16:9 que hoje vira o base 9:16 passa a ser RETIDO no S3 sob um prefixo DEDICADO (pra a regra de
# lifecycle por Filter.Prefix isolar so o 16:9, sem misturar com clip_NN_base/cap em jobs/.../clips/)
# e expira em SOURCE16X9_TTL_DAYS. SOURCE16X9_LIFECYCLE_ENSURE (default false) liga o ensure da regra
# no boot; fica off em prod ate o dono decidir (nao muta a config do bucket sem querer).
SOURCE16X9_PREFIX: str = "source16x9/"
SOURCE16X9_TTL_DAYS: int = int(os.environ.get("SOURCE16X9_TTL_DAYS", "30"))
SOURCE16X9_LIFECYCLE_ENSURE: bool = os.environ.get("SOURCE16X9_LIFECYCLE_ENSURE", "false").lower() in ("1", "true", "yes")
# Intervalo (s) de amostragem ao SEMEAR keyframes do auto a partir da trajetoria do reframe (alem de
# ancorar nos cortes de camera). Menor = seed mais denso/fiel; maior = mais esparso. So afeta o seed.
KEYFRAME_SEED_INTERVAL_S: float = float(os.environ.get("KEYFRAME_SEED_INTERVAL_S", "0.75"))

# YouTube (yt-dlp): teto de duração do vídeo de entrada, em segundos. Controla custo de
# transcrição (RunPod escala com a duração) e quanto tempo um job segura o único slot da
# fila (MAX_CONCURRENT_JOBS=1). Default 2h. Ajustável por env sem mexer no código.
YOUTUBE_MAX_DURATION: int = int(os.environ.get("YOUTUBE_MAX_DURATION", str(2 * 60 * 60)))

# ─── Auth: Zoho SSO + cadastro com aprovação ─────────────────────────────────
# Tudo OPCIONAL de propósito: sem ZOHO_CLIENT_ID/SECRET o login Zoho fica desligado
# e só o login por senha (incl. break-glass) funciona — o app sobe normal.
ZOHO_CLIENT_ID: str = os.environ.get("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET: str = os.environ.get("ZOHO_CLIENT_SECRET", "")
ZOHO_REDIRECT_URI: str = os.environ.get(
    "ZOHO_REDIRECT_URI", "https://sbclips.space/api/auth/zoho/callback"
)
# Data center do Zoho. .com no nosso caso (MX em mx.zoho.com); .eu/.in/.com.au noutras regiões.
ZOHO_ACCOUNTS_BASE: str = os.environ.get("ZOHO_ACCOUNTS_BASE", "https://accounts.zoho.com").rstrip("/")
ZOHO_ENABLED: bool = bool(ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET)

# Domínios que entram via Zoho (login direto, auto-aprovado). Fora daqui, recusa.
_ALLOWED_DOMAINS_RAW = os.environ.get("ALLOWED_EMAIL_DOMAINS", "seubone.com,grupoquatro5.com")
ALLOWED_EMAIL_DOMAINS: list = [d.strip().lower() for d in _ALLOWED_DOMAINS_RAW.split(",") if d.strip()]

# Admins iniciais, semeados no boot como is_admin + status active. Promova outros pelo painel.
_ADMIN_EMAILS_RAW = os.environ.get("ADMIN_EMAILS", "")
ADMIN_EMAILS: list = [e.strip().lower() for e in _ADMIN_EMAILS_RAW.split(",") if e.strip()]


# ─── Manutenção programada (tela 503 do nginx) ──────────────────────────────
# O nginx testa a EXISTÊNCIA deste arquivo a cada request (if -f): existe = quem
# está fora da allowlist de IP (geo em MAINTENANCE_ALLOWLIST_CONF) vê a tela de
# manutenção com 503; /api e /health nunca são bloqueados. O flag vive em
# /var/lib/opussb (dono www-data) porque o botão do painel admin liga/desliga
# pelo Flask (gunicorn = www-data), que não escreve em /etc/nginx; o break-glass
# por SSH (`maintenance on|off`, root) opera o MESMO arquivo. Ver BACKEND §43.
MAINTENANCE_FLAG: str = os.environ.get("MAINTENANCE_FLAG", "/var/lib/opussb/maintenance.on")
# O geo do nginx é a fonte de verdade da allowlist; o Flask só a LÊ (parse leve)
# pra avisar no painel se o IP atual do admin ficaria de fora ao ligar (anti-lockout).
MAINTENANCE_ALLOWLIST_CONF: str = os.environ.get(
    "MAINTENANCE_ALLOWLIST_CONF", "/etc/nginx/conf.d/00-sb-maintenance.conf"
)


# ─── Revisor de legendas (leitura por visão + revisão via Claude) ────────────
# Sonnet 5 nas DUAS chamadas: o teste de viabilidade provou que o Haiku "corrige"
# o erro silenciosamente (inutiliza a revisão); o Sonnet lê fiel (acentos, typos,
# texto cortado). thinking desligado bate o custo medido (~$0,09-0,15/revisão).
CAPTION_REVIEW_MODEL: str = os.environ.get("CAPTION_REVIEW_MODEL", "claude-sonnet-5")
CAPTION_REVIEW_THINKING: str = os.environ.get("CAPTION_REVIEW_THINKING", "disabled")  # disabled|adaptive
# Amostragem por MUDANÇA DE LEGENDA (caption_sampler.py). Histórico: a 1ª versão
# extraía a 2fps e deduplicava por diff visual com threshold 0.09; a validação da
# época media VOLUME de frames (~30 num Reels de 39s), não cobertura de estados, e
# 0.09 é 4-14x insensível a troca de legenda (palavra nova = diff 0.006-0.02) — na
# prática era um detector de corte de cena e frases inteiras nunca eram lidas (caso
# "inteligencia"). A versão atual varre denso e barato na CPU (thumbnails em cinza a
# SCAN_FPS), detecta a faixa da legenda dinamicamente (vídeos de fora, posição
# imprevisível), segmenta em blocos de frase (acumula vs reseta) e lê o estado FINAL
# de cada bloco. Cobertura de estados validada por teste sintético (3 estilos de
# legenda) + regression permanente do caso "inteligencia" em tests/test_caption_sampler.py.
CAPTION_REVIEW_SCAN_FPS: float = float(os.environ.get("CAPTION_REVIEW_SCAN_FPS", "8"))
# Largura (px) dos thumbnails da varredura; altura segue a proporção. 180 é o bastante
# pra estatística de banda/blocos e mantém a varredura leve (RAM/CPU).
CAPTION_REVIEW_SCAN_WIDTH: int = int(os.environ.get("CAPTION_REVIEW_SCAN_WIDTH", "180"))
# Sensibilidade a mudança DENTRO da faixa da legenda (diff médio normalizado contra o
# âncora do bloco). Palavra nova mede 0.006-0.02; 0.008 pega sem disparar em ruído.
CAPTION_REVIEW_BAND_DIFF_THRESHOLD: float = float(os.environ.get("CAPTION_REVIEW_BAND_DIFF_THRESHOLD", "0.008"))
# Detector de corte de cena (diff do quadro inteiro). É o antigo DEDUP_THRESHOLD
# renomeado pro que ele sempre foi: cena troca mede ~0.68, legenda ~0.02.
CAPTION_REVIEW_SCENE_THRESHOLD: float = float(os.environ.get("CAPTION_REVIEW_SCENE_THRESHOLD", "0.09"))
# Piso de confiança da banda dinâmica (score de burstiness). Abaixo disso: modo
# degradado (frames cheios + reforço uniforme), sempre LOGADO.
CAPTION_REVIEW_BAND_SCORE_FLOOR: float = float(os.environ.get("CAPTION_REVIEW_BAND_SCORE_FLOOR", "4.0"))
# Intervalo (s) do reforço uniforme no modo degradado.
CAPTION_REVIEW_DEGRADE_INTERVAL: float = float(os.environ.get("CAPTION_REVIEW_DEGRADE_INTERVAL", "2.0"))
# Recorte-e-amplia: o estado de legenda vai pro Claude como crop da faixa AMPLIADO
# (texto grande lê melhor e custa menos token que o quadro inteiro). Fator de ampliação
# e teto da borda longa (limite útil de resolução da API).
CAPTION_REVIEW_CROP_UPSCALE: float = float(os.environ.get("CAPTION_REVIEW_CROP_UPSCALE", "2.0"))
CAPTION_REVIEW_CROP_MAX_LONG_EDGE: int = int(os.environ.get("CAPTION_REVIEW_CROP_MAX_LONG_EDGE", "1568"))
# Teto de CUSTO por revisão em US$ (substitui o antigo teto de 60 frames): o dono
# priorizou qualidade sobre custo; 0.80 cobre o pior caso de 5min sem cortar cobertura
# no caso típico (~$0.10-0.16). O corte é por prioridade (frases completas caem por
# último) e todo descarte é LOGADO (nunca truncado em silêncio). Estimado na MESMA
# tabela CLAUDE_PRICING do custo logado.
CAPTION_REVIEW_MAX_COST_USD: float = float(os.environ.get("CAPTION_REVIEW_MAX_COST_USD", "0.80"))
# Teto duro de imagens por chamada (limite da Messages API). Com $0.80 de teto, é ele
# que morde primeiro em vídeos extremos.
CAPTION_REVIEW_MAX_IMAGES: int = int(os.environ.get("CAPTION_REVIEW_MAX_IMAGES", "100"))
# Borda longa (px) dos quadros de CENA enviados inteiros (contexto + textos gráficos).
# 1280 mantém legenda e lower-third legíveis (validado) e segura o custo.
CAPTION_REVIEW_SEND_LONG_EDGE: int = int(os.environ.get("CAPTION_REVIEW_SEND_LONG_EDGE", "1280"))
# Reels são curtos; teto de duração protege custo e disco. Acima disso, recusa no upload.
CAPTION_REVIEW_MAX_DURATION: int = int(os.environ.get("CAPTION_REVIEW_MAX_DURATION", "300"))
CAPTION_REVIEW_MAX_BYTES: int = int(os.environ.get("CAPTION_REVIEW_MAX_BYTES", str(500 * 1024 * 1024)))
# Teto (s) POR INVOCAÇÃO de ffmpeg (varredura densa e busca de frame cheio).
CAPTION_REVIEW_TIMEOUT: int = int(os.environ.get("CAPTION_REVIEW_TIMEOUT", "180"))
# Lane PRÓPRIA de concorrência, INDEPENDENTE de MAX_CONCURRENT_JOBS: uma revisão nunca
# trava a geração de clipes (produto principal) e vice-versa. Também é o teto de revisões
# ativas admitidas (acima disso o upload responde 429).
CAPTION_REVIEW_CONCURRENCY: int = int(os.environ.get("CAPTION_REVIEW_CONCURRENCY", "2"))

# Preço por 1M de tokens (input, output) por modelo, pro custo logado por revisão. Default
# = preço CHEIO do Sonnet 5 ($3/$15); hoje vale o introdutório ($2/$10) até 2026-08-31,
# então o gasto REAL é menor até lá. Env-tunável se a Anthropic mudar a tabela.
_sonnet_in = float(os.environ.get("CLAUDE_PRICE_SONNET5_IN", "3.0"))
_sonnet_out = float(os.environ.get("CLAUDE_PRICE_SONNET5_OUT", "15.0"))
CLAUDE_PRICING: dict = {
    "claude-sonnet-5": (_sonnet_in, _sonnet_out),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-8": (5.0, 25.0),
}
