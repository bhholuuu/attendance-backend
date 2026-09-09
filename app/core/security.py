import re

import bcrypt

from app.core.config import settings


class PasswordPolicyError(ValueError):
    """Raised when a password does not satisfy the configured policy."""


def validate_password_strength(password: str) -> None:
    """
    Enforce a minimum-strength password policy for users whose passwords are
    chosen outside the (external) IdP flow, e.g. admin-created users.

    Raises PasswordPolicyError describing the first violated rule so it can be
    returned to the caller as a user-friendly message.
    """
    min_length = settings.PASSWORD_MIN_LENGTH
    if password is None or len(password) < min_length:
        raise PasswordPolicyError(
            f"Password must be at least {min_length} characters long."
        )
    if not re.search(r"[A-Z]", password):
        raise PasswordPolicyError(
            "Password must contain at least one uppercase letter."
        )
    if not re.search(r"[a-z]", password):
        raise PasswordPolicyError(
            "Password must contain at least one lowercase letter."
        )
    if not re.search(r"\d", password):
        raise PasswordPolicyError(
            "Password must contain at least one digit."
        )


def hash_password(password: str) -> str:
    """
    Hash a plain-text password using bcrypt.

    Salt is generated automatically and embedded in the resulting hash.
    """
    password_bytes = password.encode("utf-8")
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against a bcrypt hash.
    Returns True if the password matches the hash, False otherwise.
    """
    try:
        password_bytes = plain_password.encode("utf-8")
        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except (ValueError, TypeError):
        return False
