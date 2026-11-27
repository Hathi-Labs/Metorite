"""WS-PKG PKG-13 — the field-value store on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §5.1, §8.3 and the PKG-13 row
of §10. Code: ``gateway/package_host/field_values.py``. Migration: the file
that creates ``package_field_value`` (366 at build). The hermetic fence of
the one writer is ``tests/unit/test_package_field_values.py``.

Each call runs as the app role, through ``tenant_session``, so FORCE RLS
binds each statement. Each check of rows reads as the superuser, which RLS
does not bind. The ``promoted`` fixture builds the full ladder and the four
generated phases from an empty database, so it also applies the migration.
Org A has three Studio packages with version 1. Org B has one.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_package_field_values_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-13).**

a. The table has ENABLE and FORCE RLS and one policy with USING and WITH
   CHECK.
b. Org B reads and writes none of the values of org A, through the store and
   through raw SQL.
c. An unbound session reads and writes none.
d. A value cannot point at an install of another org.
e. A value of the wrong type raises ``FieldError`` with the field key and
   writes nothing. An undeclared key raises ``FieldNotDeclared``.
f. ``remove_install`` deletes each value of the install.
g. A pause keeps the values. ``get_values`` hides them and ``set_value``
   refuses. A resume shows them again.
h. ``set_value`` after a removal raises ``InstallNotFound``. A value None
   deletes the row. A read leaves out a value whose stored type differs from
   the current declaration.
i. A write and a removal at once raise no foreign key error, in either order.
j. The viewer filter (PKG-14a). A read answers only the installs of the set,
   and a write to an install outside the set writes nothing. An install past
   its ``ends_at`` and an install of an archived app read and take no value,
   also when the set names them. Org B reads none of org A, also when the set
   names the install of org A.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import TenantUnbound, get_session_factory, tenant_session
from acb_common.studio_fields import FieldError
from gateway.package_host import field_values as fv
from gateway.package_host import store
from gateway.package_host.field_values import FieldNotDeclared
from gateway.package_host.store import (
    Contribution,
    InstallError,
    InstallNotFound,
    StateRefused,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@pkg13.test"
_SLUG_LIKE = f"pkg13-{TAG}-%"
_A_APPS = ("a1", "a2", "a3")
DEAL = "crm.deal"


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _key(name: str) -> str:
    """A field key that no other test of this run claims."""
    return f"{name}_{TAG}"


@pytest.fixture
def world(promoted):  # noqa: F811
    """Org A: three Studio packages with version 1. Org B: one. Teardown
    deletes the apps, and the installs, the claims and the values go by
    cascade."""
    p = promoted
    apps: dict[str, str] = {}

    def app(c: Any, name: str, org: str) -> None:
        man = json.dumps({"studio": 1, "id": f"r-{name}-{TAG}", "name": "P", "purpose": "T"})
        apps[name] = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
            "format, status, manifest) VALUES (:s, 'pkg13', :o, '', :org, 'manifest', "
            "'live', CAST(:m AS jsonb)) RETURNING id"),
            {"s": f"pkg13-{TAG}-{name}", "o": ANA, "org": org, "m": man}).scalar_one())
        c.execute(text(
            "INSERT INTO app_versions (app_id, version, manifest, bundle_html, "
            "bundle_sha256, published_by, organization_id) VALUES "
            "(CAST(:i AS uuid), 1, CAST(:m AS jsonb), '', 'sha', :by, CAST(:o AS uuid))"),
            {"i": apps[name], "m": man, "by": ANA, "o": org})

    with p.admin_engine.begin() as c:
        for name in _A_APPS:
            app(c, name, p.org_a)
        app(c, "b1", p.org_b)
    yield SimpleNamespace(p=p, apps=apps)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug LIKE :s"), {"s": _SLUG_LIKE})


# ── Helpers ────────────────────────────────────────────────────────────────


def _org(world: Any, tag: str) -> str:
    return world.p.org_a if tag == "a" else world.p.org_b


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the engine of the app role."""

    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()

    return asyncio.run(go())


