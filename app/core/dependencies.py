from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.jwt import decode_access_token
from app.core.request_context import set_actor
from app.database.connection import get_db
from app.models.user import User, UserRole
from app.services import audit_service

security = HTTPBearer(auto_error=False)

_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    Extract and validate the current authenticated user from the JWT token.
    Requires a valid Bearer token. Returns the active User or raises 401.
    V2.6: every rejection is audited as an AUTH_INVALID_TOKEN (security
    monitoring), and the resolved actor is published to the request context so
    later audit rows in the same request carry the actor automatically.
    """
    if credentials is None:
        audit_service.record(
            db,
            action="AUTH_INVALID_TOKEN",
            result="DENIED",
            entity_type="token",
            details={"reason": "missing_credentials"},
        )
        audit_service.commit_safely(db)
        raise _credentials_error

    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None or payload.get("sub") is None:
        audit_service.record(
            db,
            action="AUTH_INVALID_TOKEN",
            result="DENIED",
            entity_type="token",
            details={"reason": "invalid_or_expired_token"},
        )
        audit_service.commit_safely(db)
        raise _credentials_error

    username = payload.get("sub")
    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        audit_service.record(
            db,
            action="AUTH_INVALID_TOKEN",
            result="DENIED",
            entity_type="token",
            entity_label=f"user {username}",
            details={
                "reason": "account_missing_or_inactive",
                "username": username,
            },
        )
        audit_service.commit_safely(db)
        raise _credentials_error

    set_actor(user_id=user.id, username=user.username, role=user.role.value)
    return user


def require_admin(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency that ensures the current user is an active admin.
    Reusable for admin-only endpoints in future modules.
    V2.6: a non-admin user reaching an admin-only endpoint is audited as an
    ACCESS_DENIED security event.
    """
    if current_user.role != UserRole.ADMIN:
        audit_service.record(
            db,
            action="ACCESS_DENIED",
            result="DENIED",
            entity_type="user",
            entity_id=current_user.id,
            entity_label=f"user {current_user.username}",
            details={
                "username": current_user.username,
                "required_role": "ADMIN",
                "actual_role": current_user.role.value,
            },
            actor=current_user,
        )
        audit_service.commit_safely(db)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions",
        )
    return current_user