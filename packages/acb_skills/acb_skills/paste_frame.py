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
(:func:`_scan`). A form beyond that copy is a named residual: an IP address
as a host with no scheme, a number in words, and a Cyrillic or Greek
look-alike letter. The spec also
names these residuals of a link: a word-like domain with a plain label before
a space and a word (``Pay at acme.in now``, ``Thanks.to all``), a word-like
domain in capitals with no path, and a top-level domain that is not on the
list. It names ``+91 9845-01-23 45`` as a residual of a phone number.

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


# -- the address, link and phone rules (step 6) ---------------------------------

#: The signs that the scan copy reads as ``.``: the ideographic full stop
#: (U+3002), the Arabic full stop (U+06D4), the Syriac full stops (U+0701,
#: U+0702), the Armenian full stop (U+0589), the middle dots (U+00B7, U+30FB),
#: the hyphenation point (U+2027) and the dot and bullet operators (U+22C5,
#: U+2219). NFKC already folds the one dot leader (U+2024) and the small full
#: stop (U+FE52) to ``.``. The copy also reads ``_`` as a space, because an
#: underscore is no part of a domain or an address word.
_DOTS = str.maketrans({
    **{chr(code): "." for code in (0x3002, 0x06D4, 0x0701, 0x0702, 0x0589,
                                   0x00B7, 0x30FB, 0x2027, 0x22C5, 0x2219)},
    "_": " ",
})
#: A dot in brackets, as a writer hides a link: ``acme[.]com``.
_BRACKET_DOT_RE = re.compile(r"\[\.\]|\(\.\)|\{\.\}")
#: ``at`` or ``dot`` in brackets, as a writer hides an address:
#: ``ravi [at] acme [dot] test`` and ``ravi (at acme dot test)``.
_BRACKET_WORD_RE = re.compile(r"[\[\(\{]\s*(at|dot)(?:\s*[\]\)\}]|\s)")
#: The longest text that a rule reads, before and after the scan copy. The
#: split gives a rule one lead line of at most :data:`LEAD_MAX` characters,
#: and NFKC can make the copy longer. A longer text or copy counts as a hit,
#: which is the safe side. Each rule reads the copy in linear time.
_SCAN_MAX = 2_000
#: The categories of a character whose NFKC form stays joined to its
#: neighbours: a letter or a decimal digit, such as a full-width ``a``. Any
#: other character whose NFKC form holds a letter or a digit, such as ``TM``
#: or a superscript ``2``, gets a space on each side, so it cannot hide the
#: end of a domain.
_JOINED = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo", "Nd"})


def _hidden(ch: str) -> bool:
    """True for a character that a reader cannot see. It is
    ``reply_card.is_invisible_char``, the ONE wide rule: category C, the
    zero-width and bidi marks, the Hangul fillers, U+2800, U+1D159, the
    variation selectors and the TAG block. It needs no second table: the
    variation selectors (category Mn) are in its ranges. ``neutralise`` keeps
    each such character, so a lead that holds one goes into the block."""
    return is_invisible_char(ch)


def _scan_char(ch: str) -> str:
    """One character of the scan copy. NFKD first, so a combining mark comes
    apart from its letter. Then each hidden character and each combining mark
    (Mn, Me) goes, and NFKC folds the rest."""
    parts = "".join(c for c in unicodedata.normalize("NFKD", ch)
                    if not _hidden(c) and unicodedata.category(c) not in ("Mn", "Me"))
    out = unicodedata.normalize("NFKC", parts)
    if unicodedata.category(ch) not in _JOINED and any(c.isalnum() for c in out):
        return f" {out} "
    return out


