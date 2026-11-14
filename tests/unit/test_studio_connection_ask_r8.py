"""WS-STU I8b-1a — Ask an admin, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §8.2 and §14 I8b-1a. Code: the
store ``gateway/connections/attention.py`` over ``integration_attention`` (the
migration that creates it, found by content), the clear hook
``connections/store._clear_asks`` in the six registry writers, the routes
``POST`` and ``DELETE /studio/packages/{id}/connections/ask``
(``routes/studio/connections.py``) and the purge clause ``connection_asks``
(``routes/admin/members.py``). The hermetic twin is
``tests/unit/test_studio_connection_ask.py``.

The real handlers and the store run on a phase-4 promoted scratch database, as
the non-privileged app role, so FORCE RLS binds each statement. Each call binds
its org as the auth layer does. Org A has Owen, who owns the packages, and Ben.
Org B has Zed. Each provider name of this suite holds the run's tag.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_connection_ask_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (I8b-1a).**

a. A second ask within 30 days keeps the row and its ``asked_at``.
b. A session of org B reads no row of org A and deletes none.
c. A session with no bound tenant cannot insert a row.
d. Each of the six writers clears the ask. A member row, a disabled
   ``save_headers``, an attach on a disconnected row and ``needs_reconnect``
   keep it.
e. A connect in org A clears no ask of org B.
f. A withdraw deletes the row of the asker only.
g. The route refuses a name off the draft (422), a met name (409), a name
   outside the registry (409), the bare ``mcp`` (409) and a caller who may
   connect (409). Another member of the org gets 404, and so does org B.
h. A delete of the package deletes its asks, and the key refuses a package of
   another org.
i. The purge clause deletes the asks of the member in this org only, and its
   count equals its delete.
j. An ask older than 30 days reads as not asked, a new ask makes it live
   again, and the prune deletes only an expired ask of the bound org.
k. With either flag off, the routes and the hook send no statement on the
   table.
l. ON CONFLICT with the partial index works on real Postgres.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from gateway.connections import attention
from gateway.connections import store as conn_store
from gateway.crm_sources.base import SourceCredential
from gateway.routes.admin.members import _PURGE_DELETES
from gateway.routes.studio import connections, packages
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from starlette.datastructures import QueryParams

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@i8b.test"
BEN = f"ben.{TAG}@i8b.test"
ZED = f"zed.{TAG}@i8b.test"
_EMAIL_LIKE = f"%.{TAG}@i8b.test"
_SLUG_LIKE = f"i8b{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
ADMIN = ("feature:studio", "integrations:manage")
#: The names of this run: MCP servers that no other suite writes.
TALLY = f"mcp:tally-{TAG}"
LEDGER = f"mcp:ledger-{TAG}"
TABLE = "integration_attention"
APP = "integrations"


def _manifest(names: list[str]) -> dict[str, Any]:
    return {
        "studio": 1, "id": f"i8b{TAG}-app{next(_COUNTER)}", "name": "Stock",
        "purpose": "Track the stock of a site.", "connections": names,
        "objects": [{"name": "item", "fields": [{"key": "sku", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:item.list"}],
    }


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The body stream and the query that a handler reads."""

    def __init__(self, payload: Any = None, query: dict[str, str] | None = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.query_params = QueryParams(query or {})

    async def stream(self) -> Any:
        yield self._raw


async def _bound(org: str, fn: Callable[[], Awaitable[Any]]) -> tuple[int, Any]:
    token = bind_tenant(org)
    try:
        return 200, await fn()
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _new(org: str, email: str, names: list[str]) -> str:
    status, got = await _bound(org, lambda: packages.create_package(
        request=_Request(_manifest(names)), user=_user(org, email)))
    assert status == 200, got
    return str(got["package"]["id"])


async def _ask(org: str, email: str, pid: str, name: str,
               grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    return await _bound(org, lambda: connections.ask_admin(
        package_id=pid, request=_Request({"name": name}), user=_user(org, email, grants)))


async def _withdraw(org: str, email: str, pid: str, name: str) -> tuple[int, Any]:
    return await _bound(org, lambda: connections.withdraw_ask(
        package_id=pid, request=_Request(query={"name": name}), user=_user(org, email)))


async def _read(org: str, email: str, pid: str) -> tuple[int, Any]:
    return await _bound(org, lambda: connections.package_connections(
        package_id=pid, user=_user(org, email)))


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the connection gate on for both, and a
    key for the blob. The teardown removes each package, ask and registry row
    of this run."""
    import acb_llm.key_store as key_store

    p = promoted
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_orgs", "*")
    monkeypatch.setattr(settings, "studio_connection_gate_orgs", f"{p.org_a},{p.org_b}")
    monkeypatch.setattr(settings, "acb_master_key", "r8-master-key-for-the-i8b-blob",
                        raising=False)
    monkeypatch.setattr(key_store, "_store", None)
    with p.admin_engine.begin() as c:
        for email, org in ((OWEN, p.org_a), (BEN, p.org_a), (ZED, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org)"), {"e": email, "org": org})
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        c.execute(text(f"DELETE FROM {TABLE} WHERE provider LIKE :p"), {"p": f"%{TAG}%"})
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM integration_connections WHERE provider LIKE :p"),
                  {"p": f"%{TAG}%"})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


def _asks(world: Any) -> list[tuple[str, str, str, str]]:
    """Each ask of this run, as the superuser: (org, provider, member, package)."""
    with world.p.admin_engine.connect() as c:
        return sorted(tuple(str(v) for v in r) for r in c.execute(text(
            f"SELECT organization_id, provider, member_email, package_id FROM {TABLE} "
            "WHERE provider LIKE :p"), {"p": f"%{TAG}%"}).all())


def _asked_at(world: Any, org: str, provider: str, member: str) -> datetime:
    with world.p.admin_engine.connect() as c:
        return c.execute(text(
            f"SELECT asked_at FROM {TABLE} WHERE organization_id = CAST(:o AS uuid) "
            "AND provider = :p AND member_email = :m"),
            {"o": org, "p": provider, "m": member.lower()}).scalar_one()


def _age(world: Any, provider: str, days: int) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(f"UPDATE {TABLE} SET asked_at = now() - make_interval(days => :d) "
                       "WHERE provider = :p"), {"d": days, "p": provider})


def _registry_row(world: Any, provider: str, org: str, *, member: str | None = None,
                  status: str = "connected") -> str:
    """One registry row, as the superuser. No blob, so no credential."""
    with world.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, member_email, status) VALUES (CAST(:o AS uuid), :p, :a, "
            ":k, :m, :s) RETURNING id"),
            {"o": org, "p": provider, "a": APP, "k": "member" if member else "org",
             "m": member.lower() if member else None, "s": status}).scalar_one())


def _credential() -> SourceCredential:
    return SourceCredential(access_token="r8-access", refresh_token="r8-refresh",
                            expires_at=datetime.now(UTC) + timedelta(hours=1))


async def _store_ask(org: str, member: str, provider: str, pid: str) -> bool:
    async with tenant_session(org) as db:
        return await attention.ask(db, org, member, provider, pid)


# ── a and l. One ask for each member and provider ─────────────────────────


def test_r8_a_a_second_ask_keeps_the_row_and_its_time(world) -> None:
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    assert _run(world, lambda: _ask(world.a, OWEN, pid, TALLY)) == (
        200, {"asked": True, "new": True})
    first = _asked_at(world, world.a, TALLY, OWEN)
    assert _run(world, lambda: _ask(world.a, OWEN, pid, TALLY)) == (
        200, {"asked": True, "new": False})
    assert _asked_at(world, world.a, TALLY, OWEN) == first
    assert _asks(world) == [(world.a, TALLY, OWEN, pid)]
    # The read of I8a says asked, and changes no row.
    status, got = _run(world, lambda: _read(world.a, OWEN, pid))
    assert (status, [r["asked"] for r in got["connections"]]) == (200, [True])
    assert _asked_at(world, world.a, TALLY, OWEN) == first


def test_r8_l_on_conflict_infers_the_partial_index(world) -> None:
    """Two writers of one ask on two sessions give one row, and an expired row
    takes the new time and package through the same conflict target."""
    one = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    two = _run(world, lambda: _new(world.a, OWEN, [TALLY]))

    async def both() -> list[bool]:
        return list(await asyncio.gather(_store_ask(world.a, OWEN, TALLY, one),
                                         _store_ask(world.a, OWEN, TALLY, two)))
    assert sorted(_run(world, both)) == [False, True]
    assert len(_asks(world)) == 1
    _age(world, TALLY, 31)
    assert _run(world, lambda: _store_ask(world.a, OWEN, TALLY, two)) is True
    assert _asks(world) == [(world.a, TALLY, OWEN, two)]
    assert _asked_at(world, world.a, TALLY, OWEN) > datetime.now(UTC) - timedelta(minutes=5)


# ── b and c. Tenancy ───────────────────────────────────────────────────────


def test_r8_b_a_session_of_org_b_reads_and_deletes_no_row_of_org_a(world) -> None:
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    assert _run(world, lambda: _ask(world.a, OWEN, pid, TALLY))[0] == 200

    async def as_b() -> dict[str, Any]:
        async with tenant_session(world.b) as db:
            seen = (await db.execute(text(f"SELECT count(*) FROM {TABLE}"))).scalar_one()
            asked = await attention.asked_by(db, world.a, OWEN, [TALLY])
            cleared = await attention.clear_on_connect(db, world.a, TALLY)
            withdrawn = await attention.withdraw(db, world.a, OWEN, TALLY)
            wiped = (await db.execute(text(f"DELETE FROM {TABLE}"))).rowcount
            pruned = await attention.prune(db)
        return {"seen": seen, "asked": asked, "cleared": cleared, "withdrawn": withdrawn,
                "wiped": wiped, "pruned": pruned}

    _age(world, TALLY, 40)
    assert _run(world, as_b) == {"seen": 0, "asked": frozenset(), "cleared": 0,
                                 "withdrawn": False, "wiped": 0, "pruned": 0}
    assert _run(world, lambda: attention.open_asks(world.b)) == []
    assert _asks(world) == [(world.a, TALLY, OWEN, pid)]


def test_r8_c_a_session_with_no_bound_tenant_cannot_insert(world, app_engine) -> None:  # noqa: F811
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    for sql in (
        f"INSERT INTO {TABLE} (organization_id, kind, provider, member_email, package_id) "
        "VALUES (CAST(:o AS uuid), 'ask', :p, :m, CAST(:pid AS uuid))",
        f"INSERT INTO {TABLE} (kind, provider, member_email, package_id) "
        "VALUES ('ask', :p, :m, CAST(:pid AS uuid))",
    ):
        with pytest.raises(DBAPIError), app_engine.begin() as c:
            c.execute(text(sql), {"o": world.a, "p": TALLY, "m": OWEN, "pid": pid})
    assert _asks(world) == []


# ── d. The six writers ─────────────────────────────────────────────────────


def _provider(i: int) -> str:
    return f"mcp:w{i}-{TAG}"


def test_r8_d_each_of_the_six_writers_clears_the_ask(world) -> None:
    names = [_provider(i) for i in range(6)]
    pid = _run(world, lambda: _new(world.a, OWEN, names))
    for name in names:
        assert _run(world, lambda n=name: _store_ask(world.a, OWEN, n, pid)) is True
    # The rows that the attach, the public update and the status write need.
    _registry_row(world, names[3], world.a)
    _registry_row(world, names[4], world.a, status="disconnected")
    target = _registry_row(world, names[5], world.a, status="needs_reconnect")

    async def write() -> None:
        org = world.a
        async with tenant_session(org) as db:
            await conn_store.save_credential(db, org, names[0], APP, _credential(),
                                             connected_by=BEN)
        async with tenant_session(org) as db:
            await conn_store.save_no_secret(db, org, names[1], APP, provider_meta={},
                                            connected_by=BEN)
        async with tenant_session(org) as db:
            await conn_store.save_headers(db, org, names[2], APP, {}, provider_meta={},
                                          connected_by=BEN)
        async with tenant_session(org) as db:
            got = await conn_store.attach_oauth_credential(db, org, names[3], APP,
                                                           _credential(), connected_by=BEN)
            assert got is not None and got.after.status == "connected"
        async with tenant_session(org) as db:
            locked = await conn_store.lock_row(db, org, names[4], APP)
            assert locked is not None
            conn = await conn_store.update_oauth_public(db, locked[0], {}, enabled=True)
            assert conn.status == "connected"
        async with tenant_session(org) as db:
            conn = await conn_store.set_status(db, org, target, "connected", None)
            assert conn is not None and conn.status == "connected"

    _run(world, write)
    assert _asks(world) == []


def test_r8_d_a_row_that_is_not_a_connected_org_row_keeps_the_ask(world) -> None:
    names = [_provider(10 + i) for i in range(6)]
    pid = _run(world, lambda: _new(world.a, OWEN, names))
    for name in names:
        assert _run(world, lambda n=name: _store_ask(world.a, OWEN, n, pid)) is True
    _registry_row(world, names[2], world.a)
    _registry_row(world, names[3], world.a, status="disconnected")
    _registry_row(world, names[4], world.a, status="needs_reconnect")
    connected = _registry_row(world, names[5], world.a)

    async def write() -> None:
        org = world.a
        async with tenant_session(org) as db:  # a member row
            await conn_store.save_credential(db, org, names[0], APP, _credential(),
                                             connected_by=BEN, scope_kind="member",
                                             member_email=BEN)
        async with tenant_session(org) as db:  # a disabled save_headers
            got = await conn_store.save_headers(db, org, names[1], APP, {}, provider_meta={},
                                                connected_by=BEN, enabled=False)
            assert got is not None and got.status == "disconnected"
        async with tenant_session(org) as db:  # an insert that a row refuses
            got = await conn_store.save_headers(db, org, names[2], APP, {}, provider_meta={},
                                                connected_by=BEN, only_if_absent=True)
            assert got is None
        async with tenant_session(org) as db:  # an attach on a disconnected row
            got = await conn_store.attach_oauth_credential(db, org, names[3], APP,
                                                           _credential(), connected_by=BEN)
            assert got is not None and got.after.status == "disconnected"
        async with tenant_session(org) as db:  # needs_reconnect stays
            locked = await conn_store.lock_row(db, org, names[4], APP)
            assert locked is not None
            conn = await conn_store.update_oauth_public(db, locked[0], {}, enabled=True)
            assert conn.status == "needs_reconnect"
        async with tenant_session(org) as db:  # a status that is not connected
            conn = await conn_store.set_status(db, org, connected, "needs_reconnect", "expired")
            assert conn is not None and conn.status == "needs_reconnect"

    _run(world, write)
    assert [a[1] for a in _asks(world)] == sorted(names)


# ── e. Org A never clears an ask of org B ─────────────────────────────────


def test_r8_e_a_connect_in_org_a_clears_no_ask_of_org_b(world) -> None:
    zed = _run(world, lambda: _new(world.b, ZED, [TALLY]))
    owen = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    assert _run(world, lambda: _ask(world.b, ZED, zed, TALLY))[0] == 200
    assert _run(world, lambda: _ask(world.a, OWEN, owen, TALLY))[0] == 200

    async def connect() -> None:
        async with tenant_session(world.a) as db:
            await conn_store.save_no_secret(db, world.a, TALLY, APP, provider_meta={},
                                            connected_by=BEN)
    _run(world, connect)
    assert _asks(world) == [(world.b, TALLY, ZED, zed)]


# ── f. A withdraw takes the row of the asker only ─────────────────────────


def test_r8_f_a_withdraw_deletes_the_row_of_the_asker_only(world) -> None:
    owen = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    ben = _run(world, lambda: _new(world.a, BEN, [TALLY]))
    assert _run(world, lambda: _ask(world.a, OWEN, owen, TALLY))[0] == 200
    assert _run(world, lambda: _ask(world.a, BEN, ben, TALLY))[0] == 200
    assert _run(world, lambda: _withdraw(world.a, OWEN, owen, TALLY)) == (
        200, {"asked": False, "withdrawn": True})
    assert _asks(world) == [(world.a, TALLY, BEN, ben)]
    assert _run(world, lambda: _withdraw(world.a, OWEN, owen, TALLY)) == (
        200, {"asked": False, "withdrawn": False})
    # Ben cannot withdraw through a package that he does not own.
    assert _run(world, lambda: _withdraw(world.a, BEN, owen, TALLY))[0] == 404
    assert _asks(world) == [(world.a, TALLY, BEN, ben)]


# ── g. The refusals of the route ──────────────────────────────────────────


def test_r8_g_the_route_refuses_each_name_that_no_admin_can_answer(world) -> None:
    _registry_row(world, LEDGER, world.a)
    pid = _run(world, lambda: _new(world.a, OWEN,
                                   [TALLY, LEDGER, "mailbox", "apollo", "mcp", "google_calendar"]))

    def code(name: str, grants: tuple[str, ...] = STUDIO) -> tuple[int, str]:
        status, got = _run(world, lambda: _ask(world.a, OWEN, pid, name, grants))
        return status, got["code"] if isinstance(got, dict) else got

    assert code("github") == (422, "not_in_draft")
    assert code(LEDGER) == (409, "already_met")
    assert code("mailbox") == (409, "not_checkable")
    assert code("apollo") == (409, "not_checkable")
    assert code("mcp") == (409, "not_checkable")
    assert code("google_calendar") == (409, "can_connect")
    assert code(TALLY, ADMIN) == (409, "can_connect")
    assert _run(world, lambda: _ask(world.a, BEN, pid, TALLY)) == (404, "Not found")
    assert _run(world, lambda: _ask(world.b, ZED, pid, TALLY)) == (404, "Not found")
    assert _asks(world) == []
    assert _run(world, lambda: _ask(world.a, OWEN, pid, TALLY)) == (
        200, {"asked": True, "new": True})


# ── h. The package key ────────────────────────────────────────────────────


def test_r8_h_a_package_delete_takes_its_asks_and_the_key_holds_the_org(world) -> None:
    keep = _run(world, lambda: _new(world.a, OWEN, [LEDGER]))
    gone = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    assert _run(world, lambda: _ask(world.a, OWEN, keep, LEDGER))[0] == 200
    assert _run(world, lambda: _ask(world.a, OWEN, gone, TALLY))[0] == 200
    with world.p.admin_engine.begin() as c:
        for table, col in (("studio_objects", "package_id"), ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = CAST(:i AS uuid)"), {"i": gone})
        c.execute(text("DELETE FROM apps WHERE id = CAST(:i AS uuid)"), {"i": gone})
    assert _asks(world) == [(world.a, LEDGER, OWEN, keep)]
    # A package of org A cannot carry an ask of org B, for the superuser too.
    with pytest.raises(DBAPIError), world.p.admin_engine.begin() as c:
        c.execute(text(
            f"INSERT INTO {TABLE} (organization_id, kind, provider, member_email, package_id) "
            "VALUES (CAST(:o AS uuid), 'ask', :p, :m, CAST(:pid AS uuid))"),
            {"o": world.b, "p": TALLY, "m": ZED, "pid": keep})
    with pytest.raises(DBAPIError):
        _run(world, lambda: _store_ask(world.b, ZED, TALLY, keep))
    assert _asks(world) == [(world.a, LEDGER, OWEN, keep)]


# ── m. The cap of live asks, and the move of a live ask ───────────────────


def _many(n: int) -> list[str]:
    return [f"mcp:cap{i}-{TAG}" for i in range(n)]


def test_r8_m_the_eleventh_new_ask_is_refused_and_writes_no_row(world) -> None:
    names = _many(13)
    pid = _run(world, lambda: _new(world.a, OWEN, names))
    for name in names[:10]:
        assert _run(world, lambda n=name: _ask(world.a, OWEN, pid, n)) == (
            200, {"asked": True, "new": True})
    status, got = _run(world, lambda: _ask(world.a, OWEN, pid, names[10]))
    assert (status, got["code"]) == (429, "too_many_asks")
    assert len(_asks(world)) == 10 and names[10] not in [a[1] for a in _asks(world)]
    # A repeat of an ask that exists is still 200 and not new.
    assert _run(world, lambda: _ask(world.a, OWEN, pid, names[0])) == (
        200, {"asked": True, "new": False})
    # A member of org B has an own count.
    pid_b = _run(world, lambda: _new(world.b, ZED, names))
    assert _run(world, lambda: _ask(world.b, ZED, pid_b, names[10])) == (
        200, {"asked": True, "new": True})
    # A withdraw frees a slot. An expired ask frees one too.
    assert _run(world, lambda: _withdraw(world.a, OWEN, pid, names[1]))[0] == 200
    assert _run(world, lambda: _ask(world.a, OWEN, pid, names[10]))[0] == 200
    _age(world, names[2], 31)
    assert _run(world, lambda: _ask(world.a, OWEN, pid, names[11]))[0] == 200
    assert _run(world, lambda: _ask(world.a, OWEN, pid, names[12]))[0] == 429


def test_r8_m_two_presses_at_once_never_pass_the_cap(world) -> None:
    names = _many(12)
    pid = _run(world, lambda: _new(world.a, OWEN, names))

    async def storm() -> list[Any]:
        return list(await asyncio.gather(
            *[_store_ask(world.a, OWEN, n, pid) for n in names],
            return_exceptions=True))
    got = _run(world, storm)
    assert sum(1 for g in got if g is True) == 10
    assert sum(1 for g in got if isinstance(g, attention.TooManyAsks)) == 2
    assert len(_asks(world)) == 10


def test_r8_n_a_second_draft_takes_the_ask_and_keeps_its_time(world) -> None:
    x = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    y = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    assert _run(world, lambda: _ask(world.a, OWEN, x, TALLY)) == (
        200, {"asked": True, "new": True})
    first = _asked_at(world, world.a, TALLY, OWEN)
    assert _run(world, lambda: _ask(world.a, OWEN, y, TALLY)) == (
        200, {"asked": True, "new": False})
    assert _asks(world) == [(world.a, TALLY, OWEN, y)]
    assert _asked_at(world, world.a, TALLY, OWEN) == first
    with world.p.admin_engine.begin() as c:
        for table, col in (("studio_objects", "package_id"), ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = CAST(:i AS uuid)"), {"i": x})
        c.execute(text("DELETE FROM apps WHERE id = CAST(:i AS uuid)"), {"i": x})
    assert _asks(world) == [(world.a, TALLY, OWEN, y)]
    status, got = _run(world, lambda: _read(world.a, OWEN, y))
    assert (status, [r["asked"] for r in got["connections"]]) == (200, [True])


# ── i. The purge ──────────────────────────────────────────────────────────


def test_r8_i_the_purge_clause_takes_the_asks_of_this_org_only(world) -> None:
    [rows] = [r for r in _PURGE_DELETES if r.key == "connection_asks"]
    owen = _run(world, lambda: _new(world.a, OWEN, [TALLY, LEDGER]))
    zed = _run(world, lambda: _new(world.b, ZED, [TALLY]))
    for name in (TALLY, LEDGER):
        assert _run(world, lambda n=name: _ask(world.a, OWEN, owen, n))[0] == 200
    # The same address in org B, and a broken row with no member in org A.
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            f"INSERT INTO {TABLE} (organization_id, kind, provider, member_email, package_id) "
            "VALUES (CAST(:o AS uuid), 'ask', :p, :m, CAST(:pid AS uuid))"),
            {"o": world.b, "p": TALLY, "m": OWEN, "pid": zed})
        c.execute(text(f"INSERT INTO {TABLE} (organization_id, kind, provider) "
                       "VALUES (CAST(:o AS uuid), 'broken', :p)"), {"o": world.a, "p": TALLY})

    async def purge() -> tuple[int, int]:
        async with tenant_session(world.a) as db:
            count = (await db.execute(text(rows.count_sql), {"email": OWEN})).scalar_one()
            deleted = (await db.execute(text(rows.delete_sql), {"email": OWEN})).rowcount
        return int(count), int(deleted)

    assert _run(world, purge) == (2, 2)
    with world.p.admin_engine.connect() as c:
        left = sorted((str(r[0]), r[1], r[2]) for r in c.execute(text(
            f"SELECT organization_id, kind, member_email FROM {TABLE} WHERE provider LIKE :p"),
            {"p": f"%{TAG}%"}).all())
    assert left == sorted([(world.b, "ask", OWEN), (world.a, "broken", None)])


# ── j. Expiry and prune ───────────────────────────────────────────────────


def test_r8_j_an_expired_ask_reads_not_asked_and_comes_back_with_a_new_ask(world) -> None:
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY, LEDGER]))
    assert _run(world, lambda: _ask(world.a, OWEN, pid, TALLY))[0] == 200
    assert _run(world, lambda: _ask(world.a, OWEN, pid, LEDGER))[0] == 200
    _age(world, TALLY, 31)
    status, got = _run(world, lambda: _read(world.a, OWEN, pid))
    assert status == 200, got
    assert {r["name"]: r["asked"] for r in got["connections"]} == {TALLY: False, LEDGER: True}
    assert [a.provider for a in _run(world, lambda: attention.open_asks(world.a))
            if TAG in a.provider] == [LEDGER]
    assert _run(world, lambda: _ask(world.a, OWEN, pid, TALLY)) == (
        200, {"asked": True, "new": True})
    status, got = _run(world, lambda: _read(world.a, OWEN, pid))
    assert status == 200, got
    assert {r["name"]: r["asked"] for r in got["connections"]} == {TALLY: True, LEDGER: True}


def test_r8_j_the_prune_deletes_an_expired_ask_of_the_bound_org_only(world) -> None:
    owen = _run(world, lambda: _new(world.a, OWEN, [TALLY, LEDGER]))
    zed = _run(world, lambda: _new(world.b, ZED, [TALLY]))
    for name in (TALLY, LEDGER):
        assert _run(world, lambda n=name: _ask(world.a, OWEN, owen, n))[0] == 200
    assert _run(world, lambda: _ask(world.b, ZED, zed, TALLY))[0] == 200
    _age(world, TALLY, 31)

    async def prune_a() -> None:
        async with tenant_session(world.a) as db:
            await attention.prune_hook(db, datetime.now(UTC).date())
    _run(world, prune_a)
    assert _asks(world) == sorted([(world.a, LEDGER, OWEN, owen), (world.b, TALLY, ZED, zed)])


# ── k. The flags off ──────────────────────────────────────────────────────


@pytest.fixture
def statements():
    """Each statement that any engine of this process sends."""
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


@pytest.mark.parametrize("setting", ["studio_orgs", "studio_connection_gate_orgs"])
def test_r8_k_with_a_flag_off_the_routes_and_the_hook_send_no_statement_on_the_table(
    world, monkeypatch, statements, setting: str,
) -> None:
    pid = _run(world, lambda: _new(world.a, OWEN, [TALLY]))
    assert _run(world, lambda: _ask(world.a, OWEN, pid, TALLY))[0] == 200
    monkeypatch.setattr(get_settings(), setting, world.b)
    statements.clear()
    assert _run(world, lambda: _ask(world.a, OWEN, pid, TALLY)) == (404, "Not found")
    assert _run(world, lambda: _withdraw(world.a, OWEN, pid, TALLY)) == (404, "Not found")

    async def connect() -> None:
        async with tenant_session(world.a) as db:
            await conn_store.save_no_secret(db, world.a, TALLY, APP, provider_meta={},
                                            connected_by=BEN)
    _run(world, connect)
    # A copy before the checks, because the checks read the table too.
    on_table = [s for s in statements if TABLE in s]
    if setting == "studio_connection_gate_orgs":
        assert on_table == []
        assert _asks(world) == [(world.a, TALLY, OWEN, pid)]
    else:
        # STUDIO_ORGS off closes the routes. The hook reads the gate alone.
        assert [s.split()[0] for s in on_table] == ["DELETE"]
        assert _asks(world) == []
