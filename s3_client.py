import os
import logging
import re
import threading
import time
import unicodedata

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import config

log = logging.getLogger("opussb.s3")


class S3StalledError(RuntimeError):
    """Chamada síncrona ao S3 que estourou o teto do watchdog (_com_teto): sem progresso, no
    caso de uma transferência, ou sem terminar, no caso de uma chamada curta de API.

    Achado em produção 2026-07-24, três vezes no mesmo dia e em três pontos diferentes: um
    download de 7,6GB travou sem se mover e pendurou o app inteiro (1 worker/4 threads do
    gunicorn, site caiu pro time todo); o ensure_lifecycle_rule travou o BOOT por 6min20s; e o
    caminho da extração de MP3 tinha mais dois pontos sem teto (head_size/upload_file). Em
    NENHUM deles o boto3 levantou exceção sozinho (retry/read_timeout internos não salvaram),
    e try/except não pega um hang. O watchdog de FORA do boto3 é a única garantia real."""


class DownloadStalledError(S3StalledError):
    """Nome histórico do stall de download_file (2026-07-24), mantido porque a mensagem já
    aparece em erros de job gravados no banco. Quem quiser pegar qualquer stall (download,
    upload, HEAD, DELETE, LIST, lifecycle) captura S3StalledError."""

# Cliente boto3 cacheado: criar um client tem custo (sessão + resolução de endpoint)
# e antes a gente recriava em TODA chamada (upload/download/exists/presign), inclusive
# no caminho quente da queima sob demanda. Clients do boto3 são thread-safe pra chamadas,
# então um singleton guardado sob lock atende os 4 threads do gunicorn com segurança.
_client_singleton = None
_client_lock = threading.Lock()
_accel_client_singleton = None
_accel_client_lock = threading.Lock()


def _k(s3_key: str) -> str:
    """Prefixa a key com config.S3_KEY_PREFIX (namespace do ambiente; "" em producao). Toda key
    construida pelo app (app.py/processor.py) e SEM prefixo; o prefixo entra SO aqui, na borda do
    I/O, pra o staging (S3_KEY_PREFIX=staging/) nunca tocar objetos de producao."""
    return config.S3_KEY_PREFIX + s3_key


def _client():
    global _client_singleton
    if _client_singleton is None:
        with _client_lock:
            if _client_singleton is None:
                _client_singleton = boto3.client(
                    "s3",
                    endpoint_url=config.S3_ENDPOINT,
                    aws_access_key_id=config.S3_ACCESS_KEY,
                    aws_secret_access_key=config.S3_SECRET_KEY,
                    region_name="sa-east-1",
                    config=Config(
                        signature_version="s3v4",
                        retries={"max_attempts": 3, "mode": "standard"},
                    ),
                )
    return _client_singleton


def _accel_client():
    """Client separado com S3 Transfer Acceleration, SEM endpoint_url explícito (o boto3
    só resolve o host bucket.s3-accelerate.amazonaws.com sozinho quando não há endpoint_url
    fixo pra sobrepor essa resolução). Usado SÓ por get_presigned_url_accelerate: mitigação
    do download intermitente do RunPod (worker fora do Brasil, longe do bucket em SP,
    achado em produção 2026-07-24). O resto do app segue em _client(), sem accelerate."""
    global _accel_client_singleton
    if _accel_client_singleton is None:
        with _accel_client_lock:
            if _accel_client_singleton is None:
                _accel_client_singleton = boto3.client(
                    "s3",
                    aws_access_key_id=config.S3_ACCESS_KEY,
                    aws_secret_access_key=config.S3_SECRET_KEY,
                    region_name="sa-east-1",
                    config=Config(
                        signature_version="s3v4",
                        retries={"max_attempts": 3, "mode": "standard"},
                        s3={"use_accelerate_endpoint": True},
                    ),
                )
    return _accel_client_singleton


