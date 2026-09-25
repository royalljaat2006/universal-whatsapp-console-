"""Create a business (workspace) inside an organization. Idempotent on slug.

python manage.py create_business --organization eko --name "Kiosk"
python manage.py create_business --organization eko --name "EPS" --slug eps
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.text import slugify

from apps.businesses.models import Business
from apps.businesses.services import create_business
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = "Create a business (workspace) in an organization."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--organization", required=True, help="organization slug")
        parser.add_argument("--name", required=True)
        parser.add_argument("--slug")
        parser.add_argument("--description", default="")

    def handle(self, *args: Any, **opts: Any) -> None:
        organization = Organization.objects.filter(slug=opts["organization"]).first()
        if organization is None:
            raise CommandError("organization not found")
        slug = opts["slug"] or slugify(opts["name"])
        existing = Business.objects.filter(organization=organization, slug=slug).first()
        if existing:
            self.stdout.write(f"business '{slug}' already exists: {existing.pk}")
            return
        business = create_business(
            organization=organization,
            name=opts["name"],
            slug=slug,
            description=opts["description"],
        )
        self.stdout.write(self.style.SUCCESS(f"created business {business.pk} ({slug})"))
