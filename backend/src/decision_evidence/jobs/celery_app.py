from __future__ import annotations

from celery import Celery

from decision_evidence.config import get_settings

QUEUE = "decision_evidence"
TASK_RUN_JOB = "decision_evidence.run_job"
TASK_RECOVER = "decision_evidence.recover_stale"
TASK_CLEANUP = "decision_evidence.cleanup_files"


def make_celery() -> Celery:
    s = get_settings()
    app = Celery("decision_evidence", broker=s.rabbitmq_url.get_secret_value() if s.rabbitmq_url else None)
    app.conf.update(
        task_default_queue=QUEUE,
        task_serializer="json",
        accept_content=["json"],
        task_acks_late=True,                 # a crashed worker leaves the message unacked -> redelivery
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=5,
        broker_transport_options={"confirm_publish": True, "max_retries": 2, "interval_start": 0, "interval_step": 1,
                                  "interval_max": 2},
        result_backend=None,
        task_ignore_result=True,
        enable_utc=True,
        timezone="UTC",
        beat_schedule={
            "recover-stale-jobs": {"task": TASK_RECOVER, "schedule": 30.0},
            "cleanup-orphaned-files": {"task": TASK_CLEANUP, "schedule": 300.0},
        },
        worker_hijack_root_logger=False,
        # RabbitMQ 4.x forbids the transient non-exclusive queues used by remote control (pidbox); we do not need them
        worker_enable_remote_control=False,
        worker_send_task_events=False,
        worker_cancel_long_running_tasks_on_connection_loss=False,
    )
    return app


_celery: Celery | None = None


def get_celery() -> Celery:
    global _celery
    if _celery is None:
        _celery = make_celery()
    return _celery
