"""HRD-17a: the server runs one send once.

Spec: ``project-docs/specs/chat_failure_modes.md`` §4.7a rule 1, and §6 row
HRD-17a (its rules R1 to R11).

The stream of a chat is per thread, not per run. So a send that the browser
sends twice, with the same ``user_message_id``, used to start a second run
when the first run was final, or to steer the live run with the same words.
This module keeps ONE key for each send, so that a repeat replays the first
run and starts none.

**The key.** ``cc:<org>:send-once:<thread>:<member hash>:<id>``, built as a
:class:`acb_common.tenant_redis.TenantKey`. The org and the member come from
the session, never from the body. The member is in the key, so a room member
with the same id is no repeat. The id passes :data:`ID_RE`, so it holds no
colon and the shape of the key is fixed.

**The value.** JSON with ``run_id``, ``member``, ``sha256`` (the digest of the
sent words), ``claim`` (a token of the request that holds it, so that a
compare never mixes two requests), ``state`` and ``at`` (the time of the
write).

**Three states.** Each move is ONE compare and set
(``TenantRedis.replace_if``) from the value that this request wrote, so two
requests can never both move one key.

1. ``claim``: no run id. :func:`begin` writes it with SET NX and
   :data:`CLAIM_TTL_SECONDS`, before the route decides what the send does.
2. ``pending``: the run id, with :data:`RUN_TTL_SECONDS`. The route writes it
   with :meth:`SendClaim.start` right BEFORE ``run_detached``. A request
   whose move fails starts nothing.
3. ``run``: the same run id. :meth:`SendClaim.watch` writes it when the
   executor sends ``RUN_STARTED``, before the load and the model. A run whose
   move fails stops there, so it does no work.

**A repeat** (SET NX fails) reads the value:

* another digest: :data:`REUSED` (the route answers 409 ``send_id_reused``);
* a run that the first ``RUN_STARTED`` of the stream names, or the live run
  of the org's live-run index: :data:`REPLAY` (the route replays it, with
  ``X-Run-Id``);
* a ``run`` that the stream does not hold: :data:`FINISHED` (the route
  answers the synthetic ``RUN_FINISHED``, and the client reads the saved
  row). A value written less than :data:`FRESH_SECONDS` ago may not be in the
  stream yet, so the repeat waits for it first;
* a ``claim`` or a ``pending`` run: it waits up to :data:`WAIT_SECONDS`. Then
  a claim with no live run on the thread is stale (R3), and so is a pending
  run older than :data:`PENDING_STALE_SECONDS` that nothing names. Its
  request died or left before its run began. A compare and delete removes
  it, and the repeat claims the key again. Any other case gives :data:`BUSY`
  (409 ``send_in_progress``).

Each read of the stream is ``stream_relay.stream_head``: ONE ``XRANGE``,
which never blocks. Each Redis step has a hard timeout, and :func:`begin` as
a whole has :data:`BEGIN_TIMEOUT_SECONDS`.

**The end of a run.** :meth:`SendClaim.settle_end` deletes the key after a
``RUN_ERROR``, a Stop (``steer.was_stopped``) or a run that never started.
A deliberate resend can then run. A clean end keeps the key for 24 hours.
:meth:`SendClaim.release` deletes each value that this request wrote, or
was about to write, and never a value of another request.

**Fail open.** With Redis down, or stalled on the claim, :func:`begin` gives
:data:`OPEN`, and the send takes the old path (R10). A stall after a key is
known gives :data:`BUSY`, never a run. With the flag off, the route makes no
call here.

Fence: ``tests/unit/test_send_once.py``, and with a private real Redis
``tests/unit/test_send_once_real_redis.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_logger
from acb_common.tenant_redis import TIMEOUT_ERRORS

_log = get_logger("orchestrator.send_once")

#: The namespace of the key, under the tenant prefix.
NAMESPACE = "send-once"
#: The life of a claim that has no run yet (§6 HRD-17a, open choice (d)).
CLAIM_TTL_SECONDS = 60
#: The life of the key of a run.
RUN_TTL_SECONDS = 24 * 3600
#: How long a repeat waits for the first request (R5, open choice (c)).
WAIT_SECONDS = 2.0
#: A value written less than this long ago may still be on its way to the
#: stream, so a repeat waits for it before it answers FINISHED.
FRESH_SECONDS = 10.0
#: A pending run that nothing names after this long never began.
PENDING_STALE_SECONDS = 15.0
#: The step of a wait.
POLL_SECONDS = 0.1
#: The hard limit of one Redis step of this module.
STEP_TIMEOUT_SECONDS = 3.0
#: The hard limit of one :func:`begin`. It ends in BUSY, never in a run.
BEGIN_TIMEOUT_SECONDS = 10.0
#: The most full waits of one :func:`begin`. Each new value of the key gets
#: its own wait, so a slow chain of claims cannot hold a request long.
MAX_WAITS = 3
#: The most stale values that one :func:`begin` deletes before it gives up.
MAX_STALE_TAKES = 2

CLAIM, PENDING, RUN = "claim", "pending", "run"


def _max_rounds() -> int:
    """The most rounds of one :func:`begin`. A round is one SET NX and one
    read. It follows the wait, so the bound is never the normal end."""
    return MAX_WAITS * (int(WAIT_SECONDS / POLL_SECONDS) + 2) + 4


#: The release tasks of :meth:`SendClaim.release_soon`, held until each ends.
_RELEASES: set[asyncio.Task[None]] = set()

#: A send id that keys a send (R7). Anything else counts as no id.
ID_RE = re.compile(r"\A[A-Za-z0-9_-]{8,64}\Z")

#: What :func:`begin` decided.
NEW = "new"            # this request holds the key: the send runs
REPLAY = "replay"      # replay the run of the first send
FINISHED = "finished"  # the run of the first send is final and its stream gone
REUSED = "reused"      # the same id with other words
BUSY = "busy"          # the first send is starting, or no answer came in time
OPEN = "open"          # Redis did not answer: the old path, with no key


def send_once_on(org: object) -> bool:
    """True when ``CHAT_SEND_ONCE_ORGS`` names *org*. THE reader.

    A comma list of organization ids, or ``*`` for each one. Empty means off,
    the default. It reads the settings on each call, so a reload
    (``docs/flag_reload.md``) applies a change with no restart. It fails
    closed: no org, or an error, is off. It reads no Redis.
    """
    if not org:
        return False
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_send_once_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not entries:
        return False
    return "*" in entries or str(org).strip().lower() in entries


def send_id(raw: object) -> str:
    """The id that keys a send, or ``""`` for no id (R7).

    An id outside :data:`ID_RE` counts as no id, and the server logs its
    length, never its text.
    """
    text = raw if isinstance(raw, str) else ""
    if not text:
        return ""
    if ID_RE.match(text):
        return text
    _log.info("send_once.bad_id", length=len(text))
    return ""


def text_digest(text: str) -> str:
    """The SHA-256 of the sent words, as hex."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _member(member: str) -> str:
    return (member or "").strip().lower()


