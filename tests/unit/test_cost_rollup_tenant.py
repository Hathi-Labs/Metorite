"""The daily cost rollup is keyed for each tenant (H-201e, task 1748).

Before H-201e, ``/observability/cost`` read ONE deployment-global hash,
``cc:cost:{day}``. Its ``by_instance`` field held the ``u:<email>`` keys of
every tenant, and any signed-in caller could read it.

The design (``acb_common.activity``):

* a priced model call with an org folds into ``cc:<org>:cost:<day>``, through
  ``acb_common.tenant_redis``. The org is the explicit value, else the bound
  tenant (``acb_common.db.current_tenant()``);
* a priced call with NO org (the gateway's ``/v1/chat/completions`` under the
  deployment key) folds into ONE deployment-level hash,
  ``cc:_unattributed:cost:<day>``. No tenant route reads it. An org id cannot
  start with ``_``, so no tenant key can equal it;
* ``cost_summary`` takes a required ``organization_id``. The route passes
  ``viewer.organization_id`` from the session. A falsy org gets the empty
  summary;
* nothing writes or reads the old global ``cc:cost:{day}`` hash. There is no
  dual read.

The fake is a raw Redis, patched at ``aioredis.from_url``, so every pool of
``tenant_redis`` and the raw client of ``activity`` meet the same store. The
test reads the wire keys, so a key with no tenant is visible.

Mutations this suite catches (R7). Each was run, and each turned it red:

* M1: the writer and the reader back on the global ``cc:cost:{day}`` hash;
* M2: ``cost_summary`` ignores ``organization_id`` and reads the ambient
  tenant;
* M3: a priced call with no org is dropped, or falls into a tenant's hash;
* M4: the route passes the ambient tenant, not ``viewer.organization_id``.

Run::

    uv run pytest tests/unit/test_cost_rollup_tenant.py -q
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

_ORG_A = str(uuid.uuid4())
_ORG_B = str(uuid.uuid4())
_ALICE = "alice@cost-a.test"
_CAROL = "carol@cost-b.test"
_INSTANCE_A = f"u:{_ALICE}"


# ---------------------------------------------------------------------------
# A fake raw Redis with real hash arithmetic.
# ---------------------------------------------------------------------------

class _FakePipe:
    def __init__(self, owner: _FakeRedis) -> None:
        self._owner = owner
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def hincrbyfloat(self, key, name, amount):
        self._ops.append(("hincrbyfloat", (key, name, amount)))
        return self

    def hincrby(self, key, name, amount=1):
        self._ops.append(("hincrby", (key, name, amount)))
        return self

    def expire(self, key, seconds):
        self._ops.append(("expire", (key, seconds)))
        return self

    async def execute(self) -> list[Any]:
        out = []
        for name, args in self._ops:
            out.append(await getattr(self._owner, name)(*args))
        return out


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.streams: dict[str, list[Any]] = {}
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.reads: list[str] = []

    def keys_written(self) -> set[str]:
        return set(self.hashes) | set(self.streams) | set(self.store)

    async def hincrbyfloat(self, key, name, amount):
        h = self.hashes.setdefault(key, {})
        h[name] = str(float(h.get(name, 0)) + float(amount))
        return float(h[name])

    async def hincrby(self, key, name, amount=1):
        h = self.hashes.setdefault(key, {})
        h[name] = str(int(float(h.get(name, 0))) + int(amount))
        return int(h[name])

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    async def hgetall(self, key):
        self.reads.append(key)
        return dict(self.hashes.get(key, {}))

    async def xadd(self, key, fields, **_kw):
        self.streams.setdefault(key, []).append(dict(fields))
        return "1-0"

    async def set(self, key, value, **_kw):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        return sum(int(self.store.pop(k, None) is not None) for k in keys)

    def pipeline(self, transaction=False):
        return _FakePipe(self)

    async def aclose(self):
        return None


@pytest.fixture
def fake_redis(monkeypatch):
    import redis.asyncio as aioredis
    from acb_common import activity, tenant_redis

    r = _FakeRedis()
    monkeypatch.setattr(aioredis, "from_url", lambda *_a, **_kw: r)
    tenant_redis.reset_pool_for_tests()
    monkeypatch.setattr(activity, "_client", None)
    yield r
    tenant_redis.reset_pool_for_tests()


def _user(email: str, org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


async def _drain() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _priced_call(org: str | None, *, cost: float, instance: str | None = None,
                       agent: str = "cost-agent") -> None:
    """One priced model call, under the bound tenant *org* (or none)."""
    from acb_common import publish_activity
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(org) if org else None
    try:
        publish_activity(kind="model", model="cost-model", agent=agent,
                         source="chat", instance=instance,
                         cost_usd=cost, tokens=100)
    finally:
        if token is not None:
            release_tenant(token)
    await _drain()


def _cost_route(viewer, days: int = 7):
    from gateway.routes.observability import cost

    return cost(days=days, viewer=viewer)


def _all_text(summary: dict[str, Any]) -> str:
    return repr(summary)


# ---------------------------------------------------------------------------
# The route: org B sees nothing of org A, and org A sees its own.
# ---------------------------------------------------------------------------

def test_each_org_sees_only_its_own_cost(fake_redis):
    alice, carol = _user(_ALICE, _ORG_A), _user(_CAROL, _ORG_B)

    async def _go() -> tuple[dict, dict]:
        await _priced_call(_ORG_A, cost=0.25, instance=_INSTANCE_A)
        await _priced_call(_ORG_A, cost=0.5, instance=_INSTANCE_A)
        return await _cost_route(alice), await _cost_route(carol)

    a, b = asyncio.run(_go())

    # The positive control: A sees its own spend and its own member.
    assert a["totals"]["calls"] == 2
    assert a["totals"]["cost"] == 0.75
    assert a["by_instance"][_INSTANCE_A] == {"cost": 0.75, "calls": 2}
    assert a["by_agent"]["cost-agent"]["calls"] == 2

    # B sees none of it: no row, no email, no total.
    assert b["totals"] == {"cost": 0.0, "tokens": 0, "calls": 0}
    assert b["by_instance"] == {} and b["by_agent"] == {}
    assert b["by_model"] == {} and b["by_source"] == {}
    assert _ALICE not in _all_text(b)
    assert all(d["calls"] == 0 for d in b["days"])


def test_each_org_reads_only_its_own_keys(fake_redis):
    async def _go() -> None:
        await _priced_call(_ORG_A, cost=0.1, instance=_INSTANCE_A)
        await _cost_route(_user(_CAROL, _ORG_B), days=3)

    asyncio.run(_go())
    assert len(fake_redis.reads) == 3
    assert all(k.startswith(f"cc:{_ORG_B}:cost:") for k in fake_redis.reads)


def test_a_viewer_with_no_org_gets_the_empty_summary(fake_redis):
    async def _go() -> dict:
        await _priced_call(_ORG_A, cost=0.25, instance=_INSTANCE_A)
        await _priced_call(None, cost=0.4)
        return await _cost_route(_user(_ALICE, None), days=5)

    out = asyncio.run(_go())
    assert out["totals"] == {"cost": 0.0, "tokens": 0, "calls": 0}
    assert out["window_days"] == 5 and len(out["days"]) == 5
    assert out["by_instance"] == {} and out["by_model"] == {}
    assert fake_redis.reads == []


def test_the_route_follows_the_viewer_not_the_ambient_tenant(fake_redis):
    """M4: a route bound to org A still answers for its viewer of org B."""
    from acb_common.db import bind_tenant, release_tenant

    async def _go() -> dict:
        await _priced_call(_ORG_A, cost=0.3, instance=_INSTANCE_A)
        token = bind_tenant(_ORG_A)
        try:
            return await _cost_route(_user(_CAROL, _ORG_B))
        finally:
            release_tenant(token)

    out = asyncio.run(_go())
    assert out["totals"]["calls"] == 0
    assert _ALICE not in _all_text(out)


def test_the_reader_takes_the_org_it_is_given(fake_redis):
    """M2: ``cost_summary`` reads the org of its argument, never the ambient."""
    from acb_common import cost_summary
    from acb_common.tenant_redis import organization_scope

    async def _go() -> tuple[dict, dict]:
        await _priced_call(_ORG_A, cost=0.2, instance=_INSTANCE_A)
        with organization_scope(_ORG_A):
            b = await cost_summary(days=3, organization_id=_ORG_B)
        with organization_scope(_ORG_B):
            a = await cost_summary(days=3, organization_id=_ORG_A)
        return a, b

    a, b = asyncio.run(_go())
    assert a["totals"]["calls"] == 1
    assert b["totals"]["calls"] == 0


# ---------------------------------------------------------------------------
# The write side: every key carries its org, or is the one unattributed key.
# ---------------------------------------------------------------------------

def test_each_cost_key_starts_with_its_org(fake_redis):
    from acb_common import activity

    async def _go() -> None:
        await _priced_call(_ORG_A, cost=0.1, instance=_INSTANCE_A)
        await _priced_call(_ORG_B, cost=0.2, instance=f"u:{_CAROL}")

    asyncio.run(_go())
    day = activity._today()
    assert set(fake_redis.hashes) == {
        f"cc:{_ORG_A}:cost:{day}", f"cc:{_ORG_B}:cost:{day}",
    }
    a = fake_redis.hashes[f"cc:{_ORG_A}:cost:{day}"]
    assert f"instance|{_INSTANCE_A}|cost" in a
    assert not any(_CAROL in f for f in a)
    assert fake_redis.ttls[f"cc:{_ORG_A}:cost:{day}"] == activity.COST_TTL_SECONDS


def test_the_explicit_org_wins_over_the_bound_tenant(fake_redis):
    from acb_common import activity, publish_activity
    from acb_common.db import bind_tenant, release_tenant

    async def _go() -> None:
        token = bind_tenant(_ORG_A)
        try:
            publish_activity(organization_id=_ORG_B, kind="model", model="m",
                             cost_usd=0.1, tokens=1)
        finally:
            release_tenant(token)
        await _drain()

    asyncio.run(_go())
    cost_keys = {k for k in fake_redis.hashes}
    assert cost_keys == {f"cc:{_ORG_B}:cost:{activity._today()}"}


def test_an_untenanted_priced_call_counts_in_the_unattributed_key(fake_redis):
    """M3: the call is neither dropped nor folded into a tenant's hash."""
    from acb_common import activity

    async def _go() -> tuple[dict, dict]:
        await _priced_call(None, cost=0.4, agent="deploy-key-agent")
        return (await _cost_route(_user(_ALICE, _ORG_A)),
                await _cost_route(_user(_CAROL, _ORG_B)))

    a, b = asyncio.run(_go())
    unattributed = f"cc:_unattributed:cost:{activity._today()}"
    assert set(fake_redis.hashes) == {unattributed}
    h = fake_redis.hashes[unattributed]
    assert float(h["total|cost"]) == 0.4 and h["total|calls"] == "1"
    assert h["agent|deploy-key-agent|calls"] == "1"
    # No tenant viewer sees it.
    for out in (a, b):
        assert out["totals"]["calls"] == 0
        assert "deploy-key-agent" not in _all_text(out)
    assert unattributed not in fake_redis.reads
    # And it wrote no stream or presence key.
    assert fake_redis.streams == {} and fake_redis.store == {}


