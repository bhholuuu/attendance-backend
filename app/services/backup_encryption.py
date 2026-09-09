"""V2.7 backup encryption at rest.

Backup bytes are encrypted with AES-256-GCM before they are handed to the
storage provider. Encryption is transparent to the provider: the provider only
sees ciphertext, so a leaked backup file never leaks plaintext.

Key management (documented in docs/BACKUP_ENCRYPTION.md):
  * The raw key material comes ONLY from the process environment /
    secret manager via ``BACKUP_ENCRYPTION_KEY`` — never from the database,
    the source tree, the Flutter app or ``.env.example``.
  * We do not store the key anywhere. To recover a backup you must supply the
    same key (KDF salt is stored alongside the ciphertext, not secret).
  * Utility functions here take the key as a parameter so the caller (the
    backup service) decides how to obtain it; an in-memory cache lives for the
    life of a single job only.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

# Header magic so a consumer can quickly detect "that's an encrypted backup"
# and route it to the right decryptor. Plain (unencrypted) dumps carry no magic.
_MAGIC = b"SAA-BACKUP-v1\x00"

_NONCE_LEN = 12
_SALT_LEN = 16
_TAG_LEN = 16

# Rejection sentinel used by the service to avoid accidentally treating a key
# as valid when it is still the empty default.
EMPTY_KEY = ""


class MissingEncryptionKey(Exception):
    """Raised when an operation requires a key but none is configured."""


def _derive_key(master_key: str, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from the master key using HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"school-attendance-backup",
        backend=default_backend(),
    )
    return hkdf.derive(master_key.encode("utf-8"))


def is_encrypted(blob: bytes) -> bool:
    """True if the blob begins with the encrypted-backup magic header."""
    return blob.startswith(_MAGIC)


def encrypt(plaintext: bytes, key: str) -> bytes:
    """Encrypt plaintext with AES-256-GCM, returning a self-contained blob.

    Output layout: magic (11) + salt (16) + nonce (12) + ciphertext|tag.
    The salt is random per encryption and stored with the blob (not secret).
    """
    if not key:
        raise MissingEncryptionKey(
            "BACKUP_ENCRYPTION_KEY is not configured; cannot encrypt backup"
        )
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    derived = _derive_key(key, salt)
    aesgcm = AESGCM(derived)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return _MAGIC + salt + nonce + ciphertext


def decrypt(blob: bytes, key: str) -> bytes:
    """Decrypt a blob produced by :func:`encrypt`.

    Raises ValueError on tampering / wrong key / malformed input, and
    MissingEncryptionKey if no key is provided.
    """
    if not key:
        raise MissingEncryptionKey(
            "BACKUP_ENCRYPTION_KEY is not configured; cannot decrypt backup"
        )
    if not is_encrypted(blob):
        # Not encrypted at all — return as-is only if explicitly allowed by the
        # caller. Here we refuse by default because decrypting plaintext and
        # claiming it is encrypted would hide configuration errors.
        raise ValueError("Blob is not an encrypted backup")
    header_len = len(_MAGIC)
    if len(blob) < header_len + _SALT_LEN + _NONCE_LEN + _TAG_LEN:
        raise ValueError("Encrypted blob is truncated")
    salt = blob[header_len : header_len + _SALT_LEN]
    nonce = blob[header_len + _SALT_LEN : header_len + _SALT_LEN + _NONCE_LEN]
    ciphertext = blob[header_len + _SALT_LEN + _NONCE_LEN :]
    derived = _derive_key(key, salt)
    aesgcm = AESGCM(derived)
    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:  # noqa: BLE001 - cryptography raises InvalidTag
        raise ValueError("Failed to decrypt backup (wrong key or corrupted") from exc


def verify_mac_probe(blob: bytes, key: str) -> bool:
    """Cheap integrity probe: does this blob decrypt under the key?

    Used during verification to mark VERIFIED / CORRUPTED. Returns False for
    plaintext blobs (nothing to verify cryptographically).
    """
    if not is_encrypted(blob):
        return False
    try:
        decrypt(blob, key)
        return True
    except (ValueError, MissingEncryptionKey):
        return False
