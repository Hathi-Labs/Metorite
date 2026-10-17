"""One number check for text that a model wrote (WS-53 CRM-Q29).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q29 items 1 to 3.

A model writes the sentence around a number that SQL gave. This module finds
each number in that sentence and checks it against the numbers of its
sources: the tool results of a chat turn (CRM-Q29), or the numbers of a digest
prompt (CRM-Q28 item 9). It is the ONE number check. Do not write a second.

It is pure: no I/O, no model, no settings. It lives in ``acb_common`` because
the gateway (the digest) and ``acb_skills`` (the chat check) both declare it.

**The rule of a match.** A number of the text matches when a source holds a
value that rounds to it at the precision that the text states. "12.3 lakh"
states a precision of 0.1 lakh, so it matches each source value from
12,25,000 to 12,35,000. A scaled number (lakh, crore, ``k``, million,
billion) that states fewer than two significant digits must be exact. A money
value never matches a percent value, and a percent never matches money. A
number with no unit can match either.

**What is not a figure.** A date, a time, a year from 1900 to 2100 with no
unit, a list marker, a number inside a word or an id (``Q3``, ``H-236``, a
UUID), a URL and a code span. The parser skips them.

Fence: ``tests/unit/test_number_check.py``.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: The three kinds of a figure.
PLAIN = "plain"
MONEY = "money"
PERCENT = "percent"

#: Each scale word, with its multiplier. Case counts for the one-letter forms.
_SCALE_WORDS: dict[str, Decimal] = {
    "lakh": Decimal(10) ** 5, "lakhs": Decimal(10) ** 5,
    "lac": Decimal(10) ** 5, "lacs": Decimal(10) ** 5,
    "crore": Decimal(10) ** 7, "crores": Decimal(10) ** 7, "cr": Decimal(10) ** 7,
    "thousand": Decimal(10) ** 3,
    "million": Decimal(10) ** 6, "mn": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9, "bn": Decimal(10) ** 9,
}
_SCALE_LETTERS: dict[str, Decimal] = {
    "k": Decimal(10) ** 3, "K": Decimal(10) ** 3, "L": Decimal(10) ** 5,
    "M": Decimal(10) ** 6, "B": Decimal(10) ** 9,
}

_CURRENCY_BEFORE = r"₹|Rs\.?|INR|USD|US\$|\$|€|EUR|£|GBP"
_CURRENCY_AFTER = r"INR|rupees?|USD|dollars?"

#: One figure: a currency before, the digits, a scale, a percent, a currency
#: after. The digits take Indian grouping (12,34,567), Western grouping
#: (1,234,567) or none. The conditional keeps a number glued to a word, a
#: path, a time or a version out: ``H-236``, ``3/4``, ``10:30``, ``1.2.3``.
_FIGURE = re.compile(
    r"(?<![A-Za-z0-9_])"
    rf"(?:(?P<cur>{_CURRENCY_BEFORE})\s?)?"
    r"(?(cur)|(?<![\w.,/:#@])(?<!\w[-\u2013]))"
    r"(?P<num>(?:\d{1,2}(?:,\d{2})+,\d{3}|\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?![.,]\d)(?!:\d)(?!/\d)"
    r"(?:\s?(?P<scale>(?i:lakhs?|lacs?|crores?|cr|thousand|million|mn|billion|bn)"
    r"|k|K|L|M|B)(?![A-Za-z0-9_]))?"
    r"(?:\s?(?P<pct>%|(?i:per\s?cent)))?"
    rf"(?:\s?(?P<cur2>(?i:{_CURRENCY_AFTER}))(?![A-Za-z]))?"
    r"(?P<mult>[x\u00d7](?![A-Za-z0-9_]))?"
    r"(?![A-Za-z0-9_])"
)

#: The words before a number that make it an approximation ("about ₹5
#: lakh"). Such a number matches a source within :data:`APPROX_SHARE` of it.
_APPROX_BEFORE = re.compile(
    r"(?:\b(?i:about|around|approximately|approx\.?|roughly|nearly|almost|"
    r"close\s+to|some)|~|≈)\s*$"
)
#: How far an approximate number may stand from its source: 10 percent.
APPROX_SHARE = Decimal("0.10")

#: The words before a plain four-digit number that make it a year: "in 2026",
#: "since 2025", "FY 2026", "March 2026", "2025-2026". With no such word,
#: "2047 contacts" is a count and the check reads it.
_YEAR_BEFORE = re.compile(
    r"(?:\b(?i:in|since|by|of|from|to|until|till|before|after|during|year|"
    r"years|fy|cy|q[1-4]|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)|[-\u2013/'(])\s*$"
)
#: A year that opens a range of years: "2025-26", "2025 to 2026".
_YEAR_AFTER = re.compile(r"\s*(?:[-\u2013/]|to)\s*\d{2,4}(?!\d)")
_LOOK_BACK = 24

_MONTHS = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?"
)

#: The spans that hold no figure. The parser blanks them before it scans.
_NOT_FIGURES = re.compile(
    "|".join((
        r"```.*?```",                                   # a fenced code block
        r"`[^`\n]*`",                                    # a code span
        r"\bhttps?://\S+",                               # a URL
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        r"\bid=\S+",                                     # an id that a tool prints
        r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b",
        r"\b\d{4}-\d{2}\b",                              # a month: 2026-10
        r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b",          # 17/10/2026
        r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?(?i:am|pm))?\b",  # a time
        r"\+\d{1,3}[\s-]?\(?\d{2,5}\)?[\s-]?\d{3,5}(?:[\s-]?\d{2,5})?",  # a phone
        r"\(\d{2,5}\)\s?\d{3,4}[\s-]?\d{3,5}",           # (080) 2345-6789
        r"\b\d{5}[\s-]\d{5}\b",                           # 98450 12345
        r"\b\d{3,4}-\d{3,4}-\d{4}\b",                     # 080-2345-6789
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}(?:,?\s+\d{{4}})?\b",
        rf"\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b",
        rf"\b{_MONTHS}\s+\d{{4}}\b",
        r"^[ \t]*(?:[-*+][ \t]+)?\d{1,3}[.)](?=[ \t])",  # a list marker
        r"^[ \t]*#{1,6}[ \t]+\d+(?:\.\d+)*",         # a numbered heading
    )),
    re.DOTALL | re.MULTILINE,
)


@dataclass(frozen=True)
class Figure:
    """One number in a text, with its value in plain units.

    ``value`` is in rupees (or the currency), percent points or a count.
    ``unit`` is the precision that the text states, in the same units. Half
    of it is the room for rounding. ``exact`` is true for a scaled number with
    fewer than two significant digits, which must match exactly.
    """

    text: str
    value: Decimal
    kind: str
    unit: Decimal
    exact: bool
    start: int
    end: int
    approx: bool = False


@dataclass(frozen=True)
class CheckResult:
    """The answer of :func:`check`: the figures found, and those that failed."""

    figures: tuple[Figure, ...]
    unmatched: tuple[Figure, ...]

    @property
    def ok(self) -> bool:
        """True when each figure of the text matched a source."""
        return not self.unmatched


def _blank(text: str) -> str:
    """*text* with each span that holds no figure turned into spaces."""
    return _NOT_FIGURES.sub(lambda m: " " * (m.end() - m.start()), text)


def _scale_of(token: str | None) -> Decimal:
    if not token:
        return Decimal(1)
    if token in _SCALE_LETTERS:
        return _SCALE_LETTERS[token]
    return _SCALE_WORDS.get(token.lower(), Decimal(1))


def _significant(digits: str) -> int:
    """The count of significant digits in *digits* (no sign, no grouping)."""
    stripped = digits.replace(".", "").lstrip("0")
    return len(stripped)


#: A candidate for an inline list marker: "1. ", "2) ".
_INLINE_MARKER = re.compile(r"(?<![\w.,])(\d{1,2})[.)](?=\s)")


def _inline_markers(scan: str) -> set[int]:
    """The start of each inline list marker: "1. a 2. b 3. c".

    A marker is a number with "." or ")" and a space, in a run that counts
    1, 2, 3 in order. A run needs two markers at least. A lone "2. crore"
    is no run, so its number stays a figure.
    """
    run: list[int] = []
    expected = 1
    for m in _INLINE_MARKER.finditer(scan):
        if int(m.group(1)) == expected:
            run.append(m.start())
            expected += 1
    return set(run) if len(run) >= 2 else set()


def _parse(text: str, *, skip_years: bool) -> list[Figure]:
    scan = _blank(text or "")
    # The original text, so a run can start at a line-start marker.
    markers = _inline_markers(text or "") if skip_years else set()
    found: list[Figure] = []
    for m in _FIGURE.finditer(scan):
        if m.start() in markers and not m.group("cur"):
            continue
        raw = m.group("num").replace(",", "")
        try:
            number = Decimal(raw)
        except InvalidOperation:
            continue
        scale_token = m.group("scale")
        scale = _scale_of(scale_token)
        decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
        if m.group("pct"):
            kind = PERCENT
        elif m.group("cur") or m.group("cur2"):
            kind = MONEY
        else:
            kind = PLAIN
        grouped = "," in m.group("num")
        bare = kind == PLAIN and scale == 1 and not grouped and decimals == 0
        before = scan[max(0, m.start() - _LOOK_BACK):m.start()]
        if (skip_years and bare and not m.group("mult") and len(raw) == 4
                and 1900 <= int(raw) <= 2100 and (_YEAR_BEFORE.search(before)
                     or _YEAR_AFTER.match(scan, m.end()))):
            continue
        if bare and len(raw) == 10 and raw[0] in "6789":
            continue  # a mobile number with no grouping: 9845012345
        approx = bool(_APPROX_BEFORE.search(before))
        found.append(Figure(
            text=text[m.start():m.end()].strip(),
            value=number * scale,
            kind=kind,
            unit=(Decimal(1).scaleb(-decimals)) * scale,
            exact=scale != 1 and _significant(raw) < 2 and not approx,
            start=m.start(),
            end=m.end(),
            approx=approx,
        ))
    return found


def find_figures(text: str) -> list[Figure]:
    """Each figure that *text* states, in order. Skips each form of item 3."""
    return _parse(text, skip_years=True)


def _compatible(claim: str, source: str) -> bool:
    """Money and percent never meet. A plain number meets either."""
    return {claim, source} != {MONEY, PERCENT}


#: The most values of one kind that take part in a derived figure.
MAX_DERIVE = 500

_HUNDRED = Decimal(100)


def _any_in(values: list[Decimal], lo: Decimal, hi: Decimal, skip: int) -> bool:
    """True when *values* (sorted) holds a value in [lo, hi] at an index
    other than *skip*."""
    start = bisect.bisect_left(values, lo)
    end = bisect.bisect_right(values, hi)
    return any(i != skip for i in range(start, end))


@dataclass
class Sources:
    """The numbers that a text may state, kept sorted for a fast match.

    Add a text with :meth:`add_text` (a tool result, a member's message, a
    prompt) or a value with :meth:`add_value`. A source value is taken as
    exact. The sign is not compared: a text may say "down 5%" for -5.

    A source added with ``derive=True`` (a figure that a tool or code gave)
    also takes part in a DERIVED figure: the sum or the difference of two
    such figures of one kind (money with money, plain with plain), or the
    percent change or the percent share of two (a percent figure). Pairs
    only, never a chain. A member's words are added with ``derive=False``.
    """

    _values: list[Decimal] = field(default_factory=list)
    _kinds: list[str] = field(default_factory=list)
    _derive: dict[str, list[Decimal]] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self._values)

    def add_value(self, value: Decimal | int | float | str, kind: str = PLAIN,
                  *, derive: bool = False) -> None:
        """Add one exact value of *kind*."""
        try:
            number = abs(Decimal(str(value)))
        except InvalidOperation:
            return
        at = bisect.bisect_left(self._values, number)
        self._values.insert(at, number)
        self._kinds.insert(at, kind)
        if derive and kind in (MONEY, PLAIN):
            pool = self._derive.setdefault(kind, [])
            if len(pool) < MAX_DERIVE:
                bisect.insort(pool, number)

    def add_text(self, text: str, *, derive: bool = False) -> None:
        """Add each number in *text*. A year counts here, as a source."""
        for figure in _parse(text, skip_years=False):
            self.add_value(figure.value, figure.kind, derive=derive)

    def matches(self, figure: Figure) -> bool:
        """True when a source value, or a derived figure of two sources,
        rounds to *figure* at its precision."""
        target = abs(figure.value)
        room = Decimal(0) if figure.exact else figure.unit / 2
        if figure.approx:
            room = max(room, target * APPROX_SHARE)
        lo = bisect.bisect_left(self._values, target - room)
        hi = bisect.bisect_right(self._values, target + room)
        if any(_compatible(figure.kind, self._kinds[i]) for i in range(lo, hi)):
            return True
        return self._derived(figure.kind, target, room)

    def _derived(self, kind: str, target: Decimal, room: Decimal) -> bool:
        """True when two derive sources give *target* (rule above)."""
        if kind == PERCENT:
            return any(self._percent(pool, target, room) for pool in self._derive.values())
        pools = [self._derive.get(MONEY, [])]
        if kind == PLAIN:
            pools.append(self._derive.get(PLAIN, []))
        for pool in pools:
            for i, a in enumerate(pool):
                # a + b = target, or |a - b| = target with b the larger.
                if _any_in(pool, target - room - a, target + room - a, i):
                    return True
                if _any_in(pool, a + target - room, a + target + room, i):
                    return True
        return False

    @staticmethod
    def _percent(pool: list[Decimal], target: Decimal, room: Decimal) -> bool:
        """A percent share a/b, or a percent change (b - a)/a, of two values."""
        low, high = target - room, target + room
        for i, a in enumerate(pool):
            if a == 0:
                continue
            # share: a / b * 100 in [low, high], so b in [a*100/high, a*100/low].
            b_lo = a * _HUNDRED / high if high > 0 else None
            b_hi = a * _HUNDRED / low if low > 0 else None
            if b_lo is not None and _any_in(pool, b_lo, b_hi if b_hi is not None
                                            else pool[-1], i):
                return True
            # change: b = a * (1 +- t / 100).
            for sign in (1, -1):
                ends = sorted((a * (1 + sign * low / _HUNDRED),
                               a * (1 + sign * high / _HUNDRED)))
                if _any_in(pool, ends[0], ends[1], i):
                    return True
        return False


def check(text: str, sources: Sources | Iterable[str]) -> CheckResult:
    """Check each figure of *text* against *sources*.

    *sources* is a :class:`Sources`, or texts to read into one.
    """
    if not isinstance(sources, Sources):
        book = Sources()
        for source in sources:
            book.add_text(source, derive=True)
        sources = book
    figures = tuple(find_figures(text))
    return CheckResult(
        figures=figures,
        unmatched=tuple(f for f in figures if not sources.matches(f)),
    )


def group_indian(whole: int) -> str:
    """*whole* with Indian grouping: 1234567 -> ``12,34,567``."""
    sign = "-" if whole < 0 else ""
    digits = str(abs(int(whole)))
    if len(digits) <= 3:
        return sign + digits
    head, tail = digits[:-3], digits[-3:]
    pairs: list[str] = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return sign + ",".join([head, *pairs, tail])


def format_figure(value: Decimal | int | float, kind: str = PLAIN, *, decimals: int = 0) -> str:
    """The ONE formatter of a figure for a prompt (CRM-Q28 item 9).

    Money: ``INR 12,34,567``. Percent: ``45.3%``. Plain: ``12,34,567``. It
    rounds half up to *decimals* places. :func:`check` reads each form back.
    """
    quantum = Decimal(1).scaleb(-decimals)
    number = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    sign = "-" if number < 0 else ""
    whole, _, frac = f"{abs(number):f}".partition(".")
    text = sign + group_indian(int(whole)) + (f".{frac}" if frac else "")
    if kind == MONEY:
        return f"INR {text}"
    if kind == PERCENT:
        return f"{text}%"
    return text


__all__ = [
    "MONEY", "PERCENT", "PLAIN", "CheckResult", "Figure", "Sources", "check",
    "find_figures", "format_figure", "group_indian",
]
