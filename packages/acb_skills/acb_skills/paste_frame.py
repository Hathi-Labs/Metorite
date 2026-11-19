"""The split of a member's turn into the member's words and one pasted block (HRD-14a).

Spec: ``project-docs/specs/chat_failure_modes.md`` section 4.7b (PR 2529), the row
HRD-14a of its section 6, and the questions Q-HRD-14-1 to Q-HRD-14-12 of its
section 8. D-HRD-5 (D217) decides the trigger: a part of a member turn that
holds 8 or more lines that are not blank holds a block. Fence:
``tests/unit/test_paste_frame.py``.

This module is PURE, and nothing calls it yet. HRD-14b adds the seam, dark
behind its own flag. So this slice changes no chat.

:func:`split` reads ONE part and returns ``(lead, paste, tail)``:

1. An upload note at the end (the shape of ``uploadNote`` in
   ``lib/chatUpload.ts``) comes off only when ``paths_exist`` proves each of
   its paths. Then it does not count, and :func:`frame_member_text` writes it
   again after the block. Else it stays in the text and counts.
2. Each line break of ``untrusted._BREAKS`` becomes LF. A CR LF pair is one.
3. With fewer than :data:`MIN_LINES` lines that are not blank, nothing is
   framed: the lead is the whole part, as it came, and the paste is empty.
4. A part with any ``_PASTED`` line (``acb_common.assistant_profile``, the
   rule of ``typed_text``) has no lead.
5. The text splits into paragraphs at the blank lines.
6. The FIRST paragraph is the lead only when it is ONE line of at most
   :data:`LEAD_MAX` characters that passes the ALLOWLIST (:func:`_lead_ok`).
7. The paste is the rest, to the end. A tail stays inside it, so ``tail`` is
   always empty (Q-HRD-14-2). The field keeps room for another answer.

:func:`frame_member_text` keeps the lead outside, puts
:meth:`UntrustedFrame.paste_note` right before the block, wraps the paste
in ONE block of the label :data:`PASTE_LABEL`, and runs ``neutralise`` on
the lead too. The marker lines come only from :class:`UntrustedFrame`.

The lead rule is a positive ALLOWLIST (spec section 4.7b, rounds 6 and 7). Each
character of the lead must be a letter, a mark, a decimal digit, a plain
space, or one of a few signs of punctuation, and no letter of
:data:`_DOT_LETTERS`. A sentence period or colon may stand only before a
space or at the end. The lead holds at most :data:`_DIGITS_MAX` decimal
digits, and it spells no address. The rule is a fail-closed filter on SIGNS:
it needs no list of domains, schemes, slashes or phone forms. It does not
claim to catch every link, address or phone number, and the spec names the
residual forms. The cost: any other sign, an emoji, a tab, a full-width sign
and ``Done.In`` send the lead to the block, and the model asks once.

The caller owns two cases. *text* must be a ``str``: the module reads any
other value with ``str()``, so bytes give their ``repr``. A ``paths_exist``
that raises passes its error up. HRD-14b must fail closed: on an error, it
frames the whole part as one block.

WARNING: the frame is advice to the model, and no test can prove that a model
obeys it. The fence proves where the lead and the block are.

The source of this module holds only ASCII.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import NamedTuple

from acb_common.assistant_profile import _PASTED

from acb_skills.reply_card import is_invisible_char
from acb_skills.untrusted import _BREAKS, UntrustedFrame, neutralise

__all__ = [
    "LEAD_MAX",
    "MIN_LINES",
    "PASTE_LABEL",
    "PathsExist",
    "frame_member_text",
    "no_path_proven",
    "split",
]

#: The label of the block of a paste.
PASTE_LABEL = "PASTED TEXT"
#: A part with this many lines that are not blank holds a block (D-HRD-5).
MIN_LINES = 8
#: The most characters that the lead line may hold (Q-HRD-14-2).
LEAD_MAX = 200

#: The proof of an upload note. It gets each path of the note, in order, and
#: proves the note only when it returns ``True`` itself: each path is a file in
#: the thread folder of the run. HRD-14b supplies the real one.
PathsExist = Callable[[tuple[str, ...]], bool]


def no_path_proven(paths: tuple[str, ...]) -> bool:
    """The default proof: no path is proven, so an upload note stays in the
    text, counts as a line, and goes inside the block."""
    return False


# -- step 1: the upload note ----------------------------------------------------

_CLIP = chr(0x1F4CE)
_NOTE_HEAD = f"{_CLIP} Uploaded "
#: The note of ``uploadNote`` from its header to the end, as ``NOTE_RE`` of
#: ``lib/chatUpload.ts`` reads it. The count is 1 to 6 ASCII digits, and the
#: code compares it as text: a sender writes this text, and ``int()`` of more
#: than 4,300 digits raises.
_NOTE_RE = re.compile(
    rf"{_CLIP} Uploaded ([0-9]{{1,6}}) file\(s\): ([^\n]+)\n\n"
    r"Paths: (`[^`\n]+`(?:, `[^`\n]+`)*)\n\n"
    r"Read them with read_attachment\.\Z")
_PATH_RE = re.compile(r"`([^`\n]+)`")


def _note_paths(text: str) -> tuple[str, tuple[str, ...]] | None:
    """``(words, paths)`` when *text* ends with a whole upload note, else
    None. The checks of ``parseUploadNote``: the count equals the paths, and
    the names equal the ends of the paths."""
    at = text.rfind("\n\n" + _NOTE_HEAD)
    if at >= 0:
        words, note = text[:at], text[at + 2:]
    elif text.startswith(_NOTE_HEAD):
        words, note = "", text
    else:
        return None
    m = _NOTE_RE.match(note)
    if m is None:
        return None
    paths = tuple(_PATH_RE.findall(m.group(3)))
    if not paths or m.group(1) != str(len(paths)):
        return None
    if ", ".join(p[p.rfind("/") + 1:] for p in paths) != m.group(2):
        return None
    return words, paths


def _upload_note(paths: tuple[str, ...]) -> str:
    """The note of ``uploadNote``, written again from its proven paths."""
    names = ", ".join(p[p.rfind("/") + 1:] for p in paths)
    ticks = ", ".join(f"`{p}`" for p in paths)
    return (f"{_NOTE_HEAD}{len(paths)} file(s): {names}\n\n"
            f"Paths: {ticks}\n\nRead them with read_attachment.")


# -- the lead rules (step 6) ------------------------------------------------------

#: The punctuation that a lead may hold anywhere: the comma, ``!``, ``?``,
#: ``;``, the straight and curly quotes, the brackets, the hyphen-minus, the
#: en and em dashes, the ideographic comma (U+3001), and the Arabic comma,
#: semicolon and question mark (U+060C, U+061B, U+061F). No browser reads one
#: of them as a dot or a slash. Each other sign of category P or S sends the
#: lead to the block.
_PUNCT = frozenset(",!?;'\"()-" + "".join(map(chr, (0x2018, 0x2019, 0x201C, 0x201D,
                                                     0x2013, 0x2014, 0x3001, 0x060C,
                                                     0x061B, 0x061F))))
#: The punctuation that a lead may hold only before a space or at its end: a
#: sentence period, a colon, the Devanagari danda and double danda (U+0964,
#: U+0965) and the ideographic full stop (U+3002). So ``acme.com``,
#: ``https:`` + ``//`` and ``evil`` + U+3002 + ``com`` fail. No other sign
#: that looks like a dot is on a list: U+FF0E, U+FF61 and U+2024 go to the
#: block.
_END_PUNCT = frozenset(".:" + "".join(map(chr, (0x0964, 0x0965, 0x3002))))
#: The letters, marks and digits (categories L, M, Nd) that look like a dot
#: or a colon. A lead that holds one goes to the block. The list is a lower
#: bound, not a proof: another letter, mark or digit that looks like a dot or
#: a colon is a named residual of the spec. It holds the Latin sinological
#: dot, the Canadian syllabics final middle dot, the Arabic-Indic zeros, a few
#: modifier and tone letters, the Hangul araea, the Arabic baseline round dot,
#: a Duployan dot, the Devanagari high spacing dot, the Hangul single dot tone
#: mark, the musical augmentation dot, and the visarga of the Indic scripts and
#: of Myanmar.
_DOT_LETTERS = frozenset(map(chr, (
    0xA78F, 0x1427, 0x0660, 0x06F0, 0xA4F8, 0x318D, 0x18DF, 0x0887, 0x1BC94,
    0x0971, 0x302E, 0x1D16D, 0xA718, 0x02D0, 0x02D1, 0xA4FD,
    0x0903, 0x0983, 0x0A03, 0x0A83, 0x0B03, 0x0B83, 0x0C03, 0x0C83, 0x0D03,
    0x0D83, 0x1038,
)))
#: A lead may hold at most this many decimal digits, of any script. A phone
#: number, a long order number and a date have more, so they need no rule of
#: their own.
_DIGITS_MAX = 7
#: The longest scan copy that the spelled rule reads. NFKC can make a copy
#: longer than its lead. A longer copy counts as a hit, which is the safe side.
_SCAN_MAX = 2_000
#: The spelled address of the spec: `` at ``, then one to three words, then
#: `` dot `` and a word, or ``dotcom``. It reads the scan copy with each sign
#: as a space and each run of white space as one space. So ``'at'``,
#: ``-at-``, ``(at)`` and ``dot,`` read as the words. ``(dot)`` alone is a hit
#: too: ``hdfc-verify(dot)com``.
_SPELLED_RE = re.compile(r"(?:^|\s)at\s\S+(?:\s\S+){0,2}?\s(?:dot\s\w|dotcom\b)")
_PAREN_DOT_RE = re.compile(r"\(\s*dot\s*\)")
#: A sign: a character that is neither a word character nor white space.
_SIGN_RE = re.compile(r"[^\w\s]")


def _hidden(ch: str) -> bool:
    """True for a character that a reader cannot see:
    ``reply_card.is_invisible_char``, the ONE wide rule. The allowlist
    refuses most of them by category. This check stays, because some are
    letters or marks: a variation selector, the combining grapheme joiner, a
    Hangul filler. ``neutralise`` keeps each one, so a lead that holds one
    goes into the block."""
    return is_invisible_char(ch)


def _allowed(text: str, at: int) -> bool:
    """True when the character at *at* may stand in a lead: a letter (L), a
    mark (M), a decimal digit (Nd), a plain space, a sign of :data:`_PUNCT`,
    or a sign of :data:`_END_PUNCT` before a space or at the end."""
    ch = text[at]
    if ch == " " or ch in _PUNCT:
        return True
    if ch in _END_PUNCT:
        return at + 1 == len(text) or text[at + 1] == " "
    category = unicodedata.category(ch)
    return (category[0] in "LM" or category == "Nd") and ch not in _DOT_LETTERS


def _scan(text: str) -> str | None:
    """The scan copy of *text* for the spelled rule: NFKD, no hidden character
    and no combining mark (Mn, Me), NFKC, lower case, and each run of white
    space as one space. So a letter that folds, such as U+00AA, reads as its
    plain letter. None when the copy is longer than :data:`_SCAN_MAX`."""
    parts = "".join(c for c in unicodedata.normalize("NFKD", str(text or ""))
                    if not _hidden(c) and unicodedata.category(c) not in ("Mn", "Me"))
    copy = unicodedata.normalize("NFKC", parts).lower()
    if len(copy) > _SCAN_MAX:
        return None
    return " ".join(copy.split())


def _spelled(text: str) -> bool:
    """True when the scan copy of *text* spells an address: `` at `` and then
    `` dot `` (:data:`_SPELLED_RE`), or ``(dot)``. Each sign of the copy
    reads as a space first. A copy over the cap counts as one."""
    copy = _scan(text)
    if copy is None or _PAREN_DOT_RE.search(copy):
        return True
    return _SPELLED_RE.search(" ".join(_SIGN_RE.sub(" ", copy).split())) is not None


# -- the split -------------------------------------------------------------------

_BREAK_RE = re.compile(rf"\r\n|[{_BREAKS}]")


class _Parts(NamedTuple):
    lead: str
    paste: str
    tail: str
    paths: tuple[str, ...]


def _blank(line: str) -> bool:
    return not line.strip()


def _lead_ok(line: str) -> bool:
    """Step 6, the allowlist. The caller already refused a part with a
    ``_PASTED`` line. The lead stays outside the block only when ALL hold:

    1. it holds at most :data:`LEAD_MAX` characters;
    2. each character passes :func:`_allowed`, and none is :func:`_hidden`;
    3. it holds at most :data:`_DIGITS_MAX` decimal digits;
    4. its scan copy spells no address (:func:`_spelled`).

    The rule fails closed: a sign that it does not know sends the lead to the
    block."""
    text = line.strip()
    return (len(text) <= LEAD_MAX
            and all(_allowed(text, at) and not _hidden(text[at]) for at in range(len(text)))
            and sum(unicodedata.category(ch) == "Nd" for ch in text) <= _DIGITS_MAX
            and not _spelled(text))


def _parts(text: str, paths_exist: PathsExist) -> _Parts:
    raw = str(text or "")
    words, paths = raw, ()
    noted = _note_paths(raw)
    if noted is not None and paths_exist(noted[1]) is True:
        words, paths = noted
    lines = _BREAK_RE.sub("\n", words).split("\n")
    if sum(not _blank(line) for line in lines) < MIN_LINES:
        return _Parts(raw, "", "", ())
    while _blank(lines[-1]):
        lines.pop()
    start = next(i for i, line in enumerate(lines) if not _blank(line))
    lines = lines[start:]
    if _PASTED.search("\n".join(lines)):
        return _Parts("", "\n".join(lines), "", paths)
    end = next((i for i, line in enumerate(lines) if _blank(line)), len(lines))
    if end != 1 or not _lead_ok(lines[0]):
        return _Parts("", "\n".join(lines), "", paths)
    rest = next(i for i in range(1, len(lines)) if not _blank(lines[i]))
    return _Parts(lines[0].strip(), "\n".join(lines[rest:]), "", paths)


def split(text: str, paths_exist: PathsExist = no_path_proven) -> tuple[str, str, str]:
    """``(lead, paste, tail)`` of ONE part of a member turn (spec section
    4.7b, the seven steps).

    With no block, the lead is *text* as it came and the paste is empty. With
    a block, the lead is one line of the member's words or empty, the paste
    is the block, and the tail is always empty: a tail stays inside the
    paste (Q-HRD-14-2). A proven upload note is in none of the three.

    ``split`` has no token. :func:`frame_member_text` applies one more rule
    with the token of the run: a lead that ``neutralise`` would change goes
    into the block."""
    parts = _parts(text, paths_exist)
    return parts.lead, parts.paste, parts.tail


def frame_member_text(
    text: str, frame: UntrustedFrame, *, paths_exist: PathsExist = no_path_proven,
) -> str:
    """*text* with its paste in ONE block of :data:`PASTE_LABEL`.

    The lead stays outside only when ``neutralise`` leaves it as it is. The
    rules checked the raw lead, and ``neutralise`` removes each copy of the
    token, so a changed lead could read differently after the check. Such a
    lead goes into the block, which fails closed. The paste note goes right
    before the block, in the same text. A proven upload note goes after the
    END line, written again from its paths. With no block, the result is
    *text* byte for byte."""
    parts = _parts(text, paths_exist)
    if not parts.paste:
        return str(text or "")
    lead, paste = parts.lead, parts.paste
    if lead and neutralise(lead, frame.token) != lead:
        lead, paste = "", f"{lead}\n\n{paste}"
    out: list[str] = []
    if lead:
        out += [lead, ""]
    out += [frame.paste_note(), frame.wrap(PASTE_LABEL, paste)]
    if parts.paths:
        out += ["", neutralise(_upload_note(parts.paths), frame.token)]
    return "\n".join(out)
