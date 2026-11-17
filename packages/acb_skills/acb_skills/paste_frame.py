"""The split of a member's turn into the member's words and one pasted block (HRD-14a).

Spec: ``project-docs/specs/chat_failure_modes.md §4.7b (PR 2529)``, the row
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
   :data:`LEAD_MAX` characters, with no address, link or phone number.
7. The paste is the rest, to the end. A tail stays inside it, so ``tail`` is
   always empty (Q-HRD-14-2). The field keeps room for another answer.

:func:`frame_member_text` keeps the lead outside, puts
:meth:`UntrustedFrame.paste_note` right before the block, wraps the paste
in ONE block of the label :data:`PASTE_LABEL`, and runs ``neutralise`` on
the lead too. The marker lines come only from :class:`UntrustedFrame`.

The checks of an address, a link and a phone number are closed rules (spec
section 4.7b). Each one folds the line first (:func:`fold`). A form beyond
the fold is a named residual: `` (at) ``, ``[dot]``, an IP address as a host
with no scheme, and a number in words.

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

from acb_skills.untrusted import _BREAKS, UntrustedFrame, is_invisible, neutralise

__all__ = [
    "LEAD_MAX",
    "LINK_TLDS",
    "MIN_LINES",
    "PASTE_LABEL",
    "PathsExist",
    "WORD_TLDS",
    "fold",
    "frame_member_text",
    "has_address",
    "has_link",
    "has_phone",
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
#: ``lib/chatUpload.ts`` reads it.
_NOTE_RE = re.compile(
    rf"{_CLIP} Uploaded (\d+) file\(s\): ([^\n]+)\n\n"
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
    if not paths or len(paths) != int(m.group(1)):
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


# -- the address, link and phone rules (step 6) ---------------------------------

_IDEOGRAPHIC_STOP = chr(0x3002)


def _fold_case_kept(text: str) -> str:
    """:func:`fold` with no change of case."""
    norm = unicodedata.normalize("NFKC", str(text or ""))
    kept = "".join(ch for ch in norm if not is_invisible(ch))
    return kept.replace(_IDEOGRAPHIC_STOP, ".")


def fold(text: str) -> str:
    """*text* as the checks read it: NFKC, then no invisible character
    (``untrusted.is_invisible``), then the ideographic full stop as ``.``,
    then lower case. NFKC folds the full-width and the small at sign to
    ``@``, and the half-width full stop to the ideographic one."""
    return _fold_case_kept(text).lower()


#: The spelled address: `` at `` with `` dot ``.
_SPELLED_RE = re.compile(r"\w at [\w-]+(?: dot [\w-]+)+")

#: The top-level domains of :data:`LINK_TLDS` that are also a word or a word
#: end. A bare domain of one of them is a link only on the strict rule of
#: :data:`_WORD_DOMAIN_RE`, so ``Done.In the end`` is not a link.
WORD_TLDS = frozenset({"in", "ai", "me", "us", "app", "dev", "to"})

#: The closed list of top-level domains of a bare domain. A missed link in a
#: lead keeps a target outside the block, and a false hit costs one ask. So
#: the list leans wide. It holds no file end that a member often types, so
#: ``README.md``, ``app.py`` and ``it.it`` are not links: ``md``, ``py``,
#: ``it`` and ``is`` stay out. ``www.`` or a scheme is a link with any
#: top-level domain.
LINK_TLDS = frozenset({
    "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "io",
    "co", "xyz", "mobi", "asia",
    "uk", "ca", "au", "nz", "de", "fr", "nl", "es", "ch", "se", "dk", "fi",
    "ie", "eu", "ru", "cn", "jp", "kr", "sg", "hk", "tw", "ae", "sa", "za",
    "ng", "ke", "br", "mx", "ar", "cl", "pk", "bd", "lk", "np", "ph", "th",
    "vn", "tr", "il", "eg", "gr", "pt", "cz", "hu", "ro", "ua", "qa", "kw",
    *WORD_TLDS,
})
_LABEL = r"[^\W_](?:[\w-]*[^\W_])?"
_DOMAIN_RE = re.compile(
    rf"(?<![\w-]){_LABEL}(?:\.{_LABEL})*"
    rf"\.(?:{'|'.join(sorted(LINK_TLDS - WORD_TLDS))})(?![\w-])")
#: A bare domain of :data:`WORD_TLDS`. It reads the text with its case kept,
#: and the top-level domain must be in lower case, so ``Done.In`` is a
#: sentence break and not a link. The top-level domain must end the text, or
#: come before a character that is no letter, digit, ``_`` or ``-``. White
#: space counts only when no letter comes after it, so ``note.to Ravi`` is
#: not a link, and ``acme.in/pay``, ``acme.in?x``, ``acme.in.`` are.
_WORD_DOMAIN_RE = re.compile(
    rf"(?<![\w-]){_LABEL}(?:\.{_LABEL})*\.(?:{'|'.join(sorted(WORD_TLDS))})"
    r"(?=\Z|[^\w\s-]|\s(?!\s*[^\W\d_]))")
_SCHEME_RE = re.compile(r"[a-z][a-z0-9+.\-]*://")
_WWW_RE = re.compile(r"(?<![\w-])www\.")

#: A date never joins a phone number: yyyy-mm-dd or dd/mm/yyyy. The scan puts
#: a character that is no digit and no separator in its place.
_DATE_RE = re.compile(r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})(?!\d)")
#: The separators of a phone number: spaces, dashes, dots and brackets.
_SEP = r"[\s\-.()\[\]" + re.escape("".join(map(chr, (*range(0x2010, 0x2016), 0x2212)))) + "]"
_PHONE_RUN_RE = re.compile(rf"(\+)?{_SEP}*(\d(?:{_SEP}*\d)*)")


def has_address(text: str) -> bool:
    """True when the folded *text* holds an ``@``, or `` at `` with `` dot ``."""
    folded = fold(text)
    return "@" in folded or _SPELLED_RE.search(folded) is not None


def has_link(text: str) -> bool:
    """True when the folded *text* holds a scheme, ``www.``, or a bare domain
    whose top-level domain is in :data:`LINK_TLDS`. A top-level domain of
    :data:`WORD_TLDS` takes the strict rule of ``_WORD_DOMAIN_RE``."""
    folded = fold(text)
    if any(rx.search(folded) for rx in (_SCHEME_RE, _WWW_RE, _DOMAIN_RE)):
        return True
    return _WORD_DOMAIN_RE.search(_fold_case_kept(text)) is not None


def has_phone(text: str) -> bool:
    """True when the folded *text* holds a ``+`` and then 8 or more digits, or
    10 or more digits with a separator between them. A bare run of digits is
    not a phone number, and a date is never part of one."""
    masked = _DATE_RE.sub("#", fold(text))
    for m in _PHONE_RUN_RE.finditer(masked):
        core = m.group(2)
        digits = sum(ch.isdigit() for ch in core)
        if m.group(1) and digits >= 8:
            return True
        if digits >= 10 and not core.isdigit():
            return True
    return False


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
    """Step 6: one line of at most :data:`LEAD_MAX` characters that names no
    address, link or phone number. The caller already refused a part with a
    ``_PASTED`` line."""
    text = line.strip()
    return (len(text) <= LEAD_MAX
            and not has_address(text) and not has_link(text) and not has_phone(text))


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
    paste (Q-HRD-14-2). A proven upload note is in none of the three."""
    parts = _parts(text, paths_exist)
    return parts.lead, parts.paste, parts.tail


def frame_member_text(
    text: str, frame: UntrustedFrame, *, paths_exist: PathsExist = no_path_proven,
) -> str:
    """*text* with its paste in ONE block of :data:`PASTE_LABEL`.

    The lead stays outside, after ``neutralise``. The paste note goes right
    before the block, in the same text. A proven upload note goes after the
    END line, written again from its paths. With no block, the result is
    *text* byte for byte."""
    parts = _parts(text, paths_exist)
    if not parts.paste:
        return str(text or "")
    out: list[str] = []
    if parts.lead:
        out += [neutralise(parts.lead, frame.token), ""]
    out += [frame.paste_note(), frame.wrap(PASTE_LABEL, parts.paste)]
    if parts.paths:
        out += ["", neutralise(_upload_note(parts.paths), frame.token)]
    return "\n".join(out)
