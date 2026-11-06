"""KG-9 (task 786): the memory layer refuses to start at two organizations.

Spec: ``project-docs/specs/knowledge_graph.md`` §9, the KG-9 row, and Q2.

The Mem0 store and the Graphiti graph have no org scope. So while two
organizations or more exist, the memory layer must not start. This file is the
fence (R7):

1. The Mem0 flag on, two orgs: the startup check raises, and no read or write
   reaches Mem0.
2. One org: the memory layer starts.
3. Both flags off: the guard reads no database, and nothing changes.
4. The org key holds the org id, never the one shared ``org:global``. The
   Graphiti ``group_id`` holds the org too, never ``default``.
5. R8: the probe SQL runs against a real ``organization`` table.

No test here sets an env var. Each flag is a settings stub in this process.
"""
from __future__ import annotations

import os
import types
from typing import Any

import pytest

import acb_memory
from acb_memory import compartments, graphiti_client, mem0_client, tenant_guard
from acb_memory.tenant_guard import MemoryTenancyRefused

ORG_A = "0b9f1c2e-1111-4222-8333-444455556666"
ORG_B = "7c0d2e3f-aaaa-4bbb-8ccc-ddddeeeeffff"


@pytest.fixture(autouse=True)
def _fresh_verdict():
    tenant_guard.reset_memory_tenancy()
    yield
    tenant_guard.reset_memory_tenancy()


def _flags(
    monkeypatch: pytest.MonkeyPatch, *, mem0: bool = False, graphiti: bool = False,
) -> types.SimpleNamespace:
    """A settings stub for the guard, in this process only."""
    settings = types.SimpleNamespace(
        mem0_enabled=mem0, graphiti_enabled=graphiti, neo4j_password="x",
    )
    monkeypatch.setattr(tenant_guard, "get_settings", lambda: settings)
    return settings


def _orgs(monkeypatch: pytest.MonkeyPatch, count: int) -> list[int]:
    """Stub the probe. The list counts the probes that ran."""
    probes: list[int] = []

    async def _probe() -> bool:
        probes.append(count)
        return count > 1

    monkeypatch.setattr(tenant_guard, "_organizations_more_than_one", _probe)
    return probes


class _Mem0:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, **kw: Any) -> list[dict]:
        self.calls.append("search")
        return [{"memory": "a fact"}]

    def add(self, *a: Any, **kw: Any) -> None:
        self.calls.append("add")

    def get_all(self, **kw: Any) -> list[dict]:
        self.calls.append("get_all")
        return [{"id": "m1"}]


def _client_with(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _Mem0, list[int]]:
    """A MemoryClient whose Mem0 is a stub. The list counts the builds."""
    fake = _Mem0()
    builds: list[int] = []

    def _build() -> _Mem0:
        builds.append(1)
        return fake

    client = mem0_client.MemoryClient()
    monkeypatch.setattr(client, "_get_client", _build)
    return client, fake, builds


# ---------------------------------------------------------------------------
# 1. Two orgs: the memory layer refuses to start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mem0_on_with_two_orgs_refuses_to_start(monkeypatch) -> None:
    _flags(monkeypatch, mem0=True)
    _orgs(monkeypatch, 2)
    with pytest.raises(MemoryTenancyRefused):
        await tenant_guard.check_memory_tenancy()


@pytest.mark.asyncio
async def test_mem0_on_with_two_orgs_reads_and_writes_nothing(monkeypatch) -> None:
    _flags(monkeypatch, mem0=True)
    _orgs(monkeypatch, 2)
    client, fake, builds = _client_with(monkeypatch)

    assert await client.search("a@x.test", "q") == []
    await client.add("a@x.test", [{"role": "user", "content": "hi"}])
    assert await client.get_all("a@x.test") == []
    # The client was never built, so Mem0 did not start.
    assert builds == []
    assert fake.calls == []


@pytest.mark.asyncio
async def test_graphiti_on_with_two_orgs_refuses_to_start(monkeypatch) -> None:
    settings = _flags(monkeypatch, graphiti=True)
    _orgs(monkeypatch, 2)
    monkeypatch.setattr(graphiti_client, "get_settings", lambda: settings)
    gc = graphiti_client.GraphitiClient()
    assert await gc._get_graphiti() is None
    # A refusal does not mark the start as tried, so a later call asks again.
    assert gc._init_attempted is False
    with pytest.raises(MemoryTenancyRefused):
        await tenant_guard.check_memory_tenancy()


@pytest.mark.asyncio
async def test_a_refusal_stays_for_the_life_of_the_process(monkeypatch) -> None:
    _flags(monkeypatch, mem0=True)
    probes = _orgs(monkeypatch, 2)
    assert await tenant_guard.memory_tenancy_ok() is False
    assert await tenant_guard.memory_tenancy_ok() is False
    assert len(probes) == 1


