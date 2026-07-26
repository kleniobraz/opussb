"""Entrada de podcast por upload DIRETO do navegador pro S3, e a materialização do fonte.

POR QUE EXISTE: pelo caminho antigo o arquivo (até 15GB) subia do navegador pro Flask via
multipart/form-data. Medido em 14 dias de log: 21% de sucesso (8 de 37), uma das 4 threads do
gunicorn presa por até 75min, 10GB escritos em disco temporário, e um SEGUNDO tráfego de 10GB
da VPS pro S3. Um restart no meio matava o upload (aconteceu, custou 10GB e 75min).

Agora o navegador fala direto com o S3 em multipart presignado, e a VPS sai do caminho dos
bytes. Restart deixa de matar upload em voo, o que é o motivo de este caminho NÃO precisar
bloquear deploy (ver em_voo.py: a única janela que sobra é o restart cair entre o último byte
e o complete, e a proteção dela é o complete ser idempotente, não travar o gate).

O QUE ISTO **NÃO** RESOLVE, e precisa estar dito: o tráfego de 10GB entre VPS e S3 não some,
ele INVERTE de direção. O arquivo continua tendo que chegar ao disco pra transcrever, cortar e
montar (garantir_fonte_local abaixo). O ganho é que isso passa a acontecer em thread de fundo,
com retry barato e ninguém esperando, em vez de dentro do request do usuário.

E há um risco novo por causa dessa inversão: download pesado é exatamente o padrão que
correlacionamos com o bloqueio de TCP do provedor (8 a 20min de operação parada); upload
pesado nunca foi correlacionado com nada.

MEDIDO EM 2026-07-26, e o resultado contraria o que se supunha: baixar com concorrência 2
derrubou a rede IGUAL, 84s depois de começar (5a correlação, 1a com concorrência reduzida).
No mesmo teste dá pra ver o mecanismo: o throughput despenca de ~30 MB/s pra ~2,4 MB/s por
volta dos 60s, e conexões NOVAS passam a não completar o handshake. Ou seja, o gatilho é
taxa/volume, não número de conexões. A concorrência 2 fica porque não custa nada, mas quem
mitiga de verdade é config.PODCAST_MATERIALIZA_MAX_MBPS (default 0 = desligado; o valor tem
que sair de medição, não de chute).

CONSEQUÊNCIA PRÁTICA que precisa estar dita: sob estrangulamento, materializar 8GB leva
perto de uma hora. Continua valendo a pena (é background, com retry, e ninguém esperando,
contra 75min de request com 21% de sucesso), mas quem for mexer nos tetos de tempo precisa
saber que "lento" aqui é o normal, não sintoma.
"""
import logging
import os
import shutil
import subprocess
import threading
import time

import boto3.s3.transfer as boto3_transfer
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import config
import db
import em_voo
import s3_client

log = logging.getLogger("opussb.ingest")

# O salt carrega o AMBIENTE: staging e produção compartilham o mesmo FLASK_SECRET_KEY (ver
# STAGING.md), então sem isto um token emitido num vale no outro. Hoje falha fechado (o
# uploadId do staging aponta pra staging/jobs/... e o complete em produção dá NoSuchUpload),
# mas um token não deve atravessar fronteira de ambiente por construção, e sim por acidente.
_SALT = f"podcast-upload-direto|{config.S3_KEY_PREFIX}"


def _serializer():
    """Assina com a MESMA chave da sessão (config.FLASK_SECRET_KEY, app.py:53), com salt
    próprio pra um token de upload nunca ser confundido com um cookie de sessão."""
    return URLSafeTimedSerializer(config.FLASK_SECRET_KEY, salt=_SALT)


def emitir_token(dados: dict) -> str:
    """Empacota o estado da transação num token assinado que viaja NO CLIENTE.

    É isto que dispensa criar linha no banco entre o init e o complete. Por que não o banco:
    (1) o S3 já é o registro dos uploads pendentes (list_multipart_uploads enumera, e a regra
    AbortIncompleteMultipartUpload de 7 dias coleta;
    regra VERIFICADA no bucket em 2026-07-26, ID opussb-abort-multipart-prod, prefixo vazio), e duas fontes de verdade divergem no
    primeiro restart; (2) um job criado no init apareceria na lista de projetos por 75min com
    todos os botões dando 409; (3) ele seria excluível pelo DELETE /api/projects no meio do
    upload, apagando jobs/<id>/ por baixo das partes em voo.

    O token carrega o job_id sorteado, a key (derivada no SERVIDOR, nunca do cliente), o
    uploadId, o dono e o t0. Sobrevive a restart porque é assinatura, não sessão: o retry do
    cliente depois de um restart recria o job com o MESMO job_id, e o objeto deixa de ser
    órfão sozinho."""
    return _serializer().dumps(dados)


