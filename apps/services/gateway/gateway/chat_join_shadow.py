"""The shadow join match of a new chat (WS-CO CO-4).

Spec: ``project-docs/specs/chat_organization.md`` §7.1 (the outcomes), §7.2
(what may join), §10 (``chat_join_log``) and §11 row CO-4. Migration:
``infra/postgres/<n>_chat_join_log.sql``, found by its content.

The first message of a new chat with Mira asks one question: would this
message join one of the member's own chats, carry its context, ask, or start
clean? CO-4 answers it in the shadow. It writes ONE row of ``chat_join_log``
and shows the member nothing. One week of rows on Fracktal tunes the 14 days
and the score bands before CO-5 shows a chip (D163.7).

This is the ONE module that runs SQL on ``chat_join_log``. The member purge
(``routes/admin/members.py``, key ``chat_join_rows``) deletes the rows of a
leaver by its own clause. Fences: ``tests/unit/test_chat_join_shadow.py`` and
``tests/unit/test_chat_join_shadow_r8.py`` (R8).

The rules:

* **No text of the member is kept.** The run route turns the first message
  into a :data:`Fingerprint` at once (:func:`fingerprint`): a set of short
  hashes of its words. The background task gets the hashes only. No other
  function takes the message, and no column holds a text. ``reason`` and
  ``would_join_blocked_by`` are codes from fixed lists.
* **Stored data only.** :func:`decide` is pure. It reads the title of each
  candidate chat, its stored summary (through
  ``assistant_summaries.list_for_member``, the one reader), and the titles of
  the records that the chat made (``artifacts`` and ``reminders``). It calls
  no model and no embedding.
* **Records first** (D163.4). A chat that made a record that the first message
  names outranks a chat with similar words.
* **The member is the run's, the org is the run's.** The route passes the
  address of the session and its bound org, never a request field (R5, R11).
  The background task opens its own ``tenant_session(org)``.
* **Best effort.** The task has :data:`TIMEOUT_S` in all. It swallows each
  error and logs only its type. It never slows the first token.
* **The switch.** ``CHAT_JOIN_SHADOW_ORGS`` lists the orgs.
  :func:`shadow_allowed` is its one reader, and it fails closed.

Not built here: the held mark of a candidate. Its one reader
(``acb_skills.egress.session_mark_state``) refreshes the mark in Redis, and a
shadow must change nothing. So the I/O path reads each candidate as not held,
and :func:`decide` still bars a held candidate for CO-5.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from acb_common import get_logger
from acb_llm.model_limits import DEFAULT_CONTEXT_WINDOW
from sqlalchemy import text

from gateway.assistant_summaries import (
    ELIGIBLE_SQL,
    NOT_OFF_SQL,
    OPEN_RUN_SQL,
    list_for_member,
    member_switch_on,
)
from gateway.rooms import SOLO_ROOM_SQL

_log = get_logger("chat.join_shadow")

#: The table. Each statement of this module names it through this constant.
TABLE = "chat_join_log"

# ── The defaults that CO-4 tunes (§7.1) ─────────────────────────────────────

#: A chat joins only when its last activity is this recent (§7.1).
JOIN_DAYS = 14
#: The match reads the member's chats with activity in this window. An older
#: match can only carry.
SCAN_DAYS = 60
#: The most chats that one match reads.
SCAN_MAX = 30
#: The score bands. At or above STRONG the match is sure, between UNSURE and
#: STRONG it asks, and between RELATED and UNSURE it carries.
STRONG_BAND = 0.6
UNSURE_BAND = 0.4
RELATED_BAND = 0.2
#: A record matches when the first message holds this share of the words of
#: its title, and at least RECORD_WORDS_MIN of them.
RECORD_SHARE = 0.6
RECORD_WORDS_MIN = 2
#: The fewest shared words that give a words score.
WORDS_MIN = 2
#: The score of a record hit, before the words add to it. A record hit
#: always outranks similar words (D163.4).
RECORD_FLOOR = 0.9
#: The most that similar words alone can score.
WORDS_CEILING = 0.8
#: A chat that fills this share of the context window may not join (§7.2).
CONTEXT_FULL = 0.75
#: The window and the characters of one token. The window is the default of
#: ``acb_llm.model_limits``, and the context ring of the chat counts 4
#: characters to a token (``lib/tokenCount.ts``).
CONTEXT_TOKENS = DEFAULT_CONTEXT_WINDOW
CHARS_PER_TOKEN = 4
#: The most records that one match reads.
RECORDS_MAX = 200
#: The time of the whole task, in seconds.
TIMEOUT_S = 5.0

# ── The codes (the CHECKs of the migration hold the same lists) ─────────────

Outcome = Literal["join", "carry", "ask", "clean"]
Band = Literal["strong", "unsure", "related", "none"]
Reason = Literal["no_candidates", "no_match", "same_record", "same_words",
                 "unsure", "related", "older", "blocked"]
Bar = Literal["not_solo", "archived", "live_run", "open_ask", "context_full",
              "held", "not_eligible"]

OUTCOMES: tuple[str, ...] = ("join", "carry", "ask", "clean")
BANDS: tuple[str, ...] = ("strong", "unsure", "related", "none")
REASONS: tuple[str, ...] = ("no_candidates", "no_match", "same_record", "same_words",
                            "unsure", "related", "older", "blocked")
#: The bars of §7.2, in the order that :func:`first_bar` tests them.
BARS: tuple[str, ...] = ("not_solo", "archived", "live_run", "open_ask",
                         "context_full", "held", "not_eligible")

# ── The fingerprint ─────────────────────────────────────────────────────────

#: A set of short hashes of the words of a text. It never holds a word.
Fingerprint = frozenset[str]

#: The most characters of one text that :func:`fingerprint` reads.
FINGERPRINT_CHARS = 4000
#: The shortest word that counts.
WORD_MIN = 3
_WORD = re.compile(r"[^\W\d_]+")
#: Common words that say nothing about a subject.
STOP_WORDS: frozenset[str] = frozenset(["the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had", "her", "was", "one", "our", "out", "has", "him", "his", "how", "man", "new", "now", "old", "see", "two", "way", "who", "its", "did", "get", "may", "say", "she", "too", "use", "that", "with", "have", "this", "will", "your", "from", "they", "know", "want", "been", "good", "much", "some", "time", "very", "when", "come", "here", "just", "like", "long", "make", "many", "more", "only", "over", "such", "take", "than", "them", "well", "were", "what", "about", "after", "again", "also", "could", "each", "even", "first", "into", "most", "other", "should", "their", "there", "these", "thing", "think", "those", "through", "where", "which", "while", "would", "please", "thanks", "thank", "help", "need", "tell", "show", "give", "find", "hello", "okay", "yes", "lets", "today", "tomorrow", "yesterday"])


def _stem(word: str) -> str:
    """A plain stem: a plural ``s`` goes, so ``invoices`` meets ``invoice``."""
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _hash(word: str) -> str:
    return hashlib.blake2b(word.encode("utf-8"), digest_size=8).hexdigest()


def fingerprint(value: Any) -> Fingerprint:
    """The :data:`Fingerprint` of *value*: a hash of each word that counts.

    It is pure, and the ONE function of this module that takes a text. A word
    counts when it has :data:`WORD_MIN` letters or more and is not in
    :data:`STOP_WORDS`. A digit never counts. The run route calls it at once on
    the first message, so the background task never holds the words.
    """
    found: set[str] = set()
    for raw in _WORD.findall(str(value or "")[:FINGERPRINT_CHARS].lower()):
        if len(raw) < WORD_MIN or raw in STOP_WORDS:
            continue
        found.add(_hash(_stem(raw)))
    return frozenset(found)


# ── The pure match (§7.1, §7.2) ─────────────────────────────────────────────


@dataclass(frozen=True)
class Candidate:
    """One chat of the member that the first message may join.

    Each text is a :data:`Fingerprint`. The flags are the bars of §7.2.
    """

    session_id: str
    #: The hours from the last activity of the chat to the match.
    idle_hours: int
    title: Fingerprint = frozenset()
    summary: Fingerprint = frozenset()
    #: One fingerprint for the title of each record that the chat made.
    records: tuple[Fingerprint, ...] = ()
    solo: bool = True
    archived: bool = False
    live_run: bool = False
    open_ask: bool = False
    #: The share of the context window that the chat fills.
    context_share: float = 0.0
    held: bool = False
    #: ``assistant_summaries.ELIGIBLE_SQL``: Mira on the web, not archived,
    #: not deleted by the member, solo, and no other agent in the chat.
    eligible: bool = True


@dataclass(frozen=True)
class Decision:
    """What the match would do. Codes, numbers and one id only."""

    outcome: Outcome
    candidate_id: str | None
    candidate_count: int
    score: float | None
    band: Band
    record_hit: bool
    idle_hours: int | None
    reason: Reason
    blocked_by: Bar | None


def first_bar(c: Candidate) -> Bar | None:
    """The first bar of §7.2 that stops a join of *c*, or None.

    §7.2 lists four: solo, not archived, no live run and no open ask, and less
    than 75 % of the context window. A held chat and a chat that a summary may
    not cover (another agent, or a summary that the member deleted) carry too.
    """
    if not c.solo:
        return "not_solo"
    if c.archived:
        return "archived"
    if c.live_run:
        return "live_run"
    if c.open_ask:
        return "open_ask"
    if c.context_share >= CONTEXT_FULL:
        return "context_full"
    if c.held:
        return "held"
    if not c.eligible:
        return "not_eligible"
    return None


def record_hit(first: Fingerprint, c: Candidate) -> bool:
    """True when the first message names a record that *c* made: it holds
    :data:`RECORD_SHARE` of the words of the record's title, and at least
    :data:`RECORD_WORDS_MIN` of them."""
    for rec in c.records:
        shared = len(rec & first)
        if (len(rec) >= RECORD_WORDS_MIN and shared >= RECORD_WORDS_MIN
                and shared / len(rec) >= RECORD_SHARE):
            return True
    return False


def words_score(first: Fingerprint, c: Candidate) -> float:
    """The share of the words of the first message that the title or the
    summary of *c* holds. Fewer than :data:`WORDS_MIN` shared words give 0."""
    if not first:
        return 0.0
    shared = len(first & (c.title | c.summary))
    if shared < WORDS_MIN:
        return 0.0
    return shared / len(first)


def score(first: Fingerprint, c: Candidate) -> tuple[bool, float]:
    """``(record_hit, score)`` of *c*. A record hit scores at least
    :data:`RECORD_FLOOR`, and words alone at most :data:`WORDS_CEILING`."""
    words = words_score(first, c)
    if record_hit(first, c):
        return True, round(RECORD_FLOOR + (1 - RECORD_FLOOR) * words, 3)
    return False, round(WORDS_CEILING * words, 3)


def band_of(points: float) -> Band:
    """The band of a score."""
    if points >= STRONG_BAND:
        return "strong"
    if points >= UNSURE_BAND:
        return "unsure"
    if points >= RELATED_BAND:
        return "related"
    return "none"


def decide(first: Fingerprint, candidates: Iterable[Candidate]) -> Decision:
    """The outcome of §7.1 for the first message *first* and the member's
    chats *candidates*. Pure: no clock, no SQL, no model.

    * A strong match of a recent chat that may join: **join**.
    * A match between the bands of ask and join, of a recent chat that may
      join: **ask**.
    * A related match, an older chat, or a chat that a bar of §7.2 stops:
      **carry**. ``blocked_by`` names the bar.
    * Nothing: **clean**.

    The best chat is the one with a record hit first (D163.4), then the
    higher score, then the more recent one.
    """
    pool = list(candidates)
    if not pool:
        return Decision("clean", None, 0, None, "none", False, None, "no_candidates", None)
    ranked: list[tuple[bool, float, int, Candidate]] = []
    for c in pool:
        hit, value = score(first, c)
        ranked.append((hit, value, -max(0, c.idle_hours), c))
    ranked.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    hit, value, _, best = ranked[0]
    band = band_of(value)
    if band == "none":
        return Decision("clean", None, len(pool), value, "none", False, None, "no_match", None)
    bar = first_bar(best)
    recent = max(0, best.idle_hours) <= JOIN_DAYS * 24
    outcome: Outcome
    reason: Reason
    if band == "related":
        outcome, reason = "carry", "related"
    elif bar is not None:
        outcome, reason = "carry", "blocked"
    elif not recent:
        outcome, reason = "carry", "older"
    elif band == "strong":
        outcome, reason = "join", ("same_record" if hit else "same_words")
    else:
        outcome, reason = "ask", "unsure"
    return Decision(outcome, best.session_id, len(pool), value, band, hit,
                    max(0, best.idle_hours), reason, bar)


# ── The switch ──────────────────────────────────────────────────────────────


def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def shadow_allowed(organization_id: str | None) -> bool:
    """True when ``CHAT_JOIN_SHADOW_ORGS`` holds the org, or holds ``*``.
    Default OFF.

    The one reader of the setting. It reads the settings on each call, so a
    reload (SIGHUP) applies a change with no restart. A fault reads as off.
    The org comes from the session, never from request input (R5).
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_join_shadow_orgs", "") or "")
    except Exception:
        return False
    listed = _parse_orgs(raw)
    return "*" in listed or org in listed


# ── The store (SQL) ─────────────────────────────────────────────────────────
#
# RLS of the caller's session binds the tenant, and each statement names the
# bound org beside it. Each statement filters on the member. No statement
# reads a message text: the context share reads only the length of each row.

#: The bound tenant, named beside RLS, as each store module names it.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"
#: The member of the chat `s`, as the key of the table: trimmed, lower case.
_OWNER = "lower(regexp_replace(s.user_id, '^[[:space:]]+|[[:space:]]+$', '', 'g'))"
#: True when the chat `s` holds no member row other than the turn `:turn`.
_FIRST_TURN = """NOT EXISTS (
        SELECT 1 FROM chat_message f
         WHERE f.session_id = s.id AND f.role = 'user' AND f.id <> :turn)"""

#: The new chat: the member's own, one that a summary may cover (Mira on the
#: web, solo, not archived), and this turn is its first message.
NEW_CHAT_SQL = f"""
SELECT s.id FROM chat_session AS s
 WHERE s.id = :sid
   AND {_OWNER} = :member
   AND {ELIGIBLE_SQL}
   AND {_FIRST_TURN}
"""

#: The member's other chats with Mira on the web, with recent activity,
#: newest first. Each bar of §7.2 is a column, so the match can log the bar
#: that stops a join. Named columns only.
CANDIDATES_SQL = f"""
SELECT s.id, s.title, s.updated_at,
       {SOLO_ROOM_SQL} AS solo,
       (s.archived_at IS NOT NULL) AS archived,
       {OPEN_RUN_SQL} AS live_run,
       EXISTS (SELECT 1 FROM chat_pending_ask a
                WHERE a.thread_id = s.id
                  AND a.organization_id = {_BOUND_ORG}
                  AND a.state IN ('open', 'parked')) AS open_ask,
       (SELECT COALESCE(sum(length(c.content)), 0) FROM chat_message c
         WHERE c.session_id = s.id) AS chars,
       {ELIGIBLE_SQL} AS eligible
  FROM chat_session AS s
 WHERE {_OWNER} = :member
   AND s.organization_id = {_BOUND_ORG}
   AND s.agent_name = 'orchestrator'
   AND s.channel IS NULL
   AND s.id <> :sid
   AND s.updated_at >= :since
   AND {NOT_OFF_SQL}
 ORDER BY s.updated_at DESC, s.id DESC
 LIMIT :lim
"""

#: The titles of the records that the chats `:sids` made, of the member only.
RECORDS_SQL = f"""
SELECT a.session_id AS sid, a.title FROM artifacts a
 WHERE a.session_id = ANY(CAST(:sids AS text[]))
   AND a.organization_id = {_BOUND_ORG}
   AND lower(a.owner_email) = :member
   AND a.status = 'active'
UNION ALL
SELECT r.source_session_id AS sid, r.title FROM reminders r
 WHERE r.source_session_id = ANY(CAST(:sids AS text[]))
   AND r.organization_id = {_BOUND_ORG}
   AND r.member_email = :member
 LIMIT :lim
"""

#: The one write. It selects the new chat under the bound tenant and the
#: rules of :data:`NEW_CHAT_SQL` again, because a foreign key check skips
#: RLS. A second write for the same chat does nothing (the key).
INSERT_SQL = f"""
INSERT INTO {TABLE} (session_id, member_email, outcome, candidate_session_id,
                     candidate_count, score, band, record_hit, candidate_idle_hours,
                     reason, would_join_blocked_by)
SELECT s.id, CAST(:member AS text), CAST(:outcome AS text), CAST(:candidate AS text),
       CAST(:count AS integer), CAST(:score AS numeric), CAST(:band AS text),
       CAST(:hit AS boolean), CAST(:idle AS integer), CAST(:reason AS text),
       CAST(:blocked AS text)
  FROM chat_session AS s
 WHERE s.id = :sid
   AND {_OWNER} = :member
   AND {ELIGIBLE_SQL}
   AND {_FIRST_TURN}
ON CONFLICT (session_id) DO NOTHING
RETURNING session_id
"""

