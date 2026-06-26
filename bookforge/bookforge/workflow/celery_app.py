"""Celery application — Redis broker + result backend."""
from __future__ import annotations

from celery import Celery

from bookforge.config import settings

app = Celery(
    "bookforge",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["bookforge.workflow.tasks"],
)

app.conf.update(
    task_acks_late=True,             # redeliver if a worker dies mid-task
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,    # long AI tasks: don't hoard the queue
    task_track_started=True,
    result_expires=86400,
    task_default_retry_delay=10,
    broker_transport_options={"visibility_timeout": 3600},
    task_routes={
        "bookforge.*image*": {"queue": "images"},   # rate-limit-aware queues
        "bookforge.*content*": {"queue": "content"},
    },
)