def _field(field_key: str, payload: dict[str, Any], kind: str = DEAL) -> Contribution:
    """A field declaration: a claim at the point ``fields``."""
    return Contribution(point="fields", key=f"{kind}:{field_key}", payload=payload)


def _install(world: Any, app: str, claims: list[Contribution], *, org: str = "a") -> str:
    """One org install through the store, as the app role."""

    async def go() -> str:
        async with tenant_session(_org(world, org)) as db:
            got = await store.install(
                db, app_id=world.apps[app], version=1, scope_subject="org",
                scope_kind="org", installed_by=ANA, ends_at=None, contributions=claims)
            return got.id

    return _go(world, go)


def _set(world: Any, install: str, field_key: str, value: Any, *, org: str = "a",
         record: str, kind: str = DEAL, ids: Any = None) -> Any:
    """One ``set_value`` as the app role. Answers the result or the refusal.
    The set of the viewer is *ids*, or the install alone."""

    async def go() -> Any:
        try:
            async with tenant_session(_org(world, org)) as db:
                return await fv.set_value(db, install_id=install, record_kind=kind,
                                          record_id=record, field_key=field_key, value=value,
                                          install_ids=[install] if ids is None else ids)
        except (InstallError, FieldError) as exc:
            return exc

    return _go(world, go)


def _all_installs(world: Any) -> list[str]:
    """Each install of both orgs, read as the superuser."""
    return [str(r[0]) for r in _admin(world, "SELECT id FROM package_install")]


def _get(world: Any, record: str, *, org: str = "a", kind: str = DEAL,
         ids: Any = None) -> list[fv.FieldValue]:
    """One ``get_values`` as the app role. The set is *ids*, or each install
    of both orgs, so only the other checks of the store decide."""
    want = _all_installs(world) if ids is None else ids

    async def go() -> list[fv.FieldValue]:
        async with tenant_session(_org(world, org)) as db:
            return await fv.get_values(db, record_kind=kind, record_id=record, install_ids=want)

    return _go(world, go)


def _store_call(world: Any, fn: Any, install: str) -> Any:
    async def go() -> Any:
        async with tenant_session(world.p.org_a) as db:
            return await fn(db, install_id=install)

    return _go(world, go)


def _admin(world: Any, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    """Rows read as the superuser, which RLS does not bind."""
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params or {}).all())


def _values_of(world: Any, install: str) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in _admin(
        world,
        "SELECT record_id::text, field_key, field_type, value::text, organization_id::text "
        "FROM package_field_value WHERE install_id = CAST(:i AS uuid) "
        "ORDER BY record_id, field_key",
        {"i": install})]


def _seeded(world: Any, name: str, payload: dict[str, Any] | None = None) -> SimpleNamespace:
    """An org install of app a1 with one declared field and one value."""
    key = _key(name)
    install = _install(world, "a1", [_field(key, payload or {"type": "date"})])
    record = str(uuid.uuid4())
    assert _set(world, install, key, "2027-01-31", record=record) == "2027-01-31"
    return SimpleNamespace(install=install, key=key, record=record)


# ── a. The catalog ─────────────────────────────────────────────────────────


def test_r8_the_table_has_enable_force_and_one_policy(world) -> None:
    rows = _admin(world, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                         "WHERE relname = 'package_field_value'")
    policies = _admin(world, "SELECT policyname, qual IS NOT NULL, with_check IS NOT NULL "
                             "FROM pg_policies WHERE tablename = 'package_field_value'")
    assert [tuple(r) for r in rows] == [(True, True)]
    assert [tuple(r) for r in policies] == [
        ("package_field_value_tenant_isolation", True, True)]


def test_r8_a_value_is_written_and_read_back(world) -> None:
    s = _seeded(world, "w1")
    got = _get(world, s.record)
    assert [(v.install_id, v.app_id, v.field_key, v.field_type, v.value) for v in got] == [
        (s.install, world.apps["a1"], s.key, "date", "2027-01-31")]
    # A second write updates the one row.
    assert _set(world, s.install, s.key, "2028-02-29", record=s.record) == "2028-02-29"
    rows = _values_of(world, s.install)
    assert [(r[1], r[3], r[4]) for r in rows] == [(s.key, '"2028-02-29"', world.p.org_a)]


