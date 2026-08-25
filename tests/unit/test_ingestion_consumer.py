"""BO-20a — the ingestion consumer group + XREADGROUP drain loop.

Offline: no Redis, no DB, no network, no VPS.

What is faked, and what deliberately is NOT
-------------------------------------------
  - **Redis** is faked twice, both at the module's own accessor (the pattern
    the retired ``test_clickup_ingestor.py`` established): ``queue._client`` for the
    producer side and ``consumer._get_client`` for the consumer side. The real
    ``queue.enqueue`` therefore produces the entry the consumer is fed, so
    "the shape ``queue.enqueue`` writes" is a fact of this test rather than a
    hand-copied guess that could drift from the producer.
  - **The event-sink registry is REAL.** ``emit_event`` is never monkeypatched:
    faking it would make every delivery assertion vacuous, and both the
    receivers and the consumer import it inside the function body so patching
    the module attribute would not take effect anyway. A recording sink is
    registered via ``register_event_sink``; teardown restores the snapshot so
    this file cannot leave a later test looking at an empty registry.
  - **``acb_audit.record``** is faked for the Gmail/Zoho receivers (it opens a
    real ``acb_graph`` session). It carries no assertion here.

⚠️ **Re-cut 2026-08-24 by D52 (board WS-39 S1).** ClickUp was this file's
primary worked example — first stream, first payload, first receiver. Its
receiver is deleted, so Zoho took that role throughout. The *shapes* under test
are unchanged; only the source name is. Two assertions that named three streams
now name two, and that is the fixture of record: ``STREAM_SOURCES`` in
``ingestion/consumer.py`` has two entries.

Interim ack semantics are deliberate: BO-20a acks after dispatch regardless of
outcome. Honest XACK + retry + DLQ is BO-20b and is not pre-built here.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ingestion import consumer, event_hooks, queue
from ingestion.event_hooks import clear_event_sinks, register_event_sink
from ingestion.sources.gmail import webhook as gmail_webhook
from ingestion.sources.zoho import webhook as zoho_webhook

_ROOT = Path(__file__).resolve().parents[2]

GMAIL_TOKEN = "gmail-pubsub-token"
ZOHO_SECRET = "zoho-shared-secret"

GMAIL_NOTIFICATION: dict[str, Any] = {"emailAddress": "ops@fracktal.in", "historyId": 9182734}
ZOHO_PAYLOAD: dict[str, Any] = {"event": "Deals.update", "module": "Deals"}


# ---------------------------------------------------------------------------
# Fake Redis (async) — records every verb the consumer issues
# ---------------------------------------------------------------------------

class FakeRedis:
    """Just enough of ``redis.asyncio.Redis`` for the drain loop.

    ``xreadgroup`` serves the queued batches in order, then reports the stream
    as empty and sets ``drained`` — at which point every entry from the last
    batch has already been dispatched and acked, because the loop processes a
    batch before issuing the next read.
    """

    def __init__(
        self,
        batches: list[Any] | None = None,
        timeline: list[tuple[str, str, str]] | None = None,
    ) -> None:
        self.batches: list[Any] = list(batches or [])
        #: Shared with the recording sink — see the ``timeline`` fixture.
        self.timeline: list[tuple[str, str, str]] = timeline if timeline is not None else []
        self.xgroup_calls: list[dict[str, Any]] = []
        self.xreadgroup_calls: list[dict[str, Any]] = []
        self.xack_calls: list[tuple[str, str, str]] = []
        self.group_error: Exception | None = None
        self.read_error: Exception | None = None
        self.reads = 0
        self.drained = asyncio.Event()

    async def xgroup_create(
        self, name: str, groupname: str, id: str = "$", mkstream: bool = False
    ) -> bool:
        self.xgroup_calls.append(
            {"stream": name, "group": groupname, "id": id, "mkstream": mkstream}
        )
        if self.group_error is not None:
            raise self.group_error
        return True

    async def xreadgroup(
        self,
        groupname: str | None = None,
        consumername: str | None = None,
        streams: dict[str, str] | None = None,
        count: int | None = None,
        block: int | None = None,
    ) -> Any:
        self.xreadgroup_calls.append(
            {
                "groupname": groupname,
                "consumername": consumername,
                "streams": dict(streams or {}),
                "count": count,
                "block": block,
            }
        )
        self.reads += 1
        if self.read_error is not None:
            raise self.read_error
        if self.batches:
            return self.batches.pop(0)
        self.drained.set()
        await asyncio.sleep(0.01)  # stand in for the real BLOCK
        return []

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        for entry_id in ids:
            self.xack_calls.append((name, groupname, entry_id))
            self.timeline.append(("ack", name, entry_id))
        return len(ids)


def _producer_fields(stream: str, event_type: str, payload: dict[str, Any]) -> dict[str, str]:
    """The exact field dict ``queue.enqueue`` hands to ``xadd`` — real producer."""
    client = MagicMock()
    client.xadd = MagicMock(return_value="1719123456789-0")
    with patch.object(queue, "_client", lambda: client):
        queue.enqueue(stream, event_type, payload)
    return client.xadd.call_args[0][1]


def _entry(stream: str, event_type: str, payload: dict[str, Any], entry_id: str) -> Any:
    """One XREADGROUP reply carrying a single producer-shaped entry."""
    return [(stream, [(entry_id, _producer_fields(stream, event_type, payload))])]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    """Default state everywhere: the flag is unset (the shipped default)."""
    monkeypatch.delenv("INGESTION_CONSUMER", raising=False)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("INGESTION_CONSUMER", "1")


@pytest.fixture(autouse=True)
async def _no_leaked_task():
    """No test may leave the module-global drain task running."""
    yield
    await consumer.stop_ingestion_consumer()


@pytest.fixture
def timeline():
    """ONE ordered log that both the recording sink and the fake's ``xack``
    append to.

    Two independent lists (``sink_calls`` and ``xack_calls``) cannot tell
    ack-**after**-dispatch from ack-**before**-dispatch — and moving the `XACK`
    above `emit_event` is exactly the edit BO-20b makes to `_dispatch_entry`.
    Entries are ``("sink", source, event_type)`` / ``("ack", stream, entry_id)``.
    """
    return []


@pytest.fixture
def sink_calls(timeline):
    """Record every ``(source, event_type, payload)`` the REAL registry fans out."""
    saved = list(event_hooks._SINKS)
    clear_event_sinks()
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _recording_sink(source: str, event_type: str, payload: dict[str, Any]) -> None:
        calls.append((source, event_type, payload))
        timeline.append(("sink", source, event_type))

    register_event_sink(_recording_sink)
    try:
        yield calls
    finally:
        clear_event_sinks()
        event_hooks._SINKS.extend(saved)


@pytest.fixture
def fake_redis(monkeypatch, timeline):
    """Fake the consumer's own pooled async client accessor."""
    client = FakeRedis(timeline=timeline)
    monkeypatch.setattr(consumer, "_get_client", lambda: client)
    monkeypatch.setattr(consumer, "_client", None)
    return client


