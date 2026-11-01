"""The one reader and writer of ``chat_session.member_summary`` (WS-63 PZ-33a).

Spec: ``project-docs/specs/personalization.md`` §15.3 (D-PZ-25 and D-PZ-28)
and §15.9. Migration: ``infra/postgres/319_chat_session_member_summary.sql``
(319 at the merge, R1: 318 went to WS-66 RE-9b first).

A summary covers one solo chat with Mira on the web, and only its member
sees it. This module has three parts.

**The store.** :func:`read`, :func:`mark`, :func:`write_marked`,
:func:`turn_off`, :func:`clear_for_member` and :func:`clear_on_share` are the
only reads and writes of the column. Every other module calls them.
``tests/unit/test_chat_summary_private.py`` fails when another module names
the column, and when a module that is not on its list imports this one.

* Each function takes a session that the caller opened with
  ``acb_common.db.tenant_session(org)``, so RLS binds each statement to that
  tenant. :func:`clear_on_share` takes the sync ``acb_graph`` session of the
  room routes. No store function takes an org, opens a session or commits
  (R5). The one exception is :func:`seed`, below.
* Each caller checks the member. A statement finds the chat by its id only,
  and RLS binds the tenant only. So a caller that acts for a member checks
  that the chat is that member's own before it calls. The job of PZ-33b
  reads the member from ``chat_session.user_id`` under RLS, never from a
  payload (R11).
* There is no plain write. A summary reaches the column only through
  :func:`check_answer` and :func:`write_marked`. ⚠️ An :class:`Answer`
  must come only from :func:`check_answer`. The type checks the caps, and
  it cannot check the drops. So the review of PZ-33b must check that its
  caller runs :func:`check_answer` and then :func:`write_marked`, and builds
  no :class:`Answer` of its own.
* A write sets ``member_summary`` only. It never changes ``updated_at``,
  ``last_preview`` or ``message_count``. So the chat keeps its place in the
  list, and the idle clock does not move. ``routes/chat.py`` keeps the same
  rule for an archive.
* A summary that the member deleted reads ``{"v": 1, "off": true}``. No mark
  and no write changes it, because each of those statements holds the rule in
  its WHERE.
* The job of PZ-33b marks the chat before its model call (:func:`mark`). It
  writes the result with the mark in the WHERE (:func:`write_marked`). So a
  Delete, a Forget or a share during the call wins, and the write changes
  nothing.
* A clear (:func:`clear_for_member` for Forget and the purge,
  :func:`clear_on_share` for a chat that stops being solo) takes the text,
  the follow-ups and the mark away. A Delete stays ``off``. Each other chat
  keeps a waterline: ``through`` names its newest row. So no job summarises
  an old chat again until a new row comes.

**The answer checks.** Pure functions, with no I/O.

* :func:`member_texts`, :func:`member_words` and :func:`prompt_rows` choose
  what may reach the model. A row of the member counts only through
  ``acb_common.assistant_profile.typed_text``, so a pasted mail and a text of
  more than 1,500 characters stay out. A card answer stays out too, because
  it quotes the question that the model wrote
  (``gateway.chat_recovery.is_card_answer_text``). A row of Mira gives its
  ``content`` only. Tool events, reasoning, custom events, system rows and
  the brief row (``brief-<date>``) stay out.
* :func:`check_answer` parses the answer of the model and applies the caps:
  at most 3 sentences and 400 characters of text, and at most 3 follow-ups of
  120 characters each. :func:`_keep` gives the rule of a line, and the stored
  text as one string must pass it too. It calls the rules of
  ``acb_common.assistant_profile`` and copies none of them. An answer that
  does not parse gives None, so the job writes nothing.
* The strict digit rule (PZ-33b, :func:`digit_runs`): each character that is
  not a letter joins two digits, and a run of 6 digits or more must be a run
  that the member typed. Digits of each script count, after NFKD.

**The seed and the reads of the job (PZ-33b).** :func:`seed` is the
``worker.seed_hooks`` hook of the job queue, as ``brief/planner.seed`` is. It
takes the org from the worker and opens its own short sessions, because a
seed hook must. It reads the idle chats with :data:`IDLE_SQL`, and it
enqueues the kind :data:`KIND` with the payload ``{session_id, through}``
only. :func:`read_job_chat` is the read of the handler. Both SQL texts name
the column, so they live here. The handler, the prompt and the model call
live in ``gateway/chat_summary_job.py``.

The module imports no litellm and no streamed completion. The job makes the
one model call through ``acb_llm.context.acompletion_with_fallback`` with the
Router on (§15.3). Fences: ``tests/unit/test_chat_summary.py``,
``tests/unit/test_chat_summary_private.py`` and
``tests/unit/test_chat_summary_r8.py`` (R8).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger
from acb_common.assistant_profile import (
    APP_PREFERENCES,
    APP_READ_SQL,
    CONFIRM_DIGITS,
    MEMBERS_APP_SQL,
    TYPED_MAX,
    chat_summary_for,
    confirm_form_without_token,
    fold,
    grounded,
    is_sensitive,
    needs_confirm,
    sensitive_terms,
    stored_app_prefs,
    typed_text,
)
from acb_common.pg_text import storable
from sqlalchemy import text

from gateway.chat_recovery import is_card_answer_text
from gateway.rooms import SOLO_ROOM_SQL

#: The version of the stored shape.
VERSION = 1
#: The caps of a summary (§15.3): the text, its sentences, the follow-ups
#: and each follow-up.
TEXT_MAX = 400
SENTENCES_MAX = 3
FOLLOW_UPS_MAX = 3
FOLLOW_UP_MAX = 120
#: The most characters of chat text in one prompt. The newest rows go in
#: first (§15.3, "The model call").
INPUT_MAX = 12_000
#: The longest row id that a mark or a summary may name.
THROUGH_MAX = 200

#: The id of the brief row of a day, as ``routes/chat_brief.brief_message_id``
#: makes it. ``tests/unit/test_chat_summary.py`` holds the two together.
BRIEF_ROW = re.compile(r"^brief-\d{4}-\d{2}-\d{2}$")

#: The text between two typed rows of the member. An address, a link or a
#: number cannot cross it, so two rows never join into one number.
_ROW_BREAK = "\n;\n"
#: The end of a sentence, or a line break.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
#: The form of a stored time.
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
#: The fewest digits of a run that the member must have typed (the strict
#: digit rule of PZ-33b, :func:`digit_runs`). The profile guard counts the
#: same number of digits (``assistant_profile.CONFIRM_DIGITS``).
RUN_DIGITS = CONFIRM_DIGITS
#: A domain in words behind the end of a sentence: "acme. Example dot com".
#: ``needs_confirm`` reads a domain in words only after "at". It runs on
#: folded text.
_SPOKEN_DOT = re.compile(r"\w[.!?]\s+[^\W\d_]+\s+dot\s+[^\W\d_]{2,24}\b")


class SummaryShapeError(ValueError):
    """A value does not fit the stored shape. The store refuses it."""


# ── The answer checks (pure) ─────────────────────────────────────────────────


def _field(row: Any, name: str) -> Any:
    """The value *name* of *row*: a mapping, a SQLAlchemy row or an object."""
    if isinstance(row, Mapping):
        return row.get(name)
    mapping = getattr(row, "_mapping", None)
    if isinstance(mapping, Mapping):
        return mapping.get(name)
    return getattr(row, name, None)


def _key(value: Any) -> str:
    """An address as this module compares it: no outer space, lower case."""
    return str(value or "").strip().lower()


def is_brief_row(row: Any) -> bool:
    """True when *row* is the brief row of a day (``brief-<date>``)."""
    return BRIEF_ROW.match(str(_field(row, "id") or "")) is not None


def _is_member_row(row: Any, member: Any) -> bool:
    """True when *row* is a turn of *member*. In a solo chat only the member
    writes a ``user`` row. A row that names another author never counts.

    A card answer never counts. Its text quotes the question of the card,
    which the model wrote, so it grounds nothing (``routes/agent.py``)."""
    if _field(row, "role") != "user" or is_brief_row(row):
        return False
    if is_card_answer_text(str(_field(row, "content") or "").strip()):
        return False
    if _field(row, "author_kind") not in (None, "", "human"):
        return False
    author = _key(_field(row, "author_email"))
    return not author or author == _key(member)


def _is_mira_row(row: Any) -> bool:
    """True when *row* is a reply of the agent, and not the brief row."""
    if _field(row, "role") != "assistant" or is_brief_row(row):
        return False
    return _field(row, "author_kind") in (None, "", "agent")


def member_texts(rows: Iterable[Any], member: Any) -> list[str]:
    """The typed text of each row of *member*, oldest first.

    A row counts only through ``typed_text``. A pasted mail, a text of more
    than 1,500 characters and an empty row give nothing. The count of the
    list is the count of typed rows that the idle rule of PZ-33b reads."""
    out: list[str] = []
    for row in rows:
        if not _is_member_row(row, member):
            continue
        typed = typed_text(_field(row, "content")).strip()
        if typed:
            out.append(typed)
    return out


def member_words(rows: Iterable[Any], member: Any) -> str:
    """The typed words of *member* in the chat. :func:`check_answer` grounds
    a line against them. ``""`` grounds nothing."""
    return _ROW_BREAK.join(member_texts(rows, member))


def prompt_rows(rows: Iterable[Any], member: Any, *,
                limit: int = INPUT_MAX) -> list[tuple[str, str]]:
    """The rows that may reach the model, as ``(who, words)``, oldest first.

    *rows* are in time order, oldest first. *who* is ``"member"`` or
    ``"mira"``. A row of the member gives its typed text only. A row of Mira
    gives its ``content`` only, so no tool event, reasoning or custom event
    goes in. A system row and the brief row give nothing.

    The rows hold at most *limit* characters. The newest rows go in first,
    and the oldest row that goes in can lose its end."""
    chosen: list[tuple[str, str]] = []
    for row in rows:
        if _is_member_row(row, member):
            words = typed_text(_field(row, "content")).strip()
            who = "member"
        elif _is_mira_row(row):
            content = _field(row, "content")
            words = content.strip() if isinstance(content, str) else ""
            who = "mira"
        else:
            continue
        if words:
            chosen.append((who, words))
    kept: list[tuple[str, str]] = []
    room = max(0, int(limit))
    for who, words in reversed(chosen):
        if room <= 0:
            break
        part = words[:room]
        kept.append((who, part))
        room -= len(part)
    kept.reverse()
    return kept


def _one_line(value: str) -> str:
    """*value* with each format character taken out, such as a zero-width
    space, and each run of white space as one space."""
    plain = "".join(ch for ch in value if unicodedata.category(ch) != "Cf")
    return " ".join(plain.split())


def sentences(value: str) -> list[str]:
    """The sentences and lines of *value*, each on one line, none empty."""
    return [s for s in (_one_line(p) for p in _SENTENCE_END.split(value)) if s]


# ── The strict digit rule (PZ-33b) ──────────────────────────────────────────
#
# Three review rounds of PZ-33a each found a new mark between two digit groups
# that the rule of a joint missed, such as "98450!!!!12345" or "98450 , ,
# 12345". So PZ-33b reads digits in one way only: each character that is not a
# letter joins two digits. A letter is a character of the Unicode category L.
# A space, a mark, a symbol, an underscore, a format character and a line
# break each join. A run of RUN_DIGITS digits or more must be a run of the
# member's typed words, read the same way. So a time range, a date or a price
# of six digits or more that the member did not type drops too. The agent
# accepted that cost for a private summary of two or three lines.


def _read_digits(value: str) -> str:
    """*value* after NFKD, with each digit of any script as an ASCII digit.
    So a fullwidth, an Arabic-Indic, a Devanagari, a superscript and a
    circled digit each read as the digit that it shows."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", value):
        digit = unicodedata.digit(ch, None)
        out.append(ch if digit is None else str(digit))
    return "".join(out)


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch).startswith("L")


