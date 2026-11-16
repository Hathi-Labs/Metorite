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

**Six checks today.** Each returns a list of :class:`Finding`. The checks
are :func:`concentration`, :func:`outlier`, :func:`coverage`, :func:`parts`,
:func:`part_period` and :func:`grouping`. Two kinds of spec §3 are NOT built:
``jump`` needs number memory (NUM-7), and ``duplicates`` waits for a later
slice. The six checks never read a clock: the caller passes the date. They
never raise on a hostile value (None, NaN, infinity, a bool or a string in a
numeric slot). Such a value is left out, and it never makes a finding.
:func:`findings` still runs :func:`concentration` only: NUM-2 decides the
call shape of the others.

**concentration.** A bucket concentrates the answer when ALL of these hold:

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

**outlier.** A bucket is an outlier when at least :data:`OUTLIER_MIN_PEERS`
other buckets hold a value, the median of those peers is above zero, and the
bucket is over :data:`OUTLIER_RATIO` times, or under one fifth of, that
median. A bucket with no value (None, NaN or a string) or no records is no
bucket: it is no peer and no candidate. The check reads the first
:data:`OUTLIER_MAX_BUCKETS` buckets and gives the :data:`OUTLIER_MAX_FINDINGS`
farthest ones.

**coverage.** At least :data:`COVERAGE_SHARE` of the records have no value in
the measure, or carry a status of :data:`COVERAGE_STATUSES`.

**parts.** The parts differ from the total by more than
:data:`PARTS_GAP_SHARE` of the total AND by more than
:data:`PARTS_GAP_MIN_UNITS` units.

**part_period.** The day that the caller passes is before the end of the
month, the week or the quarter. A week is 7 days from its start.

**grouping.** A figure has a malformed digit grouping (neither Indian nor
western), or it reads about ten times or more away from the source
value (:data:`GROUPING_SCALE_RATIO`, 9) after any grouping and after its unit word
(crore, lakh, million). A decimal comma ("12,34") reads as a malformed
grouping: the module has no locale.

