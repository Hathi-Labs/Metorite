"""The number check of an answer (WS-53 CRM-Q30, a local stand-in for CRM-Q29).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30 item 9.

⚠️ **This is a local checker.** CRM-Q29 adds a shared function that checks
the numbers of an answer, and it is not on ``origin/main`` on 2026-10-17.
When it merges, this module goes, and :mod:`checkers` calls the shared
function instead. One rule of a number in two places is a defect.

:func:`parse` reads each number of a text, in the forms that a founder in
India reads: ``12,34,567`` and ``1,234,567`` groups, ``₹`` or ``INR``, and the
words lakh, crore, thousand, million and billion with their short forms.

:func:`matches` says whether a text holds a number. A written number stands
for each value inside half of its last written digit. ``₹1.23 crore`` stands
for 1,22,50,000 to 1,23,50,000. A written form may not be coarser than
:data:`REL_CAP` of the value, so ``₹1.2 crore`` does not stand for
1,23,45,678. A whole number stands for itself, so a count must be exact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

#: The coarsest written form that still counts: half a percent of the value.
REL_CAP = Decimal("0.005")

#: The rounding of a fact. Facts keep two decimals.
FACT_SLACK = Decimal("0.006")

_SCALES: dict[str, Decimal] = {
    "crore": Decimal(10) ** 7, "crores": Decimal(10) ** 7, "cr": Decimal(10) ** 7,
    "lakh": Decimal(10) ** 5, "lakhs": Decimal(10) ** 5, "lac": Decimal(10) ** 5,
    "lacs": Decimal(10) ** 5, "l": Decimal(10) ** 5,
    "thousand": Decimal(10) ** 3, "k": Decimal(10) ** 3,
    "million": Decimal(10) ** 6, "mn": Decimal(10) ** 6, "m": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9, "bn": Decimal(10) ** 9,
}

_NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?"
    r"(?:\s*(crores?|cr|lakhs?|lacs?|l|thousand|k|million|mn|m|billion|bn)\.?)?(?![\w])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Written:
    """One number of a text: its value, and the half step of its last digit."""

    text: str
    value: Decimal
    half_step: Decimal


def parse(text: str) -> list[Written]:
    """Each number in *text*, in order."""
    out = []
    for m in _NUMBER.finditer(text or ""):
        whole, frac, scale = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
        mult = _SCALES.get(scale, Decimal(1))
        digits = Decimal(f"{whole.replace(',', '')}.{frac}" if frac else whole.replace(",", ""))
        half = Decimal(1).scaleb(-len(frac)) * mult / 2
        out.append(Written(m.group(0), digits * mult, half))
    return out


def matches(expected: Decimal | int | float, text: str) -> bool:
    """True when *text* holds a number that stands for *expected*."""
    want = Decimal(str(expected))
    for w in parse(text):
        coarse = w.half_step > Decimal("0.5") and w.half_step > abs(want) * REL_CAP
        if not coarse and abs(w.value - want) <= w.half_step + FACT_SLACK:
            return True
    return False


def holds_exactly(number: Decimal | int, text: str) -> bool:
    """True when *text* writes *number* as a whole, for a forbidden number."""
    want = Decimal(str(number))
    return any(w.value == want for w in parse(text))


__all__ = ["REL_CAP", "Written", "holds_exactly", "matches", "parse"]
