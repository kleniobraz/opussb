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


def _com_heartbeat(job_id: str, fn):
    """Roda fn() com sinal de vida periódico (mesmo padrão de
    podcast_compose._upload_com_heartbeat/podcast_cut._com_heartbeat), cobrindo
    download+ffmpeg+upload: um final.mp4 de dezenas de GB pode passar do watchdog de
    staleness (JOB_STALE_MINUTES) sem isto. O heartbeat só renova enquanto o status seguir
    'extraindo' (um redisparo concorrente legítimo não é ressuscitado à toa).

    NÃO cobre a espera na fila do semáforo: nos dois caminhos ele só começa depois do
    acquire. Tolerável porque o teto da montagem (60s) e a fila de um único job ficam bem
    abaixo de JOB_STALE_MINUTES, mas não conte com ele pra isso."""
    pare = threading.Event()

    def _bate():
        while not pare.wait(_HEARTBEAT_INTERVAL):
            db.heartbeat_podcast_audio(job_id)

    t = threading.Thread(target=_bate, daemon=True)
    t.start()
    try:
        return fn()          # devolve o valor de fn (a key do MP3, no caminho novo)
    finally:
        pare.set()


def audio_key_de(job_id: str) -> str:
    """Key do MP3 no S3. Função (e não literal repetido) porque agora há DOIS caminhos que
    produzem esse objeto: a extração na montagem e o fallback sob demanda."""
    return f"jobs/{job_id}/podcast/audio.mp3"


def _extrair_e_subir(job_id: str, src: str, out_dir: str) -> str:
    """ffmpeg + upload do MP3 a partir de um final.mp4 que JÁ ESTÁ no disco local. Devolve
    a key no S3 e LEVANTA em falha (quem chama grava o estado no banco).

    SEM semáforo e SEM heartbeat de propósito: os dois chamadores já estão dentro dos seus,
    e pegar o _audio_sem aqui daria DEADLOCK no caminho sob demanda, que o adquire antes
    (BoundedSemaphore não é reentrante e PODCAST_AUDIO_CONCURRENCY é 1 em produção).

    NÃO baixa nada e NÃO checa disco: quem tem o arquivo local já pagou esses dois custos.
    NÃO apaga out_dir: o diretório é do chamador (na montagem ele contém o final.mp4 que
    ainda vai ser usado), e função que apaga diretório dos outros é acidente esperando."""
    mp3 = os.path.join(out_dir, "audio.mp3")
    key = audio_key_de(job_id)
    _extract_audio(src, mp3)
    s3_client.upload_file(mp3, key)
    return key


def extrair_e_publicar(job_id: str, src: str, out_dir: str, slot_timeout: float) -> str:
    """_extrair_e_subir com o slot e o heartbeat em volta, para quem AINDA NÃO os tem
    (hoje: a montagem, que chega aqui com o final.mp4 recém-subido e ainda em disco).

    slot_timeout é OBRIGATÓRIO de propósito: esperar o semáforo pra sempre aqui seria a
    opção errada, porque enquanto a montagem espera o rmtree do job_dir não roda e o vídeo
    fonte (dezenas de GB) segue ocupando disco numa VPS sem swap. Job já 'done' não conta em
    count_active_jobs nem é ceifado pelo reap_stale_jobs, então nada mais liberaria esse
    disco e a PRÓXIMA montagem bateria no guard-rail de espaço. Estourar o teto é o
    desfecho barato: o áudio vira 'error' e o usuário tem "Tentar de novo"."""
    if not _audio_sem.acquire(timeout=slot_timeout):
        raise RuntimeError(f"fila de extração de áudio ocupada (esperei {slot_timeout:.0f}s)")
    try:
        return _com_heartbeat(job_id, lambda: _extrair_e_subir(job_id, src, out_dir))
    finally:
        _audio_sem.release()


def run_podcast_audio_extract(job_id: str) -> None:
    """Thread da extração SOB DEMANDA (POST /api/podcast/<id>/audio). Falha vira
    podcast_audio_status='error' com a mensagem em podcast_audio_error; sucesso grava a
    key do MP3 cacheado. workdir sempre limpo no fim, sucesso ou falha (mesmo padrão de
    run_podcast_compose/run_podcast_cut_intro).

    Este é o caminho CARO (baixa o episódio inteiro de volta do S3) e virou FALLBACK: o
    caminho padrão é a extração durante a montagem (podcast_compose), com o arquivo ainda
    local. Continua existindo para os episódios montados ANTES dessa mudança e para o
    'tentar de novo' do usuário quando a extração da montagem falha."""
    job = db.get_job(job_id)
    if not job or job.get("job_type") != "podcast":
        return
    workdir = os.path.join(config.JOB_DIR, job_id, "audio")
    try:
        os.makedirs(workdir, exist_ok=True)
        local_final = os.path.join(workdir, "final.mp4")

        def _trabalho():
            # Disco checado só AQUI (já com o semáforo em mãos, não antes de entrar na
            # fila): uma extração presa esperando outra (ou a esteira de montagem/corte,
            # que disputa o mesmo JOB_DIR) podia "passar" no check e achar o disco já
            # consumido por outra coisa quando finalmente rodasse de verdade.
            _checar_disco(job["podcast_final_key"])
            s3_client.download_file(job["podcast_final_key"], local_final)
            # _extrair_e_subir (e não extrair_e_publicar): o slot e o heartbeat já são
            # DESTE bloco. Chamar a versão que os adquire liberaria e retomaria o semáforo
            # no meio do trabalho, abrindo brecha pra outra extração entrar entre o
            # download e o ffmpeg. O caminho sob demanda segura um slot só, de ponta a
            # ponta, exatamente como antes desta refatoração.
            return _extrair_e_subir(job_id, local_final, workdir)

        with _audio_sem:
            audio_key = _com_heartbeat(job_id, _trabalho)
        db.update_job(job_id, podcast_audio_status="done", podcast_audio_key=audio_key)
        log.info("[audio] job %s: áudio extraído e no S3 (%s)", job_id, audio_key)
    except Exception as e:
        log.warning("[audio] job %s falhou: %s", job_id, e)
        db.update_job(job_id, podcast_audio_status="error", podcast_audio_error=str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