def _com_teto(op: str, timeout: float, fn, erro=S3StalledError):
    """Watchdog ÚNICO de toda chamada síncrona ao S3 deste módulo. Roda `fn(bater_ponto)` numa
    thread daemon e mede o tempo desde o ÚLTIMO `bater_ponto()`; se passar de `timeout`, desiste
    e levanta `erro` mesmo que a chamada do boto3 nunca tenha voltado nem lançado nada.

    Dois usos do MESMO mecanismo, decididos por quem chama bater_ponto:
      - transferência (upload/download): `fn` passa `bater_ponto` como Callback do boto3, então
        `timeout` é teto de PROGRESSO (nenhum byte novo por `timeout`), NÃO de duração total,
        porque um final.mp4 de dezenas de GB leva minutos legítimos.
      - chamada curta (HEAD/DELETE/LIST/lifecycle, via _api): `fn` nunca bate ponto, então o
        tempo desde o início é o que conta e `timeout` vira teto de DURAÇÃO TOTAL.

    Existe porque essa classe de bug apareceu 3x em 2026-07-24 (ver S3StalledError) e a correção
    função-por-função não escala: TODA chamada síncrona ao S3 aqui passa por este ponto, então uma
    função nova nasce protegida em vez de ser o quarto incidente.

    Limite consciente: a thread que estourou o teto fica ÓRFÃ (não existe cancelamento limpo de
    uma chamada boto3 em andamento; ela morre com o processo ou quando o socket cair). O ponto do
    watchdog é o app seguir de pé e responder, não fazer faxina. Nunca há corrupção do próximo
    download: o s3transfer escreve num temporário próprio e só renomeia no fim, e o chamador
    (podcast_*/app.py) apaga o workdir inteiro no fim, sucesso ou erro. O efeito colateral REAL,
    se a órfã destravar depois do rmtree, é ela seguir gravando num arquivo já desvinculado: o
    espaço aparece em `df` mas não em `du` até a thread morrer, o que pode fazer o guard-rail de
    disco (PODCAST_AUDIO_DISK_FACTOR e irmãos) recusar a tentativa seguinte sem motivo visível
    no diretório. Se isso aparecer em produção, o sintoma é esse: procure `df` x `du`."""
    estado = {"ultimo": time.monotonic()}
    trava = threading.Lock()

    def bater_ponto(bytes_novos=None, *_args, **_kwargs):
        # Delta <= 0 NÃO é progresso: o s3transfer chama o Callback com valor negativo quando
        # perde o progresso de um GET e vai reiniciar a parte ("all progress for this GetObject
        # has been lost"). Renovar o relógio num reset deixaria um S3 flapando esticar o teto
        # efetivo pra num_download_attempts x timeout (uns 15min segurando o slot da queima).
        if bytes_novos is not None and bytes_novos <= 0:
            return
        with trava:
            estado["ultimo"] = time.monotonic()

    saida = {}

    def _run():
        try:
            saida["valor"] = fn(bater_ponto)
        except BaseException as e:   # repassado INTACTO pro chamador (ClientError, etc.)
            saida["erro"] = e

    t = threading.Thread(target=_run, daemon=True, name=f"s3-{op}"[:60])
    t.start()
    # Checa em fatias de no máximo 5s (produção) OU do próprio timeout, o que for menor: sem
    # isso, um timeout curto (ex.: teste) só seria detectado no próximo múltiplo de 5s, bem
    # depois do teto pedido.
    fatia = min(5.0, timeout)
    while True:
        t.join(timeout=fatia)
        if not t.is_alive():
            break
        with trava:
            parado_ha = time.monotonic() - estado["ultimo"]
        if parado_ha > timeout:
            # Loga aqui (e não só na exceção) porque parte dos chamadores trata o erro em
            # silêncio (best-effort) e a operação precisa ver o stall no journalctl.
            log.warning("s3 stall: %s sem progresso por %.0fs (teto %.0fs), desistindo",
                        op, parado_ha, timeout)
            raise erro(f"{op}: sem progresso por mais de {timeout:.0f}s")
    if "erro" in saida:
        raise saida["erro"]
    return saida.get("valor")


def _api(op: str, chamada, timeout: float = None):
    """Atalho de _com_teto pra chamada CURTA de API (metadado/controle, sem bytes pra medir):
    `chamada` é um callable sem argumentos e o teto é a duração TOTAL da chamada. Toda chamada
    de rede curta deste módulo passa por aqui.

    timeout=None usa config.S3_API_TIMEOUT (60s, o teto geral). O caminho da EXCLUSÃO passa um
    valor menor (config.S3_DELETE_TIMEOUT): ali a chamada é síncrona DENTRO de um request e
    segura uma das 4 threads do gunicorn até responder."""
    return _com_teto(op, config.S3_API_TIMEOUT if timeout is None else timeout,
                     lambda _bater_ponto: chamada())


