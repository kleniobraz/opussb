import boto3
from botocore.config import Config
import config


def _client():
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=config.S3_ACCESS_KEY,
        aws_secret_access_key=config.S3_SECRET_KEY,
        region_name="eu-central-1",
        config=Config(signature_version="s3v4"),
    )


def upload_file(local_path: str, s3_key: str) -> None:
    import os
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Arquivo não encontrado para upload: {local_path}")
    _client().upload_file(local_path, config.S3_BUCKET, s3_key)
    print(f"[s3] upload ok: {s3_key}")


def get_presigned_url(s3_key: str, expiry_seconds: int = 86400) -> str:
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": config.S3_BUCKET, "Key": s3_key},
        ExpiresIn=expiry_seconds,
    )


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
    print(f"[s3] delete prefix {prefix}: {deleted} objeto(s)")
    return deleted
