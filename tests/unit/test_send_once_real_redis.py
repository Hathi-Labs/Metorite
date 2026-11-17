"""HRD-17a on a PRIVATE real Redis: the reads of a repeat never block.

The hermetic suite (``test_send_once.py``) fakes the stream reads. That hid a
hang: ``stream_relay.replay_events`` sent ``XREAD BLOCK 0``, which waits
forever when no entry follows the cursor. A repeat whose stream was gone (the
key lives 24 hours, the stream 1 hour) then never got an answer. This suite
runs the real relay reads, the real wrapper scripts and the real
``send_once.begin`` against a real server.

Mutation that this suite catches: put ``block=0`` back in ``replay_events``
and read the head through it. Then ``test_a_run_whose_stream_is_gone_*`` and
``test_replay_events_*`` time out.

``SEND_ONCE_REDIS_URL`` names a PRIVATE Redis, because the suite runs
FLUSHDB. Never point it at a shared server. With it unset, the suite skips,
and a skip is not a pass. Run, with no network for the server::

    mkdir -p /tmp/so-sock && chmod 777 /tmp/so-sock
    docker run -d --rm --network none --name so-redis -v /tmp/so-sock:/sock \\
        redis:7-alpine redis-server --port 0 --unixsocket /sock/redis.sock \\
        --unixsocketperm 777
    SEND_ONCE_REDIS_URL=unix:///tmp/so-sock/redis.sock \\
        uv run pytest tests/unit/test_send_once_real_redis.py -v -rs
    docker rm -f so-redis
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import pytest

send_once = pytest.importorskip("orchestrator.send_once")
stream_relay = pytest.importorskip("orchestrator.stream_relay")

from acb_common.tenant_redis import TenantRedis, organization_scope  # noqa: E402

URL_ENV = "SEND_ONCE_REDIS_URL"
URL = os.environ.get(URL_ENV, "").strip()

pytestmark = pytest.mark.skipif(
    not URL, reason=f"{URL_ENV} unset: this suite needs a PRIVATE real Redis. "
    "A skip is not a pass.")

ORG = "aaaaaaaa-1111-4111-8111-111111111111"
THREAD = "thread-real-1"
ALICE = "alice@once.test"
SID = "nanoid_REALredis123456"
RUN = "run-real-1111"

#: The longest that one answer may take. The hang it guards was forever.
ANSWER_WITHIN_S = 5.0


@pytest.fixture
async def real(monkeypatch) -> Any:
    import redis.asyncio as aioredis

    client = aioredis.from_url(URL, decode_responses=True)
    await client.flushdb()

    async def _get_client():
        return client

    async def _tenant():
        return TenantRedis(client)

    monkeypatch.setattr(stream_relay, "_client", client)
    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    monkeypatch.setattr(send_once, "_client", _tenant)
    monkeypatch.setattr(send_once, "WAIT_SECONDS", 1.0)
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


async def _store(client: Any, state: str, run_id: str, age: float = 0.0) -> None:
    value = json.loads(send_once._encode(run_id, ALICE, send_once.text_digest("hello"),
                                         "c0ffee", state))
    value["at"] = round(time.time() - age, 3)
    with organization_scope(ORG):
        k = send_once.send_key(ORG, THREAD, ALICE, SID)
    await client.set(k.value, json.dumps(value), ex=send_once.RUN_TTL_SECONDS)


async def _begin() -> Any:
    return await asyncio.wait_for(
        send_once.begin(ORG, THREAD, ALICE, SID, "hello"), ANSWER_WITHIN_S)


async def _push(client: Any, thread: str, events: list[dict[str, Any]]) -> None:
    for evt in events:
        await client.xadd(f"cc:stream:{thread}", {"event": json.dumps(evt)})


async def test_a_run_whose_stream_is_gone_answers_finished_at_once(real) -> None:
    """R2: the key outlives the stream (24 h against 1 h). The answer comes
    at once, with no stream and no live run, and it never blocks."""
    await _store(real, "run", RUN, age=7200)
    t0 = time.monotonic()
    out = await _begin()
    assert (out.kind, out.run_id) == (send_once.FINISHED, RUN)
    assert time.monotonic() - t0 < 1.0


async def test_a_run_whose_stream_holds_it_is_replayed(real) -> None:
    await _store(real, "run", RUN, age=60)
    await _push(real, THREAD, [{"type": "RUN_STARTED", "runId": RUN},
                               {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}])
    out = await _begin()
    assert (out.kind, out.run_id) == (send_once.REPLAY, RUN)


async def test_a_newer_run_in_the_stream_answers_finished(real) -> None:
    await _store(real, "run", RUN, age=60)
    await _push(real, THREAD, [{"type": "RUN_STARTED", "runId": "run-newer"}])
    out = await _begin()
    assert (out.kind, out.run_id) == (send_once.FINISHED, RUN)


async def test_a_pending_run_that_never_wrote_its_stream_goes_stale(real) -> None:
    """R3 for a run id: no RUN_STARTED, no live run, older than the limit.
    The repeat takes the key with the real script and runs as new."""
    await _store(real, "pending", RUN, age=send_once.PENDING_STALE_SECONDS + 5)
    out = await _begin()
    assert out.kind == send_once.NEW
    with organization_scope(ORG):
        k = send_once.send_key(ORG, THREAD, ALICE, SID)
    assert json.loads(await real.get(k.value))["state"] == "claim"


async def test_the_real_scripts_move_and_release_only_their_own_value(real) -> None:
    out = await _begin()
    claim = out.claim
    assert await claim.start(RUN) and await claim.confirm()
    with organization_scope(ORG):
        k = send_once.send_key(ORG, THREAD, ALICE, SID)
    held = json.loads(await real.get(k.value))
    assert (held["state"], held["run_id"]) == ("run", RUN)
    assert 0 < await real.ttl(k.value) <= send_once.RUN_TTL_SECONDS
    # A value of another request stays.
    other = json.dumps({**held, "claim": "another"})
    await real.set(k.value, other)
    await claim.release()
    assert await real.get(k.value) == other


async def test_replay_events_of_a_missing_stream_returns_at_once(real) -> None:
    events = await asyncio.wait_for(
        stream_relay.replay_events("thread-none", since_id="0-0"), ANSWER_WITHIN_S)
    assert events == []


async def test_replay_events_of_a_full_batch_returns(real) -> None:
    """A stream of exactly ``count`` entries: the read after the last batch
    gets nothing, and it must return (the 500 x n case of /reconnect and the
    drain of the fold)."""
    await _push(real, "thread-full", [{"type": "TEXT_MESSAGE_CONTENT", "delta": str(i)}
                                      for i in range(10)])
    events = await asyncio.wait_for(
        stream_relay.replay_events("thread-full", since_id="0-0", count=5, drain=True),
        ANSWER_WITHIN_S)
    assert [e["delta"] for e in events] == [str(i) for i in range(10)]


async def test_the_stream_head_reads_a_missing_stream_at_once(real) -> None:
    assert await asyncio.wait_for(stream_relay.stream_head("thread-none"), 1.0) == []
