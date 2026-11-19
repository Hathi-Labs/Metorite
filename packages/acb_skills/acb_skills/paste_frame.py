"""The split of a member's turn into the member's words and one pasted block (HRD-14a).

Spec: ``project-docs/specs/chat_failure_modes.md`` section 4.7b (PR 2529), the row
HRD-14a of its section 6, and the questions Q-HRD-14-1 to Q-HRD-14-12 of its
section 8. D-HRD-5 (D217) decides the trigger: a part of a member turn that
holds 8 or more lines that are not blank holds a block. Fence:
``tests/unit/test_paste_frame.py``.

The split and the frame are PURE. HRD-14b (task 3667) adds the seam, dark
behind ``CHAT_PASTE_FRAME_ORGS`` (:func:`paste_frame_on`, empty is off):

* :data:`RAW_PARTS` is the raw binding. The chat route binds the raw member
  parts of the turn there (:func:`bind_raw_parts`). A tuple is a bound run,
  also when it is empty. None means that the door binds nothing, and then
  the seam frames nothing on the run. It is NOT ``MEMBER_SENT``: that holds
  ``typed_text``, which is empty for a long paste.
* The composer records the shape of the run input (:func:`record_shape`):
  ``session``, ``list`` or ``string``. The seam frames nothing on the
  string shape.
* :func:`frame_current_turn`, :func:`frame_earlier_turn` and
  :func:`frame_part` are the rules of the seam for one text. The MAF
  middleware that applies them to a COPY of each model request lives in the
  orchestrator (``orchestrator.paste_turns``), because it reads the steer
  turns of the run. Each one fails closed: a part that fails frames whole.

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

import contextlib
import contextvars
import re
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import NamedTuple

from acb_common import get_logger
from acb_common.assistant_profile import _PASTED

from acb_skills.reply_card import is_invisible_char
from acb_skills.untrusted import _BREAKS, UntrustedFrame, neutralise

__all__ = [
    "LEAD_MAX",
    "MIN_LINES",
    "PASTE_LABEL",
    "RAW_PARTS",
    "SHAPES",
    "PathsExist",
    "bind_raw_parts",
    "frame_current_turn",
    "frame_earlier_turn",
    "frame_member_text",
    "frame_part",
    "frame_whole",
    "is_long",
    "no_path_proven",
    "note_paths",
    "paste_frame_everywhere",
    "paste_frame_on",
    "raw_parts",
    "record_shape",
    "shape_of",
    "split",
]

_log = get_logger("acb_skills.paste_frame")

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


# -- HRD-14b: the flag ------------------------------------------------------------


def _flag_entries() -> frozenset[str] | None:
    """The entries of ``CHAT_PASTE_FRAME_ORGS``, lower case. None when the
    settings cannot be read, which reads as off."""
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_paste_frame_orgs", "") or "")
    except Exception:
        return None
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def paste_frame_on(org: object = None) -> bool:
    """True when ``CHAT_PASTE_FRAME_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant. A comma list of org ids, or ``*`` for
    each one. Empty means off, the default. It reads the settings on each
    call, so a flag reload (``docs/flag_reload.md``) applies a change with no
    restart. It fails closed. The org must come from the run binding, never
    from request input (R11)."""
    entries = _flag_entries()
    if not entries:
        return False
    if "*" in entries:
        return True
    if org is None:
        try:
            from acb_common.db import current_tenant

            org = current_tenant()
        except Exception:
            return False
    return str(org or "").strip().lower() in entries


def paste_frame_everywhere() -> bool:
    """True only when ``CHAT_PASTE_FRAME_ORGS`` holds ``*``. It reads no org.

    The ``/v1`` door binds no tenant (``current_tenant()`` is None there), and
    an org from a header or the body is the move that R5 and R11 forbid. So
    the restore of a cut block on that door asks this, never
    :func:`paste_frame_on`."""
    entries = _flag_entries()
    return bool(entries) and "*" in entries


# -- HRD-14b: the raw binding and the shape of the run input ----------------------

#: The raw member parts of the turn (HRD-14b). The chat route binds them:
#: the sent words while the message is still the client's text, and the raw
#: text of each replayed steer. A tuple is a bound run, also when it is empty
#: (a Continue whose note the server wrote, and RESUME-2a). None means that
#: the door binds nothing. Never read ``MEMBER_SENT`` or ``sent_words()`` in
#: its place: they hold ``typed_text``, which is empty for a long paste.
RAW_PARTS: contextvars.ContextVar[tuple[object, ...] | None] = contextvars.ContextVar(
    "paste_frame_raw_parts", default=None)


@contextlib.contextmanager
def bind_raw_parts(parts: Iterable[object] | None) -> Iterator[None]:
    """Bind the raw member parts of ONE turn. Closing restores the value it
    found, and never raises. None binds nothing: the door is unbound."""
    token = RAW_PARTS.set(None if parts is None else tuple(parts))
    try:
        yield
    finally:
        try:
            RAW_PARTS.reset(token)
        except (ValueError, RuntimeError):
            RAW_PARTS.set(None)


def raw_parts() -> tuple[object, ...] | None:
    """The raw member parts of the run on this frame, or None (unbound)."""
    return RAW_PARTS.get()


#: The three shapes of the input of a run (spec section 4.7b, the table).
SHAPES = ("session", "list", "string")

#: ``(run id, shape)`` of the run on this frame. The composer records it, so a
#: run that starts inside another run reads no shape of its parent.
_RUN_SHAPE: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "paste_frame_run_shape", default=None)


def record_shape(run_id: object, shape: str) -> bool:
    """Record the shape of the input of run *run_id*. True when it is the
    first record of that run, so the caller writes ONE log line for each run,
    also when the run composes twice (a Tier 1 fall back)."""
    rid = str(run_id or "")
    prior = _RUN_SHAPE.get()
    _RUN_SHAPE.set((rid, shape if shape in SHAPES else "string"))
    return prior is None or prior[0] != rid


def shape_of(run_id: object) -> str | None:
    """The recorded shape of run *run_id*, or None when that run recorded none."""
    record = _RUN_SHAPE.get()
    rid = str(run_id or "")
    if record is None or not rid or record[0] != rid:
        return None
    return record[1]


# -- HRD-14b: the rules of the seam for one text ----------------------------------


def _lines(text: str) -> list[str]:
    return _BREAK_RE.sub("\n", str(text or "")).split("\n")


def is_long(text: object) -> bool:
    """True when *text* holds :data:`MIN_LINES` or more lines that are not
    blank. The 8-line trigger reads the RAW part, never a trimmed turn."""
    return sum(not _blank(line) for line in _lines(str(text or ""))) >= MIN_LINES


def note_paths(text: object) -> tuple[str, ...] | None:
    """The paths of the whole upload note at the end of *text*, NOT proven,
    or None. The seam proves them once for each run, then gives
    :func:`split` a ``paths_exist`` that reads the proof."""
    noted = _note_paths(str(text or ""))
    return None if noted is None else noted[1]


def frame_whole(text: object, frame: UntrustedFrame) -> str:
    """*text* whole, as ONE block with no lead, with its paste note before it.
    The safe failure of each rule below."""
    return f"{frame.paste_note()}\n{frame.wrap(PASTE_LABEL, str(text or ''))}"


def frame_part(text: object, frame: UntrustedFrame, *, paths_exist: PathsExist) -> str:
    """ONE part through :func:`frame_member_text`. It fails closed: when
    *text* is not a string, or ``paths_exist`` raises, the whole part is one
    block with no lead (done-when 20 of HRD-14b)."""
    try:
        if not isinstance(text, str):
            raise TypeError("a part is not a string")
        return frame_member_text(text, frame, paths_exist=paths_exist)
    except Exception as exc:
        _log.warning("paste_frame.part_failed", error=type(exc).__name__)
        return frame_whole(text, frame)


def frame_current_turn(
    text: str, parts: Sequence[object], frame: UntrustedFrame, *, paths_exist: PathsExist,
) -> str:
    """The current turn *text*, with each raw part of *parts* framed where it
    stands (spec section 4.7b, "Which text the middleware frames").

    Each part is found in *text*, both stripped, from the end: the sent words
    end the turn, and each replayed steer stands before them. The text around
    the parts stays outside: the supersede note, the ``[steer from ...]``
    prefixes and the ``steers_frame`` line. A part that is not a string
    frames the whole turn. A part that is missing (the compose trim rewrote
    the turn) frames the whole turn as one block, with no lead, only when the
    RAW part holds :data:`MIN_LINES` or more lines that are not blank.

    A long part frames EACH copy of it in the turn (HRD-14b fix round 2). A
    server Continue note comes AFTER the replayed steers, and it can quote a
    steer word for word. The search from the end would find that quote, and
    the real steer would stay outside. So each copy goes into a block, also a
    copy inside server text: that is the safe failure. A short part frames
    nothing, so its last copy only bounds the search of the parts before it."""
    if any(not isinstance(part, str) for part in parts):
        return frame_whole(text, frame)
    spans: list[tuple[int, int, str]] = []
    end = len(text)
    for part in reversed(parts):
        want = str(part).strip()
        if not want:
            continue
        found = _copies(text, want, end) if is_long(part) else []
        if not found:
            at = text.rfind(want, 0, end)
            found = [at] if at >= 0 else []
        if not found:
            if is_long(part):
                return frame_whole(text, frame)
            continue
        spans += [(at, at + len(want), want) for at in sorted(found, reverse=True)]
        end = min(found)
    out = text
    for at, stop, want in spans:  # from the end, so each index still holds
        out = out[:at] + frame_part(want, frame, paths_exist=paths_exist) + out[stop:]
    return out


def _copies(text: str, want: str, end: int) -> list[int]:
    """The start of each copy of *want* that lies whole in ``text[:end]``,
    with no overlap, from the start."""
    out: list[int] = []
    at = text.find(want, 0, end)
    while at >= 0:
        out.append(at)
        at = text.find(want, at + len(want), end)
    return out


def _trim_marker() -> str:
    """The marker of the compose trim (``acb_llm.context._TRUNCATION_MARKER``),
    stripped. ``acb_llm`` must not import ``acb_skills``, so the read goes
    this way."""
    try:
        from acb_llm.context import _TRUNCATION_MARKER

        return _TRUNCATION_MARKER.strip()
    except Exception:
        dots = chr(0x2026)
        return f"[{dots} content truncated to fit the model context window {dots}]"


def frame_earlier_turn(text: str, frame: UntrustedFrame, *, paths_exist: PathsExist) -> str:
    """An earlier user turn, through the split, server text and all. A turn
    that holds the trim marker goes into ONE block whole: the trim may have
    cut a paste under 8 lines."""
    if _trim_marker() in str(text or ""):
        return frame_whole(text, frame)
    return frame_part(text, frame, paths_exist=paths_exist)
