"""Structured JSON logging. Never log request bodies, tokens, cookies or document text."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from decision_evidence.db.context import current_request_id, current_tenant

_JOB: dict[str, str | None] = {"job_id": None}
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def set_job_id(job_id: str | None) -> None:
    _JOB["job_id"] = job_id


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": current_request_id.get(),
            "tenant_id": str(current_tenant.get()) if current_tenant.get() else None,
            "job_id": _JOB["job_id"],
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                data[key] = value
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps({k: v for k, v in data.items() if v is not None}, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "botocore", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