@pytest.fixture
def mock_redis(monkeypatch):
    """Fake Redis behind the REAL ``queue.enqueue`` (producer side)."""
    client = MagicMock()
    client.xadd = MagicMock(return_value="1719123456789-0")
    monkeypatch.setattr(queue, "_client", lambda: client)
    return client


async def _drain(fake: FakeRedis, timeout: float = 3.0) -> None:
    """Start the loop, wait until the fake stream is exhausted, then stop."""
    assert await consumer.start_ingestion_consumer() is True
    try:
        await asyncio.wait_for(fake.drained.wait(), timeout)
    finally:
        await consumer.stop_ingestion_consumer()


# ---------------------------------------------------------------------------
# Receiver drive-through helpers (acceptance E / F)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _receiver_edges(monkeypatch):
    """Keep the receivers' non-assertion edges off the DB / provider APIs."""
    monkeypatch.setattr(gmail_webhook, "record", MagicMock())
    monkeypatch.setattr(zoho_webhook, "record", MagicMock())
    monkeypatch.setattr(
        "ingestion.sources.gmail.webhook.get_settings",
        lambda: MagicMock(gmail_pubsub_token=GMAIL_TOKEN),
    )
    monkeypatch.setattr(
        "ingestion.sources.zoho.webhook.get_settings",
        lambda: MagicMock(zoho_webhook_secret=ZOHO_SECRET),
    )


