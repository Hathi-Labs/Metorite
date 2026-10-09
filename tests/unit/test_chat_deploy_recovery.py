"""A chat recovers from a gateway restart (incident 2026-10-09, 16:18 UTC).

The owner sent a message in the Projects assistant while a deploy ran. The run
was parked on a question card, and the restart killed it. ``cc:active:{tid}``
survived the restart with its 1 h TTL. So every later message in that thread
was routed as a STEER into a run that no longer existed: ``control_undelivered``,
``steer.pending_replay stored=True``, and no answer, four times.

This file fences the fix. Each test names the rule it holds (R7):

* **The P1 from the #791 review.** The executor's own ``mark_active`` call,
  inside every detached run, deleted the actor, the source and the floor that
  ``run_detached`` had just written.
* **Liveness.** A run belongs to one process (``cc:runowner``), and that
  process proves it is alive with a heartbeat key (``cc:instance``).
* **A dead run is never steered into.** A steer that no process can deliver
  clears the dead run and starts a fresh one, in the same request, which
  replays the steers stored for it.
* **The sweep.** A process finds the runs of a dead process, marks them
  inactive, and stamps a ``run_interrupted`` marker into the saved reply.
* **A card answer across a restart** is never dropped. It comes back as a
  message the member's browser sends, with the question it answers.

Hermetic: a fake Redis that holds strings, hashes, sets, lists and streams.
No SQL changes in this PR, so no R8 half.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

stream_relay = pytest.importorskip(
    "orchestrator.stream_relay", reason="orchestrator not installed",
)

_ALICE = "alice@recover.test"
_ORG = "11111111-1111-4111-8111-111111111111"


# ---------------------------------------------------------------------------
# A fake raw Redis
# ---------------------------------------------------------------------------

class _FakePubSub:
    def __init__(self, r: "_FakeRedis") -> None:
        self.r = r
        self.channels: list[str] = []

    async def subscribe(self, channel: str) -> None:
        self.channels.append(channel)
        self.r.subscribers[channel] = self.r.subscribers.get(channel, 0) + 1

    async def unsubscribe(self, channel: str) -> None:
        if self.r.subscribers.get(channel):
            self.r.subscribers[channel] -= 1

    async def listen(self):
        while True:  # a listener that never hears a command
            await asyncio.sleep(3600)
            yield {}

    async def aclose(self) -> None:
        for c in self.channels:
            await self.unsubscribe(c)
        self.channels = []


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.subscribers: dict[str, int] = {}
        self.published: list[tuple[str, dict]] = []
        self._seq = 0

    # strings
    async def set(self, key, value, ex=None, xx=False, nx=False, **_kw):
        if xx and key not in self.store:
            return None
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        v = self.store.get(key)
        return v if isinstance(v, str) else None

    async def delete(self, *keys):
        n = 0
        for k in keys:
            n += 1 if self.store.pop(k, None) is not None else 0
        return n

    async def expire(self, *_a, **_kw):
        return True

    async def exists(self, *keys):
        return sum(1 for k in keys if k in self.store)

    async def rename(self, src, dst):
        if src not in self.store:
            raise RuntimeError("no such key")
        self.store[dst] = self.store.pop(src)
        return True

    # hashes
    async def hset(self, key, field, value):
        self.store.setdefault(key, {})[field] = value
        return 1

    async def hget(self, key, field):
        return (self.store.get(key) or {}).get(field)

    async def hgetall(self, key):
        return dict(self.store.get(key) or {})

    async def hdel(self, key, *fields):
        h = self.store.get(key) or {}
        n = 0
        for f in fields:
            n += 1 if h.pop(f, None) is not None else 0
        if key in self.store and not h:
            self.store.pop(key, None)
        return n

    # sets
    async def sadd(self, key, *members):
        s = self.store.setdefault(key, set())
        before = len(s)
        s.update(members)
        return len(s) - before

    async def srem(self, key, *members):
        s = self.store.get(key) or set()
        for m in members:
            s.discard(m)
        if key in self.store and not s:
            self.store.pop(key, None)
        return True

    async def smembers(self, key):
        return set(self.store.get(key) or set())

    # lists
    async def rpush(self, key, *values):
        self.store.setdefault(key, []).extend(values)
        return len(self.store[key])

    async def ltrim(self, key, start, end):
        lst = self.store.get(key) or []
        n = len(lst)
        s = start if start >= 0 else max(n + start, 0)
        e = end if end >= 0 else n + end
        self.store[key] = lst[s:e + 1]
        return True

    async def lrange(self, key, start, end):
        lst = list(self.store.get(key) or [])
        return lst[start:] if end == -1 else lst[start:end + 1]

    async def lrem(self, key, count, value):
        lst = self.store.get(key) or []
        if value in lst:
            lst.remove(value)
        return 1

    # streams
    async def xadd(self, key, fields, maxlen=None, approximate=True):
        self._seq += 1
        eid = f"{1_700_000_000_000 + self._seq}-0"
        self.store.setdefault(key, []).append((eid, dict(fields)))
        return eid

    async def xread(self, streams, count=None, block=None):
        out = []
        for key, cursor in streams.items():
            entries = self.store.get(key) or []
            if cursor in ("$",):
                continue
            after = [
                (eid, f) for eid, f in entries
                if cursor in ("0", "0-0") or _eid_gt(eid, cursor)
            ]
            if count:
                after = after[:count]
            if after:
                out.append((key, after))
        return out

    # pub/sub
    async def publish(self, channel, message):
        self.published.append((channel, json.loads(message)))
        return self.subscribers.get(channel, 0)

    def pubsub(self):
        return _FakePubSub(self)

    async def aclose(self):
        return None


def _eid_gt(a: str, b: str) -> bool:
    am, _, aseq = a.partition("-")
    bm, _, bseq = b.partition("-")
    return (int(am), int(aseq or 0)) > (int(bm), int(bseq or 0))


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()

    async def _get_client():
        return r

    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    monkeypatch.setattr(stream_relay, "_DETACHED_TASKS", {})
    monkeypatch.setattr(stream_relay, "_LOCAL_CONTROL_HANDLERS", {})
    monkeypatch.setattr(stream_relay, "_CONTROL_LISTENERS", {})
    # No real sleep in the zero-subscriber retry.
    monkeypatch.setattr(stream_relay, "CONTROL_ACK_TIMEOUT", 0.05)
    return r


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. The P1 from the #791 review — the run's facts survive the executor
# ---------------------------------------------------------------------------

def test_the_executor_prologue_keeps_the_run_facts(fake_redis, monkeypatch):
    """The REAL ``run_agent_stream`` prologue, wrapped in ``run_detached``.

    ``run_detached`` writes the actor, the source and the floor. The executor
    then calls ``mark_active(thread_id)`` with none of them, before it yields
    ``RUN_STARTED``. All three must still hold at that moment, or the steer
    floor fails open, the supersede guard never answers 409, and a cron run
    reads as a person's.
    """
    from orchestrator import executor

    tid = "thread-p1-fence"
    seen: dict[str, Any] = {}

    async def _no_subscriber(*_a, **_kw):
        if False:  # pragma: no cover - an async generator that yields nothing
            yield {}

    monkeypatch.setattr(stream_relay, "subscribe_events", _no_subscriber)

    # Stop the run right after its prologue. RUN_STARTED is yielded before
    # this, so the facts are read at the exact moment the fence names.
    async def _refuse(_name):
        raise RuntimeError("stop after the prologue")

    monkeypatch.setattr(executor, "_assert_may_run_agent", _refuse)

    async def _wrapped():
        async for line in executor.run_agent_stream(
            "projects-assistant", {"message": "hi", "source": "schedule"},
            run_id="run-p1", thread_id=tid,
        ):
            if '"RUN_STARTED"' in line and "actor" not in seen:
                seen["actor"] = await stream_relay.get_run_actor(tid)
                seen["source"] = await stream_relay.get_run_source(tid)
                seen["floor"] = await stream_relay.get_run_floor(tid)
            yield line

    async def _go():
        async for _ in stream_relay.run_detached(
            tid, _wrapped(), actor=_ALICE, source="schedule",
            floor=[_ALICE, "bob@recover.test"],
        ):
            pass
        task = stream_relay._DETACHED_TASKS.get(tid)
        if task is not None:
            await asyncio.wait_for(task, timeout=10)

    _run(_go())
    assert seen, "the run never yielded RUN_STARTED"
    assert seen["actor"] == _ALICE
    assert seen["source"] == "schedule"
    assert seen["floor"] == sorted([_ALICE, "bob@recover.test"])


def test_a_fresh_run_still_drops_the_previous_runs_facts(fake_redis):
    """``reset=True`` is a run boundary: an omitted fact is cleared there."""
    tid = "thread-boundary"

    async def _go():
        await stream_relay.mark_active(
            tid, reset=True, actor=_ALICE, source="schedule", floor=[_ALICE],
        )
        await stream_relay.mark_active(tid, reset=True)
        return (
            await stream_relay.get_run_actor(tid),
            await stream_relay.get_run_source(tid),
            await stream_relay.get_run_floor(tid),
        )

    assert _run(_go()) == (None, "", None)
