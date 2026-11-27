"""The run capability of the sandbox door (SBX-2, Metorite task 3801).

Spec: ``project-docs/specs/agent_sandbox.md`` §5.1, §7.5 and §12.2
decisions 4 to 9 and 13.

**What a capability is.** The code of a user tool runs in the sandbox. It
reads the CRM and Projects through the existing gateway routes, as the
member who runs the tool. It sends one header, ``X-Sandbox-Capability:
msbx_<token>``. The token is 32 random bytes. The table
``sandbox_capabilities`` (migration 503) holds only the SHA-256 of the whole
header value, with the organization, the member, the run, the thread, the
phase, the version and the scopes of one step.

**The one store.** This module is the ONE writer and the ONE reader of the
table: :func:`mint`, :func:`revoke`, :func:`revoke_step`, :func:`sweep` and
:func:`lookup`. The table is RLS-EXEMPT, because the door reads a row before
it knows the tenant. So each act opens the one UNBOUND session of
``agent_tokens`` and binds no tenant. :func:`lookup` returns the row's
organization, and the door binds it after its checks.

**R5e.** ``org`` and ``member`` of :func:`mint` come from the run binding,
never from tool input or a request. Until SBX-4, only tests call
:func:`mint`. SBX-F1 (``tests/unit/test_sandbox_capability.py``) fails when a
module outside ``tests/`` calls it.

**The flag.** :func:`door_orgs` is the one reader of ``SANDBOX_DOOR_ORGS``.

**The answer of one call.** :data:`DoorCall` is the record of one admitted
call. The gateway middleware ``SandboxDoorLimits`` opens one for each request
(:func:`open_door_call`). The door fills it at its last step
(:func:`admit`). The middleware then caps the answer and writes the log line.

**The refusal count (SBX-4b1, decision 9 of §12.4).** A tool can catch a
``DoorError`` and still exit 0, so the smoke test needs a count that the code
cannot hide. :func:`lookup` notes the hash, the org and the life of a live row
on the open record. :func:`log_door_call` marks the record as refused for
one of :data:`COUNTED_REFUSALS`. The middleware then calls
:func:`count_refusal` before it forwards the status of the refusal.
:func:`door_counts` reads the call count and the refusal count of one token.

**No secret in a log.** No line of this module holds the token, its hash, a
header value or a query value. Fence: ``tests/unit/test_sandbox_door_log_lines.py``.
"""
from __future__ import annotations

import contextvars
import functools
import re
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from acb_common import app_manifests, get_logger, get_settings, tenant_redis

from acb_auth import agent_tokens

_log = get_logger("acb_auth.sandbox")

#: The prefix of each token. The hash covers it.
TOKEN_PREFIX = "msbx_"

#: The ceiling of a capability's life, in seconds (§5.1).
MAX_TTL_S = 900

#: The steps of a tool run (§6.3 and §6.4).
PHASES: tuple[str, ...] = ("build", "smoke", "run")

#: The read scopes that a capability may hold (decision 7).
SANDBOX_READ_SCOPES: frozenset[str] = app_manifests.SANDBOX_READ_SCOPES

#: The shape of the header value: the prefix and 43 URL-safe characters.
TOKEN_PATTERN = re.compile(r"msbx_[A-Za-z0-9_-]{43}")

#: The calls of one capability (decision 9).
CALL_CAP = 600

#: The tenant Redis namespace of the call counter (decision 9).
CALLS_NAMESPACE = "sandbox_door_calls"

#: The counter lives this long past the end of its capability.
CALLS_GRACE_S = 60

#: The tenant Redis namespace of the refusal counter (§12.4 decision 9). It
#: has the life of the call counter.
REFUSALS_NAMESPACE = "sandbox_door_refusals"

#: The largest page that a query may ask for (decision 9).
PAGE_CAP = 100

#: The query keys of a page size (decision 9).
PAGE_KEYS: frozenset[str] = frozenset({"limit", "page_size", "per_lane"})

#: A page value: one to three ASCII digits.
_PAGE_VALUE = re.compile(r"[0-9]{1,3}")

#: The largest answer of one admitted call, in bytes (decision 9).
ANSWER_CAP_BYTES = 4_194_304

#: The fixed refusal codes of decision 8.
READ_ONLY = "sandbox_read_only"
ROUTE_EXCLUDED = "sandbox_route_excluded"
ROUTE_UNKNOWN = "sandbox_route_unknown"
SCOPE = "sandbox_scope"
BAD_QUERY = "sandbox_bad_query"
PAGE_CAP_CODE = "sandbox_page_cap"
CALL_CAP_CODE = "sandbox_call_cap"
TOO_LARGE = "sandbox_response_too_large"