def upload_file(local_path: str, s3_key: str, stall_timeout: float = None) -> None:
    """Sobe um arquivo local pro bucket, com teto de PROGRESSO (_com_teto): era o segundo ponto
    sem proteção do caminho da extração de MP3, e é usado por praticamente todo o resto do app
    (clipes, thumbs, strips, músicas, montagem do podcast), então proteger AQUI cobre todos."""
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Arquivo não encontrado para upload: {local_path}")
    _com_teto(f"upload {_k(s3_key)}",
              config.S3_STALL_TIMEOUT if stall_timeout is None else stall_timeout,
              lambda bater_ponto: _client().upload_file(
                  local_path, config.S3_BUCKET, _k(s3_key), Callback=bater_ponto))
    log.info("upload ok: %s", _k(s3_key))


def download_file(s3_key: str, local_path: str, stall_timeout: float = None) -> None:
    """Baixa um objeto do bucket para um caminho local, com teto de PROGRESSO (_com_teto): mede
    o tempo desde o ÚLTIMO byte recebido (via Callback do boto3, não o tamanho do arquivo no
    disco). Usado pela queima sob demanda (base sem legenda), pela montagem/corte do podcast e
    pela extração de MP3, todos protegidos de uma vez por passarem por aqui.

    Levanta DownloadStalledError (subclasse de S3StalledError) pra manter o nome histórico do
    incidente de 2026-07-24 nas mensagens de erro de job já gravadas."""
    _com_teto(f"download {_k(s3_key)}",
              config.S3_STALL_TIMEOUT if stall_timeout is None else stall_timeout,
              lambda bater_ponto: _client().download_file(
                  config.S3_BUCKET, _k(s3_key), local_path, Callback=bater_ponto),
              erro=DownloadStalledError)


def exists(s3_key: str) -> bool:
    """True se o objeto existe no bucket. Usado pra checar o cache da queima
    (clip_NN_cap.mp4) antes de decidir entre redirecionar ou queimar.

    Um objeto ausente (ClientError 404) segue devolvendo False, como sempre; um HEAD TRAVADO
    (S3StalledError) PROPAGA de propósito, em vez de virar um False silencioso: aqui False
    significa "não existe" e faz o chamador refazer trabalho caro (queimar/renderizar de novo),
    então mentir sobre um S3 fora do ar seria pior que devolver erro."""
    try:
        _api(f"head {_k(s3_key)}",
             lambda: _client().head_object(Bucket=config.S3_BUCKET, Key=_k(s3_key)))
        return True
    except ClientError:
        return False


def head_size(s3_key: str) -> int:
    """Tamanho (bytes) de um objeto no bucket, via HEAD (sem baixar). Usado pelo
    guard-rail de disco da extração de áudio do podcast, que precisa saber o tamanho
    do final.mp4 remoto ANTES de decidir se baixa."""
    return _api(f"head {_k(s3_key)}",
                lambda: _client().head_object(
                    Bucket=config.S3_BUCKET, Key=_k(s3_key)))["ContentLength"]