def member_part(member: str) -> str:
    """The member's part of the key: a hash, so the key names no address."""
    return hashlib.sha256(_member(member).encode("utf-8")).hexdigest()[:32]


def send_key(org: str, thread_id: str, member: str, sid: str) -> Any:
    """The key of one send. Call it inside ``organization_scope(org)``.

    It is a ``TenantKey``, so it refuses to build without the bound tenant.
    """
    from acb_common.tenant_redis import TenantKey

    return TenantKey(org, NAMESPACE, (thread_id, member_part(member), sid))


def _encode(run_id: str, member: str, digest: str, claim: str, state: str) -> str:
    return json.dumps(
        {"run_id": run_id, "member": _member(member), "sha256": digest, "claim": claim,
         "state": state, "at": round(time.time(), 3)},
        sort_keys=True, separators=(",", ":"),
    )


_FIELDS = ("run_id", "member", "sha256", "claim", "state", "at")


def _decode(raw: Any) -> dict[str, str] | None:
    try:
        held = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(held, dict):
        return None
    out = {k: str(held.get(k) or "") for k in _FIELDS}
    if not out["state"]:
        out["state"] = RUN if out["run_id"] else CLAIM
    return out


def _age(held: dict[str, str]) -> float:
    """Seconds since the value was written. An unreadable time is old."""
    try:
        return time.time() - float(held.get("at") or 0)
    except ValueError:
        return float("inf")


async def _step[T](aw: Awaitable[T]) -> T:
    """One Redis step, under :data:`STEP_TIMEOUT_SECONDS`."""
    return await asyncio.wait_for(aw, STEP_TIMEOUT_SECONDS)


