"""WS-STU A3 — the package store of AI Studio on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.6 and the §14 A3 row. Code:
``gateway/studio/packages_store.py``. Migration: the file that adds
``apps.format`` (322 at build). The hermetic twin is
``tests/unit/test_studio_packages_store.py``.

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_packages_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (A3).**

a. The migration adds `format` (nullable, DEFAULT 'code'), two CHECKs and a
   trigger, and a second run of the file changes nothing.
b. An INSERT of the old shape, with no `format`, reads as `code`.
c. Each CHECK refuses its case: a bad `format`, a package with no object, no
   number `studio` or more than 256 KiB. A package of exactly the store's
   limit passes the CHECK.
d. The format of an app never changes, also as the superuser.
e. The store writes a private draft package. A manifest that the store
   refuses writes nothing, and the session stays usable.
f. Org B lists and reads none of org A's packages, and an unbound session
   reads none. A member reads only their own.
g. Two orgs may hold one manifest id. A slug clash with another org leaks
   nothing, and the store takes a new id.
h. `list_apps`, `load_app`, the pins and `app_tools` skip a package, and
   still return a code app.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. The migration: drop the CHECK ``apps_manifest_package``.
  ``test_r8_the_checks_refuse_a_bad_package`` and
  ``test_r8_a_package_at_the_store_limit_passes_the_check`` go red.
* M2. The migration: drop the CHECK ``apps_format``.
  ``test_r8_the_checks_refuse_a_bad_package[bad_format]`` goes red.
* M3. The migration: ``DEFAULT 'manifest'``.
  ``test_r8_an_insert_of_the_old_shape_reads_as_code`` goes red.
* M4. ``packages_store.check_manifest``: drop the ``validate_manifest``
  call. ``test_r8_an_invalid_manifest_writes_nothing`` goes red.
* M5. Drop the guard ``format IS DISTINCT FROM 'manifest'`` in one reader at
  a time: ``load_app``, ``list_apps``, ``list_pinned_apps`` or
  ``_granted_live_apps``. Its own ``test_r8_*_skips_a_package`` goes red.
* M6. ``packages_store.create_package``: use the plain manifest id as the
  slug. ``test_r8_two_orgs_may_hold_one_manifest_id`` and
  ``test_r8_a_slug_clash_with_another_org_leaks_nothing`` go red.
* M7. The migration: drop the trigger ``apps_format_fixed``.
  ``test_r8_the_format_of_an_app_never_changes`` goes red.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole
from acb_common.db import (
    async_database_url,
    bind_tenant,
    clear_tenant,
    release_tenant,
    tenant_session,
)
from fastapi import HTTPException
from gateway.routes.apps import _common, lifecycle, pins
from gateway.studio import packages_store as store
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_studio_packages_store import MANIFEST, manifest_of_size

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "COMMENT ON COLUMN apps.format IS" in p.read_text(encoding="utf-8"))

TAG = uuid.uuid4().hex[:8]
#: The two members of this suite. The tag lets teardown find their rows.
ANA = f"ana.{TAG}@stua3.test"
BEN = f"ben.{TAG}@stua3.test"
_LIKE = f"%.{TAG}@stua3.test"
#: The manifest id of this run, so a slug of an earlier run never clashes.
MID = f"quotes-{TAG}"


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _manifest(mid: str = MID, **patch: Any) -> dict[str, Any]:
    d = copy.deepcopy(MANIFEST)
    d["id"] = mid
    d.update(patch)
    return d


@pytest.fixture
def world(promoted):  # noqa: F811
    p = promoted
    yield SimpleNamespace(p=p)
    # The versions, grants and pins go by the cascade of their foreign keys.
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _create(org: str, member: str = ANA, mid: str = MID) -> store.Package:
    async with tenant_session(org) as db:
        return await store.create_package(db, member, _manifest(mid))


def _count(world: Any, where: str = "format = 'manifest'") -> int:
    """The rows of this suite in ``apps``, read as the superuser."""
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            f"SELECT count(*) FROM apps WHERE owner_email LIKE :t AND {where}"),
            {"t": _LIKE}).scalar_one())


def _refused(world: Any, org: str, sql: str, params: dict[str, Any]) -> str:
    """Run *sql* as the app role in *org*. Answers the error text."""
    async def go() -> None:
        async with tenant_session(org) as db:
            await db.execute(text(sql), params)

    with pytest.raises(DBAPIError) as exc:
        _go(world, go)
    return str(exc.value)


#: An INSERT of the App Workshop shape (lifecycle.py, the create and the
#: fork). It names no `format`.
_OLD_INSERT = (
    "INSERT INTO apps (slug, name, icon, description, owner_email, manifest, "
    "workspace_path) VALUES (:slug, 'Old app', '', '', :m, CAST(:manifest AS jsonb), "
    ":wp) RETURNING id::text, format")

_RAW_PACKAGE = (
    "INSERT INTO apps (slug, name, owner_email, manifest, workspace_path, format) "
    "VALUES (:slug, 'Raw', :m, CAST(:manifest AS jsonb), '', :format)")


def _old_app(world: Any, org: str, slug: str, manifest: str = "{}") -> str:
    """One code app of ANA, written as the App Workshop writes it."""
    async def go() -> str:
        async with tenant_session(org) as db:
            row = (await db.execute(text(_OLD_INSERT), {
                "slug": slug, "m": ANA, "manifest": manifest,
                "wp": f"/tmp/{slug}"})).one()
        assert row[1] == "code"
        return str(row[0])
    return _go(world, go)


# ── a. The migration ───────────────────────────────────────────────────────


def test_the_migration_holds_the_column_the_checks_and_the_trigger() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "SET LOCAL lock_timeout" in sql
    assert "CONSTRAINT apps_format CHECK" in sql
    assert "format IS NULL OR format IN ('code', 'manifest')" in sql
    assert "CONSTRAINT apps_manifest_package CHECK" in sql
    assert "jsonb_typeof(manifest -> 'studio') IS NOT DISTINCT FROM 'number'" in sql
    assert f"octet_length(manifest::text) <= {store.MAX_MANIFEST_BYTES}" in sql
    assert "BEFORE UPDATE OF format ON apps" in sql
    # Expand only: no new table, no rename, no drop of a column.
    assert "CREATE TABLE" not in sql
    assert "RENAME" not in sql and "DROP COLUMN" not in sql
    assert "gtd_" not in sql


def test_r8_the_column_is_nullable_with_default_code(world) -> None:
    with world.p.admin_engine.connect() as c:
        nullable, default = c.execute(text(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'apps' AND column_name = 'format'")).one()
        cons = c.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'apps'::regclass "
            "AND conname IN ('apps_format', 'apps_manifest_package') ORDER BY 1")).scalars().all()
        trig = c.execute(text(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'apps'::regclass "
            "AND NOT tgisinternal AND tgname = 'apps_format_fixed'")).scalars().all()
        rls = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'apps'")).one()
    assert nullable == "YES" and default.startswith("'code'")
    assert cons == ["apps_format", "apps_manifest_package"]
    assert trig == ["apps_format_fixed"]
    assert tuple(rls) == (True, True)


def test_r8_a_second_run_of_the_file_changes_nothing(world) -> None:
    """The runner applies every file on every deploy, so the file must be
    idempotent: no error, and one copy of each CHECK and of the trigger."""
    with world.p.admin_engine.begin() as conn, conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(_MIGRATION.read_text(encoding="utf-8"))
    with world.p.admin_engine.connect() as c:
        cons = c.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = 'apps'::regclass "
            "AND conname IN ('apps_format', 'apps_manifest_package')")).scalar_one()
        trig = c.execute(text(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'apps'::regclass "
            "AND tgname = 'apps_format_fixed'")).scalar_one()
    assert (cons, trig) == (2, 1)


# ── b. An old row reads as code ────────────────────────────────────────────


def test_r8_an_insert_of_the_old_shape_reads_as_code(world) -> None:
    a = world.p.org_a
    app_id = _old_app(world, a, f"old-{TAG}")
    with world.p.admin_engine.connect() as c:
        fmt = c.execute(text("SELECT format FROM apps WHERE id = CAST(:i AS uuid)"),
                        {"i": app_id}).scalar_one()
        seeded = c.execute(text(
            "SELECT DISTINCT format FROM apps WHERE slug LIKE 'h3rls-app-%'")).scalars().all()
    assert fmt == "code"
    # The rows that the fixture seeds have the old shape too.
    assert seeded == ["code"]


# ── c. The CHECKs ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("fmt", "manifest", "constraint"), [
    pytest.param("html", "{}", "apps_format", id="bad_format"),
    pytest.param("", "{}", "apps_format", id="empty_format"),
    pytest.param("manifest", "{}", "apps_manifest_package", id="no_studio"),
    pytest.param("manifest", '{"studio": "1"}', "apps_manifest_package", id="studio_text"),
    pytest.param("manifest", '{"studio": null}', "apps_manifest_package", id="studio_null"),
    pytest.param("manifest", "[1]", "apps_manifest_package", id="not_object"),
    pytest.param("manifest", json.dumps({"studio": 1, "x": "y" * 262144}),
                 "apps_manifest_package", id="too_big"),
])
def test_r8_the_checks_refuse_a_bad_package(world, fmt: str, manifest: str,
                                            constraint: str) -> None:
    err = _refused(world, world.p.org_a, _RAW_PACKAGE, {
        "slug": f"raw-{TAG}", "m": ANA, "manifest": manifest, "format": fmt})
    assert constraint in err
    assert _count(world, "true") == 0


def test_r8_a_code_app_and_a_null_format_pass_with_any_manifest(world) -> None:
    async def go() -> None:
        async with tenant_session(world.p.org_a) as db:
            for i, (fmt, manifest) in enumerate([("code", "[1]"), (None, '"x"')]):
                await db.execute(text(_RAW_PACKAGE), {
                    "slug": f"raw-{i}-{TAG}", "m": ANA, "manifest": manifest, "format": fmt})
    _go(world, go)
    assert _count(world, "format IS DISTINCT FROM 'manifest'") == 2


def test_r8_a_package_at_the_store_limit_passes_the_check(world) -> None:
    """The store measures its JSON text, and the CHECK measures the jsonb
    text. A manifest of exactly MAX_MANIFEST_BYTES passes both, and is stored
    at that length. One byte more, and the CHECK refuses it on its own."""
    a = world.p.org_a
    at = manifest_of_size(store.MAX_MANIFEST_BYTES, base=_manifest())
    assert len(store.check_manifest(at).encode("utf-8")) == store.MAX_MANIFEST_BYTES

    async def go() -> store.Package:
        async with tenant_session(a) as db:
            return await store.create_package(db, ANA, at)

    pkg = _go(world, go)
    assert pkg.manifest == at
    with world.p.admin_engine.connect() as c:
        stored = c.execute(text(
            "SELECT octet_length(manifest::text) FROM apps WHERE id = CAST(:i AS uuid)"),
            {"i": pkg.id}).scalar_one()
    assert stored == store.MAX_MANIFEST_BYTES
    big = copy.deepcopy(at)
    big["views"][-1]["text"] += "x"
    err = _refused(world, a, _RAW_PACKAGE, {
        "slug": f"big-{TAG}", "m": ANA, "manifest": json.dumps(big, ensure_ascii=False),
        "format": "manifest"})
    assert "apps_manifest_package" in err
    assert _count(world) == 1


# ── d. The format never changes ────────────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
def test_r8_the_format_of_an_app_never_changes(world, role: str) -> None:
    """A code app whose manifest passes the CHECK still cannot become a
    package, so only the trigger refuses it. A package cannot become code.
    NULL and `code` count as one value."""
    a = world.p.org_a
    code_id = _old_app(world, a, f"fmt-{TAG}", manifest='{"studio": 1}')
    pkg = _go(world, lambda: _create(a))
    upd = "UPDATE apps SET format = :f WHERE id = CAST(:i AS uuid)"

    def refused(fmt: str, app_id: str) -> str:
        if role == "app":
            return _refused(world, a, upd, {"f": fmt, "i": app_id})
        with pytest.raises(DBAPIError) as exc, world.p.admin_engine.begin() as c:
            c.execute(text(upd), {"f": fmt, "i": app_id})
        return str(exc.value)

    assert "the format of an app cannot change" in refused("manifest", code_id)
    assert "the format of an app cannot change" in refused("code", pkg.id)
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE apps SET format = NULL WHERE id = CAST(:i AS uuid)"),
                  {"i": code_id})
        c.execute(text("UPDATE apps SET format = 'code' WHERE id = CAST(:i AS uuid)"),
                  {"i": code_id})
    assert "the format of an app cannot change" in refused("manifest", code_id)
    assert _count(world) == 1


# ── e. The store ───────────────────────────────────────────────────────────


def test_r8_the_store_writes_a_private_draft_package(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        made = await _create(a, member=ANA.upper())
        async with tenant_session(a) as db:
            return {"made": made, "got": await store.get_package(db, ANA, made.id),
                    "locked": await store.get_package(db, ANA, made.id, for_update=True),
                    "list": await store.list_packages(db, ANA)}

    out = _go(world, go)
    made = out["made"]
    assert made.owner_email == ANA and made.manifest == _manifest()
    assert (made.visibility, made.status, made.live_version) == ("private", "draft", None)
    assert (made.name, made.description) == (MANIFEST["name"], MANIFEST["purpose"])
    assert made.slug.startswith(f"{MID}-") and len(made.slug) == len(MID) + 9
    assert out["got"] == made and out["locked"] == made
    assert [p.id for p in out["list"]] == [made.id]
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT format, workspace_path, organization_id::text FROM apps "
            "WHERE id = CAST(:i AS uuid)"), {"i": made.id}).one()
    assert tuple(row) == ("manifest", "", a)


def test_r8_update_manifest_replaces_the_manifest_and_keeps_the_slug(world) -> None:
    a = world.p.org_a
    new = _manifest(name="Price quotes", purpose="Quotes and their prices.")

    async def go() -> tuple[store.Package, store.Package | None]:
        made = await _create(a)
        async with tenant_session(a) as db:
            return made, await store.update_manifest(db, ANA, made.id, new)

    made, updated = _go(world, go)
    assert updated is not None and updated.manifest == new
    assert (updated.name, updated.description) == ("Price quotes", "Quotes and their prices.")
    assert (updated.id, updated.slug, updated.status) == (made.id, made.slug, "draft")
    assert updated.updated_at >= made.updated_at


def test_r8_an_invalid_manifest_writes_nothing(world) -> None:
    """A2 refuses an unknown block and a colour. The CHECK would take both,
    so only the store's call of validate_manifest stands in the way."""
    a = world.p.org_a
    bad = _manifest(views=[{"id": "list", "block": "spreadsheet", "color": "#ff0000"}])

    async def go() -> dict[str, Any]:
        made = await _create(a)
        out: dict[str, Any] = {"made": made}
        async with tenant_session(a) as db:
            with pytest.raises(store.PackageStoreError) as created:
                await store.create_package(db, ANA, bad)
            with pytest.raises(store.PackageStoreError) as updated:
                await store.update_manifest(db, ANA, made.id, bad)
            # A failed statement would end the transaction, and this read
            # would raise.
            out["after"] = await store.get_package(db, ANA, made.id)
        out["paths"] = (created.value.paths, updated.value.paths)
        return out

    out = _go(world, go)
    assert out["paths"][0] == out["paths"][1] == ["$.views[0].block", "$.views[0].color"]
    assert out["after"] is not None and out["after"].manifest == _manifest()
    assert _count(world) == 1


