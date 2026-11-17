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
   :data:`LEAD_MAX` characters, with no hidden character, address, link or
   phone number. A hidden character is one of
   ``reply_card.is_invisible_char``, the one wide rule of the repo, because
   ``neutralise`` keeps it in the lead.
7. The paste is the rest, to the end. A tail stays inside it, so ``tail`` is
   always empty (Q-HRD-14-2). The field keeps room for another answer.

:func:`frame_member_text` keeps the lead outside, puts
:meth:`UntrustedFrame.paste_note` right before the block, wraps the paste
in ONE block of the label :data:`PASTE_LABEL`, and runs ``neutralise`` on
the lead too. The marker lines come only from :class:`UntrustedFrame`.

The checks of an address, a link and a phone number are closed rules (spec
section 4.7b). Each one reads a scan copy of the line first
(:func:`_scan_text`). A form beyond that copy is a named residual: `` (at) ``,
``[dot]``, an IP address as a host with no scheme, a number in words, and a
Cyrillic or Greek look-alike letter. The spec also names these residuals of
a link: a word-like domain with a plain label before a space and a word
(``Pay at acme.in now``, ``Thanks.to all``), a word-like domain in capitals
with no path, and a top-level domain that is not on the list.

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
    "LINK_TLDS",
    "MIN_LINES",
    "PASTE_LABEL",
    "WORD_TLDS",
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

#: The signs that the scan copy reads as ``.``: the ideographic full stop
#: (U+3002), the Arabic full stop (U+06D4) and the Syriac supralinear full
#: stop (U+0701). NFKC already folds the one dot leader (U+2024) and the small
#: full stop (U+FE52) to ``.``, and the half-width full stop to U+3002.
_DOTS = str.maketrans({chr(0x3002): ".", chr(0x06D4): ".", chr(0x0701): "."})
#: A dot in brackets, as a writer hides a link: ``acme[.]com``.
_BRACKET_DOT_RE = re.compile(r"\[\.\]|\(\.\)|\{\.\}")
#: The longest text that a rule reads. The split gives a rule only one lead
#: line of at most :data:`LEAD_MAX` characters. A longer text counts as a hit,
#: so each rule stays linear and fails on the safe side.
_SCAN_MAX = 2_000


def _hidden(ch: str) -> bool:
    """True for a character that a reader cannot see. It is
    ``reply_card.is_invisible_char``, the ONE wide rule: category C, the
    zero-width and bidi marks, the Hangul fillers, U+2800, U+1D159, the
    variation selectors and the TAG block. It needs no second table: the
    variation selectors (category Mn) are in its ranges. ``neutralise`` keeps
    each such character, so a lead that holds one goes into the block."""
    return is_invisible_char(ch)


def _scan_text_case_kept(text: str) -> str:
    """:func:`_scan_text` with no change of case."""
    raw = "".join(ch for ch in str(text or "") if not _hidden(ch))
    norm = unicodedata.normalize("NFKC", raw)
    kept = "".join(ch for ch in norm
                   if not _hidden(ch) and unicodedata.category(ch) not in ("Mn", "Me"))
    return _BRACKET_DOT_RE.sub(".", kept.translate(_DOTS))


def _scan_text(text: str) -> str:
    """*text* as the rules read it: no hidden character, NFKC, again no
    hidden character, no combining mark (Mn, Me), the signs of :data:`_DOTS`
    and a dot in brackets as ``.``, then lower case. NFKC folds the
    full-width and the small at sign to ``@``. The copy is only for the
    rules: the lead keeps its own text."""
    return _scan_text_case_kept(text).lower()


#: The spelled address of the spec: `` at `` and later `` dot `` and a word.
#: It reads the scan copy with each run of white space as one space. Up to
#: three words may stand between ``at`` and ``dot``, and a word may hold dots,
#: so ``ravi at mail.acme dot test`` and ``(ravi) at acme dot test`` are
#: addresses. ``dotcom`` counts as `` dot com``.
_SPELLED_RE = re.compile(r"(?:^|\s)at\s\S+(?:\s\S+){0,2}?\s(?:dot\s\w|dotcom\b)")

#: The top-level domains of :data:`LINK_TLDS` that are also a word or a word
#: end. A bare domain of one of them takes the strict end rule of
#: :func:`_word_domain`, so ``Done.In the end`` and ``note.to Ravi`` are not
#: links.
WORD_TLDS = frozenset({
    "in", "ai", "me", "us", "app", "dev", "to", "be", "top", "online", "shop",
    "click", "link", "site", "store", "zip",
})

#: The closed list of top-level domains of a bare domain. A missed link in a
#: lead keeps a target outside the block, and a false hit costs one ask. So
#: the list leans wide, and it holds the domains that short links and abuse
#: use. It holds no file end that a member often types, so ``README.md``,
#: ``app.py``, ``run.sh`` and ``it.it`` are not links: ``md``, ``py``, ``it``,
#: ``is`` and ``sh`` stay out. ``www.`` or a scheme is a link with any
#: top-level domain.
LINK_TLDS = frozenset({
    "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "io",
    "co", "xyz", "mobi", "asia",
    "uk", "ca", "au", "nz", "de", "fr", "nl", "es", "ch", "se", "dk", "fi",
    "ie", "eu", "ru", "cn", "jp", "kr", "sg", "hk", "tw", "ae", "sa", "za",
    "ng", "ke", "br", "mx", "ar", "cl", "pk", "bd", "lk", "np", "ph", "th",
    "vn", "tr", "il", "eg", "gr", "pt", "cz", "hu", "ro", "ua", "qa", "kw",
    "tk", "ly", "gl", "ml", "ga", "cf", "gq", "cc",
    *WORD_TLDS,
})
_LABEL = r"[^\W_](?:[\w-]*[^\W_])?"
_DOMAIN_RE = re.compile(
    rf"(?<![\w-]){_LABEL}(?:\.{_LABEL})*"
    rf"\.(?:{'|'.join(sorted(LINK_TLDS - WORD_TLDS))})(?![\w-])")
