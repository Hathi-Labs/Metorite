"""Two recovery races the review of #797 found. Each test interleaves two requests.

The fake Redis yields to the event loop on every call, as a real client does,
so two requests really do interleave at each round trip.

* **Race 1, the late claimant.** "Is it dead?" and "take the claim" are two
  steps. Between them a rival finishes its own recovery and starts a new run,
  whose ``claim_run`` deletes the claim. Before the fix, the late claimant got
  the claim and closed the live NEW run. ``claim_dead_run`` now looks again
  after it claims, and stands down when the run moved on.
* **Race 2, the slow fold.** The claim had a fixed 15 s TTL and nothing
  renewed it. A fold slower than that let a second claimant through, and two
  runs started. The holder now renews the claim while it works.

Mutations this file kills (R7): drop the re-check after the claim (race 1),
and drop the keeper (race 2).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from tests.unit.test_chat_deploy_recovery import (  # noqa: F401 — fixtures load by name
    _ALICE,
    _DEAD,
    _FakeRedis,
    _record,
    _run,
    _seed_run,
    no_persist,
    stream_relay,
)

run_liveness = pytest.importorskip("orchestrator.run_liveness")


class _TtlRedis(_FakeRedis):
    """The fake, plus key expiry, so a claim can lapse as it does in Redis."""

    def __init__(self) -> None:
        super().__init__()
        self.deadline: dict[str, float] = {}
        self.yield_io = True

    def _purge(self) -> None:
        now = time.monotonic()
        for k, t in list(self.deadline.items()):
            if t <= now:
                self.deadline.pop(k, None)
                self.store.pop(k, None)

    async def _io(self):
        self._purge()
        await asyncio.sleep(0)

    async def set(self, key, value, ex=None, xx=False, nx=False, **kw):
        ok = await super().set(key, value, ex=ex, xx=xx, nx=nx, **kw)
        if ok and ex is not None and key.startswith("cc:recover:"):
            self.deadline[key] = time.monotonic() + ex
        elif ok:
            self.deadline.pop(key, None)
        return ok

    async def expire(self, key, seconds, *_a, **_kw):
        self._purge()
        if key in self.store and key.startswith("cc:recover:"):
            self.deadline[key] = time.monotonic() + seconds
        return key in self.store

    async def delete(self, *keys):
        for k in keys:
            self.deadline.pop(k, None)
        return await super().delete(*keys)


@pytest.fixture
def liveness(monkeypatch):
    r = _TtlRedis()

    async def _get_client():
        return r

    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    monkeypatch.setattr(stream_relay, "_DETACHED_TASKS", {})
    monkeypatch.setattr(stream_relay, "_LOCAL_CONTROL_HANDLERS", {})
    monkeypatch.setattr(stream_relay, "_CONTROL_LISTENERS", {})
    monkeypatch.setattr(stream_relay, "CONTROL_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(run_liveness, "_LOCAL_RUNS", set())
    monkeypatch.setattr(run_liveness, "_KEEPERS", {})
    monkeypatch.setattr(run_liveness, "ensure_heartbeat", lambda: None)
    monkeypatch.setattr(run_liveness, "_ON_INTERRUPTED", None)
    return r


def _gate_first_claim(monkeypatch) -> asyncio.Event:
    """Hold the FIRST caller of claim_recovery until the returned event is set."""
    real = run_liveness.claim_recovery
    gate = asyncio.Event()
    calls: list[int] = []

    async def _claim(tid):
        calls.append(1)
        if len(calls) == 1:
            await gate.wait()
        return await real(tid)

    monkeypatch.setattr(run_liveness, "claim_recovery", _claim)
    return gate


# ---------------------------------------------------------------------------
# Race 1: the late claimant
# ---------------------------------------------------------------------------

def test_a_late_claimant_never_closes_the_new_run(liveness, no_persist, monkeypatch):
    """B sees the run dead and goes to claim. A recovers it first and starts
    the next run, whose start deletes the claim. B then gets the claim, and
    must stand down and steer into A's run, not close it."""
    from gateway.routes import agent as agent_routes
    from orchestrator.steer import Route

    tid = "t-late"
    _seed_run(liveness, tid, _DEAD, record=_record(tid))

    async def _go():
        gate = _gate_first_claim(monkeypatch)
        loop = asyncio.get_running_loop()
        late = loop.create_task(agent_routes._route_incoming_turn(tid, _ALICE, "two"))
        await asyncio.sleep(0.05)  # B has looked, and waits at its claim
        won = await agent_routes._route_incoming_turn(tid, _ALICE, "one")
        # A's new run starts. Its claim_run deletes the claim.
        await stream_relay.mark_active(tid, reset=True, actor=_ALICE)
        gate.set()
        return won, await late

    won, late = _run(_go())
    assert won.route is Route.ENGAGE and won.reason == "dead_run_recovered"
    assert late.route is Route.STEER, "the late claimant joins the new run"
    assert liveness.store.get(f"cc:active:{tid}") == "1", "the new run is still live"
    assert no_persist == [f"asst-{tid}"], "the dead run was closed once"
    assert f"cc:recover:{tid}" not in liveness.store, "the late claim was released"


