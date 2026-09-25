"""Abstract base models shared by all domain modules."""

from django.db import models

from core.ids import uuid7


class BaseModel(models.Model):
    """UUIDv7 primary key + audit timestamps."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