def _client_for(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _post_gmail() -> Any:
    data = base64.b64encode(json.dumps(GMAIL_NOTIFICATION).encode()).decode()
    envelope = {"message": {"data": data, "messageId": "1"}}
    return _client_for(gmail_webhook.router).post(
        "/webhooks/gmail", json=envelope, headers={"Authorization": f"Bearer {GMAIL_TOKEN}"}
    )


def _post_zoho() -> Any:
    return _client_for(zoho_webhook.router).post(
        "/webhooks/zoho", json=ZOHO_PAYLOAD, headers={"X-Zoho-Token": ZOHO_SECRET}
    )


_RECEIVERS = {
    "gmail": (_post_gmail, queue.STREAM_GMAIL, "gmail"),
    "zoho": (_post_zoho, queue.STREAM_ZOHO, "zoho"),
}


# ---------------------------------------------------------------------------
# A. Delivery + ack
# ---------------------------------------------------------------------------

async def test_entry_is_delivered_to_the_sink_and_acked_once(
    flag_on, fake_redis, sink_calls, timeline
):
    """The producer's own entry shape → sink → exactly one XACK, in that order."""
    fake_redis.batches = [
        _entry(queue.STREAM_ZOHO, "Contacts.edit", ZOHO_PAYLOAD, "1719123456789-0")
    ]

    await _drain(fake_redis)

    assert sink_calls == [("zoho", "Contacts.edit", ZOHO_PAYLOAD)]
    # The sink must receive a decoded dict — never the JSON string on the wire.
    payload = sink_calls[0][2]
    assert isinstance(payload, dict)
    assert payload == json.loads(
        _producer_fields(queue.STREAM_ZOHO, "taskUpdated", ZOHO_PAYLOAD)["data"]
    )
    assert fake_redis.xack_calls == [(queue.STREAM_ZOHO, "cc-ingest", "1719123456789-0")]
    # ORDER, on one shared timeline: dispatch strictly precedes the ack. Two
    # separate lists would pass just as happily with the XACK hoisted above
    # emit_event — which is the line BO-20b edits.
    assert timeline == [
        ("sink", "zoho", "Contacts.edit"),
        ("ack", queue.STREAM_ZOHO, "1719123456789-0"),
    ]


async def test_read_uses_the_prescribed_group_and_constants(flag_on, fake_redis, sink_calls):
    await _drain(fake_redis)
    read = fake_redis.xreadgroup_calls[0]
    assert read["groupname"] == "cc-ingest"
    assert read["count"] == 8
    assert read["block"] == 5_000
    assert read["streams"] == {
        queue.STREAM_ZOHO: ">",
        queue.STREAM_GMAIL: ">",
    }
    # Per-worker consumer name: BO-20b's reclaim pass identifies a dead worker
    # by it, so it must not be a constant shared by every gateway process.
    assert read["consumername"] == consumer.consumer_name()
    assert re.fullmatch(r"gw-.+-\d+", read["consumername"])


# ---------------------------------------------------------------------------
# B. The group is declared on all three streams, at the tail
# ---------------------------------------------------------------------------

async def test_group_created_on_every_stream_at_the_tail(flag_on, fake_redis, sink_calls):
    """``$`` skips the buffered backlog; ``0`` would replay up to 10 000 entries
    per stream into real workflow runs at flag-flip."""
    await _drain(fake_redis)

    by_stream = {call["stream"]: call for call in fake_redis.xgroup_calls}
    assert set(by_stream) == {queue.STREAM_ZOHO, queue.STREAM_GMAIL}
    for stream, call in by_stream.items():
        assert call["group"] == "cc-ingest", stream
        assert call["id"] == "$", f"{stream}: id must be '$' (tail), not a replay id"
        assert call["mkstream"] is True, stream


async def test_ensure_groups_called_directly_pins_the_same_contract(fake_redis):
    """Same assertion against the helper, so a loop refactor cannot lose it."""
    await consumer._ensure_groups(fake_redis)
    assert [
        (c["stream"], c["group"], c["id"], c["mkstream"]) for c in fake_redis.xgroup_calls
    ] == [
        (queue.STREAM_ZOHO, "cc-ingest", "$", True),
        (queue.STREAM_GMAIL, "cc-ingest", "$", True),
    ]


# ---------------------------------------------------------------------------
# C. Idempotence — BUSYGROUP and a double start
# ---------------------------------------------------------------------------

async def test_busygroup_does_not_fail_startup(flag_on, fake_redis, sink_calls):
    fake_redis.group_error = redis.ResponseError(
        "BUSYGROUP Consumer Group name already exists"
    )
    fake_redis.batches = [
        _entry(queue.STREAM_ZOHO, "Deals.update", ZOHO_PAYLOAD, "17-0")
    ]

    await _drain(fake_redis)

    assert sink_calls == [("zoho", "Deals.update", ZOHO_PAYLOAD)]
    assert fake_redis.xack_calls == [(queue.STREAM_ZOHO, "cc-ingest", "17-0")]


async def test_non_busygroup_response_error_is_not_swallowed(flag_on, fake_redis, monkeypatch):
    """Only BUSYGROUP is idempotent noise; anything else must surface as a
    failed cycle (and must not wedge the loop)."""
    monkeypatch.setattr(consumer, "_ERROR_BACKOFF_SECS", 0)
    fake_redis.group_error = redis.ResponseError("WRONGTYPE Operation against a key")

    assert await consumer.start_ingestion_consumer() is True
    for _ in range(20):
        await asyncio.sleep(0)
        if len(fake_redis.xgroup_calls) >= 2:
            break
    await consumer.stop_ingestion_consumer()

    # The error aborted the cycle before any read, and the loop retried.
    assert fake_redis.xreadgroup_calls == []
    assert len(fake_redis.xgroup_calls) >= 2


async def test_second_start_while_running_is_a_no_op(flag_on, fake_redis, sink_calls):
    assert await consumer.start_ingestion_consumer() is True
    first = consumer._task
    assert await consumer.start_ingestion_consumer() is True
    assert consumer._task is first
    live = [t for t in asyncio.all_tasks() if t.get_name() == "ingestion-consumer"]
    assert len(live) == 1, f"expected one drain task, got {len(live)}"
    assert consumer.consumer_status()["running"] is True

    await consumer.stop_ingestion_consumer()
    assert consumer.consumer_status()["running"] is False


# ---------------------------------------------------------------------------
# D. Flag OFF — nothing starts
# ---------------------------------------------------------------------------

async def test_flag_off_starts_no_task(fake_redis):
    assert await consumer.start_ingestion_consumer() is False
    assert consumer._task is None
    assert consumer.consumer_status()["running"] is False
    assert consumer.consumer_status()["enabled"] is False
    assert fake_redis.xgroup_calls == []
    assert fake_redis.xreadgroup_calls == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        (" on ", True), ("0", False), ("false", False), ("", False), ("maybe", False),
    ],
)
def test_consumer_enabled_truthy_set(raw, expected):
    assert consumer.consumer_enabled({"INGESTION_CONSUMER": raw}) is expected