def digit_runs(value: Any) -> list[str]:
    """Each run of digits in *value*, in order, as ASCII digits only.

    The digits are read through :func:`_read_digits`. Each character between
    two digits that is not a letter joins them into one run. A letter ends a
    run. So "98450!!!!12345", "98450 , , 12345" and "98450\\n12345" are each
    the one run "9845012345", and "98450x12345" is two runs."""
    runs: list[str] = []
    run: list[str] = []
    for ch in _read_digits(str(value or "")):
        if "0" <= ch <= "9":
            run.append(ch)
        elif _is_letter(ch) and run:
            runs.append("".join(run))
            run = []
    if run:
        runs.append("".join(run))
    return runs


def _typed_runs(words: str) -> set[str]:
    """The digit runs of the member's typed words. Each typed row is read
    alone, so two rows never join into one run (:data:`_ROW_BREAK`)."""
    return {run for part in words.split(_ROW_BREAK) for run in digit_runs(part)}


def _long_runs(line: str) -> list[str]:
    return [run for run in digit_runs(line) if len(run) >= RUN_DIGITS]


def _runs_typed(line: str, words: str) -> bool:
    """True when each run of :data:`RUN_DIGITS` digits or more in *line* is a
    whole run of the member's typed words. The digit strings must be equal:
    a part of a longer typed run does not count."""
    long = _long_runs(line)
    return not long or set(long) <= _typed_runs(words)


