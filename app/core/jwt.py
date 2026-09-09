from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from jose import JWTError, jwt

from app.core.config import settings

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def create_access_token(subject: str, role: str, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token for the given user subject (username) and role.
    """
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta
        if expires_delta
        else timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode = {
        "sub": subject,
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "jti": str(uuid4()),
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def create_refresh_token(
    subject: str, role: str, expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create a JWT refresh token for the given user subject (username) and role.

    Refresh tokens carry the ``type`` claim so they can never be used as access
    tokens (and vice versa). They live longer than access tokens and are
    rotated / revoked server-side. A unique ``jti`` guarantees each issued
    token is distinct even within the same second.
    """
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta
        if expires_delta
        else timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    )
    to_encode = {
        "sub": subject,
        "role": role,
        "type": REFRESH_TOKEN_TYPE,
        "jti": str(uuid4()),
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT token.

    Returns the payload if the token is valid, otherwise None. The token type
    is validated by the caller via ``token_type_matches``.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except JWTError:
        return None


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT access token only.

    Refresh tokens are rejected here so they cannot be used to authorize
    requests.
    """
    payload = decode_token(token)
    if payload is None or payload.get("type") != ACCESS_TOKEN_TYPE:
        return None
    return payload


def decode_refresh_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT refresh token only.

    Access tokens are rejected here so they cannot be used to obtain a new
    token pair.
    """
    payload = decode_token(token)
    if payload is None or payload.get("type") != REFRESH_TOKEN_TYPE:
        return None
    return payload
