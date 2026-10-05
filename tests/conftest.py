"""Shared pytest configuration. Integration tests need PostgreSQL with all migrations applied
(see scripts/test_integration.sh) and the DB_* environment variables for the runtime roles."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("PUBLIC_ORIGIN", "http://localhost:8480")


def pytest_configure(config):  # type: ignore[no-untyped-def]
    config.addinivalue_line("markers", "integration: needs a real PostgreSQL (see scripts/test_integration.sh)")


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
