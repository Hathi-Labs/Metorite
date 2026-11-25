"""The encrypted store of the local copy: one file for each company.

Each file holds one ``Snapshot`` as JSON, encrypted with Fernet (AES-128-CBC
and HMAC-SHA256) under the key of the ``KeyProvider``. A file that does not
decrypt (a new key, a damaged file) reads as no copy, and the next sync
writes a new one. The file name is a hash of the company name, so the folder
does not show which companies the machine holds. Writes are atomic (a new file,
then a replace) with mode 600.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from tally_bridge.localcopy.keys import KeyProvider
from tally_bridge.reader.records import Entry, Ledger, StockEntry, Voucher
from tally_bridge.reader.values import Amount

log = logging.getLogger("tally_bridge.localcopy")

SUFFIX = ".copy"
#: The temporary file of ``save``: the 32-hex name of ``_path``, then ``.tmp``.
_OWN_TMP = re.compile(r"[0-9a-f]{32}\.tmp")


@dataclass
class Snapshot:
    """The copy of one company at one time."""

    company: str
    copied_at: datetime
    max_alter_id: int = 0
    ledgers: list[Ledger] = field(default_factory=list)
    vouchers: dict[str, Voucher] = field(default_factory=dict)

    def age_minutes(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        return max(0, int((now - self.copied_at).total_seconds() // 60))


# ── The JSON shape ─────────────────────────────────────────────────────────


def _amount(a: Amount) -> list[Any]:
    return [str(a.value), a.side]


def _amount_of(v: list[Any]) -> Amount:
    return Amount(Decimal(v[0]), v[1])


def _day(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _voucher_out(v: Voucher) -> dict[str, Any]:
    return {
        "guid": v.guid,
        "on": _day(v.on),
        "type": v.voucher_type,
        "number": v.number,
        "party": v.party,
        "narration": v.narration,
        "alter_id": v.alter_id,
        "entries": [[e.ledger, _amount(e.amount)] for e in v.entries],
        "stock": [[s.item, s.quantity, s.rate, _amount(s.amount)] for s in v.stock],
    }


def _voucher_in(d: dict[str, Any]) -> Voucher:
    return Voucher(
        d["guid"],
        date.fromisoformat(d["on"]) if d["on"] else None,
        d["type"],
        d["number"],
        d["party"],
        d["narration"],
        [Entry(e[0], _amount_of(e[1])) for e in d["entries"]],
        [StockEntry(s[0], s[1], s[2], _amount_of(s[3])) for s in d["stock"]],
        int(d.get("alter_id", 0)),
    )


def to_json(s: Snapshot) -> bytes:
    return json.dumps(
        {
            "company": s.company,
            "copied_at": s.copied_at.isoformat(),
            "max_alter_id": s.max_alter_id,
            "ledgers": [
                [x.name, x.group, _amount(x.opening), _amount(x.closing)] for x in s.ledgers
            ],
            "vouchers": [_voucher_out(v) for v in s.vouchers.values()],
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def from_json(raw: bytes) -> Snapshot:
    d = json.loads(raw.decode("utf-8"))
    vouchers = [_voucher_in(v) for v in d["vouchers"]]
    return Snapshot(
        company=d["company"],
        copied_at=datetime.fromisoformat(d["copied_at"]),
        max_alter_id=int(d["max_alter_id"]),
        ledgers=[Ledger(x[0], x[1], _amount_of(x[2]), _amount_of(x[3])) for x in d["ledgers"]],
        vouchers={v.guid: v for v in vouchers},
    )


# ── The store ──────────────────────────────────────────────────────────────


class CopyStore:
    def __init__(self, folder: str | os.PathLike[str], keys: KeyProvider) -> None:
        self._folder = Path(folder)
        self._keys = keys

    def _path(self, company: str) -> Path:
        name = hashlib.sha256(company.encode("utf-8")).hexdigest()[:32]
        return self._folder / f"{name}{SUFFIX}"

    def _fernet(self) -> Fernet:
        return Fernet(self._keys.key())

    def save(self, snapshot: Snapshot) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        path = self._path(snapshot.company)
        tmp = path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(self._fernet().encrypt(to_json(snapshot)))
        os.replace(tmp, path)

    def load(self, company: str) -> Snapshot | None:
        path = self._path(company)
        if not path.exists():
            return None
        try:
            found = from_json(self._fernet().decrypt(path.read_bytes()))
        # The file is authenticated, so a wrong shape is unlikely. Any failure
        # still reads as no copy, never as a tool error.
        except (InvalidToken, ValueError, KeyError, TypeError, IndexError, ArithmeticError):
            log.warning("the local copy of one company does not read; the next sync replaces it")
            return None
        # A file is the copy of the company whose name hashes to it, and of no other.
        return found if found.company == company else None

    def delete(self, company: str) -> None:
        self._path(company).unlink(missing_ok=True)

    def delete_all(self) -> int:
        """Delete every copy (Disconnect, uninstall). Returns the count."""
        if not self._folder.exists():
            return 0
        gone = 0
        for path in self._folder.glob(f"*{SUFFIX}"):
            path.unlink(missing_ok=True)
            gone += 1
        # A crash between the write and the replace leaves a .tmp file. Only
        # the store's own: a 32-hex name, as ``_path`` makes it. COPY_DIR is
        # the customer's choice, so another .tmp there is not ours.
        for path in self._folder.glob("*.tmp"):
            if _OWN_TMP.fullmatch(path.name):
                path.unlink(missing_ok=True)
        return gone

    def keep_only(self, companies: tuple[str, ...] | list[str]) -> int:
        """Delete the copy of each company that left the allow-list."""
        if not self._folder.exists():
            return 0
        keep = {self._path(c).name for c in companies}
        gone = 0
        for path in self._folder.glob(f"*{SUFFIX}"):
            if path.name not in keep:
                path.unlink(missing_ok=True)
                gone += 1
        return gone