This is not the faithfulness check of model text. That is
``acb_common.number_check``, and it stays separate. Fence:
``tests/unit/test_number_sense.py``.
"""

from __future__ import annotations

import heapq
import math
import re
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
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

#: The kind of a finding of :func:`outlier`.
OUTLIER = "outlier"
#: A bucket is an outlier when it is over this many times the peer median,
#: or under 1 / this many times.
OUTLIER_RATIO = 5.0
#: At least this many other buckets hold a value.
OUTLIER_MIN_PEERS = 3
#: The most buckets that :func:`outlier` reads (the first ones).
OUTLIER_MAX_BUCKETS = 20_000
#: The most findings of :func:`outlier`: the farthest ones.
OUTLIER_MAX_FINDINGS = 3

#: The kind of a finding of :func:`coverage`.
COVERAGE = "coverage"
#: This share of the records (0 to 1) with no value, or with a status below.
COVERAGE_SHARE = 0.20
#: The statuses that a total usually leaves out.
COVERAGE_STATUSES = ("draft", "cancelled")

#: The kind of a finding of :func:`parts`.
PARTS = "parts"
#: The gap must be over this share of the total (0 to 1) ...
PARTS_GAP_SHARE = 0.01
#: ... and over this many units.
PARTS_GAP_MIN_UNITS = 1.0
#: The most parts that :func:`parts` reads.
PARTS_MAX = 50

#: The kind of a finding of :func:`part_period`.
PART_PERIOD = "part_period"
#: The period kinds that :func:`part_period` knows.
PERIOD_KINDS = ("month", "week", "quarter")

#: The kind of a finding of :func:`grouping`.
GROUPING = "grouping"
#: A figure is right when it is this close to the source (a share, 0 to 1).
GROUPING_TOLERANCE = 0.01
#: A figure that is this many times away from the source (or more) is wrong.
#: The spec says ten times. A source that the answer rounds (33.57 crore)
#: reads 9.9995 times away from the digits 3,35,68,33,347, so the edge is 9.
GROUPING_SCALE_RATIO = 9.0

#: The severities of a finding.
INFO = "info"
WARN = "warn"

#: The shape of a day key. A month (``2026-10``) or a year is a bucket.
_DAY_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Finding:
    """One thing that a reader must know before the total.

    ``share`` is the bucket's share of the records, from 0 to 1.
    ``value_share`` is its share of the summed value, or None when the
    values do not add up (an average, a minimum, a maximum, or no value).
    ``sentence`` is one plain sentence that names the key, the count and
    the share. ``severity`` is ``warn`` (say it first) or ``info``. A check
    that has no bucket (``parts``, ``grouping``) sets ``records`` and
    ``total_records`` to 0 and ``share`` to the gap or to 0."""

    kind: str
    key: str
    records: int
    total_records: int
    share: float
    value_share: float | None
    sentence: str
    severity: str = WARN

    def wire(self) -> dict[str, Any]:
        """The finding as JSON for an API answer."""
        return {
            "kind": self.kind, "key": self.key, "records": self.records,
            "total_records": self.total_records, "share": round(self.share, 4),
            "value_share": None if self.value_share is None else round(self.value_share, 4),
            "sentence": self.sentence,
            "severity": self.severity,
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


def _median_without(counts: Sequence[float], one: float) -> float:
    """The median of the sorted *counts* with ONE copy of *one* left out."""
    at = bisect_left(counts, one)
    rest = [*counts[:at], *counts[at + 1:]]
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


# ── The safe readers of a hostile value ──────────────────────────────────────


def _num(value: Any) -> float | None:
    """A finite number, or None. A bool, a string, None, NaN and infinity
    are no number: they never make a finding."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count(value: Any) -> int | None:
    """A whole count of 0 or more, or None."""
    number = _num(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _word(value: Any, fallback: str, limit: int = 60) -> str:
    """A caller word for a sentence: one line, at most *limit* characters."""
    text = " ".join(str(value).split())[:limit]
    return text or fallback


def _amount(value: float, decimals: int = 2, *, indian: bool = True) -> str:
    """A small pure format: 78,000,000 reads "7.8 crore". No locale."""
    size = abs(value)
    if indian:
        steps = ((1e7, "crore"), (1e5, "lakh"))
    else:
        steps = ((1e9, "billion"), (1e6, "million"))
    for unit, name in steps:
        if size >= unit:
            return f"{_trim(value / unit, decimals)} {name}"
    return _trim(value, decimals)


def _trim(value: float, decimals: int) -> str:
    """*value* with its commas and at most *decimals* decimals."""
    text = f"{value:,.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _pair(left: float, right: float, *, indian: bool) -> tuple[str, str]:
    """Two amounts, each with enough decimals to read different."""
    for decimals in (2, 3, 4):
        a, b = _amount(left, decimals, indian=indian), _amount(right, decimals, indian=indian)
        if a != b:
            break
    return a, b


def _list(names: Sequence[str]) -> str:
    """"A", "A and B", "A, B and C"; more than 5 names read "The 7 parts"."""
    if len(names) > 5:
        return f"The {len(names)} parts"
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


# ── outlier ──────────────────────────────────────────────────────────────────


def _kth_without(values: Sequence[float], at: int, k: int) -> float:
    """The k-th value of the sorted *values* with the one at *at* left out."""
    return values[k] if k < at else values[k + 1]


def _times(ratio: float) -> str:
    """A ratio as "6 times", or "5.5 times" under 10."""
    whole = round(ratio)
    return f"{whole} times" if ratio >= 10 or abs(ratio - whole) < 0.05 else f"{ratio:.1f} times"


def outlier(
    buckets: Iterable[tuple[Any, Any, Any]], *, bucket: str = "bucket",
) -> list[Finding]:
    """The buckets that are far from the median of their peers, the farthest
    first. *buckets* holds ``(key, value, records)`` as for
    :func:`concentration`. A bucket with no key, no value or no records is no
    bucket. Returns an empty list when fewer than :data:`OUTLIER_MIN_PEERS`
    peers exist, or when the peer median is zero or less."""
    rows: list[tuple[str, float, int]] = []
    for key, value, records in buckets:
        number, count = _num(value), _count(records)
        if key is None or str(key) == "" or number is None or not count:
            continue
        rows.append((str(key), number, count))
        if len(rows) >= OUTLIER_MAX_BUCKETS:
            break
    if len(rows) - 1 < OUTLIER_MIN_PEERS:
        return []
    values = sorted(value for _k, value, _r in rows)
    total = sum(records for _k, _v, records in rows)
    word = bucket if bucket.isalpha() and len(bucket) <= 20 else "bucket"
    found: list[tuple[float, Finding]] = []
    for key, value, records in rows:
        at = bisect_left(values, value)
        mid = (len(values) - 1) // 2
        median = (
            float(_kth_without(values, at, mid)) if (len(values) - 1) % 2
            else (_kth_without(values, at, mid - 1) + _kth_without(values, at, mid)) / 2
        )
        if median <= 0:
            continue
        ratio = value / median
        if ratio > OUTLIER_RATIO:
            how = f"is {_times(ratio)} the median"
        elif ratio < 1 / OUTLIER_RATIO:
            how = "is under one fifth of the median"
        else:
            continue
        found.append((abs(math.log(max(ratio, 1e-300))), Finding(
            kind=OUTLIER, key=key, records=records, total_records=total,
            share=records / total, value_share=None,
            sentence=f"{key} {how} of the other {len(rows) - 1} {word}s. Check why.",
        )))
    found.sort(key=lambda pair: (-pair[0], pair[1].key))
    return [finding for _far, finding in found[:OUTLIER_MAX_FINDINGS]]


# ── coverage ─────────────────────────────────────────────────────────────────


def coverage(
    records_total: Any, records_without_value: Any,
    by_status: Mapping[Any, Any] | None = None, *, measure: str = "value",
) -> list[Finding]:
    """The gaps in the records of one answer.

    *records_without_value* of *records_total* have no value in the *measure*
    (a plain caller string). *by_status* maps a status to its count. One
    finding for the missing value, and one for the statuses of
    :data:`COVERAGE_STATUSES`, each at :data:`COVERAGE_SHARE` or more."""
    total, missing = _count(records_total), _count(records_without_value)
    if not total or (missing is not None and missing > total):
        return []
    out: list[Finding] = []
    if missing is not None and missing / total >= COVERAGE_SHARE:
        out.append(Finding(
            kind=COVERAGE, key="no_value", records=missing, total_records=total,
            share=missing / total, value_share=None,
            sentence=f"{missing:,} of the records have no {_word(measure, 'value')}. Check why.",
        ))
    left_out: dict[str, int] = {}
    for status, count in (by_status or {}).items():
        name, number = str(status).strip().lower(), _count(count)
        if name in COVERAGE_STATUSES and number:
            left_out[name] = left_out.get(name, 0) + number
    held = sum(left_out.values())
    if held and held <= total and held / total >= COVERAGE_SHARE:
        names = " or ".join(n for n in COVERAGE_STATUSES if n in left_out)
        out.append(Finding(
            kind=COVERAGE, key="status", records=held, total_records=total,
            share=held / total, value_share=None,
            sentence=f"{held:,} of the records have the status {names}. Check if they belong in the total.",
        ))
    return out


# ── parts ────────────────────────────────────────────────────────────────────


def parts(
    total: Any, items: Sequence[tuple[Any, Any]], *, indian: bool = True,
) -> list[Finding]:
    """Parts that must add up to *total* but do not.

    *items* holds ``(name, value)``. A part with no number is a garbage
    input: the check gives NO finding then, because a sum of a part that is
    not read would show a false gap. Needs at least 2 parts and at most
    :data:`PARTS_MAX`."""
    whole = _num(total)
    try:
        listed = list(items)[: PARTS_MAX + 1]
    except TypeError:
        return []
    if whole is None or not 2 <= len(listed) <= PARTS_MAX:
        return []
    values: list[float] = []
    names: list[str] = []
    for item in listed:
        try:
            name, value = item
        except (TypeError, ValueError):
            return []
        number = _num(value)
        if number is None:
            return []
        values.append(number)
        names.append(_word(name, "part", 30))
    added = math.fsum(values)
    gap = abs(added - whole)
    if gap <= PARTS_GAP_SHARE * abs(whole) or gap <= PARTS_GAP_MIN_UNITS:
        return []
    shown, wanted = _pair(added, whole, indian=indian)
    return [Finding(
        kind=PARTS, key="parts", records=0, total_records=0,
        share=gap / abs(whole) if whole else 1.0, value_share=None,
        sentence=f"{_list(names)} add up to {shown}, not {wanted}. Check the parts.",
    )]


# ── part_period ──────────────────────────────────────────────────────────────


def _as_date(value: Any) -> date | None:
    """A date, or None. A datetime reads as its date."""
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _period_end(kind: str, start: date) -> date | None:
    """The last day of the period that starts on *start*."""
    try:
        if kind == "week":
            return start + timedelta(days=6)
        months = 1 if kind == "month" else 3
        index = start.year * 12 + start.month - 1 + months
        return date(index // 12, index % 12 + 1, 1) - timedelta(days=1)
    except (OverflowError, ValueError):
        return None


def part_period(period_kind: Any, period_start: Any, as_of: Any) -> list[Finding]:
    """A period that is not complete on the day *as_of*.

    *period_kind* is one of :data:`PERIOD_KINDS`. The caller passes *as_of*,
    because this module reads no clock. A period that has not begun, and a
    period that ended on or before *as_of*, give no finding."""
    start, today = _as_date(period_start), _as_date(as_of)
    if period_kind not in PERIOD_KINDS or start is None or today is None:
        return []
    end = _period_end(period_kind, start)
    if end is None or not start <= today < end:
        return []
    days, size = (today - start).days + 1, (end - start).days + 1
    name = {
        "month": _MONTHS[start.month - 1],
        "week": f"The week of {start.isoformat()}",
        "quarter": f"The quarter from {start.isoformat()}",
    }[period_kind]
    return [Finding(
        kind=PART_PERIOD, key=start.isoformat(), records=days, total_records=size,
        share=days / size, value_share=None, severity=INFO,
        sentence=f"{name} has only {days} {'day' if days == 1 else 'days'} so far.",
    )]


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")

# ── grouping ─────────────────────────────────────────────────────────────────

_FIGURE = re.compile(
    r"^(?P<cur>INR|Rs\.?|₹|USD|\$|EUR|€)?\s*(?P<num>[+-]?[\d,]+(?:\.\d+)?)"
    r"\s*(?P<unit>crore|cr|lakh|lac|million|mn|billion|bn|thousand)?$",
    re.IGNORECASE,
)
_INDIAN = re.compile(r"^\d{1,2}(,\d{2})*,\d{3}$")
_WESTERN = re.compile(r"^\d{1,3}(,\d{3})+$")
_UNITS = {"crore": 1e7, "cr": 1e7, "lakh": 1e5, "lac": 1e5, "million": 1e6,
          "mn": 1e6, "billion": 1e9, "bn": 1e9, "thousand": 1e3}


def grouping(
    figure_text: Any, source_value: Any, tolerance: Any = GROUPING_TOLERANCE,
    *, indian: bool = True,
) -> list[Finding]:
    """A figure of an answer whose digits cannot be the source value.

    A finding when the digit grouping is neither Indian (3,35,68,33,347) nor
    western (335,683,334), or when the figure is more than *tolerance* (a
    share) and :data:`GROUPING_SCALE_RATIO` times or more away from
    *source_value*. A text that is no figure (words, two numbers) gives no
    finding: the check cannot judge it."""
    if not isinstance(figure_text, str):
        return []
    match = _FIGURE.match(figure_text.strip())
    if match is None:
        return []
    digits, shown = match.group("num"), " ".join(figure_text.split())
    whole = digits.split(".")[0].lstrip("+-")
    if "," in whole and not (_INDIAN.match(whole) or _WESTERN.match(whole)):
        return [_grouping(shown, f"{shown} has a wrong digit grouping.")]
    source, tol = _num(source_value), _num(tolerance)
    if source is None or source == 0 or tol is None or not 0 <= tol < 1:
        return []
    unit = match.group("unit")
    value = abs(float(digits.replace(",", ""))) * (_UNITS[unit.lower()] if unit else 1.0)
    gap = abs(value - abs(source))
    if gap <= tol * abs(source):
        return []
    if value and max(value / abs(source), abs(source) / value) < GROUPING_SCALE_RATIO:
        return []
    return [_grouping(shown, f"{shown} is not {_amount(source, 2, indian=indian)}.")]


def _grouping(shown: str, head: str) -> Finding:
    """The one finding of :func:`grouping`."""
    return Finding(
        kind=GROUPING, key=shown, records=0, total_records=0, share=0.0,
        value_share=None, sentence=f"{head} Use the display value.",
    )


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
    "COVERAGE",
    "COVERAGE_SHARE",
    "COVERAGE_STATUSES",
    "GROUPING",
    "GROUPING_SCALE_RATIO",
    "GROUPING_TOLERANCE",
    "INFO",
    "MONTH_EDGE_IS_NORMAL",
    "OUTLIER",
    "OUTLIER_MAX_BUCKETS",
    "OUTLIER_MAX_FINDINGS",
    "OUTLIER_MIN_PEERS",
    "OUTLIER_RATIO",
    "PARTS",
    "PARTS_GAP_MIN_UNITS",
    "PARTS_GAP_SHARE",
    "PARTS_MAX",
    "PART_PERIOD",
    "PERIOD_KINDS",
    "WARN",
    "Finding",
    "concentration",
    "coverage",
    "findings",
    "grouping",
    "month_edge",
    "outlier",
    "part_period",
    "parts",
]
