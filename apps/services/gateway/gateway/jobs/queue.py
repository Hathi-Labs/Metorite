"""The job queue: enqueue, claim, hold and settle (WS-BRIEF BR-1a).

Reference: ``docs/job_queue.md``. Table: migration ``jobs`` (312 at build).
This module is the ONE writer of ``jobs``.

**Tenancy (R5).** Every function takes a session that the caller opened with
``tenant_session(org)``, so RLS binds each statement. No function takes an
org. :func:`enqueue` writes no ``organization_id``: the column DEFAULT reads
the bound tenant, so a session with no tenant fails the NOT NULL, and a
session of another tenant fails the WITH CHECK.

**The states.** ``queued`` → ``running`` (a claim) → ``done`` or, after a
failure, ``queued`` again with a backoff, or ``failed`` at the last attempt.
A ``running`` row whose ``lease_until`` has passed goes to the next claim.

**The fence token.** Each claim writes a new ``locked_by``. :func:`hold`,
:func:`complete` and :func:`fail` match it, so a worker that lost its lease
changes nothing.

**The clock.** Each scheduling time is a ``now`` that the caller passes. A
test freezes it. ``created_at`` and ``updated_at`` take the server clock,
because they schedule nothing.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

#: The four states, as migration 312's CHECK ``jobs_state`` holds them.
STATES: tuple[str, ...] = ("queued", "running", "done", "failed")
#: The largest payload, in bytes of its compact JSON form.
MAX_PAYLOAD_BYTES = 64 * 1024
DEFAULT_MAX_ATTEMPTS = 5
#: The bound of the CHECK ``jobs_attempts``.
MAX_ATTEMPTS_LIMIT = 25
#: How long a claim holds a row before another worker may take it.
LEASE = timedelta(minutes=5)
#: The wait after failed attempt n is ``BACKOFF_BASE * 2 ** (n - 1)``, at most
#: ``BACKOFF_CAP``.
BACKOFF_BASE = timedelta(seconds=30)
BACKOFF_CAP = timedelta(hours=1)
#: The bound of the CHECK ``jobs_last_error``.
ERROR_CHARS = 2000
#: The bound of the CHECK ``jobs_dedupe_key``.
DEDUPE_CHARS = 200

#: The CHECK ``jobs_kind``.
_KIND = re.compile(r"[a-z][a-z0-9_.]{0,63}")


class JobError(ValueError):
    """An enqueue that the queue refuses: a bad kind, payload or option."""


@dataclass(frozen=True)
class Enqueued:
    """The answer of :func:`enqueue`. ``created`` is False when a live job of
    the same kind and dedupe key already exists. ``id`` is then its id."""

    id: str
    created: bool


@dataclass(frozen=True)
class Claim:
    """One claimed job. ``token`` is the fence of this claim only."""

    id: str
    organization_id: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    token: str


def check_kind(kind: object) -> str:
    """Answers *kind* when it matches the CHECK ``jobs_kind``, else raises."""
    if not isinstance(kind, str) or not _KIND.fullmatch(kind):
        raise JobError("kind must match [a-z][a-z0-9_.]{0,63}")
    return kind


def encode_payload(payload: object) -> str:
    """The compact JSON of *payload*, or :class:`JobError`.

    The payload must be a JSON object. NaN and Infinity are refused, because
    JSONB does not hold them. A NUL character is refused, because JSONB does
    not hold it either. A payload over :data:`MAX_PAYLOAD_BYTES` is refused.
    """
    if not isinstance(payload, dict):
        raise JobError("payload must be a JSON object")
    try:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True)
    except (ValueError, TypeError) as exc:
        raise JobError(f"payload is not finite JSON: {type(exc).__name__}") from exc
    if "\\u0000" in body:
        raise JobError("payload holds a NUL character")
    if len(body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise JobError(f"payload is over {MAX_PAYLOAD_BYTES} bytes")
    return body


def backoff(attempts: int) -> timedelta:
    """The wait after the failed attempt number *attempts* (1 is the first)."""
    step: timedelta = BACKOFF_BASE * (2 ** max(0, attempts - 1))
    return min(step, BACKOFF_CAP)


def _now() -> datetime:
    """The queue's one clock, for a caller that passes no ``now``."""
    return datetime.now(UTC)


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise JobError(f"{name} must be a datetime with a UTC offset")
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    """A JSONB value as a dict. A driver can answer the text form."""
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value or {})