def ler_token(token: str, user_email: str) -> dict:
    """Valida assinatura, validade e DONO. Levanta ValueError com mensagem pro usuário.

    A checagem de dono existe porque o token é um portador: sem ela, um token vazado de outra
    sessão autorizaria escrever no upload alheio."""
    try:
        dados = _serializer().loads(token, max_age=config.PODCAST_UPLOAD_TOKEN_TTL)
    except SignatureExpired:
        raise ValueError("Este envio expirou. Comece de novo.")
    except BadSignature:
        raise ValueError("Envio inválido.")
    if dados.get("user_email") != user_email:
        raise ValueError("Este envio pertence a outro usuário.")
    return dados


def calcular_partes(tamanho: int) -> tuple:
    """(tamanho_da_parte, quantidade). Levanta ValueError se passar do limite do S3.

    A guarda de 10.000 partes existe pro dia em que alguém subir o teto de tamanho: sem ela a
    falha apareceria lá na frente como um InvalidPart incompreensível, em vez de uma mensagem
    clara antes de subir o primeiro byte."""
    parte = config.PODCAST_UPLOAD_PART_BYTES
    n = max(1, -(-int(tamanho) // parte))          # divisão pra cima
    if n > 10000:
        raise ValueError("Arquivo grande demais pro tamanho de parte configurado "
                         f"({n} partes, máximo 10000). Avise o administrador.")
    return parte, n


def _com_heartbeat(job_id: str, status: str, fn):
    """Roda fn() batendo sinal de vida no job, no mesmo molde de podcast_cut._com_heartbeat.

    SEM ISTO a feature se autossabota: a materialização não escreve NADA no banco enquanto
    baixa, e ela leva ~55min quando o provedor estrangula. O reap_stale_jobs ceifa em 30min e
    roda em 6 rotas, ou seja, qualquer outra pessoa clicando em enviar/transcrever/montar mata
    um job perfeitamente vivo. Pelo /transcribe o estrago é definitivo: o job vira 'error' e
    nada no sistema devolve um podcast pra 'uploaded', então o único caminho é excluir e
    reenviar os 10GB, que é exatamente o incidente que esta frente existe pra eliminar.

    O status esperado vem do snapshot do chamador de propósito: se alguém cancelou nesse meio
    tempo, o WHERE status do heartbeat_job não bate e a batida vira no-op, em vez de
    ressuscitar um job morto."""
    pare = threading.Event()

    def _bate():
        while not pare.wait(60):
            db.heartbeat_job(job_id, status)

    threading.Thread(target=_bate, daemon=True).start()
    try:
        return fn()
    finally:
        pare.set()


def checar_cfr(path: str) -> bool:
    """True se o vídeo é CFR. Histograma dos deltas de DTS de TODOS os pacotes (só demux,
    ~30s pra 8GB); mesma detecção da rota antiga, que vive em app._podcast_is_cfr.

    Duplicada aqui, e não importada, porque app.py importa este módulo: importar de volta
    fecharia ciclo. São 12 linhas estáveis (o formato de saída do ffprobe não muda), e o
    teste test_cfr_bate_com_a_rota_antiga trava as duas contra o mesmo arquivo."""
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "packet=dts", "-of", "csv=p=0", path],
                        capture_output=True, text=True, timeout=config.PODCAST_PROBE_TIMEOUT)
    if pr.returncode != 0:
        return False
    dts = [float(x) for x in pr.stdout.split() if x.strip()]
    if len(dts) < 3:
        return True
    deltas = [round(b - a, 4) for a, b in zip(dts, dts[1:])]
    if any(d <= 0 for d in deltas):
        return False
    mais_comum = max(set(deltas), key=deltas.count)
    return deltas.count(mais_comum) / len(deltas) >= 0.999


