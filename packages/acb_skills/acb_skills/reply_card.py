"""The reply card: one email reply, confirmed and done in the chat (EM-CR).

Spec ``project-docs/specs/email_app_master_plan.md`` §18. The owner asked on
2026-10-20 for a reply that the member confirms in the chat and that then
completes, with no Email app opened. The email assistant's ``compose_reply``
shows ONE card. The card holds the mail it answers, the recipients that the
gateway derived, a Reply or Reply all toggle, the editable body, and the
buttons Send, Save to Drafts and Discard.

The card is the confirmation card of :func:`acb_skills.ask_tools.request_confirmation`
with a ``reply`` form. No second card mechanism exists. This module holds
the parts that two processes must agree on:

* the answer of the card, ``APPROVE {"reply": {...}}`` (:func:`parse_reply_answer`);
* the record of that answer, which ``POST /agent/respond-input`` writes when
  the member's browser posts it (:func:`record_reply_answer`), and which the
  act route of the gateway takes once (:func:`take_reply_answer`);
* the one cleaning of a reply body (:func:`clean_reply_body`), so the text
  that leaves is the text that the card showed.

**Why a record.** The tool may send even in a run that read outside mail
(H-236 holds such a run), because the member confirmed this one reply. So the
body that leaves must be the body that the MEMBER submitted. The gateway does
not take the tool's word for it. The respond-input route stores a digest of
the answer, keyed by the card's thread and request id, and bound to the
member who answered. The answer names the email. The act route sends only an
answer whose digest matches, for the same member, once. A model has no tool
that writes the record. Fence: ``tests/unit/test_email_chat_reply.py``.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_skills.reply_card")

__all__ = [
    "INVISIBLE_CODE_POINTS",
    "MAX_MARK_RUN",
    "MAX_REPLY_ANSWER",
    "MAX_REPLY_BODY",
    "MAX_REPLY_RECIPIENTS",
    "RECORD_NAMESPACE",
    "RECORD_TTL_S",
    "REPLY_ACTIONS",
    "REPLY_ANSWER_KEYS",
    "REPLY_ANSWER_OPTIONAL_KEYS",
    "REPLY_ANSWER_PREFIX",
    "LookalikeWord",
    "ReplyChoice",
    "clean_reply_body",
    "has_hidden_text",
    "has_invisible_spacing",
    "is_invisible_char",
    "is_lookalike_space",
    "lookalike_refusal",
    "lookalike_warning",
    "lookalike_words",
    "looks_like_reply_answer",
    "looks_like_send_answer",
    "parse_reply_answer",
    "record_reply_answer",
    "reply_answer_digest",
    "reply_body_refusal",
    "strip_invisible_spacing",
    "take_reply_answer",
    "visible_spacing",
]

#: The prefix of an Approve with a payload. The rows card uses the same one
#: (``ask_tools.ROWS_ANSWER_PREFIX``), and the JSON key tells the two apart.
REPLY_ANSWER_PREFIX = "APPROVE "

#: What the member may choose on the card. Discard is ``REJECT``.
REPLY_ACTIONS: frozenset[str] = frozenset({"send", "save"})

#: The keys of ``answer.reply``, exactly. ``to`` and ``cc`` are the people
#: that the member TICKED on the card (owner, 2026-10-24). The gateway
#: derives the recipients again, and it sends only when each ticked person
#: is on the derived list (``reply_card.ticked_subset``): the member can take
#: a person off and never add one. ``message_id`` is the
#: email the card answers, so the record of one card cannot act on another
#: email (security review of #1191).
REPLY_ANSWER_KEYS: frozenset[str] = frozenset(
    {"action", "reply_all", "body", "to", "cc", "message_id"})
#: The one key that an answer MAY add: ``bcc``, the Bcc people that the
#: member ticked (§21.14, owner 2026-10-24). The gateway sends a Bcc only
#: when each one is on the record of THIS card (``reply_card.ticked_subset``),
#: so the member can take one off and never add one. An answer of an older
#: card names no ``bcc``, and it still parses.
REPLY_ANSWER_OPTIONAL_KEYS: frozenset[str] = frozenset({"bcc"})

#: The longest body the card takes. A longer one is refused, never cut.
MAX_REPLY_BODY = 20_000
#: The most addresses of one list.
MAX_REPLY_RECIPIENTS = 100
_MAX_ADDRESS = 320
_MAX_MESSAGE_ID = 200
#: The longest answer that can be honest: the body escaped as JSON (six
#: characters for each one at most), the two lists, the email id and the frame.
MAX_REPLY_ANSWER = (len(REPLY_ANSWER_PREFIX) + 6 * MAX_REPLY_BODY
                    + 3 * MAX_REPLY_RECIPIENTS * (_MAX_ADDRESS + 4) + _MAX_MESSAGE_ID + 200)

#: The Redis namespace of the records (``cc:<org>:reply-card:<thread>:<id>``).
RECORD_NAMESPACE = "reply-card"
#: A card waits an hour at most (``wait_user_future``), and the act follows
#: at once. The record lives a little longer than the wait.
RECORD_TTL_S = 3700
#: How long the act route waits for the record. respond-input writes it just
#: AFTER the run took the answer (review of #1191, P2), so the act can come first.
TAKE_WAIT_S = 3.0
_TAKE_STEP_S = 0.1


# ── Hidden text (security review of #1191, P1, rounds 1 and 2) ────────────────
#
# The drafter reads the knowledge base, sent mail, the calendar and the
# thread. A mail can steer it to hide member data in text that a member does
# not see: Unicode tag characters, zero-width characters, bidi controls,
# variation selectors after a letter ("emoji smuggling"), blank fillers, or
# data after many blank lines. So the body of a reply card is cleaned BEFORE
# the card shows it, and the member's body is checked and cleaned again
# before the send. What leaves is what the card showed. The email agent's
# card text (``_card_text``) reads the same rule (:func:`is_invisible_char`).

#: Each code point a reader cannot see, as inclusive ranges. THE one list.
#:
#: Source: Unicode 15.1, ``DerivedCoreProperties.txt``, the property
#: ``Default_Ignorable_Code_Point``, plus two blank fillers that the property
#: leaves out: U+2800 BRAILLE PATTERN BLANK and U+1D159 MUSICAL SYMBOL NULL
#: NOTEHEAD. The standard library has no
#: table of the property. ``tests/unit/test_email_chat_reply.py`` holds this
#: list to ``\p{Default_Ignorable_Code_Point}`` of the ``regex`` package where
#: the test environment has it, so a newer Unicode cannot leave a hole.
INVISIBLE_CODE_POINTS: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),    # SOFT HYPHEN
    (0x034F, 0x034F),    # COMBINING GRAPHEME JOINER
    (0x061C, 0x061C),    # ARABIC LETTER MARK
    (0x115F, 0x1160),    # HANGUL CHOSEONG and JUNGSEONG FILLER
    (0x17B4, 0x17B5),    # KHMER VOWEL INHERENT AQ and AA
    (0x180B, 0x180F),    # MONGOLIAN FREE VARIATION SELECTORS, VOWEL SEPARATOR
    (0x200B, 0x200F),    # ZERO WIDTH SPACE to RIGHT-TO-LEFT MARK
    (0x202A, 0x202E),    # the bidi embeddings and overrides
    (0x2060, 0x206F),    # WORD JOINER to NOMINAL DIGIT SHAPES, the isolates
    (0x2800, 0x2800),    # BRAILLE PATTERN BLANK (a blank filler, not in the property)
    (0x3164, 0x3164),    # HANGUL FILLER
    (0xFE00, 0xFE0F),    # VARIATION SELECTORS 1-16
    (0xFEFF, 0xFEFF),    # ZERO WIDTH NO-BREAK SPACE
    (0xFFA0, 0xFFA0),    # HALFWIDTH HANGUL FILLER
    (0xFFF0, 0xFFF8),    # unassigned specials
    (0x1BCA0, 0x1BCA3),  # SHORTHAND FORMAT CONTROLS
    (0x1D159, 0x1D159),  # MUSICAL SYMBOL NULL NOTEHEAD (a blank So, not in the property)
    (0x1D173, 0x1D17A),  # MUSICAL SYMBOL BEGIN BEAM to END PHRASE
    (0xE0000, 0xE0FFF),  # the tags and VARIATION SELECTORS 17-256
)

_ZWJ = "\u200d"
#: The two emoji presentation selectors. Each may follow ONE pictograph.
_PRESENTATION = frozenset("\ufe0e\ufe0f")
#: COMBINING ENCLOSING KEYCAP, the end of a keycap emoji such as 1, VS16, keycap.
_KEYCAP = "\u20e3"
_KEYCAP_BASES = frozenset("0123456789#*")
_KEEP = frozenset("\n\t")
#: The line and paragraph separators break a line, as a new line does.
_LINE_BREAKS = frozenset("\u2028\u2029")
#: The most combining marks one letter carries in real text. Hindi, Tamil,
#: Thai and fully vowelled Arabic use three at most. A longer run is refused.
MAX_MARK_RUN = 4


def _in_ranges(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in INVISIBLE_CODE_POINTS)


def is_invisible_char(ch: str) -> bool:
    """True for a character that a reader cannot see: a code point of
    :data:`INVISIBLE_CODE_POINTS`, or of Unicode category C. A new line and a
    tab are not, and neither is a space. THE one test of hidden text."""
    if ch in _KEEP:
        return False
    return _in_ranges(ord(ch)) or unicodedata.category(ch).startswith("C")


def is_lookalike_space(ch: str) -> bool:
    """True for a character that a reader sees as a space and that is not
    U+0020: Unicode category Zs (NBSP, U+2000 to U+200A, U+202F, U+205F,
    U+3000, and the Ogham space mark). A body that swaps some plain spaces
    for these hides a few bits per word (review of #1191, P2), so
    :func:`clean_reply_body` makes each one a plain space."""
    return ch != " " and unicodedata.category(ch) == "Zs"


def _is_pictograph(ch: str) -> bool:
    """A real pictograph: a visible symbol (category So) or an emoji skin
    tone. A blank filler (U+2800) is not one."""
    if not ch or is_invisible_char(ch):
        return False
    return unicodedata.category(ch) == "So" or 0x1F3FB <= ord(ch) <= 0x1F3FF


def _kept_join(raw: str, i: int, out: list[str]) -> bool:
    """True when the joiner or selector at *raw[i]* joins a real emoji."""
    ch = raw[i]
    prev = out[-1] if out else ""
    nxt = raw[i + 1] if i + 1 < len(raw) else ""
    if ch in _PRESENTATION:
        # Never two in a row: a selector is not a pictograph, so a second one
        # after it is not kept (fence: the two-vs16 probe).
        return _is_pictograph(prev) or (prev in _KEYCAP_BASES and nxt == _KEYCAP)
    if ch == _ZWJ:
        before = out[-2] if prev in _PRESENTATION and len(out) > 1 else prev
        return _is_pictograph(before) and _is_pictograph(nxt)
    return False


def clean_reply_body(text: str) -> str:
    """*text* as the member can see it, and nothing more. THE one cleaning.

    * Each invisible character leaves (:func:`is_invisible_char`), but a new
      line and a tab. A zero-width joiner stays only between two real
      pictographs, and one presentation selector (VS15 or VS16) stays only
      right after a real pictograph, or in a keycap.
    * A space that only looks like a space (:func:`is_lookalike_space`) is a
      plain space, U+0020. A line separator is a new line. The spaces at the
      end of a line leave, so a line of spaces is a blank line.
    * A run of blank lines is one blank line, and the text is trimmed.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for i, ch in enumerate(raw):
        if ch in _LINE_BREAKS:
            out.append("\n")
        elif is_lookalike_space(ch):
            out.append(" ")
        elif ch in _PRESENTATION or ch == _ZWJ:
            if _kept_join(raw, i, out):
                out.append(ch)
        elif not is_invisible_char(ch):
            out.append(ch)
    kept: list[str] = []
    for line in "".join(out).split("\n"):
        line = line.rstrip()
        if not line and kept and not kept[-1]:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


#: The bidi controls, which reorder what the member reads.
_BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def _long_mark_run(text: str) -> bool:
    run = 0
    for ch in text:
        if unicodedata.category(ch) in ("Mn", "Me"):
            run += 1
            if run > MAX_MARK_RUN:
                return True
        else:
            run = 0
    return False


def reply_body_refusal(text: str) -> str | None:
    """Why the member's body may not go, or None.

    A body is refused, never cleaned in silence, when it holds a character
    that carries hidden data or changes what a reader sees: a tag character
    or a variation selector 17-256 (U+E0000 to U+E0FFF), a bidi control, a
    variation selector that does not follow a real pictograph, or a run of
    more than :data:`MAX_MARK_RUN` combining marks on one letter. A member
    does not type one. Each other hidden character is cleaned
    (:func:`clean_reply_body`), so a typed emoji keeps its joiner."""
    raw = str(text or "")
    out: list[str] = []
    for i, ch in enumerate(raw):
        cp = ord(ch)
        if 0xE0000 <= cp <= 0xE0FFF or ch in _BIDI_CONTROLS:
            return _HIDDEN_REFUSAL
        if 0xFE00 <= cp <= 0xFE0F and not (ch in _PRESENTATION and _kept_join(raw, i, out)):
            return _HIDDEN_REFUSAL
        out.append(ch)
    if _long_mark_run(clean_reply_body(raw)):
        return _HIDDEN_REFUSAL
    return None


_HIDDEN_REFUSAL = ("The reply text holds hidden characters that change what the reader "
                   "sees, so nothing was sent or saved. Ask for the reply again.")


def visible_spacing(text: str) -> str:
    """*text* with only the visible normalisations of :func:`clean_reply_body`:
    one kind of line end, a plain space for each space that only looks like
    one (NBSP), no space at the end of a line, one blank line for a run, and
    no blank edge. It removes no character that a reader cannot see.

    The draft path saves this form (§21 fix round 2): NBSP and trailing
    spaces occur in real mail, so they are normalised and never refused."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = "".join("\n" if ch in _LINE_BREAKS else (" " if is_lookalike_space(ch) else ch)
                  for ch in raw)
    kept: list[str] = []
    for line in raw.split("\n"):
        line = line.rstrip()
        if not line and kept and not kept[-1]:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _lines(text: str) -> list[str]:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def has_invisible_spacing(text: str) -> bool:
    """True when *text* holds a space that a card cannot show (§21 fix round
    3): a space that only looks like one (NBSP, category Zs), or a space or a
    tab at the end of a line. A chat send refuses such a draft, and a chat
    writer removes them (:func:`strip_invisible_spacing`)."""
    raw = str(text or "")
    if any(is_lookalike_space(ch) or ch in _LINE_BREAKS for ch in raw):
        return True
    return any(line != line.rstrip(" \t") for line in _lines(raw))


def strip_invisible_spacing(text: str) -> str:
    """*text* with each NBSP a plain space and no space at the end of a line.
    It keeps every line and every blank line, so it changes nothing that a
    reader sees (§21 fix round 3)."""
    raw = "".join(" " if is_lookalike_space(ch) else ("\n" if ch in _LINE_BREAKS else ch)
                  for ch in str(text or ""))
    return "\n".join(line.rstrip(" \t") for line in _lines(raw))


def has_hidden_text(text: str) -> bool:
    """True when *text* holds a character that a reader cannot see, or that
    changes what the reader sees (§21 fix round 2): a tag character, a
    zero-width character, a bidi control, a stray variation selector, or a
    long run of marks. An emoji joiner and a presentation selector inside a
    real emoji are not hidden. NBSP and trailing spaces are not hidden: they
    are normalised (:func:`visible_spacing`).

    THE one test of a draft's text. It holds exactly when the one cleaning
    (:func:`clean_reply_body`) would remove a character."""
    raw = str(text or "")
    if reply_body_refusal(raw):
        return True
    return clean_reply_body(raw) != visible_spacing(raw)


# ── Look-alike letters (H-393, ``email_app_master_plan.md`` §21.13) ──────────
#
# A Cyrillic "\u0430" inside a Latin word shows on a card as a Latin "a". A model
# can use such letters, or an invisible character inside a word, to carry
# member data out in a text that looks right. So each chat send path that a
# held chat reaches refuses a word that mixes scripts, and a word with an
# invisible character. Outside a held chat the card shows a warning. A text
# that is all in one script (Hindi, Russian, Greek) passes.

#: The scripts that join in one word in real text: Japanese mixes kanji,
#: hiragana and katakana with no space. Their letters do not count.
_FREE_SCRIPTS = frozenset({"CJK", "HIRAGANA", "KATAKANA", "KATAKANA-HIRAGANA", "HANGUL",
                           "BOPOMOFO", "IDEOGRAPHIC", "HALFWIDTH"})
#: ZERO WIDTH NON-JOINER and ZERO WIDTH JOINER, which shape letters of the
#: Arabic and the Indic scripts.
_SCRIPT_JOINERS = frozenset("‌‍")


def _script_of(ch: str) -> str:
    """The script of one letter, from its Unicode name ("CYRILLIC SMALL
    LETTER A" is CYRILLIC), or "" for a character that does not count: a
    mark, a digit, a modifier letter, or a letter of :data:`_FREE_SCRIPTS`."""
    cat = unicodedata.category(ch)
    if not cat.startswith("L") or cat == "Lm":
        return ""
    head = unicodedata.name(ch, "").split(" ", 1)[0]
    return "" if head in _FREE_SCRIPTS else head


@dataclass(frozen=True)
class LookalikeWord:
    """One word that a card cannot show as it is (H-393)."""

    word: str
    #: The letter that does not belong, or the invisible character.
    letter: str
    #: The script of that letter, such as CYRILLIC, or "" for an invisible one.
    script: str
    #: The main script of the word, such as LATIN.
    main: str
    #: The Latin letter that the letter imitates (``held_card.confusable_of``).
    looks_like: str


def lookalike_words(text: str) -> list[LookalikeWord]:
    """Each word of *text* that mixes scripts, or that holds an invisible
    character, in order. A word is a run of letters, marks and invisible
    characters. NBSP and the other look-alike spaces end a word."""
    from acb_skills.held_card import confusable_of

    out: list[LookalikeWord] = []
    word: list[str] = []

    def close() -> None:
        if not word:
            return
        raw = "".join(word)
        word.clear()
        shown = "".join(ch for ch in raw if not is_invisible_char(ch))
        latin = any(_script_of(ch) == "LATIN" for ch in raw)
        # Persian, Hindi and Malayalam join letters with ZWNJ and ZWJ. In a
        # word with no Latin letter those two are writing, not a hiding place.
        hidden = [ch for ch in raw if is_invisible_char(ch)
                  and (latin or ch not in _SCRIPT_JOINERS)]
        if hidden and shown:
            out.append(LookalikeWord(shown, hidden[0], "", "", ""))
            return
        scripts = [s for s in (_script_of(ch) for ch in raw) if s]
        if len(set(scripts)) < 2:
            return
        main = max(set(scripts), key=lambda s: (scripts.count(s), s == "LATIN"))
        odd = next(ch for ch in raw if _script_of(ch) not in ("", main))
        out.append(LookalikeWord(shown, odd, _script_of(odd), main, confusable_of(odd)))

    for ch in str(text or ""):
        if (is_invisible_char(ch) and ch not in "\n\t") or unicodedata.category(ch)[0] in "LM":
            word.append(ch)
        else:
            close()
    close()
    return out


def _lookalike_sentence(hit: LookalikeWord, where: str) -> str:
    if not hit.script:
        return (f"The {where} holds the word \"{hit.word}\" with an invisible character "
                f"(U+{ord(hit.letter):04X}) inside it")
    letter = f"a {hit.script.title()} letter \"{hit.letter}\" (U+{ord(hit.letter):04X}"
    letter += (f", it looks like the Latin \"{hit.looks_like}\")" if hit.looks_like else ")")
    return (f"The {where} holds the word \"{hit.word}\", which mixes {letter} into "
            f"{hit.main.title()} letters")


def lookalike_refusal(texts: dict[str, str]) -> str | None:
    """The one sentence that refuses a send in a held chat (H-393), or None.

    *texts* maps a name ("subject", "text") to its text. The first word that
    mixes scripts, or that holds an invisible character, is named."""
    for where, value in texts.items():
        hits = lookalike_words(value)
        if hits:
            more = f" ({len(hits) - 1} more word(s) too)" if len(hits) > 1 else ""
            return (f"Not sent. {_lookalike_sentence(hits[0], where)}{more}. A chat that "
                    "read outside mail sends no such word. Write it again with plain letters.")
    return None


def lookalike_warning(texts: dict[str, str]) -> str | None:
    """The warning of a card outside a held chat (H-393), or None."""
    for where, value in texts.items():
        hits = lookalike_words(value)
        if hits:
            more = f" ({len(hits) - 1} more word(s) too)" if len(hits) > 1 else ""
            return (f"Check each letter. {_lookalike_sentence(hits[0], where)}{more}. "
                    "It can look like a plain word.")
    return None


@dataclass(frozen=True)
class ReplyChoice:
    """What the member chose on a reply card."""

    action: str
    reply_all: bool
    body: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    message_id: str = ""
    #: The Bcc people that the member ticked (§21.14). Empty for an answer
    #: that names no ``bcc``.
    bcc: tuple[str, ...] = ()


def _addresses(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or len(value) > MAX_REPLY_RECIPIENTS:
        return None
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > _MAX_ADDRESS:
            return None
        out.append(item.strip())
    return tuple(out)


def parse_reply_answer(answer: Any) -> ReplyChoice | None:
    """The member's choice, or None when *answer* is not a reply answer.

    Strict, and it fails closed. The answer is ``APPROVE`` and one JSON
    object with the one key ``reply``. That object holds exactly
    :data:`REPLY_ANSWER_KEYS`, and it may add ``bcc``
    (:data:`REPLY_ANSWER_OPTIONAL_KEYS`). A missing key, an extra key (an
    ``attachments``, a ``files``), an unknown action, an empty body, a body over
    :data:`MAX_REPLY_BODY`, or no email id reads as no answer. Nothing is
    trimmed to fit.
    """
    text = answer if isinstance(answer, str) else ""
    if len(text) > MAX_REPLY_ANSWER:
        return None
    text = text.strip()
    if not text.upper().startswith(REPLY_ANSWER_PREFIX):
        return None
    try:
        payload = json.loads(text[len(REPLY_ANSWER_PREFIX):])
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"reply"}:
        return None
    reply = payload["reply"]
    if not isinstance(reply, dict) or not (
            REPLY_ANSWER_KEYS <= set(reply) <= REPLY_ANSWER_KEYS | REPLY_ANSWER_OPTIONAL_KEYS):
        return None
    action, reply_all, body = reply["action"], reply["reply_all"], reply["body"]
    if action not in REPLY_ACTIONS or not isinstance(reply_all, bool):
        return None
    if not isinstance(body, str) or not body.strip() or len(body) > MAX_REPLY_BODY:
        return None
    to, cc = _addresses(reply["to"]), _addresses(reply["cc"])
    bcc = _addresses(reply.get("bcc", []))
    if to is None or cc is None or bcc is None or not to:
        return None
    message_id = reply["message_id"]
    if (not isinstance(message_id, str) or not message_id.strip()
            or len(message_id) > _MAX_MESSAGE_ID):
        return None
    return ReplyChoice(action=action, reply_all=reply_all, body=body, to=to, cc=cc,
                       message_id=message_id.strip(), bcc=bcc)


def looks_like_reply_answer(answer: Any) -> bool:
    """True when *answer* parses as a reply answer."""
    return parse_reply_answer(answer) is not None


def looks_like_send_answer(answer: Any) -> bool:
    """True when *answer* has the shape of the answer of a held send card
    (``email_app_master_plan.md`` §21): ``APPROVE {"rows": [...]}`` with 1 to
    ``MAX_CARD_ROWS`` short string ids, and nothing else.

    It checks the shape only. The gateway's act route checks the ids against
    the rows that it recorded for the card (``ask_tools.ticked_rows``)."""
    from acb_skills.ask_tools import MAX_CARD_ROWS, MAX_ROWS_ANSWER, ROWS_ANSWER_PREFIX

    text = answer if isinstance(answer, str) else ""
    if len(text) > MAX_ROWS_ANSWER:
        return False
    text = text.strip()
    if not text.upper().startswith(ROWS_ANSWER_PREFIX):
        return False
    try:
        payload = json.loads(text[len(ROWS_ANSWER_PREFIX):])
    except (TypeError, ValueError, RecursionError):
        return False
    if not isinstance(payload, dict) or set(payload) != {"rows"}:
        return False
    ids = payload["rows"]
    return (isinstance(ids, list) and 0 < len(ids) <= MAX_CARD_ROWS
            and all(isinstance(i, str) and 0 < len(i) <= 64 for i in ids))


def _recordable(answer: Any) -> bool:
    """True when respond-input records *answer*: a reply answer, and, only
    while ``EMAIL_HELD_CARD_SEND`` is on, the answer of a held send card."""
    if looks_like_reply_answer(answer):
        return True
    if not looks_like_send_answer(answer):
        return False
    from acb_skills.held_card import held_card_send_enabled

    return held_card_send_enabled()


def reply_answer_digest(
    request_id: str, responder: str, answer: str, thread_id: str = "",
) -> str:
    """The digest of one answer: the card, its thread, the member and the
    exact text. The text names the email (``message_id``)."""
    parts = "\x1f".join((str(request_id or ""), str(thread_id or ""),
                         str(responder or "").strip().lower(), str(answer or "")))
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def _tenant_redis() -> Any:
    """The tenant Redis client. A seam, so a test passes a fake client."""
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


def _record_key(thread_id: str, request_id: str) -> Any:
    from acb_common.tenant_redis import key

    return key(RECORD_NAMESPACE, str(thread_id), str(request_id))


async def record_reply_answer(
    organization_id: str | None, request_id: str, responder: str | None, answer: str,
    thread_id: str | None = None,
) -> bool:
    """Record the digest of a reply answer that the member's browser posted.

    ``POST /agent/respond-input`` calls it AFTER the card's own thread took
    the answer, so a request id that a viewer saw cannot be recorded from
    another room (H-201, review of #1191). The key names the thread and the
    card, with the caller's own tenant and email from the session (R5). An
    answer that is not a reply answer is not recorded. The first record of a
    card in its thread wins (``SET NX``). Never raises. A fault records
    nothing, so the act route refuses, and nothing is sent.

    With ``EMAIL_HELD_CARD_SEND`` on, the answer of a held send card is
    recorded too, with the same key, the same ``SET NX`` and the same digest
    (``email_app_master_plan.md`` §21.4 item 3).
    """
    if not (organization_id and request_id and responder and thread_id):
        return False
    if not _recordable(answer):
        return False
    digest = reply_answer_digest(request_id, responder, answer, thread_id)
    try:
        from acb_common.tenant_redis import organization_scope

        with organization_scope(str(organization_id)):
            done = await _tenant_redis().set(
                _record_key(thread_id, request_id), digest, nx=True, ex=RECORD_TTL_S)
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("reply_card.record_failed", error=type(exc).__name__)
        return False
    _log.info("reply_card.answer_recorded", first=bool(done))
    return bool(done)


async def take_reply_answer(
    organization_id: str | None, request_id: str, responder: str | None, answer: str,
    thread_id: str | None = None, *, wait_s: float = TAKE_WAIT_S,
) -> bool:
    """True when the member posted exactly *answer* to the card *request_id*
    of the thread *thread_id*.

    The act route of the gateway calls it, with its own tenant and the
    caller's email from the session. The record is read and deleted in ONE
    command (``GETDEL``), so one answer acts once. respond-input writes the
    record just after the run takes the answer, so a missing record is asked
    for again, for up to *wait_s* seconds. A missing record, another thread,
    another member, another text and a fault are each False.
    """
    if not (organization_id and request_id and responder and thread_id):
        return False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(wait_s, 0.0)
    stored: Any = None
    try:
        from acb_common.tenant_redis import organization_scope

        while True:
            with organization_scope(str(organization_id)):
                stored = await _tenant_redis().getdel(_record_key(thread_id, request_id))
            if stored or loop.time() >= deadline:
                break
            await asyncio.sleep(_TAKE_STEP_S)
    except Exception as exc:
        _log.warning("reply_card.take_failed", error=type(exc).__name__)
        return False
    if isinstance(stored, bytes):
        stored = stored.decode("utf-8", errors="replace")
    if not isinstance(stored, str) or not stored:
        return False
    expected = reply_answer_digest(request_id, responder, answer, thread_id)
    return hmac.compare_digest(stored, expected)
