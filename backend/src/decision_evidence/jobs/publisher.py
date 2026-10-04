"""Outbox publisher. Runs with role de_outbox: it can read mediation metadata and mark rows, nothing else.

Delivery is at-least-once: a crash between broker confirmation and marking the row publishes the message again;
the worker's claim logic makes that harmless.
"""

from __future__ import annotations

import json
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from decision_evidence.config import get_settings
from decision_evidence.db.engines import get_engine
from decision_evidence.jobs.celery_app import QUEUE, TASK_RUN_JOB, get_celery
from decision_evidence.observability.logging import configure_logging, get_logger

log = get_logger("decision_evidence.publisher")
HEARTBEAT_FILE = Path("/tmp/publisher-alive")  # noqa: S108 - container-local liveness marker
_stop = False


def build_envelope(row: dict) -> dict:
    return {
        "event_id": str(row["id"]),
        "type": row["event_type"],
        "schema_version": row["schema_version"],
        "tenant_id": str(row["tenant_id"]),
        "job_id": str(row["job_id"]),
        "occurred_at": datetime.now(UTC).isoformat(),
        "correlation_id": row["correlation_id"],
    }


def publish_one() -> bool:
    """Publish a single due outbox row. Returns False when nothing was due."""
    engine = get_engine("outbox")
    with engine.connect() as conn:
        with conn.begin():
            row = conn.execute(text(
                "SELECT id, tenant_id, job_id, event_type, schema_version, correlation_id, attempts FROM infra.outbox "
                "WHERE published_at IS NULL AND available_at <= now() ORDER BY available_at "
                "LIMIT 1 FOR UPDATE SKIP LOCKED")).mappings().first()
            if row is None:
                return False
            envelope = build_envelope(dict(row))
            try:
                get_celery().send_task(TASK_RUN_JOB, args=[envelope], queue=QUEUE, task_id=str(uuid.uuid4()),
                                       headers={"correlation_id": row["correlation_id"]})
            except Exception as exc:
                attempts = row["attempts"] + 1
                backoff = min(60, 2 ** min(attempts, 6))
                conn.execute(text(
                    "UPDATE infra.outbox SET attempts = :a, last_error_code = :c, available_at = :t WHERE id = :i"),
                    {"a": attempts, "c": type(exc).__name__[:100], "t": datetime.now(UTC) + timedelta(seconds=backoff), "i": row["id"]})
                log.warning("broker publish failed", extra={"outbox_id": str(row["id"]), "error": type(exc).__name__, "retry_in": backoff})
                return True
            conn.execute(text("UPDATE infra.outbox SET published_at = now(), attempts = attempts + 1, last_error_code = NULL WHERE id = :i"),
                         {"i": row["id"]})
            log.info("published", extra={"outbox_id": str(row["id"]), "job_id": str(row["job_id"])})
            return True


def main() -> None:
    global _stop
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.validate_for_runtime(needs={"broker"})
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("_stop", True))
    signal.signal(signal.SIGINT, lambda *_: globals().__setitem__("_stop", True))
    log.info("outbox publisher started")
    while not _stop:
        try:
            worked = publish_one()
            HEARTBEAT_FILE.write_text(json.dumps({"ts": time.time()}))
        except Exception:
            log.exception("publisher loop error")
            worked = False
            time.sleep(2)
        if not worked:
            time.sleep(0.5)
    log.info("outbox publisher stopped")


if __name__ == "__main__":
    main()
