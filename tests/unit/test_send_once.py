"""HRD-17a: the server runs one send once (chat_failure_modes.md §6, R1 to R11).

Each test names the rule it holds (R7). The module runs under the REAL
``TenantRedis`` over a fake raw client, so a key that skips the wrapper, the
org or the member fails here. The fake keeps strings with a TTL, and it runs
the two fixed scripts of the wrapper (``delete_if``, ``replace_if``).

Mutations this suite catches (each was made, run red, and restored):

* the member leaves the key: a room member with the same id joins the run;
* the org leaves the key: org B with the same ids joins the run of org A;
* the digest check goes: other words with the same id replay the first run;
* the run-id match goes: a repeat replays the NEWER run of the thread;
* the stale rule goes: a claim that its request left forever blocks the send;
* the flag off still calls Redis: the fake sees a call;
* the replay forgets ``X-Run-Id``: the header test fails;
* the key skips ``TenantKey``: the wrapper refuses it, and no send claims.

Hermetic: Redis only, no SQL, so R8 does not apply.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

send_once = pytest.importorskip("orchestrator.send_once")
stream_relay = pytest.importorskip("orchestrator.stream_relay")

from acb_common import tenant_redis as tr  # noqa: E402
from acb_common.tenant_redis import TenantKey, TenantRedis, organization_scope  # noqa: E402

ORG_A = "aaaaaaaa-1111-4111-8111-111111111111"
ORG_B = "bbbbbbbb-2222-4222-8222-222222222222"
THREAD = "thread-once-1"
ALICE = "alice@once.test"
BOB = "bob@once.test"
SID = "nanoid_ABCdef123456789"  # 22 characters of the browser alphabet
RUN = "run-1111"
RUN_NEW = "run-2222"


class FakeRaw:
    """A raw Redis client: strings with a TTL, hashes, and the two scripts."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.down = False
        self.yield_io = False

    async def _io(self, *call: Any) -> None:
        self.calls.append(call)
        if self.down:
            raise ConnectionError("redis down")
        if self.yield_io:
            await asyncio.sleep(0)

    async def set(self, k: str, v: str, nx: bool = False, ex: int | None = None,
                  **_kw: Any) -> bool | None:
        await self._io("set", k, v, {"nx": nx, "ex": ex})
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        if ex:
            self.ttl[k] = int(ex)
        return True

    async def get(self, k: str) -> str | None:
        await self._io("get", k)
        return self.kv.get(k)

    async def hget(self, k: str, field: str) -> str | None:
        await self._io("hget", k, field)
        return self.hashes.get(k, {}).get(field)

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        await self._io("eval", script, numkeys, *args)
        k = args[0]
        if script == tr._DELETE_IF_LUA:
            if self.kv.get(k) == args[1]:
                self.kv.pop(k, None)
                return 1
            return 0
        if script == tr._REPLACE_IF_LUA:
            if self.kv.get(k) == args[1]:
                self.kv[k] = args[2]
                self.ttl[k] = int(args[3])
                return 1
            return 0
        raise AssertionError(f"unexpected script {script!r}")


class World:
    """The relay facts that a repeat reads: the stream and the live flag."""

    def __init__(self) -> None:
        self.raw = FakeRaw()
        self.stream: dict[str, list[dict[str, Any]]] = {}
        self.active: dict[str, bool] = {}
        self.stopped = False

    def started(self, thread: str, run_id: str) -> None:
        self.stream[thread] = [{"type": "RUN_STARTED", "runId": run_id, "threadId": thread},
                               {"type": "TEXT_MESSAGE_CONTENT", "delta": f"answer of {run_id}"}]

    def live_index(self, org: str, thread: str, run_id: str) -> None:
        self.raw.hashes.setdefault(f"cc:{org}:liveruns", {})[thread] = json.dumps(
            {"actor": ALICE, "runId": run_id})


@pytest.fixture
def world(monkeypatch) -> World:
    w = World()

    async def _client():
        return TenantRedis(w.raw)

    async def _replay(thread_id, since_id="0-0", count=500, **_k):
        return list(w.stream.get(thread_id, []))[:count]

    async def _head(thread_id, count=5):
        return list(w.stream.get(thread_id, []))[:count]

    async def _is_active(thread_id):
        return w.active.get(thread_id, False)

    async def _was_stopped(_tid):
        return w.stopped

    from orchestrator import steer

    monkeypatch.setattr(send_once, "_client", _client)
    monkeypatch.setattr(stream_relay, "replay_events", _replay)
    monkeypatch.setattr(stream_relay, "stream_head", _head)
    monkeypatch.setattr(stream_relay, "is_active", _is_active)
    monkeypatch.setattr(steer, "was_stopped", _was_stopped)
    # Wide against a busy CI shard: each event of a wait test lands at
    # 0.05 s, and a wait test asserts only a lower bound.
    monkeypatch.setattr(send_once, "WAIT_SECONDS", 1.0)
    monkeypatch.setattr(send_once, "POLL_SECONDS", 0.01)
    return w


def _begin(org: str = ORG_A, member: str = ALICE, text: str = "hello",
           sid: str = SID, thread: str = THREAD) -> Any:
    return send_once.begin(org, thread, member, sid, text)


def _key(org: str = ORG_A, member: str = ALICE, thread: str = THREAD) -> str:
    with organization_scope(org):
        return send_once.send_key(org, thread, member, SID).value


def _held(w: World, org: str = ORG_A, member: str = ALICE) -> dict[str, Any] | None:
    raw = w.raw.kv.get(_key(org, member))
    return json.loads(raw) if raw else None


def _line(evt: dict[str, Any]) -> str:
    return f"data: {json.dumps(evt)}\n\n"


async def _drain(gen) -> list[str]:
    return [line async for line in gen]


async def _run_gen(run_id: str, *, error: bool = False, restart: bool = False):
    yield _line({"type": "RUN_STARTED", "runId": run_id, "threadId": THREAD})
    if restart:  # a tier restart inside the same run
        yield _line({"type": "RUN_STARTED", "runId": run_id, "threadId": THREAD})
    yield _line({"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"})
    if error:
        yield _line({"type": "RUN_ERROR", "message": "boom"})
    else:
        yield _line({"type": "RUN_FINISHED", "runId": run_id})


async def _first_run(w: World, run_id: str = RUN, **kw: Any) -> Any:
    """The first send claims, its route moves the key to the pending run, and
    its RUN_STARTED confirms the run."""
    first = await _begin(**kw)
    assert first.kind == send_once.NEW
    assert await first.claim.start(run_id)
    assert await first.claim.confirm()
    return first.claim


