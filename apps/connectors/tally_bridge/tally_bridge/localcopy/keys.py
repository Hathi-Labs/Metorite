"""The key of the local copy.

``KeyProvider`` is the seam. A customer's machine keeps the key with Windows
DPAPI at machine scope (build step 4), behind the same interface.
``DevFileKeyProvider`` is DEV ONLY: a key file that only its owner reads.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet


class KeyProvider(Protocol):
    def key(self) -> bytes:
        """The Fernet key of the copy (32 random bytes, URL-safe base64)."""
        ...


class MemoryKeyProvider:
    """A key held in memory. For tests."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or Fernet.generate_key()

    def key(self) -> bytes:
        return self._key


class DevFileKeyProvider:
    """DEV ONLY. The key in one file, made on first use with mode 600."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def key(self) -> bytes:
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(Fernet.generate_key())
        return self._path.read_bytes().strip()
