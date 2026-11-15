import unicodedata

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


def normalize_name(value: str) -> str:
    """Normalize a name for matching: NFKC, trimmed, single-spaced, casefolded."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class UserManager(BaseUserManager):
    """Manager for the email-login user model (no username field)."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["is_staff"] is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields["is_superuser"] is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Custom user: unique email is the login identifier; no username."""

    username = None
    email = models.EmailField("email address", unique=True)
    first_name = models.CharField("first name", max_length=150)
    last_name = models.CharField("last name", max_length=150)
    email_verified = models.BooleanField(
        default=False,
        help_text="Set when the user has confirmed their email address.",
    )
    deactivated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Set when an administrator deactivates this account. Distinguishes "
            "administrative deactivation from a pending email verification; "
            "accounts with this set are never reactivated by email links."
        ),
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

    def __str__(self) -> str:
        return self.email

    @property
    def is_admin(self) -> bool:
        """An active administrator in the sense of the spec (equal permissions)."""
        return self.is_active and self.is_staff and self.is_superuser


class ApprovedResident(models.Model):
    """A resident name approved for registration by an administrator (UC-01)."""

    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    normalized_first_name = models.CharField(max_length=150, editable=False)
    normalized_last_name = models.CharField(max_length=150, editable=False)
    registered_user = models.OneToOneField(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_resident",
        help_text="The account that has claimed this approved name, if any.",
    )
    is_eligible = models.BooleanField(
        default=True,
        help_text="Uncheck to prevent this name from being used for registration.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_first_name", "normalized_last_name"],
                name="unique_normalized_resident_name",
                violation_error_message=(
                    "An approved resident with this name already exists. "
                    "Identical names must be resolved manually by an administrator."
                ),
            )
        ]
        ordering = ["normalized_last_name", "normalized_first_name"]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def save(self, *args, **kwargs):
        self.first_name = " ".join(self.first_name.split())
        self.last_name = " ".join(self.last_name.split())
        self.normalized_first_name = normalize_name(self.first_name)
        self.normalized_last_name = normalize_name(self.last_name)
        super().save(*args, **kwargs)

    @property
    def is_claimed(self) -> bool:
        return self.registered_user_id is not None