# ── The SQL ─────────────────────────────────────────────────────────────────
#
# No statement names an org. RLS binds each one to `tenant_session(org)`.
# ⚠️ A parameter inside a CASE or a function has no column type, so asyncpg
# sends it as text. Each one carries a CAST (R8 found it).

_INSERT_SQL = (
    "INSERT INTO jobs (kind, payload, run_at, max_attempts, dedupe_key) "
    "VALUES (:kind, CAST(:payload AS jsonb), :run_at, :max_attempts, :dedupe_key) "
    "ON CONFLICT (organization_id, kind, dedupe_key) "
    "  WHERE dedupe_key IS NOT NULL AND state IN ('queued', 'running') "
    "DO NOTHING RETURNING id"
)

_LIVE_SQL = (
    "SELECT id FROM jobs WHERE kind = :kind AND dedupe_key = :dedupe_key "
    "AND state IN ('queued', 'running')"
)

#: ONE row that a handler of this worker runs: a queued row that is due, or
#: a running row whose lease has passed. A row that another worker holds is
#: skipped.
_CLAIM_SQL = (
    "SELECT id, organization_id, kind, payload, state, attempts, max_attempts "
    "FROM jobs "
    "WHERE kind = ANY(CAST(:kinds AS text[])) "
    "  AND ((state = 'queued' AND run_at <= :now) "
    "    OR (state = 'running' AND lease_until <= :now)) "
    "ORDER BY run_at, id LIMIT 1 "
    "FOR UPDATE SKIP LOCKED"
)

_TAKE_SQL = (
    "UPDATE jobs SET state = 'running', attempts = attempts + 1, "
    "locked_by = CAST(:token AS uuid), locked_at = :now, lease_until = :lease_until, "
    "updated_at = now() "
    "WHERE id = CAST(:id AS uuid)"
)

#: A lost lease at the last attempt. The worker died, so no handler error
#: exists to keep.
_EXPIRE_SQL = (
    "UPDATE jobs SET state = 'failed', locked_by = NULL, lease_until = NULL, "
    "finished_at = :now, updated_at = now(), "
    "last_error = left(COALESCE(last_error || ' | ', '') || 'lease expired', CAST(:chars AS integer)) "
    "WHERE id = CAST(:id AS uuid)"
)

#: The fence of the claim. The row lock lasts for the transaction of the
#: handler, so no other worker can take the row while it runs.
_HOLD_SQL = (
    "SELECT id FROM jobs WHERE id = CAST(:id AS uuid) AND state = 'running' "
    "AND locked_by = CAST(:token AS uuid) FOR UPDATE"
)

_COMPLETE_SQL = (
    "UPDATE jobs SET state = 'done', locked_by = NULL, lease_until = NULL, "
    "finished_at = :now, updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND state = 'running' "
    "AND locked_by = CAST(:token AS uuid)"
)

_FAIL_SQL = (
    "UPDATE jobs SET "
    "state = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END, "
    "run_at = CASE WHEN attempts >= max_attempts THEN run_at ELSE CAST(:retry_at AS timestamptz) END, "
    "finished_at = CASE WHEN attempts >= max_attempts THEN CAST(:now AS timestamptz) ELSE NULL END, "
    "locked_by = NULL, lease_until = NULL, last_error = :error, updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND state = 'running' "
    "AND locked_by = CAST(:token AS uuid) "
    "RETURNING state"
)

_GET_SQL = (
    "SELECT id, organization_id, kind, payload, state, attempts, max_attempts, "
    "run_at, locked_by, lease_until, last_error, dedupe_key, finished_at "
    "FROM jobs WHERE id = CAST(:id AS uuid)"
)


# ── The producer side ───────────────────────────────────────────────────────


