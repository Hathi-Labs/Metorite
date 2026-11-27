"""Reads that fall back to the local copy when TallyPrime is closed (TLY-4).

One rule for ``find_ledgers``, ``list_vouchers`` and ``get_voucher``:

1. TallyPrime answers: the tool reads TallyPrime, as before.
2. TallyPrime is closed (``TallyDown``), and the company has a copy younger
   than ``COPY_MAX_AGE_DAYS``: the tool reads the copy, and the answer holds a
   ``source`` that names the copy's time and age, and one plain note. The chat
   repeats the age to the member (``integrations_console.md`` §3.8.13 item 3).
3. The copy is older than the maximum age: one plain sentence that names the
   age, and no data (§3.8.13 item 4).
4. No copy: the plain "TallyPrime is not running" sentence, as before.

A write never reads the copy. The other report tools need TallyPrime.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from tally_bridge.localcopy.store import CopyStore, Snapshot
from tally_bridge.localcopy.sync import copy_window
from tally_bridge.reader import TallyDown
from tally_bridge.tools.common import Refusal


def source_of(snap: Snapshot, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    at = snap.copied_at.astimezone(UTC)
    # The copy holds two financial years only. An empty answer for an older
    # date means "not in the copy", never "none exist", so the note says so.
    holds_from = copy_window(at.date())[0]
    return {
        "from": "local_copy",
        "copied_at": at.isoformat(timespec="minutes"),
        "age_minutes": snap.age_minutes(now),
        "holds_from": holds_from.isoformat(),
        "note": (
            f"From the local copy of {at:%Y-%m-%d %H:%M} UTC. TallyPrime is closed. "
            f"The copy holds vouchers from {holds_from.isoformat()} only."
        ),
    }


def fresh_copy(copy: CopyStore | None, company: str, max_age_days: int) -> Snapshot | None:
    """The copy of *company* that a read may use, or None when there is none.
    Raises Refusal when the copy is older than the maximum age."""
    if copy is None:
        return None
    snap = copy.load(company)
    if snap is None:
        return None
    age_days = snap.age_minutes() / (60 * 24)
    if age_days > max_age_days:
        raise Refusal(
            f"TallyPrime is not running, and the local copy of '{company}' is "
            f"{age_days:.1f} days old, past the limit of {max_age_days} days."
        )
    return snap


async def tally_or_copy(
    read: Callable[[], Awaitable[ET.Element]],
    copy: CopyStore | None,
    company: str,
    max_age_days: int,
) -> tuple[ET.Element | None, Snapshot | None]:
    """Read TallyPrime, or, when it is closed, the copy. Exactly one of the two
    results is set. Raises TallyDown when Tally is closed and no copy exists."""
    try:
        return await read(), None
    except TallyDown:
        snap = fresh_copy(copy, company, max_age_days)
        if snap is None:
            raise
        return None, snap


def copy_ages(copy: CopyStore | None, companies: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """For the status tool: the age in minutes of each allowed company's copy,
    or None when it has no copy. Empty when the copy is off."""
    if copy is None:
        return {}
    out: dict[str, Any] = {}
    for name in companies:
        snap = copy.load(name)
        out[name] = None if snap is None else snap.age_minutes()
    return out
