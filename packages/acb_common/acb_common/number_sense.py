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

1. The answer has at least :data:`CONCENTRATION_MIN_BUCKETS` buckets.
2. The bucket holds at least :data:`CONCENTRATION_MIN_RECORDS` records.
3. The bucket holds at least :data:`CONCENTRATION_SHARE` of all records.
4. The bucket holds more than :data:`CONCENTRATION_RATIO` times the median
   records of the other buckets.

Rule 4 compares a bucket with what is normal for this answer, so a month of
invoices that all carry the first day of the month gives no finding. Rule 3
keeps a small spike out. Rule 2 keeps a tiny sample out.

This is not the faithfulness check of model text. That is
``acb_common.number_check``, and it stays separate. Fence:
``tests/unit/test_number_sense.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median
from typing import Any

#: The kind of a finding of :func:`concentration`.
CONCENTRATION = "concentration"
#: Rule 4: a bucket holds more than this many times the median of the others.
CONCENTRATION_RATIO = 5.0
#: Rule 3: a bucket holds at least this share of all the records (0 to 1).
CONCENTRATION_SHARE = 0.30
#: Rule 2: a bucket holds at least this many records.
CONCENTRATION_MIN_RECORDS = 20
#: Rule 1: an answer with fewer buckets than this has no "other" to compare.
CONCENTRATION_MIN_BUCKETS = 2


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


def concentration(
    buckets: Iterable[tuple[Any, Any, Any]], *, label: str = "value",
    summed: bool = True,
) -> list[Finding]:
    """The buckets that concentrate the answer, the largest first.

    *buckets* holds ``(key, value, records)`` for each bucket. *label* names
    what a key is, for the sentence ("Invoice Date"). *summed* says that the
    values add up to the total (a count or a sum), so a finding also gives
    its share of the value. Returns an empty list when no bucket passes
    every rule of the module doc."""
    rows = _clean(buckets)
    if len(rows) < CONCENTRATION_MIN_BUCKETS:
        return []
    total = sum(records for _key, _value, records in rows)
    values = [value for _key, value, _records in rows]
    value_total = (
        sum(v for v in values if v is not None)
        if summed and any(v is not None for v in values) else None
    )
    found: list[Finding] = []
    for index, (key, value, records) in enumerate(rows):
        others = [r for i, (_k, _v, r) in enumerate(rows) if i != index]
        share = records / total
        if (records < CONCENTRATION_MIN_RECORDS or share < CONCENTRATION_SHARE
                or records <= CONCENTRATION_RATIO * median(others)):
            continue
        value_share = (
            (value or 0.0) / value_total
            if value_total is not None and value_total > 0 and value is not None
            and value >= 0 else None
        )
        sentence = (
            f"{records:,} of the {total:,} records ({_percent(share)}) have the one "
            f"{label} {key}"
        )
        if value_share is not None:
            sentence += f", and they hold {_percent(value_share)} of the total"
        found.append(Finding(
            kind=CONCENTRATION, key=key, records=records, total_records=total,
            share=share, value_share=value_share, sentence=sentence + ".",
        ))
    found.sort(key=lambda f: (-f.records, f.key))
    return found


def findings(
    buckets: Iterable[tuple[Any, Any, Any]], *, label: str = "value",
    summed: bool = True,
) -> list[Finding]:
    """Every finding of this module over *buckets*. Today that is
    :func:`concentration`. A later check (an outlier, the coverage of a
    field, a sum of parts that does not match) adds its findings here."""
    return concentration(list(buckets), label=label, summed=summed)


__all__ = [
    "CONCENTRATION",
    "CONCENTRATION_MIN_BUCKETS",
    "CONCENTRATION_MIN_RECORDS",
    "CONCENTRATION_RATIO",
    "CONCENTRATION_SHARE",
    "Finding",
    "concentration",
    "findings",
]
