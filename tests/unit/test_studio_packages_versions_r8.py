"""WS-STU A4 — publish, version and roll back a Studio package, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.6 and the §14 A4 row. Code:
``gateway/studio/packages_store.py`` (``publish_version``, ``rollback``,
``list_versions``, ``get_version``). The hermetic twin is
``tests/unit/test_studio_packages_store.py`` (group h). No migration: every
column exists since 114 and 322.

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL="postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>"
    uv run pytest tests/unit/test_studio_packages_versions_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (A4).**

a. A publish makes version N+1, sets ``apps.live_version`` and
   ``apps.status = 'live'``, and leaves ``apps.manifest`` as it is.
b. A rollback to v1 after v2 makes v3 with the manifest of v1. The draft
   equals it, the name follows, and ``live_version`` is 3.
c. A publish or a rollback never edits an existing ``app_versions`` row.
d. Org B and another member see no versions, and cannot publish or roll back.
e. An invalid draft publishes nothing: no version, no pointer, no audit row.
f. Two writers on two connections get N and N+1.
g. The ``app_audit`` row is in the transaction of the write.
h. A rollback to the live version, and to a missing version, write nothing.

The mutations (R7) are in ``MUTATIONS`` below.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.studio import packages_store as store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_studio_packages_store import MANIFEST

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stua4.test"
BEN = f"ben.{TAG}@stua4.test"
_LIKE = f"%.{TAG}@stua4.test"
MID = f"quotes-{TAG}"

#: A catalog that serves one action of class B.
CATALOG = {"crm.create_lead": "B"}
_VERSION_COLUMNS = (
    "id::text, app_id::text, version, manifest::text, bundle_html, bundle_sha256, "
    "release_notes, scope_set_hash, published_by, published_at::text, review_status")

MUTATIONS = """
Each result was run on 2026-11-01, with this suite and group h of the twin.
Commit first, and restore with git after.

* M-a. Remove ``for_update=True`` from ``publish_version`` and ``rollback``.
  ``test_r8_two_writers_on_two_connections_get_n_and_n_plus_1`` and two
  hermetic lock tests go red.
* M-b. Drop the ``owner_email`` filter from the statements.
  ``test_r8_org_b_and_another_member_see_no_versions`` goes red, and so do
  the filter tests of the twin.
* M-c. Rollback moves only the pointer (no new version).
  ``test_r8_a_rollback_to_v1_after_v2_makes_v3_and_restores_the_draft`` and
  ``test_r8_a_publish_after_a_rollback_never_edits_an_old_row`` go red.
* M-d. Rollback skips the draft copy. The same first test goes red.
* M-e. Publish skips ``check_manifest``.
  ``test_publish_and_rollback_run_check_manifest_on_the_stored_manifest``
  goes red.
* M-f. The audit commits outside the caller's transaction.
  ``test_r8_a_failure_after_a_write_rolls_back_the_version_and_the_audit``
  goes red.
* M-g. Publish updates ``apps.manifest``.
  ``test_publish_writes_the_version_the_pointer_and_the_audit_in_order``
  goes red.
* M-h. A string ``UPDATE app_versions`` or a call of ``propose`` in the
  store. ``test_a_version_row_is_never_updated_or_deleted_and_no_review_is_proposed``
  goes red.