# ── b. Org B ───────────────────────────────────────────────────────────────


def test_r8_org_b_reads_and_writes_none_of_org_a(world) -> None:
    s = _seeded(world, "b1")
    before = _values_of(world, s.install)
    assert _get(world, s.record, org="b") == []
    got = _set(world, s.install, s.key, "2030-01-01", org="b", record=s.record)
    assert isinstance(got, InstallNotFound), got
    got = _set(world, s.install, s.key, None, org="b", record=s.record)
    assert isinstance(got, InstallNotFound), got

    async def raw() -> tuple[int, int, int, int]:
        async with tenant_session(world.p.org_b) as db:
            read = len((await db.execute(text(
                "SELECT 1 FROM package_field_value WHERE install_id = CAST(:i AS uuid)"),
                {"i": s.install})).all())
            upd = (await db.execute(text(
                "UPDATE package_field_value SET value = '\"x\"' "
                "WHERE install_id = CAST(:i AS uuid)"), {"i": s.install})).rowcount
            dele = (await db.execute(text(
                "DELETE FROM package_field_value WHERE install_id = CAST(:i AS uuid)"),
                {"i": s.install})).rowcount
            gone = await fv.delete_values(db, record_kind=DEAL, record_id=s.record)
            return read, upd, dele, gone

    assert _go(world, raw) == (0, 0, 0, 0)
    assert _values_of(world, s.install) == before


# ── c. An unbound session ──────────────────────────────────────────────────


def test_r8_an_unbound_session_reads_and_writes_none(world) -> None:
    s = _seeded(world, "c1")
    before = _values_of(world, s.install)

    async def go() -> SimpleNamespace:
        out = SimpleNamespace(raised=[], read=None, write=None)
        calls = (
            lambda db: fv.set_value(db, install_id=s.install, record_kind=DEAL,
                                    record_id=s.record, field_key=s.key, value="2030-01-01",
                                    install_ids=[s.install]),
            lambda db: fv.get_values(db, record_kind=DEAL, record_id=s.record,
                                     install_ids=[s.install]),
            lambda db: fv.delete_values(db, record_kind=DEAL, record_id=s.record),
        )
        for call in calls:
            session = get_session_factory()()
            try:
                await call(session)
                out.raised.append(None)
            except TenantUnbound as exc:
                out.raised.append(exc)
            finally:
                await session.rollback()
                await session.close()
        session = get_session_factory()()
        try:
            out.read = len((await session.execute(text(
                "SELECT 1 FROM package_field_value"))).all())
            try:
                await session.execute(text(
                    "INSERT INTO package_field_value (install_id, record_kind, record_id, "
                    "field_key, field_type, value) VALUES (CAST(:i AS uuid), 'task', "
                    "gen_random_uuid(), 'k', 'text', '\"x\"')"), {"i": s.install})
                out.write = "written"
            except DBAPIError as exc:
                out.write = exc
        finally:
            await session.rollback()
            await session.close()
        return out

    out = _go(world, go)
    assert all(isinstance(e, TenantUnbound) for e in out.raised), out.raised
    assert out.read == 0
    assert isinstance(out.write, DBAPIError), out.write
    assert _values_of(world, s.install) == before


# ── d. An install of another org ───────────────────────────────────────────


