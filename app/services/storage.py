# app/services/storage.py
import os
import boto3
from botocore.exceptions import ClientError

AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)


def upload_file_bytes(data: bytes, key: str, content_type: str):
    """
    Upload raw bytes to S3.
    """
    s3.put_object(
        Bucket=S3_BUCKET_NAME,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return key


def file_exists(key: str) -> bool:
    """
    Check if a file exists in S3.
    """
    try:
        s3.head_object(Bucket=S3_BUCKET_NAME, Key=key)
        return True
    except ClientError:
        return False


def generate_download_url(key: str, expires_seconds: int = 3600) -> str:
    """
    Generate a temporary secure download URL.
    """
    return s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": S3_BUCKET_NAME,
            "Key": key,
        },
        ExpiresIn=expires_seconds,
    )


def delete_file(key: str):
    """
    Delete file from S3.
    """
    s3.delete_object(
        Bucket=S3_BUCKET_NAME,
        Key=key,
    )