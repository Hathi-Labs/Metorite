"""Copy one company from TallyPrime into the local copy (TLY-4).

**The first copy** reads the ledgers, then the vouchers of this financial year
and the last one, one month at a time. A long export freezes the Tally screen
of the person at the desk (brief section 5.5), so no request covers more than
one month. The reader's queue of one keeps the requests in line.

**Each later copy** reads the ledgers again (they are few, and their balances
move), and only the vouchers whose ``AlterID`` is above the highest one the
copy holds, over the same two years.

**A known gap.** A voucher that someone deletes in TallyPrime raises no
``AlterID``, so the copy keeps it until the next first copy. ``sync_company``
takes ``full=True`` to make a first copy again. The schedule runs one each day.

Writes never use the copy (writes are rung 3).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tally_bridge.localcopy.store import CopyStore, Snapshot
from tally_bridge.reader import TallyReader, envelope, records


def fy_start(d: date) -> date:
    """The first day of the Indian financial year (1 April) that holds d."""
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def copy_window(today: date) -> tuple[date, date]:
    """This financial year and the last one: from 1 April a year before the
    start of this one, to today."""
    start = fy_start(today)
    return date(start.year - 1, 4, 1), today


def months(first: date, last: date) -> list[tuple[date, date]]:
    """[first, last] cut into calendar months."""
    out = []
    cur = first
    while cur <= last:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        out.append((cur, min(last, nxt - timedelta(days=1))))
        cur = nxt
    return out


async def sync_company(
    reader: TallyReader,
    store: CopyStore,
    company: str,
    *,
    today: date | None = None,
    now: datetime | None = None,
    full: bool = False,
) -> Snapshot:
    """Bring the copy of *company* up to date, save it and return it.

    A reader error (Tally down, a refusal, an unreadable answer) propagates,
    and the old copy stays as it was: a copy is saved only when every request
    of the run succeeded.
    """
    today = today or date.today()
    now = now or datetime.now(UTC)
    first, last = copy_window(today)
    old = None if full else store.load(company)

    ledgers = records.ledgers(
        await reader.post(envelope.collection("BridgeLedgers", company=company))
    )

    vouchers = dict(old.vouchers) if old else {}
    after = old.max_alter_id if old else 0
    ranges = [(first, last)] if old else months(first, last)
    for start, end in ranges:
        root = await reader.post(envelope.vouchers_since(after, start, end, company=company))
        for v in records.vouchers(root):
            if v.guid:
                vouchers[v.guid] = v
    # A new financial year moves the window: drop what fell out of it.
    vouchers = {g: v for g, v in vouchers.items() if v.on is None or v.on >= first}
    top = max((v.alter_id for v in vouchers.values()), default=0)

    snapshot = Snapshot(
        company=company,
        copied_at=now,
        max_alter_id=max(top, after),
        ledgers=ledgers,
        vouchers=vouchers,
    )
    store.save(snapshot)
    return snapshot