def _one_number(text: str) -> str:
    """*text* with each digit run of :func:`digit_runs` as one number of
    ASCII digits: the characters between two digits of a run go. A joint
    that holds an "@" stays, so an address keeps its shape.

    ``grounded`` reads a number across a space, a dot, a dash and a bracket
    only. Both of its sides go through this function first, so a number
    that the member typed with other marks still matches."""
    out: list[str] = []
    held: list[str] = []
    in_run = False
    for ch in _read_digits(text):
        if "0" <= ch <= "9":
            if not (in_run and "@" not in held):
                out.extend(held)
            held = []
            out.append(ch)
            in_run = True
        elif _is_letter(ch):
            out.extend(held)
            held = []
            out.append(ch)
            in_run = False
        elif in_run:
            held.append(ch)
        else:
            out.append(ch)
    out.extend(held)
    return "".join(out)


def _one_number_words(words: str) -> str:
    """:func:`_one_number` of each typed row alone. The row break stays, so
    two rows never join into one number."""
    return _ROW_BREAK.join(_one_number(part) for part in words.split(_ROW_BREAK))


def _flagged(line: str) -> bool:
    """True when *line* holds a sensitive word, an address, a link, a run of
    :data:`RUN_DIGITS` digits or more (:func:`digit_runs`), or a domain in
    words behind the end of a sentence (:data:`_SPOKEN_DOT`)."""
    return (is_sensitive(line) or needs_confirm(line) or bool(_long_runs(line))
            or _SPOKEN_DOT.search(fold(line)) is not None)


def _keep(line: str, words: str) -> bool:
    """True when the summary may store *line*. A line that is not flagged
    stays. A flagged line stays only when each of these holds:

    * each long digit run of the line is a run of the typed words of the
      member (:func:`_runs_typed`, the strict digit rule);
    * the typed words hold the line (``grounded``). Both sides read each
      number through :func:`_one_number`, so a typed number matches in any
      marks;
    * they hold each sensitive term of the line (``sensitive_terms``). So 80%
      of the words of a line is not enough when the member never typed the
      sensitive word itself;
    * the line has no form that ``grounded`` cannot match as a token
      (``confirm_form_without_token``), such as an address in words or a
      domain of another script.

    The rule over-drops on purpose: a lost line costs little, and a kept one
    stores what the member did not say (D-PZ-16, D-PZ-28)."""
    if not _flagged(line):
        return True
    if not _runs_typed(line, words):
        return False
    if not grounded(_one_number(line), _one_number_words(words)):
        return False
    if not sensitive_terms(line) <= sensitive_terms(words):
        return False
    return not confirm_form_without_token(line)


