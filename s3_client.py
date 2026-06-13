import boto3
from botocore.config import Config
import config


def _client():
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=config.S3_ACCESS_KEY,
        aws_secret_access_key=config.S3_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


def upload_file(local_path: str, s3_key: str) -> None:
    _client().upload_file(local_path, config.S3_BUCKET, s3_key)


def get_presigned_url(s3_key: str, expiry_seconds: int = 86400) -> str:
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": config.S3_BUCKET, "Key": s3_key},
        ExpiresIn=expiry_seconds,
    )
