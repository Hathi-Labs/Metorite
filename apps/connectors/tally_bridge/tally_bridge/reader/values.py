"""Dates and amounts in Tally XML (brief section 5.5).

- **Dates** go in as ``YYYYMMDD``. They come out as ``YYYYMMDD``, or as
  ``1-Apr-2025`` / ``1-Apr-25`` in some reports. The bridge answers
  ``YYYY-MM-DD``.
- **Signs.** In Tally XML a debit amount is usually negative and a credit
  amount positive. The bridge never answers a bare signed number: it answers
  an amount with two decimals and a side, ``Dr`` or ``Cr``. A ``Dr`` or ``Cr``
  word in the text wins over the sign. **The sign rule is not proven yet**:
  the README lists a known ledger to record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

Side = Literal["Dr", "Cr"]

_CENT = Decimal("0.01")
_NUMBER = re.compile(r"^\(?(-|\(-\))?\)?\s*([0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+)$")
_SIDE_WORD = re.compile(r"\s*\b(Dr|Cr)\.?\s*$", re.I)


def to_tally_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def from_tally_date(text: str) -> date:
    """Read a Tally date. Raises ValueError on a form it does not know."""
    text = text.strip()
    if re.fullmatch(r"[0-9]{8}", text):
        return datetime.strptime(text, "%Y%m%d").date()
    for form in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(text, form).date()
        except ValueError:
            continue
    raise ValueError(f"Not a Tally date: {text!r}")


@dataclass(frozen=True)
class Amount:
    value: Decimal  # never negative
    side: Side | None  # None for zero

    @property
    def text(self) -> str:
        """Two decimals, as the tools answer it."""
        return str(self.value.quantize(_CENT, rounding=ROUND_HALF_UP))


def parse_amount(text: str | None) -> Amount:
    """Read a Tally amount: ``-1234.50``, ``1,234.50``, ``(-)1234.50``, ``1234.50 Dr``.

    An empty text is zero. Raises ValueError on anything else.
    """
    raw = (text or "").strip()
    if not raw:
        return Amount(Decimal(0), None)
    side: Side | None = None
    word = _SIDE_WORD.search(raw)
    if word:
        side = "Dr" if word.group(1).lower() == "dr" else "Cr"
        raw = raw[: word.start()].strip()
    m = _NUMBER.match(raw)
    if not m:
        raise ValueError(f"Not a Tally amount: {text!r}")
    try:
        value = Decimal(m.group(2).replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"Not a Tally amount: {text!r}") from exc
    negative = bool(m.group(1))
    if value == 0:
        return Amount(Decimal(0), None)
    if side is None:
        side = "Dr" if negative else "Cr"
    return Amount(value, side)
