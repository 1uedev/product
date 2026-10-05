"""Storage port. Adapters: S3 (production/demo via SeaweedFS), in-memory (unit tests only)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Protocol


class ObjectNotFound(Exception):
    pass


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def stream(self, key: str, chunk_size: int = 65536) -> Iterator[bytes]: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def list_keys(self, prefix: str) -> Iterator[str]: ...
    def ping(self) -> None: ...


def new_object_key(tenant_id: uuid.UUID) -> str:
    """Random, tenant-prefixed key. The browser never supplies or sees it."""
    return f"tenant/{tenant_id}/{uuid.uuid4().hex}"


def assert_key_in_tenant(key: str, tenant_id: uuid.UUID) -> None:
    if not key.startswith(f"tenant/{tenant_id}/"):
        raise PermissionError("object key outside tenant prefix")
