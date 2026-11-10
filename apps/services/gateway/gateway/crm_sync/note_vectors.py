"""Vectors of CRM notes, for search by meaning (WS-53 CRM-Q27).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q27.

**What it does.** After a sync run, :func:`embed_after_run` takes the org's
new or changed notes and sends their text to the ONE embed door,
``gateway/kb_index/embed.embed``. Each note gets one ``crm_note_vectors``
row: the md5 of the embedded text, the vector and the time. Only
``GET /crm/notes/search`` (``routes/crm/note_search.py``) reads the table.

**When it runs.** Only for an org on ``CRM_NOTE_SEARCH_ORGS``
(``flags.note_search_allowed``). With the org outside the list,
:func:`enabled_for` answers False, and the scheduler starts no task: no SQL,
no member read and no embed call.

**What is new or changed.** The rule of CRM-Q26. A note with no row is new.
A note whose ``md5(coalesce(subject,'') || coalesce(body,''))`` differs from
the row's ``body_md5`` has changed. The step reads the newest first, by
``COALESCE(ext_modified_at, created_at)``. The text that it embeds is the
subject and the body, clipped to :data:`BODY_CLIP` (2,000) characters.

**What it costs.** At most :data:`STEP_LIMIT` (640) notes in one step, in
requests of :data:`NOTES_PER_REQUEST` (64), the most texts of one request of
the door. Each org has a cap of :data:`DAILY_REQUESTS` (80) REQUESTS for each
UTC day, so 5,120 notes. The cap counts in Redis BEFORE each request goes
out, as CRM-Q26 does, so a request that fails still counts. A Redis failure
embeds nothing: spend fails closed. The door writes no ``usage_event`` row,
so Metorite pays the vendor and no org pays credits (``knowledge_base.md``
§6.4). Its log line gives the token estimate of each call.

**The backlog allowance** (owner request, 2026-10-17). While more than
:data:`BACKLOG_THRESHOLD` (1,000) notes wait, the cap is
:data:`BACKLOG_DAILY_REQUESTS` (400) requests, so 25,600 notes, and one step
takes :data:`BACKLOG_STEP_LIMIT` (2,560) notes. So the first pass of an org
ends in one day. DEWiN's 17,519 notes are about 0.76M tokens, about USD 0.02.
The step counts the waiting notes up to the threshold plus one only, as
CRM-Q26 does, and it reserves each request against the cap that it read.

**Who pays.** Nobody, today. The check is still the payer check of CRM-Q26:
the ``connected_by`` of the org's Zoho connection, still an ACTIVE member of
THIS org (``my_day_loop.departed``). With no such member, the step skips,
logs a count, and embeds nothing. When the Router bills the door (WS-45 S5),
this check names the payer, and the call runs in ``job_member_scope``.

**A failed vector.** The door answers ``None`` for an error, a timeout and a
zero vector. A ``None`` counts one failure on the row, and a failure clears
the old vector, so a row never pairs a new md5 with the vector of an older
text. After :data:`MAX_FAILURES` (3) the note waits :data:`PARK_DAYS` (7)
days or until its text changes. The step also refuses a vector of the wrong
size or a zero vector itself, so it never stores one. A request of two or
more notes with no usable vector at all stops the step and writes NO
failure: the door is down, and the next run tries again. So an outage never
parks a batch of notes. A request of one note counts its failure, because
it cannot tell an outage from a bad note, and a bad note alone must park.

**A note with no text** (fix round 2 of 2026-10-17). The vendor refuses an
empty input for the whole request, so a note whose :func:`note_text` is
empty never goes out. The SQL leaves out a note of ASCII whitespace only
(:func:`_filled`), and the step marks any other empty note done with no
vector (:data:`_EMPTY_SQL`). An empty note is no error: it counts no failure,
and it waits again only when its text changes.

**No tenant text out.** A row holds a key, an md5, a vector and numbers. A
log line holds the org id, counts, the reason and the time.

**Sessions.** The step runs outside the run lock. It opens
``tenant_session(org)`` for each read and each write. No session stays open
across an embed call.

Fences: ``tests/unit/test_crm_note_vectors.py`` and
``tests/unit/test_crm_note_vectors_r8.py`` (R8).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from acb_common import get_logger
from acb_common.db import bind_tenant, release_tenant, tenant_session
from sqlalchemy import text

from gateway.crm_sync import note_labels
from gateway.crm_sync.note_labels import MAX_FAILURES, PARK_DAYS
from gateway.kb_index import embed as embed_door
from gateway.kb_index.index import vector_literal
from gateway.routes.crm import flags

_log = get_logger("crm.note_vectors")

#: The name of each call in the logs, and the agent of the job scope.
FEATURE = "crm.note_vectors"

#: The app that a call will bill, when the door bills (WS-45 S5).
MODULE_SLUG = "crm"

#: The texts of one request: the most that one request of the door holds.
NOTES_PER_REQUEST = embed_door.BATCH

#: The notes of one step: 10 requests (§18.11.2 item 9).
STEP_LIMIT = 10 * NOTES_PER_REQUEST

#: The requests of one org in one UTC day (§18.11.2 item 9), so 5,120 notes.
DAILY_REQUESTS = 80

#: The requests of one org in one UTC day while more than
#: :data:`BACKLOG_THRESHOLD` notes wait (owner request, 2026-10-17), so 25,600
#: notes. The first pass of an org then ends in one day, not in four. At the
#: threshold or below, the normal caps apply.
BACKLOG_DAILY_REQUESTS = 400
BACKLOG_THRESHOLD = 1000

#: The notes of one step while the backlog holds: 40 requests.
BACKLOG_STEP_LIMIT = 40 * NOTES_PER_REQUEST

#: The text of a note: the subject and the body, clipped to this many
#: characters, about 500 tokens (§18.11.2 item 8).
BODY_CLIP = 2000

#: The Redis namespace of the request counter, and its life (26 hours, as
#: CRM-Q26 and My Day).
NAMESPACE = "crm_note_vectors"
REQUESTS_TTL_S = 26 * 3600


# ── The gate ────────────────────────────────────────────────────────────────


def enabled_for(org: str) -> bool:
    """Whether the step may run for ``org``: it is on ``CRM_NOTE_SEARCH_ORGS``.

    The cheap gate. It reads the settings and nothing else, so a dark box
    starts no task, sends no SQL and makes no embed call."""
    return flags.note_search_allowed(org)


# ── The text and the vector ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Note:
    """One note to embed. ``body_md5`` is the md5 the step read with it."""

    id: str
    subject: str | None
    body: str | None
    body_md5: str


def note_text(note: Note) -> str:
    """The text that the door embeds: the subject and the body, clipped."""
    parts = (str(note.subject or "").strip(), str(note.body or "").strip())
    return "\n".join(p for p in parts if p)[:BODY_CLIP]


def usable(vector: Any) -> list[float] | None:
    """The vector, or ``None`` for one of the wrong size, one that is not a
    number, and a zero vector. The step never stores a zero vector, also when
    a door other than :func:`embed_door.embed` gives one."""
    if not isinstance(vector, list | tuple) or len(vector) != embed_door.EMBED_DIMS:
        return None
    try:
        out = [float(x) for x in vector]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in out) or not any(out):
        return None
    return out


# ── SQL ─────────────────────────────────────────────────────────────────────

#: The md5 of the embedded text. The expression of CRM-Q26, in the select
#: and in the anti-join, so a note that did not change is never sent again.
_MD5 = "md5(coalesce(a.subject, '') || coalesce(a.body, ''))"

def _filled(column: str) -> str:
    """True in SQL when *column* holds more than ASCII whitespace: the rule
    of :func:`note_text`, which strips each part. ``btrim`` takes the six
    ASCII whitespace characters (``\\x0B`` is the vertical tab, because an
    E-string reads ``\\v`` as a plain ``v``). Python's ``strip`` also takes
    Unicode spaces. A note that only that difference admits gets the empty
    mark of :data:`_EMPTY_SQL`, so the two rules never disagree twice."""
    return f"btrim(coalesce({column}, ''), E' \\t\\n\\r\\f\\x0B') <> ''"


#: A note waits when it has no row, when its text changed, or when it failed
#: fewer than :data:`MAX_FAILURES` times. A note that failed that often waits
#: again after :data:`PARK_DAYS` days, or as soon as its text changes. A note
#: of whitespace only never waits (fix round 2 of 2026-10-17).
_WAITING = (
    "FROM crm_activities a "
    "LEFT JOIN crm_note_vectors v ON v.activity_id = a.id "
    "WHERE a.organization_id = CAST(:o AS uuid) AND a.type = 'note' "
    f"AND ({_filled('a.subject')} OR {_filled('a.body')}) "
    f"AND (v.activity_id IS NULL OR v.body_md5 <> {_MD5} "
    "OR (v.embedded_at IS NULL AND (v.failures < :max_failures "
    "OR v.failed_at < now() - make_interval(days => :park_days)))) "
)

#: The failures of a note whose text did not change. A failed note goes to
#: the back of the queue, so it never blocks the notes behind it.
_FAILURES_NOW = (
    f"CASE WHEN v.activity_id IS NOT NULL AND v.body_md5 = {_MD5} "
    "AND v.embedded_at IS NULL THEN v.failures ELSE 0 END"
)

_PENDING_SQL = (
    f"SELECT a.id::text AS id, a.subject, a.body, {_MD5} AS body_md5 "
    + _WAITING
    + f"ORDER BY {_FAILURES_NOW}, COALESCE(a.ext_modified_at, a.created_at) DESC, a.id "
    "LIMIT :n"
)

#: One vector for each note. ``INSERT … SELECT`` from the activity, so a note
#: deleted since the read writes nothing and meets no foreign key. A vector
#: clears the failures.
_VECTOR_SQL = (
    "INSERT INTO crm_note_vectors (organization_id, activity_id, body_md5, "
    "embedding, embedded_at, failures, failed_at) "
    "SELECT a.organization_id, a.id, :body_md5, CAST(:vec AS vector), now(), 0, NULL "
    "FROM crm_activities a "
    "WHERE a.id = CAST(:activity_id AS uuid) AND a.organization_id = CAST(:o AS uuid) "
    "ON CONFLICT (activity_id) DO UPDATE SET body_md5 = EXCLUDED.body_md5, "
    "embedding = EXCLUDED.embedding, embedded_at = EXCLUDED.embedded_at, "
    "failures = 0, failed_at = NULL"
)

#: One failure of a note. The count starts again at 1 when the text changed.
#: A failure keeps no vector of an older text: the conflict branch clears it
#: with the md5 it writes.
_FAILED_SQL = (
    "INSERT INTO crm_note_vectors (organization_id, activity_id, body_md5, "
    "embedding, embedded_at, failures, failed_at) "
    "SELECT a.organization_id, a.id, :body_md5, NULL, NULL, 1, now() "
    "FROM crm_activities a "
    "WHERE a.id = CAST(:activity_id AS uuid) AND a.organization_id = CAST(:o AS uuid) "
    "ON CONFLICT (activity_id) DO UPDATE SET "
    "failures = CASE WHEN crm_note_vectors.body_md5 = EXCLUDED.body_md5 "
    "AND crm_note_vectors.embedded_at IS NULL "
    "THEN crm_note_vectors.failures + 1 ELSE 1 END, "
    "embedding = NULL, embedded_at = NULL, failed_at = now(), "
    "body_md5 = EXCLUDED.body_md5"
)

#: The mark of a note with no text to embed (fix round 2 of 2026-10-17). An
#: empty note is not an error, so it counts no failure. The row says "done,
#: and no vector": ``embedded_at`` is set and ``embedding`` is NULL. So
#: :data:`_WAITING` never picks it again until its md5 changes, and the
#: search, which reads ``embedding IS NOT NULL`` only, never finds it.
_EMPTY_SQL = (
    "INSERT INTO crm_note_vectors (organization_id, activity_id, body_md5, "
    "embedding, embedded_at, failures, failed_at) "
    "SELECT a.organization_id, a.id, :body_md5, NULL, now(), 0, NULL "
    "FROM crm_activities a "
    "WHERE a.id = CAST(:activity_id AS uuid) AND a.organization_id = CAST(:o AS uuid) "
    "ON CONFLICT (activity_id) DO UPDATE SET body_md5 = EXCLUDED.body_md5, "
    "embedding = NULL, embedded_at = EXCLUDED.embedded_at, "
    "failures = 0, failed_at = NULL"
)


#: The waiting notes, counted up to ``:n`` only, so a large backlog costs no
#: full count (as ``note_labels._BACKLOG_SQL``).
_BACKLOG_SQL = "SELECT count(*) FROM (SELECT 1 " + _WAITING + "LIMIT :n) AS waiting"


async def _backlog(db: Any, org: str) -> int:
    """The waiting notes, up to :data:`BACKLOG_THRESHOLD` + 1."""
    return int((await db.execute(text(_BACKLOG_SQL), {
        "o": org, "n": BACKLOG_THRESHOLD + 1, "max_failures": MAX_FAILURES,
        "park_days": PARK_DAYS,
    })).scalar() or 0)


def daily_requests(backlog: int) -> int:
    """The org's request cap for one day. A backlog above
    :data:`BACKLOG_THRESHOLD` takes :data:`BACKLOG_DAILY_REQUESTS`, and
    otherwise :data:`DAILY_REQUESTS`."""
    return BACKLOG_DAILY_REQUESTS if backlog > BACKLOG_THRESHOLD else DAILY_REQUESTS


def step_limit(backlog: int) -> int:
    """The notes of one step. A backlog above :data:`BACKLOG_THRESHOLD`
    takes :data:`BACKLOG_STEP_LIMIT`, and otherwise :data:`STEP_LIMIT`."""
    return BACKLOG_STEP_LIMIT if backlog > BACKLOG_THRESHOLD else STEP_LIMIT


async def _pending(db: Any, org: str, limit: int) -> list[Note]:
    rows = (await db.execute(text(_PENDING_SQL), {
        "o": org, "n": limit, "max_failures": MAX_FAILURES, "park_days": PARK_DAYS,
    })).all()
    return [Note(id=str(r.id), subject=r.subject, body=r.body, body_md5=str(r.body_md5))
            for r in rows]


async def _write(
    org: str, notes: Sequence[Note], vectors: Sequence[list[float] | None],
) -> tuple[int, int]:
    """Write the vectors of one request in one transaction, and one failure
    for each note with no vector. Answers ``(embedded, failed)``."""
    embedded = failed = 0
    async with tenant_session(org) as db:
        for note, vector in zip(notes, vectors, strict=True):
            params = {"o": org, "activity_id": note.id, "body_md5": note.body_md5}
            if vector is None:
                result = await db.execute(text(_FAILED_SQL), params)
                failed += int(result.rowcount or 0)
            else:
                result = await db.execute(
                    text(_VECTOR_SQL), {**params, "vec": vector_literal(vector)})
                embedded += int(result.rowcount or 0)
    return embedded, failed


async def _mark_empty(org: str, notes: Sequence[Note]) -> None:
    """Write the empty mark (:data:`_EMPTY_SQL`) for each note in one
    transaction. No door call and no failure count."""
    async with tenant_session(org) as db:
        for note in notes:
            await db.execute(text(_EMPTY_SQL), {
                "o": org, "activity_id": note.id, "body_md5": note.body_md5,
            })


# ── The request counter (Redis, as CRM-Q26 counts its requests) ─────────────


def _utc_date(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()


def _requests_key(utc_date: str) -> Any:
    from acb_common.tenant_redis import key

    return key(NAMESPACE, "requests", utc_date)


def _redis() -> Any:
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


async def requests_used(org: str, utc_date: str) -> int:
    """The requests that the org sent on ``utc_date``."""
    from acb_common.tenant_redis import organization_scope

    with organization_scope(org):
        return int(await _redis().get(_requests_key(utc_date)) or 0)


async def reserve_request(org: str, utc_date: str, cap: int) -> bool:
    """Count one request against the org's ``cap`` for ``utc_date``, BEFORE
    it goes out. Answers False, and gives the count back, at the cap."""
    from acb_common.tenant_redis import organization_scope

    with organization_scope(org):
        redis = _redis()
        k = _requests_key(utc_date)
        used = int(await redis.incr(k, 1))
        await redis.expire(k, REQUESTS_TTL_S)
        if used > cap:
            await redis.decrby(k, 1)
            return False
    return True


# ── The step ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Embedded:
    """What one step did. Counts only."""

    reason: str
    notes: int = 0
    embedded: int = 0
    failed: int = 0
    requests: int = 0
    #: The notes with no text to embed, marked done with no vector.
    empty: int = 0


async def embed_after_run(
    org: str, *, should_stop: Callable[[], bool] = lambda: False,
    now: datetime | None = None,
) -> Embedded:
    """One embed step for ``org``, after a sync run. Never raises.

    The scheduler calls it outside the run lock, in a background task. The
    org comes from the scheduler, never from a request. ``now`` is for a
    test: it picks the UTC date of the request counter.
    """
    started = time.monotonic()
    token = bind_tenant(org)
    try:
        result = await _embed(org, should_stop, _utc_date(now))
    except Exception as exc:  # the step never breaks the sync loop
        _log.warning("crm.note_vectors.failed", organization_id=org,
                     error_type=type(exc).__name__)
        result = Embedded("failed")
    finally:
        release_tenant(token)
    if result.reason not in ("off", "none"):
        _log.info("crm.note_vectors.step", organization_id=org, reason=result.reason,
                  notes=result.notes, embedded=result.embedded, failed=result.failed,
                  requests=result.requests, empty=result.empty,
                  elapsed_ms=int((time.monotonic() - started) * 1000))
    return result


async def _embed(org: str, should_stop: Callable[[], bool], utc_date: str) -> Embedded:
    from acb_common._log import job_member_scope

    if not enabled_for(org):
        return Embedded("off")

    # A Redis failure raises here, and the step embeds nothing (spend fails closed).
    used = await requests_used(org, utc_date)
    async with tenant_session(org) as db:
        backlog = await _backlog(db, org)
        cap = daily_requests(backlog)
        room = cap - used
        if room <= 0:
            return Embedded("daily_cap")
        member = await note_labels._member(db, org)
        notes = await _pending(db, org, min(step_limit(backlog), room * NOTES_PER_REQUEST))
    # Fix round 2 (2026-10-17): a note with no text to embed never goes out.
    # The vendor refuses an empty input for the WHOLE request, so one such
    # note in a chunk gave no vector for any note of it, and the outage rule
    # stopped the step on the same chunk in each run. The mark costs no
    # request and no payer, so it runs before the payer check.
    empty = [n for n in notes if not note_text(n)]
    if empty:
        await _mark_empty(org, empty)
        notes = [n for n in notes if note_text(n)]
    if not notes:
        return Embedded("done" if empty else "none", empty=len(empty))
    if member is None:
        return Embedded("no_member", notes=len(notes))
    if await note_labels._member_left(org, member) is not None:
        # The payer check of CRM-Q26: this member would bill another org, or none.
        return Embedded("member_left", notes=len(notes))

    embedded = failed = requests = 0
    reason = "done"
    for start in range(0, len(notes), NOTES_PER_REQUEST):
        chunk = notes[start:start + NOTES_PER_REQUEST]
        if should_stop():
            reason = "stopped"
            break
        # The reserve holds the cap that this step read, never a second one.
        if not await reserve_request(org, utc_date, cap):
            reason = "daily_cap"
            break
        requests += 1
        # No session is open here: the read above has closed, and the write
        # below opens its own.
        with job_member_scope(member, app=MODULE_SLUG, agent=FEATURE):
            raw = await embed_door.embed([note_text(n) for n in chunk], org=org)
        vectors = [usable(v) for v in (list(raw) + [None] * len(chunk))[:len(chunk)]]
        if len(chunk) > 1 and not any(v is not None for v in vectors):
            # The door gave nothing usable: it is down. Write NO failure, or
            # a one-day outage parks every note of the day for PARK_DAYS
            # (fix round of 2026-10-17). The next run tries again.
            # ⚠️ A request of ONE note cannot tell an outage from a bad
            # note, so its failure counts. Without that, one bad note alone
            # in the queue spends a request in each run until the daily cap
            # stops every other note of the org.
            reason = "no_vectors"
            break
        done, lost = await _write(org, chunk, vectors)
        embedded += done
        failed += lost
    return Embedded(reason, notes=len(notes), embedded=embedded, failed=failed,
                    requests=requests, empty=len(empty))


__all__ = [
    "BACKLOG_DAILY_REQUESTS",
    "BACKLOG_STEP_LIMIT",
    "BACKLOG_THRESHOLD",
    "BODY_CLIP",
    "DAILY_REQUESTS",
    "FEATURE",
    "NAMESPACE",
    "NOTES_PER_REQUEST",
    "STEP_LIMIT",
    "Embedded",
    "Note",
    "daily_requests",
    "embed_after_run",
    "enabled_for",
    "note_text",
    "requests_used",
    "reserve_request",
    "step_limit",
    "usable",
]