def _passes(text_lines: list[str], follow: list[str], words: str) -> bool:
    """True when the text, the follow-ups and the two together each pass
    :func:`_keep` as one string. A number or an address can cross the end of
    a line, so a check of each line alone is not enough."""
    groups = (text_lines, follow, text_lines + follow)
    return all(_keep(" ".join(group), words) for group in groups if group)


@dataclass(frozen=True)
class Answer:
    """A checked summary: the text and the follow-ups. It refuses a value
    over the caps, so no caller can store one."""

    text: str
    follow_ups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or len(self.text) > TEXT_MAX:
            raise SummaryShapeError(f"text must be a string of {TEXT_MAX} characters or less")
        if len(sentences(self.text)) > SENTENCES_MAX:
            raise SummaryShapeError(f"text must hold {SENTENCES_MAX} sentences or less")
        items = self.follow_ups
        if not isinstance(items, tuple) or len(items) > FOLLOW_UPS_MAX:
            raise SummaryShapeError(f"follow_ups must be a tuple of {FOLLOW_UPS_MAX} or less")
        for item in items:
            if not isinstance(item, str) or not item or len(item) > FOLLOW_UP_MAX:
                raise SummaryShapeError(
                    f"each follow-up must be a string of 1 to {FOLLOW_UP_MAX} characters")
        if not self.text and not items:
            raise SummaryShapeError("a summary needs a text or a follow-up")


def _parse(raw: Any) -> dict[str, Any] | None:
    """The JSON object in *raw*, or None. A fence of code around the object
    is allowed, as the WhatsApp group summary allows it."""
    if not isinstance(raw, str):
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def check_answer(raw: Any, words: Any) -> Answer | None:
    """The checked summary in the model answer *raw*, or None.

    *raw* must hold one JSON object with ``text``, a string, and
    ``follow_ups``, a list of strings (it may be absent). Any other shape
    does not parse, and gives None, so the job writes nothing. *words* are
    the typed words of the member (:func:`member_words`).

    * The text keeps its first 3 sentences. A sentence that :func:`_keep`
      refuses drops. Then the sentences at the end drop until the text holds
      400 characters or less.
    * The follow-ups keep the first 3 items. An item that is not a string,
      that has more than 120 characters, or that :func:`_keep` refuses drops.
    * Then the joined text, the joined follow-ups and the two together must
      each pass :func:`_keep` (:func:`_passes`). If one fails, each flagged
      line drops, even a grounded one, and the check runs again. If it still
      fails, a form crosses the end of a line, and the answer gives None.
    * With no text and no follow-up left, the answer gives None.
    """
    data = _parse(raw)
    if data is None:
        return None
    body = data.get("text")
    items = data.get("follow_ups")
    if items is None:
        items = []
    if not isinstance(body, str) or not isinstance(items, list):
        return None
    said = str(words or "")
    kept = [line for line in sentences(body)[:SENTENCES_MAX] if _keep(line, said)]
    while kept and len(" ".join(kept)) > TEXT_MAX:
        kept.pop()
    follow: list[str] = []
    for item in items[:FOLLOW_UPS_MAX]:
        if not isinstance(item, str):
            continue
        line = _one_line(item)
        if line and len(line) <= FOLLOW_UP_MAX and _keep(line, said):
            follow.append(line)
    if not _passes(kept, follow, said):
        kept = [line for line in kept if not _flagged(line)]
        follow = [line for line in follow if not _flagged(line)]
        if not _passes(kept, follow, said):
            return None
    text_out = " ".join(kept)
    if not text_out and not follow:
        return None
    return Answer(text=text_out, follow_ups=tuple(follow))


# ── The stored value ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Stored:
    """The stored summary of one chat, as :func:`stored` reads it."""

    off: bool = False
    text: str = ""
    follow_ups: tuple[str, ...] = ()
    #: The id of the newest row that the summary covers, and its time.
    through: str | None = None
    through_ms: int | None = None
    at: str | None = None
    #: The intent mark of the job: the row id that it started on, and when.
    mark_through: str | None = None
    mark_at: str | None = None


