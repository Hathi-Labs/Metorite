"""Number sense: plausibility checks over the numbers of one answer (task 3129).

Spec: ``project-docs/specs/crm_platform.md`` §18.11, the row CRM-Q25e part 4.

A total can be true and still mislead. On 2026-11-13 a Zoho org had 13,222 of
its 14,313 October invoices on ONE Invoice_Date, 2026-10-17, after a mass
update in Zoho. The chat summed them as the sales of October, and nobody saw
why the number was four times too large. This module finds that shape in the
numbers that a statement already gave, so the answer can say it first.

**Pure.** No I/O, no settings, no model, no clock. The caller passes the
buckets of one answer: ``(key, value, records)`` for each group or each day.
The CRM aggregate (``gateway/routes/crm/aggregate.py``) is the first caller.
Other apps reuse it for their own plausibility checks, so each check is
general, and each threshold is a named constant here.

**One check today: concentration.** A bucket concentrates the answer when
ALL of these hold:

1. At least :data:`CONCENTRATION_MIN_OTHERS` OTHER buckets hold records. Two
   or three buckets give no "normal" to compare with: a year of 3 records
   beside a year of 60, or last month beside the second day of this month.
2. The bucket holds at least :data:`CONCENTRATION_MIN_RECORDS` records.
3. The bucket holds at least :data:`CONCENTRATION_SHARE` of all records.
4. The bucket holds more than :data:`CONCENTRATION_RATIO` times the median
   records of the other buckets.
5. A DAY (a key ``YYYY-MM-DD``) is not the first or the last day of its
   month. Rent, fees and subscriptions bill on the first day, and a month,
   a quarter or a year closes on the last day. A batch on such a day is
   normal, so it is no finding (:data:`MONTH_EDGE_IS_NORMAL`).

Rule 4 compares a bucket with what is normal for this answer. Rule 3 keeps a
small spike out. Rule 2 keeps a tiny sample out. The five rules give NO
finding for 95 rent invoices on 2026-10-01 beside 5 on other days, for a
year-end batch of 400 of 550 on 2026-03-31, and for two buckets. They still
give one for 13,222 of 14,313 October records on 2026-10-17.

**Cost.** One pass over the buckets, one sort of the counts, and at most 3
candidates (rule 3 allows no more). The check reads the
:data:`CONCENTRATION_MAX_BUCKETS` largest buckets at most, and the total of
records counts every bucket. A caller can run it on an event loop.

This is not the faithfulness check of model text. That is
``acb_common.number_check``, and it stays separate. Fence:
``tests/unit/test_number_sense.py``.
"""

from __future__ import annotations

import heapq
import re
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

#: The kind of a finding of :func:`concentration`.
CONCENTRATION = "concentration"
#: Rule 4: a bucket holds more than this many times the median of the others.
CONCENTRATION_RATIO = 5.0
#: Rule 3: a bucket holds at least this share of all the records (0 to 1).
CONCENTRATION_SHARE = 0.30
#: Rule 2: a bucket holds at least this many records.
CONCENTRATION_MIN_RECORDS = 20
#: Rule 1: at least this many OTHER buckets hold records.
CONCENTRATION_MIN_OTHERS = 4
#: Rule 5: the first and the last day of a month are normal batch days.
MONTH_EDGE_IS_NORMAL = True
#: The most buckets that one check reads: the largest ones. A bucket past
#: the cap is small, so it can only lower the median of the others.
CONCENTRATION_MAX_BUCKETS = 20_000

#: The shape of a day key. A month (``2026-10``) or a year is a bucket.
_DAY_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Finding:
    """One thing that a reader must know before the total.

    ``share`` is the bucket's share of the records, from 0 to 1.
    ``value_share`` is its share of the summed value, or None when the
    values do not add up (an average, a minimum, a maximum, or no value).
    ``sentence`` is one plain sentence that names the key, the count and
    the share."""

    kind: str
    key: str
    records: int
    total_records: int
    share: float
    value_share: float | None
    sentence: str

    def wire(self) -> dict[str, Any]:
        """The finding as JSON for an API answer."""
        return {
            "kind": self.kind, "key": self.key, "records": self.records,
            "total_records": self.total_records, "share": round(self.share, 4),
            "value_share": None if self.value_share is None else round(self.value_share, 4),
            "sentence": self.sentence,
        }


def _percent(share: float) -> str:
    """A share as a whole percent. A share under 1 % reads "under 1%"."""
    whole = round(share * 100)
    return "under 1%" if whole == 0 and share > 0 else f"{whole}%"


