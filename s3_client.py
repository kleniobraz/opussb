import os
import logging
import threading

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import config

log = logging.getLogger("opussb.s3")

# Cliente boto3 cacheado: criar um client tem custo (sessão + resolução de endpoint)
# e antes a gente recriava em TODA chamada (upload/download/exists/presign), inclusive
# no caminho quente da queima sob demanda. Clients do boto3 são thread-safe pra chamadas,
# então um singleton guardado sob lock atende os 4 threads do gunicorn com segurança.
_client_singleton = None
_client_lock = threading.Lock()


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


def upload_file(local_path: str, s3_key: str) -> None:
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Arquivo não encontrado para upload: {local_path}")
    _client().upload_file(local_path, config.S3_BUCKET, _k(s3_key))
    log.info("upload ok: %s", _k(s3_key))


def download_file(s3_key: str, local_path: str) -> None:
    """Baixa um objeto do bucket para um caminho local (usado pela queima sob
    demanda, que parte da base sem legenda guardada no S3)."""
    _client().download_file(config.S3_BUCKET, _k(s3_key), local_path)


def exists(s3_key: str) -> bool:
    """True se o objeto existe no bucket. Usado pra checar o cache da queima
    (clip_NN_cap.mp4) antes de decidir entre redirecionar ou queimar."""
    try:
        _client().head_object(Bucket=config.S3_BUCKET, Key=_k(s3_key))
        return True
    except ClientError:
        return False


def head_size(s3_key: str) -> int:
    """Tamanho (bytes) de um objeto no bucket, via HEAD (sem baixar). Usado pelo
    guard-rail de disco da extração de áudio do podcast, que precisa saber o tamanho
    do final.mp4 remoto ANTES de decidir se baixa."""
    return _client().head_object(Bucket=config.S3_BUCKET, Key=_k(s3_key))["ContentLength"]


def get_presigned_url(s3_key: str, expiry_seconds: int = 86400,
                      download_name: str = None) -> str:
    """URL presignada de GET. Com download_name, embute response-content-disposition pra o S3
    devolver Content-Disposition: attachment (força o download com nome amigável em vez de tocar
    inline) sem alterar o objeto nem afetar o player, que pede a mesma key sem o nome."""
    params = {"Bucket": config.S3_BUCKET, "Key": _k(s3_key)}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    return _client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expiry_seconds,
    )


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
    _client().delete_object(Bucket=config.S3_BUCKET, Key=_k(s3_key))


def delete_prefix(prefix: str) -> int:
    """Apaga todos os objetos sob um prefixo (ex: 'jobs/{job_id}/'). Lista paginado
    e deleta em lotes de até 1000. Retorna quantos objetos foram removidos."""
    client = _client()
    deleted = 0
    batch = []
    paginator = client.get_paginator("list_objects_v2")
    # Prefixa SO a listagem; as keys retornadas pelo S3 ja vem com o prefixo e sao deletadas
    # como estao (nao re-prefixar, senao viraria duplo prefixo).
    for page in paginator.paginate(Bucket=config.S3_BUCKET, Prefix=_k(prefix)):
        for obj in page.get("Contents", []):
            batch.append({"Key": obj["Key"]})
            if len(batch) == 1000:
                client.delete_objects(Bucket=config.S3_BUCKET, Delete={"Objects": batch})
                deleted += len(batch)
                batch = []
    if batch:
        client.delete_objects(Bucket=config.S3_BUCKET, Delete={"Objects": batch})
        deleted += len(batch)
    log.info("delete prefix %s: %d objeto(s)", prefix, deleted)
    return deleted


def ensure_lifecycle_rule(prefix: str, days: int, rule_id: str) -> bool:
    """Garante (idempotente) uma regra de lifecycle de EXPIRACAO no bucket: objetos sob `prefix`
    expiram apos `days` dias (TTL do 16:9 retido, BACKEND §33). O prefixo entra namespaced via _k
    (staging/ no staging). Faz MERGE por rule_id com as regras existentes (NAO apaga regras de outros
    prefixos). So PUTa quando a regra falta ou difere (steady-state e so um GET read-only). Retorna
    True se aplicou/atualizou, False se ja estava igual. Validado: Hetzner RGW honra lifecycle."""
    client = _client()
    want_prefix = _k(prefix)
    desired = {"ID": rule_id, "Filter": {"Prefix": want_prefix},
               "Status": "Enabled", "Expiration": {"Days": int(days)}}
    try:
        existing = client.get_bucket_lifecycle_configuration(
            Bucket=config.S3_BUCKET).get("Rules", [])
    except ClientError:
        existing = []   # NoSuchLifecycleConfiguration (bucket sem regra) ou erro transitorio
    current = next((r for r in existing if r.get("ID") == rule_id), None)
    if (current and current.get("Status") == "Enabled"
            and current.get("Expiration", {}).get("Days") == int(days)
            and current.get("Filter", {}).get("Prefix") == want_prefix):
        return False
    others = [r for r in existing if r.get("ID") != rule_id]
    client.put_bucket_lifecycle_configuration(
        Bucket=config.S3_BUCKET, LifecycleConfiguration={"Rules": others + [desired]})
    log.info("lifecycle rule %s -> prefix=%s days=%d", rule_id, want_prefix, days)
    return True
