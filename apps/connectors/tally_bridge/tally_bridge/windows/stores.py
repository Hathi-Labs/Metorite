"""The DPAPI stores: the two hashes, and the key of the local copy.

``DpapiSecretStore`` implements ``secret_store.SecretStore``. It keeps the
read token hash and the setup secret hash in one JSON object, protected by
the ``Protector``, in one file. ``DpapiKeyProvider`` implements
``localcopy.KeyProvider``: the Fernet key of the local copy, protected the
same way, made on first use.

A file that does not unprotect (a new machine, a damaged file) reads as
empty. The bridge then asks for a new setup link, and the local copy makes a
new key and a new copy. Writes are atomic: a new file, then a replace.

``data_dir`` is ``%ProgramData%\\TallyBridge``. The installer makes it, and
only the service account and the administrators may write it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet

from tally_bridge.secret_store import _checked
from tally_bridge.windows.dpapi import ProtectError, Protector

APP_DIR = "TallyBridge"
SECRETS_FILE = "secrets.bin"
COPY_KEY_FILE = "copy.key"


def data_dir(env: dict[str, str] | None = None) -> Path:
    """``%ProgramData%\\TallyBridge``. It needs ``PROGRAMDATA``."""
    base = (env if env is not None else os.environ).get("PROGRAMDATA", "")
    if not base:
        raise RuntimeError("PROGRAMDATA is not set. This path is for Windows.")
    return Path(base) / APP_DIR


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


class DpapiSecretStore:
    """The product store of the two hashes (brief section 6.3)."""

    def __init__(self, path: str | os.PathLike[str], protector: Protector) -> None:
        self._path = Path(path)
        self._protector = protector

    def _data(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._protector.unprotect(self._path.read_bytes()))
        except (ProtectError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, key: str, digest: str) -> None:
        data = self._data()
        data[key] = _checked(digest) or ""
        _write_atomic(self._path, self._protector.protect(json.dumps(data).encode()))

    def read_token_sha256(self) -> str | None:
        return _checked(self._data().get("read_token_sha256") or None)

    def set_read_token_sha256(self, digest: str) -> None:
        self._write("read_token_sha256", digest)

    def setup_secret_sha256(self) -> str | None:
        return _checked(self._data().get("setup_secret_sha256") or None)

    def set_setup_secret_sha256(self, digest: str) -> None:
        self._write("setup_secret_sha256", digest)

    def delete(self) -> None:
        """Uninstall: the stored hashes go."""
        self._path.unlink(missing_ok=True)


class DpapiKeyProvider:
    """The key of the local copy, protected by DPAPI at machine scope."""

    def __init__(self, path: str | os.PathLike[str], protector: Protector) -> None:
        self._path = Path(path)
        self._protector = protector

    def key(self) -> bytes:
        if self._path.exists():
            try:
                return self._protector.unprotect(self._path.read_bytes()).strip()
            except ProtectError:
                pass  # a new machine: a new key, and the next sync makes a new copy
        key = Fernet.generate_key()
        _write_atomic(self._path, self._protector.protect(key))
        return key


#: TLY-2: the link state (the link credential, the connector id and the
#: company name), as one DPAPI blob.
LINK_FILE = "link.bin"


class DpapiBlobFile:
    """Bytes kept with DPAPI at machine scope, written atomically. A blob
    that cannot be unprotected reads as None, so the program serves nothing
    (it fails closed)."""

    def __init__(self, path: str | os.PathLike[str], protector: Protector) -> None:
        self._path = Path(path)
        self._protector = protector

    def read(self) -> bytes | None:
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            return self._protector.unprotect(raw)
        except ProtectError:
            return None

    def write(self, data: bytes) -> None:
        _write_atomic(self._path, self._protector.protect(data))

    def delete(self) -> None:
        self._path.unlink(missing_ok=True)
