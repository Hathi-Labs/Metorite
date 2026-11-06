"""Unit tests for the live activity bus (E2 live observability).

The bus (`acb_common.activity`) publishes one small event per agent run / model
call to the tenant's Redis stream (H-201c) so the /observability feed can show
activations in real time. test_activity_feed_tenant.py owns the two-org proof. These tests cover the pure event-shaping + the best-effort,
non-blocking publish contract WITHOUT needing a live Redis (the Redis IO is
monkeypatched / exercised only on the no-loop swallow path).
"""
from __future__ import annotations

import asyncio
import json

import pytest
from acb_common import bind_run_context, clear_run_context, publish_activity
from acb_common.activity import _build_event
from acb_common.db import bind_tenant, release_tenant

#: The tenant that the publish and presence tests bind. H-201c drops a write
#: with no tenant, so each test that expects a write binds this one.
_ORG = "org-bus-test"


@pytest.fixture
def bound_org():
    token = bind_tenant(_ORG)
    try:
        yield _ORG
    finally:
        release_tenant(token)


def _live(run_id: str) -> str:
    return f"cc:{_ORG}:activity:live:{run_id}"

# ── Event shaping (pure) ─────────────────────────────────────────────────────

def test_build_event_inherits_run_context():
    # A model call inside an agent run inherits that run's attribution so the
    # feed can show WHO/WHICH-app triggered the model call.
    bind_run_context(run_id="r1", thread_id="t1", agent="sales",
                     user="u@x", source="email")
    try:
        evt = _build_event({"kind": "model", "model": "gpt-x", "tier": "2"})
    finally:
        clear_run_context()
    assert evt["kind"] == "model"
    assert evt["model"] == "gpt-x"
    assert evt["agent"] == "sales"
    assert evt["user"] == "u@x"
    assert evt["source"] == "email"
    assert evt["run_id"] == "r1"
    assert "ts" in evt  # always stamped


def test_build_event_inherits_the_run_instance():
    # WS-6c: `instance` inherits through the SAME _INHERIT mechanism as
    # agent/user/run_id, which is what makes a model call inside a personal
    # agent's run attributable to that tenant partition with no change at the
    # emitting call site.
    bind_run_context(run_id="r-i", agent="email-assistant",
                     user="alice@fracktal.in", instance="u:alice@fracktal.in")
    try:
        evt = _build_event({"kind": "model", "model": "gpt-x"})
    finally:
        clear_run_context()
    assert evt["instance"] == "u:alice@fracktal.in"


def test_build_event_for_a_shared_run_carries_no_instance():
    # A shared agent binds no instance, so the event simply has no such field —
    # the pre-WS-6 shape, byte for byte. Consumers that never heard of
    # `instance` see exactly what they saw before.
    bind_run_context(run_id="r-s", agent="task-manager", source="chat")
    try:
        evt = _build_event({"kind": "model", "model": "gpt-x"})
    finally:
        clear_run_context()
    assert "instance" not in evt


def test_build_event_explicit_fields_win_over_context():
    bind_run_context(agent="sales", source="chat")
    try:
        evt = _build_event({"kind": "agent", "agent": "triage", "source": "tasks"})
    finally:
        clear_run_context()
    assert evt["agent"] == "triage"
    assert evt["source"] == "tasks"


def test_build_event_drops_none_values():
    evt = _build_event({"kind": "model", "model": "m", "tier": None, "tokens": None})
    assert "tier" not in evt
    assert "tokens" not in evt


def test_source_is_bound_into_run_context():
    from acb_common import get_run_context
    bind_run_context(run_id="r2", agent="a", source="email")
    try:
        ctx = get_run_context()
        assert ctx["source"] == "email"
    finally:
        clear_run_context()
    # Cleared symmetrically — no leak into the next run.
    assert "source" not in get_run_context()


# ── Publish contract ─────────────────────────────────────────────────────────

def test_publish_schedules_write_on_the_running_loop(monkeypatch, bound_org):
    recorded: list[dict] = []
    orgs: list[str] = []

    async def _fake_axadd(evt, org):
        recorded.append(evt)
        orgs.append(org)

    monkeypatch.setattr("acb_common.activity._axadd", _fake_axadd)

    async def _run():
        publish_activity(kind="agent", phase="start", agent="sales", run_id="r9")
        # Let the scheduled task run (create_task defers to the loop).
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert recorded, "publish_activity should schedule a stream write"
    assert recorded[0]["kind"] == "agent"
    assert recorded[0]["phase"] == "start"
    assert recorded[0]["run_id"] == "r9"
    # The org rides as an argument, never in the event body (H-201c).
    assert orgs == [_ORG]
    assert "organization_id" not in recorded[0]


