"""The body of a confirmation card: what stays whole, and what may be cut.

``acb_skills.ask_tools.request_confirmation`` clips ``context`` at
:data:`CARD_CLIP` characters. The Projects tools and the CRM tools each cut a
body that was too long, from its tail, as one string. So a long description took
every line after it. An ``update_project`` with 4100 characters of description
drew no ``status`` line and no ``lead`` line, and the write sent both (RX-1).

D151 rests on one promise: the member's click on a server-built card is the
guard, so the card must show the exact change. The rule lives here, once:

1. A short field shows first, and it is never cut. :data:`LONG_TEXT_KEYS` names
   the free text. Each other key is a short field, whatever its length.
2. Only long text may be cut, and the last line of the card says so
   (:data:`CUT_MARK`).
3. When the short fields alone do not fit, no card can show the change.
   :func:`card_body` raises :class:`CardWontFit`, a refusal the model reads.
4. A value holds one line (:func:`one_line`). A value with a newline can forge
   a line of the card, because a card is read line by line.

The caller decides what a cut means. An open chat may show it. A chat that read
outside mail refuses it (:func:`cut_refusal`), because a text with its tail
hidden is not consent. Fence: ``tests/unit/test_card_body.py``.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from acb_common.tool_refusals import GatewayRefusal

__all__ = [
    "CARD_CLIP",
    "CUT_MARK",
    "LONG_TEXT_KEYS",
    "CardBody",
    "CardWontFit",
    "card_body",
    "cut_refusal",
    "is_cut",
    "one_line",
]

#: ``request_confirmation`` clips ``context`` at this many characters.
CARD_CLIP = 4000
#: The last line of a card that a cut shortened. The client draws it as the
#: trailing note (``ConfirmationCard.tsx`` ``TRUNCATED``).
CUT_MARK = "… [truncated on this card; the full text is what gets written]"
#: The keys of a card that hold free text. Only these may be cut.
LONG_TEXT_KEYS: frozenset[str] = frozenset({"body", "description", "notes"})
#: The least of each long text that a cut card shows. Below this, a cut shows no
#: useful part of the text, so the card does not fit.
_LONG_TEXT_MIN = 80


class CardWontFit(GatewayRefusal):
    """The short fields alone do not fit on a card. Nothing was written."""


class CardBody(str):
    """A card body. :attr:`cut` names the long text that a cut shortened, and
    :attr:`room` the characters that the card kept for it."""

    cut: tuple[str, ...]
    room: int

    def __new__(cls, text: str, cut: Iterable[str] = (), room: int = 0) -> CardBody:
        made = super().__new__(cls, text)
        made.cut = tuple(cut)
        made.room = room
        return made


def one_line(value: Any) -> str:
    """*value* as one line of a card: each run of white space is one space."""
    return " ".join(str(value).split())


def _cut_line(line: str, size: int) -> str:
    """*line* in at most *size* characters, with an ellipsis. A fence that the
    cut opens stays closed, so the client still draws one token."""
    text = line[: max(0, size - 2)].rstrip() + "…"
    return text + "»" if text.count("«") > text.count("»") else text


def _cut_lines(lines: list[str], room: int) -> list[str]:
    """*lines* cut to fit *room*. The shortest line takes what it needs first,
    and what it leaves goes to the next, so no text loses more than it must."""
    out = dict(enumerate(lines))
    left = room
    order = sorted(range(len(lines)), key=lambda i: len(lines[i]))
    for done, i in enumerate(order):
        share = left // (len(order) - done)
        if len(lines[i]) > share:
            out[i] = _cut_line(lines[i], share)
        left -= len(out[i])
    return [out[i] for i in range(len(lines))]


def card_body(note: str, rows: Iterable[tuple[str, str]]) -> CardBody:
    """The card body from *rows*, pairs of a key and its whole line.

    *note* is the fixed first text, and the rule of the module doc decides the
    rest. The body always fits in :data:`CARD_CLIP`. Raises :class:`CardWontFit`
    when the short fields alone do not fit.
    """
    pairs = list(rows)
    short = [line for key, line in pairs if key not in LONG_TEXT_KEYS]
    long_keys = [key for key, _line in pairs if key in LONG_TEXT_KEYS]
    long_lines = [line for key, line in pairs if key in LONG_TEXT_KEYS]
    room = CARD_CLIP - len(note) - 1
    whole = "\n".join([*short, *long_lines])
    if len(whole) <= room:
        return CardBody(f"{note}\n{whole}" if whole else note)
    head = "\n".join(short)
    newlines = len(short) + len(long_lines)  # one before each line, and the mark
    spare = room - len(head) - newlines - len(CUT_MARK)
    if not long_lines or spare < _LONG_TEXT_MIN * len(long_lines):
        wide = sorted(pairs, key=lambda pair: len(pair[1]), reverse=True)[:2]
        names = " and ".join(sorted({key for key, _line in wide}))
        raise CardWontFit(
            f"This change does not fit on one card, so nothing was changed. Shorten "
            f"{names}, or split the change, and call the tool once more."
        )
    kept = _cut_lines(long_lines, spare)
    cut = [key for key, was, now in zip(long_keys, long_lines, kept, strict=True) if was != now]
    text = "\n".join(line for line in (note, head, *kept, CUT_MARK) if line)
    return CardBody(text, cut, spare)


def is_cut(body: object) -> bool:
    """True when the card body *body* holds a cut."""
    return CUT_MARK in str(body)


def cut_refusal(body: object) -> str:
    """The answer of a held chat to a cut card, in plain words: what to shorten.

    A body that lost its :class:`CardBody` type on the way, to a ``+=``, names
    the text in general words.
    """
    cut = getattr(body, "cut", ()) or ("the long text",)
    room = int(getattr(body, "room", 0) or 0)
    size = f" to about {room} characters" if room else ""
    return (
        "This change does not fit on one card, so nothing was changed. This chat "
        "read email from outside senders, so a card here must show each word that "
        f"it writes. Shorten {' and '.join(cut)}{size}, or split the change, and "
        "call the tool once more."
    )
