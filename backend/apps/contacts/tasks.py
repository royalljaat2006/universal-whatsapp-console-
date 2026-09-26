from celery import shared_task

from apps.contacts.import_csv import run


@shared_task(acks_late=True)
def process_contact_import(job_id: str) -> str:
    """Parse one uploaded CSV and resolve/update contacts (idempotent: a
    finished job is not reprocessed)."""
    return run(job_id)