def test_tool_event_carries_tool_and_agent(monkeypatch):
    # Granular observability: a kind:"tool" event must keep the exact `tool` name
    # and the `agent` it belongs to so /observability can pin the tool's icon on
    # that agent's avatar. `tool` is a non-inherited field; `agent` inherits from
    # the run context when the emit site omits it.
    bind_run_context(run_id="r7", agent="email-assistant", source="email")
    try:
        evt = _build_event({"kind": "tool", "phase": "start", "tool": "draft_reply"})
    finally:
        clear_run_context()
    assert evt["kind"] == "tool"
    assert evt["phase"] == "start"
    assert evt["tool"] == "draft_reply"
    assert evt["agent"] == "email-assistant"  # keyed to the office cast member


def test_publish_schedules_tool_event(monkeypatch, bound_org):
    recorded: list[dict] = []

    async def _fake_axadd(evt, _org):
        recorded.append(evt)

    monkeypatch.setattr("acb_common.activity._axadd", _fake_axadd)

    async def _run():
        publish_activity(kind="tool", phase="start", tool="run_diagnostics",
                         agent="apis-config", run_id="r8")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert recorded, "publish_activity should schedule the tool event write"
    assert recorded[0]["kind"] == "tool"
    assert recorded[0]["tool"] == "run_diagnostics"
    assert recorded[0]["agent"] == "apis-config"


def test_publish_never_raises_even_if_shaping_fails(monkeypatch):
    def _boom(_fields):
        raise RuntimeError("shape failed")

    monkeypatch.setattr("acb_common.activity._build_event", _boom)
    # Must swallow — a broken feed can never take down the run that emitted it.
    publish_activity(kind="model", model="x")


def test_publish_write_failure_is_swallowed(monkeypatch, bound_org):
    async def _fake_axadd(_evt, _org):
        raise ConnectionError("no redis")

    monkeypatch.setattr("acb_common.activity._axadd", _fake_axadd)

    async def _run():
        publish_activity(kind="model", model="x", tier="1")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # The scheduled task raises internally; the done-callback retrieves the
    # exception so it never surfaces as an unhandled error.
    asyncio.run(_run())


# ── Presence gate (kind="agent" | "app") ────────────────────────────────────