def _opt_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def stored(raw: Any) -> Stored | None:
    """The stored value *raw* as a :class:`Stored`, or None for no summary.

    It reads leniently: a key of the wrong type reads as absent. A value that
    is not a JSON object reads as no summary."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    if raw.get("off") is True:
        return Stored(off=True)
    items = raw.get("follow_ups")
    follow = tuple(i for i in items if isinstance(i, str)) if isinstance(items, list) else ()
    ms = raw.get("through_ms")
    intent = raw.get("mark") if isinstance(raw.get("mark"), dict) else {}
    return Stored(
        text=raw["text"] if isinstance(raw.get("text"), str) else "",
        follow_ups=follow,
        through=_opt_text(raw.get("through")),
        through_ms=ms if isinstance(ms, int) and not isinstance(ms, bool) else None,
        at=_opt_text(raw.get("at")),
        mark_through=_opt_text(intent.get("through")),
        mark_at=_opt_text(intent.get("at")),
    )


def _through(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > THROUGH_MAX:
        raise SummaryShapeError(f"through must be a row id of 1 to {THROUGH_MAX} characters")
    return value


def _stamp(moment: Any) -> str:
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise SummaryShapeError("a time must carry a time zone")
    return moment.astimezone(UTC).strftime(_STAMP)


def _value(answer: Answer, *, through: str, through_ms: int, at: datetime) -> str:
    """The JSON text of a summary, as the column stores it."""
    if not isinstance(answer, Answer):
        raise SummaryShapeError("a summary must be a checked Answer")
    if not isinstance(through_ms, int) or isinstance(through_ms, bool) or through_ms < 0:
        raise SummaryShapeError("through_ms must be an integer of 0 or more")
    value = {
        "v": VERSION,
        "text": answer.text,
        "follow_ups": list(answer.follow_ups),
        "through": _through(through),
        "through_ms": through_ms,
        "at": _stamp(at),
    }
    return json.dumps(storable(value), ensure_ascii=False)


# ── The store (SQL) ──────────────────────────────────────────────────────────
#
# Each statement names the one column. None names `updated_at`,
# `last_preview` or `message_count`. RLS of the caller's session binds the
# tenant, and a statement finds the chat by its id, or by its member for a
# clear. The caller checks the member.

#: True when the member deleted the summary of the chat.
_OFF = "COALESCE(member_summary @> jsonb_build_object('off', true), false)"
#: The bound tenant, named beside RLS, as each store module names it.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"
#: The newest row of the chat `s`, as a waterline.
_NEWEST = """(SELECT m.{col} FROM chat_message m WHERE m.session_id = s.id
                 ORDER BY m.timestamp_ms DESC, m.id DESC LIMIT 1)"""
#: What a clear leaves: a Delete stays `off`. Each other chat keeps only a
#: waterline at its newest row, so no job summarises its old rows again.
_CLEARED = f"""CASE WHEN {_OFF}
            THEN jsonb_build_object('v', {VERSION}, 'off', true)
            ELSE jsonb_strip_nulls(jsonb_build_object(
                     'v', {VERSION},
                     'through', {_NEWEST.format(col="id")},
                     'through_ms', {_NEWEST.format(col="timestamp_ms")}))
       END"""

READ_SQL = "SELECT member_summary FROM chat_session WHERE id = :sid"

#: The intent mark of the job. It keeps the summary that is there, and a
#: chat that reads `off` does not change.
MARK_SQL = f"""
UPDATE chat_session
   SET member_summary =
       (CASE WHEN jsonb_typeof(member_summary) = 'object'
             THEN member_summary ELSE CAST('{{}}' AS jsonb) END)
       || jsonb_build_object(
              'v', {VERSION},
              'mark', jsonb_build_object('through', CAST(:through AS text),
                                         'at', CAST(:at AS text)))
 WHERE id = :sid
   AND NOT {_OFF}
RETURNING id
"""

#: The common part of the two writes: the summary in place of the one that
#: is there. It keeps the mark, and a chat that reads `off` does not change.
_WRITE_HEAD = f"""
UPDATE chat_session
   SET member_summary = CAST(:summary AS jsonb)
       || (CASE WHEN jsonb_typeof(member_summary -> 'mark') = 'object'
                THEN jsonb_build_object('mark', member_summary -> 'mark')
                ELSE CAST('{{}}' AS jsonb) END)
 WHERE id = :sid
   AND NOT {_OFF}
"""

#: The write of the job: it changes the chat only while the mark is the one
#: that the job wrote. So a Delete during the model call wins.
WRITE_MARKED_SQL = (
    _WRITE_HEAD
    + "   AND member_summary -> 'mark' ->> 'through' = CAST(:through AS text)\n"
    + "RETURNING id\n"
)

#: The Delete of the member. No later mark or write changes the chat.
OFF_SQL = f"""
UPDATE chat_session
   SET member_summary = jsonb_build_object('v', {VERSION}, 'off', true)
 WHERE id = :sid
RETURNING id
"""

#: Forget and the purge: each chat of the member in the bound org loses its
#: summary. A chat with no summary and no row does not change.
CLEAR_FOR_MEMBER_SQL = f"""
UPDATE chat_session AS s
   SET member_summary = {_CLEARED}
 WHERE lower(regexp_replace(s.user_id, '^[[:space:]]+|[[:space:]]+$', '', 'g')) = :member
   AND s.organization_id = {_BOUND_ORG}
   AND (s.member_summary IS NOT NULL
        OR EXISTS (SELECT 1 FROM chat_message m WHERE m.session_id = s.id))
RETURNING s.id
"""

#: A chat that stops being solo loses its summary: another party may now read
#: the chat, and the summary is for its member only.
CLEAR_ON_SHARE_SQL = f"""
UPDATE chat_session AS s
   SET member_summary = {_CLEARED}
 WHERE s.id = :sid
   AND s.member_summary IS NOT NULL
   AND NOT {SOLO_ROOM_SQL}
