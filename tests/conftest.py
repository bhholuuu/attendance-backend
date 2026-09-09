import os

# Ensure tests run in the "testing" environment before the app is imported.
# This disables rate limiting and other production-only behavior so tests
# remain deterministic.
os.environ.setdefault("APP_ENV", "testing")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.jwt import create_access_token
from app.database.connection import Base, get_db
from app.main import app
from app.models.user import User, UserRole
from app.services import audit_service
from app.services import backup_service

# Middleware-driven audit writes (rate-limit hits) have no request-scoped DB
# session; the client fixture points the audit session factory at the CURRENT
# test DB's sessionmaker (a fresh session per call, sharing the engine). See
# record_security_event: the factory's session is always closed after writing,
# so it must never hand back the shared per-test session.
_CURRENT_TEST_DB: dict = {}


@pytest.fixture()
def db_session():
    """Create an isolated in-memory SQLite database per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    test_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = test_factory()
    _CURRENT_TEST_DB["factory"] = test_factory
    yield session
    session.close()
    _CURRENT_TEST_DB.clear()
    engine.dispose()


@pytest.fixture()
def client(db_session):
    """FastAPI TestClient wired to the isolated test database."""

    def override_get_db():
        yield db_session

    def session_factory():
        return _CURRENT_TEST_DB["factory"]()

    audit_service.set_session_factory(session_factory)
    # V2.7: background backup/restore jobs open their own sessions (mirroring
    # the audit service) so they write into the same isolated test DB.
    backup_service.set_session_factory(session_factory)
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    audit_service.reset_session_factory()
    backup_service.reset_session_factory()
    from app.api.auth import reset_failed_login_tracking

    reset_failed_login_tracking()


def _create_user(db, username, role, full_name=None):
    from app.core.security import hash_password

    user = User(
        full_name=full_name or username.title(),
        username=username,
        password_hash=hash_password("password123"),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def users(db_session):
    """Create an admin and a teacher user, returning both."""
    admin = _create_user(db_session, "admin", UserRole.ADMIN, "Admin User")
    teacher = _create_user(db_session, "teacher", UserRole.TEACHER, "Teacher Name")
    return {"admin": admin, "teacher": teacher}


def make_auth_header(user):
    token = create_access_token(subject=user.username, role=user.role.value)
    return {"Authorization": f"Bearer {token}"}


def grant_teacher_access(db, teacher, class_id, division_id=None):
    """Grant a teacher V2.1 access to a class (and optionally a specific division)."""
    from app.models.teacher_assignment import TeacherAssignment

    assignment = TeacherAssignment(
        teacher_id=teacher.id,
        class_id=class_id,
        division_id=division_id,
        created_by=teacher.id,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment
