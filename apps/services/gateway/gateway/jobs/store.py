"""The job store: enqueue, claim, settle and the retry rule (WS-BRIEF BR-1a).

Spec: ``project-docs/specs/job_queue.md`` §4. The table is migration
``311_jobs.sql``. This module is the ONE writer of ``jobs``.

**Enqueue** writes in the CALLER's tenant session and does not commit, so the
job commits with the write that asked for it, or rolls back with it. Each
argument is checked before any SQL, so a bad argument never aborts the
caller's transaction (H-249). The org comes from the bound tenant through the
column default, never from an argument or a payload (H4, R5e).

**Claim** takes due jobs of ONE org with ``FOR UPDATE SKIP LOCKED`` and sets a
lease in the same statement, then commits. The handler runs with no
transaction open. A held row lock does not cover slow work
(``kb_index/index.py:22``), so the lease does.

**Settle** opens a NEW ``tenant_session(org)``. Each UPDATE names the job's
``tries``, so a late settle from a lost lease changes nothing.

Every statement runs inside ``tenant_session(org)``, so RLS binds it, and
each claim and settle also names ``organization_id`` beside RLS (defence in
depth, the shape of ``memory_purge._TENANT_STORE``). No block holds a
``try``: an error goes out of ``tenant_session``, which rolls back, and the
worker catches it one level up (§4.4).

Fences: ``tests/unit/test_job_queue_r8.py`` (R8) and
``tests/unit/test_job_queue.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from acb_common.db import TenantUnbound, tenant_session
from acb_common.pg_text import storable
from sqlalchemy import text

#: The lease of a claimed job, in seconds. One value for each kind in v1.
#: It is longer than ``worker.HANDLER_TIMEOUT_S``, so a slow handler fails
#: before its lease expires, and no second worker runs a live job.
LEASE_S = 300.0

#: The legal kind: the CHECK ``jobs_kind`` of the migration.
KIND_RE = re.compile(r"[a-z][a-z0-9_.]{2,63}")

#: The largest payload, in UTF-8 bytes. The CHECK of the migration allows
#: twice this, so this check always refuses first.
PAYLOAD_MAX_BYTES = 8192

#: Top-level payload keys that name a tenant. The org of a job is its column
#: only (H4), so a handler never finds an org in its payload.
TENANT_KEYS = frozenset({"organization_id", "org_id", "org", "tenant", "tenant_id"})

#: The longest idempotency key, in characters (the CHECK ``jobs_key``).
KEY_MAX_CHARS = 200

#: The bounds of ``max_tries`` (the CHECK ``jobs_tries``).
MIN_TRIES, MAX_TRIES = 1, 20

#: The retry back-off: 30 s, doubled for each earlier try, at most 1 hour.
RETRY_BASE_S = 30.0
RETRY_CAP_S = 3600.0

#: The longest ``last_error`` (the CHECK ``jobs_error``).
ERROR_MAX_CHARS = 500

#: The three outcomes of a settle (§4.4).
OUTCOMES = ("succeeded", "retry", "dead")


class PermanentJobError(Exception):
    """A handler raises this when a retry cannot help. The job goes
    ``dead`` at once."""


@dataclass(frozen=True)
class Enqueued:
    """What :func:`enqueue` answers. ``created`` is False when the
    idempotency key already named a job."""

    id: str
    state: str
    created: bool


@dataclass(frozen=True)
class Job:
    """One claimed job, all from the row. A handler gets this.

    ``organization_id`` is the column of the row, never a payload value.
    ``run_at`` and ``started_at`` feed the lag of the log line only.
    """

    id: str
    organization_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    tries: int = 0
    max_tries: int = 5
    run_at: datetime | None = None
    started_at: datetime | None = None


# ── The retry rule ──────────────────────────────────────────────────────────


def retry_delay_s(tries: int) -> float:
    """The back-off after a failed run of a job that has ``tries`` tries.

    The shape of ``memory_purge.retry_delay_s``, with a base of 30 s and a
    cap of 1 hour: 30, 60, 120 and 240 s for tries 1 to 4.
    """
    step = max(int(tries) - 1, 0)
    return float(min(RETRY_CAP_S, RETRY_BASE_S * 2 ** min(step, 16)))


# ── Enqueue ─────────────────────────────────────────────────────────────────

_TENANT_SQL = "SELECT current_setting('app.tenant_id', true)"

_ENQUEUE_SQL = (
    "INSERT INTO jobs (kind, payload, idempotency_key, max_tries, run_at) "
    "VALUES (:kind, CAST(:payload AS jsonb), :key, :max_tries, "
    "        COALESCE(CAST(:run_at AS timestamptz), now())) "
    "ON CONFLICT (organization_id, kind, idempotency_key) "
    "    WHERE idempotency_key IS NOT NULL DO NOTHING "
    "RETURNING id, state"
)

#: The job that a duplicate key named. RLS binds the read, and the predicate
#: names the bound tenant too.
_BY_KEY_SQL = (
    "SELECT id, state FROM jobs "
    "WHERE organization_id = CAST(current_setting('app.tenant_id', true) AS uuid) "
    "AND kind = :kind AND idempotency_key = :key"
)


def _refuse_unstorable(value: str, what: str) -> None:
    """Postgres refuses NUL and a lone surrogate in text and in ``jsonb``,
    AFTER the statement is sent. ``pg_text.storable`` is the one rule of
    what it refuses: it answers the same object when nothing changes."""
    if storable(value) is not value:
        raise ValueError(f"{what} holds a character that Postgres refuses")


def _float_growth(value: float) -> int:
    """How many more bytes the ``jsonb`` text of *value* takes than the JSON
    of Python. ``jsonb`` stores a number as ``numeric`` and prints it in
    full, so ``1e300`` takes 301 bytes and not 6."""
    shown = repr(value)
    full = format(Decimal(shown), "f")
    return max(0, len(full) - len(shown))


def _payload_text(payload: dict[str, Any] | None) -> tuple[str, int]:
    """The JSON text of *payload* and its size, or ValueError. No SQL runs
    here.

    It refuses a payload that is not a dict, a value that JSON cannot hold
    (a NaN, an infinity, an object, a cycle), a tenant key at the top level,
    a string that Postgres refuses, and a text past
    :data:`PAYLOAD_MAX_BYTES`. The size counts UTF-8 bytes, and a float
    counts at the length that ``jsonb`` prints. So the size is the
    ``octet_length(payload::text)`` that Postgres stores.
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    try:
        encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False)
        plain = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"payload is not JSON: {type(exc).__name__}") from None
    if any(str(key).lower() in TENANT_KEYS for key in plain):
        raise ValueError("payload must not name a tenant: the org is the job's column")
    try:
        size = len(encoded.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("payload holds a character that Postgres refuses") from None
    # One walk with a list, not a recursion: a deep payload must not raise
    # RecursionError here.
    todo: list[Any] = [plain]
    while todo:
        item = todo.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                _refuse_unstorable(key, "a payload key")
                todo.append(value)
        elif isinstance(item, list):
            todo.extend(item)
        elif isinstance(item, str):
            _refuse_unstorable(item, "a payload value")
        elif isinstance(item, float):
            size += _float_growth(item)
    if size > PAYLOAD_MAX_BYTES:
        raise ValueError(f"payload is over {PAYLOAD_MAX_BYTES} bytes")
    return encoded, size


def _check_arguments(kind: str, run_at: datetime | None,
                     idempotency_key: str | None, max_tries: int) -> None:
    """ValueError for a bad argument. No SQL runs here."""
    if not isinstance(kind, str) or KIND_RE.fullmatch(kind) is None:
        raise ValueError("kind must match [a-z][a-z0-9_.]{2,63}")
    if run_at is not None and (not isinstance(run_at, datetime)
                               or run_at.utcoffset() is None):
        raise ValueError("run_at must be a datetime with a time zone")
    if idempotency_key is not None:
        if (not isinstance(idempotency_key, str)
                or not 1 <= len(idempotency_key) <= KEY_MAX_CHARS):
            raise ValueError(f"idempotency_key must hold 1 to {KEY_MAX_CHARS} characters")
        _refuse_unstorable(idempotency_key, "idempotency_key")
    if (isinstance(max_tries, bool) or not isinstance(max_tries, int)
            or not MIN_TRIES <= max_tries <= MAX_TRIES):
        raise ValueError(f"max_tries must be an int from {MIN_TRIES} to {MAX_TRIES}")


async def enqueue(db: Any, kind: str, payload: dict[str, Any] | None = None, *,
                  run_at: datetime | None = None,
                  idempotency_key: str | None = None,
                  max_tries: int = 5) -> Enqueued:
    """Add one job in the caller's tenant session *db*. Does not commit.

    A bad argument raises ``ValueError`` before any SQL. A session with no
    tenant raises ``TenantUnbound`` and writes nothing, and that refuses a
    session from ``get_db()`` too. A duplicate ``idempotency_key`` for the
    same kind adds no row and answers the job it names, with
    ``created=False``.

    The key holds for ever until retention (JQ-4) exists. A recurring caller
    puts the date or the run id in the key.
    """
    _check_arguments(kind, run_at, idempotency_key, max_tries)
    encoded, _size = _payload_text(payload)
    tenant = (await db.execute(text(_TENANT_SQL))).scalar()
    if not tenant:
        raise TenantUnbound(
            "enqueue needs a tenant session: the org of a job is the bound "
            "tenant, never an argument (job_queue.md §4.1, H4)")
    row = (await db.execute(text(_ENQUEUE_SQL), {
        "kind": kind, "payload": encoded, "key": idempotency_key,
        "max_tries": max_tries, "run_at": run_at,
    })).fetchone()
    if row is not None:
        return Enqueued(id=str(row.id), state=str(row.state), created=True)
    # ON CONFLICT DO NOTHING does not abort the transaction.
    found = (await db.execute(text(_BY_KEY_SQL), {
        "kind": kind, "key": idempotency_key})).fetchone()
    if found is None:  # pragma: no cover - the unique index makes this a defect
        raise RuntimeError("a duplicate idempotency key named no visible job")
    return Enqueued(id=str(found.id), state=str(found.state), created=False)


# ── Claim ───────────────────────────────────────────────────────────────────

#: Due jobs of ONE org, and its running jobs whose lease expired, oldest
#: first. ``SKIP LOCKED`` lets two workers claim at once, and neither takes a
#: row that the other holds. The outer WHERE repeats the due predicate, so a
#: claim that waited on a row lock takes nothing that another claim moved.
_CLAIM_SQL = (
    "UPDATE jobs j "
    "   SET state = 'running', tries = j.tries + 1, "
    "       lease_until = now() + make_interval(secs => CAST(:lease_s AS double precision)), "
    "       started_at = now(), updated_at = now() "
    " WHERE j.id IN ( "
    "       SELECT id FROM jobs "
    "        WHERE organization_id = CAST(:org AS uuid) "
    "          AND ((state = 'queued' AND run_at <= now()) "
    "            OR (state = 'running' AND lease_until <= now())) "
    "        ORDER BY run_at, id "
    "        LIMIT :limit "
    "        FOR UPDATE SKIP LOCKED) "
    "   AND j.organization_id = CAST(:org AS uuid) "
    "   AND ((j.state = 'queued' AND j.run_at <= now()) "
    "     OR (j.state = 'running' AND j.lease_until <= now())) "
    "RETURNING j.id, j.organization_id, j.kind, j.payload, j.tries, "
    "          j.max_tries, j.run_at, j.started_at"
)


async def _after_claim(db: Any) -> None:
    """A no-op that :func:`claim` awaits after an UPDATE that took a job, and
    before the commit. The two-worker test (JQ-A4) patches it, so two claims
    are open at the same time. Do not remove it: mutation JQ-M1 needs it."""
    return None


def _job(row: Any) -> Job:
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    return Job(
        id=str(row.id), organization_id=str(row.organization_id), kind=str(row.kind),
        payload=payload, tries=int(row.tries), max_tries=int(row.max_tries),
        run_at=row.run_at, started_at=row.started_at,
    )


def _require_org(org: str | None) -> str:
    """The org of a claim or a settle. With none, ``TenantUnbound``: the
    queue never falls back to an ambient tenant (H4)."""
    if not org:
        raise TenantUnbound("the job queue acts for one explicit org (job_queue.md §5)")
    return str(org)


async def claim(org: str, limit: int = 1) -> list[Job]:
    """Take up to *limit* due jobs of *org*, set each lease, and commit."""
    org = _require_org(org)
    async with tenant_session(org) as db:
        rows = (await db.execute(text(_CLAIM_SQL), {
            "org": org, "lease_s": LEASE_S, "limit": int(limit)})).fetchall()
        if rows:
            await _after_claim(db)
    return [_job(row) for row in rows]


# ── Settle ──────────────────────────────────────────────────────────────────

#: The guard of every settle: this job, this org, still running, and still at
#: the try that this worker claimed. A late settle from a lost lease matches
#: no row.
_GUARD = (
    " WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND state = 'running' AND tries = :tries RETURNING id"
)

_SETTLE_SQL: dict[str, str] = {
    "succeeded": (
        "UPDATE jobs SET state = 'succeeded', finished_at = now(), "
        "lease_until = NULL, last_error = NULL, updated_at = now()" + _GUARD),
    "retry": (
        "UPDATE jobs SET state = 'queued', "
        "run_at = now() + make_interval(secs => CAST(:delay_s AS double precision)), "
        "lease_until = NULL, last_error = :error, updated_at = now()" + _GUARD),
    "dead": (
        "UPDATE jobs SET state = 'dead', finished_at = now(), "
        "lease_until = NULL, last_error = :error, updated_at = now()" + _GUARD),
}

#: The state of the row after each outcome.
STATE_AFTER = {"succeeded": "succeeded", "retry": "queued", "dead": "dead"}


def describe(exc: BaseException) -> str:
    """``last_error`` for *exc*: the class name, then ``: `` and the first
    line of the message, cut to :data:`ERROR_MAX_CHARS`. NUL and a lone
    surrogate become U+FFFD, so the settle never fails on the text."""
    name = type(exc).__name__
    lines = str(exc).splitlines()
    first = lines[0].strip() if lines else ""
    out = f"{name}: {first}" if first else name
    return str(storable(out))[:ERROR_MAX_CHARS]


async def settle(org: str, job: Job, outcome: str, error: str | None = None) -> bool:
    """Settle *job* of *org* in a NEW tenant session. Answers False when the
    row is no longer this worker's (a lost lease, or another org's job)."""
    if outcome not in _SETTLE_SQL:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    org = _require_org(org)
    params: dict[str, Any] = {"id": job.id, "org": org, "tries": int(job.tries)}
    if outcome != "succeeded":
        params["error"] = str(storable(error or "error"))[:ERROR_MAX_CHARS]
    if outcome == "retry":
        params["delay_s"] = retry_delay_s(job.tries)
    async with tenant_session(org) as db:
        row = (await db.execute(text(_SETTLE_SQL[outcome]), params)).fetchone()
    return row is not None