RETURNING s.id
"""


async def read(db: Any, session_id: str) -> Stored | None:
    """The summary of the chat *session_id*, or None for no chat and for a
    chat with no summary."""
    row = (await db.execute(text(READ_SQL), {"sid": session_id})).first()
    return None if row is None else stored(row[0])


async def mark(db: Any, session_id: str, *, through: str, at: datetime) -> bool:
    """Set the intent mark of the job on the chat: the newest row id that it
    starts on, and the time. The summary that is there stays. Answers False
    when no chat has the id, and when the chat reads ``off``."""
    row = (await db.execute(text(MARK_SQL), {
        "sid": session_id, "through": _through(through), "at": _stamp(at)})).first()
    return row is not None


async def write_marked(db: Any, session_id: str, answer: Answer, *, through: str,
                       through_ms: int, at: datetime) -> bool:
    """Store *answer* only while the mark of the chat names *through*.

    The mark is in the WHERE, so a Delete (:func:`turn_off`) after the mark
    wins, and this changes nothing. A newer mark wins too. Answers True when
    the summary is stored."""
    row = (await db.execute(text(WRITE_MARKED_SQL), {
        "sid": session_id,
        "through": _through(through),
        "summary": _value(answer, through=through, through_ms=through_ms, at=at),
    })).first()
    return row is not None


async def turn_off(db: Any, session_id: str) -> bool:
    """The Delete of the member: the chat reads ``{"v": 1, "off": true}``,
    and no later mark or write changes it. Answers False when no chat has
    the id. The caller checks that the chat is the member's own."""
    row = (await db.execute(text(OFF_SQL), {"sid": session_id})).first()
    return row is not None


async def clear_for_member(db: Any, member: str) -> int:
    """Clear each summary of *member* in the bound org, and answer the count.

    Forget (PZ-35) and the member purge call it. A chat that reads ``off``
    stays ``off``. Each other chat of the member keeps only a waterline at
    its newest row, so the job writes no summary of an old chat again. A new
    row starts a new summary while the member's switch is on. The member
    comes from the session or the member record, never from request input.
    """
    who = _key(member)
    if not who:
        raise SummaryShapeError("member must be a non-empty address")
    rows = (await db.execute(text(CLEAR_FOR_MEMBER_SQL), {"member": who})).fetchall()
    return len(rows)


def clear_on_share(s: Any, session_id: str) -> bool:
    """Clear the summary of the chat *session_id* when it is no longer solo.

    The room routes call it in their own transaction, with the sync
    ``acb_graph`` session, after a participant joins and after the visibility
    leaves ``private``. A solo chat does not change. A chat that reads
    ``off`` stays ``off``. Answers True when a summary was cleared.
    """
    return s.execute(text(CLEAR_ON_SHARE_SQL), {"sid": session_id}).first() is not None


# ── The job of PZ-33b: the kind, the seed and the read of the handler ────────
#
# Spec: personalization.md §15.3 ("When a chat is idle", "Which job writes
# it") and docs/job_queue.md §5a and §5c. The handler, the prompt and the
# model call are in gateway/chat_summary_job.py, which registers the kind
# and adds :func:`seed` to ``worker.seed_hooks``.

_log = get_logger("assistant.chat_summary")

#: The kind of the job queue that writes a summary (§15.3).
KIND = "assistant.chat_summary"
#: The one agent that a summary covers: Mira (§15.3).
AGENT = "orchestrator"
#: The tries of one job. A retry is safe, because the handler reads its mark
#: first and makes no second call (``docs/job_queue.md`` §5a).
ATTEMPTS = 3
#: A chat is idle this long after its newest row (D-PZ-26).
IDLE = timedelta(minutes=30)
#: The seed reads only the chats with a row in this window (§15.3).
WINDOW = timedelta(days=7)
#: The most jobs that one seed enqueues for one org (§15.3).
SUMMARY_SEED_MAX = 20
#: The most candidate chats that one seed reads. The typed-row rule runs in
#: Python, so the SQL reads more chats than the cap.
SEED_SCAN_MAX = 3 * SUMMARY_SEED_MAX
#: The newest short rows of the member that the seed reads for each chat.
SEED_ROWS = 20
#: The fewest typed rows of the member that a chat needs (§15.3).
TYPED_ROWS_MIN = 2
#: The most rows of one chat that the handler reads, the newest first.
JOB_ROWS_MAX = 400
#: The app and the key of the member's switch, "Keep chat summaries"
#: (PZ-35, §15.5). A missing value reads as on.
SWITCH_APP = "chat"
SWITCH_KEY = "summaries"

#: True when the chat `s` is one that a summary may cover: a chat of the
#: bound org with Mira on the web, not archived, not deleted by the member
#: (`off`), and solo (`rooms.SOLO_ROOM_SQL`). It binds no parameter.
ELIGIBLE_SQL = f"""(
        s.organization_id = {_BOUND_ORG}
        AND s.agent_name = 'orchestrator'
        AND s.channel IS NULL
        AND s.archived_at IS NULL
        AND NOT COALESCE(s.member_summary @> jsonb_build_object('off', true), false)
        AND {SOLO_ROOM_SQL}
    )"""

#: True when an agent row of the chat `s` is not sealed: a run is open
#: (migration 220, `run_final_at`).
OPEN_RUN_SQL = """EXISTS (
        SELECT 1 FROM chat_message o
         WHERE o.session_id = s.id AND o.role = 'assistant'
           AND o.run_final_at IS NULL)"""

