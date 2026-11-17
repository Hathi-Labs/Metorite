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
compare never mixes two requests) and ``at`` (the time of the last write).

**Two states.**

1. The claim: ``run_id`` is empty. :func:`begin` writes it with SET NX and
   :data:`CLAIM_TTL_SECONDS`, before the route decides what the send does.
2. The run: ``run_id`` is set, with :data:`RUN_TTL_SECONDS`. The route
   writes it with :meth:`SendClaim.start` as the first step of its stream,
   BEFORE ``run_detached``. The write is ONE compare and set
   (``TenantRedis.replace_if``) from the claim of this request. A request
   whose write fails starts nothing. So two runs of one send cannot both
   start, and a run never starts and then stops.

**A repeat** (SET NX fails) reads the value:

* another digest: :data:`REUSED` (the route answers 409 ``send_id_reused``);
* a run whose ``RUN_STARTED`` the stream holds, or which is the live run of
  the thread: :data:`REPLAY` (the route replays it, with ``X-Run-Id``);
* a run that the stream does not hold: :data:`FINISHED` (the route answers
  the synthetic ``RUN_FINISHED``, and the client reads the saved row). A run
  written less than :data:`FRESH_SECONDS` ago may not have reset the stream
  yet, so the repeat waits for it first;
* a claim: it polls for :data:`WAIT_SECONDS`. A claim that stays, with no
  live run on the thread, is stale: a compare and delete removes it, and the
  repeat claims the key again. A claim that stays while a run is live gives
  :data:`BUSY` (409 ``send_in_progress``).

**The end of a run.** :meth:`SendClaim.settle_end` deletes the key after a
``RUN_ERROR``, a Stop (``steer.was_stopped``) or a run that never started.
A deliberate resend can then run. A clean end keeps the key for 24 hours.

**Fail open.** With Redis down, :func:`begin` gives :data:`OPEN`, and the send
takes the old path (R10). With the flag off, the route makes no call here.

Fence: ``tests/unit/test_send_once.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger

_log = get_logger("orchestrator.send_once")

#: The namespace of the key, under the tenant prefix.
NAMESPACE = "send-once"
#: The life of a claim that has no run yet (§6 HRD-17a, open choice (d)).
CLAIM_TTL_SECONDS = 60
#: The life of the key of a run that started.
RUN_TTL_SECONDS = 24 * 3600
#: How long a repeat waits for the first request to start its run (R5,
#: open choice (c)).
WAIT_SECONDS = 2.0
#: A run written to the key less than this long ago may still be on its way
#: to the stream, so a repeat waits for it before it answers FINISHED.
FRESH_SECONDS = 10.0
#: The step of that wait.
POLL_SECONDS = 0.1
#: The most full waits of one :func:`begin`. Each new value of the key gets
#: its own wait, so a slow chain of claims cannot hold a request forever.
MAX_WAITS = 3
#: The most stale claims that one :func:`begin` deletes before it gives up.
MAX_STALE_TAKES = 2


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
BUSY = "busy"          # the first send has not started, and a run is live
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


def _encode(run_id: str, member: str, digest: str, claim: str) -> str:
    return json.dumps(
        {"run_id": run_id, "member": _member(member), "sha256": digest, "claim": claim,
         "at": round(time.time(), 3)},
        sort_keys=True, separators=(",", ":"),
    )


def _decode(raw: Any) -> dict[str, str] | None:
    try:
        held = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(held, dict):
        return None
    return {k: str(held.get(k) or "") for k in ("run_id", "member", "sha256", "claim", "at")}


def _age(held: dict[str, str]) -> float:
    """Seconds since the value was written. An unreadable time is old."""
    try:
        return time.time() - float(held.get("at") or 0)
    except ValueError:
        return float("inf")


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
    run_id: str = ""
    started: bool = False
    errored: bool = False
    #: Another request holds the key now, so this run must not start.
    lost: bool = False
    #: The key is gone or released. No call writes it again.
    closed: bool = False
    #: The run pulled its first line, so a drain owns it and settles the key.
    pulled: bool = False

    async def start(self, run_id: str) -> bool:
        """Move the claim to the run. The route calls it right BEFORE
        ``run_detached``, so nothing has started yet.

        True: the run may start. False: another request took the key, so this
        request starts nothing. A Redis fault gives True, because the key
        fails open (R10).
        """
        if self.closed or self.started:
            return True
        new = _encode(run_id, self.member, self.digest, self.token)
        try:
            from acb_common.tenant_redis import organization_scope

            with organization_scope(self.org):
                r = await _client()
                k = send_key(self.org, self.thread_id, self.member, self.sid)
                ok = bool(await r.replace_if(k, self.value, new, RUN_TTL_SECONDS))
                if not ok:
                    # The claim ran out with no taker: the key is free.
                    ok = bool(await r.set(k, new, nx=True, ex=RUN_TTL_SECONDS))
        except Exception as exc:
            _log.warning("send_once.start_open", error=type(exc).__name__)
            self.closed = True
            return True
        if not ok:
            self.lost = True
            _log.warning("send_once.start_lost", thread_id=self.thread_id[:12])
            return False
        self.value, self.run_id, self.started = new, run_id, True
        return True

    async def release(self) -> None:
        """Delete this request's own value, and never a newer one. Never raises."""
        if self.closed:
            return
        self.closed = True
        try:
            from acb_common.tenant_redis import organization_scope

            with organization_scope(self.org):
                r = await _client()
                await r.delete_if(
                    send_key(self.org, self.thread_id, self.member, self.sid), self.value,
                )
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

            return bool(await was_stopped(self.thread_id))
        return False

    async def settle_end(self, *, stopped: bool | None = None) -> None:
        """At the end of the run (R4): delete the key after a ``RUN_ERROR``, a
        Stop or a run that never started. A clean end keeps it. The caller
        passes *stopped* when it read the Stop mark earlier, before a new
        send of the thread can clear it."""
        if stopped is None:
            stopped = await self.was_stopped()
        if self.errored or stopped or not self.started:
            await self.release()

    async def watch(self, gen: AsyncIterator[str]) -> AsyncIterator[str]:
        """Pass the run's SSE lines on unchanged, and note a ``RUN_ERROR``
        (R4). A run that raises counts as one. It writes nothing, so a tier
        fallback or a parallel restart inside the run never touches the key
        (R9)."""
        self.pulled = True
        try:
            async for line in gen:
                if _event_type(line) == "RUN_ERROR":
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
    """The ``runId`` of the first ``RUN_STARTED`` that the thread's stream
    holds, or ``""``. The same rule as ``chat_fold`` (``expect_run_id``)."""
    from orchestrator import stream_relay

    events = await stream_relay.replay_events(thread_id, since_id="0-0", count=5)
    started = next((e for e in events if e.get("type") == "RUN_STARTED"), None)
    return str((started or {}).get("runId") or "")


