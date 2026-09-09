import logging
import sys

from app.core.config import settings


def configure_logging() -> None:
    """Configure application-wide structured logging.

    Logs are emitted to stdout in a predictable format with a level derived
    from configuration. Levels are enforced globally so noisy debug output is
    suppressed in production unless explicitly enabled.
    """
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root = logging.getLogger()
    # Remove existing handlers to avoid duplicate output when the app is
    # reloaded in development.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root.setLevel(level)
    root.addHandler(handler)
    # Keep uvicorn/access logs from overriding our root configuration badly.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    logging.getLogger("app").setLevel(level)