@pytest.mark.asyncio
async def test_a_probe_fault_refuses_and_asks_again(monkeypatch) -> None:
    _flags(monkeypatch, mem0=True)
    calls: list[int] = []

    async def _boom() -> bool:
        calls.append(1)
        raise ConnectionError("no database")

    monkeypatch.setattr(tenant_guard, "_organizations_more_than_one", _boom)
    assert await tenant_guard.memory_tenancy_ok() is False
    assert await tenant_guard.memory_tenancy_ok() is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_second_org_stops_a_started_layer_after_the_ttl(monkeypatch) -> None:
    _flags(monkeypatch, mem0=True)
    count = {"n": 1}

    async def _probe() -> bool:
        return count["n"] > 1

    monkeypatch.setattr(tenant_guard, "_organizations_more_than_one", _probe)
    clock = {"t": 1000.0}
    monkeypatch.setattr(tenant_guard.time, "monotonic", lambda: clock["t"])

    assert await tenant_guard.memory_tenancy_ok() is True
    count["n"] = 2
    clock["t"] += tenant_guard.PASS_TTL_S / 2
    assert await tenant_guard.memory_tenancy_ok() is True  # the pass is kept
    clock["t"] += tenant_guard.PASS_TTL_S
    assert await tenant_guard.memory_tenancy_ok() is False


# ---------------------------------------------------------------------------
# 2. One org: the memory layer starts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mem0_on_with_one_org_starts(monkeypatch) -> None:
    _flags(monkeypatch, mem0=True)
    _orgs(monkeypatch, 1)
    await tenant_guard.check_memory_tenancy()
    client, fake, builds = _client_with(monkeypatch)

    assert await client.search("a@x.test", "q") == [{"memory": "a fact"}]
    await client.add("a@x.test", [{"role": "user", "content": "hi"}])
    assert await client.get_all("a@x.test") == [{"id": "m1"}]
    assert fake.calls == ["search", "add", "get_all"]
    assert builds


# ---------------------------------------------------------------------------
# 3. Flags off: nothing happens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flags_off_reads_no_database(monkeypatch) -> None:
    _flags(monkeypatch)

    async def _never() -> bool:
        raise AssertionError("the guard must not read the database with both flags off")

    monkeypatch.setattr(tenant_guard, "_organizations_more_than_one", _never)
    assert await tenant_guard.memory_tenancy_ok() is True
    await tenant_guard.check_memory_tenancy()  # does not raise


@pytest.mark.asyncio
async def test_flags_off_leaves_the_client_as_it_was(monkeypatch) -> None:
    """With the flag off, ``_get_client`` answers None, as before KG-9."""
    settings = _flags(monkeypatch)
    monkeypatch.setattr(mem0_client, "get_settings", lambda: settings)
    client = mem0_client.MemoryClient()
    assert await client.search("a@x.test", "q") == []
    assert await client.get_all("a@x.test") == []


@pytest.mark.asyncio
async def test_delete_is_not_gated(monkeypatch) -> None:
    """A refused layer must still forget what a member asks it to forget."""
    _flags(monkeypatch, mem0=True)
    _orgs(monkeypatch, 2)
    deleted: list[str] = []

    class _Del:
        def delete(self, memory_id: str) -> None:
            deleted.append(memory_id)

    client = mem0_client.MemoryClient()
    monkeypatch.setattr(client, "_get_client", lambda: _Del())
    await client.delete("m1")
    assert deleted == ["m1"]


def test_the_package_exports_the_guard() -> None:
    assert acb_memory.check_memory_tenancy is tenant_guard.check_memory_tenancy
    assert acb_memory.MemoryTenancyRefused is MemoryTenancyRefused


def test_the_gateway_runs_the_startup_check() -> None:
    from pathlib import Path

    main = Path("apps/services/gateway/gateway/main.py").read_text(encoding="utf-8")
    lifespan = main[main.index("async def lifespan("):main.index("lifespan=lifespan")]
    assert "check_memory_tenancy()" in lifespan


# ---------------------------------------------------------------------------
# 4. The org key holds the org id
# ---------------------------------------------------------------------------


def test_the_org_key_holds_the_org_id() -> None:
    assert compartments.scope_key(org=True, organization_id=ORG_A) == f"org:{ORG_A}"
    assert compartments.scope_key(org=True, organization_id=ORG_B) == f"org:{ORG_B}"
    assert compartments.scope_kind(f"org:{ORG_A}") == "org"


def test_no_org_id_gives_no_org_key() -> None:
    """It never falls back to the one key that every org shares."""
    assert compartments.scope_key(org=True) == ""
    c = compartments.resolve_clearance(
        actor="a@x.test", agent_name="sales", thread_id=None, shared=False,
    )
    assert compartments.ORG_SCOPE_KEY not in c.read
    assert not [s for s in c.read if compartments.scope_kind(s) == "org"]


