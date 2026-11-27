"""The link state of this machine, and the store that holds it.

The state holds the link credential itself, because the program presents
it on each call. So the store must protect it: Windows keeps it with DPAPI at
machine scope (``windows.stores.DpapiLinkStore``). On a dev box a JSON file
with mode 600 holds it (DEV ONLY). The credential never reaches a log, a
print or an answer of the setup page.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class LinkState:
    credential: str
    connector_id: str
    company_name: str
    #: Rule j: the program serves no call before the customer confirms the
    #: company name that the approval returned.
    confirmed: bool = False

    def __repr__(self) -> str:  # the credential never reaches a log
        return (
            f"LinkState(connector_id={self.connector_id!r}, "
            f"company_name={self.company_name!r}, confirmed={self.confirmed})"
        )

    def confirm(self) -> LinkState:
        return replace(self, confirmed=True)


def state_to_bytes(state: LinkState) -> bytes:
    return json.dumps(asdict(state)).encode("utf-8")


def state_from_bytes(raw: bytes) -> LinkState | None:
    """The state, or None when the bytes are not a whole state."""
    try:
        data = json.loads(raw.decode("utf-8"))
        state = LinkState(
            credential=str(data["credential"]),
            connector_id=str(data["connector_id"]),
            company_name=str(data["company_name"]),
            confirmed=data.get("confirmed") is True,
        )
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None
    return state if state.credential and state.connector_id else None


class LinkStore(Protocol):
    def load(self) -> LinkState | None: ...

    def save(self, state: LinkState) -> None: ...

    def delete(self) -> None: ...


class MemoryLinkStore:
    """For tests."""

    def __init__(self, state: LinkState | None = None) -> None:
        self.state = state

    def load(self) -> LinkState | None:
        return self.state

    def save(self, state: LinkState) -> None:
        self.state = state

    def delete(self) -> None:
        self.state = None


class Blob(Protocol):
    """Bytes kept safe on disk. Windows: ``windows.stores.DpapiBlobFile``."""

    def read(self) -> bytes | None: ...

    def write(self, data: bytes) -> None: ...

    def delete(self) -> None: ...


class BlobLinkStore:
    """The link state in a protected blob. A blob that cannot be read or
    unprotected reads as no link, so the program serves nothing."""

    def __init__(self, blob: Blob) -> None:
        self._blob = blob

    def load(self) -> LinkState | None:
        raw = self._blob.read()
        return state_from_bytes(raw) if raw else None

    def save(self, state: LinkState) -> None:
        self._blob.write(state_to_bytes(state))

    def delete(self) -> None:
        self._blob.delete()


class DevFileLinkStore:
    """DEV ONLY: the state in a JSON file with mode 600. The product uses the
    DPAPI store on Windows."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def load(self) -> LinkState | None:
        try:
            return state_from_bytes(self._path.read_bytes())
        except FileNotFoundError:
            return None

    def save(self, state: LinkState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(state_to_bytes(state))
        os.replace(tmp, self._path)

    def delete(self) -> None:
        self._path.unlink(missing_ok=True)
