"""Celery application. Queue topology and routing live in workers/queues.py."""

import os

from celery import Celery

from workers.queues import BEAT_SCHEDULE, TASK_QUEUES, TASK_ROUTES

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("whatsapp_platform")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.task_queues = TASK_QUEUES
app.conf.task_routes = TASK_ROUTES
app.conf.task_default_queue = "default"
app.conf.beat_schedule = BEAT_SCHEDULE
app.autodiscover_tasks()

# Propagates request/organization context from publisher to worker logs.
import core.celery_context  # noqa: E402, F401