def test_consumer_enabled_reads_os_environ_at_call_time(monkeypatch):
    """Not a cached ``Settings`` field: ``get_settings`` is lru_cached, which
    would freeze the flag for the process."""
    monkeypatch.delenv("INGESTION_CONSUMER", raising=False)
    assert consumer.consumer_enabled() is False
    monkeypatch.setenv("INGESTION_CONSUMER", "1")
    assert consumer.consumer_enabled() is True


# ---------------------------------------------------------------------------
# E. No double dispatch — all three receivers, both modes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_RECEIVERS))
def test_receiver_emits_inline_when_flag_off(name, mock_redis, sink_calls):
    post, stream, source = _RECEIVERS[name]
    resp = post()
    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}
    assert mock_redis.xadd.call_count == 1
    assert mock_redis.xadd.call_args[0][0] == stream
    # TestClient runs BackgroundTasks before returning — no sleep needed.
    assert [c[0] for c in sink_calls] == [source]


@pytest.mark.parametrize("name", sorted(_RECEIVERS))
def test_receiver_does_not_emit_when_flag_on(name, flag_on, mock_redis, sink_calls):
    """Flag ON ⇒ the consumer is the dispatch path: enqueue still happens
    exactly once, the inline fan-out does not happen at all."""
    post, stream, _source = _RECEIVERS[name]
    resp = post()
    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}
    assert mock_redis.xadd.call_count == 1
    assert mock_redis.xadd.call_args[0][0] == stream
    assert sink_calls == []


# ---------------------------------------------------------------------------
# F. Flag ON + Redis down — accepted drop, logged loudly, never re-emitted
# ---------------------------------------------------------------------------

_WEBHOOK_MODULES = {
    "gmail": gmail_webhook,
    "zoho": zoho_webhook,
}


