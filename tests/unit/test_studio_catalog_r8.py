"""``GET /studio/actions`` on a real database (STU-B1, R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.2 and §14 B1. Code:
``gateway/routes/studio/actions.py``.

The real handler runs on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Org A and org B each hold
workflows. The route takes no org in its SQL, so the wall is the bound
session and nothing else.

Run (one R8 suite at a time on the shared scratch database)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_catalog_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS. A skip is not a pass.

**Acceptance (STU-B1).**

a. Org A sees its own published workflows, and never org B's.
b. Org B sees its own, and never org A's.
c. A draft and a disabled workflow never appear, in either org.
d. An unbound session sees no workflow at all (the role cannot bypass RLS).

**The mutation (R7).** In ``actions._workflow_rows``, read with a session
that is not bound to the tenant (``get_session_factory()()`` in place of
``_tenant_session()``). ``test_r8_an_org_sees_its_own_published_workflows``
goes red, because the app role then reads no row.

A second mutation: read with a session that bypasses RLS (a superuser or
BYPASSRLS engine). Both ``test_r8_an_org_sees_its_own_published_workflows``
and ``test_r8_an_org_never_sees_another_orgs_workflow`` go red. The
independent verifier ran it on 2026-10-31 and got ``2 failed, 3 passed``.

**STU-B1c (the rows of the caller's own packages).** Code:
``actions.package_rows`` over ``packages_store.list_package_actions``.

a. The owner sees five rows for each object and one for each named action.
b. Another member and org B see none of those rows. A member with no package
   sees none.
c. Two packages with one object name give two rows with different ``app``.
d. A named action whose target the catalog does not serve is left out.
e. ``?app=`` narrows to one package.
f. The statement count of the route does not grow with the packages.
g. ``catalog_rows`` holds no row of source ``package``.

**The mutations (B1c, run 2026-11-02, with both catalog suites).**

* M1. ``packages_store._ACTIONS_SQL``: drop ``owner_email = :m``. Four R8
  tests go red, among them ``test_r8_another_member_of_the_org_sees_only_their_own_rows``.
* M2. Same SQL: drop the ``organization_id`` bound. No R8 test goes red,
  because FORCE RLS still binds the org. The hermetic
  ``test_the_statement_binds_the_org_and_the_member_and_loads_no_manifest``
  goes red.
* M3. ``actions.package_rows``: read once for each package.
  ``test_r8_the_statement_count_does_not_grow_with_the_packages`` goes red.
* M4. ``actions.catalog_rows``: add the package rows.
  ``test_a_fence_catalog_rows_holds_no_package_row`` goes red.
* M5 (own). ``actions.package_rows``: ignore ``?app=``.
  ``test_r8_the_app_filter_narrows_to_one_package`` and
  ``test_the_app_filter_narrows_to_one_package`` go red.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.studio import actions
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A holds two published, one draft and one disabled workflow. Org B
    holds one published and one draft. The flag is open for every org."""
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    seeded: dict[tuple[str, str], str] = {}
    with promoted.admin_engine.begin() as conn:
        for tag, org, kinds in (
            ("A", promoted.org_a, ("published", "published", "draft", "disabled")),
            ("B", promoted.org_b, ("published", "draft")),
        ):
            for i, status in enumerate(kinds):
                wid = str(uuid.uuid4())
                seeded[(tag, f"{status}{i}")] = wid
                conn.execute(text(
                    "INSERT INTO workflows (id, name, owner_email, status, "
                    "organization_id) VALUES (:id, :n, :o, :s, :org)"),
                    {"id": wid, "n": f"{tag} {status} {i} {TAG}",
                     "o": f"owner@{tag.lower()}.stu.test", "s": status, "org": org})
    promoted.seeded = seeded
    yield promoted
    with promoted.admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM workflows WHERE name LIKE :p"),
                     {"p": f"% {TAG}"})


def _user(org: str) -> UserContext:
    return UserContext(
        email="m@example.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:workflows"]), organization_id=org)


def _as(world, org: str):
    """Call the real handler on the app-role engine with *org* bound."""

    async def _go():
        dsn = world.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn):
            token = bind_tenant(org)
            try:
                return await actions.list_actions(_user(org))
            finally:
                release_tenant(token)

    return asyncio.run(_go())


def _workflow_ids(body: dict) -> set[str]:
    return {r["id"].removeprefix("workflow:")
            for r in body["actions"] if r["source"] == "workflow"}


def _published(world, tag: str) -> set[str]:
    return {wid for (t, kind), wid in world.seeded.items()
            if t == tag and kind.startswith("published")}