def _disposition(download_name: str) -> str:
    """Monta o Content-Disposition de um download, SANITIZANDO o nome aqui na borda.

    O valor chega percent-encoded na URL presignada (o botocore encoda tudo fora de '-._~') e
    entra na string assinada, então não dá pra injetar parâmetro na URL nem CRLF no header, e
    terceiro nenhum consegue adulterar. O que o encoding NÃO resolve: o S3 decodifica e devolve
    o valor VERBATIM no header, então uma aspa dupla no meio fecha o filename="..." e deixa
    acrescentar outro parâmetro (ex.: filename*=UTF-8''outro.nome, que o navegador prioriza).
    Nome de arquivo enganoso em download interno.

    Sanitizar AQUI e não só no chamador é de propósito: os chamadores não são uniformes (o
    clipe passa por app._safe_filename, o episódio monta o nome a partir do filename CRU do
    multipart, que o Werkzeug não sanitiza), e uma função nova que só chame get_presigned_url
    nasceria desprotegida. Mesma regra do app._safe_filename (NFKD -> ASCII, só
    [A-Za-z0-9 ._-]), duplicada em vez de importada porque s3_client não importa app."""
    s = unicodedata.normalize("NFKD", str(download_name or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9 ._-]+", "", s)
    s = re.sub(r"\s+", " ", s).strip().strip(".")[:120].strip()
    return f'attachment; filename="{s or "download"}"'


def get_presigned_url(s3_key: str, expiry_seconds: int = 86400,
                      download_name: str = None) -> str:
    """URL presignada de GET. Com download_name, embute response-content-disposition pra o S3
    devolver Content-Disposition: attachment (força o download com nome amigável em vez de tocar
    inline) sem alterar o objeto nem afetar o player, que pede a mesma key sem o nome.

    ÚNICO grupo de funções deste módulo FORA do _com_teto, de propósito: presignar é assinatura
    LOCAL (HMAC sobre credenciais estáticas + endpoint e região fixos no client), não faz chamada
    de rede nenhuma, então não tem como travar num socket. Envolver num watchdog só criaria uma
    thread por request no caminho quente do player, sem proteger nada."""
    params = {"Bucket": config.S3_BUCKET, "Key": _k(s3_key)}
    if download_name:
        params["ResponseContentDisposition"] = _disposition(download_name)
    return _client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expiry_seconds,
    )


def get_presigned_url_accelerate(s3_key: str, expiry_seconds: int = 86400,
                                 download_name: str = None) -> str:
    """URL presignada de GET via o endpoint accelerate do S3. Dois usos: o áudio que o RunPod
    baixa pra transcrever (ver processor.run_thread1) e, quando config.S3_ACCELERATE_DOWNLOADS
    está ligado, os downloads explícitos do usuário (via get_presigned_download_url).
    O bucket precisa ter Transfer Acceleration habilitado (já está, verificado 2026-07-24).
    Aceita download_name com a MESMA semântica de get_presigned_url: sem isso, acelerar um
    download faria o navegador tocar o arquivo inline em vez de baixar."""
    params = {"Bucket": config.S3_BUCKET, "Key": _k(s3_key)}
    if download_name:
        params["ResponseContentDisposition"] = _disposition(download_name)
    return _accel_client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expiry_seconds,
    )


def get_presigned_download_url(s3_key: str, download_name: str,
                               expiry_seconds: int = 86400) -> str:
    """URL presignada pra DOWNLOAD EXPLÍCITO do usuário (episódio, clipe com ?dl=1, MP3).
    Ponto ÚNICO que decide accelerate x normal, pela flag config.S3_ACCELERATE_DOWNLOADS:
    ligada, sai pelo endpoint accelerate (contorna peering ruim de provedor); desligada, o
    comportamento é IDÊNTICO ao de antes da flag existir. Não usar pra player/thumb/waveform
    nem pra URL que a VPS consome (ver o comentário da flag em config.py)."""
    if config.S3_ACCELERATE_DOWNLOADS:
        return get_presigned_url_accelerate(s3_key, expiry_seconds, download_name=download_name)
    return get_presigned_url(s3_key, expiry_seconds, download_name=download_name)


def get_presigned_put_url(s3_key: str, content_type: str = "video/mp4",
                          expiry_seconds: int = 3600) -> str:
    """URL presignada de PUT (upload). Usada pelo worker de reframe na GPU pra subir o resultado
    SEM credenciais de longa duração (least-privilege). CUIDADO: o Content-Type enviado no PUT TEM
    que ser EXATAMENTE este, senão o S3 responde 403 (é o gotcha nº 1 do presigned PUT)."""
    return _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": config.S3_BUCKET, "Key": _k(s3_key), "ContentType": content_type},
        ExpiresIn=expiry_seconds,
    )


def delete_object(s3_key: str) -> None:
    """Apaga um objeto único (usado pra invalidar o cache da queima ao editar)."""
    _api(f"delete {_k(s3_key)}",
         lambda: _client().delete_object(Bucket=config.S3_BUCKET, Key=_k(s3_key)))


