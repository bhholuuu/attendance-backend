from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Create all tables defined in the models if they do not already exist.
    Import the models module first so tables are registered on Base.metadata.
    """
    from app.models import user as user_models  # noqa: F401
    from app.models import school_class as class_models  # noqa: F401
    from app.models import student as student_models  # noqa: F401
    from app.models import attendance as attendance_models  # noqa: F401
    from app.models import message as message_models  # noqa: F401
    from app.models import refresh_token as refresh_token_models  # noqa: F401
    from app.models import student_import as student_import_models  # noqa: F401
    from app.models import teacher_assignment as assignment_models  # noqa: F401
    from app.models import academic_year as academic_year_models  # noqa: F401
    from app.models import calendar_event as calendar_event_models  # noqa: F401
    from app.models import student_leave as student_leave_models  # noqa: F401
    from app.models import admin_settings as admin_settings_models  # noqa: F401
    from app.models import audit_log as audit_log_models  # noqa: F401
    from app.models import backup as backup_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
