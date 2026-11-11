"""WS-STU G17 — the save history of a Studio draft, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 G17 row (task 2390). Code:
``gateway/studio/packages_store.py``. Migration: the file that creates
``studio_draft_revisions`` (409 at build), found by content, never by number
(R1). The hermetic twin is ``tests/unit/test_studio_draft_revisions.py``.

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations, two members.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL="postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>"
    uv run pytest tests/unit/test_studio_draft_revisions_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (G17).**

a. A draft with 5 saves lists 5 revisions with their authors.
b. A revert makes a new revision and loses none.
c. A publish names its revision.
d. Two saves at once give two distinct revisions in order.
e. Org B cannot read the revisions of org A, through the store or by SQL.
f. A draft that no revision holds is kept before the next save.
"""

from __future__ import annotations

import asyncio
import copy
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
ANA = f"ana.{TAG}@stug17.test"
BEN = f"ben.{TAG}@stug17.test"
_LIKE = f"%.{TAG}@stug17.test"
MID = f"quotes-{TAG}"
CATALOG = {"crm.create_lead": "B"}


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
        c.execute(text("DELETE FROM app_audit WHERE user_email LIKE :t"), {"t": _LIKE})
        # The revisions cascade with the package.
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _LIKE})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


def _rows(world: Any, pid: str) -> list[tuple[Any, ...]]:
    """Every revision of a package, as the superuser, oldest first."""
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT seq, author_kind, author, on_behalf_of, message, restores, "
            "manifest_hash, diff, organization_id::text "
            "FROM studio_draft_revisions WHERE app_id = CAST(:i AS uuid) ORDER BY seq"),
            {"i": pid})]


async def _create(org: str, member: str = ANA, *, author: bool = True) -> store.Package:
    async with tenant_session(org) as db:
        return await store.create_package(
            db, member, _manifest(), author=store.member_author(member) if author else None)


async def _save(org: str, pid: str, doc: dict[str, Any], author: store.Author | None,
                message: str = "") -> store.Package | None:
    async with tenant_session(org) as db:
        return await store.update_manifest(db, ANA, pid, doc, author=author, message=message)


def test_r8_a_five_saves_list_five_revisions_with_their_authors(world) -> None:
    a = world.p.org_a
    me = store.member_author(ANA)

    async def go() -> Any:
        pkg = await _create(a)  # save 1
        await _save(a, pkg.id, _manifest(name="Two"), me, "Second.")
        await _save(a, pkg.id, _manifest(name="Three"), store.MIRA_AUTHOR, "By Mira.")
        await _save(a, pkg.id, _manifest(name="Four"), me)
        await _save(a, pkg.id, _manifest(name="Five"), store.MIRA_AUTHOR)
        async with tenant_session(a) as db:
            listed = await store.list_revisions(db, ANA, pkg.id)
            three = await store.get_revision(db, ANA, pkg.id, 3)
            page = await store.list_revisions(db, ANA, pkg.id, limit=2, before=4)
        return pkg, listed, three, page

    pkg, listed, three, page = _go(world, go)
    assert [r.seq for r in listed] == [5, 4, 3, 2, 1]
    assert [(r.author_kind, r.author) for r in listed] == [
        ("mira", "mira"), ("member", ANA), ("mira", "mira"), ("member", ANA), ("member", ANA)]
    assert all(r.on_behalf_of == ANA for r in listed)
    assert all(r.manifest is None and r.diff is None for r in listed)
    assert [r.message for r in listed] == [
        store.SAVED_TEXT, store.SAVED_TEXT, "By Mira.", "Second.", store.CREATED_TEXT]
    assert three is not None and three.manifest == _manifest(name="Three")
    assert '"name": "Three"' in (three.diff or "") and '"name": "Two"' in (three.diff or "")
    assert [r.seq for r in page] == [3, 2]
    rows = _rows(world, pkg.id)
    assert {r[8] for r in rows} == {a}
    assert rows[0][7] == ""


def test_r8_b_a_revert_adds_a_revision_and_loses_none(world) -> None:
    a = world.p.org_a
    me = store.member_author(ANA)

    async def go() -> Any:
        pkg = await _create(a)
        await _save(a, pkg.id, _manifest(name="Two"), me)
        await _save(a, pkg.id, _manifest(name="Three"), me)
        before = _rows(world, pkg.id)
        async with tenant_session(a) as db:
            out = await store.revert_to_revision(db, ANA, pkg.id, 1, author=me)
        async with tenant_session(a) as db:
            draft = await store.get_package(db, ANA, pkg.id)
        return pkg, before, out, draft

    pkg, before, out, draft = _go(world, go)
    assert out is not None
    _package, revision = out
    assert revision.seq == 4 and revision.restores == 1
    after = _rows(world, pkg.id)
    assert [r[0] for r in after] == [1, 2, 3, 4]
    assert after[:3] == before
    assert after[3][6] == after[0][6]
    assert after[3][4] == "Reverted to revision 1."
    assert draft is not None and draft.manifest == _manifest()


