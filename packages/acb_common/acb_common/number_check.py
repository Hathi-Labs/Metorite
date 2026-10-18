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
UUID), a URL and a code span. The parser skips them. An inline marker
(``**bold**``, ``_italic_``, a citation such as ``[1]``) is blanked first, so
it neither hides a count nor adds one.

**A derived figure** (fix round 3). A figure that no source holds can still
be the sum, the difference, the percent share or the percent change of TWO
tool figures. It passes only when the same text also cites BOTH operands,
each as a figure of its own. It gets the room of its stated precision and no
more: "about" gives room only to a figure that a source holds.

Fence: ``tests/unit/test_number_check.py``.
"""

from __future__ import annotations

import bisect
import re
from collections import deque
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
    # Only after a currency sign (fix round 4): "₹19.2m", "$4.5b", "$3mm".
    "m": Decimal(10) ** 6, "mm": Decimal(10) ** 6, "b": Decimal(10) ** 9,
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
    # A scale stays on the line of its number: "12,10,000\nB: ..." is no
    # billion (fix round 3). A no-break space may come before it, and "m",
    # "mm" and "b" are a scale only after a currency sign (fix round 4).
    r"(?:(?:[ \t\u00a0\u202f]?\n?[ \t]*(?P<scale>(?i:lakhs?|lacs?|crores?|thousand|"
    r"million|billion))|[ \t\u00a0\u202f]?(?P<scale2>(?i:cr|mn|bn)|k|K|L|M|B|"
    r"(?(cur)(?:mm|m|b)|(?!))))(?![A-Za-z0-9_]))?"
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
#: The most room that "about" gives a percent: 1 point. So "nearly 66%" is
#: no approximation of 60% (fix round 4).
APPROX_PERCENT_POINTS = Decimal(1)

#: A range with one unit for both ends: "₹3-25 lakh", "5-17 deals", or
#: "₹1.15" and "9.9 lakh" with an en dash. The parser reads BOTH ends, each
#: with the shared currency, scale and percent (fix round 5). A range of
#: years is no range.
_RANGE = re.compile(
    r"(?<![\w.,/:#@-])"
    rf"(?:(?P<cur>{_CURRENCY_BEFORE})\s?)?"
    r"(?P<a>\d+(?:\.\d+)?)[ \t]?[-\u2013][ \t]?(?P<b>\d+(?:\.\d+)?)"
    r"(?:[ \t\u00a0\u202f]?(?P<scale>(?i:lakhs?|lacs?|crores?|cr|thousand|million|mn|"
    r"billion|bn)|k|K|L|M|B)(?![A-Za-z0-9_]))?"
    r"(?:\s?(?P<pct>%))?"
    r"(?!\w|\.\d|[-\u2013/]\d)"
)

#: A currency sign before digits that are glued to letters: "₹19.2xy". The
#: parser cannot read it, so it keeps it as a figure that always fails. A
#: figure after a currency sign never disappears (fix round 4).
_GLUED = re.compile(
    r"(?<![A-Za-z0-9_])"
    rf"(?:{_CURRENCY_BEFORE})[\s\u00a0\u202f]?"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)[A-Za-z_][A-Za-z0-9_]*"
)

#: The words before a plain four-digit number that make it a year: "in 2026",
#: "since 2025", "FY 2026", "March 2026", "2025-2026". With no such word,
#: "2047 contacts" is a count and the check reads it. "of" and "to" make a
#: year only after a word of time ("the end of 2026") or after a year ("2025
#: to 2026"). So "a total of 2047" and "down to 1985" are counts (round 3).
_YEAR_BEFORE = re.compile(
    r"(?:\b(?i:in|since|by|from|until|till|before|after|during|year|"
    r"years|fy|cy|q[1-4]|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)"
    r"|\b(?i:end|start|beginning|middle|mid|half|quarter|q[1-4]|h[12]|month|"
    r"months|year|spring|summer|autumn|fall|winter)\s+(?i:of)"
    r"|\b(?:19|20)\d{2}\s+(?i:to|until|till|through)"
    r"|[-\u2013/'(])\s*$"
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
        # a numbered heading, "## 2. Deals" or "## 2.1 Deals". "## 2047
        # contacts" is a count, so a heading number needs its dot.
        r"^[ \t]*#{1,6}[ \t]+\d+(?:(?:\.\d+)+\.?|[.)])(?=[ \t])",
    )),
    re.DOTALL | re.MULTILINE,
)


#: An inline marker that is not part of a number: bold, italic and strike
#: (``**``, ``__``, ``_``, ``~~``), a citation (``[1]``, ``[^1]``, ``^1``,
#: ``【…】``, a dagger) and an inline HTML tag of emphasis. The parser blanks
#: each one first. So a marker neither hides a count ("__2047__") nor adds
#: one ("2047[1]"), and a year word still reads through it ("in **2026**").
_MARKUP = re.compile(
    "|".join((
        r"\*{1,3}",
        r"~~",
        r"(?<![A-Za-z0-9])_{1,3}(?=\S)",
        r"(?<=\S)_{1,3}(?![A-Za-z0-9])",
        r"\[\^?\d{1,3}\]",
        r"\^\d{1,3}(?!\d)",
        r"【[^】\n]{0,60}】",
        r"[†‡]",
        r"</?(?i:b|strong|i|em|u|mark|sup|sub|span)\b[^>]*>",
    )),
)


@dataclass(frozen=True)
class Figure:
    """One number in a text, with its value in plain units.

    ``value`` is in rupees (or the currency), percent points or a count.
    ``unit`` is the precision that the text states, in the same units. Half
    of it is the room for rounding. ``exact`` is true for a scaled number with
    fewer than two significant digits, which must match exactly.
    ``short`` is true for such a number also after "about". A derived figure
    gets no room for "about", so a short one must then be exact too.
    """

    text: str
    value: Decimal
    kind: str
    unit: Decimal
    exact: bool
    start: int
    end: int
    approx: bool = False
    short: bool = False
    #: True for a figure the parser cannot read (``_GLUED``). It never
    #: matches, and it is never a source.
    unreadable: bool = False

    @property
    def strict_room(self) -> Decimal:
        """The room of the stated precision alone, with none for "about"."""
        return Decimal(0) if self.short else self.unit / 2

    @property
    def room(self) -> Decimal:
        """The room of a direct match: the stated precision, or 10 percent
        after "about"."""
        room = Decimal(0) if self.exact else self.unit / 2
        if self.approx:
            share = abs(self.value) * APPROX_SHARE
            if self.kind == PERCENT:
                share = min(share, APPROX_PERCENT_POINTS)
            room = max(room, share)
        return room


@dataclass(frozen=True)
class CheckResult:
    """The answer of :func:`check`: the figures found, and those that failed."""

    figures: tuple[Figure, ...]
    unmatched: tuple[Figure, ...]

    @property
    def ok(self) -> bool:
        """True when each figure of the text matched a source."""
        return not self.unmatched


def _spaces(m: re.Match[str]) -> str:
    return " " * (m.end() - m.start())


def _unmark(text: str) -> str:
    """*text* with each inline marker turned into spaces (same length)."""
    return _MARKUP.sub(_spaces, text)


def _blank(text: str) -> str:
    """*text* with each marker and each span that holds no figure turned
    into spaces. The length stays, so each offset still points into *text*."""
    return _NOT_FIGURES.sub(_spaces, _unmark(text))


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
    is no run, so its number stays a figure. Only the first marker may come
    after a label (":" or "="). A later number after a label is a value, so
    "Won: 1. Lost: 2." holds two counts, and no run (fix round 3).
    """
    run: list[int] = []
    expected = 1
    for m in _INLINE_MARKER.finditer(scan):
        if int(m.group(1)) == expected:
            if expected > 1 and scan[:m.start()].rstrip().endswith((":", "=")):
                break
            run.append(m.start())
            expected += 1
    return set(run) if len(run) >= 2 else set()


