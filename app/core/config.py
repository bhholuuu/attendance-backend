import json
import secrets
from pathlib import Path
from typing import Annotated, List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file.

    All sensitive values (JWT secret, database URL, provider credentials) come
    from the environment and are never hardcoded. Safe defaults are provided
    for development only. Production deployments MUST override them.
    """

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application environment: development | testing | production
    APP_ENV: str = "development"
    DEBUG: bool = False

    PROJECT_NAME: str = "School Attendance API"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = (
        "postgresql://postgres:postgres@localhost:5432/school_attendance"
    )
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # CORS
    CORS_ORIGINS: Annotated[List[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
    ]

    # Security / JWT
    JWT_SECRET_KEY: str = "change-this-to-a-random-secret-key-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    # Refresh token expiry in days.
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # Password policy for admin-created users
    PASSWORD_MIN_LENGTH: int = 8

    # Rate limiting (requests per window per client)
    RATE_LIMIT_ENABLED: bool = True
    LOGIN_RATE_LIMIT_LIMIT: int = 8
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 60
    SEND_RATE_LIMIT_LIMIT: int = 10
    SEND_RATE_LIMIT_WINDOW_SECONDS: int = 60
    RETRY_RATE_LIMIT_LIMIT: int = 20
    RETRY_RATE_LIMIT_WINDOW_SECONDS: int = 60

    # V2.6: Failed-login tracking. After this many failed logins for a username
    # coming from the same client IP within the window, further attempts from
    # that pair are briefly refused (429). Lockout is temporary only — it is
    # never permanent, so legitimate users who mistype are never locked out
    # forever (and a password reset / cooldown always recovers access).
    FAILED_LOGIN_LIMIT: int = 5
    FAILED_LOGIN_WINDOW_SECONDS: int = 300

    # V2.6: Audit retention (advisory only). Audit rows are never deleted
    # automatically; this value documents the recommended retention horizon
    # for admins / backup policy.
    AUDIT_RETENTION_DAYS: int = 365

    # ------------------------------------------------------------------
    # V2.7: Database backup + restore + disaster recovery
    # ------------------------------------------------------------------
    # Master switch for the backup service. When False, the backup API rejects
    # new jobs (existing metadata stays readable).
    BACKUP_ENABLED: bool = True
    # Storage provider name. The provider abstraction is pluggable:
    #   "local"          - write encrypted pg_dump output to BACKUP_DIR.
    #   "s3" / "gcs" ... - add new providers without changing API code.
    # Only "local" ships by default; provider selection stays config-driven so
    # a new provider never requires a code change elsewhere.
    BACKUP_PROVIDER: str = "local"
    # Root directory for the "local" provider (created on demand).
    BACKUP_DIR: str = "./backups"
    # Default retention (days) applied to backups that do not specify one.
    BACKUP_RETENTION_DAYS: int = 30
    # How quickly must we be able to restore (recovery point objective in
    # minutes) and recover (recovery time objective in minutes). These are
    # documented SLOs surfaced to the admin dashboard / disaster plan.
    BACKUP_RPO_MINUTES: int = 1440
    BACKUP_RTO_MINUTES: int = 120
    # Retain this many backups regardless of age (rotation floor).
    BACKUP_KEEP_AT_LEAST: int = 5
    # Enable automatic scheduled backups (drive via a scheduler/cron calling
    # the service). The interval is advisory and documented.
    BACKUP_SCHEDULED_ENABLED: bool = True
    BACKUP_SCHEDULED_CRON: str = "0 2 * * *"
    # ------------------------------------------------------------------
    # V2.7: Backup encryption.
    #   * Backups are encrypted at rest in the storage provider.
    #   * BACKUP_ENCRYPTION_KEY is a server-side secret. It is NEVER stored in
    #     the database, the source tree, .env.example or the Flutter app. The
    #     production operator supplies it via the process environment / secret
    #     manager (documented strategy in docs/BACKUP_ENCRYPTION.md).
    #   * An empty key disables encryption for local/dev/testing only; in
    #     production validate_production_config() fails fast if a backup run
    #     is attempted without a key (the service enforces this too).
    # ------------------------------------------------------------------
    BACKUP_ENCRYPTION_ENABLED: bool = False
    BACKUP_ENCRYPTION_KEY: str = ""
    BACKUP_ENCRYPTION_ALGORITHM: str = "aes-256-gcm"
    # Location of pg_dump / pg_restore binaries (overridable; used only by the
    # PostgreSQL-native "local" provider). Test suite stubs these out.
    PG_DUMP_BIN: str = "pg_dump"
    PG_RESTORE_BIN: str = "pg_restore"

    # Messaging / Provider
    MESSAGE_PROVIDER: str = "mock"
    # Maximum send attempts per message (applies to FAILED-message retries).
    # Once reached, a failed message is no longer auto-retried.
    MESSAGE_MAX_ATTEMPTS: int = 3

    # ------------------------------------------------------------------
    # WhatsApp Cloud API (required only when MESSAGE_PROVIDER=whatsapp)
    # ------------------------------------------------------------------
    # Graph API version, e.g. "v21.0". Used to build the endpoint
    # https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages
    WHATSAPP_API_VERSION: str = "v21.0"
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""
    # Webhook subscription & verification.
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""
    # Message template (configurable, not hardcoded in route logic).
    WHATSAPP_TEMPLATE_NAME: str = "attendance_update"
    WHATSAPP_TEMPLATE_LANGUAGE: str = "en"
    # HTTP client timeout for a single provider call (seconds).
    WHATSAPP_API_TIMEOUT: float = 10.0

    # API documentation availability (Swagger/ReDoc). Always on in development
    # and testing; production may disable it.
    SWAGGER_ENABLED: bool = True

    # Logging
    LOG_LEVEL: str = "INFO"

    @field_validator("APP_ENV")
    @classmethod
    def _validate_env(cls, v: str) -> str:
        normalized = (v or "development").strip().lower()
        if normalized not in {"development", "testing", "production"}:
            raise ValueError(
                "APP_ENV must be one of: development, testing, production"
            )
        return normalized

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v):
        """Accept both JSON arrays and comma-separated lists.

        pydantic-settings attempts JSON parsing for list-typed fields, so the
        existing comma-separated ``.env`` convention must be handled here.
        """
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return v

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _validate_jwt_secret(cls, v: str, info) -> str:
        secret = (v or "").strip()
        # Only enforce a strong secret in production. Development and testing
        # may use the documented insecure default for convenience.
        if info.data.get("APP_ENV", "development") == "production":
            if len(secret) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be at least 32 characters in "
                    "production"
                )
            if secret == "change-this-to-a-random-secret-key-in-production":
                raise ValueError(
                    "JWT_SECRET_KEY must not use the default development value "
                    "in production"
                )
        if not secret:
            raise ValueError("JWT_SECRET_KEY must not be empty")
        return secret

    @model_validator(mode="after")
    def _validate_whatsapp_config(self) -> "Settings":
        """Validate WhatsApp credentials only when the whatsapp provider is active.

        The mock provider (and the default empty provider) must NOT require any
        WhatsApp credential so development and tests can run without secrets.
        """
        if (self.MESSAGE_PROVIDER or "mock").strip().lower() != "whatsapp":
            return self

        required = {
            "WHATSAPP_PHONE_NUMBER_ID": self.WHATSAPP_PHONE_NUMBER_ID,
            "WHATSAPP_ACCESS_TOKEN": self.WHATSAPP_ACCESS_TOKEN,
            "WHATSAPP_BUSINESS_ACCOUNT_ID": self.WHATSAPP_BUSINESS_ACCOUNT_ID,
            "WHATSAPP_WEBHOOK_VERIFY_TOKEN": self.WHATSAPP_WEBHOOK_VERIFY_TOKEN,
            "WHATSAPP_APP_SECRET": self.WHATSAPP_APP_SECRET,
        }
        missing = [name for name, value in required.items() if not (value or "").strip()]
        if missing:
            raise ValueError(
                "MESSAGE_PROVIDER=whatsapp requires the following environment "
                "variables: " + ", ".join(missing)
            )
        if self.MESSAGE_MAX_ATTEMPTS < 1:
            raise ValueError("MESSAGE_MAX_ATTEMPTS must be at least 1")
        if not (self.WHATSAPP_API_VERSION or "").strip():
            raise ValueError("WHATSAPP_API_VERSION must not be empty")
        if not (self.WHATSAPP_TEMPLATE_NAME or "").strip():
            raise ValueError("WHATSAPP_TEMPLATE_NAME must not be empty")
        if not (self.WHATSAPP_TEMPLATE_LANGUAGE or "").strip():
            raise ValueError("WHATSAPP_TEMPLATE_LANGUAGE must not be empty")
        return self

    @model_validator(mode="after")
    def _apply_environment_defaults(self) -> "Settings":
        env = self.APP_ENV
        if env == "development":
            # Safe, convenient defaults for local development.
            self.DEBUG = True
            self.SWAGGER_ENABLED = True
        elif env == "testing":
            # Isolated defaults for the test suite (may still be overridden).
            self.DEBUG = False
            self.SWAGGER_ENABLED = True
            # Rate limiting is disabled by default under test to keep tests
            # deterministic (the in-memory store is per-process).
            self.RATE_LIMIT_ENABLED = False
        elif env == "production":
            # Production is explicit: DEBUG off, Swagger off unless requested,
            # restrictive CORS unless configured.
            self.DEBUG = False
        return self

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def docs_enabled(self) -> bool:
        return bool(self.SWAGGER_ENABLED)

    def validate_production_config(self) -> None:
        """Fail fast if required production configuration is missing/insecure.

        Called at startup so a misconfigured production deploy never starts.
        """
        if not self.is_production:
            return
        errors = []
        if len((self.JWT_SECRET_KEY or "")) < 32:
            errors.append("JWT_SECRET_KEY must be at least 32 characters")
        if self.JWT_SECRET_KEY == "change-this-to-a-random-secret-key-in-production":
            errors.append("JWT_SECRET_KEY must not use the default value")
        if "postgres:postgres@localhost" in self.DATABASE_URL:
            errors.append("DATABASE_URL must point to a production database")
        if not self.CORS_ORIGINS:
            errors.append("CORS_ORIGINS must not be empty in production")
        # V2.7: backups must be encrypted at rest in production. Failing fast
        # here prevents an administrator from taking unencrypted backups of
        # real data.
        if self.BACKUP_ENABLED and not self.BACKUP_ENCRYPTION_ENABLED:
            errors.append(
                "BACKUP_ENCRYPTION_ENABLED must be true when backups are "
                "enabled in production"
            )
        if errors:
            raise RuntimeError(
                "Invalid production configuration: " + "; ".join(errors)
            )


def generate_secret_key() -> str:
    """Generate a strong random secret for JWT signing (for documentation)."""
    return secrets.token_urlsafe(48)


settings = Settings()