async def _live_run_id(r: Any, org: str, thread_id: str) -> str:
    """The run id of the thread's live run, from the org's live-run index.
    ``start_detached`` writes it before the run sends ``RUN_STARTED``."""
    from orchestrator import stream_relay

    raw = await r.hget(stream_relay._live_runs_key(org), thread_id)
    held = _decode_any(raw)
    return str(held.get("runId") or "")


def _decode_any(raw: Any) -> dict[str, Any]:
    try:
        held = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


async def _run_verdict(
    r: Any, org: str, thread_id: str, run_id: str, *, fresh: bool, deadline: float,
) -> str:
    """:data:`REPLAY` or :data:`FINISHED` for a repeat of the run *run_id*.

    It replays only a run that is provably *run_id*: the first
    ``RUN_STARTED`` of the stream, or the live run of the index. A stream
    that holds another run is never replayed (R2). A *fresh* run may still
    be on its way: its ``start_detached`` has not reset the stream, or its
    ``RUN_STARTED`` has not landed. So the repeat waits up to *deadline*
    first. An old run gets its answer at once.
    """
    from orchestrator import stream_relay

    loop = asyncio.get_running_loop()
    while True:
        if await _stream_run_id(thread_id) == run_id:
            return REPLAY
        if await stream_relay.is_active(thread_id) and \
                await _live_run_id(r, org, thread_id) == run_id:
            return REPLAY  # live, and the head of its stream is trimmed
        if not fresh or loop.time() >= deadline:
            return FINISHED
        await asyncio.sleep(POLL_SECONDS)


async def begin(
    org: str, thread_id: str, member: str, sid: str, text: str,
) -> Outcome:
    """Claim the key of this send, or decide what its repeat gets.

    The route calls it after the room check and the edit guard, and before
    the route decision and the run cap. *org* and *member* come from the
    session. It never raises: a Redis fault gives :data:`OPEN`.
    """
    digest = text_digest(text)
    token = uuid.uuid4().hex
    claim_value = _encode("", member, digest, token)
    try:
        from acb_common.tenant_redis import organization_scope

        from orchestrator import stream_relay

        with organization_scope(org):
            r = await _client()
            k = send_key(org, thread_id, member, sid)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + WAIT_SECONDS
            seen: Any = None
            stale_takes = 0
            for _ in range(_max_rounds()):
                if await r.set(k, claim_value, nx=True, ex=CLAIM_TTL_SECONDS):
                    return Outcome(NEW, claim=SendClaim(
                        org=org, thread_id=thread_id, member=_member(member),
                        sid=sid, digest=digest, token=token, value=claim_value,
                    ))
                raw = await r.get(k)
                if raw is None:
                    continue  # it ran out or its run ended: claim it again
                if raw != seen:
                    # Each claim gets its own full wait, also a claim that
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
                if held["run_id"]:
                    verdict = await _run_verdict(
                        r, org, thread_id, held["run_id"],
                        fresh=_age(held) < FRESH_SECONDS, deadline=deadline,
                    )
                    _log.info("send_once.repeat", thread_id=thread_id[:12], verdict=verdict)
                    return Outcome(verdict, run_id=held["run_id"])
                if loop.time() < deadline:
                    await asyncio.sleep(POLL_SECONDS)
                    continue
                # R5 ran out with no run id. R3: with no live run, the
                # request that claimed it died before its run started.
                if await stream_relay.is_active(thread_id) or stale_takes >= MAX_STALE_TAKES:
                    _log.info("send_once.busy", thread_id=thread_id[:12])
                    return Outcome(BUSY)
                stale_takes += 1
                await r.delete_if(k, raw)
                _log.info("send_once.stale_taken", thread_id=thread_id[:12])
            return Outcome(BUSY)
    except Exception as exc:
        _log.warning("send_once.open", error=type(exc).__name__)
        return Outcome(OPEN)
