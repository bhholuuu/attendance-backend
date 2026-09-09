"""V2.7 backup storage providers.

The provider abstraction decouples "where backups live" from the rest of the
backup/restore logic. The API surface is deliberately small:

  * save(ref, data)      - store bytes at a provider reference.
  * load(ref)            - retrieve bytes for a reference.
  * delete(ref)          - remove bytes for a reference.
  * list()               - enumerate (ref, size_bytes) for retention cleanup.

Only the "local" provider ships by default. Adding S3/GCS/etc. is a single new
class registered in ``get_provider`` — no API, service or schema change.

The provider sees ONLY ciphertext (encryption happens before save). A leaked
provider never leaks plaintext.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings


class BackupProvider(ABC):
    """Minimal storage abstraction for backup blobs."""

    name: str = "abstract"

    @abstractmethod
    def save(self, ref: str, data: bytes) -> int:
        """Persist bytes; returns number of bytes stored."""

    @abstractmethod
    def load(self, ref: str) -> bytes:
        """Load bytes for a reference."""

    @abstractmethod
    def delete(self, ref: str) -> None:
        """Delete bytes for a reference (no-op if missing)."""

    @abstractmethod
    def list(self) -> list[tuple[str, int]]:
        """Return [(ref, size_bytes), ...] for all stored backups."""


class LocalProvider(BackupProvider):
    """Writes encrypted backup blobs into a local directory.

    The directory is created on demand. References are paths *relative* to the
    configured backup dir, so the same relative ref stays valid if the dir is
    remounted to a new location. Safe on Windows and POSIX via pathlib.
    """

    name = "local"

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.BACKUP_DIR).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, ref: str) -> Path:
        # Guard against path traversal from a malicious ref.
        safe = Path(ref).name
        if safe != ref.replace("\\", "/").split("/")[-1] or safe in ("", ".", ".."):
            safe = Path(ref).name
        return self.root / safe

    def save(self, ref: str, data: bytes) -> int:
        path = self._path_for(ref)
        path.write_bytes(data)
        return len(data)

    def load(self, ref: str) -> bytes:
        path = self._path_for(ref)
        return path.read_bytes()

    def delete(self, ref: str) -> None:
        path = self._path_for(ref)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def list(self) -> list[tuple[str, int]]:
        out = []
        for path in self.root.iterdir():
            if path.is_file():
                out.append((path.name, path.stat().st_size))
        return out


def get_provider(name: str | None = None) -> BackupProvider:
    """Return the provider instance for ``name`` (default from settings)."""
    provider_name = (name or settings.BACKUP_PROVIDER or "local").strip().lower()
    if provider_name in ("local", ""):
        return LocalProvider()
    # Pluggable: register additional providers here without touching callers.
    # e.g. if provider_name == "s3": return S3Provider()
    raise ValueError(f"Unsupported backup provider: {provider_name}")