def test_r8_a_value_cannot_point_at_an_install_of_another_org(world) -> None:
    s = _seeded(world, "d1")

    async def stamped_b() -> None:
        # The org of the row is org B (the DEFAULT), and the install is of
        # org A. The composite foreign key refuses it, because a foreign key
        # check skips RLS.
        async with tenant_session(world.p.org_b) as db:
            await db.execute(text(
                "INSERT INTO package_field_value (install_id, record_kind, record_id, "
                "field_key, field_type, value) VALUES (CAST(:i AS uuid), :k, "
                "CAST(:r AS uuid), :f, 'date', '\"2030-01-01\"')"),
                {"i": s.install, "k": DEAL, "r": str(uuid.uuid4()), "f": s.key})

    async def stamped_a() -> None:
        # WITH CHECK refuses a row that names org A from a session of org B.
        async with tenant_session(world.p.org_b) as db:
            await db.execute(text(
                "INSERT INTO package_field_value (organization_id, install_id, record_kind, "
                "record_id, field_key, field_type, value) VALUES (CAST(:o AS uuid), "
                "CAST(:i AS uuid), :k, CAST(:r AS uuid), :f, 'date', '\"2030-01-01\"')"),
                {"o": world.p.org_a, "i": s.install, "k": DEAL, "r": str(uuid.uuid4()),
                 "f": s.key})

    for fn, needle in ((stamped_b, "package_field_value_install_fk"),
                       (stamped_a, "row-level security")):
        with pytest.raises(DBAPIError) as exc:
            _go(world, fn)
        assert needle in str(exc.value), str(exc.value)
    assert len(_values_of(world, s.install)) == 1


# ── e. The type check and the declaration ──────────────────────────────────


def test_r8_a_wrong_type_raises_field_error_and_writes_nothing(world) -> None:
    key = _key("e1")
    install = _install(world, "a1", [_field(key, {"type": "number"})])
    record = str(uuid.uuid4())
    got = _set(world, install, key, "twelve", record=record)
    assert isinstance(got, FieldError), got
    assert got.field == key and f"`{key}`" in str(got)
    assert _values_of(world, install) == []


def test_r8_an_undeclared_key_raises_field_not_declared(world) -> None:
    key = _key("e2")
    install = _install(world, "a1", [_field(key, {"type": "text"})])
    record = str(uuid.uuid4())
    for kind, field_key in ((DEAL, _key("e3")), ("task", key)):
        got = _set(world, install, field_key, "x", record=record, kind=kind)
        assert isinstance(got, FieldNotDeclared), (kind, got)
        assert f"`{field_key}`" in str(got)
    assert _values_of(world, install) == []


def test_r8_a_declaration_of_a_refused_type_takes_no_value(world) -> None:
    key = _key("e4")
    install = _install(world, "a1", [_field(key, {"type": "file"})])
    got = _set(world, install, key, {"id": str(uuid.uuid4())}, record=str(uuid.uuid4()))
    assert isinstance(got, FieldError) and got.field == key, got
    assert _values_of(world, install) == []


# ── f. A removal deletes the values ────────────────────────────────────────


def test_r8_a_removal_deletes_each_value_of_the_install(world) -> None:
    s = _seeded(world, "f1")
    other = str(uuid.uuid4())
    assert _set(world, s.install, s.key, "2027-05-05", record=other) == "2027-05-05"
    assert len(_values_of(world, s.install)) == 2
    assert _store_call(world, store.remove_install, s.install) is True
    assert _values_of(world, s.install) == []
    assert _get(world, s.record) == []
    # h. A write after the removal is InstallNotFound, never a foreign key error.
    got = _set(world, s.install, s.key, "2027-06-06", record=s.record)
    assert isinstance(got, InstallNotFound), got


# ── g. A pause keeps the values and hides them ─────────────────────────────


def test_r8_a_pause_keeps_and_hides_the_values_and_a_resume_shows_them(world) -> None:
    s = _seeded(world, "g1")
    before = _values_of(world, s.install)
    assert _store_call(world, store.pause_install, s.install) is True
    assert _values_of(world, s.install) == before
    assert _get(world, s.record) == []
    for value in ("2030-01-01", None):
        got = _set(world, s.install, s.key, value, record=s.record)
        assert isinstance(got, StateRefused), (value, got)
    assert _values_of(world, s.install) == before
    assert _store_call(world, store.resume_install, s.install) is True
    assert [v.value for v in _get(world, s.record)] == ["2027-01-31"]


# ── h. None deletes, and a changed type hides ──────────────────────────────


def test_r8_none_deletes_the_row(world) -> None:
    s = _seeded(world, "h1")
    assert _set(world, s.install, s.key, None, record=s.record) is None
    assert _values_of(world, s.install) == []
    # A second deletion finds nothing and is no error.
    assert _set(world, s.install, s.key, None, record=s.record) is None