def _parse(text: str, *, skip_years: bool) -> list[Figure]:
    scan = _blank(text or "")
    # The text with its markup blanked only, so a run can start at a
    # line-start marker, and "**1.** Open **2.** Won" is a run.
    markers = _inline_markers(_unmark(text or "")) if skip_years else set()
    found: list[Figure] = []
    for m in _FIGURE.finditer(scan):
        if m.start() in markers and not m.group("cur"):
            continue
        raw = m.group("num").replace(",", "")
        try:
            number = Decimal(raw)
        except InvalidOperation:
            continue
        scale_token = m.group("scale") or m.group("scale2")
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
        short = scale != 1 and _significant(raw) < 2
        found.append(Figure(
            text=text[m.start():m.end()].strip(),
            value=number * scale,
            kind=kind,
            unit=(Decimal(1).scaleb(-decimals)) * scale,
            exact=short and not approx,
            start=m.start(),
            end=m.end(),
            approx=approx,
            short=short,
        ))
    for r in _RANGE.finditer(scan):
        a, b = r.group("a"), r.group("b")
        if a.isdigit() and len(a) == 4 and 1900 <= int(a) <= 2100:
            continue  # "2025-26" is a range of years
        found = [f for f in found if not (r.start() <= f.start < r.end())]
        scale = _scale_of(r.group("scale"))
        kind = PERCENT if r.group("pct") else MONEY if r.group("cur") else PLAIN
        for digits, start, end in ((a, r.start(), r.end("a")), (b, r.start("b"), r.end())):
            decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
            short = scale != 1 and _significant(digits) < 2
            found.append(Figure(
                text=text[start:end].strip(), value=Decimal(digits) * scale, kind=kind,
                unit=Decimal(1).scaleb(-decimals) * scale, exact=short,
                start=start, end=end, short=short,
            ))
    spans = [(f.start, f.end) for f in found]
    for g in _GLUED.finditer(scan):
        if any(start <= g.start() < end for start, end in spans):
            continue
        try:
            number = Decimal(g.group("num").replace(",", ""))
        except InvalidOperation:
            number = Decimal(0)
        found.append(Figure(
            text=text[g.start():g.end()].strip(), value=number, kind=MONEY,
            unit=Decimal(1), exact=True, start=g.start(), end=g.end(),
            unreadable=True,
        ))
    found.sort(key=lambda f: f.start)
    return found


