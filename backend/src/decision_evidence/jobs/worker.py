"""Celery entry point: ``celery -A decision_evidence.jobs.worker worker -B``."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from celery.signals import setup_logging, worker_ready

from decision_evidence.config import get_settings
from decision_evidence.jobs import runner
from decision_evidence.jobs.celery_app import TASK_CLEANUP, TASK_RECOVER, TASK_RUN_JOB, get_celery
from decision_evidence.observability.logging import configure_logging

HEARTBEAT_FILE = Path("/tmp/worker-alive")  # noqa: S108 - container-local liveness marker


def touch_heartbeat() -> None:
    HEARTBEAT_FILE.write_text(json.dumps({"ts": time.time()}))


settings = get_settings()
settings.validate_for_runtime(needs={"broker", "storage", "ai"})
app = get_celery()


@setup_logging.connect
def _logging(**_: Any) -> None:
    configure_logging(settings.log_level)


@worker_ready.connect
def _ready(**_: Any) -> None:
    touch_heartbeat()


# import for side effects: handler registration
from decision_evidence.jobs import handlers  # noqa: E402, F401


@app.task(name=TASK_RUN_JOB, acks_late=True)
def run_job(envelope: dict[str, Any]) -> str:
    if envelope.get("schema_version") != 1 or envelope.get("type") != "job.enqueued":
        raise ValueError("unsupported event envelope")
    return runner.execute_job(uuid.UUID(envelope["tenant_id"]), uuid.UUID(envelope["job_id"]))


@app.task(name=TASK_RECOVER)
def recover_stale() -> int:
    touch_heartbeat()  # proves the whole pipeline (beat -> broker -> worker) is alive
    return runner.recover_stale_jobs()


@app.task(name=TASK_CLEANUP)
def cleanup_files() -> int:
    return runner.cleanup_stale_files()