def garantir_fonte_local(job: dict, fator_disco: float = None,
                         aceita_original: bool = False) -> str:
    """Garante o fonte do podcast no disco e devolve o caminho. Idempotente: se já está lá,
    devolve na hora sem tocar a rede.

    EXISTE PORQUE o pipeline inteiro lê do disco: /cut-intro, /transcribe, /approve, /compose,
    o source_available do /hooks, o processor.run_thread1 e o podcast_compose. Com upload
    direto o arquivo nasce só no S3, então sem isto o upload funciona e o produto quebra da
    tela seguinte em diante, com 409 em tudo.

    CHAME DENTRO DAS THREADS, nunca num request: baixar 10GB síncrono prenderia uma das 4
    threads do gunicorn por minutos, que é o incidente de 2026-07-24 na direção contrária.

    NÃO re-baixa por cima de corte já aplicado: se intro_cut_at está gravado, o arquivo local
    é o CORTADO (podcast_cut troca o local_path), e re-baixar o original desfaria o corte em
    silêncio. Nesse caso falha com mensagem, em vez de mentir."""
    local = job.get("local_path")
    if local and os.path.exists(local):
        return local
    key = job.get("podcast_source_key")
    if not key:
        raise RuntimeError("este projeto não tem fonte no armazenamento (job antigo?)")
    if job.get("intro_cut_at") is not None and not aceita_original:
        # Quem NÃO vai refazer o corte (transcrição, montagem) não pode receber o original:
        # o local_path apontava pro arquivo CORTADO, e devolver o íntegro desfaria em silêncio
        # o corte do produtor. Já o podcast_cut passa aceita_original=True, porque ele vai
        # reaplicar o cut_at que está gravado. Sem essa distinção o projeto ficava travado
        # pra sempre: a mensagem mandava refazer o corte, e o refazer batia neste mesmo guard.
        raise RuntimeError("o corte de início já foi aplicado e a cópia local sumiu; "
                           "refaça o corte antes de continuar")

    job_id = job["job_id"]
    destino = os.path.join(config.JOB_DIR, job_id, "original.mp4")
    os.makedirs(os.path.dirname(destino), exist_ok=True)

    # Guard-rail de disco ANTES de baixar, no molde do podcast_audio: a VPS não tem swap.
    tamanho = s3_client.head_size(key)
    livre = shutil.disk_usage(config.JOB_DIR).free
    preciso = int(tamanho * (fator_disco or config.PODCAST_AUDIO_DISK_FACTOR))
    if livre < preciso:
        raise RuntimeError(f"disco insuficiente pra materializar: livre {livre / 2**30:.1f}GB, "
                           f"necessário ~{preciso / 2**30:.1f}GB")

    # Concorrência reduzida E teto de banda. O teto é quem mitiga de verdade: em 2026-07-26 um
    # download de 8,12GB com concorrência 2 derrubou o TCP do provedor igual, 84s depois de
    # começar, refutando a hipótese de que o gatilho fosse o número de conexões. Com o teto em
    # 0 (default) o comportamento é o de antes, então ligar isto é decisão de operação, não
    # efeito colateral de deploy.
    limite = config.PODCAST_MATERIALIZA_MAX_MBPS
    cfg = boto3_transfer.TransferConfig(
        max_concurrency=max(1, config.PODCAST_MATERIALIZA_CONCURRENCY),
        **({"max_bandwidth": int(limite * 2**20)} if limite > 0 else {}))
    t0 = time.monotonic()
    with em_voo.registrar("materializa", f"job {job_id}"):
        _com_heartbeat(job_id, job.get("status") or "",
                       lambda: s3_client.download_file(key, destino, transfer_config=cfg))
    dur = time.monotonic() - t0
    # A taxa vai pro log de propósito: é o número que diz se o provedor estrangulou de novo
    # (~2,4 MB/s é a assinatura) sem precisar de teste dedicado que derruba a rede do time.
    log.info("[ingest] job %s: fonte materializado (%.2fGB em %.0fs = %.1f MB/s, conc=%d, "
             "teto=%s)", job_id, tamanho / 2**30, dur, (tamanho / 2**20) / max(dur, 0.001),
             config.PODCAST_MATERIALIZA_CONCURRENCY,
             f"{limite:g} MB/s" if limite > 0 else "sem")
    # CFR aqui, e não no /complete: a checagem lê TODOS os pacotes (baixaria os 8GB inteiros
    # por HTTP), então só dá pra fazer com o arquivo em disco. Este é o primeiro momento em
    # que ele existe, e é o análogo exato do ponto onde a rota antiga validava. Sem isto um
    # export VFR passa, gasta RunPod + Claude + o tempo do produtor aprovando o gancho, e só
    # estoura na montagem com "a duração não bate", que não diz a ninguém o que fazer.
    try:
        cfr = checar_cfr(destino)
    except subprocess.TimeoutExpired:
        cfr = True          # não dá pra afirmar que é VFR: segue, como fazia o caminho antigo
        log.warning("[ingest] job %s: checagem de CFR estourou o tempo; seguindo", job_id)
    if not cfr:
        os.remove(destino)  # não deixa 8GB no disco de um job que não vai andar
        raise RuntimeError(
            "o vídeo tem frame rate variável (VFR) e o corte de precisão exige CFR. "
            "Peça ao estúdio um export com frame rate constante e envie de novo.")

    db.update_job(job_id, local_path=destino)
    return destino
