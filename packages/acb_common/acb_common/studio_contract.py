"""The version of the Studio API contract (STU-G20).

Spec: ``project-docs/specs/ai_studio.md`` section 6.5 ("Compatibility (G20)")
and the code ``contract_unsupported`` of section 19.10.

``metorite.json`` pins a contract version. The server calls
:func:`check_contract` before a push writes anything. A refused version gets
the code ``contract_unsupported``. A version near its retire date gets a
warning, so ``check`` can tell the author 30 days ahead. The module is pure
data and does no I/O. The caller passes ``today``.

A change to the manifest schema needs a decision on this table. See
``tests/unit/test_studio_contract.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

#: The fixed error code of a refused version (spec section 19.10).
CONTRACT_UNSUPPORTED = "contract_unsupported"

#: ``check`` warns this many days before a version retires.
WARN_DAYS = 30

#: A version is a short run of digits. Anything else is refused.
_VERSION_RE = re.compile(r"[0-9]{1,6}")


@dataclass(frozen=True)
class ContractVersion:
    """One served version. ``retires_on`` is the last day it is accepted."""

    version: str
    served_from: date
    retires_on: date | None = None


#: Every version the server knows. Add a row, and never edit an old row
#: except to set ``retires_on``.
CONTRACT_VERSIONS: tuple[ContractVersion, ...] = (
    ContractVersion("1", served_from=date(2026, 11, 11)),
)

CURRENT_VERSION = "1"


@dataclass(frozen=True)
class ContractCheck:
    """The result of :func:`check_contract`."""

    status: str  # "ok", "warn" or "refused"
    version: str | None
    code: str | None = None
    days_left: int | None = None
    message: str = ""


def _refused(version: Any, message: str) -> ContractCheck:
    shown = version if isinstance(version, str) and _VERSION_RE.fullmatch(version) else None
    return ContractCheck("refused", shown, CONTRACT_UNSUPPORTED, None, message)


def check_contract(version: Any, today: date) -> ContractCheck:
    """Check a pinned version on ``today``. Never raises on bad input.

    The last day of service is ``retires_on``. On that day the version
    still works, with a warning of 0 days left. The day after, it is refused.
    """
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        return _refused(version, 'The contract version must be a text of digits, like "1".')
    row = next((r for r in CONTRACT_VERSIONS if r.version == version), None)
    if row is None or today < row.served_from:
        return _refused(version, f"The server does not serve contract version {version}.")
    if row.retires_on is None:
        return ContractCheck("ok", version)
    days_left = (row.retires_on - today).days
    if days_left < 0:
        return _refused(version, f"Contract version {version} retired on {row.retires_on}.")
    if days_left <= WARN_DAYS:
        return ContractCheck(
            "warn",
            version,
            None,
            days_left,
            f"Contract version {version} retires in {days_left} days.",
        )
    return ContractCheck("ok", version, None, days_left)


def support_window() -> dict[str, Any]:
    """The support window as data that a client can publish."""
    return {
        "current": CURRENT_VERSION,
        "warn_days": WARN_DAYS,
        "versions": [
            {
                "version": r.version,
                "served_from": r.served_from.isoformat(),
                "retires_on": r.retires_on.isoformat() if r.retires_on else None,
            }
            for r in CONTRACT_VERSIONS
        ],
    }