def test_the_sweep_never_closes_a_run_a_request_just_started(
    liveness, no_persist, monkeypatch,
):
    """The same race with the sweep as the late claimant."""
    from gateway import chat_recovery
    from gateway.routes import agent as agent_routes

    tid = "t-sweep-late"
    _seed_run(liveness, tid, _DEAD, record=_record(tid))
    monkeypatch.setattr(run_liveness, "_ON_INTERRUPTED", chat_recovery.persist_interrupted)

    async def _go():
        gate = _gate_first_claim(monkeypatch)
        sweep = asyncio.get_running_loop().create_task(run_liveness.sweep_dead_instances())
        await asyncio.sleep(0.05)
        await agent_routes._route_incoming_turn(tid, _ALICE, "one")
        await stream_relay.mark_active(tid, reset=True, actor=_ALICE)
        gate.set()
        return await sweep

    closed = _run(_go())
    assert closed == []
    assert liveness.store.get(f"cc:active:{tid}") == "1"
    assert no_persist == [f"asst-{tid}"]


# ---------------------------------------------------------------------------
# Race 2: a fold slower than the claim's TTL
# ---------------------------------------------------------------------------

def test_a_slow_fold_keeps_its_claim(liveness, monkeypatch):
    """A's fold takes 2.5 TTLs. B tries to claim the whole time, and must
    never get it. When A is done, the claim and its keeper are gone."""
    from gateway import chat_fold
    from gateway.chat_recovery import recover_dead_run

    monkeypatch.setattr(run_liveness, "RECOVER_TTL_SECONDS", 0.4)
    tid = "t-slow"
    _seed_run(liveness, tid, _DEAD, record=_record(tid))

    rival_got: list[str] = []

    async def _rival(stop: asyncio.Event):
        while not stop.is_set():
            token = await run_liveness.claim_recovery(tid)
            if token is not None:
                rival_got.append(token)
                await run_liveness.release_recovery(tid, token)
            await asyncio.sleep(0.05)

    async def _slow_fold(_tid, mid, **_kw):
        # A holds the claim now. B tries to take it for the whole fold.
        stop = asyncio.Event()
        rival = asyncio.get_running_loop().create_task(_rival(stop))
        await asyncio.sleep(1.0)
        stop.set()
        await rival
        return {"content": "old half"}

    monkeypatch.setattr(chat_fold, "persist_final_assistant_message", _slow_fold)

    async def _go():
        return await recover_dead_run(tid, why="test")

    rec = _run(_go())
    assert rec is not None and rec["messageId"] == f"asst-{tid}"
    assert rival_got == [], "a second claimant got in while the fold ran"
    assert f"cc:recover:{tid}" not in liveness.store
    assert run_liveness._KEEPERS == {}


def test_a_held_claim_is_renewed_until_the_new_run_starts(liveness, monkeypatch):
    """hold=True: the winner keeps the claim for the run it starts. The keeper
    renews it past the TTL, and stops when that run's claim_run deletes it."""
    monkeypatch.setattr(run_liveness, "RECOVER_TTL_SECONDS", 0.3)
    tid = "t-held"
    _seed_run(liveness, tid, _DEAD, record=_record(tid))

    async def _go():
        token = await run_liveness.claim_dead_run(tid)
        await asyncio.sleep(0.9)  # three TTLs
        held = await run_liveness.recovery_in_progress(tid)
        await stream_relay.mark_active(tid, reset=True, actor=_ALICE)
        await asyncio.sleep(0.25)
        return token, held, await run_liveness.recovery_in_progress(tid)

    token, held, after = _run(_go())
    assert token and held is True and after is False
    _keeper = run_liveness._KEEPERS.get(tid)
    assert _keeper is None or _keeper[1].done()


def test_a_dead_holder_lets_its_claim_lapse(liveness, monkeypatch):
    """No keeper, no renewal: a claim whose holder died is free within a TTL."""
    monkeypatch.setattr(run_liveness, "RECOVER_TTL_SECONDS", 0.2)

    async def _go():
        token = await run_liveness.claim_recovery("t-lapse")
        await asyncio.sleep(0.35)
        return token, await run_liveness.claim_recovery("t-lapse")

    first, second = _run(_go())
    assert first and second, "the fake must let an unrenewed claim lapse"


def test_the_record_used_by_the_recheck_is_the_run_id(liveness):
    """The re-check compares the run id the record names."""
    _seed_run(liveness, "t-id", _DEAD, record=_record("t-id"))
    snap = _run(run_liveness._dead_run_snapshot("t-id"))
    assert snap == (_DEAD, "run-t-id")
    liveness.store[f"cc:instance-runs:{_DEAD}"]["t-id"] = json.dumps({"runId": "run-other"})
    assert _run(run_liveness._dead_run_snapshot("t-id")) == (_DEAD, "run-other")
