"""The schedule of the local copy (TLY-4).

Every ``COPY_INTERVAL_MIN`` minutes (30 by default, the owner's answer to
LNK-Q9), while TallyPrime answers, the loop copies each allowed company. Each
tick first deletes the copy of a company that left the allow-list. Once a day
it makes a first copy again, which drops the vouchers that someone deleted in
TallyPrime (``sync.py``, "A known gap").

``one_tick`` does one round, so a test runs it with no clock.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any

from tally_bridge.localcopy.store import CopyStore
from tally_bridge.localcopy.sync import sync_company
from tally_bridge.reader import TallyAnswerError, TallyDown, TallyReader, TallyRefused, TallyTimeout

log = logging.getLogger("tally_bridge.localcopy")


async def one_tick(
    reader: TallyReader,
    store: CopyStore,
    settings: Any,
    *,
    full_day: dict[str, date],
    now: datetime | None = None,
) -> dict[str, str]:
    """One round. Returns the result for each allowed company: ``copied``,
    ``tally_down`` or ``failed``. *full_day* remembers the day of the last
    first copy of each company."""
    now = now or datetime.now(UTC)
    allowed = tuple(settings.allowed_companies)
    store.keep_only(allowed)
    out: dict[str, str] = {}
    if not allowed:
        return out
    if not await reader.is_up():
        return {c: "tally_down" for c in allowed}
    for company in allowed:
        full = full_day.get(company) != now.date()
        try:
            await sync_company(reader, store, company, today=now.date(), now=now, full=full)
        except (TallyDown, TallyTimeout):
            out[company] = "tally_down"
            continue
        except (TallyRefused, TallyAnswerError) as exc:
            log.warning("the local copy of one company failed: %s", type(exc).__name__)
            out[company] = "failed"
            continue
        if full:
            full_day[company] = now.date()
        out[company] = "copied"
    return out


async def run_forever(reader: TallyReader, store: CopyStore, settings: Any) -> None:
    """The loop that ``serve`` starts. It never raises: a failed round is
    logged, and the next round tries again."""
    full_day: dict[str, date] = {}
    while True:
        try:
            result = await one_tick(reader, store, settings, full_day=full_day)
            if result:
                log.info("local copy round: %s", result)
        except Exception:
            log.exception("local copy round failed")
        await asyncio.sleep(max(60, int(settings.copy_interval_min) * 60))
