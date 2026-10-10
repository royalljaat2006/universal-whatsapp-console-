"""Global identity. A User is NOT tenant-owned: one person can belong to many
organizations through organizations.Membership."""

from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower

from core.ids import uuid7


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra: Any) -> "User":
        if not email:
            raise ValueError("email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> "User":
        extra.update(is_staff=True, is_superuser=True)
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    email = models.EmailField(max_length=254, unique=True)
    full_name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    # Advisory only — never blocks login (see apps.identity.api). The frontend
    # may show a reminder banner; nothing server-side is gated on this today.
    is_email_verified = models.BooleanField(default=False)
    # Django admin access for platform operators only — unrelated to tenant RBAC.
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        constraints = [
            # Case-insensitive uniqueness: "A@x.com" and "a@x.com" are one account.
            models.UniqueConstraint(Lower("email"), name="identity_user_email_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.email


class TOTPDevice(models.Model):
    """A user's authenticator-app enrolment (RFC 6238 TOTP). One per user.

    ``confirmed_at`` is null until the user proves possession by entering a
    valid code; an unconfirmed device never gates login. The secret is stored
    only encrypted (core.crypto, the same Fernet keys as provider tokens) and
    is never returned again after enrolment. ``last_used_step`` is the TOTP
    time-step of the most recently accepted code: a code is accepted only for
    a strictly later step, so an observed code cannot be replayed."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    encrypted_secret = models.BinaryField(editable=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"TOTP for {self.user_id} ({'confirmed' if self.confirmed_at else 'pending'})"


class RecoveryCode(models.Model):
    """A single-use fallback for a lost authenticator. Only a keyed hash of the
    code is stored (see apps.identity.mfa.hash_recovery_code); the plaintext is
    shown once at generation."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "code_hash"], name="identity_recoverycode_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"recovery code for {self.user_id} ({'used' if self.used_at else 'unused'})"