def _scan(text: str) -> tuple[str, str] | None:
    """``(kept, lower)``: the scan copy of *text* with its case kept, and in
    lower case. None when *text* or its copy is longer than
    :data:`_SCAN_MAX`, so the caller counts a hit. The copy is only for the
    rules: the lead keeps its own text."""
    raw = str(text or "")
    if len(raw) > _SCAN_MAX:
        return None
    kept = "".join(_scan_char(ch) for ch in raw if not _hidden(ch))
    kept = _BRACKET_DOT_RE.sub(".", kept.translate(_DOTS))
    if len(kept) > _SCAN_MAX:
        return None
    return kept, kept.lower()


#: The spelled address of the spec: `` at `` and later `` dot `` and a word.
#: It reads the scan copy with each run of white space as one space. Up to
#: three words may stand between ``at`` and ``dot``, and a word may hold dots,
#: so ``ravi at mail.acme dot test`` and ``(ravi) at acme dot test`` are
#: addresses. ``dotcom`` counts as `` dot com``.
_SPELLED_RE = re.compile(r"(?:^|\s)at\s\S+(?:\s\S+){0,2}?\s(?:dot\s\w|dotcom\b)")

#: The top-level domains of :data:`LINK_TLDS` that are also a word or a word
#: end. A bare domain of one of them takes the end rule of :func:`_word_tld`,
#: so ``Done.In the end`` and ``note.to Ravi`` are not links.
WORD_TLDS = frozenset({
    "in", "ai", "me", "us", "app", "dev", "to", "be", "top", "online", "shop",
    "click", "link", "site", "store", "zip",
})

#: The closed list of top-level domains of a bare domain. A missed link in a
#: lead keeps a target outside the block, and a false hit costs one ask. So
#: the list leans wide, and it holds the domains that short links and abuse
#: use. It leaves out the file ends that a member types most often in a lead:
#: ``md``, ``py``, ``it``, ``is``, ``sh``, ``js``, ``ts`` and ``rs``. So
#: ``README.md``, ``app.py`` and ``run.sh`` are not links. Some file ends are
#: on the list on purpose, for example ``zip``, ``ai``, ``cc`` and ``ml``.
#: ``www.`` or a scheme is a link with any top-level domain.
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
#: A path, a query, a fragment or a port. After a domain it is never the end
#: of a sentence, so the domain is a link in any case.
_PATH_AFTER = frozenset("/?#:")
#: The word-like domains that are seldom a word after a dot. Before a space
#: and a word, one of them is a link when its last label holds
#: :data:`_WORD_LABEL_MIN` or more characters, in any case: ``Payroll.ai and``.
#: ``to`` and ``be`` are sentence words, so ``Thanks.to all`` is not a link.
_STRONG_TLDS = frozenset({
    "ai", "in", "app", "dev", "me", "us",
    "online", "top", "shop", "site", "store", "click", "link", "zip",
})
#: The shortest last label of a strong word-like domain that is a link before
#: a space and a word. ``payroll.ai and`` is a link, and ``acme.in now`` is not.
_WORD_LABEL_MIN = 5
#: A scheme: a scheme character right before ``://``. One pass, no nesting.
_SCHEME_RE = re.compile(r"[a-z0-9+.\-]://")
#: ``www.`` with no ASCII letter, digit or dash right before it. A letter of
#: another script may touch it: ``...www.``.
_WWW_RE = re.compile(r"(?<![a-z0-9\-])www\.")

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
#: A run of digits with separators. Separators lead only after a ``+``, so a
#: match starts at a ``+`` or a digit and the scan stays linear.
_PHONE_RUN_RE = re.compile(rf"(\+{_SEP}*)?(\d(?:{_SEP}*\d)*)")


def _has_address(text: str) -> bool:
    """True when the scan copy of *text* holds an ``@``, or `` at `` and
    later `` dot ``. ``at`` or ``dot`` in brackets counts as the word. A text
    or a copy over :data:`_SCAN_MAX` counts as one."""
    scan = _scan(text)
    if scan is None:
        return True
    low = " ".join(_BRACKET_WORD_RE.sub(r" \1 ", scan[1]).split())
    return "@" in low or _SPELLED_RE.search(low) is not None