class _FakePresenceRedis:
    """Enough of the redis.asyncio surface for `_axadd`'s presence branch.

    The test wraps it in a TenantRedis, so the keys it sees are the wire keys
    (``cc:<org>:activity:live:<run>``).
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.xadded: list[dict] = []

    async def xadd(self, stream, fields, **_kw):
        self.xadded.append(fields)

    async def set(self, key, value, ex=None, xx=False):
        # `xx=True` means "only if it already exists" — the guard that stops a
        # presence patch from resurrecting a run whose end already landed.
        if xx and key not in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)

    async def scan_iter(self, match=None, count=None):
        import fnmatch
        for k in list(self.store):
            if fnmatch.fnmatchcase(k, match):
                yield k

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]


def _patch_client(monkeypatch, r):
    from acb_common import activity
    from acb_common.tenant_redis import TenantRedis

    monkeypatch.setattr(activity, "get_tenant_redis", lambda **_kw: TenantRedis(r))


def test_app_start_creates_a_presence_key_like_an_agent_run(monkeypatch):
    # A Custom App AI call (kind="app") is a first-class presence source now,
    # not just agents — the office view treats a working app identically to a
    # working agent (RFC docs/app-workshop/README.md §4.6).
    from acb_common import activity

    r = _FakePresenceRedis()
    _patch_client(monkeypatch, r)

    asyncio.run(activity._axadd({
        "kind": "app", "phase": "start", "run_id": "app-run-1",
        "agent": "app:filament-tracker",
    }, _ORG))
    assert _live("app-run-1") in r.store


def test_app_end_clears_the_presence_key(monkeypatch):
    from acb_common import activity

    r = _FakePresenceRedis()
    _patch_client(monkeypatch, r)

    asyncio.run(activity._axadd({
        "kind": "app", "phase": "start", "run_id": "app-run-2",
    }, _ORG))
    assert _live("app-run-2") in r.store
    asyncio.run(activity._axadd({
        "kind": "app", "phase": "end", "run_id": "app-run-2",
    }, _ORG))
    assert _live("app-run-2") not in r.store


def test_presence_carries_the_instance_after_the_late_bind(monkeypatch, bound_org):
    # WS-6a: the presence key is written from the phase="start" event, which is
    # published BEFORE load_agent and therefore before `instance` exists. Without
    # refresh_run_presence the snapshot — and so /observability/active and the
    # office roster, which read it verbatim — could never carry a partition for
    # ANY run. Patching the snapshot is preferred over re-publishing `start`,
    # which every stream consumer would read as a second activation.
    from acb_common import activity

    r = _FakePresenceRedis()
    _patch_client(monkeypatch, r)

    async def _run():
        await activity._axadd({
            "kind": "agent", "phase": "start", "run_id": "run-inst",
            "agent": "email-assistant", "user": "alice@fracktal.in",
            "source": "email", "ts": "2026-08-02T00:00:00+00:00",
        }, _ORG)
        # The pre-fix state, asserted so the regression is visible if the
        # refresh is ever removed.
        before = json.loads(r.store[_live("run-inst")])
        assert "instance" not in before

        activity.refresh_run_presence("run-inst", instance="u:alice@fracktal.in")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # …and the office view's own reader sees it.
        return await activity.active_runs(organization_id=_ORG)

    live = asyncio.run(_run())
    snap = json.loads(r.store[_live("run-inst")])
    assert snap["instance"] == "u:alice@fracktal.in"
    # Nothing else in the snapshot was disturbed — it is a patch, not a rewrite.
    assert snap["agent"] == "email-assistant"
    assert snap["user"] == "alice@fracktal.in"
    assert snap["phase"] == "start"
    assert len(r.xadded) == 1, "the refresh must not add a stream entry"
    assert [e["instance"] for e in live] == ["u:alice@fracktal.in"]


def test_presence_refresh_never_resurrects_a_finished_run(monkeypatch, bound_org):
    # A patch that lands after the end event (or for a run that never started)
    # must be a no-op — a resurrected presence key would show a finished agent
    # as working for LIVE_TTL_SECONDS.
    from acb_common import activity

    r = _FakePresenceRedis()
    _patch_client(monkeypatch, r)

    async def _run():
        await activity._axadd({
            "kind": "agent", "phase": "start", "run_id": "run-gone",
        }, _ORG)
        await activity._axadd({
            "kind": "agent", "phase": "end", "run_id": "run-gone",
        }, _ORG)
        activity.refresh_run_presence("run-gone", instance="u:a@b.com")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert r.store == {}


def test_presence_refresh_ignores_empty_and_missing_input(monkeypatch, bound_org):
    # A shared run's instance is '' — nothing to patch, and no Redis touched
    # (not even the no-running-loop one-shot path, which would open a client).
    from acb_common import activity

    calls: list[tuple] = []

    async def _spy(r, run_id, patch):
        calls.append((run_id, patch))

    monkeypatch.setattr(activity, "_apply_presence_patch", _spy)
    activity.refresh_run_presence("run-x", instance="")
    activity.refresh_run_presence("run-x", instance=None)
    activity.refresh_run_presence("", instance="u:a@b.com")
    assert calls == []


def test_model_events_never_create_a_presence_key(monkeypatch):
    # kind="model" is cost/feed-only — it must never leak into presence, even
    # though the gate now accepts more than one kind.
    from acb_common import activity

    r = _FakePresenceRedis()
    _patch_client(monkeypatch, r)

    asyncio.run(activity._axadd({
        "kind": "model", "phase": "start", "run_id": "model-run-1",
    }, _ORG))
    assert r.store == {}


# ── Cost rollup ──────────────────────────────────────────────────────────────

def test_split_field_parses_rollup_hash_fields():
    from acb_common.activity import _split_field
    assert _split_field("total|cost") == ("total", "", "cost")
    assert _split_field("total|tokens") == ("total", "", "tokens")
    # Provider-prefixed model names contain "/" but never "|".
    assert _split_field("model|deepseek/deepseek-chat|cost") == (
        "model", "deepseek/deepseek-chat", "cost")
    assert _split_field("source|email|calls") == ("source", "email", "calls")


def test_cost_summary_aggregates_daily_rollups(monkeypatch):
    from acb_common import activity

    today = activity._today()

    class _FakeRedis:
        async def hgetall(self, key):
            if key == activity._cost_key(today):
                return {
                    "total|cost": "0.5",
                    "total|tokens": "1500",
                    "total|calls": "3",
                    "model|gpt-4o-mini|cost": "0.2",
                    "model|gpt-4o-mini|tokens": "1000",
                    "model|gpt-4o-mini|calls": "2",
                    "model|deepseek/deepseek-chat|cost": "0.3",
                    "model|deepseek/deepseek-chat|calls": "1",
                    "source|email|cost": "0.2",
                    "source|chat|cost": "0.3",
                    "agent|orchestrator|cost": "0.35",
                    "agent|orchestrator|calls": "2",
                    "agent|email-assistant|cost": "0.15",
                    "instance|u:alice@fracktal.in|cost": "0.15",
                    "instance|u:alice@fracktal.in|calls": "1",
                }
            return {}

    monkeypatch.setattr(activity, "_get_client", lambda: _FakeRedis())

    out = asyncio.run(activity.cost_summary(days=3))
    assert out["window_days"] == 3
    assert len(out["days"]) == 3
    assert out["days"][-1]["date"] == today          # newest last (chart L→R)
    assert out["days"][-1]["cost"] == 0.5
    assert out["totals"]["cost"] == 0.5
    assert out["totals"]["calls"] == 3
    assert out["by_model"]["gpt-4o-mini"]["cost"] == 0.2
    assert out["by_model"]["deepseek/deepseek-chat"]["calls"] == 1
    assert out["by_source"]["email"]["cost"] == 0.2
    assert out["by_source"]["chat"]["cost"] == 0.3
    assert out["by_agent"]["orchestrator"]["cost"] == 0.35
    assert out["by_agent"]["orchestrator"]["calls"] == 2
    assert out["by_agent"]["email-assistant"]["cost"] == 0.15
    # WS-6a interim: "what did alice's personal agents cost today" is a rollup
    # dimension, not a scan of the (2000-entry-bounded) raw stream.
    assert out["by_instance"]["u:alice@fracktal.in"]["cost"] == 0.15
    assert out["by_instance"]["u:alice@fracktal.in"]["calls"] == 1


def test_cost_rollup_folds_the_tenant_partition(monkeypatch):
    # The write side of the same dimension: a priced model event carrying an
    # instance increments instance|<key>|cost, and a shared run (no instance)
    # writes no such field at all — byte-identical to the pre-WS-6 rollup.
    from acb_common import activity

    class _FakePipe:
        def __init__(self):
            self.ops: list[tuple] = []

        def hincrbyfloat(self, key, field, val):
            self.ops.append((field, val))

        def hincrby(self, key, field, val):
            self.ops.append((field, val))

        def expire(self, key, ttl):
            pass

        async def execute(self):
            return []

    class _FakeRedis:
        def __init__(self):
            self.pipe = _FakePipe()

        def pipeline(self, transaction=False):
            return self.pipe

    r = _FakeRedis()
    asyncio.run(activity._record_cost(r, {
        "kind": "model", "model": "gpt-4o-mini", "cost_usd": 0.25,
        "tokens": 100, "source": "email", "agent": "email-assistant",
        "instance": "u:alice@fracktal.in",
    }))
    fields = dict(r.pipe.ops)
    assert fields["instance|u:alice@fracktal.in|cost"] == 0.25
    assert fields["instance|u:alice@fracktal.in|calls"] == 1

    shared = _FakeRedis()
    asyncio.run(activity._record_cost(shared, {
        "kind": "model", "model": "gpt-4o-mini", "cost_usd": 0.25,
        "tokens": 100, "source": "chat", "agent": "task-manager",
    }))
    assert not [f for f, _ in shared.pipe.ops if f.startswith("instance|")]


def test_split_field_parses_an_instance_key():
    # Partition keys contain ':' and '@' but never '|', so the dim|name|metric
    # parse is unambiguous.
    from acb_common.activity import _split_field
    assert _split_field("instance|u:alice@fracktal.in|cost") == (
        "instance", "u:alice@fracktal.in", "cost")
    assert _split_field("instance|t:growth|calls") == (
        "instance", "t:growth", "calls")


def test_cost_summary_empty_history_is_all_zero(monkeypatch):
    from acb_common import activity

    class _Empty:
        async def hgetall(self, key):
            return {}

    monkeypatch.setattr(activity, "_get_client", lambda: _Empty())
    out = asyncio.run(activity.cost_summary(days=7))
    assert out["totals"] == {"cost": 0.0, "tokens": 0, "calls": 0}
    assert out["by_model"] == {}