#: The refusals that the refusal counter counts (§12.4 decision 9). A 422 of
#: the query and a 401 are not on it.
COUNTED_REFUSALS: frozenset[str] = frozenset({READ_ONLY, ROUTE_EXCLUDED, ROUTE_UNKNOWN, SCOPE})


# ── The rows ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Minted:
    """The one value that carries the raw token out of this module."""

    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Capability:
    """A live row of ``sandbox_capabilities``. The hash keys the call
    counter only. It is never logged and never leaves the door."""

    token_sha256: str = field(repr=False)
    organization_id: str
    member_email: str
    run_id: str
    thread_id: str
    phase: str
    app_id: str | None
    version: int | None
    read_scopes: tuple[str, ...]
    write_scopes: tuple[str, ...]
    expires_at: datetime
    #: The whole seconds to ``expires_at``, from the clock of the database.
    seconds_left: int


_MINT_SQL = """
INSERT INTO sandbox_capabilities (
    token_sha256, organization_id, member_email, run_id, thread_id, app_id,
    version, phase, read_scopes, write_scopes, expires_at
) VALUES (
    :hash, CAST(:org AS uuid), :member, :run_id, :thread_id,
    CAST(:app_id AS uuid), :version, :phase, CAST(:read_scopes AS text[]),
    CAST(:write_scopes AS text[]),
    now() + make_interval(secs => CAST(:ttl_s AS double precision))
)
RETURNING created_at, expires_at
"""

_LOOKUP_SQL = """
SELECT token_sha256, organization_id, member_email, run_id, thread_id,
       app_id, version, phase, read_scopes, write_scopes, expires_at,
       GREATEST(CAST(CEIL(EXTRACT(EPOCH FROM expires_at - now())) AS integer), 1)
           AS seconds_left
  FROM sandbox_capabilities
 WHERE token_sha256 = :hash
   AND revoked_at IS NULL
   AND expires_at > now()
"""

_REVOKE_SQL = """
UPDATE sandbox_capabilities
   SET revoked_at = now()
 WHERE token_sha256 = :hash
   AND revoked_at IS NULL
   AND expires_at > now()
RETURNING organization_id, run_id, thread_id, phase, expires_at, read_scopes
"""

_REVOKE_STEP_SQL = """
UPDATE sandbox_capabilities
   SET revoked_at = now()
 WHERE organization_id = CAST(:org AS uuid)
   AND run_id = :run_id
   AND phase = :phase
   AND revoked_at IS NULL
   AND expires_at > now()
RETURNING thread_id
"""

_SWEEP_SQL = """
DELETE FROM sandbox_capabilities
 WHERE expires_at < now() - interval '1 day'
RETURNING 1
"""