def test_r8_c_a_publish_names_its_revision(world) -> None:
    a = world.p.org_a
    me = store.member_author(ANA)

    async def go() -> Any:
        pkg = await _create(a)
        await _save(a, pkg.id, _manifest(name="Two"), me)
        async with tenant_session(a) as db:
            named = await store.publish_version(db, ANA, pkg.id, "", catalog=CATALOG,
                                                name_revision=True)
        async with tenant_session(a) as db:
            plain = await store.publish_version(db, ANA, pkg.id, "", catalog=CATALOG)
        async with tenant_session(a) as db:
            got = await store.get_version(db, ANA, pkg.id, 1)
        return named, plain, got

    named, plain, got = _go(world, go)
    assert named is not None and named.draft_revision == 2
    assert plain is not None and plain.draft_revision is None
    assert got is not None and got.draft_revision == 2


def test_r8_d_two_saves_at_once_give_two_ordered_distinct_revisions(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pkg = await _create(a)
        await asyncio.gather(
            _save(a, pkg.id, _manifest(name="Left"), store.member_author(ANA), "Left."),
            _save(a, pkg.id, _manifest(name="Right"), store.MIRA_AUTHOR, "Right."))
        async with tenant_session(a) as db:
            draft = await store.get_package(db, ANA, pkg.id)
        return pkg, draft

    pkg, draft = _go(world, go)
    rows = _rows(world, pkg.id)
    assert [r[0] for r in rows] == [1, 2, 3]
    assert {r[4] for r in rows[1:]} == {"Left.", "Right."}
    # The newest revision holds the draft that won, so the order is the commit order.
    assert draft is not None
    assert draft.manifest["name"] == ("Left" if rows[2][4] == "Left." else "Right")


def test_r8_e_org_b_cannot_read_the_revisions_of_org_a(world) -> None:
    a, b = world.p.org_a, world.p.org_b

    async def go() -> Any:
        pkg = await _create(a)
        await _save(a, pkg.id, _manifest(name="Two"), store.member_author(ANA))
        out: dict[str, Any] = {"pkg": pkg}
        for label, org, member in (("org_b", b, ANA), ("ben", a, BEN)):
            async with tenant_session(org) as db:
                out[label] = (await store.list_revisions(db, member, pkg.id),
                              await store.get_revision(db, member, pkg.id, 1),
                              await store.revert_to_revision(
                                  db, member, pkg.id, 1, author=store.member_author(member)))
        async with tenant_session(b) as db:
            out["sql_b"] = (await db.execute(text(
                "SELECT count(*) FROM studio_draft_revisions "
                "WHERE app_id = CAST(:i AS uuid)"), {"i": pkg.id})).scalar_one()
        try:
            async with tenant_session(b) as db:
                await db.execute(text(
                    "INSERT INTO studio_draft_revisions (organization_id, app_id, seq, "
                    "manifest, manifest_hash, author_kind, author, on_behalf_of) "
                    "VALUES (CAST(:o AS uuid), CAST(:i AS uuid), 9, CAST('{}' AS jsonb), "
                    ":h, 'member', :m, :m)"), {"o": a, "i": pkg.id, "h": "0" * 64, "m": ANA})
            out["write_b"] = "written"
        except Exception as exc:  # RLS WITH CHECK refuses the row of org A
            out["write_b"] = type(exc).__name__
        async with tenant_session(a) as db:
            out["sql_a"] = (await db.execute(text(
                "SELECT count(*) FROM studio_draft_revisions "
                "WHERE app_id = CAST(:i AS uuid)"), {"i": pkg.id})).scalar_one()
        return out

    out = _go(world, go)
    assert out["org_b"] == ([], None, None)
    assert out["ben"] == ([], None, None)
    assert out["sql_b"] == 0 and out["sql_a"] == 2
    assert out["write_b"] != "written"
    assert [r[0] for r in _rows(world, out["pkg"].id)] == [1, 2]


def test_r8_f_a_draft_that_no_revision_holds_is_kept_before_the_save(world) -> None:
    a = world.p.org_a

    async def go() -> Any:
        pkg = await _create(a, author=False)
        await _save(a, pkg.id, _manifest(name="Off"), None)
        await _save(a, pkg.id, _manifest(name="On"), store.member_author(ANA))
        return pkg

    pkg = _go(world, go)
    rows = _rows(world, pkg.id)
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (1, "system", store.SYSTEM_AUTHOR), (2, "member", ANA)]
    assert rows[0][4] == store.OUTSIDE_TEXT
    assert '"name": "Off"' in rows[1][7] and '"name": "On"' in rows[1][7]
