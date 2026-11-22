"""The tokens: make one, hash it, check it, and the store that holds the hash.

Security rule 2 of the brief (section 6.5): a token comes from 32 or more
random bytes. The bridge stores only its SHA-256 hash, and it compares in
constant time.

``SecretStore`` is the seam. Rung 1 on Linux reads the environment
(``EnvSecretStore``). Step 4 adds Windows DPAPI behind the same interface.
The module is not named ``secrets``, because that would hide the standard
library module of the same name.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def new_token() -> str:
    """A new token: 32 random bytes, URL-safe base64, 43 characters."""
    return secrets.token_urlsafe(32)


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(presented: str, stored_sha256: str | None) -> bool:
    """True when the presented token hashes to the stored hash.

    The comparison of the two digests runs in constant time. With no stored
    hash, nothing matches.
    """
    if not stored_sha256 or not presented:
        return False
    return hmac.compare_digest(token_sha256(presented), stored_sha256)


class SecretStore(Protocol):
    """The hashes of the read token and of the setup secret.

    The store never holds a token or a secret in plain text: only its SHA-256.
    Rung 1 on Linux uses the environment or the dev file store. Step 4 adds
    Windows DPAPI behind the same interface.
    """

    def read_token_sha256(self) -> str | None:
        """The SHA-256 hex digest of the read token, or None."""
        ...

    def set_read_token_sha256(self, digest: str) -> None: ...

    def setup_secret_sha256(self) -> str | None:
        """The SHA-256 hex digest of the per-install setup secret, or None."""
        ...

    def set_setup_secret_sha256(self, digest: str) -> None: ...


class ReadOnlySecretStore(Exception):
    pass


class MemorySecretStore:
    """Holds the hashes in memory. For tests."""

    def __init__(
        self, read_token_sha256: str | None, setup_secret_sha256: str | None = None
    ) -> None:
        self._read = _checked(read_token_sha256)
        self._setup = _checked(setup_secret_sha256)

    def read_token_sha256(self) -> str | None:
        return self._read

    def set_read_token_sha256(self, digest: str) -> None:
        self._read = _checked(digest)

    def setup_secret_sha256(self) -> str | None:
        return self._setup

    def set_setup_secret_sha256(self, digest: str) -> None:
        self._setup = _checked(digest)


class EnvSecretStore:
    """Reads the hashes from the environment. For a dev box. It cannot write."""

    NAME = "TALLY_BRIDGE_READ_TOKEN_SHA256"
    SETUP_NAME = "TALLY_BRIDGE_SETUP_SECRET_SHA256"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env

    def read_token_sha256(self) -> str | None:
        return _checked(self._env.get(self.NAME) or None)

    def set_read_token_sha256(self, digest: str) -> None:
        raise ReadOnlySecretStore("The environment store is read only.")

    def setup_secret_sha256(self) -> str | None:
        return _checked(self._env.get(self.SETUP_NAME) or None)

    def set_setup_secret_sha256(self, digest: str) -> None:
        raise ReadOnlySecretStore("The environment store is read only.")


class DevFileSecretStore:
    """DEV ONLY. The two hashes in one JSON file, readable by its owner only.

    It holds hashes, never a token or a secret. It is not the product store:
    a customer's machine uses Windows DPAPI at machine scope (brief section
    6.3, build step 4). The bridge logs a warning when it runs with this store.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def _data(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def _write(self, key: str, digest: str) -> None:
        data = self._data()
        data[key] = _checked(digest) or ""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.unlink(missing_ok=True)  # a stale file would keep its old mode
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self._path)

    def read_token_sha256(self) -> str | None:
        return _checked(self._data().get("read_token_sha256") or None)

    def set_read_token_sha256(self, digest: str) -> None:
        self._write("read_token_sha256", digest)

    def setup_secret_sha256(self) -> str | None:
        return _checked(self._data().get("setup_secret_sha256") or None)

    def set_setup_secret_sha256(self, digest: str) -> None:
        self._write("setup_secret_sha256", digest)


def _checked(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if not _HEX64.match(value):
        raise ValueError("A stored token hash must be 64 hex characters.")
    return value