def _canonical_uuid(value: object) -> str | None:
    """*value* as the lower-case text of a UUID, or ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _log_capability(act: str, *, org: str, run_id: str, thread_id: str, phase: str,
                    expires_at: object, read_scopes: int) -> None:
    """The line ``sandbox.capability`` (decision 13). Ids, never the token.

    The field ``act`` holds the event of the spec (``minted``, ``revoked``,
    ``revoked_step`` or ``swept``), because the logger names its own first
    argument ``event``."""
    _log.info("sandbox.capability", act=act, org=org, run=run_id, thread=thread_id,
              phase=phase, expires_at=str(expires_at), read_scopes=read_scopes)


async def mint(*, org: str, member: str, run_id: str, thread_id: str,
               phase: str, read_scopes: Iterable[str],
               write_scopes: Iterable[str] = (), app_id: str | None = None,
               version: int | None = None, ttl_s: int = MAX_TTL_S) -> Minted:
    """Write one capability, and give its raw token once.

    Raises ``ValueError`` before any SQL for a ``ttl_s`` below 1 or above
    :data:`MAX_TTL_S`, a phase outside :data:`PHASES`, a read scope outside
    :data:`SANDBOX_READ_SCOPES`, any write scope (SBX-2 opens none), an
    ``org`` that is not a UUID, and an empty member, run or thread.

    ⚠️ R5e: *org* and *member* come from the run binding, never from tool
    input or a request. The database clock sets ``created_at`` and
    ``expires_at`` in the INSERT, so the CHECK of migration 503 holds when the
    two clocks differ.
    """
    from sqlalchemy import text

    if isinstance(ttl_s, bool) or not isinstance(ttl_s, int) or not 1 <= ttl_s <= MAX_TTL_S:
        raise ValueError(f"ttl_s must be 1 to {MAX_TTL_S} seconds")
    if phase not in PHASES:
        raise ValueError("unknown phase")
    reads = tuple(dict.fromkeys(read_scopes))
    if any(scope not in SANDBOX_READ_SCOPES for scope in reads):
        raise ValueError("a read scope is not a scope of the sandbox door")
    writes = tuple(dict.fromkeys(write_scopes))
    if writes:
        raise ValueError("SBX-2 opens no write scope")
    org_id = _canonical_uuid(org)
    if org_id is None:
        raise ValueError("org is not a UUID")
    who = (member or "").strip().lower()
    run = (run_id or "").strip()
    thread = (thread_id or "").strip()
    if not who or not run or not thread:
        raise ValueError("member, run_id and thread_id are required")
    package = None
    if app_id is not None:
        package = _canonical_uuid(app_id)
        if package is None:
            raise ValueError("app_id is not a UUID")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        raise ValueError("version is not an integer")

    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    digest = agent_tokens.hash_token(raw)

    async def _work(session: Any) -> Any:
        return (await session.execute(text(_MINT_SQL), {
            "hash": digest, "org": org_id, "member": who, "run_id": run,
            "thread_id": thread, "app_id": package, "version": version,
            "phase": phase, "read_scopes": list(reads), "write_scopes": list(writes),
            "ttl_s": ttl_s,
        })).one()

    row = await agent_tokens._run(_work)
    _log_capability("minted", org=org_id, run_id=run, thread_id=thread, phase=phase,
                    expires_at=row.expires_at, read_scopes=len(reads))
    return Minted(token=raw, expires_at=row.expires_at)


async def revoke(token_sha256: str) -> bool:
    """End one live capability. ``True`` when a live row changed."""
    from sqlalchemy import text

    async def _work(session: Any) -> Any:
        return (await session.execute(
            text(_REVOKE_SQL), {"hash": str(token_sha256 or "")})).one_or_none()

    row = await agent_tokens._run(_work)
    if row is None:
        return False
    _log_capability("revoked", org=str(row.organization_id), run_id=row.run_id,
                    thread_id=row.thread_id, phase=row.phase, expires_at=row.expires_at,
                    read_scopes=len(row.read_scopes or ()))
    return True


async def revoke_step(*, org: str, run_id: str, phase: str) -> int:
    """End each live capability of one run and phase. The end of a step
    calls it. The count of rows that changed."""
    from sqlalchemy import text

    org_id = _canonical_uuid(org)
    if org_id is None or phase not in PHASES or not (run_id or "").strip():
        return 0

    async def _work(session: Any) -> Any:
        return (await session.execute(text(_REVOKE_STEP_SQL), {
            "org": org_id, "run_id": run_id.strip(), "phase": phase})).all()

    rows = await agent_tokens._run(_work)
    if rows:
        _log_capability("revoked_step", org=org_id, run_id=run_id.strip(),
                        thread_id=str(rows[0].thread_id), phase=phase, expires_at="",
                        read_scopes=0)
    return len(rows)


async def sweep() -> int:
    """Delete each row 1 day after its ``expires_at`` (§7.5). SBX-4 schedules
    it with the first real mint. The count of rows deleted."""
    from sqlalchemy import text

    async def _work(session: Any) -> Any:
        return (await session.execute(text(_SWEEP_SQL))).all()

    rows = await agent_tokens._run(_work)
    _log.info("sandbox.capability", act="swept", count=len(rows))
    return len(rows)


def well_formed(raw: object) -> bool:
    """True when *raw* has the shape of a header value (decision 3)."""
    return isinstance(raw, str) and TOKEN_PATTERN.fullmatch(raw) is not None


async def lookup(raw: str) -> Capability | None:
    """The live capability of the header value *raw*, or ``None``.

    It refuses a value with the wrong shape before any read. One point read
    by the hash, in an UNBOUND session, with the expiry and the revoke in
    the WHERE. It binds no tenant. ``None`` covers an unknown, an expired and
    a revoked capability alike. A failed read raises, and the door tells an
    outage from a refusal.
    """
    from sqlalchemy import text

    if not well_formed(raw):
        return None
    digest = agent_tokens.hash_token(raw)

    async def _work(session: Any) -> Any:
        return (await session.execute(text(_LOOKUP_SQL), {"hash": digest})).one_or_none()

    row = await agent_tokens._run(_work)
    if row is None:
        return None
    capability = Capability(
        token_sha256=row.token_sha256,
        organization_id=str(row.organization_id),
        member_email=(row.member_email or "").strip().lower(),
        run_id=row.run_id,
        thread_id=row.thread_id,
        phase=row.phase,
        app_id=str(row.app_id) if row.app_id is not None else None,
        version=row.version,
        read_scopes=tuple(row.read_scopes or ()),
        write_scopes=tuple(row.write_scopes or ()),
        expires_at=row.expires_at,
        seconds_left=int(row.seconds_left),
    )
    _note_lookup(capability)
    return capability


# ── The flag (decision 4) ───────────────────────────────────────────────────


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def door_orgs() -> frozenset[str]:
    """The organizations of ``SANDBOX_DOOR_ORGS``: org ids, or ``*``.

    The one reader of the flag. It reads the settings at each call, so a
    reload (SIGHUP) applies a change. Empty is off. ``*`` lists every org,
    and it is allowed only while self-serve signup is off: while
    ``SELF_SERVE_SIGNUP_ENABLED`` is ``true``, a value that holds ``*`` reads
    as empty, as ``compute_enabled`` of the broker reads it. A fault reads
    as off.
    """
    try:
        settings = get_settings()
        raw = str(getattr(settings, "sandbox_door_orgs", "") or "")
        signup = str(getattr(settings, "self_serve_signup_enabled", "") or "")
    except Exception:
        return frozenset()
    listed = _parse_orgs(raw)
    if "*" in listed and signup.strip().lower() == "true":
        return frozenset()
    return listed


# ── The query (decision 9) ──────────────────────────────────────────────────


def page_within_cap(pairs: Iterable[tuple[str, str]]) -> bool:
    """True when each page key holds one to three ASCII digits, at most
    :data:`PAGE_CAP`. A value such as ``0100`` or ``1e2`` is refused."""
    for key, value in pairs:
        if key not in PAGE_KEYS:
            continue
        if _PAGE_VALUE.fullmatch(value) is None or int(value) > PAGE_CAP:
            return False
    return True


# ── The admitted call of one request (decision 9) ───────────────────────────


@dataclass(slots=True)
class DoorCall:
    """The admitted call of one request. It holds ids and counts only."""

    admitted: bool = False
    organization_id: str = ""
    run_id: str = ""
    phase: str = ""
    method: str = ""
    template: str = ""
    call_n: int = 0
    #: The hash of the live row that :func:`lookup` read (§12.4 decision 9).
    #: It keys the refusal counter only. It is never logged.
    token_sha256: str = field(default="", repr=False)
    #: The org of that row. The refusal counter binds it.
    token_org: str = ""
    #: The whole seconds to the end of that row.
    token_seconds_left: int = 0
    #: True when the door refused the call for a code of COUNTED_REFUSALS.
    refused: bool = False
    #: True when :func:`count_refusal` ran for this record.
    refusal_counted: bool = False


_DOOR_CALL: contextvars.ContextVar[DoorCall | None] = contextvars.ContextVar(
    "sandbox_door_call", default=None)


def open_door_call() -> tuple[DoorCall, contextvars.Token[DoorCall | None]]:
    """A fresh record for one request. The middleware calls it, and resets
    the token after the answer, so one request never sees another's."""
    holder = DoorCall()
    return holder, _DOOR_CALL.set(holder)


