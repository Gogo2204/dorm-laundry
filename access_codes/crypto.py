"""PIN encryption and keyed fingerprints.

PINs are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256, i.e.
authenticated encryption) using the dedicated ``PIN_ENCRYPTION_KEY`` setting.
Duplicate detection uses an HMAC-SHA256 fingerprint keyed with a key derived
from the same secret, so plaintext PINs are never stored or compared directly.

Key management:
- The key lives only in the environment (``.env`` locally, the server's
  environment in production) and must be backed up securely; losing it makes
  all stored PINs unreadable.
- Rotation: decrypt-and-re-encrypt requires the old key, so rotate by
  exporting assignments while the old key is loaded, switching the key, and
  re-importing — or simply retire the inventory and import fresh PINs.
- A missing or malformed key raises ``ImproperlyConfigured`` on first use;
  PIN features fail loudly instead of silently storing plaintext.
"""

import hashlib
import hmac
import re
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError

_KEY_HELP = (
    "PIN_ENCRYPTION_KEY must be a valid Fernet key. Generate one with: "
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


@lru_cache(maxsize=4)
def _fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise ImproperlyConfigured(_KEY_HELP)


@lru_cache(maxsize=4)
def _fingerprint_key(key: str) -> bytes:
    # Domain-separated key so fingerprints are not keyed directly by the
    # encryption key material.
    return hashlib.sha256(b"dorm-laundry.pin-fingerprint:" + key.encode()).digest()


def _require_key() -> str:
    key = settings.PIN_ENCRYPTION_KEY
    if not key:
        raise ImproperlyConfigured(f"PIN_ENCRYPTION_KEY is not set. {_KEY_HELP}")
    return key


def encrypt_pin(pin: str) -> str:
    return _fernet(_require_key()).encrypt(pin.encode()).decode()


def decrypt_pin(token: str) -> str:
    try:
        return _fernet(_require_key()).decrypt(token.encode()).decode()
    except InvalidToken:
        raise ImproperlyConfigured(
            "Stored PIN could not be decrypted; PIN_ENCRYPTION_KEY does not "
            "match the key the inventory was encrypted with."
        )


def pin_fingerprint(pin: str) -> str:
    """Deterministic keyed fingerprint for duplicate detection (no plaintext)."""
    return hmac.new(_fingerprint_key(_require_key()), pin.encode(), hashlib.sha256).hexdigest()


def validate_pin_format(pin: str) -> None:
    if not re.fullmatch(settings.PIN_FORMAT_REGEX, pin):
        raise ValidationError(
            "PIN does not match the accepted format (default: 4–8 digits)."
        )