def test_the_admin_sees_both_orgs_rows(world):
    """Precondition: both orgs hold rows, so a wall is what hides them."""
    with world.admin_engine.connect() as c:
        n = c.execute(text(
            "SELECT count(*) FROM workflows WHERE name LIKE :p"),
            {"p": f"% {TAG}"}).scalar()
    assert n == 6


def test_r8_an_org_sees_its_own_published_workflows(world):
    assert _workflow_ids(_as(world, world.org_a)) == _published(world, "A")
    assert _workflow_ids(_as(world, world.org_b)) == _published(world, "B")


def test_r8_an_org_never_sees_another_orgs_workflow(world):
    a = _workflow_ids(_as(world, world.org_a))
    b = _workflow_ids(_as(world, world.org_b))
    assert not a & set(world.seeded.values()) - _published(world, "A")
    assert not b & set(world.seeded.values()) - _published(world, "B")
    assert not a & b


def test_r8_a_draft_and_a_disabled_workflow_never_appear(world):
    unpublished = {wid for (_, kind), wid in world.seeded.items()
                   if not kind.startswith("published")}
    for org in (world.org_a, world.org_b):
        assert not _workflow_ids(_as(world, org)) & unpublished


def test_r8_an_unbound_session_sees_no_workflow(world):
    async def _go():
        dsn = world.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn):
            from acb_common.db import get_session_factory

            async with get_session_factory()() as db:
                return (await db.execute(text(
                    "SELECT count(*) FROM workflows WHERE name LIKE :p"),
                    {"p": f"% {TAG}"})).scalar()

    assert asyncio.run(_go()) == 0


# --- B1c: the rows of the caller's own packages (R8) -------------------------

ANA = f"ana.{TAG}@stub1c.test"
BEN = f"ben.{TAG}@stub1c.test"
_VERBS = {"list": "A", "get": "A", "create": "B", "update": "B", "delete": "C"}


def _manifest(mid: str, wf_a: str, wf_b: str, objects: list[str]) -> dict:
    return {
        "studio": 1,
        "id": mid,
        "name": "Catalog",
        "purpose": "Test the package rows.",
        "objects": [{"name": n, "fields": [{"key": "title", "type": "text"}]}
                    for n in objects],
        "actions": [
            {"id": "peek", "uses": f"object:{objects[0]}.get", "title": "Peek"},
            {"id": "flow", "uses": f"workflow:{wf_a}"},
            {"id": "flow_b", "uses": f"workflow:{wf_b}"},
            {"id": "pages", "uses": "knowledge.list_pages"},
            {"id": "ghost", "uses": "knowledge.no_such_op"},
        ],
    }


def _go(world, fn):
    async def go():
        dsn = world.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn):
            return await fn()

    return asyncio.run(go())