def test_r8_a_value_of_another_type_than_the_declaration_is_left_out(world) -> None:
    s = _seeded(world, "h2")
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE package_contribution SET payload = '{\"type\": \"text\"}' "
            "WHERE install_id = CAST(:i AS uuid) AND point = 'fields'"), {"i": s.install})
    assert len(_values_of(world, s.install)) == 1
    assert _get(world, s.record) == []


def test_r8_delete_values_deletes_each_value_of_one_record(world) -> None:
    s = _seeded(world, "h3")
    key2 = _key("h4")
    install2 = _install(world, "a2", [_field(key2, {"type": "checkbox"})])
    assert _set(world, install2, key2, True, record=s.record) is True
    keep = str(uuid.uuid4())
    assert _set(world, install2, key2, False, record=keep) is False
    assert len(_get(world, s.record)) == 2

    async def go() -> int:
        async with tenant_session(world.p.org_a) as db:
            return await fv.delete_values(db, record_kind=DEAL, record_id=s.record)

    assert _go(world, go) == 2
    assert _get(world, s.record) == []
    assert [v.value for v in _get(world, keep)] == [False]


# ── i. A write and a removal at once ───────────────────────────────────────


def _waits_on_a_lock(world: Any, pid: int, task: asyncio.Task[Any]) -> str:
    """``waited`` when the backend *pid* waits on a lock, ``done`` when the
    task ended first. Polls the server, so no sleep sets the order."""
    rows = _admin(world, "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid",
                  {"pid": pid})
    if rows and rows[0][0] == "Lock":
        return "waited"
    return "done" if task.done() else ""