def find_figures(text: str) -> list[Figure]:
    """Each figure that *text* states, in order. Skips each form of item 3."""
    return _parse(text, skip_years=True)


def _compatible(claim: str, source: str) -> bool:
    """Money and percent never meet. A plain claim meets either kind of
    source. A percent claim never meets a plain source, so "down 12%" is no
    quote of a count of 12 (fix round 5)."""
    if claim == PERCENT and source == PLAIN:
        return False
    return {claim, source} != {MONEY, PERCENT}


#: The most values of one kind that take part in a derived figure. When a new
#: value comes and the pool is full, the OLDEST value leaves, so the figures of
#: the newest tool results always take part (fix round 3).
MAX_DERIVE = 500
#: The most values of one kind that ONE text adds to a derive pool. A text
#: with more adds its first ones (the route sorts the largest group first).
#: So one large result cannot push out the figures of every other result.
MAX_DERIVE_PER_TEXT = 200

#: The kinds that have a derive pool. A percent pool gives only the sum or
#: the difference of two percents ("a gap of 9.8 points", fix round 4).
_POOL_KINDS = (MONEY, PLAIN, PERCENT)

_HUNDRED = Decimal(100)

#: A value of compact JSON (``{"target":1234567}``) and of a CSV row
#: (``region,1234567,88``). The figure parse skips both, because a number
#: after ":" or "," is a time or a part of a grouped number in prose. So a
#: source text reads them here too (fix round 4).
_JSON_VALUE = re.compile(r'"[^"\n]{1,80}"\s*:\s*(-?\d+(?:\.\d+)?)(?=\s*[,}\]])')
_CSV_ROW = re.compile(r"^[^\n,]*,[^\n]*$", re.MULTILINE)
_CSV_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_CSV_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.+Z-]*)?")