async def _client() -> Any:
    """The relay's pooled client, behind the tenant-prefix wrapper."""
    from orchestrator.stream_relay import _tenant_client

    return await _tenant_client()  # type: ignore[no-untyped-call]


def _event_type(line: Any) -> str:
    """The ``type`` of one SSE line of the executor, for a run event only."""
    if not isinstance(line, str) or '"RUN_' not in line:
        return ""
    for part in line.split("\n"):
        part = part.strip()
        if part.startswith("data:"):
            try:
                evt = json.loads(part[5:].strip())
            except ValueError:
                return ""
            return str(evt.get("type") or "") if isinstance(evt, dict) else ""
    return ""


@dataclass
class SendClaim:
    """The key that this request holds for its send."""

    org: str
    thread_id: str
    member: str
    sid: str
    digest: str
    token: str
    #: The value that this request wrote last. Each compare uses it.
    value: str
    #: Each value that this request wrote, or was about to write when a
    #: cancel came. :meth:`release` deletes each one, and only those.
    written: list[str] = field(default_factory=list)
    run_id: str = ""
    #: :meth:`start` moved the claim to the pending run.
    started: bool = False
    #: :meth:`confirm` moved the pending run to the run.
    confirmed: bool = False
    errored: bool = False
    #: Another request holds the key now, so this run must not go on.
    lost: bool = False
    #: The key is released, or a Redis fault opened it. No call writes again.
    closed: bool = False
    #: The run pulled its first line, so a drain owns it and settles the key.
    pulled: bool = False

    def __post_init__(self) -> None:
        if self.value not in self.written:
            self.written.append(self.value)

    async def _move(self, new: str, state: str) -> bool | None:
        """Move the key from :attr:`value` to *new*. True: moved. False:
        the key holds another value, or no value. None: a Redis fault (fail
        open).

        A key with no value is a loss too. Its claim ran out, or a request
        took it as stale, ran, and then a Stop or an error released it. The
        two look the same, and only the first is safe to run, so the move
        never writes into an empty key (F-E of the review). A request slower
        than :data:`CLAIM_TTL_SECONDS` then starts nothing, and a resend of
        the same id runs.
        """
        self.written.append(new)  # BEFORE the await: a cancel still releases it
        try:
            from acb_common.tenant_redis import organization_scope

            with organization_scope(self.org):
                r = await _client()
                k = send_key(self.org, self.thread_id, self.member, self.sid)
                ok = bool(await _step(r.replace_if(k, self.value, new, RUN_TTL_SECONDS)))
        except Exception as exc:
            _log.warning("send_once.move_open", state=state, error=type(exc).__name__)
            self.closed = True
            return None
        if ok:
            self.value = new
        return ok

    async def start(self, run_id: str) -> bool:
        """Move the claim to the pending run. The route calls it right BEFORE
        ``run_detached``, inside the guarded part of the stream.

        True: the run may start. False: another request took the key, so this
        request starts nothing. A Redis fault gives True, because the key
        fails open (R10).
        """
        if self.closed or self.started:
            return True
        self.run_id = run_id
        moved = await self._move(
            _encode(run_id, self.member, self.digest, self.token, PENDING), PENDING)
        if moved is False:
            self.lost = True
            _log.warning("send_once.start_lost", thread_id=self.thread_id[:12])
            return False
        self.started = moved is True
        return True

    async def confirm(self) -> bool:
        """Move the pending run to the run, at its ``RUN_STARTED``.

        False: another request took the key, so the run must stop before its
        work. A Redis fault gives True (fail open).
        """
        if self.closed or self.confirmed or not self.started:
            return True
        moved = await self._move(
            _encode(self.run_id, self.member, self.digest, self.token, RUN), RUN)
        if moved is False:
            self.lost = True
            _log.warning("send_once.confirm_lost", thread_id=self.thread_id[:12])
            return False
        self.confirmed = moved is True
        return True

    async def release(self) -> None:
        """Delete each value that this request wrote, and never a value of
        another request. Never raises."""
        if self.closed:
            return
        self.closed = True
        try:
            from acb_common.tenant_redis import organization_scope

            with organization_scope(self.org):
                r = await _client()
                k = send_key(self.org, self.thread_id, self.member, self.sid)
                for value in reversed(self.written):
                    await _step(r.delete_if(k, value))
        except Exception as exc:
            _log.warning("send_once.release_failed", error=type(exc).__name__)

    def release_soon(self) -> None:
        """:meth:`release` in its own task, for a caller that a cancel may
        stop at its next await. The task keeps a strong reference until it
        ends, so it is never lost half way."""
        if self.closed:
            return
        task = asyncio.get_running_loop().create_task(self.release())
        _RELEASES.add(task)
        task.add_done_callback(_RELEASES.discard)

    async def was_stopped(self) -> bool:
        """True when a Stop ended the run of the thread. False on error."""
        with contextlib.suppress(Exception):
            from orchestrator.steer import was_stopped

            return bool(await _step(was_stopped(self.thread_id)))
        return False

    async def settle_end(self, *, stopped: bool | None = None) -> None:
        """At the end of the run (R4): delete the key after a ``RUN_ERROR``, a
        Stop or a run that never started. A clean end keeps it. The caller
        passes *stopped* when it read the Stop mark earlier, before a new
        send of the thread can clear it."""
        if stopped is None:
            stopped = await self.was_stopped()
        if self.errored or stopped or not self.started or self.lost:
            await self.release()

    async def watch(self, gen: AsyncIterator[str]) -> AsyncIterator[str]:
        """Pass the run's SSE lines on unchanged. At the first
        ``RUN_STARTED`` it confirms the run, and a run that lost the key stops
        there, before the load and the model. It notes a ``RUN_ERROR`` (R4),
        and a run that raises counts as one. A tier fallback or a parallel
        restart stays inside this one run, so it moves nothing more (R9)."""
        self.pulled = True
        try:
            async for line in gen:
                kind = _event_type(line)
                if kind == "RUN_STARTED" and not self.confirmed and not self.closed:
                    if not await self.confirm():
                        with contextlib.suppress(Exception):
                            await gen.aclose()  # type: ignore[attr-defined]
                        return
                elif kind == "RUN_ERROR":
                    self.errored = True
                yield line
        except Exception:
            self.errored = True
            raise