async def _hold_then_race(world: Any, hold: Any, race: Any) -> SimpleNamespace:
    """``hold(db)`` runs and keeps its transaction open. ``race(db)`` starts in a
    second session. The test sees the wait of the second on the server before
    the first commits. Each answer is the result or the exception."""
    done, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    pid: dict[str, int] = {}
    a = world.p.org_a

    async def first() -> Any:
        async with tenant_session(a) as db:
            out = await hold(db)
            done.set()
            await release.wait()
            return out

    async def second() -> Any:
        try:
            async with tenant_session(a) as db:
                pid["pid"] = int((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                started.set()
                return await race(db)
        except Exception as exc:  # the test reads the type
            return exc

    one = asyncio.create_task(first())
    await asyncio.wait_for(done.wait(), 30)
    two = asyncio.create_task(second())
    await asyncio.wait_for(started.wait(), 30)
    state = ""
    end = time.monotonic() + 30
    while not state and time.monotonic() < end:
        state = await asyncio.to_thread(_waits_on_a_lock, world, pid["pid"], two)
        if not state:
            await asyncio.sleep(0.02)
    release.set()
    return SimpleNamespace(state=state, one=await one, two=await two)


def test_r8_a_removal_waits_for_a_write_and_then_deletes_its_value(world) -> None:
    """The write holds FOR KEY SHARE on the install row. The removal must wait
    for it, and then the cascade deletes the new value."""
    s = _seeded(world, "i1")
    record = str(uuid.uuid4())

    async def write(db: Any) -> Any:
        return await fv.set_value(db, install_id=s.install, record_kind=DEAL,
                                  record_id=record, field_key=s.key, value="2029-09-09",
                                  install_ids=[s.install])

    async def remove(db: Any) -> Any:
        return await store.remove_install(db, install_id=s.install)

    out = _go(world, lambda: _hold_then_race(world, write, remove))
    assert out.state == "waited", "the removal did not wait for the write"
    assert out.one == "2029-09-09"
    assert out.two is True, out.two
    assert _values_of(world, s.install) == []


def test_r8_a_write_after_an_open_removal_is_install_not_found(world) -> None:
    """The removal deletes the install row and keeps its transaction open. The
    write waits on the row lock, then finds no row: ``InstallNotFound``, never
    ``IntegrityError`` (23503)."""
    s = _seeded(world, "i2")
    record = str(uuid.uuid4())

    async def remove(db: Any) -> Any:
        return await store.remove_install(db, install_id=s.install)

    async def write(db: Any) -> Any:
        return await fv.set_value(db, install_id=s.install, record_kind=DEAL,
                                  record_id=record, field_key=s.key, value="2029-09-09",
                                  install_ids=[s.install])

    out = _go(world, lambda: _hold_then_race(world, remove, write))
    assert out.state == "waited", "the write did not wait for the removal"
    assert out.one is True
    assert not isinstance(out.two, IntegrityError), out.two
    assert isinstance(out.two, InstallNotFound), out.two
    assert _values_of(world, s.install) == []


# ── j. The viewer filter (PKG-14a) ─────────────────────────────────────────


def test_r8_a_read_answers_only_the_installs_of_the_set(world) -> None:
    s = _seeded(world, "j1")
    key2 = _key("j2")
    other = _install(world, "a2", [_field(key2, {"type": "checkbox"})])
    assert _set(world, other, key2, True, record=s.record) is True
    both = {(v.install_id, v.field_key) for v in _get(world, s.record)}
    assert both == {(s.install, s.key), (other, key2)}
    assert [(v.install_id, v.field_key) for v in _get(world, s.record, ids=[other])] == [
        (other, key2)]
    assert _get(world, s.record, ids=[]) == []
    assert _get(world, s.record, ids=[str(uuid.uuid4())]) == []


def test_r8_a_write_to_an_install_outside_the_set_writes_nothing(world) -> None:
    s = _seeded(world, "j3")
    before = _values_of(world, s.install)
    for value in ("2030-01-01", None):
        for ids in ([], [str(uuid.uuid4())]):
            got = _set(world, s.install, s.key, value, record=s.record, ids=ids)
            assert isinstance(got, InstallNotFound), (value, ids, got)
            assert got.status == 404
    assert _set(world, s.install, s.key, "2030-01-01", record=str(uuid.uuid4()),
                ids=[]).code == "install_not_found"
    assert _values_of(world, s.install) == before


def test_r8_an_ended_install_and_an_archived_app_read_and_take_no_value(world) -> None:
    """Known gap 2 of §8.3: the store checked the state only. The set names
    each install here, so only the predicates of the resolve decide."""
    ended = _seeded(world, "j4")
    gone = SimpleNamespace(key=_key("j5"), record=str(uuid.uuid4()))
    gone.install = _install(world, "a3", [_field(gone.key, {"type": "date"})])
    assert _set(world, gone.install, gone.key, "2027-01-31", record=gone.record) == "2027-01-31"
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE package_install SET ends_at = now() - interval '1 minute' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": ended.install})
        c.execute(text("UPDATE apps SET status = 'archived' WHERE id = CAST(:a AS uuid)"),
                  {"a": world.apps["a3"]})
    for s in (ended, gone):
        before = _values_of(world, s.install)
        assert len(before) == 1
        assert _get(world, s.record, ids=[s.install]) == []
        for value in ("2030-01-01", None):
            got = _set(world, s.install, s.key, value, record=s.record)
            assert isinstance(got, InstallNotFound), (s.key, value, got)
        assert _set(world, s.install, s.key, "2030-01-01",
                    record=str(uuid.uuid4())).code == "install_not_found"
        assert _values_of(world, s.install) == before
    # A later end date shows the value again: the predicate is the clock.
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE package_install SET ends_at = now() + interval '1 day' "
                       "WHERE id = CAST(:i AS uuid)"), {"i": ended.install})
    assert [v.value for v in _get(world, ended.record, ids=[ended.install])] == ["2027-01-31"]


def test_r8_org_b_reads_none_of_org_a_also_when_the_set_names_it(world) -> None:
    s = _seeded(world, "j6")
    assert _get(world, s.record, org="b", ids=[s.install]) == []
    got = _set(world, s.install, s.key, "2030-01-01", org="b", record=s.record)
    assert isinstance(got, InstallNotFound), got
    assert [v.value for v in _get(world, s.record, ids=[s.install])] == ["2027-01-31"]