def _data_values(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """Each JSON value and CSV field of *text* that the figure parse did not
    read: (offset, digits). A CSV row has no ", " and holds only numbers,
    dates and fields with no digit, so a grouped number in prose is no row."""
    out: list[tuple[int, str]] = []

    def _free(at: int) -> bool:
        return not any(start <= at < end for start, end in spans)

    for m in _JSON_VALUE.finditer(text):
        if _free(m.start(1)):
            out.append((m.start(1), m.group(1)))
    for row in _CSV_ROW.finditer(text):
        line = row.group(0)
        if ", " in line or line.count(",") < 1:
            continue
        fields = line.split(",")
        if not all(_CSV_NUMBER.fullmatch(f.strip()) or _CSV_DATE.fullmatch(f.strip())
                   or not any(c.isdigit() for c in f) for f in fields):
            continue
        at = row.start()
        for f in fields:
            if _CSV_NUMBER.fullmatch(f.strip()) and _free(at):
                out.append((at, f.strip()))
            at += len(f) + 1
    return out


class _Pool:
    """The values of one kind that take part in a derived figure.

    It keeps the values sorted for a fast range read, and in their order of
    arrival, so the oldest value leaves first when the pool is full.
    """

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.sorted: list[tuple[Decimal, int]] = []
        self.order: deque[tuple[Decimal, int]] = deque()
        self._seq = 0

    def add(self, value: Decimal) -> None:
        if len(self.order) >= self.cap:
            old = self.order.popleft()
            del self.sorted[bisect.bisect_left(self.sorted, old)]
        entry = (value, self._seq)
        self._seq += 1
        self.order.append(entry)
        bisect.insort(self.sorted, entry)

    def within(self, lo: Decimal, hi: Decimal) -> list[tuple[Decimal, int]]:
        """Each entry with a value in [lo, hi]."""
        start = bisect.bisect_left(self.sorted, (lo, -1))
        out: list[tuple[Decimal, int]] = []
        for entry in self.sorted[start:]:
            if entry[0] > hi:
                break
            out.append(entry)
        return out

    def values(self) -> list[Decimal]:
        """The values, oldest first."""
        return [value for value, _seq in self.order]

    def __len__(self) -> int:
        return len(self.order)


#: One operand that a text cites: its value, the index of the figure of the
#: text that cites it, and the id of its pool entry.
_Cited = tuple[Decimal, int, int]


@dataclass
class Sources:
    """The numbers that a text may state, kept sorted for a fast match.

    Add a text with :meth:`add_text` (a tool result, a member's message, a
    prompt) or a value with :meth:`add_value`. A source value is taken as
    exact. The sign is not compared: a text may say "down 5%" for -5. A book
    grows as its texts arrive, so a caller adds each tool result once. A new
    value waits in a list and the book sorts once before its next match, so
    a text of 100,000 figures costs one sort, not one insert each (round 4).

    A source added with ``derive=True`` (a figure that a tool or code gave)
    also goes into the derive pool of its kind. :func:`check` passes a
    figure that no source holds only as a derived figure of two operands
    that the text also cites, next to it in its sentence. Pairs only.

    :meth:`layer` gives a new book that reads this one too. Use it for a
    source of one check only (the member's words), with no copy.
    """

    _values: list[Decimal] = field(default_factory=list)
    _kinds: list[str] = field(default_factory=list)
    _pending: list[tuple[Decimal, str]] = field(default_factory=list)
    _pools: dict[str, _Pool] = field(default_factory=dict)
    _base: Sources | None = None

    def __len__(self) -> int:
        own = len(self._values) + len(self._pending)
        return own + (len(self._base) if self._base is not None else 0)

    def layer(self) -> Sources:
        """A new, empty book on top of this one. A value added to the new
        book does not change this one."""
        return Sources(_base=self)

    def add_value(self, value: Decimal | int | float | str, kind: str = PLAIN,
                  *, derive: bool = False) -> None:
        """Add one exact value of *kind*."""
        try:
            number = abs(Decimal(str(value)))
        except InvalidOperation:
            return
        self._pending.append((number, kind))
        if derive and kind in _POOL_KINDS:
            self._pools.setdefault(kind, _Pool(MAX_DERIVE)).add(number)

    def add_text(self, text: str, *, derive: bool = False) -> None:
        """Add each number in *text*. A year counts here, as a source. With
        *derive*, the first :data:`MAX_DERIVE_PER_TEXT` figures of each kind
        also go into the derive pool. A JSON value and a CSV field count too.
        A figure that the parser cannot read is no source."""
        pooled: dict[str, int] = {}
        figures = [f for f in _parse(text, skip_years=False) if not f.unreadable]
        data = _data_values(text or "", [(f.start, f.end) for f in figures])
        values = [(f.value, f.kind) for f in figures] + [(Decimal(v), PLAIN) for _at, v in data]
        for value, kind in values:
            used = pooled.get(kind, 0)
            take = derive and used < MAX_DERIVE_PER_TEXT
            if take:
                pooled[kind] = used + 1
            self.add_value(value, kind, derive=take)
        for _at, digits in data:
            # A data value has no unit: {"win_rate":60} may stand for 60%.
            self.add_value(Decimal(digits), PERCENT)

    def pool(self, kind: str) -> list[Decimal]:
        """The values of the derive pool of *kind*, oldest first."""
        found = self._pool(kind)
        return found.values() if found is not None else []

    def settle(self) -> None:
        """Sort each value that waits. A caller on a worker thread calls it
        after a large text, so the next match on the event loop sorts
        nothing (fix round 4)."""
        self._settle()

    def _settle(self) -> None:
        if not self._pending:
            return
        merged = sorted([*zip(self._values, self._kinds, strict=True), *self._pending],
                        key=lambda item: item[0])
        self._values = [value for value, _kind in merged]
        self._kinds = [kind for _value, kind in merged]
        self._pending = []

    def _books(self) -> list[Sources]:
        books: list[Sources] = []
        book: Sources | None = self
        while book is not None:
            books.append(book)
            book = book._base
        return books

    def _pool(self, kind: str) -> _Pool | None:
        for book in self._books():
            if kind in book._pools:
                return book._pools[kind]
        return None

    def matches(self, figure: Figure) -> bool:
        """True when a source value rounds to *figure* at its precision, or
        lies near it after "about". A derived figure is no match here.
        :func:`check` decides it, because it needs the text. A figure that
        the parser cannot read never matches."""
        if figure.unreadable:
            return False
        target = abs(figure.value)
        room = figure.room
        for book in self._books():
            book._settle()
            lo = bisect.bisect_left(book._values, target - room)
            hi = bisect.bisect_right(book._values, target + room)
            if any(_compatible(figure.kind, book._kinds[i]) for i in range(lo, hi)):
                return True
        return False

    def _cited(self, figures: tuple[Figure, ...]) -> dict[str, list[_Cited]]:
        """Each pool entry that a figure of the text states at the room of
        its stated precision (no room for "about"), sorted by value. Only a
        percent cites the percent pool, and a percent cites nothing else."""
        out: dict[str, list[_Cited]] = {}
        for kind in _POOL_KINDS:
            pool = self._pool(kind)
            if pool is None:
                continue
            cited: list[_Cited] = []
            for index, figure in enumerate(figures):
                if figure.unreadable or (figure.kind == PERCENT) != (kind == PERCENT):
                    continue
                if not _compatible(figure.kind, kind):
                    continue
                target, room = abs(figure.value), figure.strict_room
                cited.extend((value, index, seq)
                             for value, seq in pool.within(target - room, target + room))
            cited.sort()
            out[kind] = cited
        return out


def _ranges(target_kind: str, pool_kind: str, a: Decimal, target: Decimal,
            room: Decimal) -> list[tuple[Decimal, Decimal]]:
    """The ranges of the other operand ``b`` that give *target* with *a*.

    A sum or a difference: a + b, or b - a with b the larger. That is the
    rule of a money or plain target, and of each target from the percent
    pool. A percent target from a money or plain pool: the share a / b * 100,
    or the change (b - a) / a * 100 up or down.
    """
    low, high = target - room, target + room
    if target_kind != PERCENT or pool_kind == PERCENT:
        return [(low - a, high - a), (a + low, a + high)]
    out: list[tuple[Decimal, Decimal]] = []
    if a == 0 or high <= 0:
        return out
    out.append((a * _HUNDRED / high,
                a * _HUNDRED / low if low > 0 else Decimal("Infinity")))
    for sign in (1, -1):
        ends = sorted((a * (1 + sign * low / _HUNDRED), a * (1 + sign * high / _HUNDRED)))
        out.append((ends[0], ends[1]))
    return out


#: The pools that can give a derived figure of each kind.
_TARGET_POOLS: dict[str, tuple[str, ...]] = {
    MONEY: (MONEY,),
    PLAIN: (MONEY, PLAIN, PERCENT),
    PERCENT: (MONEY, PLAIN, PERCENT),
}

#: The end of a block: a blank line. A paragraph, a list and a table are each
#: ONE block, so a total in a heading line or a row of a table takes part
#: (fix round 5). Round 4 ended a window at each sentence and each new line,
#: and that failed the commonest true breakdowns.
_BLOCK_END = re.compile(r"\n[ \t]*\n")

#: A word that says a figure is derived (fix rounds 4 and 5). A derived
#: figure needs one in its block, at most :data:`_WORD_BEFORE` characters
#: before it or :data:`_WORD_AFTER` after it.
_DERIVE_WORDS = re.compile(
    r"\b(?i:total|totals|sum|together|combined|altogether|in\s+all|plus|net|minus|"
    r"gap|difference|differs?|change|changed|share|of|more|less|higher|lower|"
    r"more\s+than|less\s+than|fewer\s+than|exceeds?|exceeded|over|under|up|down|"
    r"points?|rest|remaining|so|between|that\s+is|this\s+is|which\s+is|"
    r"average|avg|mean|per|each)\b"
)
#: A word that says a figure is a quotient: "the average deal is ₹15.3 lakh
#: (₹1.84 crore over 12 deals)".
_QUOTIENT_WORDS = re.compile(r"\b(?i:average|avg|mean|per|each|over)\b")
_WORD_BEFORE = 48
_WORD_AFTER = 24

#: How many citing figures on EACH side of a derived figure give its
#: operands, in its block (fix round 5: two on each side). So a pair of far
#: figures of a long list never gives a figure by chance.
_NEAR = 2

#: The text between a figure and a percent that is its share: "₹7.06 crore
#: (52%)" or a table cell "| ₹7.06 crore | 52% |" (fix round 5).
_HOST_GAP = re.compile(r"[ \t]*\([ \t]*|[ \t]*\|[ \t]*")


@dataclass(frozen=True)
class _Text:
    """The blocks of the checked text, for the rule of a derived figure."""

    scan: str
    ends: tuple[int, ...]

    @classmethod
    def of(cls, text: str) -> _Text:
        scan = _blank(text or "")
        return cls(scan, tuple(m.start() for m in _BLOCK_END.finditer(scan)))

    def block(self, at: int) -> int:
        return bisect.bisect_left(self.ends, at)

    def bounds(self, at: int) -> tuple[int, int]:
        i = self.block(at)
        start = self.ends[i - 1] + 1 if i > 0 else 0
        end = self.ends[i] if i < len(self.ends) else len(self.scan)
        return start, end

    def has_word(self, figure: Figure, words: re.Pattern[str] = _DERIVE_WORDS) -> bool:
        start, end = self.bounds(figure.start)
        lo = max(start, figure.start - _WORD_BEFORE)
        hi = min(end, figure.end + _WORD_AFTER)
        return bool(words.search(self.scan[lo:hi]))

    def host(self, figures: tuple[Figure, ...], index: int) -> int | None:
        """The figure that a percent is the share of: the figure right before
        it, with only "(" or a table cell bar between them."""
        if index == 0 or figures[index].kind != PERCENT:
            return None
        before = figures[index - 1]
        if _HOST_GAP.fullmatch(self.scan[before.end:figures[index].start]):
            return index - 1
        return None


def _near_citers(entries: list[_Cited], index: int, figures: tuple[Figure, ...],
                 text: _Text) -> set[int]:
    """The citing figures next to figure *index* in its block: the nearest
    :data:`_NEAR` before it and the nearest :data:`_NEAR` after it."""
    here = text.block(figures[index].start)
    citers = sorted({by for _v, by, _s in entries
                     if by != index and text.block(figures[by].start) == here})
    before = [by for by in citers if by < index][-_NEAR:]
    after = [by for by in citers if by > index][:_NEAR]
    return {*before, *after}


def _within(value: Decimal, target: Decimal, room: Decimal) -> bool:
    return target - room <= value <= target + room


def _pairs_give(entries: list[_Cited], target_kind: str, pool_kind: str,
                target: Decimal, room: Decimal) -> bool:
    """True when two entries of two different figures give *target*."""
    if len(entries) < 2:
        return False
    values = [c[0] for c in entries]
    for a, by, seq in entries:
        for lo, hi in _ranges(target_kind, pool_kind, a, target, room):
            start = bisect.bisect_left(values, lo)
            end = bisect.bisect_right(values, hi)
            if any(entries[j][1] != by and entries[j][2] != seq
                   for j in range(start, end)):
                return True
    return False


def _host_share(figures: tuple[Figure, ...], index: int, host: int,
                cited: dict[str, list[_Cited]], text: _Text) -> bool:
    """A percent right after a figure ("₹7.06 crore (52%)", a Share cell):
    the share of that figure in a figure of the same block. No derive word
    is needed. The host may be its own whole ("Total … 100%")."""
    target, room = abs(figures[index].value), figures[index].strict_room
    here = text.block(figures[index].start)
    for kind in (MONEY, PLAIN):
        entries = cited.get(kind, [])
        hosts = [c[0] for c in entries if c[1] == host]
        wholes = [c[0] for c in entries
                  if c[1] != index and text.block(figures[c[1]].start) == here]
        for a in hosts:
            if any(b and _within(a * _HUNDRED / b, target, room) for b in wholes):
                return True
    return False


def _quotient(figures: tuple[Figure, ...], index: int, cited: dict[str, list[_Cited]],
              text: _Text) -> bool:
    """A money figure over a count next to it: "₹1.84 crore over 12 deals"
    gives an average of ₹15.3 lakh. It needs a word of a quotient."""
    figure = figures[index]
    if figure.kind == PERCENT or not text.has_word(figure, _QUOTIENT_WORDS):
        return False
    target, room = abs(figure.value), figure.strict_room
    money = cited.get(MONEY, [])
    counts = cited.get(PLAIN, [])
    near_money = _near_citers(money, index, figures, text)
    near_counts = _near_citers(counts, index, figures, text)
    return any(b and by_a != by_b and _within(a / b, target, room)
               for a, by_a, _s in money if by_a in near_money
               for b, by_b, _t in counts if by_b in near_counts)


def _share_base(figures: tuple[Figure, ...], index: int, cited: dict[str, list[_Cited]],
                text: _Text) -> bool:
    """The whole of a share that the text states: "we won 12 of 20 closed
    deals, a 60% win rate" gives 20 from 12 and 60%."""
    figure = figures[index]
    if figure.kind == PERCENT:
        return False
    target, room = abs(figure.value), figure.strict_room
    percents = cited.get(PERCENT, [])
    near_percents = _near_citers(percents, index, figures, text)
    for kind in _TARGET_POOLS[figure.kind]:
        if kind == PERCENT:
            continue
        parts = cited.get(kind, [])
        near_parts = _near_citers(parts, index, figures, text)
        for a, by_a, _s in parts:
            if by_a not in near_parts or a > target + room:
                continue
            for p, by_p, _t in percents:
                if by_p in near_percents and by_p != by_a and p and _within(
                        a * _HUNDRED / p, target, room):
                    return True
    return False


def _derived(figures: tuple[Figure, ...], index: int, cited: dict[str, list[_Cited]],
             text: _Text) -> bool:
    """True when OTHER figures of the text that cite tool values give figure
    *index*, at the room of its stated precision (round 3).

    The operands stand next to it in its block, two on each side, and a word
    of derivation stands near it (rounds 4 and 5). A percent right after a
    figure is the share of that figure, with no word. A quotient and the whole
    of a stated share are derived figures too (round 5).
    """
    figure = figures[index]
    if figure.unreadable:
        return False
    host = text.host(figures, index)
    if host is not None and _host_share(figures, index, host, cited, text):
        return True
    if not text.has_word(figure):
        return False
    target, room = abs(figure.value), figure.strict_room
    for kind in _TARGET_POOLS[figure.kind]:
        near = _near_citers(cited.get(kind, []), index, figures, text)
        entries = [c for c in cited.get(kind, []) if c[1] in near]
        if _pairs_give(entries, figure.kind, kind, target, room):
            return True
    return _share_base(figures, index, cited, text) or _quotient(figures, index, cited, text)


def check(text: str, sources: Sources | Iterable[str]) -> CheckResult:
    """Check each figure of *text* against *sources*.

    *sources* is a :class:`Sources`, or texts to read into one (each with
    ``derive=True``). A figure passes when a source holds it, or when OTHER
    figures next to it in its block cite tool values that give it, by a rule
    of :func:`_derived`.
    """
    if not isinstance(sources, Sources):
        book = Sources()
        for source in sources:
            book.add_text(source, derive=True)
        sources = book
    figures = tuple(find_figures(text))
    unmatched: list[Figure] = []
    cited: dict[str, list[_Cited]] | None = None
    shape: _Text | None = None
    for index, figure in enumerate(figures):
        if sources.matches(figure):
            continue
        if cited is None or shape is None:
            cited = sources._cited(figures)
            shape = _Text.of(text)
        if not _derived(figures, index, cited, shape):
            unmatched.append(figure)
    return CheckResult(figures=figures, unmatched=tuple(unmatched))


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