def _age_value(w: World, seconds: float, org: str = ORG_A) -> None:
    """Make the stored value *seconds* old."""
    held = _held(w, org=org)
    held["at"] = round(time.time() - seconds, 3)
    w.raw.kv[_key(org)] = json.dumps(held, sort_keys=True, separators=(",", ":"))


# ── The flag and the id (R7, R10) ────────────────────────────────────────────


def test_the_flag_is_empty_by_default(monkeypatch) -> None:
    from acb_common import get_settings
    from acb_common.settings import Settings

    assert Settings.model_fields["chat_send_once_orgs"].default == ""
    s = get_settings()
    monkeypatch.setattr(s, "chat_send_once_orgs", "")
    assert not send_once.send_once_on(ORG_A)
    monkeypatch.setattr(s, "chat_send_once_orgs", f" x , {ORG_A.upper()} ")
    assert send_once.send_once_on(ORG_A)
    assert not send_once.send_once_on(ORG_B)
    monkeypatch.setattr(s, "chat_send_once_orgs", "*")
    assert send_once.send_once_on(ORG_B)
    assert not send_once.send_once_on(None)  # no org, no key


@pytest.mark.parametrize("raw", [
    "", None, 42, "short7_", "x" * 65, "has:colon_123", "has space 123",
    "dot.in.id.123", "abc/def/ghi", "nanoid_ABCdef123456789\n",
])
def test_an_id_outside_the_rule_counts_as_no_id(raw) -> None:
    assert send_once.send_id(raw) == ""


@pytest.mark.parametrize("raw", [SID, "a" * 8, "Z-_9" * 16])
def test_a_browser_id_keys_the_send(raw) -> None:
    assert send_once.send_id(raw) == raw


# ── The key (R5 tenancy, rule (1) of the key) ────────────────────────────────


def test_the_key_is_a_tenant_key_with_a_fixed_shape() -> None:
    with organization_scope(ORG_A):
        k = send_once.send_key(ORG_A, "a:b", ALICE, SID)
    assert isinstance(k, TenantKey)
    parts = k.value.split(":")
    # cc, org, namespace, the thread (it may hold a colon), member hash, id
    assert parts[:3] == ["cc", ORG_A, "send-once"]
    assert parts[-1] == SID and len(parts[-2]) == 32 and ALICE not in k.value
    assert ":".join(parts[3:-2]) == "a:b"
    with organization_scope(ORG_A):
        assert send_once.send_key(ORG_A, THREAD, BOB, SID).value != _key()
    # No key builds without the bound tenant, and the wrapper refuses a str.
    with pytest.raises(tr.TenantNotBound):
        send_once.send_key(ORG_A, THREAD, ALICE, SID)
    with organization_scope(ORG_A), pytest.raises(TypeError, match="TenantKey"):
        asyncio.run(TenantRedis(FakeRaw()).get(_key()))  # type: ignore[arg-type]


async def test_the_first_send_claims_with_set_nx_and_a_60_s_ttl(world) -> None:
    out = await _begin()
    assert out.kind == send_once.NEW
    op, key, value, kw = world.raw.calls[0]
    assert (op, key, kw) == ("set", _key(), {"nx": True, "ex": 60})
    held = json.loads(value)
    assert held["run_id"] == "" and held["member"] == ALICE
    assert held["sha256"] == send_once.text_digest("hello") and held["claim"]
    assert out.claim.value == value


async def test_the_run_start_moves_the_key_to_the_run_for_24_h(world) -> None:
    first = await _begin()
    claim = first.claim
    assert await claim.start(RUN)
    held = _held(world)
    assert (held["run_id"], held["state"], held["claim"]) == (RUN, "pending", claim.token)
    assert world.raw.ttl[_key()] == 24 * 3600
    assert await claim.confirm()
    held = _held(world)
    assert (held["run_id"], held["state"]) == (RUN, "run")
    assert claim.started and claim.confirmed and claim.run_id == RUN
    # Every value that the request wrote is on its list for a release.
    assert [json.loads(v)["state"] for v in claim.written] == ["claim", "pending", "run"]


# ── A repeat (R1, R2) ────────────────────────────────────────────────────────


async def test_a_repeat_of_a_live_run_replays_it(world) -> None:
    await _first_run(world)
    world.started(THREAD, RUN)
    world.active[THREAD] = True
    out = await _begin()
    assert (out.kind, out.run_id, out.claim) == (send_once.REPLAY, RUN, None)


async def test_a_repeat_of_a_final_run_whose_stream_stays_replays_it(world) -> None:
    await _first_run(world)
    world.started(THREAD, RUN)
    out = await _begin()
    assert (out.kind, out.run_id) == (send_once.REPLAY, RUN)


async def test_a_newer_run_in_the_stream_is_never_replayed(world) -> None:
    """The stream is per thread. A newer run reset it, so the repeat answers
    that its own run is final, with the stored run id (R2)."""
    await _first_run(world)
    world.started(THREAD, RUN_NEW)
    world.active[THREAD] = True
    out = await _begin()
    assert (out.kind, out.run_id) == (send_once.FINISHED, RUN)


async def test_a_stream_that_ran_out_gives_finished(world) -> None:
    await _first_run(world)
    out = await _begin()
    assert (out.kind, out.run_id) == (send_once.FINISHED, RUN)


async def test_the_live_index_names_the_run_when_the_stream_head_is_gone(world) -> None:
    await _first_run(world)
    world.stream[THREAD] = [{"type": "TEXT_MESSAGE_CONTENT", "delta": "late part"}]
    world.active[THREAD] = True
    world.live_index(ORG_A, THREAD, RUN)
    out = await _begin()
    assert (out.kind, out.run_id) == (send_once.REPLAY, RUN)


async def test_the_start_window_waits_and_never_rules_stale(world) -> None:
    """The run moved the key, and its RUN_STARTED is still on its way to the
    stream. The repeat waits, and then replays."""
    await _first_run(world)
    world.active[THREAD] = True

    async def _arrives():
        await asyncio.sleep(0.05)
        world.started(THREAD, RUN)

    late = asyncio.create_task(_arrives())
    out = await _begin()
    await late
    assert (out.kind, out.run_id) == (send_once.REPLAY, RUN)
    assert _held(world)["run_id"] == RUN  # the key stays


async def test_the_same_id_with_other_words_is_refused(world) -> None:
    await _first_run(world)
    out = await _begin(text="other words")
    assert out.kind == send_once.REUSED
    assert _held(world)["run_id"] == RUN