#: The idle chats of the bound org that need a summary, the newest first.
#: The newest row is between `:since_ms` and `:idle_ms`, no run is open, the
#: newest row id is neither `through` nor `mark.through`, and the chat holds
#: `:typed_min` short user rows or more. The seed then counts the typed rows
#: of the member in Python (`member_texts`).
IDLE_SQL = f"""
SELECT s.id, s.user_id, newest.id AS newest_id, newest.timestamp_ms AS newest_ms
  FROM chat_session s
 CROSS JOIN LATERAL (
       SELECT m.id, m.timestamp_ms FROM chat_message m
        WHERE m.session_id = s.id
        ORDER BY m.timestamp_ms DESC, m.id DESC
        LIMIT 1) AS newest
 WHERE {ELIGIBLE_SQL}
   AND newest.timestamp_ms BETWEEN :since_ms AND :idle_ms
   AND newest.id IS DISTINCT FROM (s.member_summary ->> 'through')
   AND newest.id IS DISTINCT FROM (s.member_summary -> 'mark' ->> 'through')
   AND NOT {OPEN_RUN_SQL}
   AND (SELECT count(*) FROM chat_message u
         WHERE u.session_id = s.id AND u.role = 'user'
           AND length(u.content) <= :typed_max) >= :typed_min
 ORDER BY newest.timestamp_ms DESC, s.id
 LIMIT :scan
"""

#: The newest short user rows of each chat in `:sids`, `:rows` for each
#: chat at most. RLS binds the tenant.
SEED_ROWS_SQL = """
SELECT r.session_id, r.id, r.role, r.content, r.author_kind, r.author_email
  FROM (SELECT m.session_id, m.id, m.role, m.content, m.author_kind, m.author_email,
               row_number() OVER (PARTITION BY m.session_id
                                  ORDER BY m.timestamp_ms DESC, m.id DESC) AS k
          FROM chat_message m
         WHERE m.session_id = ANY(CAST(:sids AS text[])) AND m.role = 'user'
           AND length(m.content) <= :typed_max) AS r
 WHERE r.k <= :rows
"""

#: The chat of one job: its member, the stored summary, and the two rules
#: as booleans. A chat of another org gives no row (RLS and the bound org).
JOB_CHAT_SQL = f"""
SELECT s.user_id, s.member_summary, {ELIGIBLE_SQL} AS eligible,
       {OPEN_RUN_SQL} AS open_run
  FROM chat_session s
 WHERE s.id = :sid
   AND s.organization_id = {_BOUND_ORG}
"""

#: The newest `:rows` rows of one chat, oldest first, with the columns that
#: the answer checks read and nothing else.
JOB_ROWS_SQL = """
SELECT t.id, t.role, t.content, t.author_kind, t.author_email, t.timestamp_ms
  FROM (SELECT m.id, m.role, m.content, m.author_kind, m.author_email, m.timestamp_ms
          FROM chat_message m
         WHERE m.session_id = :sid
         ORDER BY m.timestamp_ms DESC, m.id DESC
         LIMIT :rows) AS t
 ORDER BY t.timestamp_ms, t.id
"""


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def dedupe_key(session_id: str, through: str) -> str:
    """``summary:<session_id>:<through>`` (§15.3). A key longer than the queue
    takes (``queue.DEDUPE_CHARS``) becomes ``summary:`` and a SHA-256 of the
    pair, so the same pair always gives the same key."""
    from gateway.jobs.queue import DEDUPE_CHARS

    key = f"summary:{session_id}:{through}"
    if len(key) <= DEDUPE_CHARS:
        return key
    return "summary:" + hashlib.sha256(f"{session_id}\n{through}".encode()).hexdigest()


def switch_on(stored: Any) -> bool:
    """The member's switch "Keep chat summaries" in the stored ``apps.chat``
    object of the profile. A missing value or a value of the wrong type reads
    as on. When the profile declares the key (PZ-35, ``APP_PREFERENCES``),
    its reader and its default decide."""
    if SWITCH_KEY in APP_PREFERENCES.get(SWITCH_APP, {}):
        return stored_app_prefs(stored, SWITCH_APP)[SWITCH_KEY] is True
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            return True
    return not (isinstance(stored, dict) and stored.get(SWITCH_KEY) is False)


async def member_switch_on(db: Any, org: str, member: str) -> bool:
    """The switch of *member* in *org*, read through the one row rule of the
    profile (``assistant_profile.APP_READ_SQL``). The caller resolved the
    member from ``chat_session.user_id``, never from a payload."""
    row = (await db.execute(text(APP_READ_SQL), {
        "app": SWITCH_APP, "org": org, "m": member})).first()
    return switch_on(None if row is None else row[1])


async def member_switches(db: Any, org: str) -> dict[str, bool]:
    """The switch of each ACTIVE member of *org*, by address in lower case
    (``assistant_profile.MEMBERS_APP_SQL``). A member who is not active is
    not in the map."""
    rows = (await db.execute(text(MEMBERS_APP_SQL), {"app": SWITCH_APP, "org": org})).all()
    return {_key(r[0]): switch_on(r[1]) for r in rows}


@dataclass(frozen=True)
class JobChat:
    """The chat of one job, as :func:`read_job_chat` reads it."""

    member: str
    stored: Stored | None
    #: :data:`ELIGIBLE_SQL`: Mira on the web, not archived, not ``off``, solo.
    eligible: bool
    #: :data:`OPEN_RUN_SQL`: an agent row of the chat is not sealed.
    open_run: bool
    #: The newest rows of the chat, oldest first (:data:`JOB_ROWS_MAX`).
    rows: tuple[Mapping[str, Any], ...]

    @property
    def newest_id(self) -> str | None:
        return str(self.rows[-1]["id"]) if self.rows else None

    @property
    def newest_ms(self) -> int | None:
        return int(self.rows[-1]["timestamp_ms"]) if self.rows else None