@pytest.mark.parametrize("name", sorted(_RECEIVERS))
def test_flag_on_redis_down_drops_the_event_but_says_so(
    name, flag_on, mock_redis, sink_calls, monkeypatch
):
    """The §BO-20 Q1 regression, accepted deliberately: after the cutover a
    failed enqueue is a lost event. It must still 200 (a provider webhook never
    5xxes), must NOT fall back to an inline emit (that is double dispatch by
    another name), and must NOT be silent."""
    post, _stream, _source = _RECEIVERS[name]
    mock_redis.xadd.side_effect = RuntimeError("redis down")
    log = MagicMock()
    monkeypatch.setattr(_WEBHOOK_MODULES[name], "_log", log)

    resp = post()

    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}
    assert sink_calls == []
    keys = [call.args[0] for call in log.warning.call_args_list]
    assert f"{name}.queue.dropped" in keys, keys


@pytest.mark.parametrize("name", sorted(_RECEIVERS))
def test_flag_off_redis_down_still_dispatches_inline(name, mock_redis, sink_calls, monkeypatch):
    """The mirror: before the cutover Redis down is survivable — the event still
    dispatches, and nothing claims a drop."""
    post, _stream, source = _RECEIVERS[name]
    mock_redis.xadd.side_effect = RuntimeError("redis down")
    log = MagicMock()
    monkeypatch.setattr(_WEBHOOK_MODULES[name], "_log", log)

    resp = post()

    assert resp.status_code == 200
    assert [c[0] for c in sink_calls] == [source]
    keys = [call.args[0] for call in log.warning.call_args_list]
    assert f"{name}.queue.dropped" not in keys, keys


# ---------------------------------------------------------------------------
# G. Supervision
# ---------------------------------------------------------------------------

