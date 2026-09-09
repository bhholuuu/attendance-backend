from logging.config import fileConfig
from pathlib import Path
import sys

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Make the backend package importable (env.py lives in backend/alembic).
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.database.connection import Base  # noqa: E402
from app.models import (  # noqa: E402,F401 -- registers tables on Base.metadata
    User,
    SchoolClass,
    Division,
    Student,
    AttendanceSession,
    StudentAttendance,
    MessageBatch,
    ParentMessage,
    RefreshToken,
)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Use the application's configured database URL (falls back to the ini value).
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _is_postgres(url: str) -> bool:
    """True when the migration target is PostgreSQL (backups are PG-native)."""
    return (url or "").startswith("postgres")


def _maybe_backup_before_migration() -> None:
    """V2.7: take a PRE_MIGRATION backup before Alembic applies changes.

    Only runs for real PostgreSQL databases (not SQLite used in tests) and
    only when the backup service is enabled. The backup is synchronous and
    blocking so a migration never proceeds without a recovery point. Any
    failure here aborts the migration (fail-safe rather than migrate blind).
    """
    if not _is_postgres(config.get_main_option("sqlalchemy.url")):
        return
    if not settings.BACKUP_ENABLED:
        return
    from app.services.backup_service import backup_before_migration

    backup_before_migration()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # V2.7: snapshot the DB before Alembic mutates it (PG only, no-op in tests).
    _maybe_backup_before_migration()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
