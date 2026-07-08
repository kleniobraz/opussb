"""Corte manual do início do corpo bruto do podcast (§39 Parte 2), ANTES da transcrição:
o produtor marca um ponto e o pipeline gera um arquivo novo que passa a ser o local_path
do job (tudo depois nasce alinhado, sem offset). Medição real (ffprobe, 2 episódios de
~52min da Carbone) confirmou GOP fixo de 1s nesse estúdio; a técnica é reencodar só o
pedacinho até o próximo keyframe (probing DINÂMICO via ffprobe, nunca um valor fixo) e
copiar o resto em -c copy, pra nunca reencodar o corpo inteiro (pode ter várias horas e
vários GB). Módulo irmão de podcast_compose.py: reusa probe_av_signature, build_matched_cmd,
concat_copy, duration e run_ffmpeg de lá em vez de duplicar a lógica de ffmpeg."""
import json
import logging
import os
import shutil
import subprocess
import threading

import config
import db
import podcast_compose

log = logging.getLogger("opussb.cut")

_PROBE_TIMEOUT = 30
# Tolerância de sanidade da duração final MUITO mais apertada que a do _compose (2.0s):
# o GOP medido nesse estúdio é ~1s, então um erro de "ancorou no keyframe vizinho por
# imprecisão de ponto flutuante" já produz quase 1s de diferença, que passaria
# despercebido com a tolerância do compose.
_DUR_TOLERANCIA = 0.3
# Janela de amostra (segundos) usada só pro diagnóstico da mensagem de erro, no meio do
# arquivo, longe do ponto marcado (sugestão do produto: 60 a 120s).
_DIAG_SAMPLE_WINDOW = 90.0