def test_r8_a_manifest_that_jsonb_refuses_or_grows_writes_nothing(world) -> None:
    """A2 passes each of these. Postgres refuses the NUL, and writes 1e308
    with 309 digits, past the CHECK. The store refuses each first, so the
    session stays usable."""
    a = world.p.org_a
    # Python writes `1e+308`, and Postgres writes 309 digits: 303 bytes more.
    grown = manifest_of_size(store.MAX_MANIFEST_BYTES - 200, base=_manifest())
    grown["actions"][0]["set"]["amount"] = 1e308
    raw = json.dumps(grown, ensure_ascii=False)
    assert len(raw.encode("utf-8")) < store.MAX_MANIFEST_BYTES
    err = _refused(world, a, _RAW_PACKAGE, {
        "slug": f"grown-{TAG}", "m": ANA, "manifest": raw, "format": "manifest"})
    assert "apps_manifest_package" in err
    nul = _manifest(name="Quo\x00tes")

    async def go() -> list[store.Package]:
        async with tenant_session(a) as db:
            for doc in (grown, nul):
                with pytest.raises(store.PackageStoreError):
                    await store.create_package(db, ANA, doc)
            return await store.list_packages(db, ANA)

    assert _go(world, go) == []
    assert _count(world) == 0


# ── f. Tenancy and the member ──────────────────────────────────────────────