def test_two_orgs_get_two_org_keys() -> None:
    a = compartments.resolve_clearance(
        actor="a@x.test", agent_name="sales", thread_id=None, shared=False,
        organization_id=ORG_A,
    )
    b = compartments.resolve_clearance(
        actor="a@x.test", agent_name="sales", thread_id=None, shared=False,
        organization_id=ORG_B,
    )
    assert f"org:{ORG_A}" in a.read and f"org:{ORG_B}" not in a.read
    assert f"org:{ORG_B}" in b.read and f"org:{ORG_A}" not in b.read


def test_the_org_scope_comes_from_the_bound_tenant() -> None:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    token = bind_tenant(ORG_A)
    try:
        assert mem0_client.org_scope() == f"org:{ORG_A}"
        assert graphiti_client.graphiti_group_id() == f"org_{ORG_A}"
    finally:
        release_tenant(token)
    token = clear_tenant()
    try:
        assert mem0_client.org_scope() == ""
        assert graphiti_client.graphiti_group_id() == ""
    finally:
        release_tenant(token)


def test_the_fingerprint_does_not_change() -> None:
    """The session and cache keys stay byte-identical to the ones before KG-9."""
    import hashlib

    for shared, org in ((False, ORG_A), (True, ORG_B), (False, None)):
        c = compartments.resolve_clearance(
            actor="a@x.test", agent_name="sales", thread_id="t-1", shared=shared,
            organization_id=org,
        )
        old_read = [s for s in c.read if compartments.scope_kind(s) != "org"]
        old_read.append("org:global")
        material = "|".join((*sorted(old_read), ">", c.write))
        assert c.fingerprint == hashlib.sha256(material.encode()).hexdigest()[:12]


@pytest.mark.asyncio
async def test_a_graphiti_episode_and_search_carry_the_org(monkeypatch) -> None:
    _flags(monkeypatch, graphiti=True)
    _orgs(monkeypatch, 1)
    seen: dict[str, Any] = {}

    class _G:
        async def add_episode(self, **kw: Any) -> None:
            seen["add"] = kw

        async def search(self, **kw: Any) -> list[Any]:
            seen["search"] = kw
            return []

    gc = graphiti_client.GraphitiClient()

    async def _g() -> _G:
        return _G()

    monkeypatch.setattr(gc, "_get_graphiti", _g)
    await gc.add_episode(name="n", content="c", organization_id=ORG_A)
    await gc.search("q", organization_id=ORG_A)
    assert seen["add"]["group_id"] == f"org_{ORG_A}"
    assert seen["search"]["group_ids"] == [f"org_{ORG_A}"]

    # No org, no write and no search. Never the group "default".
    seen.clear()
    from acb_common.db import clear_tenant, release_tenant

    token = clear_tenant()
    try:
        await gc.add_episode(name="n", content="c")
        assert await gc.search("q") == []
    finally:
        release_tenant(token)
    assert seen == {}


def test_the_memory_api_reaches_only_the_members_own_org() -> None:
    from fastapi import HTTPException
    from gateway.routes.memory import _authorize_scope

    class _User:
        role = None
        organization_id = ORG_A
        email = "a@x.test"

        def has_permission(self, perm: str) -> bool:
            return perm in {"memory:read_org", "memory:write_org"}

    _authorize_scope(f"org:{ORG_A}", _User(), write=False)
    _authorize_scope(f"org:{ORG_A}", _User(), write=True)
    with pytest.raises(HTTPException) as exc:
        _authorize_scope(f"org:{ORG_B}", _User(), write=False)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 5. R8: the probe against a real organization table
# ---------------------------------------------------------------------------

_LADDER_URL = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH", "").strip()


@pytest.mark.asyncio
@pytest.mark.skipif(not _LADDER_URL, reason="TENANT_LADDER_DATABASE_URL is unset (R8)")
async def test_the_probe_counts_real_organizations(monkeypatch) -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    text = sqlalchemy.text
    engine = create_async_engine(_LADDER_URL)
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                async def _get_db() -> AsyncSession:
                    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint")

                import acb_common.db as db

                monkeypatch.setattr(db, "get_db", _get_db)
                _flags(monkeypatch, mem0=True)

                async def _count() -> int:
                    return int((await conn.execute(
                        text("SELECT count(*) FROM organization"))).scalar_one())

                async def _add_org(n: int) -> None:
                    await conn.execute(text(
                        "INSERT INTO organization (slug, display_name) "
                        "VALUES (:s, :s)"), {"s": f"kg9-guard-{n}-{os.getpid()}"})

                n = await _count()
                while n < 1:
                    await _add_org(n)
                    n = await _count()
                if n == 1:
                    assert await tenant_guard._organizations_more_than_one() is False
                    assert await tenant_guard.memory_tenancy_ok() is True
                    await _add_org(n)
                    tenant_guard.reset_memory_tenancy()
                assert await tenant_guard._organizations_more_than_one() is True
                assert await tenant_guard.memory_tenancy_ok() is False
                with pytest.raises(MemoryTenancyRefused):
                    await tenant_guard.check_memory_tenancy()
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()