def _keyframes_no_intervalo(path: str, inicio: float, janela: float) -> list:
    """Pacotes de vídeo com flag de keyframe ('K') numa janela [inicio, inicio+janela],
    via ffprobe a nível de PACOTE (sem decodificar). read_intervals restringe o ffprobe a
    essa janela em vez de escanear o arquivo inteiro, fundamental num corpo de horas."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-read_intervals", f"{inicio}%+{janela}",
             "-show_entries", "packet=pts_time,flags", "-of", "json", path],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
        pacotes = (json.loads(r.stdout) if r.returncode == 0 and r.stdout else {}).get("packets") or []
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return []
    pts = []
    for p in pacotes:
        if "K" not in (p.get("flags") or ""):
            continue
        try:
            pts.append(float(p.get("pts_time")))
        except (TypeError, ValueError):
            continue
    return sorted(pts)


def find_next_keyframe(path: str, t: float, lookahead: float) -> float | None:
    """Menor keyframe estritamente DEPOIS de t, dentro da janela [t, t+lookahead]. A margem
    de 1ms evita empatar exatamente no ponto marcado (o corte tem que ANDAR pra frente).
    Sem achar dentro da janela, devolve None (o chamador decide a mensagem certa)."""
    limite = t + 1e-3
    candidatos = [p for p in _keyframes_no_intervalo(path, t, lookahead) if p > limite]
    return min(candidatos) if candidatos else None


def keyframe_density(path: str, inicio: float, janela: float) -> float | None:
    """Intervalo médio entre keyframes numa amostra [inicio, inicio+janela]. Usado só pro
    diagnóstico da mensagem de erro (ver mensagem_sem_keyframe): mede se uma parte
    QUALQUER do arquivo tem GOP largo, sem depender do ponto marcado pelo usuário."""
    pts = _keyframes_no_intervalo(path, inicio, janela)
    if len(pts) < 2:
        return None
    return (pts[-1] - pts[0]) / (len(pts) - 1)


def _diagnostico_arquivo_todo(path: str, total: float | None, lookahead: float) -> bool:
    """True se uma amostra em outra parte do arquivo (o meio, longe do ponto marcado)
    também tiver keyframes espaçados além do lookahead configurado: sinal de que o
    problema é do arquivo inteiro (GOP largo em todo canto), não só perto do ponto
    marcado. Usada só pra escolher a mensagem de erro certa."""
    if not total or total <= 0:
        return False
    janela = min(_DIAG_SAMPLE_WINDOW, total)
    inicio = max(0.0, (total - janela) / 2)
    media = keyframe_density(path, inicio, janela)
    return media is not None and media > lookahead


def mensagem_sem_keyframe(path: str, t: float, lookahead: float, total: float | None) -> str:
    """Mensagem diferenciada pra quando não achou keyframe perto de t (requisito do
    produto): distingue "esse ponto específico" de "o arquivo inteiro", pra quem está
    cortando não ficar preso num retry sem saída quando o problema é estrutural."""
    if _diagnostico_arquivo_todo(path, total, lookahead):
        return ("não encontramos um ponto de corte rápido perto de nenhuma posição "
                "amostrada deste arquivo; o corte de início pode não estar disponível "
                "pra este episódio específico")
    return ("não encontramos um keyframe perto desse ponto marcado; tente marcar um "
            "ponto um pouco diferente")


def _copiar_resto_com_heartbeat(cmd: list, nice: int, job_id: str) -> None:
    """Cópia do resto (-c copy) é I/O de um corpo de possivelmente vários GB, a etapa mais
    demorada do corte. Heartbeat periódico (mesmo padrão de _upload_com_heartbeat do
    podcast_compose) pra não ser ceifada pelo watchdog no meio de um trabalho legítimo."""
    pare = threading.Event()

    def _bate():
        while not pare.wait(60):
            db.heartbeat_job(job_id, "cortando_inicio")

    t = threading.Thread(target=_bate, daemon=True)
    t.start()
    try:
        podcast_compose.run_ffmpeg(cmd, nice, "cópia do resto", timeout=config.PODCAST_CUT_TIMEOUT)
    finally:
        pare.set()


def _cortar_inicio(job_id: str, job: dict, workdir: str, cut_at: float) -> None:
    src = job.get("local_path")
    if not src or not os.path.exists(src):
        raise RuntimeError("o arquivo fonte não está mais no servidor")

    # Gravado ANTES do ffmpeg começar (não só no sucesso), igual hook_choice é gravado
    # antes da montagem: permite o front distinguir erro do corte de erro de outra fase.
    db.update_job(job_id, intro_cut_at=cut_at)

    total = podcast_compose.duration(src)
    if total is None:
        raise RuntimeError("não consegui ler a duração do arquivo fonte")
    if (total - cut_at) < config.PODCAST_CUT_MIN_REMAINING_SECONDS:
        raise RuntimeError(
            f"sobra insuficiente após o corte: {total - cut_at:.1f}s "
            f"(mínimo {config.PODCAST_CUT_MIN_REMAINING_SECONDS:.0f}s)")

    # Defesa em profundidade: o endpoint já checa disco livre, mas o motor do corte não
    # confia cegamente (mesmo padrão de podcast_compose._compose). Pico real do corte é
    # ~2x o fonte (resto + final coexistem com o fonte), não ~1x como na montagem.
    src_bytes = os.path.getsize(src)
    livre = shutil.disk_usage(os.path.dirname(src)).free
    preciso = int(src_bytes * config.PODCAST_CUT_DISK_FACTOR)
    if livre < preciso:
        raise RuntimeError(f"disco insuficiente: livre {livre / 2**30:.1f}GB, "
                           f"necessário ~{preciso / 2**30:.1f}GB")

    sig = podcast_compose.probe_av_signature(src, require_audio=False)
    if not sig:
        raise RuntimeError("não consegui ler a assinatura A/V do fonte")

    keyframe = find_next_keyframe(src, cut_at, config.PODCAST_CUT_KEYFRAME_LOOKAHEAD)
    if keyframe is None:
        raise RuntimeError(mensagem_sem_keyframe(
            src, cut_at, config.PODCAST_CUT_KEYFRAME_LOOKAHEAD, total))

    os.makedirs(workdir, exist_ok=True)
    nice, threads = config.BURN_NICE, config.BURN_FFMPEG_THREADS

    db.update_job(job_id, progress=15)
    pedaco = os.path.join(workdir, "pedaco.mp4")
    podcast_compose.run_ffmpeg(
        podcast_compose.build_matched_cmd(src, pedaco, sig, seek=(cut_at, keyframe - cut_at),
                                          threads=threads),
        nice, "reencode do pedaço inicial")

    db.update_job(job_id, progress=35)
    resto = os.path.join(workdir, "resto.mp4")
    # Precisão alta no -ss (6 casas decimais), nunca truncado pra milissegundos: o keyframe
    # veio de pts_time com essa mesma precisão, um arredondamento aqui reintroduz o offset
    # que a técnica inteira existe pra evitar.
    cmd_resto = ["ffmpeg", "-y", "-ss", f"{keyframe:.6f}", "-i", src,
                 "-c", "copy", "-avoid_negative_ts", "make_zero", resto]
    _copiar_resto_com_heartbeat(cmd_resto, nice, job_id)

    db.update_job(job_id, progress=80)
    final_local = os.path.join(workdir, "final.mp4")
    if not podcast_compose.concat_copy([pedaco, resto], final_local,
                                       timeout=config.PODCAST_CUT_TIMEOUT, nice=nice):
        raise RuntimeError("concat do corte de início falhou")

    # Sanidade da emenda: tolerância apertada de propósito (ver _DUR_TOLERANCIA).
    esperado = total - cut_at
    total_final = podcast_compose.duration(final_local) or 0
    if abs(total_final - esperado) > _DUR_TOLERANCIA:
        raise RuntimeError(
            f"duração após o corte ({total_final:.2f}s) não bate com a esperada "
            f"({esperado:.2f}s)")

    # Local ESTÁVEL dentro do JOB_DIR do job (não o workdir, que é limpo no fim). Move em
    # vez de copiar: mesmo filesystem (workdir é subpasta do próprio job_dir), sem custo
    # extra de disco nem cópia de um arquivo de possivelmente vários GB.
    job_dir = os.path.join(config.JOB_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    novo_path = os.path.join(job_dir, "corpo_cortado.mp4")
    shutil.move(final_local, novo_path)
    if not os.path.exists(novo_path) or os.path.getsize(novo_path) < 1024:
        raise RuntimeError("o arquivo cortado não ficou íntegro no destino final")

    # Regra 7 da casa (adaptada de S3 pra arquivo local): o ponteiro do banco só troca
    # DEPOIS do arquivo novo confirmado íntegro, e o fonte antigo só morre DEPOIS disso.
    # intro_cut_at repetido aqui de propósito (já foi gravado no início): reafirma o valor
    # final junto do resto do estado consistente, mesmo sendo idempotente.
    db.update_job(job_id, local_path=novo_path, status="uploaded", progress=0, intro_cut_at=cut_at)
    log.info("[cut_intro] job %s: início cortado em %.3fs (keyframe em %.6fs), "
             "sobra %.1fs", job_id, cut_at, keyframe, total_final)
    try:
        os.remove(src)
    except OSError as e:
        log.warning("[cut_intro] job %s: falha ao apagar o fonte pré-corte %s: %s",
                   job_id, src, e)

    shutil.rmtree(workdir, ignore_errors=True)


def run_podcast_cut_intro(job_id: str, cut_at: float) -> None:
    """Thread do corte de início (POST /api/podcast/<id>/cut-intro). Espelha
    run_podcast_compose: falha vira status='error' e libera o disco do workdir, mas
    PRESERVA a fonte original (o local_path só troca em caso de sucesso confirmado)."""
    job = db.get_job(job_id)
    if not job or job.get("job_type") != "podcast":
        return
    workdir = os.path.join(config.JOB_DIR, job_id, "cut_intro")
    try:
        _cortar_inicio(job_id, job, workdir, cut_at)
    except Exception as e:
        log.warning("[cut_intro] job %s falhou: %s", job_id, e)
        db.update_job(job_id, status="error", error=f"corte de início falhou: {e}")
        shutil.rmtree(workdir, ignore_errors=True)