def test_r8_org_b_reads_and_writes_nothing_of_org_a(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> dict[str, Any]:
        made = await _create(a)
        out: dict[str, Any] = {"made": made}
        async with tenant_session(b) as db:
            out["get"] = await store.get_package(db, ANA, made.id)
            out["list"] = await store.list_packages(db, ANA)
            out["update"] = await store.update_manifest(
                db, ANA, made.id, _manifest(name="Taken over"))
            out["raw"] = (await db.execute(text(
                "SELECT count(*) FROM apps WHERE id = CAST(:i AS uuid)"),
                {"i": made.id})).scalar_one()
        # A session with no tenant bound reads nothing either. A new backend,
        # because a pooled one that held a SET LOCAL reads the GUC as ''.
        # The URL of the seam (the app role, on asyncpg) inside this scope.
        eng = create_async_engine(async_database_url(), poolclass=NullPool)
        try:
            async with AsyncSession(eng) as s:
                out["unbound_list"] = await store.list_packages(s, ANA)
                out["unbound_get"] = await store.get_package(s, ANA, made.id)
                out["unbound_raw"] = (await s.execute(text(
                    "SELECT count(*) FROM apps WHERE format = 'manifest'"))).scalar_one()
                await s.rollback()
        finally:
            await eng.dispose()
        return out

    out = _go(world, go)
    assert out["get"] is None and out["list"] == [] and out["update"] is None
    assert out["raw"] == 0
    assert out["unbound_list"] == [] and out["unbound_get"] is None
    assert out["unbound_raw"] == 0
    with world.p.admin_engine.connect() as c:
        name, org = c.execute(text(
            "SELECT name, organization_id::text FROM apps WHERE id = CAST(:i AS uuid)"),
            {"i": out["made"].id}).one()
    assert (name, org) == (MANIFEST["name"], a)


def test_r8_a_member_reads_and_writes_only_their_own_packages(world) -> None:
    a = world.p.org_a

    async def go() -> dict[str, Any]:
        anas = await _create(a, member=ANA)
        bens = await _create(a, member=BEN)
        async with tenant_session(a) as db:
            return {
                "anas": anas, "bens": bens,
                "ben_gets_anas": await store.get_package(db, BEN, anas.id),
                "ben_lists": await store.list_packages(db, BEN),
                "ben_updates_anas": await store.update_manifest(
                    db, BEN, anas.id, _manifest(name="Mine now")),
            }

    out = _go(world, go)
    assert out["ben_gets_anas"] is None and out["ben_updates_anas"] is None
    assert [p.id for p in out["ben_lists"]] == [out["bens"].id]
    with world.p.admin_engine.connect() as c:
        name = c.execute(text("SELECT name FROM apps WHERE id = CAST(:i AS uuid)"),
                         {"i": out["anas"].id}).scalar_one()
    assert name == MANIFEST["name"]


# ── g. The slug ────────────────────────────────────────────────────────────


def test_r8_two_orgs_may_hold_one_manifest_id(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> tuple[store.Package, store.Package]:
        return await _create(a), await _create(b)

    pa, pb = _go(world, go)
    assert pa.slug != pb.slug
    assert pa.slug.startswith(f"{MID}-") and pb.slug.startswith(f"{MID}-")
    assert _count(world) == 2


def test_r8_a_slug_clash_with_another_org_leaks_nothing(world, monkeypatch) -> None:
    """Org A holds the slug that the first id of org B gives. The INSERT of
    org B writes nothing, raises nothing, and the store takes the next id.
    Org B never sees the package of org A."""
    a, b = world.p.org_a, world.p.org_b
    first = uuid.UUID(f"{TAG}-0000-4000-8000-000000000001")
    clash = uuid.UUID(f"{TAG}-0000-4000-8000-000000000002")
    fresh = uuid.uuid4()
    ids = iter([first, clash, fresh])
    monkeypatch.setattr(store, "_new_id", lambda: next(ids))

    async def go() -> dict[str, Any]:
        pa = await _create(a)
        pb = await _create(b)
        async with tenant_session(b) as db:
            listed = await store.list_packages(db, ANA)
            seen = (await db.execute(text(
                "SELECT count(*) FROM apps WHERE slug = :s"), {"s": pa.slug})).scalar_one()
        return {"pa": pa, "pb": pb, "listed": listed, "seen": seen}

    out = _go(world, go)
    assert out["pa"].slug == f"{MID}-{TAG}" and out["pa"].id == str(first)
    assert out["pb"].id == str(fresh) and out["pb"].slug == f"{MID}-{fresh.hex[:8]}"
    assert [p.id for p in out["listed"]] == [out["pb"].id]
    assert out["seen"] == 0
    assert _count(world) == 2


# ── h. The readers skip a package ──────────────────────────────────────────


def _user(email: str = ANA) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE)


def test_r8_load_app_skips_a_package(world) -> None:
    a = world.p.org_a
    code_slug = f"code-{TAG}"
    _old_app(world, a, code_slug)

    async def go() -> dict[str, Any]:
        pkg = await _create(a)
        async with tenant_session(a) as db:
            out: dict[str, Any] = {
                "pkg": await _common.load_app(db, pkg.slug),
                "code": await _common.load_app(db, code_slug),
            }
            with pytest.raises(HTTPException) as exc:
                await _common.get_app_or_404(db, pkg.slug, _user())
            out["status"] = exc.value.status_code
        return out

    out = _go(world, go)
    assert out["pkg"] is None
    assert out["code"] is not None and out["code"].slug == code_slug
    assert out["status"] == 404


def test_r8_list_apps_skips_a_package(world) -> None:
    a = world.p.org_a
    code_slug = f"code-{TAG}"
    _old_app(world, a, code_slug)

    async def go() -> list[Any]:
        await _create(a)
        tok = bind_tenant(a)
        try:
            return await lifecycle.list_apps(user=_user())
        finally:
            release_tenant(tok)

    listed = [s.slug for s in _go(world, go)]
    assert code_slug in listed
    assert not [s for s in listed if s.startswith(f"{MID}-")]


def test_r8_list_pinned_apps_skips_a_package(world) -> None:
    a = world.p.org_a
    code_slug = f"code-{TAG}"
    code_id = _old_app(world, a, code_slug)
    pkg = _go(world, lambda: _create(a))
    with world.p.admin_engine.begin() as c:
        for app_id in (code_id, pkg.id):
            c.execute(text(
                "INSERT INTO app_pins (app_id, user_email, organization_id) "
                "VALUES (CAST(:i AS uuid), :m, CAST(:o AS uuid))"),
                {"i": app_id, "m": ANA, "o": a})

    async def go() -> list[Any]:
        tok = bind_tenant(a)
        try:
            return await pins.list_pinned_apps(user=_user())
        finally:
            release_tenant(tok)

    assert [p.slug for p in _go(world, go)] == [code_slug]


@pytest.fixture
def graph_on_promoted(world, monkeypatch):
    """The sync ``acb_graph`` engine on the promoted catalog, as the app role,
    with the run's tenant bind on, as ``test_acb_graph_agenttool_reads_bind``
    does it."""
    from acb_graph import db as graph_db

    eng = create_engine(_dsn(world.p.app_url), future=True, poolclass=NullPool)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    try:
        yield world
    finally:
        eng.dispose()


def test_r8_granted_live_apps_skips_a_package(graph_on_promoted) -> None:
    """A live package with a live version and a grant to the agent still
    never becomes an agent tool. A code app of the same shape does."""
    from orchestrator import app_tools

    world = graph_on_promoted
    a = world.p.org_a
    agent = f"pkgbot{TAG}"
    code_slug = f"code-{TAG}"
    code_id = _old_app(world, a, code_slug)
    pkg = _go(world, lambda: _create(a))
    with world.p.admin_engine.begin() as c:
        for app_id in (code_id, pkg.id):
            c.execute(text(
                "UPDATE apps SET status = 'live', live_version = 1 "
                "WHERE id = CAST(:i AS uuid)"), {"i": app_id})
            c.execute(text(
                "INSERT INTO app_versions (app_id, version, manifest, bundle_html, "
                "bundle_sha256, published_by, organization_id) VALUES "
                "(CAST(:i AS uuid), 1, CAST(:man AS jsonb), '', 'sha', :m, CAST(:o AS uuid))"),
                {"i": app_id, "man": json.dumps(_manifest()), "m": ANA, "o": a})
            c.execute(text(
                "INSERT INTO app_grants (app_id, subject, granted_by, organization_id) "
                "VALUES (CAST(:i AS uuid), :s, :m, CAST(:o AS uuid))"),
                {"i": app_id, "s": f"agent:{agent}", "m": ANA, "o": a})
    clear_tenant()
    tok = bind_tenant(a)
    try:
        granted = app_tools._granted_live_apps(agent)
    finally:
        release_tenant(tok)
        clear_tenant()
    assert [slug for slug, _m in granted] == [code_slug]
