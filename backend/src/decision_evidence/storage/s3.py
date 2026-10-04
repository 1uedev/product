from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from decision_evidence.config import get_settings
from decision_evidence.storage.port import ObjectNotFound, ObjectStorage


class S3Storage:
    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                raise ObjectNotFound(key) from exc
            raise

    def stream(self, key: str, chunk_size: int = 65536) -> Iterator[bytes]:
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                raise ObjectNotFound(key) from exc
            raise
        try:
            yield from body.iter_chunks(chunk_size)
        finally:
            body.close()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def list_keys(self, prefix: str) -> Iterator[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def ping(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)


def build_s3_client() -> Any:
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        region_name=s.s3_region,
        aws_access_key_id=s.s3_access_key.get_secret_value() if s.s3_access_key else None,
        aws_secret_access_key=s.s3_secret_key.get_secret_value() if s.s3_secret_key else None,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                      retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=30),
    )


_override: ObjectStorage | None = None


def set_storage_override(storage: ObjectStorage | None) -> None:
    """Test hook: unit tests inject the in-memory adapter."""
    global _override
    _override = storage
    get_storage.cache_clear()


@lru_cache
def get_storage() -> ObjectStorage:
    if _override is not None:
        return _override
    return S3Storage(build_s3_client(), get_settings().s3_bucket)
