"""Extração de áudio (MP3) do episódio de podcast já montado (§39 Parte 5+), sob demanda:
o time precisa do episódio como MP3 pra subir no Spotify. Baixa o final.mp4 já pronto do
S3, extrai só a trilha de áudio via ffmpeg e recacheia o MP3 no S3. Roda em thread de
FUNDO disparada pelo POST /api/podcast/<id>/audio (nunca dentro do próprio request: um
episódio de horas tornaria o download + extração mais lento que o timeout do nginx, e
essa abordagem síncrona já foi tentada e rejeitada por esse motivo). Módulo deliberadamente
leve, no mesmo espírito de podcast_compose.py/podcast_cut.py."""
import logging
import os
import shutil
import subprocess
import threading

import config
import db
import s3_client

log = logging.getLogger("opussb.audio")

_audio_sem = threading.BoundedSemaphore(max(1, config.PODCAST_AUDIO_CONCURRENCY))

# Intervalo do sinal de vida durante o trabalho (mesmos 60s de podcast_compose/podcast_cut).
# Constante à parte (não literal dentro de _com_heartbeat) pra dar pra encurtar em teste.
_HEARTBEAT_INTERVAL = 60


def _build_extract_cmd(src: str, out: str) -> list:
    """Comando (puro, testável) que extrai só a trilha de áudio em MP3, sem tocar no
    vídeo (-vn). libmp3lame não é multi-thread como o libx264 da queima, então não há
    flag de -threads aqui; o nice (config.BURN_NICE) na chamada ainda ajuda a não
    competir por CPU com o resto do site."""
    return ["ffmpeg", "-y", "-i", src, "-vn", "-acodec", "libmp3lame", "-b:a", "192k", out]


def _extract_audio(src: str, out: str) -> None:
    """Roda o ffmpeg da extração e valida o resultado (mesmo padrão de
    podcast_compose.run_ffmpeg: a saída tem que existir e não ser lixo pequeno). Timeout
    reaproveita config.COMPOSE_TIMEOUT: a extração de áudio é bem mais leve que o
    remux/concat da montagem (não reencoda vídeo, só decodifica e recomprime uma trilha),
    então o mesmo teto (900s) sobra folga mesmo num episódio de várias horas."""
    r = subprocess.run(_build_extract_cmd(src, out), capture_output=True, text=True,
                       timeout=config.COMPOSE_TIMEOUT, preexec_fn=lambda: os.nice(config.BURN_NICE))
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1024:
        raise RuntimeError(f"extração de áudio: ffmpeg falhou ({(r.stderr or '')[-300:]})")


def _checar_disco(final_key: str) -> None:
    """Guard-rail de disco ANTES de baixar o final.mp4 (pode ser dezenas de GB): HEAD no
    S3 pra saber o tamanho remoto sem baixar, mesmo espírito do guard-rail de
    podcast_compose/podcast_cut (a VPS não tem swap)."""
    tamanho = s3_client.head_size(final_key)
    livre = shutil.disk_usage(config.JOB_DIR).free
    preciso = int(tamanho * config.PODCAST_AUDIO_DISK_FACTOR)
    if livre < preciso:
        raise RuntimeError(f"disco insuficiente: livre {livre / 2**30:.1f}GB, "
                           f"necessário ~{preciso / 2**30:.1f}GB")


def _com_heartbeat(job_id: str, fn) -> None:
    """Roda fn() com sinal de vida periódico (mesmo padrão de
    podcast_compose._upload_com_heartbeat/podcast_cut._com_heartbeat), cobrindo
    download+ffmpeg+upload: um final.mp4 de dezenas de GB, ou a espera na fila do
    semáforo (PODCAST_AUDIO_CONCURRENCY=1), pode passar do watchdog de staleness
    (JOB_STALE_MINUTES) sem isto. O heartbeat só renova enquanto o status seguir
    'extraindo' (um redisparo concorrente legítimo não é ressuscitado à toa)."""
    pare = threading.Event()

    def _bate():
        while not pare.wait(_HEARTBEAT_INTERVAL):
            db.heartbeat_podcast_audio(job_id)

    t = threading.Thread(target=_bate, daemon=True)
    t.start()
    try:
        fn()
    finally:
        pare.set()


def run_podcast_audio_extract(job_id: str) -> None:
    """Thread da extração (POST /api/podcast/<id>/audio). Falha vira
    podcast_audio_status='error' com a mensagem em podcast_audio_error; sucesso grava a
    key do MP3 cacheado. workdir sempre limpo no fim, sucesso ou falha (mesmo padrão de
    run_podcast_compose/run_podcast_cut_intro)."""
    job = db.get_job(job_id)
    if not job or job.get("job_type") != "podcast":
        return
    workdir = os.path.join(config.JOB_DIR, job_id, "audio")
    try:
        os.makedirs(workdir, exist_ok=True)
        local_final = os.path.join(workdir, "final.mp4")
        local_mp3 = os.path.join(workdir, "audio.mp3")
        audio_key = f"jobs/{job_id}/podcast/audio.mp3"

        def _trabalho():
            # Disco checado só AQUI (já com o semáforo em mãos, não antes de entrar na
            # fila): uma extração presa esperando outra (ou a esteira de montagem/corte,
            # que disputa o mesmo JOB_DIR) podia "passar" no check e achar o disco já
            # consumido por outra coisa quando finalmente rodasse de verdade.
            _checar_disco(job["podcast_final_key"])
            s3_client.download_file(job["podcast_final_key"], local_final)
            _extract_audio(local_final, local_mp3)
            s3_client.upload_file(local_mp3, audio_key)

        with _audio_sem:
            _com_heartbeat(job_id, _trabalho)
        db.update_job(job_id, podcast_audio_status="done", podcast_audio_key=audio_key)
        log.info("[audio] job %s: áudio extraído e no S3 (%s)", job_id, audio_key)
    except Exception as e:
        log.warning("[audio] job %s falhou: %s", job_id, e)
        db.update_job(job_id, podcast_audio_status="error", podcast_audio_error=str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
