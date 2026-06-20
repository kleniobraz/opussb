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
                    region_name="eu-central-1",
                    config=Config(
                        signature_version="s3v4",
                        retries={"max_attempts": 3, "mode": "standard"},
                    ),
                )
    return _client_singleton


def upload_file(local_path: str, s3_key: str) -> None:
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Arquivo não encontrado para upload: {local_path}")
    _client().upload_file(local_path, config.S3_BUCKET, s3_key)
    log.info("upload ok: %s", s3_key)


def download_file(s3_key: str, local_path: str) -> None:
    """Baixa um objeto do bucket para um caminho local (usado pela queima sob
    demanda, que parte da base sem legenda guardada no S3)."""
    _client().download_file(config.S3_BUCKET, s3_key, local_path)


def exists(s3_key: str) -> bool:
    """True se o objeto existe no bucket. Usado pra checar o cache da queima
    (clip_NN_cap.mp4) antes de decidir entre redirecionar ou queimar."""
    try:
        _client().head_object(Bucket=config.S3_BUCKET, Key=s3_key)
        return True
    except ClientError:
        return False


def get_presigned_url(s3_key: str, expiry_seconds: int = 86400) -> str:
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": config.S3_BUCKET, "Key": s3_key},
        ExpiresIn=expiry_seconds,
    )


def delete_object(s3_key: str) -> None:
    """Apaga um objeto único (usado pra invalidar o cache da queima ao editar)."""
    _client().delete_object(Bucket=config.S3_BUCKET, Key=s3_key)


def delete_prefix(prefix: str) -> int:
    """Apaga todos os objetos sob um prefixo (ex: 'jobs/{job_id}/'). Lista paginado
    e deleta em lotes de até 1000. Retorna quantos objetos foram removidos."""
    client = _client()
    deleted = 0
    batch = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.S3_BUCKET, Prefix=prefix):
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