async def test_a_different_member_with_the_same_id_is_no_repeat(world) -> None:
    await _first_run(world)
    world.started(THREAD, RUN)
    out = await _begin(member=BOB)
    assert out.kind == send_once.NEW
    assert _held(world, member=BOB)["member"] == BOB
    assert _held(world)["run_id"] == RUN


async def test_another_org_with_the_same_ids_never_joins_the_run(world) -> None:
    await _first_run(world)
    world.started(THREAD, RUN)
    out = await _begin(org=ORG_B)
    assert out.kind == send_once.NEW
    assert _key(ORG_B).startswith(f"cc:{ORG_B}:") and _key(ORG_A).startswith(f"cc:{ORG_A}:")
    assert _held(world, org=ORG_B)["run_id"] == ""


# ── A claim that has no run (R3, R5) ─────────────────────────────────────────


async def test_a_stale_claim_is_taken_and_the_send_runs_as_new(world) -> None:
    dead = await _begin()  # its request died before its run started
    assert dead.kind == send_once.NEW
    out = await _begin()
    assert out.kind == send_once.NEW
    assert _held(world)["claim"] == out.claim.token != dead.claim.token


async def test_a_claim_with_a_live_run_on_the_thread_is_busy(world) -> None:
    first = await _begin()
    assert first.kind == send_once.NEW
    world.active[THREAD] = True
    out = await _begin()
    assert out.kind == send_once.BUSY
    assert _held(world)["claim"] == first.claim.token


async def test_a_repeat_waits_for_the_first_run_and_replays_it(world) -> None:
    first = await _begin()

    async def _first_starts():
        await asyncio.sleep(0.05)
        await first.claim.start(RUN)
        world.started(THREAD, RUN)
        world.active[THREAD] = True

    starting = asyncio.create_task(_first_starts())
    out = await _begin()
    await starting
    assert (out.kind, out.run_id) == (send_once.REPLAY, RUN)


async def test_two_repeats_at_once_start_one_run(world) -> None:
    """Three copies of one send arrive together. SET NX picks one, and its
    run starts. The others wait for it and replay it (R5)."""
    world.raw.yield_io = True
    started: list[str] = []

    async def _send(i: int) -> Any:
        out = await _begin()
        if out.kind == send_once.NEW and await out.claim.start(f"run-{i}"):
            started.append(f"run-{i}")
            world.started(THREAD, f"run-{i}")
            world.active[THREAD] = True
        return out

    outs = await asyncio.gather(_send(1), _send(2), _send(3))
    assert len(started) == 1, [o.kind for o in outs]
    kinds = sorted(o.kind for o in outs)
    assert kinds == sorted([send_once.NEW, send_once.REPLAY, send_once.REPLAY])
    assert {o.run_id for o in outs if o.kind == send_once.REPLAY} == set(started)


async def test_a_claim_that_a_repeat_made_gets_its_own_wait(world) -> None:
    """Two repeats find one stale claim. The first takes it, and the second
    waits a full turn for the new claim before it judges it."""
    await _begin()  # its request died
    world.raw.yield_io = True
    loop = asyncio.get_running_loop()
    done: list[float] = []

    async def _timed() -> Any:
        out = await _begin()
        done.append(loop.time())
        return out

    outs = await asyncio.gather(_timed(), _timed())
    # No run started in this test, so the second taker takes the first
    # taker's claim too, but only after a full wait for it.
    assert [o.kind for o in outs] == [send_once.NEW, send_once.NEW]
    assert done[1] - done[0] >= send_once.WAIT_SECONDS * 0.9
    assert _held(world)["claim"] in {o.claim.token for o in outs}


async def test_a_slow_first_request_loses_its_start_to_the_taker(world) -> None:
    """The repeat took a claim that looked stale. The first request then
    reaches its start, before ``run_detached``, and the move fails, so it
    starts nothing. Only one run goes on, in each order."""
    slow = await _begin()
    taker = await _begin()  # no live run: the claim reads stale
    assert taker.kind == send_once.NEW
    assert await slow.claim.start(RUN) is False
    assert slow.claim.lost
    assert await taker.claim.start(RUN_NEW) is True
    assert _held(world)["run_id"] == RUN_NEW


async def test_the_taker_loses_when_the_slow_request_starts_first(world) -> None:
    slow = await _begin()
    taker = await _begin()
    assert await taker.claim.start(RUN_NEW) is True  # the taker moved first
    assert await slow.claim.start(RUN) is False
    assert _held(world)["run_id"] == RUN_NEW
    # The loser's end never deletes the winner's key.
    await slow.claim.settle_end()
    assert _held(world)["run_id"] == RUN_NEW


# ── Fail open (R10) ──────────────────────────────────────────────────────────


async def test_redis_down_fails_open(world) -> None:
    world.raw.down = True
    out = await _begin()
    assert out.kind == send_once.OPEN


async def test_a_redis_fault_at_the_start_lets_the_run_go_on(world) -> None:
    first = await _begin()
    world.raw.down = True
    assert await first.claim.start(RUN) is True
    assert first.claim.closed
    lines = await _drain(first.claim.watch(_run_gen(RUN)))
    assert len(lines) == 3


# ── The end of a run (R4, R9) ────────────────────────────────────────────────


async def _started(w: World) -> Any:
    first = await _begin()
    assert await first.claim.start(RUN)
    return first.claim


async def test_a_run_error_deletes_the_key(world) -> None:
    claim = await _started(world)
    lines = await _drain(claim.watch(_run_gen(RUN, error=True)))
    assert lines[-1].startswith("data: ") and claim.errored
    assert _held(world)["run_id"] == RUN
    await claim.settle_end()
    assert _held(world) is None


async def test_a_run_that_raises_deletes_the_key(world) -> None:
    claim = await _started(world)

    async def _gen():
        yield _line({"type": "RUN_STARTED", "runId": RUN})
        raise RuntimeError("the run failed")

    with pytest.raises(RuntimeError):
        await _drain(claim.watch(_gen()))
    await claim.settle_end()
    assert _held(world) is None


async def test_a_stop_deletes_the_key(world) -> None:
    claim = await _started(world)
    await _drain(claim.watch(_run_gen(RUN)))
    world.stopped = True
    await claim.settle_end()
    assert _held(world) is None


async def test_a_clean_end_keeps_the_key(world) -> None:
    claim = await _started(world)
    await _drain(claim.watch(_run_gen(RUN)))
    await claim.settle_end()
    assert _held(world)["run_id"] == RUN and world.raw.ttl[_key()] == 24 * 3600


async def test_a_run_that_never_started_deletes_its_claim(world) -> None:
    first = await _begin()
    await first.claim.settle_end()
    assert _held(world) is None