"""


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _manifest(**patch: Any) -> dict[str, Any]:
    d = copy.deepcopy(MANIFEST)
    d["id"] = MID
    d.update(patch)
    return d


@pytest.fixture
def world(promoted):  # noqa: F811
    p = promoted
    yield SimpleNamespace(p=p)
    with p.admin_engine.begin() as c:
        # app_audit has no foreign key, so it goes by its own filter.
        c.execute(text("DELETE FROM app_audit WHERE user_email LIKE :t"), {"t": _LIKE})
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _create(org: str, member: str = ANA, doc: dict[str, Any] | None = None) -> store.Package:
    async with tenant_session(org) as db:
        return await store.create_package(db, member, doc or _manifest())


def _versions(world: Any, pid: str) -> list[tuple[Any, ...]]:
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            f"SELECT {_VERSION_COLUMNS} FROM app_versions WHERE app_id = CAST(:i AS uuid) "
            "ORDER BY version"), {"i": pid})]


def _app(world: Any, pid: str) -> tuple[Any, ...]:
    with world.p.admin_engine.connect() as c:
        return tuple(c.execute(text(
            "SELECT status, live_version, manifest::text, name, description, visibility "
            "FROM apps WHERE id = CAST(:i AS uuid)"), {"i": pid}).one())


def _audit(world: Any, pid: str) -> list[tuple[Any, ...]]:
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT kind, app_version, user_email, detail FROM app_audit "
            "WHERE app_id = CAST(:i AS uuid) ORDER BY id"), {"i": pid})]


async def _publish(org: str, pid: str, notes: str = "", member: str = ANA,
                   catalog: dict[str, str] | None = None) -> store.PackageVersion | None:
    async with tenant_session(org) as db:
        return await store.publish_version(
            db, member, pid, notes, catalog=CATALOG if catalog is None else catalog)


async def _edit(org: str, pid: str, doc: dict[str, Any]) -> None:
    async with tenant_session(org) as db:
        assert await store.update_manifest(db, ANA, pid, doc) is not None


# ── a. Publish ─────────────────────────────────────────────────────────────


def test_r8_a_publish_makes_the_next_version_and_moves_the_pointer(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pkg = await _create(a)
        v1 = await _publish(a, pkg.id, "First.")
        await _edit(a, pkg.id, _manifest(purpose="A second purpose."))
        v2 = await _publish(a, pkg.id, "Second.")
        return pkg, v1, v2

    pkg, v1, v2 = _go(world, go)
    assert (v1.version, v2.version) == (1, 2)
    rows = _versions(world, pkg.id)
    assert [r[2] for r in rows] == [1, 2]
    one = rows[0]
    # The text is that of the stored (jsonb) manifest, which orders its keys.
    encoded = store.check_manifest(json.loads(one[3]))
    assert json.loads(one[3]) == _manifest()
    assert one[4] == "" and one[5] == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    assert (one[6], one[7], one[8], one[10]) == ("First.", "", ANA, "auto")
    status, live, manifest, _name, _desc, visibility = _app(world, pkg.id)
    # The pointer and the status moved. The draft is the second manifest.
    assert (status, live, visibility) == ("live", 2, "private")
    assert json.loads(manifest)["purpose"] == "A second purpose."
    assert [(k, v, e) for k, v, e, _d in _audit(world, pkg.id)] == [
        ("publish", 1, ANA), ("publish", 2, ANA)]


def test_r8_a_publish_leaves_the_draft_as_it_is(world) -> None:
    a = world.p.org_a

    async def go() -> store.Package:
        pkg = await _create(a)
        await _publish(a, pkg.id)
        async with tenant_session(a) as db:
            got = await store.get_package(db, ANA, pkg.id)
            assert got is not None
            return got

    got = _go(world, go)
    assert got.manifest == _manifest()
    assert (got.status, got.live_version) == ("live", 1)


def test_r8_list_and_get_read_the_versions(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pkg = await _create(a)
        await _publish(a, pkg.id, "One.")
        await _edit(a, pkg.id, _manifest(name="Quotes 2"))
        await _publish(a, pkg.id, "Two.")
        async with tenant_session(a) as db:
            return (await store.list_versions(db, ANA, pkg.id),
                    await store.get_version(db, ANA, pkg.id, 1),
                    await store.get_version(db, ANA, pkg.id, 2),
                    await store.get_version(db, ANA, pkg.id, 3))

    listed, one, two, three = _go(world, go)
    assert [v.version for v in listed] == [2, 1]
    assert all(v.manifest is None for v in listed)
    assert one is not None and one.manifest == _manifest() and one.release_notes == "One."
    assert two is not None and two.manifest["name"] == "Quotes 2"
    assert three is None


# ── b. Rollback ────────────────────────────────────────────────────────────


def test_r8_a_rollback_to_v1_after_v2_makes_v3_and_restores_the_draft(world) -> None:
    a = world.p.org_a
    second = _manifest(name="Quotes two", purpose="The second purpose.")

    async def go() -> Any:
        pkg = await _create(a)
        await _publish(a, pkg.id, "One.")
        await _edit(a, pkg.id, second)
        await _publish(a, pkg.id, "Two.")
        before = _versions(world, pkg.id)
        async with tenant_session(a) as db:
            v3 = await store.rollback(db, ANA, pkg.id, 1, catalog=CATALOG)
        return pkg, before, v3

    pkg, before, v3 = _go(world, go)
    assert v3 is not None and v3.version == 3 and v3.manifest == _manifest()
    rows = _versions(world, pkg.id)
    assert [r[2] for r in rows] == [1, 2, 3]
    assert json.loads(rows[2][3]) == _manifest()
    # The copy hashes the same text as v1, and an old row is not edited (c).
    assert rows[2][5] == rows[0][5]
    assert rows[:2] == before
    status, live, manifest, name, desc, _vis = _app(world, pkg.id)
    assert json.loads(manifest) == _manifest()
    assert (status, live, name, desc) == ("live", 3, MANIFEST["name"], MANIFEST["purpose"])
    kinds = _audit(world, pkg.id)
    assert [(k, v) for k, v, _e, _d in kinds] == [("publish", 1), ("publish", 2), ("rollback", 3)]
    detail = kinds[2][3]
    assert (detail["to_version"], detail["new_version"], detail["from_version"]) == (1, 3, 2)
    assert detail["draft_overwritten"] is True


def test_r8_a_publish_after_a_rollback_never_edits_an_old_row(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pkg = await _create(a)
        await _publish(a, pkg.id)
        await _edit(a, pkg.id, _manifest(name="Quotes two"))
        await _publish(a, pkg.id)
        async with tenant_session(a) as db:
            await store.rollback(db, ANA, pkg.id, 1, catalog=CATALOG)
        before = _versions(world, pkg.id)
        await _publish(a, pkg.id, "Four.")
        async with tenant_session(a) as db:
            await store.rollback(db, ANA, pkg.id, 2, catalog=CATALOG)
        return pkg, before

    pkg, before = _go(world, go)
    after = _versions(world, pkg.id)
    assert [r[2] for r in after] == [1, 2, 3, 4, 5]
    assert after[:3] == before
    assert json.loads(after[4][3])["name"] == "Quotes two"


def test_r8_a_rollback_to_the_live_version_or_a_missing_one_writes_nothing(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pkg = await _create(a)
        await _publish(a, pkg.id)
        await _edit(a, pkg.id, _manifest(name="Draft edit"))
        await _publish(a, pkg.id)
        out: dict[str, Any] = {"pkg": pkg}
        async with tenant_session(a) as db:
            out["missing"] = await store.rollback(db, ANA, pkg.id, 9, catalog=CATALOG)
        try:
            async with tenant_session(a) as db:
                await store.rollback(db, ANA, pkg.id, 2, catalog=CATALOG)
        except store.PackageStoreError as exc:
            out["live"] = str(exc)
        return out

    out = _go(world, go)
    pid = out["pkg"].id
    assert out["missing"] is None and out["live"] == store.ROLLBACK_LIVE
    assert [r[2] for r in _versions(world, pid)] == [1, 2]
    assert len(_audit(world, pid)) == 2
    assert _app(world, pid)[1] == 2


# ── d. Tenancy and the member ──────────────────────────────────────────────


def test_r8_org_b_and_another_member_see_no_versions(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> Any:
        pkg = await _create(a)
        await _publish(a, pkg.id)
        await _publish(a, pkg.id)
        before = _versions(world, pkg.id)
        out: dict[str, Any] = {"pkg": pkg, "before": before}
        for label, org, member in (("org_b", b, ANA), ("ben", a, BEN), ("ben_b", b, BEN)):
            async with tenant_session(org) as db:
                out[label] = (
                    await store.list_versions(db, member, pkg.id),
                    await store.get_version(db, member, pkg.id, 1),
                    await store.publish_version(db, member, pkg.id, "x", catalog=CATALOG),
                    await store.rollback(db, member, pkg.id, 1, catalog=CATALOG))
        return out

    out = _go(world, go)
    for label in ("org_b", "ben", "ben_b"):
        assert out[label] == ([], None, None, None), label
    assert _versions(world, out["pkg"].id) == out["before"]
    assert len(_audit(world, out["pkg"].id)) == 2


def test_r8_a_code_app_has_no_versions_through_the_package_store(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        async with tenant_session(a) as db:
            row = (await db.execute(text(
                "INSERT INTO apps (slug, name, icon, description, owner_email, manifest, "
                "workspace_path) VALUES (:s, 'Old', '', '', :m, CAST('{}' AS jsonb), '/tmp/x') "
                "RETURNING id::text"), {"s": f"old-{TAG}", "m": ANA})).one()
        async with tenant_session(a) as db:
            return (await store.publish_version(db, ANA, row[0], "", catalog=CATALOG),
                    await store.rollback(db, ANA, row[0], 1, catalog=CATALOG),
                    await store.list_versions(db, ANA, row[0]))

    assert _go(world, go) == (None, None, [])


# ── e. An invalid draft publishes nothing ──────────────────────────────────


def _set_draft(world: Any, pid: str, doc: dict[str, Any]) -> None:
    """Write a draft that the store would refuse, as the superuser."""
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE apps SET manifest = CAST(:m AS jsonb) "
                       "WHERE id = CAST(:i AS uuid)"), {"m": json.dumps(doc), "i": pid})


@pytest.mark.parametrize("case", ["colour", "unknown_action", "class_x"])
def test_r8_an_invalid_draft_publishes_nothing(world, case: str) -> None:
    a = world.p.org_a
    doc = _manifest()
    catalog = dict(CATALOG)
    if case == "colour":
        doc["views"] = [{"id": "list", "block": "table", "color": "#ff0000"}]
    else:
        doc["actions"] = [*doc["actions"], {"id": "lead", "uses": "crm.create_lead", "set": {}}]
        catalog = {} if case == "unknown_action" else {"crm.create_lead": "X"}

    async def go() -> Any:
        pkg = await _create(a)
        _set_draft(world, pkg.id, doc)
        with pytest.raises(store.PackageStoreError) as exc:
            await _publish(a, pkg.id, catalog=catalog)
        assert exc.value.errors
        return pkg

    pkg = _go(world, go)
    assert _versions(world, pkg.id) == [] and _audit(world, pkg.id) == []
    status, live, *_ = _app(world, pkg.id)
    assert (status, live) == ("draft", None)


# ── f. Two writers ─────────────────────────────────────────────────────────


def test_r8_two_writers_on_two_connections_get_n_and_n_plus_1(world) -> None:
    """Writer 1 publishes and holds its transaction open. Writer 2, on a
    second connection, must wait on the row lock, and take the next number
    after writer 1 commits."""
    a = world.p.org_a

    async def pid_of(db: Any) -> str:
        return str((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())

    async def second(pid: str) -> tuple[store.PackageVersion, str]:
        async with tenant_session(a) as db2:
            ver = await store.publish_version(db2, ANA, pid, "two", catalog=CATALOG)
            assert ver is not None
            return ver, await pid_of(db2)

    async def go() -> Any:
        pkg = await _create(a)
        async with tenant_session(a) as db1:
            first = await store.publish_version(db1, ANA, pkg.id, "one", catalog=CATALOG)
            pid1 = await pid_of(db1)
            task = asyncio.create_task(second(pkg.id))
            await asyncio.sleep(1.0)
            blocked = not task.done()
        ver2, pid2 = await task
        return pkg, first, ver2, blocked, pid1, pid2

    pkg, first, ver2, blocked, pid1, pid2 = _go(world, go)
    assert blocked, "writer 2 did not wait for the row lock"
    assert pid1 != pid2
    assert (first.version, ver2.version) == (1, 2)
    assert [r[2] for r in _versions(world, pkg.id)] == [1, 2]
    assert _app(world, pkg.id)[1] == 2


# ── g. The audit is in the transaction ─────────────────────────────────────


def test_r8_a_failure_after_a_write_rolls_back_the_version_and_the_audit(world) -> None:
    a = world.p.org_a

    async def go() -> store.Package:
        pkg = await _create(a)
        await _publish(a, pkg.id)
        with pytest.raises(RuntimeError):
            async with tenant_session(a) as db:
                await store.publish_version(db, ANA, pkg.id, "lost", catalog=CATALOG)
                raise RuntimeError("the caller fails after the store")
        await _edit(a, pkg.id, _manifest(name="Quotes two"))
        await _publish(a, pkg.id)
        with pytest.raises(RuntimeError):
            async with tenant_session(a) as db:
                await store.rollback(db, ANA, pkg.id, 1, catalog=CATALOG)
                raise RuntimeError("the caller fails after the store")
        return pkg

    pkg = _go(world, go)
    assert [r[2] for r in _versions(world, pkg.id)] == [1, 2]
    assert [(k, v) for k, v, _e, _d in _audit(world, pkg.id)] == [("publish", 1), ("publish", 2)]
    status, live, manifest, *_ = _app(world, pkg.id)
    assert (status, live) == ("live", 2) and json.loads(manifest)["name"] == "Quotes two"


# ── i. A rollback checks the catalog (fix round 1) ─────────────────────────


def test_r8_a_rollback_to_a_version_the_catalog_withdrew_writes_nothing(world) -> None:
    a = world.p.org_a
    with_action = _manifest(
        actions=[*MANIFEST["actions"], {"id": "lead", "uses": "crm.create_lead", "set": {}}])

    async def go() -> Any:
        pkg = await _create(a, doc=with_action)
        await _publish(a, pkg.id, "One.")
        await _edit(a, pkg.id, _manifest(name="Quotes two"))
        await _publish(a, pkg.id, "Two.")
        before = (_versions(world, pkg.id), _app(world, pkg.id), _audit(world, pkg.id))
        with pytest.raises(store.PackageStoreError) as exc:
            async with tenant_session(a) as db:
                await store.rollback(db, ANA, pkg.id, 1, catalog={})
        assert exc.value.errors
        return pkg, before

    pkg, before = _go(world, go)
    assert (_versions(world, pkg.id), _app(world, pkg.id), _audit(world, pkg.id)) == before