def _clean(buckets: Iterable[tuple[Any, Any, Any]]) -> list[tuple[str, float | None, int]]:
    """Each bucket with a key, as ``(key, value or None, records)``. A bucket
    with no key (no date) or no record is left out: it is no bucket."""
    out: list[tuple[str, float | None, int]] = []
    for key, value, records in buckets:
        count = int(records or 0)
        if key is None or str(key) == "" or count <= 0:
            continue
        out.append((str(key), None if value is None else float(value), count))
    return out


def month_edge(key: str) -> bool:
    """True when *key* is a day (``YYYY-MM-DD``) that is the first or the
    last day of its month. A key that is no real day is False."""
    if not _DAY_KEY.match(key):
        return False
    try:
        day = date.fromisoformat(key)
    except ValueError:
        return False
    return day.day == 1 or (day + timedelta(days=1)).day == 1


def _median_without(counts: list[int], one: int) -> float:
    """The median of the sorted *counts* with ONE copy of *one* left out."""
    at = bisect_left(counts, one)
    rest = counts[:at] + counts[at + 1:]
    middle = len(rest) // 2
    return float(rest[middle]) if len(rest) % 2 else (rest[middle - 1] + rest[middle]) / 2


def concentration(
    buckets: Iterable[tuple[Any, Any, Any]], *, label: str = "value",
    summed: bool = True, bucket: str | None = None,
) -> list[Finding]:
    """The buckets that concentrate the answer, the largest first.

    *buckets* holds ``(key, value, records)`` for each bucket. *label* names
    the field ("Invoice Date"). *bucket* is None when each key is a day, or
    the word of the bucket ("month", "year") when each key is a bucket. Rule
    5 holds only for a day. *summed* says that the values add up to the
    total (a count or a sum), so a finding also gives its share of the
    value. Returns an empty list when no bucket passes every rule of the
    module doc."""
    rows = _clean(buckets)
    if len(rows) - 1 < CONCENTRATION_MIN_OTHERS:
        return []
    total = sum(records for _key, _value, records in rows)
    value_total = (
        sum(value for _key, value, _records in rows if value is not None)
        if summed and any(value is not None for _k, value, _r in rows) else None
    )
    if len(rows) > CONCENTRATION_MAX_BUCKETS:
        rows = heapq.nlargest(CONCENTRATION_MAX_BUCKETS, rows, key=lambda row: row[2])
    counts = sorted(records for _key, _value, records in rows)
    found: list[Finding] = []
    for key, value, records in rows:
        share = records / total
        if records < CONCENTRATION_MIN_RECORDS or share < CONCENTRATION_SHARE:
            continue
        if bucket is None and MONTH_EDGE_IS_NORMAL and month_edge(key):
            continue
        if records <= CONCENTRATION_RATIO * _median_without(counts, records):
            continue
        value_share = (
            (value or 0.0) / value_total
            if value_total is not None and value_total > 0 and value is not None
            and value >= 0 else None
        )
        found.append(Finding(
            kind=CONCENTRATION, key=key, records=records, total_records=total,
            share=share, value_share=value_share,
            sentence=_sentence(key, records, total, share, value_share, label, bucket),
        ))
    found.sort(key=lambda f: (-f.records, f.key))
    return found


def _sentence(key: str, records: int, total: int, share: float,
              value_share: float | None, label: str, bucket: str | None) -> str:
    """One plain sentence. A day: "… have the one Invoice Date 2026-10-17".
    A bucket: "… fall in 2026-10, one month of Invoice Date"."""
    head = f"{records:,} of the {total:,} records ({_percent(share)})"
    where = (f"have the one {label} {key}" if bucket is None
             else f"fall in {key}, one {bucket} of {label}")
    tail = (f", and they hold {_percent(value_share)} of the total"
            if value_share is not None else "")
    return f"{head} {where}{tail}."


def findings(
    buckets: Iterable[tuple[Any, Any, Any]], *, label: str = "value",
    summed: bool = True, bucket: str | None = None,
) -> list[Finding]:
    """Every finding of this module over *buckets*. Today that is
    :func:`concentration`. A later check (an outlier, the coverage of a
    field, a sum of parts that does not match) adds its findings here."""
    return concentration(buckets, label=label, summed=summed, bucket=bucket)


__all__ = [
    "CONCENTRATION",
    "CONCENTRATION_MAX_BUCKETS",
    "CONCENTRATION_MIN_OTHERS",
    "CONCENTRATION_MIN_RECORDS",
    "CONCENTRATION_RATIO",
    "CONCENTRATION_SHARE",
    "MONTH_EDGE_IS_NORMAL",
    "Finding",
    "concentration",
    "findings",
    "month_edge",
]