#: The columns that the one write names. The fence compares them with the
#: migration.
WRITTEN_COLUMNS: tuple[str, ...] = (
    "session_id", "member_email", "outcome", "candidate_session_id", "candidate_count",
    "score", "band", "record_hit", "candidate_idle_hours", "reason",
    "would_join_blocked_by",
)


def member_key(email: str | None) -> str:
    """The key of the member in the table: the address, trimmed, in lower case."""
    return str(email or "").strip().lower()


def _hours(now: datetime, at: Any) -> int:
    if not isinstance(at, datetime):
        return 0
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return max(0, int((now - at).total_seconds() // 3600))


def candidates_from(rows: Iterable[Any], records: Mapping[str, list[Fingerprint]],
                    summaries: Mapping[str, Fingerprint], now: datetime) -> list[Candidate]:
    """The rows of :data:`CANDIDATES_SQL` as :class:`Candidate` values. Each
    title becomes a fingerprint here, and no text stays."""
    out: list[Candidate] = []
    for r in rows:
        sid = str(r.id)
        chars = int(r.chars or 0)
        out.append(Candidate(
            session_id=sid,
            idle_hours=_hours(now, r.updated_at),
            title=fingerprint(r.title),
            summary=summaries.get(sid, frozenset()),
            records=tuple(records.get(sid, ())),
            solo=bool(r.solo),
            archived=bool(r.archived),
            live_run=bool(r.live_run),
            open_ask=bool(r.open_ask),
            context_share=chars / CHARS_PER_TOKEN / CONTEXT_TOKENS,
            eligible=bool(r.eligible),
        ))
    return out


async def _summaries(db: Any, org: str, member: str) -> dict[str, Fingerprint]:
    """The fingerprint of the stored summary of each chat of the member.

    It reads through ``assistant_summaries``, the one reader, and only as
    ``recent`` reads for the briefing: the flag of PZ-33 for the org, and the
    member's switch "Keep chat summaries". Either off gives none.
    """
    from acb_common.assistant_profile import chat_summary_for

    if not chat_summary_for(org) or not await member_switch_on(db, org, member):
        return {}
    found: dict[str, Fingerprint] = {}
    for item in await list_for_member(db, member):
        found[item.session_id] = fingerprint(" ".join((item.text, *item.follow_ups)))
    return found


async def _match(db: Any, org: str, member: str, session_id: str, turn_id: str,
                 first: Fingerprint, now: datetime) -> Decision | None:
    """Read the member's chats and decide. None when the chat is not a new
    chat of the member, or this turn is not its first message."""
    params = {"sid": session_id, "member": member, "turn": turn_id}
    if (await db.execute(text(NEW_CHAT_SQL), params)).first() is None:
        return None
    # Spec §5 and §16.2: a member whose switch "Keep chat summaries" is off
    # never joins, and a chat that is `off` is never a candidate (the SQL
    # leaves it out). So nothing of the member's chats is read or logged.
    if not await member_switch_on(db, org, member):
        return None
    rows = (await db.execute(text(CANDIDATES_SQL), {
        "sid": session_id, "member": member,
        "since": now - timedelta(days=SCAN_DAYS), "lim": SCAN_MAX})).fetchall()
    records: dict[str, list[Fingerprint]] = {}
    if rows:
        found = (await db.execute(text(RECORDS_SQL), {
            "sids": [str(r.id) for r in rows], "member": member,
            "lim": RECORDS_MAX})).fetchall()
        for rec in found:
            records.setdefault(str(rec.sid), []).append(fingerprint(rec.title))
    summaries = await _summaries(db, org, member) if rows else {}
    return decide(first, candidates_from(rows, records, summaries, now))


def row_params(member: str, session_id: str, turn_id: str,
               d: Decision) -> dict[str, Any]:
    """The parameters of :data:`INSERT_SQL`. Codes, numbers and ids only."""
    return {
        "sid": session_id, "member": member, "turn": turn_id,
        "outcome": d.outcome, "candidate": d.candidate_id, "count": d.candidate_count,
        "score": d.score, "band": d.band, "hit": d.record_hit, "idle": d.idle_hours,
        "reason": d.reason, "blocked": d.blocked_by,
    }


async def _run(org: str, member: str, session_id: str, turn_id: str,
               first: Fingerprint, now: datetime) -> Decision | None:
    from acb_common.db import mark_background, release_background, tenant_session

    token = mark_background()
    try:
        async with tenant_session(org) as db:
            decision = await _match(db, org, member, session_id, turn_id, first, now)
        if decision is None:
            return None
        async with tenant_session(org) as db:
            wrote = (await db.execute(text(INSERT_SQL), row_params(
                member, session_id, turn_id, decision))).first()
        return decision if wrote is not None else None
    finally:
        release_background(token)


async def record_first_message(org: str | None, member: str | None, session_id: str | None,
                               turn_id: str | None, first: Fingerprint, *,
                               now: datetime | None = None) -> Decision | None:
    """Decide the outcome of the first message of a new chat, and write ONE
    row of ``chat_join_log``. Never raises.

    *org* and *member* are the run's, from the verified session. *first* is
    the :data:`Fingerprint` of the first message, never the words. *turn_id*
    is the id of the member's row of this turn, or empty. It answers the
    :class:`Decision` that it wrote, or None when it wrote nothing: the switch
    is off, the chat is not a new solo chat of the member, a row is there, the
    time ran out, or a step failed.
    """
    org_id = str(org or "").strip()
    who = member_key(member)
    sid = str(session_id or "").strip()
    if not (who and sid and shadow_allowed(org_id)):
        return None
    try:
        async with asyncio.timeout(TIMEOUT_S):
            decision = await _run(org_id, who, sid, str(turn_id or ""), frozenset(first),
                                  now or datetime.now(UTC))
    except Exception as exc:  # A shadow never stops a chat.
        _log.warning("chat_join_shadow.failed", error=type(exc).__name__)
        return None
    if decision is not None:
        _log.info("chat_join_shadow.logged", outcome=decision.outcome,
                  band=decision.band, reason=decision.reason,
                  blocked_by=decision.blocked_by, candidates=decision.candidate_count)
    return decision


#: The live tasks, so that no task is lost to the collector before it ends.
_TASKS: set[asyncio.Task[Any]] = set()


def schedule_first_message(org: str | None, member: str | None, session_id: str | None,
                           turn_id: str | None, first: Fingerprint) -> asyncio.Task[Any] | None:
    """Start :func:`record_first_message` as a task, and return at once.

    The run route calls it after the chat row exists. It never raises, and it
    starts nothing when the switch is off.
    """
    try:
        if not shadow_allowed(org):
            return None
        task = asyncio.get_running_loop().create_task(
            record_first_message(org, member, session_id, turn_id, first))
    except Exception as exc:  # A shadow never stops a chat.
        _log.warning("chat_join_shadow.not_started", error=type(exc).__name__)
        return None
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task
