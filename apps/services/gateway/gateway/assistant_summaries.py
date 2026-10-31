"""The one reader and writer of ``chat_session.member_summary`` (WS-63 PZ-33a).

Spec: ``project-docs/specs/personalization.md`` §15.3 (D-PZ-25 and D-PZ-28)
and §15.9. Migration: ``infra/postgres/319_chat_session_member_summary.sql``
(319 at the merge, R1: 318 went to WS-66 RE-9b first).

A summary covers one solo chat with Mira on the web, and only its member
sees it. This module has two parts.

**The store.** :func:`read`, :func:`mark`, :func:`write_marked`,
:func:`turn_off`, :func:`clear_for_member` and :func:`clear_on_share` are the
only reads and writes of the column. Every other module calls them.
``tests/unit/test_chat_summary_private.py`` fails when another module names
the column, and when a module that is not on its list imports this one.

* Each function takes a session that the caller opened with
  ``acb_common.db.tenant_session(org)``, so RLS binds each statement to that
  tenant. :func:`clear_on_share` takes the sync ``acb_graph`` session of the
  room routes. No function takes an org, opens a session or commits (R5).
* Each caller checks the member. A statement finds the chat by its id only,
  and RLS binds the tenant only. So a caller that acts for a member checks
  that the chat is that member's own before it calls. The job of PZ-33b
  reads the member from ``chat_session.user_id`` under RLS, never from a
  payload (R11).
* There is no plain write. A summary reaches the column only through
  :func:`check_answer` and :func:`write_marked`.
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

The module imports no litellm and no streamed completion. PZ-33b makes the
one model call through ``acb_llm.context.acompletion_with_fallback`` with the
Router on (§15.3). Fences: ``tests/unit/test_chat_summary.py``,
``tests/unit/test_chat_summary_private.py`` and
``tests/unit/test_chat_summary_r8.py`` (R8).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from acb_common.assistant_profile import (
    confirm_form_without_token,
    grounded,
    is_sensitive,
    needs_confirm,
    sensitive_terms,
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
#: A mark between two digits that ``needs_confirm`` does not read as a
#: number joint. "98450/12345" reads as one number for the check.
_DIGIT_JOINT = re.compile(r"(?<=\d)[/\\|_](?=\d)")


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
    if is_card_answer_text(str(_field(row, "content") or "")):
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


def _flagged(line: str) -> bool:
    """True when *line* holds a sensitive word, an address, a link or a long
    number. A slash, a bar or an underscore between two digits counts as a
    number joint here."""
    return (is_sensitive(line) or needs_confirm(line)
            or needs_confirm(_DIGIT_JOINT.sub("-", line)))


def _keep(line: str, words: str) -> bool:
    """True when the summary may store *line*. A line that is not flagged
    stays. A flagged line stays only when each of these holds:

    * the typed words of the member hold the line (``grounded``);
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
    if not grounded(line, words):
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
 WHERE lower(s.user_id) = :member
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