@dataclass(frozen=True)
class Outcome:
    """What :func:`begin` decided, and the stored run id when it names one."""

    kind: str
    run_id: str = ""
    claim: SendClaim | None = None


async def _stream_run_id(thread_id: str) -> str:
    """The ``runId`` of the first ``RUN_STARTED`` at the head of the thread's
    stream, or ``""``. One ``XRANGE``, which never blocks (the rule of
    ``chat_fold``'s ``expect_run_id``)."""
    from orchestrator import stream_relay

    events = await _step(stream_relay.stream_head(thread_id, count=5))
    started = next((e for e in events if e.get("type") == "RUN_STARTED"), None)
    return str((started or {}).get("runId") or "")


async def _live_run_id(r: Any, org: str, thread_id: str) -> str:
    """The run id of the thread's live run, from the org's live-run index.
    ``start_detached`` writes it before the run sends ``RUN_STARTED``."""
    from orchestrator import stream_relay

    raw = await _step(r.hget(stream_relay._live_runs_key(org), thread_id))
    try:
        held = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return ""
    return str(held.get("runId") or "") if isinstance(held, dict) else ""


async def _named(r: Any, org: str, thread_id: str, run_id: str) -> bool:
    """True when the stream or the live run of the thread is *run_id*. Only
    such a run is ever replayed (R1, R2)."""
    from orchestrator import stream_relay

    if await _stream_run_id(thread_id) == run_id:
        return True
    return bool(await _step(stream_relay.is_active(thread_id))) and \
        await _live_run_id(r, org, thread_id) == run_id


async def begin(
    org: str, thread_id: str, member: str, sid: str, text: str,
) -> Outcome:
    """Claim the key of this send, or decide what its repeat gets.

    The route calls it after the room check and the edit guard, and before
    the route decision and the run cap. *org* and *member* come from the
    session. It never raises. A Redis fault gives :data:`OPEN`, and a step
    past :data:`BEGIN_TIMEOUT_SECONDS` gives :data:`BUSY`, never a run.
    """
    try:
        return await asyncio.wait_for(
            _begin(org, thread_id, member, sid, text), BEGIN_TIMEOUT_SECONDS)
    except TIMEOUT_ERRORS:
        _log.warning("send_once.timeout", thread_id=thread_id[:12])
        return Outcome(BUSY)