@pytest.fixture
def pworld(promoted, monkeypatch):  # noqa: F811
    """Org A: two packages of Ana (objects ``items`` and ``tasks``, then
    ``items``) and one of Ben (``items``). Org B: one package of a member with
    Ana's email. One published workflow in each org."""
    from acb_common.db import tenant_session
    from gateway.studio import packages_store

    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    run = uuid.uuid4().hex[:6]
    wf = {}
    with p.admin_engine.begin() as c:
        for tag, org in (("a", p.org_a), ("b", p.org_b)):
            wf[tag] = str(uuid.uuid4())
            c.execute(text(
                "INSERT INTO workflows (id, name, owner_email, status, organization_id) "
                "VALUES (:id, :n, :o, 'published', :org)"),
                {"id": wf[tag], "n": f"Flow {tag} {TAG}", "o": ANA, "org": org})
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, wf=wf, mid=f"cat-{TAG}-{run}")
    w.app_url = p.app_url

    async def make(org, member, objects, n=0):
        async with tenant_session(org) as db:
            made = await packages_store.create_package(
                db, member, _manifest(f"{w.mid}-{n}", wf["a"], wf["b"], objects))
        return made.id

    w.make = make

    async def setup():
        w.p1 = await make(w.a, ANA, ["items", "tasks"], 1)
        w.p2 = await make(w.a, ANA, ["items"], 2)
        w.p_ben = await make(w.a, BEN, ["items"], 3)
        w.p_b = await make(w.b, ANA, ["items"], 4)

    _go(w, setup)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"{w.mid}-%"})
        c.execute(text("DELETE FROM workflows WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                  {"a": wf["a"], "b": wf["b"]})


def _list(world, org: str, email: str, app: str | None = None) -> list[dict]:
    """Call the real handler with *org* bound and *email* as the caller."""

    async def fn():
        token = bind_tenant(org)
        try:
            user = UserContext(email=email, role=UserRole.EMPLOYEE,
                               access=build_access(["feature:workflows"]),
                               organization_id=org)
            return (await actions.list_actions(user, app=app))["actions"]
        finally:
            release_tenant(token)

    return _go(world, fn)


def _mine(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["source"] == "package"]


def test_r8_the_owner_sees_five_rows_for_each_object_and_one_for_each_named_action(pworld):
    rows = _mine(_list(pworld, pworld.a, ANA))
    p1 = [r for r in rows if r["app"] == pworld.p1]
    objects = {r["id"]: r["class"] for r in p1 if r["id"].startswith("object:")}
    assert objects == {f"object:{o}.{v}": c for o in ("items", "tasks")
                       for v, c in _VERBS.items()}
    named = {r["id"]: r for r in p1 if not r["id"].startswith("object:")}
    # peek (object), flow (own-org workflow, B) and pages (door op, A) stay.
    assert set(named) == {"peek", "flow", "pages"}
    assert (named["peek"]["uses"], named["peek"]["class"]) == ("object:items.get", "A")
    assert (named["flow"]["uses"], named["flow"]["class"]) == (
        f"workflow:{pworld.wf['a']}", "B")
    assert (named["pages"]["uses"], named["pages"]["class"]) == ("knowledge.list_pages", "A")


def test_r8_an_action_outside_the_catalog_is_left_out(pworld):
    ids = {r["id"] for r in _mine(_list(pworld, pworld.a, ANA))}
    # flow_b names a workflow of org B. ghost names no catalog op.
    assert not ids & {"flow_b", "ghost"}


def test_r8_two_packages_with_one_object_name_give_two_rows_with_different_app(pworld):
    rows = [r for r in _mine(_list(pworld, pworld.a, ANA)) if r["id"] == "object:items.list"]
    assert sorted(r["app"] for r in rows) == sorted([pworld.p1, pworld.p2])


def test_r8_another_member_of_the_org_sees_only_their_own_rows(pworld):
    rows = _mine(_list(pworld, pworld.a, BEN))
    assert {r["app"] for r in rows} == {pworld.p_ben}
    ana = _mine(_list(pworld, pworld.a, ANA))
    assert pworld.p_ben not in {r["app"] for r in ana}


def test_r8_a_member_with_no_package_and_org_b_see_none(pworld):
    assert _mine(_list(pworld, pworld.a, f"nobody.{TAG}@stub1c.test")) == []
    # Org B holds a package of a member with Ana's email: only that one shows.
    assert {r["app"] for r in _mine(_list(pworld, pworld.b, ANA))} == {pworld.p_b}
    assert _mine(_list(pworld, pworld.b, BEN)) == []


def test_r8_the_app_filter_narrows_to_one_package(pworld):
    rows = _list(pworld, pworld.a, ANA, app=pworld.p2)
    assert {r["app"] for r in rows} == {pworld.p2}
    assert len([r for r in rows if r["id"].startswith("object:")]) == 5
    # The package of another member and of org B give nothing.
    assert _list(pworld, pworld.a, ANA, app=pworld.p_ben) == []
    assert _list(pworld, pworld.a, ANA, app=pworld.p_b) == []


def test_r8_the_catalog_rows_hold_no_package_row(pworld):
    async def fn():
        token = bind_tenant(pworld.a)
        try:
            return await actions.catalog_rows()
        finally:
            release_tenant(token)

    assert all(r["source"] != "package" for r in _go(pworld, fn))


def test_r8_the_statement_count_does_not_grow_with_the_packages(pworld):
    from acb_common.db import get_engine
    from sqlalchemy import event

    member = f"count.{TAG}@stub1c.test"

    async def add(n: int) -> None:
        for i in range(n):
            await pworld.make(pworld.a, member, ["items"], 100 + i)

    async def counted() -> tuple[int, int]:
        seen: list[str] = []
        eng = get_engine().sync_engine

        def _on(conn, cursor, statement, *a):
            seen.append(statement)

        event.listen(eng, "before_cursor_execute", _on)
        try:
            token = bind_tenant(pworld.a)
            try:
                user = UserContext(email=member, role=UserRole.EMPLOYEE,
                                   access=build_access(["feature:workflows"]),
                                   organization_id=pworld.a)
                body = (await actions.list_actions(user, app=None))["actions"]
            finally:
                release_tenant(token)
        finally:
            event.remove(eng, "before_cursor_execute", _on)
        return len(seen), len(_mine(body))

    _go(pworld, lambda: add(1))
    one, rows_one = _go(pworld, counted)
    _go(pworld, lambda: add(6))
    seven, rows_seven = _go(pworld, counted)
    assert rows_one < rows_seven
    assert one == seven