def close_door_call(token: contextvars.Token[DoorCall | None]) -> None:
    """Undo :func:`open_door_call`."""
    _DOOR_CALL.reset(token)


def admit(capability: Capability, method: str, template: str | None, call_n: int) -> None:
    """Step 11 of the door: mark this request as an admitted sandbox call.

    It fills the record that the middleware opened. With no record open (a
    direct test of the dependency), it sets one. The middleware of the
    gateway always opens one first, and a task that the request starts
    copies the reference to that same record.
    """
    holder = _DOOR_CALL.get()
    if holder is None:
        holder = DoorCall()
        _DOOR_CALL.set(holder)
    holder.admitted = True
    holder.organization_id = capability.organization_id
    holder.run_id = capability.run_id
    holder.phase = capability.phase
    holder.method = method
    holder.template = template or ""
    holder.call_n = call_n


def current_door_call() -> DoorCall | None:
    """The record of this request, or ``None``."""
    return _DOOR_CALL.get()


def _note_lookup(capability: Capability) -> None:
    """Note the hash, the org and the life of a live row on the open record
    (§12.4 decision 9). With no record open, it does nothing."""
    holder = _DOOR_CALL.get()
    if holder is None:
        return
    holder.token_sha256 = capability.token_sha256
    holder.token_org = capability.organization_id
    holder.token_seconds_left = capability.seconds_left


