"""Celery application — broker and result backend from REDIS_URL.

Run worker:
    celery -A api.worker worker --loglevel=info
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

from celery import Celery

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("pdm_engine", broker=redis_url, backend=redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Fail fast when Redis is unavailable so inline fallback kicks in quickly.
    broker_transport_options={"socket_connect_timeout": 3, "socket_timeout": 3},
    result_backend_transport_options={"socket_connect_timeout": 3, "socket_timeout": 3},
    beat_schedule={
        "recheck-due-listings": {
            "task": "api.tasks.run_recheck_batch_task",
            "schedule": float(os.environ.get("RECHECK_BEAT_SECONDS", "300")),  # default 5m
        },
    },
)

celery_app.autodiscover_tasks(["api.tasks"])