async def enqueue(
    db: Any,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    now: datetime | None = None,
) -> Enqueued:
    """Queue one job of *kind* in the tenant that *db* binds.

    *run_at* is the earliest run, *now* when it is None. *dedupe_key* keeps
    one live job (``queued`` or ``running``) for each key of a kind. A second
    enqueue with a live key answers the live job with ``created=False``.
    Every check runs before the first statement, so a refused enqueue writes
    nothing. The org is never an argument: the DEFAULT reads the bound tenant.
    """
    check_kind(kind)
    body = encode_payload({} if payload is None else payload)
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not (
            1 <= max_attempts <= MAX_ATTEMPTS_LIMIT):
        raise JobError(f"max_attempts must be an integer from 1 to {MAX_ATTEMPTS_LIMIT}")
    if dedupe_key is not None and (
            not isinstance(dedupe_key, str) or not 1 <= len(dedupe_key) <= DEDUPE_CHARS):
        raise JobError(f"dedupe_key must be 1 to {DEDUPE_CHARS} characters")
    at = _aware(run_at, "run_at") if run_at is not None else (
        _aware(now, "now") if now is not None else _now())
    params = {"kind": kind, "payload": body, "run_at": at,
              "max_attempts": max_attempts, "dedupe_key": dedupe_key}
    # Two tries: a live job of the key can end between the insert and the read.
    for _ in range(2):
        new = (await db.execute(text(_INSERT_SQL), params)).scalar_one_or_none()
        if new is not None:
            return Enqueued(str(new), True)
        live = (await db.execute(text(_LIVE_SQL), {
            "kind": kind, "dedupe_key": dedupe_key})).scalar_one_or_none()
        if live is not None:
            return Enqueued(str(live), False)
    raise JobError("the dedupe key changed state twice during the enqueue")


async def get_job(db: Any, job_id: str) -> Any:
    """The row of *job_id* in the bound tenant, or None."""
    return (await db.execute(text(_GET_SQL), {"id": str(job_id)})).fetchone()


# ── The worker side ─────────────────────────────────────────────────────────


async def claim(db: Any, kinds: list[str], *, now: datetime,
                lease: timedelta = LEASE) -> Claim | None:
    """Claim the oldest due job of one of *kinds*, or answer None.

    The caller commits, so the claim lasts after this transaction. A running
    row with a lost lease at its last attempt turns ``failed``, and the
    claim takes the next row.
    """
    if not kinds:
        return None
    while True:
        row = (await db.execute(text(_CLAIM_SQL), {
            "kinds": sorted(kinds), "now": now})).fetchone()
        if row is None:
            return None
        if row.state == "running" and int(row.attempts) >= int(row.max_attempts):
            await db.execute(text(_EXPIRE_SQL), {
                "id": str(row.id), "now": now, "chars": ERROR_CHARS})
            continue
        token = str(uuid.uuid4())
        await db.execute(text(_TAKE_SQL), {
            "id": str(row.id), "token": token, "now": now, "lease_until": now + lease})
        return Claim(
            id=str(row.id), organization_id=str(row.organization_id), kind=row.kind,
            payload=_as_dict(row.payload), attempts=int(row.attempts) + 1,
            max_attempts=int(row.max_attempts), token=token)


async def hold(db: Any, job: Claim) -> bool:
    """Lock the row of *job* for this transaction. False when the claim is
    gone: another worker took the row after the lease passed."""
    found = (await db.execute(text(_HOLD_SQL), {
        "id": job.id, "token": job.token})).scalar_one_or_none()
    return found is not None


async def complete(db: Any, job: Claim, *, now: datetime) -> int:
    """Settle *job* as ``done``. Answers the rows changed: 1, or 0 when the
    claim is gone or the row is of another tenant."""
    result = await db.execute(text(_COMPLETE_SQL), {
        "id": job.id, "token": job.token, "now": now})
    return int(result.rowcount or 0)


async def fail(db: Any, job: Claim, error: str, *, now: datetime) -> str | None:
    """Settle a failed attempt of *job*. Answers the new state: ``queued``
    with the backoff, ``failed`` at the last attempt, or None when the claim
    is gone or the row is of another tenant. *error* is kept, cut to
    :data:`ERROR_CHARS`."""
    state = (await db.execute(text(_FAIL_SQL), {
        "id": job.id, "token": job.token, "now": now,
        "retry_at": now + backoff(job.attempts), "error": error[:ERROR_CHARS],
    })).scalar_one_or_none()
    return None if state is None else str(state)
