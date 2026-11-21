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
import os
import re
import secrets
from collections.abc import Mapping
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
    def read_token_sha256(self) -> str | None:
        """The SHA-256 hex digest of the read token, or None."""
        ...


class MemorySecretStore:
    """Holds the hash in memory. For tests."""

    def __init__(self, read_token_sha256: str | None) -> None:
        self._read = _checked(read_token_sha256)

    def read_token_sha256(self) -> str | None:
        return self._read


class EnvSecretStore:
    """Reads ``TALLY_BRIDGE_READ_TOKEN_SHA256`` from the environment. For a dev box."""

    NAME = "TALLY_BRIDGE_READ_TOKEN_SHA256"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env

    def read_token_sha256(self) -> str | None:
        return _checked(self._env.get(self.NAME) or None)


def _checked(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if not _HEX64.match(value):
        raise ValueError("A stored token hash must be 64 hex characters.")
    return value
