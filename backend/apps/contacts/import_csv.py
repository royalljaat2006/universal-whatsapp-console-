"""CSV contact import — the read-direction counterpart of apps.reports.exports.

One row per contact. Required column: ``phone_number``. Recognised optional
columns: ``display_name``, ``tags`` (comma-separated tag names, merged with
the job's own ``tag_name`` if any), ``opt_in_status``
(unknown|opted_in|opted_out). Every other column becomes a contact attribute
— same shape/limits as ``ContactUpdateSerializer.attributes`` (at most 50
keys, ``[A-Za-z][A-Za-z0-9_]{0,39}``); columns that don't match are dropped
rather than failing the row, since they're enrichment data, not identity.

A bad individual row never aborts the job: it's counted in ``failed_count``
and (up to ``MAX_REPORTED_ERRORS``) recorded in ``row_errors``. Only a
structural problem (missing file, missing ``phone_number`` column, too many
rows) fails the whole job before any row is processed.
"""

import csv
import io
import re
from collections.abc import Iterable
from typing import Any

from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record as audit
from apps.businesses.models import Business
from apps.contacts.management_services import create_tag, update_contact
from apps.contacts.models import Contact, ContactIdentity, ContactImportJob, Tag
from apps.contacts.services import (
    ContactIdentifiers,
    InvalidIdentifierError,
    normalize_phone,
    resolve_contact,
)
from apps.media import storage
from apps.media.storage import MediaStorageError
from apps.messaging.services import events

MAX_IMPORT_ROWS = 5000
MAX_REPORTED_ERRORS = 200
_RECOGNISED_COLUMNS = {"phone_number", "display_name", "tags", "opt_in_status"}
_ATTRIBUTE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
_OPT_IN_VALUES = {choice for choice, _ in Contact.OptInStatus.choices}


class ImportAbortedError(Exception):
    """A structural problem — nothing in the file could be processed."""


def _clean_headers(fieldnames: Iterable[str] | None) -> list[str]:
    return [(name or "").strip().lower() for name in (fieldnames or [])]


def _split_tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


def _attributes_from_row(row: dict[str, str]) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for key, value in row.items():
        if key in _RECOGNISED_COLUMNS or not key:
            continue
        if not _ATTRIBUTE_KEY.fullmatch(key):
            continue
        attributes[key] = (value or "").strip()[:500]
        if len(attributes) >= 50:
            break
    return attributes


def _get_or_create_tag(business: Business, name: str, *, actor: Any) -> Tag:
    existing = (
        Tag.objects.filter(business=business)
        .annotate(n=Lower("name"))
        .filter(n=name.lower())
        .first()
    )
    if existing is not None:
        return existing
    return create_tag(business=business, name=name, actor=actor)


def _process_row(
    business: Business, row: dict[str, str], job_tag_names: list[str], *, actor: Any
) -> str:
    """Returns "created" or "updated"; raises on a row-level failure."""
    raw_phone = (row.get("phone_number") or "").strip()
    if not raw_phone:
        raise InvalidIdentifierError("phone_number is required")
    phone = normalize_phone(raw_phone)

    pre_existing = ContactIdentity.objects.filter(
        business=business, kind=ContactIdentity.Kind.PHONE, scope="", value=phone
    ).exists()

    contact = resolve_contact(business, ContactIdentifiers(phone_number=phone))

    changes: dict[str, Any] = {}
    display_name = (row.get("display_name") or "").strip()
    if display_name:
        changes["display_name"] = display_name[:200]
    attributes = _attributes_from_row(row)
    if attributes:
        changes["attributes"] = attributes
    opt_in_status = (row.get("opt_in_status") or "").strip().lower()
    if opt_in_status:
        if opt_in_status not in _OPT_IN_VALUES:
            raise InvalidIdentifierError(f"invalid opt_in_status '{opt_in_status}'")
        changes["opt_in_status"] = opt_in_status
    if changes:
        update_contact(contact, changes, actor=actor)

    tag_names = list(job_tag_names) + _split_tags(row.get("tags") or "")
    if tag_names:
        tags = [
            _get_or_create_tag(business, name, actor=actor) for name in dict.fromkeys(tag_names)
        ]
        contact.tags.add(*tags)

    if changes or tag_names:
        events.contact_updated(contact)

    return "updated" if pre_existing else "created"


def run(job_id: Any) -> str:
    job = ContactImportJob.objects.select_related("organization", "business", "requested_by").get(
        pk=job_id
    )
    if job.status not in {ContactImportJob.Status.PENDING, ContactImportJob.Status.RUNNING}:
        return job.status
    job.status, job.started_at = ContactImportJob.Status.RUNNING, timezone.now()
    job.save(update_fields=["status", "started_at", "updated_at"])

    try:
        rows = _read_rows(job)
    except ImportAbortedError as exc:
        job.status, job.error = ContactImportJob.Status.FAILED, str(exc)[:500]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at", "updated_at"])
        return job.status

    job_tag_names = _split_tags(job.tag_name)
    created = updated = failed = 0
    row_errors: list[dict[str, Any]] = []
    actor = job.requested_by
    for number, row in enumerate(rows, start=1):
        try:
            with transaction.atomic():
                outcome = _process_row(job.business, row, job_tag_names, actor=actor)
            created += outcome == "created"
            updated += outcome == "updated"
        except Exception as exc:
            failed += 1
            if len(row_errors) < MAX_REPORTED_ERRORS:
                row_errors.append({"row": number, "error": str(exc)[:200]})

    job.status = (
        ContactImportJob.Status.SUCCEEDED
        if failed == 0
        else (
            ContactImportJob.Status.SUCCEEDED_WITH_ERRORS
            if (created or updated)
            else ContactImportJob.Status.FAILED
        )
    )
    job.total_rows = len(rows)
    job.created_count = created
    job.updated_count = updated
    job.failed_count = failed
    job.row_errors = row_errors
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "total_rows",
            "created_count",
            "updated_count",
            "failed_count",
            "row_errors",
            "finished_at",
            "updated_at",
        ]
    )
    audit(
        actions.CONTACTS_IMPORTED,
        organization=job.organization,
        actor=actor,
        resource=job,
        metadata={
            "business_id": str(job.business_id),
            "total_rows": job.total_rows,
            "created": created,
            "updated": updated,
            "failed": failed,
        },
    )
    return job.status


def _read_rows(job: ContactImportJob) -> list[dict[str, str]]:
    try:
        handle = storage.open_file(job.storage_key)
    except MediaStorageError as exc:
        raise ImportAbortedError(f"Could not read the uploaded file: {exc}") from exc
    try:
        text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        # Reading .fieldnames triggers the header parse; reassigning it then
        # makes every subsequent row dict use the cleaned keys.
        reader.fieldnames = _clean_headers(reader.fieldnames)
        if "phone_number" not in reader.fieldnames:
            raise ImportAbortedError("The file needs a 'phone_number' column.")
        rows: list[dict[str, str]] = []
        for raw_row in reader:
            rows.append({k: v for k, v in raw_row.items() if k})
            if len(rows) > MAX_IMPORT_ROWS:
                raise ImportAbortedError(f"The file has more than {MAX_IMPORT_ROWS} rows.")
        return rows
    finally:
        handle.close()
