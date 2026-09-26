"""Password reset and email verification tokens.

Both reuse Django's own ``PasswordResetTokenGenerator`` — HMAC-based, time-
limited (``PASSWORD_RESET_TIMEOUT``), and it never needs a DB row: the token
itself encodes a timestamp and is verified against a hash of fields that
change once used (password hash for reset, the verified flag for
verification), so a used or stale token stops working on its own. This is
Django's standard mechanism, not custom cryptography.
"""

from django.contrib.auth.tokens import PasswordResetTokenGenerator

from apps.identity.models import User


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "apps.identity.tokens.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user: User, timestamp: int) -> str:
        # Differs from the base class only in salt and in hashing
        # is_email_verified instead of the password — once verified, any
        # older unverified-state token stops validating.
        return f"{user.pk}{user.is_email_verified}{timestamp}"


email_verification_token = EmailVerificationTokenGenerator()