def delete_prefix(prefix: str, timeout: float = None) -> int:
    """Apaga todos os objetos sob um prefixo (ex: 'jobs/{job_id}/'). Lista paginado
    e deleta em lotes de até 1000. Retorna quantos objetos foram removidos.

    Pagina com ContinuationToken na mão em vez do paginator do boto3 porque o paginator é um
    gerador lazy (a chamada de rede acontece escondida dentro do `for`), e cada chamada precisa
    ser visível pra passar pelo _api e ter teto próprio. Um prefixo grande faz N chamadas, cada
    uma com seu teto: o teto é POR chamada (uma faxina legítima e longa não é interrompida), o
    que importa é nenhuma delas pendurar o worker pra sempre.

    timeout: repassado pro _api de CADA chamada. None mantém o teto geral (S3_API_TIMEOUT); o
    caminho da exclusão de projeto passa o menor (S3_DELETE_TIMEOUT) porque roda síncrono
    dentro de um request, segurando uma das 4 threads do gunicorn.

    IDEMPOTENTE: prefixo já vazio lista zero objetos e nem chega a chamar delete_objects,
    devolvendo 0. É o que faz o retry da exclusão ser seguro depois de uma falha no meio."""
    client = _client()
    want_prefix = _k(prefix)
    deleted = 0
    batch = []

    def _apagar_lote(objs):
        _api(f"delete_objects {want_prefix} ({len(objs)})",
             lambda: client.delete_objects(Bucket=config.S3_BUCKET, Delete={"Objects": objs}),
             timeout=timeout)

    token = None
    while True:
        kwargs = {"Bucket": config.S3_BUCKET, "Prefix": want_prefix}
        if token:
            kwargs["ContinuationToken"] = token
        # Prefixa SO a listagem; as keys retornadas pelo S3 ja vem com o prefixo e sao deletadas
        # como estao (nao re-prefixar, senao viraria duplo prefixo).
        page = _api(f"list {want_prefix}", lambda: client.list_objects_v2(**kwargs),
                    timeout=timeout)
        for obj in page.get("Contents", []):
            batch.append({"Key": obj["Key"]})
            if len(batch) == 1000:
                _apagar_lote(batch)
                deleted += len(batch)
                batch = []
        token = page.get("NextContinuationToken")
        if not token:
            break
    if batch:
        _apagar_lote(batch)
        deleted += len(batch)
    log.info("delete prefix %s: %d objeto(s)", prefix, deleted)
    return deleted


def ensure_lifecycle_rule(prefix: str, days: int, rule_id: str) -> bool:
    """Garante (idempotente) uma regra de lifecycle de EXPIRACAO no bucket: objetos sob `prefix`
    expiram apos `days` dias (TTL do 16:9 retido, BACKEND §33). O prefixo entra namespaced via _k
    (staging/ no staging). Faz MERGE por rule_id com as regras existentes (NAO apaga regras de outros
    prefixos). So PUTa quando a regra falta ou difere (steady-state e so um GET read-only). Retorna
    True se aplicou/atualizou, False se ja estava igual. Validado: Hetzner RGW honra lifecycle.

    O GET e o PUT passam pelo _api (teto de config.S3_API_TIMEOUT CADA um): em 2026-07-24 esta
    função travou o BOOT do app por 6min20s sem exceção nenhuma, porque o unico teto era o do
    botocore (60s x 3 tentativas). Quem chama no boot (app.py) tambem passou a rodar isso em
    thread de fundo, entao nem os 2x S3_API_TIMEOUT do pior caso bloqueiam o app subir."""
    client = _client()
    want_prefix = _k(prefix)
    desired = {"ID": rule_id, "Filter": {"Prefix": want_prefix},
               "Status": "Enabled", "Expiration": {"Days": int(days)}}
    try:
        existing = _api("get_lifecycle", lambda: client.get_bucket_lifecycle_configuration(
            Bucket=config.S3_BUCKET)).get("Rules", [])
    except ClientError:
        existing = []   # NoSuchLifecycleConfiguration (bucket sem regra) ou erro transitorio
    current = next((r for r in existing if r.get("ID") == rule_id), None)
    if (current and current.get("Status") == "Enabled"
            and current.get("Expiration", {}).get("Days") == int(days)
            and current.get("Filter", {}).get("Prefix") == want_prefix):
        return False
    others = [r for r in existing if r.get("ID") != rule_id]
    _api("put_lifecycle", lambda: client.put_bucket_lifecycle_configuration(
        Bucket=config.S3_BUCKET, LifecycleConfiguration={"Rules": others + [desired]}))
    log.info("lifecycle rule %s -> prefix=%s days=%d", rule_id, want_prefix, days)
    return True