async def test_a_release_never_deletes_a_newer_claim(world) -> None:
    old = await _begin()
    new = await _begin()  # took the stale claim
    await old.claim.release()
    assert _held(world)["claim"] == new.claim.token


async def test_a_tier_restart_inside_one_run_moves_the_key_once(world) -> None:
    """R9: the first RUN_STARTED confirms the run. A second RUN_STARTED of the
    same run passes, unchanged, with no Redis call."""
    claim = await _started(world)
    before = len(world.raw.calls)
    lines = await _drain(claim.watch(_run_gen(RUN, restart=True)))
    assert lines == [line async for line in _run_gen(RUN, restart=True)]
    moves = [c for c in world.raw.calls[before:]
             if c[0] == "eval" and c[1] == tr._REPLACE_IF_LUA]
    assert len(moves) == 1 and len(world.raw.calls) == before + 1
    assert _held(world)["state"] == "run"
    assert await claim.start(RUN) is True and len(world.raw.calls) == before + 1


async def test_a_lost_confirm_stops_the_run_before_its_work(world) -> None:
    """The run's request read as dead for longer than the stale limit, and a
    repeat took the key. Its RUN_STARTED then fails to confirm, and the run
    stops before the load and the model."""
    slow = await _started(world)
    _age_value(world, send_once.PENDING_STALE_SECONDS + 120)  # wide: the box clock steps
    taker = await _begin()
    assert taker.kind == send_once.NEW
    closed: list[bool] = []

    async def _gen():
        try:
            yield _line({"type": "RUN_STARTED", "runId": RUN})
            yield _line({"type": "TEXT_MESSAGE_CONTENT", "delta": "work"})
        finally:
            closed.append(True)

    assert await _drain(slow.watch(_gen())) == [] and closed == [True]
    assert slow.lost
    await slow.settle_end()
    assert _held(world)["claim"] == taker.claim.token


async def test_a_cancel_during_the_move_still_releases_the_pending_run(world) -> None:
    """The client left while ``start`` waited on its compare and set. The
    move landed, and the value of this request was never updated. The
    release still finds the pending run, because it was listed first."""
    first = await _begin()
    claim = first.claim
    gate = asyncio.Event()
    real_eval = world.raw.eval

    async def _eval_then_hang(script, numkeys, *args):
        out = await real_eval(script, numkeys, *args)
        if script == tr._REPLACE_IF_LUA:
            await gate.wait()  # the reply never comes back
        return out

    world.raw.eval = _eval_then_hang
    task = asyncio.create_task(claim.start(RUN))
    await asyncio.sleep(0.05)
    assert _held(world)["state"] == "pending"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    world.raw.eval = real_eval
    assert json.loads(claim.value)["state"] == "claim"  # never updated
    await claim.release()
    assert _held(world) is None


async def test_a_pending_run_that_nothing_names_goes_stale(world) -> None:
    """R3 for a run id: the process died between the move and the run's
    start. After the stale limit, a repeat takes the key and runs as new."""
    await _started(world)  # pending, and no stream and no live run
    _age_value(world, send_once.PENDING_STALE_SECONDS + 120)  # wide: the box clock steps
    out = await _begin()
    assert out.kind == send_once.NEW
    assert _held(world)["state"] == "claim"


async def test_a_young_pending_run_that_nothing_names_is_busy(world) -> None:
    await _started(world)
    out = await _begin()
    assert out.kind == send_once.BUSY
    assert _held(world)["state"] == "pending"


async def test_a_pending_run_that_the_live_index_names_is_replayed(world) -> None:
    await _started(world)
    world.active[THREAD] = True
    world.live_index(ORG_A, THREAD, RUN)
    out = await _begin()
    assert (out.kind, out.run_id) == (send_once.REPLAY, RUN)


async def test_a_stuck_redis_step_ends_in_busy_and_never_in_a_run(world, monkeypatch) -> None:
    """Each Redis step has a hard timeout, and begin as a whole too."""
    await _first_run(world)
    monkeypatch.setattr(send_once, "STEP_TIMEOUT_SECONDS", 0.05)

    async def _never(thread_id, count=5):
        await asyncio.Event().wait()

    monkeypatch.setattr(stream_relay, "stream_head", _never)
    out = await asyncio.wait_for(_begin(), 2)
    assert out.kind == send_once.BUSY
    monkeypatch.setattr(send_once, "STEP_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(send_once, "BEGIN_TIMEOUT_SECONDS", 0.1)
    out = await asyncio.wait_for(_begin(), 2)
    assert out.kind == send_once.BUSY


async def test_a_redis_that_stalls_on_the_first_claim_fails_open(world, monkeypatch) -> None:
    """F-C: a FIRST send whose claim gets no answer takes the old path, as a
    refusing Redis does (R10). It is never a 409."""
    monkeypatch.setattr(send_once, "STEP_TIMEOUT_SECONDS", 0.05)
    real_set = world.raw.set

    async def _stall(*a, **k):
        await asyncio.Event().wait()
        return await real_set(*a, **k)

    world.raw.set = _stall
    out = await asyncio.wait_for(_begin(), 2)
    assert out.kind == send_once.OPEN


async def test_a_start_into_an_empty_key_starts_nothing(world) -> None:
    """F-E: the key is gone at the start. It ran out, or a taker ran and a
    Stop released it. The move never writes into an empty key."""
    first = await _begin()
    world.raw.kv.pop(_key())
    assert await first.claim.start(RUN) is False
    assert first.claim.lost and _held(world) is None


# ── The fences of the reads and the scripts (F-F), with no Redis ────────────


def test_replay_events_sends_no_block() -> None:
    """``XREAD BLOCK 0`` waits forever. ``replay_events`` must send no
    ``block`` argument at all, so a read past the last entry returns."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(stream_relay.replay_events)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "xread"]
    assert calls, "replay_events reads with XREAD"
    for call in calls:
        assert "block" not in {kw.arg for kw in call.keywords}


def test_send_once_reads_the_stream_head_only() -> None:
    import inspect

    src = inspect.getsource(send_once)
    assert "xread" not in src and "replay_events(" not in src
    head = inspect.getsource(send_once._stream_run_id)
    assert "stream_relay.stream_head(" in head
    body = inspect.getsource(stream_relay.stream_head)
    assert ".xrange(" in body and "xread" not in body and "block" not in body


def test_the_scripts_compare_before_they_write() -> None:
    """``replace_if`` and ``delete_if`` act only when the key holds the
    value. A script that always writes passes no other hermetic test."""
    from acb_common import tenant_redis

    for script, act in ((tenant_redis._REPLACE_IF_LUA, "redis.call('SET'"),
                        (tenant_redis._DELETE_IF_LUA, "redis.call('DEL'")):
        compare = "if redis.call('GET', KEYS[1]) == ARGV[1] then "
        assert script.startswith(compare), script
        assert script.index(compare) < script.index(act)
        assert script.count("redis.call(") == 2 and script.rstrip().endswith("return 0")


async def test_an_old_run_gets_finished_at_once(world) -> None:
    """A run written long ago that the stream no longer holds answers at
    once: no wait, and no replay of what the stream holds now."""
    await _first_run(world)
    held = _held(world)
    held["at"] = 1.0
    world.raw.kv[_key()] = json.dumps(held)
    world.started(THREAD, RUN_NEW)
    reads: list[str] = []
    real = send_once._stream_run_id

    async def _counted(thread_id: str) -> str:
        reads.append(thread_id)
        return await real(thread_id)

    send_once._stream_run_id = _counted
    try:
        out = await _begin()
    finally:
        send_once._stream_run_id = real
    assert (out.kind, out.run_id) == (send_once.FINISHED, RUN)
    assert reads == [THREAD], "one look at the stream, and no wait"


async def test_release_soon_deletes_the_claim_in_its_own_task(world) -> None:
    first = await _begin()
    first.claim.release_soon()
    assert send_once._RELEASES  # held until it ends
    await asyncio.gather(*list(send_once._RELEASES))
    assert _held(world) is None and not send_once._RELEASES


# ═══════════════════════════════════════════════════════════════════════════
# The route: POST /agent/run/stream, through FastAPI, with its edges faked.
# ═══════════════════════════════════════════════════════════════════════════


class _RelayRedis:
    """The relay's raw client for the steer store and the stopped mark."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}

    async def rpush(self, key: str, val: str) -> int:
        self.lists.setdefault(key, []).append(val)
        return len(self.lists[key])

    async def ltrim(self, *_a: Any) -> None:
        return None

    async def expire(self, *_a: Any, **_k: Any) -> bool:
        return True

    async def rename(self, src: str, dst: str) -> None:
        if src not in self.lists:
            raise RuntimeError("no such key")
        self.lists[dst] = self.lists.pop(src)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = list(self.lists.get(key, []))
        return items if end == -1 else items[start:end + 1]

    async def lrem(self, key: str, _count: int, val: str) -> int:
        items = self.lists.get(key, [])
        if val in items:
            items.remove(val)
            return 1
        return 0

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.lists.pop(k, None)
            self.kv.pop(k, None)

    async def set(self, key: str, val: Any, *_a: Any, xx: bool = False, **_k: Any) -> bool:
        if xx and key not in self.kv:
            return False
        self.kv[key] = str(val)
        return True

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)