_WORD_DOMAIN_RE = re.compile(
    rf"(?<![\w-])(?:{_LABEL}\.)*({_LABEL})\.((?i:{'|'.join(sorted(WORD_TLDS))}))(?![\w-])")
#: A path, a query, a fragment or a port. After a domain it is never the end
#: of a sentence, so the domain is a link in any case.
_PATH_AFTER = frozenset("/?#:")
#: The word-like domains that are seldom a word after a dot. Before a space
#: and a word, one of them is a link when its last label holds
#: :data:`_WORD_LABEL_MIN` or more characters, in any case: ``Payroll.ai and``.
#: ``to``, ``be`` and the rest are sentence words, so ``Thanks.to all`` is not.
_STRONG_TLDS = frozenset({"ai", "in", "app", "dev", "me", "us"})
#: The shortest last label of a strong word-like domain that is a link before
#: a space and a word. ``payroll.ai and`` is a link, and ``acme.in now`` is not.
_WORD_LABEL_MIN = 5
_SCHEME_RE = re.compile(r"[a-z][a-z0-9+.\-]*://")
_WWW_RE = re.compile(r"(?<![\w-])www\.")

#: A date never joins a phone number: yyyy-mm-dd or dd/mm/yyyy, with a month
#: of 01 to 12 and a day of 01 to 31. The scan puts a character that is no
#: digit and no separator in its place. A digit run right after a ``+`` or a
#: digit is not a date, and neither is ``2079-46-09``.
_MONTH = r"(?:0[1-9]|1[0-2])"
_DAY = r"(?:0[1-9]|[12]\d|3[01])"
_DATE_RE = re.compile(
    rf"(?<![\d+])(?:\d{{4}}-{_MONTH}-{_DAY}|{_DAY}/{_MONTH}/\d{{4}})(?!\d)")
#: The separators of a phone number: spaces, dashes, dots and brackets.
_SEP = r"[\s\-.()\[\]" + re.escape("".join(map(chr, (*range(0x2010, 0x2016), 0x2212)))) + "]"
_PHONE_RUN_RE = re.compile(rf"(\+)?{_SEP}*(\d(?:{_SEP}*\d)*)")


def _has_address(text: str) -> bool:
    """True when the scan copy of *text* holds an ``@``, or `` at `` and
    later `` dot ``. A text over :data:`_SCAN_MAX` counts as one."""
    if len(text) > _SCAN_MAX:
        return True
    scan = " ".join(_scan_text(text).split())
    return "@" in scan or _SPELLED_RE.search(scan) is not None


def _word_domain(text: str) -> bool:
    """True when *text* (case kept) holds a bare domain of :data:`WORD_TLDS`
    that is a link. A path, a query, a fragment or a port after it makes a
    link in any case. Else the top-level domain must be in lower case, so
    ``Done.In`` is no link. Then it is a link at the end of the text, or
    before a sign that is no letter. Before a space and a word, it is a link
    when its last label holds a digit or a dash, when it has two or more
    labels, or when it is one of :data:`_STRONG_TLDS` and its last label
    holds :data:`_WORD_LABEL_MIN` or more characters. The case of the label
    does not count."""
    for m in _WORD_DOMAIN_RE.finditer(text):
        after = text[m.end():]
        if after and after[0] in _PATH_AFTER:
            return True
        tld = m.group(2)
        if not tld.islower():
            continue
        if not after or not after[0].isspace():
            return True
        rest = after.lstrip()
        if not rest or not rest[0].isalpha():
            return True
        label = m.group(1)
        if any(ch.isdigit() or ch == "-" for ch in label) or m.group(0).count(".") >= 2:
            return True
        if tld in _STRONG_TLDS and len(label) >= _WORD_LABEL_MIN:
            return True
    return False


def _has_link(text: str) -> bool:
    """True when the scan copy of *text* holds a scheme, ``www.``, or a bare
    domain whose top-level domain is in :data:`LINK_TLDS`. A top-level domain
    of :data:`WORD_TLDS` takes the rule of :func:`_word_domain`. A text over
    :data:`_SCAN_MAX` counts as one."""
    if len(text) > _SCAN_MAX:
        return True
    scan = _scan_text(text)
    if any(rx.search(scan) for rx in (_SCHEME_RE, _WWW_RE, _DOMAIN_RE)):
        return True
    return _word_domain(_scan_text_case_kept(text))


def _has_phone(text: str) -> bool:
    """True when the scan copy of *text* holds a ``+`` and then 8 or more
    digits, or 10 or more digits with a separator between them. A bare run
    of digits is not a phone number, and a date is never part of one. A text
    over :data:`_SCAN_MAX` counts as one."""
    if len(text) > _SCAN_MAX:
        return True
    for m in _PHONE_RUN_RE.finditer(_DATE_RE.sub("#", _scan_text(text))):
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
    """Step 6: one line of at most :data:`LEAD_MAX` characters that holds no
    hidden character and names no address, link or phone number. The caller
    already refused a part with a ``_PASTED`` line."""
    text = line.strip()
    return (len(text) <= LEAD_MAX and not any(_hidden(ch) for ch in text)
            and not _has_address(text) and not _has_link(text) and not _has_phone(text))


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
