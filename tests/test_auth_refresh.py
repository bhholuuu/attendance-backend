from datetime import datetime, timedelta, timezone

import pytest

from app.core.jwt import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from app.core.security import PasswordPolicyError, validate_password_strength
from app.models.refresh_token import RefreshToken, hash_token


# ----------------------------- JWT type isolation -----------------------------

def test_access_token_rejected_as_refresh():
    token = create_access_token(subject="admin", role="ADMIN")
    assert decode_refresh_token(token) is None
    assert decode_access_token(token) is not None


def test_refresh_token_rejected_as_access():
    token = create_refresh_token(subject="admin", role="ADMIN")
    assert decode_access_token(token) is None
    assert decode_refresh_token(token) is not None


# ----------------------------- password policy -----------------------------

@pytest.mark.parametrize(
    "password",
    [
        "Short1!",  # too short (min 8)
        "alllowercase1",  # no uppercase
        "ALLUPPERCASE1",  # no lowercase
        "NoDigitsHere!",  # no digit
    ],
)
def test_password_policy_rejects_weak(password):
    with pytest.raises(PasswordPolicyError):
        validate_password_strength(password)


def test_password_policy_accepts_strong():
    # Does not raise.
    validate_password_strength("StrongPass1")


def test_hash_token_consistent_and_not_plaintext():
    h = hash_token("sometoken")
    assert h == hash_token("sometoken")
    assert "sometoken" not in h


# ----------------------------- refresh token flow -----------------------------

def test_refresh_token_model_hash_storage(client, db_session, users):
    raw = create_refresh_token(subject="admin", role="ADMIN")
    stored = RefreshToken(
        token_hash=hash_token(raw),
        user_id=users["admin"].id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.add(stored)
    db_session.commit()
    db_session.refresh(stored)
    assert stored.token_hash == hash_token(raw)
    assert stored.is_expired is False


def test_login_returns_refresh_token(client, users):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "password123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert decode_access_token(body["access_token"]) is not None


def test_refresh_rotates_token(client, users):
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "password123"},
    ).json()
    old_refresh = login["refresh_token"]

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200
    new = resp.json()
    assert new["access_token"] != login["access_token"]
    assert new["refresh_token"] != old_refresh

    # The old (presented) refresh token must be revoked -> reuse is rejected.
    retry = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert retry.status_code == 401


def test_refresh_rejects_access_token(client, users):
    access = create_access_token(subject="admin", role="ADMIN")
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert resp.status_code == 401


def test_refresh_rejects_garbage(client, users):
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert resp.status_code == 401


def test_logout_revokes_refresh_token(client, users):
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "password123"},
    ).json()
    refresh = login["refresh_token"]

    resp = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert resp.status_code == 204

    # Refresh token now rejected.
    retry = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert retry.status_code == 401
