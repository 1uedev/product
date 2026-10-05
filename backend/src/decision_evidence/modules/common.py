"""Shared helpers for module services and routers."""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from fastapi import Header, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.errors import ApiError

T = TypeVar("T")
MAX_PAGE_SIZE = 100


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class Paging:
    def __init__(self, limit: int = Query(25, ge=1, le=MAX_PAGE_SIZE), offset: int = Query(0, ge=0, le=100_000)) -> None:
        self.limit, self.offset = limit, offset


def tenant_settings(s: Session) -> m.TenantSettings:
    row = s.scalar(select(m.TenantSettings))
    if row is None:
        raise ApiError(500, "tenant_not_initialised", "Workspace ist nicht vollständig eingerichtet")
    return row


def etag(version: int) -> str:
    return f'"{version}"'


def require_if_match(if_match: str | None = Header(default=None)) -> int:
    if if_match is None:
        raise ApiError(428, "precondition_required", "If-Match fehlt", "Bitte die zuletzt geladene Version mitsenden.")
    mt = re.fullmatch(r'\s*(?:W/)?"?(\d+)"?\s*', if_match)
    if not mt:
        raise ApiError(400, "bad_if_match", "If-Match ungültig")
    return int(mt.group(1))


def check_version(current: int, expected: int) -> None:
    if current != expected:
        raise ApiError(412, "version_conflict", "Der Datensatz wurde zwischenzeitlich geändert",
                       "Bitte neu laden und die Änderung erneut vornehmen.", current_version=current)


_FORMULA = re.compile(r"^[=+\-@\t\r]")


def csv_safe(value: Any) -> Any:
    """Neutralise spreadsheet formula injection for string cells (OWASP CSV injection)."""
    if isinstance(value, str) and _FORMULA.match(value):
        return "'" + value
    return value


def to_csv(header: list[str], rows: Iterable[Iterable[Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_safe("" if v is None else v) for v in row])
    return "﻿" + buf.getvalue()


def now() -> datetime:
    return datetime.now(UTC)


def uuid_str(v: uuid.UUID | None) -> str | None:
    return None if v is None else str(v)
