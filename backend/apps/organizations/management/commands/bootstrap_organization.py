"""Create the first organization and its owner (idempotent on the slug).

    python manage.py bootstrap_organization --name "Eko" --owner-email ops@example.com

The owner's password is read from the BOOTSTRAP_OWNER_PASSWORD environment
variable or prompted for; it is never accepted on the command line (shell
history / process lists would leak it).
"""

import getpass
import os
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils.text import slugify

from apps.organizations.models import Organization
from apps.organizations.services import create_organization


class Command(BaseCommand):
    help = "Create an organization with an owner user."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--name", required=True)
        parser.add_argument("--slug")
        parser.add_argument("--owner-email", required=True)
        parser.add_argument("--owner-name", default="")

    @transaction.atomic
    def handle(self, *args: Any, **opts: Any) -> None:
        slug = opts["slug"] or slugify(opts["name"])
        existing = Organization.objects.filter(slug=slug).first()
        if existing:
            self.stdout.write(f"organization '{slug}' already exists: {existing.pk}")
            return

        User = get_user_model()
        email = opts["owner_email"].lower()
        user = User.objects.filter(email=email).first()
        if user is None:
            password = os.environ.get("BOOTSTRAP_OWNER_PASSWORD") or getpass.getpass(
                "Owner password: "
            )
            if not password:
                raise CommandError("a password is required for a new owner")
            validate_password(password)
            user = User.objects.create_user(
                email=email, password=password, full_name=opts["owner_name"]
            )

        organization = create_organization(name=opts["name"], slug=slug, owner=user)
        self.stdout.write(self.style.SUCCESS(f"created organization {organization.pk} ({slug})"))
