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
)

celery_app.autodiscover_tasks(["api.tasks"])