async def read_job_chat(db: Any, session_id: str, *,
                        rows: int = JOB_ROWS_MAX) -> JobChat | None:
    """The chat *session_id* for the job, or None when the bound org has no
    such chat. The member is ``chat_session.user_id``, with no outer space and
    in lower case."""
    head = (await db.execute(text(JOB_CHAT_SQL), {"sid": session_id})).first()
    if head is None:
        return None
    found = (await db.execute(text(JOB_ROWS_SQL), {"sid": session_id, "rows": rows})).all()
    return JobChat(
        member=_key(head.user_id),
        stored=stored(head.member_summary),
        eligible=bool(head.eligible),
        open_run=bool(head.open_run),
        rows=tuple(dict(r._mapping) for r in found),
    )


async def _gone(org: str, member: str) -> bool:
    """True when *member* left *org* (``my_day_loop.departed``). A failed read
    skips the member for this tick, and the next tick reads again."""
    from gateway.my_day_loop import departed

    try:
        return await departed(org, member) is not None
    except Exception as exc:  # a failed read does not say that they left
        _log.warning("assistant.chat_summary_member_check_failed", error=type(exc).__name__)
        return True


async def holds_mail(org: str, session_id: str) -> bool:
    """True when the chat can hold mail text, so it gets no call and no mark.

    ``acb_skills.egress.run_holds_mail`` is the one predicate of the chat
    memory stores. A reply of Mira can quote a mail that she read, and
    :func:`check_answer` cannot tell that from her own words. So a chat with
    the mail mark gets no summary at all (the review of PZ-33b, P1).

    *org* comes from the worker. It is bound only for the read, as
    ``chat_fold._run_holds_mail`` binds it, because a frame with no tenant
    reads as clear. A fault reads as mail, so the check fails closed."""
    from acb_common.db import bind_tenant, release_tenant

    token = None
    try:
        from acb_skills.egress import run_holds_mail

        token = bind_tenant(str(org))
        return await run_holds_mail(AGENT, {"session_id": session_id})
    except Exception as exc:  # fail closed: the chat holds mail
        _log.warning("assistant.chat_summary_mail_check_failed", error=type(exc).__name__)
        return True
    finally:
        release_tenant(token)


async def seed(org: str, now: datetime) -> None:
    """The ``worker.seed_hooks`` hook of PZ-33b (§15.3, "Which job writes it").

    It runs once for each org and 15-minute bucket. It enqueues one job of
    :data:`KIND` for each idle chat, :data:`SUMMARY_SEED_MAX` at most, with
    the payload ``{session_id, through}`` and the dedupe key of
    :func:`dedupe_key`. It enqueues nothing when the flag is off for the org
    (``chat_summary_for``), and nothing when ``acb_llm.routed.routing_is_on()``
    is False: a call off the Router writes no usage row and draws no credit.

    A chat gets a job when :data:`IDLE_SQL` selects it, its member is active
    with the switch on (:func:`member_switches`), the newest short rows hold
    :data:`TYPED_ROWS_MIN` typed rows of the member (:func:`member_texts`),
    ``my_day_loop.departed`` says that the member did not leave, and the chat
    holds no mail (:func:`holds_mail`)."""
    from acb_common.db import tenant_session
    from acb_llm import routed

    from gateway.jobs import queue

    if not chat_summary_for(org) or not routed.routing_is_on():
        return
    async with tenant_session(org) as db:
        found = (await db.execute(text(IDLE_SQL), {
            "since_ms": _ms(now - WINDOW), "idle_ms": _ms(now - IDLE),
            "typed_max": TYPED_MAX, "typed_min": TYPED_ROWS_MIN,
            "scan": SEED_SCAN_MAX})).all()
        if not found:
            return
        typed: dict[str, list[Any]] = {}
        for row in (await db.execute(text(SEED_ROWS_SQL), {
                "sids": [str(c.id) for c in found], "typed_max": TYPED_MAX,
                "rows": SEED_ROWS})).all():
            typed.setdefault(str(row.session_id), []).append(row)
        switches = await member_switches(db, org)
    picked: list[Any] = []
    left: dict[str, bool] = {}
    for chat in found:
        member = _key(chat.user_id)
        newest = str(chat.newest_id)
        if "@" not in member or not switches.get(member, False) or len(newest) > THROUGH_MAX:
            continue
        if len(member_texts(typed.get(str(chat.id), []), member)) < TYPED_ROWS_MIN:
            continue
        if member not in left:
            left[member] = await _gone(org, member)
        if left[member] or await holds_mail(org, str(chat.id)):
            continue
        picked.append(chat)
        if len(picked) >= SUMMARY_SEED_MAX:
            break
    if not picked:
        return
    new = 0
    async with tenant_session(org) as db:
        for chat in picked:
            sid, through = str(chat.id), str(chat.newest_id)
            out = await queue.enqueue(db, KIND, {"session_id": sid, "through": through},
                                      dedupe_key=dedupe_key(sid, through),
                                      max_attempts=ATTEMPTS, now=now)
            new += int(out.created)
    _log.info("assistant.chat_summary_seeded", chats=len(picked), new=new)
