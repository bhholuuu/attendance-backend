"""Development bootstrap: ensure tables exist and an ADMIN account exists.

This module is DEVELOPMENT-ONLY. It is called automatically at application
startup when ``APP_ENV=development`` and can also be run manually with::

    python -m app.db_seed

Security notes
--------------
* The admin password is never stored in plain text: it is hashed with the
  same bcrypt ``hash_password`` used everywhere else in the application.
* The password literal lives only in this development bootstrap module. It is
  never written to Flutter source, configuration files, API responses or logs.
* The operation is idempotent: running it repeatedly never creates a second
  ``admin`` user or a duplicate account.
* It never changes an existing user's password. If an ``admin`` user already
  exists with the wrong role/inactive state, only the privilege/active flag is
  corrected and a warning is logged so an operator can inspect the account.
* Nothing runs in the ``testing`` environment. In ``production`` the initial
  admin account is created ONLY when the operator explicitly provides
  ``ATTENDANCE_ADMIN_BOOTSTRAP_PASSWORD`` (never the development default).
"""

import logging
import os

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.database.connection import SessionLocal, init_db
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

ADMIN_USERNAME = "admin"
ADMIN_FULL_NAME = "Administrator"
# Development-only default password for the very first admin account.
# Override locally without committing anything by exporting
# ATTENDANCE_ADMIN_BOOTSTRAP_PASSWORD in your shell if a different value is
# preferred. The value is never stored, returned or logged.
DEFAULT_DEV_ADMIN_PASSWORD = "Admin123."


def _bootstrap_password() -> str:
    return os.environ.get(
        "ATTENDANCE_ADMIN_BOOTSTRAP_PASSWORD", DEFAULT_DEV_ADMIN_PASSWORD
    )


def ensure_admin_account(
    db: Session, password: str | None = None
) -> bool:
    """Idempotently ensure an ADMIN user named ``admin`` exists.

    When [password] is provided (production first-run bootstrap) it is used
    instead of the development default. Returns True if the account was created
    or corrected, False if it already existed in a valid state. Never changes
    an existing password.
    """
    user = db.query(User).filter(User.username == ADMIN_USERNAME).first()

    if user is not None:
        changed = False
        if user.role != UserRole.ADMIN:
            user.role = UserRole.ADMIN
            changed = True
            logger.warning(
                "Existing user '%s' promoted to ADMIN role (bootstrap)",
                ADMIN_USERNAME,
            )
        if not user.is_active:
            user.is_active = True
            changed = True
            logger.warning(
                "Existing user '%s' re-activated (bootstrap)",
                ADMIN_USERNAME,
            )
        if changed:
            db.commit()
        # Never overwrite an existing password. If login fails for an existing
        # account, the operator must reset it through the proper mechanism.
        logger.debug(
            "Admin bootstrap: 'admin' account already exists (id=%s)",
            user.id,
        )
        return changed

    admin = User(
        full_name=ADMIN_FULL_NAME,
        username=ADMIN_USERNAME,
        password_hash=hash_password(password or _bootstrap_password()),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    logger.info(
        "Admin bootstrap: created initial ADMIN account '%s'",
        ADMIN_USERNAME,
    )
    return True


def bootstrap() -> None:
    """Create missing tables and ensure the development admin account."""
    init_db()
    db = SessionLocal()
    try:
        ensure_admin_account(db)
    finally:
        db.close()


def bootstrap_production_admin(password: str) -> None:
    """Idempotently create the initial ADMIN account on a production server.

    Runs only when the operator explicitly supplies a first-run password via
    the ``ATTENDANCE_ADMIN_BOOTSTRAP_PASSWORD`` environment variable. The
    development default is never used in production, and an existing admin's
    password is never changed.
    """
    if not (password or "").strip():
        return
    init_db()
    db = SessionLocal()
    try:
        ensure_admin_account(db, password=password)
    finally:
        db.close()


if __name__ == "__main__":
    bootstrap()
    print(f"Development admin account '{ADMIN_USERNAME}' is ready.")
    print("Log in with the password configured for development bootstrap.")