async def count_refusal(holder: DoorCall) -> None:
    """Add 1 to the refusal counter of the token of a refused call (§12.4
    decision 9). The middleware calls it before it forwards the status.

    The key is the token hash under :data:`REFUSALS_NAMESPACE`, in the tenant
    Redis wrapper bound to the ROW's org. Its life is the life of the call
    counter. It runs once for each record, and only for a record that
    :func:`log_door_call` marked. A Redis failure loses this one count and
    logs the class of the error only. SBX-5 closes that residual."""
    if not holder.refused or holder.refusal_counted or not holder.token_sha256:
        return
    holder.refusal_counted = True
    try:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(holder.token_org):
            counter = tenant_redis.key(REFUSALS_NAMESPACE, holder.token_sha256)
            pipe = client.pipeline(transaction=True)
            pipe.incr(counter)
            pipe.expire(counter, max(holder.token_seconds_left, 1) + CALLS_GRACE_S)
            await pipe.execute()
    except Exception as exc:
        _log.warning("sandbox.door_refusal_count_failed", error=type(exc).__name__)


@dataclass(frozen=True, slots=True)
class DoorCounts:
    """The counts of one token: its admitted calls and its refusals."""

    calls: int
    refusals: int


async def door_counts(org: str, token_sha256: str) -> DoorCounts:
    """The call count and the refusal count of one token (§12.4 decision 9).

    It reads the two keys in the tenant Redis wrapper of *org*. A missing key
    counts 0. A read error raises, and the step engine fails the round on
    it. *org* comes from the run binding, never from tool input (R5e)."""
    org_id = _canonical_uuid(org)
    if org_id is None:
        raise ValueError("org is not a UUID")
    if not isinstance(token_sha256, str) or not token_sha256:
        raise ValueError("token_sha256 is required")
    client = tenant_redis.get_tenant_redis()
    with tenant_redis.organization_scope(org_id):
        keys = [tenant_redis.key(CALLS_NAMESPACE, token_sha256),
                tenant_redis.key(REFUSALS_NAMESPACE, token_sha256)]
        calls, refusals = await client.mget(keys)
    return DoorCounts(calls=int(calls or 0), refusals=int(refusals or 0))


def log_door_call(*, status: int, code: str, method: str, template: str | None,
                  org: str = "", run_id: str = "", phase: str = "",
                  call_n: int | None = None, size: int = 0, warn: bool = False) -> None:
    """The line ``sandbox.door.call`` (decision 13). The door writes it for
    each refusal, and the middleware for each admitted call. A refusal
    before the lookup has no org and no run. No token, hash, header value,
    query value or answer text.

    A refusal for a code of :data:`COUNTED_REFUSALS` marks the open record
    as refused, when :func:`lookup` noted a row on it (§12.4 decision 9)."""
    if code in COUNTED_REFUSALS:
        holder = _DOOR_CALL.get()
        if holder is not None and holder.token_sha256:
            holder.refused = True
    log = _log.warning if warn else _log.info
    log("sandbox.door.call", org=org or None, run=run_id or None, phase=phase or None,
        method=method, template=template, status=status, code=code, call_n=call_n,
        bytes=size)


__all__ = [
    "ANSWER_CAP_BYTES",
    "CALLS_NAMESPACE",
    "CALL_CAP",
    "COUNTED_REFUSALS",
    "MAX_TTL_S",
    "PAGE_CAP",
    "PAGE_KEYS",
    "PHASES",
    "REFUSALS_NAMESPACE",
    "SANDBOX_READ_SCOPES",
    "TOKEN_PREFIX",
    "Capability",
    "DoorCall",
    "DoorCounts",
    "Minted",
    "admit",
    "close_door_call",
    "count_refusal",
    "current_door_call",
    "door_counts",
    "door_orgs",
    "log_door_call",
    "lookup",
    "mint",
    "open_door_call",
    "page_within_cap",
    "revoke",
    "revoke_step",
    "sweep",
    "well_formed",
]
