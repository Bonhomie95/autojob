"""
Symmetric encryption for per-user secrets (SMTP passwords, API keys).

Secrets are encrypted with Fernet (AES-128-CBC + HMAC) before they touch the
database and decrypted only in memory, only when the pipeline needs them. The
key comes from ``CREDENTIAL_ENCRYPTION_KEY``; production requires it explicitly
(see ``ProductionConfig``). In development, if it is unset, we derive a stable
key from ``SECRET_KEY`` so the app runs — but log a warning, because rotating
``SECRET_KEY`` would then make stored secrets unreadable.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

logger = logging.getLogger(__name__)


def _derive_key_from_secret(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet:
    key = current_app.config.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if key:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)

    if current_app.config.get("APP_ENV") == "production":
        # Should never reach here — ProductionConfig requires the key — but be
        # defensive rather than silently encrypt with a guessable key.
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is required in production")

    logger.warning(
        "CREDENTIAL_ENCRYPTION_KEY unset — deriving a dev key from SECRET_KEY. "
        "Do NOT use this in production; rotating SECRET_KEY will orphan secrets."
    )
    return Fernet(_derive_key_from_secret(current_app.config["SECRET_KEY"]))


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string, returning a URL-safe token string."""
    if plaintext is None:
        plaintext = ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt`. Returns '' on failure."""
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.error("Failed to decrypt a stored credential (key mismatch?)")
        return ""
