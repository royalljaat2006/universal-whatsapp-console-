from celery import shared_task

from apps.reports.exports import run


@shared_task(acks_late=True)
def run_export(job_id: str) -> str:
    """Build one CSV export (idempotent: finished jobs are not rebuilt)."""
    return run(job_id)