async def _is_stale(held: dict[str, str], thread_id: str) -> bool:
    """R3, after the wait of R5 ran out with nothing that names the value: the
    request of the value died or left before its run began.

    A claim (no run id) is stale when no run is live on the thread. A pending
    run is stale when it is :data:`PENDING_STALE_SECONDS` old.
    """
    if held["run_id"]:
        return _age(held) >= PENDING_STALE_SECONDS
    from orchestrator import stream_relay

    return not await _step(stream_relay.is_active(thread_id))


async def _claim_step(r: Any, k: Any, claim_value: str) -> bool | None:
    """The SET NX of a claim. True: claimed. False: the key holds a value.
    None: the step stalled.

    F-C of the review: a Redis that stalls on the FIRST claim of a send does
    not answer, as a refusing one does not (R10). No key is known yet, so
    :func:`_begin` fails open. A stall in a later round comes after the send
    found a key, so :func:`_begin` gives BUSY, never a run.
    """
    try:
        return bool(await _step(r.set(k, claim_value, nx=True, ex=CLAIM_TTL_SECONDS)))
    except TIMEOUT_ERRORS:
        return None


async def _begin(
    org: str, thread_id: str, member: str, sid: str, text: str,
) -> Outcome:
    digest = text_digest(text)
    token = uuid.uuid4().hex
    claim_value = _encode("", member, digest, token, CLAIM)
    try:
        from acb_common.tenant_redis import organization_scope

        with organization_scope(org):
            r = await _client()
            k = send_key(org, thread_id, member, sid)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + WAIT_SECONDS
            seen: Any = None
            stale_takes = 0
            for round_no in range(_max_rounds()):
                claimed = await _claim_step(r, k, claim_value)
                if claimed is None:
                    # Only the first round knows no key. A later round found
                    # one, so a stall there is BUSY, never the old path.
                    verdict = OPEN if round_no == 0 else BUSY
                    _log.warning("send_once.claim_stalled", verdict=verdict,
                                 thread_id=thread_id[:12])
                    return Outcome(verdict)
                if claimed:
                    return Outcome(NEW, claim=SendClaim(
                        org=org, thread_id=thread_id, member=_member(member),
                        sid=sid, digest=digest, token=token, value=claim_value,
                    ))
                raw = await _step(r.get(k))
                if raw is None:
                    continue  # it ran out or its run ended: claim it again
                if raw != seen:
                    # Each value gets its own full wait, also a claim that
                    # another repeat made a moment ago.
                    seen, deadline = raw, loop.time() + WAIT_SECONDS
                held = _decode(raw)
                if held is None or held["member"] != _member(member):
                    # Not a value of this module: take the old path.
                    _log.warning("send_once.unreadable", thread_id=thread_id[:12])
                    return Outcome(OPEN)
                if held["sha256"] != digest:
                    _log.info("send_once.id_reused", thread_id=thread_id[:12])
                    return Outcome(REUSED)
                run_id = held["run_id"]
                if run_id and await _named(r, org, thread_id, run_id):
                    _log.info("send_once.repeat", thread_id=thread_id[:12], verdict=REPLAY)
                    return Outcome(REPLAY, run_id=run_id)
                waiting = loop.time() < deadline
                if run_id and held["state"] == RUN:
                    # R2: a run that began, and that the stream no longer
                    # holds. A fresh one may be on its way to the stream.
                    if waiting and _age(held) < FRESH_SECONDS:
                        await asyncio.sleep(POLL_SECONDS)
                        continue
                    _log.info("send_once.repeat", thread_id=thread_id[:12], verdict=FINISHED)
                    return Outcome(FINISHED, run_id=run_id)
                if waiting:
                    await asyncio.sleep(POLL_SECONDS)
                    continue
                if not await _is_stale(held, thread_id) or stale_takes >= MAX_STALE_TAKES:
                    _log.info("send_once.busy", thread_id=thread_id[:12])
                    return Outcome(BUSY)
                stale_takes += 1
                await _step(r.delete_if(k, raw))
                _log.info("send_once.stale_taken", thread_id=thread_id[:12],
                          pending=bool(run_id))
            return Outcome(BUSY)
    except TIMEOUT_ERRORS:
        # A stall after the first claim: :func:`begin` gives BUSY. The
        # TimeoutError of redis-py is not the builtin one, so both count.
        raise
    except Exception as exc:
        _log.warning("send_once.open", error=type(exc).__name__)
        return Outcome(OPEN)
