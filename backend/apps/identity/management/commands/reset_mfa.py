"""Remove a user's two-step verification when they have lost both their
authenticator and every recovery code.

    python manage.py reset_mfa --email person@example.com

This is an OPERATOR action that replaces hand-editing the database: it
deletes the device and recovery codes, which also invalidates the user's
existing sessions' MFA stamp, and writes an audit entry. The user can then sign
in with their password and enrol again. Verify the person's identity out of
band before running it; it is the one path that weakens an account's sign-in.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.audit import actions
from apps.audit.services import record as audit
from apps.identity import mfa
from apps.identity.models import User


class Command(BaseCommand):
    help = "Remove a user's two-step verification (lost device and recovery codes)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the interactive confirmation (for scripted use).",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        email = opts["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"No account with email {email}.")
        if not mfa.user_has_mfa(user):
            self.stdout.write(
                f"{email} does not have two-step verification enabled. Nothing to do."
            )
            return
        if not opts["yes"]:
            answer = input(
                f"Remove two-step verification for {email}? "
                "Confirm you have verified their identity. [y/N] "
            )
            if answer.strip().lower() not in {"y", "yes"}:
                raise CommandError("Cancelled. Nothing was changed.")
        mfa.disable(user)
        mfa.clear_failures(user)
        audit(
            actions.AUTH_MFA_DISABLED,
            resource=user,
            metadata={"via": "management_command:reset_mfa"},
        )
        self.stdout.write(self.style.SUCCESS(f"Two-step verification removed for {email}."))