def test_no_write_or_read_touches_the_old_global_hash(fake_redis):
    """M1: nothing writes or reads ``cc:cost:{day}`` any more."""
    async def _go() -> None:
        await _priced_call(_ORG_A, cost=0.1, instance=_INSTANCE_A)
        await _priced_call(None, cost=0.2)
        await _cost_route(_user(_ALICE, _ORG_A))
        await _cost_route(_user(_CAROL, _ORG_B))

    asyncio.run(_go())
    touched = fake_redis.keys_written() | set(fake_redis.reads)
    assert touched
    assert not [k for k in touched if k.startswith("cc:cost:")], touched
    assert all(
        k.startswith((f"cc:{_ORG_A}:", f"cc:{_ORG_B}:", "cc:_unattributed:cost:"))
        for k in touched
    ), touched


def test_an_old_global_hash_is_never_read_back(fake_redis):
    """No dual read: the orphaned global hash stays invisible after deploy."""
    from acb_common import activity

    fake_redis.hashes[f"cc:cost:{activity._today()}"] = {
        "total|cost": "9.0", "total|calls": "3",
        f"instance|{_INSTANCE_A}|cost": "9.0",
    }
    out = asyncio.run(_cost_route(_user(_CAROL, _ORG_B)))
    assert out["totals"]["calls"] == 0
    assert _ALICE not in _all_text(out)


def test_the_unattributed_key_cannot_be_a_tenant_key():
    """``_`` cannot lead an org id, so no tenant prefix can equal the key."""
    from acb_common.tenant_redis import bind_organization

    with pytest.raises(ValueError):
        bind_organization("_unattributed")