class _Room:
    def __init__(self, email: str) -> None:
        self.email = email
        self.can_send = True
        self.can_read = True
        self.is_shared = False
        self.members = [email]
        self.visibility = "private"
        self.resolve_failed = False


class RouteWorld(World):
    def __init__(self, base: World) -> None:
        super().__init__()
        self.raw, self.stream, self.active = base.raw, base.stream, base.active
        self.relay = _RelayRedis()
        self.runs: list[dict[str, Any]] = []
        self.mints = 0
        self.admits = 0
        self.room_events = 0
        self.once_calls = 0
        self.run_error = False
        self.detached_fault: BaseException | None = None
        self.live_tail: list[dict[str, Any]] = []


@pytest.fixture
def route(monkeypatch, world) -> Any:  # noqa: C901 — one fixture holds every edge
    import acb_memory
    import orchestrator.executor as executor
    from acb_common import get_settings
    from gateway import chat_fold
    from gateway.routes import agent
    from orchestrator import run_cap, run_liveness, steer

    w = RouteWorld(world)
    world_stopped = world  # the fixture `world` reads its own `stopped`
    steer._PENDING_GUIDANCE.clear()
    steer._SEEN_STEER_IDS.clear()
    steer._CLOSED.clear()
    monkeypatch.setattr(get_settings(), "chat_send_once_orgs", ORG_A)

    async def _once_client():
        w.once_calls += 1
        return TenantRedis(w.raw)

    async def _get_client():
        return w.relay

    async def _noop(*_a, **_k):
        return None

    async def _source(_tid):
        return "chat"

    async def _alive(_tid):
        return "alive"

    async def _false(*_a, **_k):
        return False

    async def _dispatch(_tid, _cmd):
        return True

    async def _was_stopped(_tid):
        return w.stopped or world_stopped.stopped

    class _Admitted:
        admitted = True
        limit = 5
        running: tuple = ()

    async def _admit(**_k):
        w.admits += 1
        return _Admitted()

    async def _memory(**_k):
        return ""

    async def _mint(*_a, **_k):
        w.mints += 1

    async def _room_event(*_a, **_k):
        w.room_events += 1

    def _run_agent_stream(agent_name, payload, **kw):
        run_id = kw["run_id"]

        async def _gen():
            yield _line({"type": "RUN_STARTED", "runId": run_id, "threadId": kw["thread_id"]})
            yield _line({"type": "TEXT_MESSAGE_CONTENT", "delta": "the answer"})
            if w.run_error:
                yield _line({"type": "RUN_ERROR", "message": "boom"})
            else:
                yield _line({"type": "RUN_FINISHED", "runId": run_id})

        return _gen()

    async def _run_detached(thread_id, gen, **kw):
        if w.detached_fault is not None:
            raise w.detached_fault
        # The drain: the run's lines land in the stream, then the run ends.
        events = [json.loads(line[6:]) async for line in gen]
        w.runs.append({"thread": thread_id, "events": events, "kw": kw})
        w.stream[thread_id] = list(events)
        for evt in events:
            yield evt
        await kw["on_complete"]()

    async def _no_fold(*_a, **_k):
        return None

    async def _stream_exists(thread_id):
        return thread_id in w.stream

    monkeypatch.setattr(send_once, "_client", _once_client)
    monkeypatch.setattr(steer, "was_stopped", _was_stopped)
    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    monkeypatch.setattr(stream_relay, "get_run_source", _source)
    monkeypatch.setattr(stream_relay, "get_run_floor", _noop)
    monkeypatch.setattr(stream_relay, "dispatch_control", _dispatch)
    monkeypatch.setattr(stream_relay, "run_detached", _run_detached)
    monkeypatch.setattr(stream_relay, "stream_exists", _stream_exists)
    monkeypatch.setattr(run_liveness, "recovery_in_progress", _false)
    monkeypatch.setattr(run_liveness, "run_liveness", _alive)
    monkeypatch.setattr(run_cap, "admit_member_run", _admit)
    monkeypatch.setattr(run_cap, "release_member_slot", _noop)
    monkeypatch.setattr(agent, "_resolve_room", lambda tid, email, org: _Room(email))
    monkeypatch.setattr(agent, "assert_can_run_agent_in_session", _noop)
    monkeypatch.setattr(agent, "_prepare_if_new_thread", _noop)
    monkeypatch.setattr(agent, "_refuse_if_another_run_is_active", _noop)
    monkeypatch.setattr(agent, "_mint_run_row_bounded", _mint)
    monkeypatch.setattr(agent, "_save_steered_prompt", lambda *_a, **_k: True)
    monkeypatch.setattr(agent, "publish_room_event", _room_event)
    monkeypatch.setattr(agent, "_address_agent", lambda req, room, org=None: "orchestrator")
    monkeypatch.setattr(executor, "run_agent_stream", _run_agent_stream)
    monkeypatch.setattr(chat_fold, "persist_final_assistant_message", _no_fold)
    monkeypatch.setattr(acb_memory, "get_session_memory", _memory)
    monkeypatch.setattr(acb_memory, "add_episode", _noop)
    yield w
    steer._PENDING_GUIDANCE.clear()
    steer._SEEN_STEER_IDS.clear()
    steer._CLOSED.clear()