async def test_a_raising_sink_does_not_kill_the_loop(flag_on, fake_redis, sink_calls):
    async def _boom(source: str, event_type: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("sink exploded")

    register_event_sink(_boom)
    fake_redis.batches = [
        _entry(queue.STREAM_ZOHO, "Contacts.edit", ZOHO_PAYLOAD, "1-0"),
        _entry(queue.STREAM_GMAIL, "historyUpdated", GMAIL_NOTIFICATION, "2-0"),
    ]

    await _drain(fake_redis)

    assert sink_calls == [
        ("zoho", "Contacts.edit", ZOHO_PAYLOAD),
        ("gmail", "historyUpdated", GMAIL_NOTIFICATION),
    ]
    assert fake_redis.xack_calls == [
        (queue.STREAM_ZOHO, "cc-ingest", "1-0"),
        (queue.STREAM_GMAIL, "cc-ingest", "2-0"),
    ]


async def test_a_failing_read_does_not_kill_the_loop(flag_on, fake_redis, sink_calls, monkeypatch):
    monkeypatch.setattr(consumer, "_ERROR_BACKOFF_SECS", 0)
    fake_redis.read_error = RuntimeError("connection reset")

    assert await consumer.start_ingestion_consumer() is True
    for _ in range(50):
        await asyncio.sleep(0)
        if fake_redis.reads >= 3:
            break
    task = consumer._task
    await consumer.stop_ingestion_consumer()

    assert fake_redis.reads >= 3, "the loop stopped after the first failed cycle"
    assert task is not None and task.done()


async def test_cancellation_propagates_so_stop_returns(flag_on, fake_redis, sink_calls):
    assert await consumer.start_ingestion_consumer() is True
    task = consumer._task
    assert task is not None

    # Let the loop actually REACH its blocking read first. Without this wait,
    # `cancel()` lands on a task that has never been stepped: asyncio then
    # throws CancelledError in at the coroutine's first instruction — above the
    # `try` — and marks the task cancelled no matter what the except clause
    # does. The assertion below would pass against a loop that swallows
    # CancelledError and returns (verified: it does). This is what makes
    # criterion G's "CancelledError propagates" a real pin rather than a pin on
    # asyncio's own `_must_cancel` fast path.
    await asyncio.wait_for(fake_redis.drained.wait(), 2.0)

    await asyncio.wait_for(consumer.stop_ingestion_consumer(), 2.0)

    # `.cancelled()`, not `.done()`: criterion G says CancelledError must
    # PROPAGATE. A loop that caught it and returned normally would be `.done()`
    # too — the weaker assertion cannot see the difference. (`wait_for` above
    # is what pins the other half, that `stop` returns at all.)
    assert task.cancelled()
    assert consumer._task is None
    assert consumer.consumer_status()["running"] is False


async def test_stop_when_never_started_is_a_no_op():
    await consumer.stop_ingestion_consumer()
    assert consumer.consumer_status()["running"] is False


async def test_a_hung_sink_times_out_and_the_bus_keeps_draining(
    flag_on, fake_redis, sink_calls, monkeypatch
):
    """One serial loop drains all three streams, so an unbounded ``await
    emit_event`` would convert a single hung sink into a **bus-wide** stall —
    strictly worse than pre-cutover, where the hang was confined to one
    request's ``BackgroundTasks``. ``_DISPATCH_TIMEOUT_SECS`` bounds it."""
    monkeypatch.setattr(consumer, "_DISPATCH_TIMEOUT_SECS", 0.05)
    log = MagicMock()
    monkeypatch.setattr(consumer, "_log", log)

    async def _hang(source: str, event_type: str, payload: dict[str, Any]) -> None:
        await asyncio.sleep(30)  # never returns within the test

    register_event_sink(_hang)
    fake_redis.batches = [
        _entry(queue.STREAM_ZOHO, "Contacts.edit", ZOHO_PAYLOAD, "1-0"),
        _entry(queue.STREAM_GMAIL, "historyUpdated", GMAIL_NOTIFICATION, "2-0"),
    ]

    await _drain(fake_redis)

    # The stream after the hung one still drains, and both entries still ack.
    assert [c[0] for c in sink_calls] == ["zoho", "gmail"]
    assert fake_redis.xack_calls == [
        (queue.STREAM_ZOHO, "cc-ingest", "1-0"),
        (queue.STREAM_GMAIL, "cc-ingest", "2-0"),
    ]
    # And it is LOUD — the failure mode this replaces had no log line at all.
    keys = [call.args[0] for call in log.warning.call_args_list]
    assert keys.count("consumer.dispatch_timeout") == 2, keys


async def test_undecodable_entry_is_acked_not_dispatched(flag_on, fake_redis, sink_calls):
    """A poison entry must not wedge the group, and must not reach a sink with
    a payload no run could act on."""
    fake_redis.batches = [
        [(queue.STREAM_ZOHO, [("9-0", {"event_type": "Deals.update", "data": "{not json"})])]
    ]

    await _drain(fake_redis)

    assert sink_calls == []
    assert fake_redis.xack_calls == [(queue.STREAM_ZOHO, "cc-ingest", "9-0")]


# ---------------------------------------------------------------------------
# H. Packaging — the gateway must actually depend on `ingestion`
# ---------------------------------------------------------------------------

def test_gateway_declares_the_ingestion_dependency():
    text = (_ROOT / "apps/services/gateway/pyproject.toml").read_text(encoding="utf-8")
    # split on a bracket at column 0 — `uvicorn[standard]` closes one too
    deps = text.split("dependencies = [", 1)[1].split("\n]", 1)[0]
    assert '"ingestion"' in deps, (
        "the gateway lifespan starts ingestion.consumer; the dependency must be "
        "declared, not inherited from the root workspace umbrella"
    )


def test_uv_lock_records_ingestion_under_gateway():
    lock = (_ROOT / "uv.lock").read_text(encoding="utf-8")
    block = lock.split('name = "gateway"', 1)[1].split("[[package]]", 1)[0]
    assert '{ name = "ingestion" }' in block
    assert '{ name = "ingestion", editable = "apps/services/ingestion" }' in block


def test_consumer_module_imports_and_exposes_its_lifecycle():
    import importlib  # noqa: PLC0415

    mod = importlib.import_module("ingestion.consumer")
    for attr in (
        "start_ingestion_consumer",
        "stop_ingestion_consumer",
        "consumer_status",
        "consumer_enabled",
    ):
        assert hasattr(mod, attr), attr
    assert mod._GROUP == "cc-ingest"
    assert mod._BLOCK_MS == 5_000
    assert mod._READ_COUNT == 8
    assert mod._DISPATCH_TIMEOUT_SECS == 30.0


def test_gateway_lifespan_starts_the_consumer_and_stops_it_after_yield():
    """The "stopped unconditionally" contract (checklist §BO-20a, gateway
    AGENTS.md §1) is otherwise unpinned: deleting the stop call, or hoisting the
    start above the sink registration, leaves every other test in this file
    green. Text-level for the same reason as the two packaging tests above —
    importing `gateway.main` pulls in the whole app."""
    text = (_ROOT / "apps/services/gateway/gateway/main.py").read_text(encoding="utf-8")
    assert "start_ingestion_consumer" in text
    assert "stop_ingestion_consumer" in text

    start_at = text.index("await start_ingestion_consumer()")
    yield_at = text.index("\n    yield")
    stop_at = text.index("await stop_ingestion_consumer()")
    assert start_at < yield_at, "the consumer must start during startup"
    assert yield_at < stop_at, "the consumer must be stopped on SHUTDOWN, after the yield"

    # Unconditional: the stop is not guarded on the flag. A flag read at
    # shutdown can differ from the one at startup and would leak the task.
    tail = text[yield_at:stop_at]
    assert "consumer_enabled" not in tail, (
        "stop_ingestion_consumer must be called unconditionally, like "
        "stop_whatsapp_enrichment — not behind an INGESTION_CONSUMER check"
    )


# ---------------------------------------------------------------------------
# J. `emit_event` strict mode — BO-20b's blocker, landed ahead of the loop
#
# BO-20b's retry/DLQ logic is dead code without this: `emit_event` swallows
# every sink exception by design, so a drain loop calling it can never observe
# a failed dispatch and can never honestly withhold an `XACK`. This slice adds
# only the option; `consumer.py` is untouched and still acks regardless of
# outcome (BO-20a interim semantics).
# ---------------------------------------------------------------------------

@pytest.fixture
def two_sinks():
    """A raising sink followed by a recording one, through the REAL registry.

    Registered with `register_event_sink`, never by monkeypatching `emit_event`
    — patching the function would make "the second sink was/wasn't called"
    vacuous, and it is the *ordering* through the real `_SINKS` list that both
    modes differ on. Teardown restores the snapshot, like `sink_calls`.
    """
    saved = list(event_hooks._SINKS)
    clear_event_sinks()
    boom = RuntimeError("sink one exploded")
    second_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _first(source: str, event_type: str, payload: dict[str, Any]) -> None:
        raise boom

    async def _second(source: str, event_type: str, payload: dict[str, Any]) -> None:
        second_calls.append((source, event_type, payload))

    register_event_sink(_first)
    register_event_sink(_second)
    try:
        yield boom, second_calls
    finally:
        clear_event_sinks()
        event_hooks._SINKS.extend(saved)


async def test_strict_emit_propagates_the_first_sink_error_and_skips_the_rest(two_sinks):
    """`raise_on_error=True`: the FIRST exception reaches the caller and the
    fan-out stops there — the consumer needs the exception, not a best-effort
    summary, to decide against acking."""
    boom, second_calls = two_sinks

    with pytest.raises(RuntimeError) as excinfo:
        await event_hooks.emit_event(
            "zoho", "Contacts.edit", ZOHO_PAYLOAD, raise_on_error=True
        )

    assert excinfo.value is boom, "the sink's own exception, not a wrapper"
    assert second_calls == [], "sinks after the raising one must not be invoked"


async def test_default_emit_still_swallows_logs_and_continues(two_sinks, monkeypatch):
    """The default is byte-for-byte today's behaviour: swallow, log
    `event_hooks.sink_failed`, run the next sink, return None. Every receiver
    depends on it — a webhook must never 5xx because a workflow sink failed."""
    _boom, second_calls = two_sinks
    log = MagicMock()
    monkeypatch.setattr(event_hooks, "_log", log)

    result = await event_hooks.emit_event("zoho", "Contacts.edit", ZOHO_PAYLOAD)

    assert result is None
    assert second_calls == [("zoho", "Contacts.edit", ZOHO_PAYLOAD)]
    keys = [call.args[0] for call in log.warning.call_args_list]
    assert keys == ["event_hooks.sink_failed"], keys


def test_raise_on_error_defaults_to_false_and_is_keyword_only():
    """Pinned against the literal `False`, so a later PR cannot flip
    provider-facing behaviour silently; keyword-only so the three receivers'
    three-positional-arg `add_task(emit_event, source, event_type, payload)`
    can never reach it, whatever a future fourth positional argument is."""
    param = inspect.signature(event_hooks.emit_event).parameters["raise_on_error"]

    assert param.default is False
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
