from __future__ import annotations

from collections.abc import Iterator

from decision_evidence.storage.port import ObjectNotFound


class MemoryStorage:
    """In-memory adapter for unit tests. Not used by any running service."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key][0]
        except KeyError as exc:
            raise ObjectNotFound(key) from exc

    def stream(self, key: str, chunk_size: int = 65536) -> Iterator[bytes]:
        data = self.get(key)
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def list_keys(self, prefix: str) -> Iterator[str]:
        yield from sorted(k for k in self.objects if k.startswith(prefix))

    def ping(self) -> None:
        return None