def _label_start(kept: str, dot: int) -> int:
    """The index where the last label before the dot at *dot* starts: a run
    of letters, digits and dashes of any script."""
    k = dot
    while k > 0 and (kept[k - 1].isalnum() or kept[k - 1] == "-"):
        k -= 1
    return k


def _word_tld(kept: str, dot: int, end: int, tld: str) -> bool:
    """The end rule of a domain of :data:`WORD_TLDS`, with the dot at *dot*
    and the top-level domain *tld* in ``kept[dot + 1:end]``. A path, a query,
    a fragment or a port after it makes a link in any case. Else the
    top-level domain must be in lower case, so ``Done.In`` is no link. Then
    it is a link at the end of the text, or before a sign that is no space.
    Before a space and a word, it is a link when its last label holds a
    digit or a dash, when a dot and another label stand right before that
    label, or when it is one of :data:`_STRONG_TLDS` and its last label holds
    :data:`_WORD_LABEL_MIN` or more characters. The case of the label does
    not count."""
    nxt = kept[end] if end < len(kept) else ""
    if nxt and nxt in _PATH_AFTER:
        return True
    if not tld.islower():
        return False
    if not nxt or not nxt.isspace():
        return True
    k = end
    while k < len(kept) and kept[k].isspace():
        k += 1
    if k == len(kept) or not kept[k].isalpha():
        return True
    start = _label_start(kept, dot)
    label = kept[start:dot]
    if any(ch.isdigit() or ch == "-" for ch in label):
        return True
    if start >= 2 and kept[start - 1] == "." and kept[start - 2].isalnum():
        return True
    return tld in _STRONG_TLDS and len(label) >= _WORD_LABEL_MIN


def _domain_hit(kept: str) -> bool:
    """True when *kept* holds a bare domain of :data:`LINK_TLDS`. One pass
    over the dots: the top-level domain is the run of ASCII letters right
    after a dot, and a letter or a digit of any script stands right before
    the dot. Only an ASCII letter, digit or dash right after the run stops a
    domain, so a letter of another script may touch it: ``...hdfc.com...``.
    Each run of letters and each label is read once, so the pass is linear."""
    size = len(kept)
    dot = kept.find(".")
    while dot >= 0:
        end = dot + 1
        while end < size and kept[end].isascii() and kept[end].isalpha():
            end += 1
        tld = kept[dot + 1:end]
        nxt = kept[end] if end < size else ""
        bound = nxt.isascii() and (nxt.isdigit() or nxt == "-")
        low = tld.lower()
        if tld and dot and kept[dot - 1].isalnum() and not bound and low in LINK_TLDS:
            if low not in WORD_TLDS or _word_tld(kept, dot, end, tld):
                return True
        dot = kept.find(".", end)
    return False


def _has_link(text: str) -> bool:
    """True when the scan copy of *text* holds a scheme, ``www.``, or a bare
    domain whose top-level domain is in :data:`LINK_TLDS`
    (:func:`_domain_hit`). A domain of :data:`WORD_TLDS` takes the rule of
    :func:`_word_tld`. A text or a copy over :data:`_SCAN_MAX` counts as
    one."""
    scan = _scan(text)
    if scan is None:
        return True
    kept, low = scan
    if _SCHEME_RE.search(low) or _WWW_RE.search(low):
        return True
    return _domain_hit(kept)


def _has_phone(text: str) -> bool:
    """True when the scan copy of *text* holds a ``+`` and then 8 or more
    digits, or 10 or more digits with a separator between them. A bare run
    of digits is not a phone number, and a date is never part of one. A text
    or a copy over :data:`_SCAN_MAX` counts as one."""
    scan = _scan(text)
    if scan is None:
        return True
    for m in _PHONE_RUN_RE.finditer(_DATE_RE.sub("#", scan[1])):
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