def _client(email: str = ALICE, org: str = ORG_A):
    from acb_auth import UserContext, get_current_user
    from acb_auth.roles import UserRole
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.agent import reconnect_agent_stream, run_agent_stream_endpoint

    user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)
    app = FastAPI()
    app.post("/agent/run/stream")(run_agent_stream_endpoint)
    app.get("/agent/run/{thread_id}/reconnect")(reconnect_agent_stream)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _send(words: str = "hello", *, sid: Any = SID, thread: str = THREAD,
          **payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"mode": "chat", "message": words, "messages": [], **payload}
    if sid is not None:
        body["user_message_id"] = sid
    return {"agent": "orchestrator", "thread_id": thread,
            "assistant_message_id": f"a-{thread}", "payload": body}


def _post(body: dict[str, Any], *, email: str = ALICE, org: str = ORG_A):
    with _client(email, org).stream("POST", "/agent/run/stream", json=body) as r:
        text = r.read().decode("utf-8")
        return r.status_code, dict(r.headers), text


def _sse(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_route_a_repeat_starts_no_run_and_replays_the_first(route) -> None:
    code, headers, _ = _post(_send())
    assert code == 200 and len(route.runs) == 1
    first_run = headers["x-run-id"]
    assert _held(route)["run_id"] == first_run
    mints, admits, events = route.mints, route.admits, route.room_events

    code, headers, text = _post(_send())
    assert code == 200
    assert len(route.runs) == 1, "a repeat starts no second run"
    assert headers["x-run-id"] == first_run, "the BFF binds its reader to the stored run"
    assert (route.mints, route.admits, route.room_events) == (mints, admits, events)
    evts = _sse(text)
    assert evts[0]["type"] == "RUN_STARTED" and evts[0]["runId"] == first_run
    assert evts[-1]["type"] == "RUN_FINISHED" and evts[-1]["runId"] == first_run
    assert "the answer" in text


def test_route_the_flag_off_makes_no_redis_call(route, monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_send_once_orgs", "")
    for _ in range(2):
        code, _h, _t = _post(_send())
        assert code == 200
    assert route.once_calls == 0 and route.raw.calls == []
    assert len(route.runs) == 2, "the old behaviour: each send runs"
    # The run-end hook of before, not the send-once wrapper.
    assert route.runs[0]["kw"]["on_complete"].__name__ == "_persist_on_complete"


@pytest.mark.parametrize("sid", [None, "", "bad:id:12345", "x" * 65, "short"])
def test_route_no_id_or_a_bad_id_takes_the_old_path(route, sid) -> None:
    for _ in range(2):
        assert _post(_send(sid=sid))[0] == 200
    assert route.once_calls == 0 and len(route.runs) == 2


def test_route_other_words_with_the_same_id_get_409(route) -> None:
    _post(_send("hello"))
    code, _h, text = _post(_send("goodbye"))
    assert code == 409 and json.loads(text)["detail"]["error"] == "send_id_reused"
    assert len(route.runs) == 1


def test_route_a_newer_run_in_the_stream_is_never_replayed(route) -> None:
    _, headers, _ = _post(_send())
    first_run = headers["x-run-id"]
    route.started(THREAD, RUN_NEW)  # the member's next send reset the stream
    route.active[THREAD] = True
    code, headers, text = _post(_send())
    assert code == 200 and headers["x-run-id"] == first_run
    assert _sse(text) == [{"type": "RUN_FINISHED", "runId": first_run, "threadId": THREAD}]
    assert RUN_NEW not in text
    assert len(route.runs) == 1


def test_route_another_member_with_the_same_id_runs(route) -> None:
    _post(_send())
    code, _h, _t = _post(_send(), email=BOB)
    assert code == 200 and len(route.runs) == 2


def test_route_another_org_with_the_same_ids_runs(route, monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_send_once_orgs", "*")
    _post(_send())
    code, headers, _t = _post(_send(), org=ORG_B)
    assert code == 200 and len(route.runs) == 2
    assert headers["x-run-id"] == _held(route, org=ORG_B)["run_id"]


def test_route_a_followup_never_keys(route) -> None:
    code, _h, _t = _post(_send(steer_followup=True))
    assert code == 202 and route.once_calls == 0


def test_route_an_edit_and_a_continuation_never_key(monkeypatch) -> None:
    import inspect

    from acb_common import get_settings
    from gateway.routes import agent

    monkeypatch.setattr(get_settings(), "chat_send_once_orgs", "*")
    base = {"org": ORG_A, "thread_id": THREAD, "member": ALICE, "room": _Room(ALICE),
            "raw_id": SID, "supersedes": "", "continuation": False, "followup": False}
    assert agent._send_once_id(**base) == SID
    for change in ({"supersedes": "old-id"}, {"continuation": True}, {"followup": True},
                   {"member": ""}, {"thread_id": ""}, {"room": None}, {"org": None}):
        assert agent._send_once_id(**{**base, **change}) == "", change
    src = inspect.getsource(agent.run_agent_stream_endpoint)
    assert "continuation=_resume_launch is not None, followup=_followup" in src
    assert "raw_id=_new_user_id, supersedes=_supersedes" in src


def test_route_the_check_sits_after_the_room_and_before_the_decision() -> None:
    import inspect

    from gateway.routes import agent

    src = inspect.getsource(agent.run_agent_stream_endpoint)
    at = src.index("await send_once.begin(")
    assert src.index("room.can_send") < at
    assert src.index("check_supersede, req.thread_id") < at
    assert at < src.index("_decision = await _route_incoming_turn(")
    assert at < src.index("_admission = await admit_member_run(")
    assert at < src.index("await publish_room_event(")


def test_route_redis_down_fails_open(route) -> None:
    route.raw.down = True
    for _ in range(2):
        assert _post(_send())[0] == 200
    assert len(route.runs) == 2


def test_route_a_run_error_deletes_the_key(route) -> None:
    route.run_error = True
    _post(_send())
    assert _held(route) is None
    _post(_send())
    assert len(route.runs) == 2, "a deliberate resend runs again"


def test_route_a_stop_deletes_the_key(route) -> None:
    route.stopped = True  # the Stop route marked the thread
    _post(_send())
    assert _held(route) is None


def test_route_a_stop_mark_cleared_during_the_fold_still_deletes(route, monkeypatch) -> None:
    """A new send of the thread clears the Stop mark while the stopped run
    folds. The end reads the mark before the fold, so the key still goes."""
    from gateway import chat_fold

    async def _fold_then_clear(*_a, **_k):
        route.stopped = False
        return None

    monkeypatch.setattr(chat_fold, "persist_final_assistant_message", _fold_then_clear)
    route.stopped = True
    _post(_send())
    assert _held(route) is None


class _Gone(BaseException):
    """The cancel of a request whose client left while the run started."""


def test_route_a_cancel_before_the_run_began_releases_the_key(route, monkeypatch) -> None:
    released: list[str] = []
    monkeypatch.setattr(send_once.SendClaim, "release_soon",
                        lambda self: released.append(self.value))
    route.detached_fault = _Gone()
    try:
        _post(_send())
    except BaseException as exc:
        assert isinstance(exc, _Gone) or "_Gone" in repr(exc)
    else:
        raise AssertionError("the cancel did not reach the test")
    assert route.runs == []
    assert len(released) == 1 and json.loads(released[0])["run_id"], (
        "the key held the moved run id, and the end of the request lets it go")


def test_route_a_cancel_after_the_drain_began_keeps_the_key(route, monkeypatch) -> None:
    """A client that leaves a live run leaves its drain: the run end settles."""
    released: list[str] = []
    monkeypatch.setattr(send_once.SendClaim, "release_soon",
                        lambda self: released.append(self.value))
    _post(_send())
    assert released == [] and _held(route)["run_id"]


def test_route_a_clean_run_keeps_the_key(route) -> None:
    _, headers, _ = _post(_send())
    assert _held(route)["run_id"] == headers["x-run-id"]


def test_route_a_steer_releases_the_claim(route) -> None:
    route.active[THREAD] = True  # a live run of the same member
    code, _h, text = _post(_send())
    assert code == 202 and json.loads(text)["steered"] is True
    assert _held(route) is None and route.runs == []


def test_route_a_lost_start_starts_nothing(route, monkeypatch) -> None:
    async def _lost(self, run_id):
        return False

    monkeypatch.setattr(send_once.SendClaim, "start", _lost)
    code, _h, text = _post(_send())
    assert code == 200 and route.runs == []
    assert _sse(text)[-1]["code"] == "send_in_progress"


def test_route_the_direct_path_deletes_the_key(route) -> None:
    route.detached_fault = RuntimeError("redis went away")
    code, _h, text = _post(_send())
    assert code == 200 and "the answer" in text
    assert _held(route) is None


def test_route_a_refused_start_deletes_the_key(route) -> None:
    route.detached_fault = stream_relay.SupersedeRefused(THREAD, BOB, ALICE)
    code, _h, text = _post(_send())
    assert code == 200 and _sse(text)[-1]["code"] == "run_in_progress"
    assert _held(route) is None


def test_route_a_busy_claim_gets_409(route) -> None:
    asyncio.run(_begin())  # another request holds the claim, and has no run
    route.active[THREAD] = True
    code, _h, text = _post(_send())
    assert code == 409 and json.loads(text)["detail"]["error"] == "send_in_progress"
    assert route.runs == []


# ── Each exit before the run start releases the claim (F-A) ─────────────────


def test_route_a_cap_refusal_releases_the_claim(route, monkeypatch) -> None:
    """A 429 of the run cap starts no run, so the claim goes at once."""
    from orchestrator import run_cap

    class _Refused:
        admitted = False
        limit = 5
        running: tuple = ("t-1",)

    async def _refuse(**_k):
        return _Refused()

    monkeypatch.setattr(run_cap, "admit_member_run", _refuse)
    code, _h, text = _post(_send())
    assert code == 429 and json.loads(text)["error"] == "too_many_runs"
    assert _held(route) is None and route.runs == []


def test_route_a_raise_of_the_turn_decision_releases_the_claim(route, monkeypatch) -> None:
    """``_apply_turn_decision`` raises (the steer floor 409): no run, no key."""
    from fastapi import HTTPException
    from gateway.routes import agent

    async def _floor(*_a, **_k):
        raise HTTPException(status_code=409, detail={"error": "steer_outside_run_floor"})

    monkeypatch.setattr(agent, "_apply_turn_decision", _floor)
    code, _h, text = _post(_send())
    assert code == 409 and json.loads(text)["detail"]["error"] == "steer_outside_run_floor"
    assert _held(route) is None and route.runs == []


def test_route_a_refusal_of_another_members_run_releases_the_claim(route, monkeypatch) -> None:
    """``_refuse_if_another_run_is_active`` gives 409: no run, no key."""
    from fastapi import HTTPException
    from gateway.routes import agent

    async def _refuse(thread_id, actor):
        raise HTTPException(status_code=409, detail={"error": "run_in_progress"})

    monkeypatch.setattr(agent, "_refuse_if_another_run_is_active", _refuse)
    code, _h, text = _post(_send())
    assert code == 409 and json.loads(text)["detail"]["error"] == "run_in_progress"
    assert _held(route) is None and route.runs == []


class _LiveTask:
    """A drain task that ``start_detached`` registered, and that has not run
    its first step yet."""

    def done(self) -> bool:
        return False


def test_route_a_client_that_leaves_after_the_start_keeps_the_live_key(route, monkeypatch) -> None:
    """The client left after ``start_detached`` and before the drain pulled a
    line. The run is live, so its pending key must stay, and a repeat must
    never start a second run."""
    released: list[str] = []
    real_release_soon = send_once.SendClaim.release_soon

    def _spy(self):
        released.append(self.value)
        real_release_soon(self)

    async def _start_then_leave(thread_id, gen, **kw):
        stream_relay._DETACHED_TASKS[thread_id] = _LiveTask()
        raise _Gone()
        yield  # an async generator, as the real one is

    async def _no_tail(*_a, **_k):
        return
        yield

    monkeypatch.setattr(send_once.SendClaim, "release_soon", _spy)
    monkeypatch.setattr(stream_relay, "run_detached", _start_then_leave)
    monkeypatch.setattr(stream_relay, "subscribe_events", _no_tail)
    try:
        try:
            _post(_send())
        except BaseException as exc:
            assert isinstance(exc, _Gone) or "_Gone" in repr(exc)
        assert released == []
        held = _held(route)
        assert held is not None and held["state"] == "pending" and held["run_id"]
        code, _h, text = _post(_send())
        assert code == 409 and json.loads(text)["detail"]["error"] == "send_in_progress"
        assert route.runs == [], "a repeat never starts a second run"
        route.active[THREAD] = True
        route.live_index(ORG_A, THREAD, held["run_id"])
        code, headers, _t = _post(_send())
        assert code == 200 and headers["x-run-id"] == held["run_id"]
        assert route.runs == []
    finally:
        stream_relay._DETACHED_TASKS.pop(THREAD, None)


def test_route_a_start_after_the_claim_ran_out_starts_nothing(route, monkeypatch) -> None:
    """F-E: the claim is gone at the start (a taker ran and released it, or
    it ran out). The request cannot tell the two apart, so it starts nothing,
    and a Stop of the taker's run is never undone."""
    real_start = send_once.SendClaim.start

    async def _gone_then_start(self, run_id):
        route.raw.kv.pop(_key(), None)
        return await real_start(self, run_id)

    monkeypatch.setattr(send_once.SendClaim, "start", _gone_then_start)
    code, _h, text = _post(_send())
    assert code == 200 and _sse(text)[-1]["code"] == "send_in_progress"
    assert route.runs == [] and _held(route) is None


# ── The ONE replay: /reconnect keeps its bytes ───────────────────────────────


def _reconnect(thread: str = THREAD, since: str | None = None) -> str:
    url = f"/agent/run/{thread}/reconnect" + (f"?since={since}" if since else "")
    with _client().stream("GET", url) as r:
        assert r.status_code == 200
        assert "x-run-id" not in r.headers
        return r.read().decode("utf-8")


@pytest.fixture
def replay(monkeypatch, route) -> Any:
    from gateway.routes import agent

    async def _replay(thread_id, since_id="0-0", count=500, **_k):
        events = [dict(e, _stream_id=f"{i + 1}-0")
                  for i, e in enumerate(route.stream.get(thread_id, []))]
        if since_id not in ("0-0", "0"):
            seen = [e["_stream_id"] for e in events]
            events = events[seen.index(since_id) + 1:] if since_id in seen else []
        return events[:count]

    async def _subscribe(thread_id, since_id="$", block_ms=0):
        for evt in route.live_tail:
            yield evt

    monkeypatch.setattr(stream_relay, "replay_events", _replay)
    monkeypatch.setattr(stream_relay, "subscribe_events", _subscribe)
    monkeypatch.setattr(agent, "_thread_owner_ok", lambda *_a, **_k: True)
    return route


def _frame(evt: dict[str, Any]) -> str:
    return f"data: {json.dumps(evt)}\n\n"


def test_reconnect_a_final_run_keeps_its_bytes(replay) -> None:
    replay.started(THREAD, RUN)
    want = "".join(_frame(dict(e, _stream_id=f"{i + 1}-0"))
                   for i, e in enumerate(replay.stream[THREAD]))
    want += _frame({"type": "RUN_FINISHED", "runId": THREAD, "threadId": THREAD})
    assert _reconnect() == want
    assert _reconnect(since="local-17-1") == want  # a local cursor reads all
    assert _reconnect(since="1-0") == "".join(
        [_frame(dict(replay.stream[THREAD][1], _stream_id="2-0")),
         _frame({"type": "RUN_FINISHED", "runId": THREAD, "threadId": THREAD})])


def test_reconnect_a_live_run_keeps_its_bytes(replay) -> None:
    replay.started(THREAD, RUN)
    replay.active[THREAD] = True
    tail = [{"type": "TEXT_MESSAGE_CONTENT", "delta": "more", "_stream_id": "3-0"},
            {"type": "RUN_FINISHED", "runId": RUN, "_stream_id": "4-0"}]
    replay.live_tail = tail
    want = "".join(_frame(dict(e, _stream_id=f"{i + 1}-0"))
                   for i, e in enumerate(replay.stream[THREAD]))
    want += "".join(_frame(e) for e in tail)
    assert _reconnect() == want


def test_reconnect_with_no_stream_sends_the_finish_only(replay) -> None:
    assert _reconnect() == _frame({"type": "RUN_FINISHED", "runId": THREAD, "threadId": THREAD})


def test_reconnect_reads_the_one_replay() -> None:
    import inspect

    from gateway.routes import agent

    src = inspect.getsource(agent.reconnect_agent_stream)
    assert "_replay_run_events(thread_id, since)" in src
    assert "subscribe_events" not in src and "recover_dead_run" not in src
    assert "replay_events(" not in src.replace("_replay_run_events(", "")
    whole = inspect.getsource(agent)
    assert whole.count("subscribe_events(thread_id, since_id=_cursor)") == 1


def test_the_replay_of_a_send_stops_at_a_newer_run(replay) -> None:
    from gateway.routes import agent

    replay.stream[THREAD] = [
        {"type": "RUN_STARTED", "runId": RUN},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "mine"},
        {"type": "RUN_STARTED", "runId": RUN_NEW},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "newer"},
    ]

    async def _all(**kw):
        return [line async for line in agent._replay_run_events(THREAD, "0-0", **kw)]

    lines = asyncio.run(_all(expect_run_id=RUN))
    assert "newer" not in "".join(lines) and "mine" in "".join(lines)
    assert json.loads(lines[-1][6:]) == {"type": "RUN_FINISHED", "runId": RUN, "threadId": THREAD}
    # The live tail is checked too.
    replay.stream[THREAD] = replay.stream[THREAD][:2]
    replay.active[THREAD] = True
    replay.live_tail = [{"type": "RUN_STARTED", "runId": RUN_NEW},
                        {"type": "TEXT_MESSAGE_CONTENT", "delta": "newer"}]
    lines = asyncio.run(_all(expect_run_id=RUN))
    assert "newer" not in "".join(lines)
    # With no expected run, the replay sends the stream as it is.
    assert "newer" in "".join(asyncio.run(_all()))
