"""The ONE rule of a hidden character in a text that an owner saves (WS-STU F1b).

Spec: ``project-docs/specs/ai_studio.md`` section 14, the F1b row (Metorite
task 1555). Fence: ``tests/unit/test_hidden_text.py``, with the shared cases
of ``tests/fixtures/hidden_text_cases.json``.

**Why.** A model reads a skill as instructions. A character that no person
sees can carry a second text into that prompt: a TAG character, a variation
selector, a zero-width space, a bidi control or a blank filler. So the skill
store REFUSES such a text, and it never strips it in silence. A strip would
change what the owner saved, and the ``content_hash`` of a version would no
longer be the hash of what the owner wrote. A chat read of an old row leaves
each such character out (:func:`strip_hidden`).

**The rule.** :func:`find_hidden` finds each character that
``reply_card.is_invisible_char`` calls invisible: the Unicode table
``Default_Ignorable_Code_Point``, U+2800, U+1D159 and each character of
category C. These exceptions apply:

* A new line, a tab and a carriage return are legal.
* U+200C (ZWNJ) and U+200D (ZWJ) are legal between two visible
  characters. A visible character is not white space and not hidden. A
  legal U+FE0E or U+FE0F counts as visible on the left. Persian, Hindi,
  Bengali and emoji sequences keep theirs.
* At a word end (the end of the text, or before a space, a new line or
  other white space that a person sees), ONE joiner is legal directly after
  a virama (canonical combining class 9). A legacy input writes a Malayalam
  chillu and a Bengali khanda ta as a virama and a ZWJ, and an explicit
  halant as a virama and a ZWNJ.
* Each other joiner is hidden: at the start, next to white space, next to a
  second joiner, or at a word end after any character but a virama. A ZWNJ
  before a space after a Persian letter is hidden too. The space already
  breaks the join, so that ZWNJ changes no shape and carries only data.
* U+FE0E and U+FE0F are legal only directly after ONE visible symbol: a
  character of category S or Po, or a keycap base (0 to 9, ``#`` or ``*``)
  with U+20E3 directly after the selector. Two in a row are hidden.

``reply_card.clean_reply_body`` is a different job. It cleans a mail body in
silence, and it keeps a joiner only between two pictographs, so it breaks
Hindi, Bengali and Persian. Do not use it for owner text.

**The residual risk.** A legal joiner between two letters still carries one
bit for each gap. The rule bounds it (no edge, no double, never next to white
space), but it cannot close it.

**A position** is 1-based and counts code points, as Python indexes a string.
A client that counts UTF-16 units must convert.

The module imports only the standard library at load. It reads
``reply_card`` at the first call, so a reader of this module does not load
the email card code first.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Iterator, Mapping
from typing import Any

__all__ = ["body_hidden_problem", "find_hidden", "hidden_problem", "strip_hidden"]

#: A new line, a tab and a carriage return. Never hidden.
_LEGAL_CONTROLS = frozenset((chr(0x0A), chr(0x09), chr(0x0D)))
#: U+200C ZERO WIDTH NON-JOINER and U+200D ZERO WIDTH JOINER.
_JOINERS = frozenset((chr(0x200C), chr(0x200D)))
#: U+FE0E and U+FE0F, the text and the emoji presentation selectors.
_SELECTORS = frozenset((chr(0xFE0E), chr(0xFE0F)))
#: U+20E3 COMBINING ENCLOSING KEYCAP, the end of a keycap emoji.
_KEYCAP = chr(0x20E3)
_KEYCAP_BASES = frozenset("0123456789#*")

#: A character that may be hidden: anything but printable ASCII and the
#: legal controls. A text with no such character holds no hidden character,
#: and one C scan says so.
_CANDIDATE = re.compile(
    "[^" + re.escape("".join(sorted(_LEGAL_CONTROLS))) + re.escape(" ") + "-~]")

#: A body key that a path can name as it is. Any other key shows as its
#: place in its object, so a path never echoes a strange key.
_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


@functools.lru_cache(maxsize=8192)
def _invisible(ch: str) -> bool:
    """True when ``reply_card.is_invisible_char`` says so, but not for a legal
    control. THE base test. The cache holds one answer for each character."""
    from acb_skills.reply_card import is_invisible_char

    return ch not in _LEGAL_CONTROLS and is_invisible_char(ch)


def _visible(ch: str) -> bool:
    """A character that a person sees: not white space and not hidden. A
    joiner and a selector are in the invisible table, so neither is visible."""
    return not ch.isspace() and not _invisible(ch)


def _selector_legal(text: str, i: int) -> bool:
    """True when the selector at *text[i]* follows ONE visible symbol, or a
    keycap base with U+20E3 next. A selector before it is not a symbol, so
    two in a row fail."""
    if i == 0:
        return False
    prev = text[i - 1]
    if _visible(prev) and unicodedata.category(prev)[0] == "S":
        return True
    if _visible(prev) and unicodedata.category(prev) == "Po":
        return True
    return prev in _KEYCAP_BASES and i + 1 < len(text) and text[i + 1] == _KEYCAP


def _word_end(text: str, i: int) -> bool:
    """True when *text[i]* is the last character, or the next one is white
    space that a person sees (a space, a new line, a tab, NBSP). A hidden
    control such as U+0085 is no word end."""
    if i + 1 >= len(text):
        return True
    nxt = text[i + 1]
    return nxt.isspace() and not _invisible(nxt)


def _joiner_legal(text: str, i: int) -> bool:
    """True when the joiner at *text[i]* sits between two visible characters,
    or directly after a virama at a word end.

    A legal selector counts as visible on the left. The right side cannot be
    a legal selector, because a selector after a joiner is never legal.

    The word end: Malayalam writes a chillu as a virama and a ZWJ, Bengali
    writes the khanda ta the same way, and Devanagari writes an explicit
    halant as a virama and a ZWNJ. Each of these can end a word, so ONE
    joiner directly after a virama (canonical combining class 9) is legal
    there. A second joiner follows a joiner, not a virama, so it fails."""
    if i == 0:
        return False
    prev = text[i - 1]
    if _word_end(text, i):
        return _visible(prev) and unicodedata.combining(prev) == 9
    left = _visible(prev) or (prev in _SELECTORS and _selector_legal(text, i - 1))
    return left and _visible(text[i + 1])


def _hits(text: str) -> Iterator[tuple[int, str]]:
    """Each hidden character of *text*, in order, as (1-based position, char)."""
    first = _CANDIDATE.search(text)
    if first is None:
        return
    for i in range(first.start(), len(text)):
        ch = text[i]
        if ch in _SELECTORS:
            if not _selector_legal(text, i):
                yield i + 1, ch
        elif ch in _JOINERS:
            if not _joiner_legal(text, i):
                yield i + 1, ch
        elif _invisible(ch):
            yield i + 1, ch


def find_hidden(text: str) -> list[tuple[int, str]]:
    """Each hidden character of *text*, as (position, character). The position
    is 1-based and counts code points. A value that is not a string has none."""
    if not isinstance(text, str):
        return []
    return list(_hits(text))


def strip_hidden(text: str) -> str:
    """*text* with each character that :func:`find_hidden` finds removed, and
    nothing else. A legal joiner or selector stays. The result holds no hidden
    character: a legal joiner or selector never sits next to a hidden one, so
    no removal changes the context of a character that stays."""
    if not isinstance(text, str):
        return text
    found = {at - 1 for at, _ in _hits(text)}
    if not found:
        return text
    return "".join(ch for i, ch in enumerate(text) if i not in found)


def hidden_problem(value: object, path: str) -> str | None:
    """THE one refusal of a hidden character, or None.

    The sentence is "PATH: This text holds a hidden character at position N
    (U+CODE). Remove it." It names the first hidden character only. It never
    echoes the text. The store, the routes and the chat tools all call it, so
    the sentence of a chat refusal equals the sentence of the route."""
    if not isinstance(value, str):
        return None
    hit = next(_hits(value), None)
    if hit is None:
        return None
    at, ch = hit
    return (f"{path}: This text holds a hidden character at position {at} "
            f"(U+{ord(ch):04X}). Remove it.")


def _child_path(path: str, key: object, place: int) -> str:
    """The path of the value of *key*, the key at *place* (1-based) of its
    object. A plain key shows as it is, and any other key by its place."""
    if isinstance(key, str) and _PLAIN_KEY.fullmatch(key):
        return f"{path}.{key}"
    return f"{path}[key {place}]"


def body_hidden_problem(body: object) -> str | None:
    """The refusal of the first hidden character in a skill body, or None.

    The walk reads each string and each key, at any depth, in the order of the
    body. A key goes before its value. The path starts at ``body``. A list
    item shows as ``[i]`` (0-based), and a value under a plain key as
    ``.key``. A hit IN a key names the key by its place: ``body.cards[0]
    key 2`` is the second key of that object. The walk is iterative, so no
    depth can overflow the stack. A caller passes a body with no cycle: the
    store dumps the body to JSON first, and JSON refuses a cycle."""
    stack: list[tuple[str, Any]] = [("body", body)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, str):
            problem = hidden_problem(value, path)
            if problem is not None:
                return problem
        elif isinstance(value, Mapping):
            todo: list[tuple[str, Any]] = []
            for place, (key, child) in enumerate(value.items(), start=1):
                if isinstance(key, str):
                    todo.append((f"{path} key {place}", key))
                todo.append((_child_path(path, key, place), child))
            stack.extend(reversed(todo))
        elif isinstance(value, list | tuple):
            stack.extend(reversed([(f"{path}[{i}]", item) for i, item in enumerate(value)]))
    return None
