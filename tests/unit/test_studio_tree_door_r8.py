"""WS-STU STU-D18-2a: the tree read with the rights of the viewer, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` section 14, row D18-2a. Code:
``gateway/studio/objects_store.read_tree`` (the row rule on each step of the
walk) and ``gateway/routes/studio/invoke.py`` (the door op
``object:<name>.tree``). The hermetic half is ``test_studio_tree_door.py``.

The store and the real handler run on a phase-4 promoted scratch database, as
the app role with no BYPASSRLS, so FORCE RLS binds each statement. Two
organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_tree_door_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**The world.** A ``site`` is a tree through its hot relation ``parent``. Each
site names a ``keeper`` (a hot person field). ``hr`` reads all. ``keeper``
reads the sites where ``keeper = $me``, and ``secret`` is hidden from it.
``hider`` reads all, and ``parent`` is hidden from it. The tree of org A::

    A (E1) ── B (E1) ── D (E1)
          └── C (E2) ── F (E1)
    G (E2) ── H (E1)

**Acceptance (D18-2a).** A viewer reads only the nodes that its row rules
allow. The subtree below a hidden node is cut: E1 reads A, B and D only, and
never F or H. A hidden node shows in no count, no parent id, no level and no
``truncated``. Org B never reads a node of org A, also with a guessed root id,
and the door answers 404 ``Not found``, never an empty tree. A root id that
names nothing is the same 404. A tree of 2,000 nodes answers in ONE door call
with ``truncated`` false, and 2,001 nodes give ``truncated`` true. The depth
cap holds.

**The mutations (R7).** Commit before each, and restore with git after. Each
was run on 2026-11-14.

* M1. ``objects_store.read_tree``: always use the plain statement (drop
  ``_scoped``). ``test_r8_a_viewer_reads_only_its_nodes_and_a_hidden_node_cuts_its_subtree``
  and ``test_r8_truncated_counts_only_the_nodes_that_the_viewer_reads`` go red.
* M2. ``objects_store._tree_read_sql``: drop the row rule of the step down
  (``step_rule``). ``test_r8_a_viewer_reads_only_its_nodes_and_a_hidden_node_cuts_its_subtree``
  goes red (F appears under a hidden parent).
* M3. ``invoke._run_object_tree``: drop the ``root_id`` branch that sets
  ``missing``. ``test_r8_a_root_of_org_b_another_package_or_nothing_is_404`` goes red.
* M4. ``objects_store.read_tree``: drop the refusal of a hidden parent field.
  ``test_r8_a_parent_field_hidden_from_the_viewer_refuses_the_read`` goes red.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, get_engine, release_tenant, tenant_session
from acb_common.studio_access import Viewer
from acb_common.studio_query import QueryError
from fastapi import HTTPException
from gateway.routes.studio import invoke
from gateway.studio import objects_store as store
from gateway.studio import packages_store
from gateway.studio.objects_store import Write
from sqlalchemy import event, text

from tests.unit import _r8_lists as r8_lists
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWNER = f"ana.{TAG}@stud18.test"
BEN = f"ben.{TAG}@stud18.test"
E1, E2 = (f"e{i}.{TAG}@stud18.test" for i in (1, 2))
AS_OWNER = Viewer(OWNER)
AS_E1 = Viewer(E1, frozenset({"keeper"}))
AS_HR = Viewer(f"hr.{TAG}@stud18.test", frozenset({"hr"}))
AS_HIDER = Viewer(f"hider.{TAG}@stud18.test", frozenset({"hider"}))
AS_STRANGER = Viewer(f"x.{TAG}@stud18.test", frozenset({"intern"}))
NOT_FOUND = "Not found"

SITE_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "parent", "type": "relation", "to": "site", "hot": True},
    {"key": "keeper", "type": "person", "hot": True},
    {"key": "secret", "type": "text"},
]
LOT_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "parent", "type": "relation", "to": "lot", "hot": True},
]
FLAT_FIELDS: list[dict[str, Any]] = [{"key": "name", "type": "text"}]
SITE_TREE = {"name": "tree", "kind": "hierarchy", "field": "parent"}
SITE_ACCESS: list[dict[str, Any]] = [
    {"role": "hr", "read": "all"},
    {"role": "keeper", "read": {"where": "keeper = $me"}, "hide": ["secret"]},
    {"role": "hider", "read": "all", "hide": ["parent"]},
]


def _manifest(mid: str) -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": "Sites", "purpose": "The D18-2a R8 suite.",
        "objects": [
            {"name": "site", "fields": SITE_FIELDS, "rules": [SITE_TREE]},
            {"name": "lot", "fields": LOT_FIELDS, "rules": [SITE_TREE]},
            {"name": "flat", "fields": FLAT_FIELDS},
        ],
        "actions": [
            {"id": "sites", "uses": "object:site.tree"},
            {"id": "lots", "uses": "object:lot.tree"},
            {"id": "flats", "uses": "object:flat.tree"},
        ],
    }


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio"]), organization_id=org)


class _Request:
    """The one part of a request that the handler reads: the body stream."""

    def __init__(self, payload: Any) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x/actions/y")
        self.query_params: dict[str, str] = {}

    async def stream(self) -> Any:
        yield self._raw


async def _call(org: str, email: str, pid: str, action: str,
                payload: Any = None) -> tuple[int, Any]:
    """Call the real handler with *org* bound, as the auth layer binds it."""
    token = bind_tenant(org)
    try:
        found = await invoke.invoke_action(pid, action, request=_Request(payload),
                                           user=_user(org, email))
        return 200, found
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _define(org: str, pkg: str, name: str, fields: Any, rules: Any = None,
                  access: Any = None) -> store.StudioObject:
    async with tenant_session(org) as db:
        made = await store.define_object(db, OWNER, package_id=pkg, name=name, fields=fields)
        assert made is not None
        if rules:
            assert await store.set_constraints(db, made.id, rules, package_id=pkg)
        if access:
            assert await store.set_access(db, made.id, access, package_id=pkg)
    return made


async def _put(org: str, obj: store.StudioObject, *writes: Write) -> list[Any]:
    async with tenant_session(org) as db:
        return await store.apply_writes(db, OWNER, package_id=obj.package_id,
                                        writes=list(writes), viewer=AS_OWNER)


def _site(obj: store.StudioObject, name: str, keeper: str, parent: str | None) -> Write:
    data: dict[str, Any] = {"name": name, "keeper": keeper, "secret": f"s-{name}"}
    if parent is not None:
        data["parent"] = {"id": parent}
    return Write("create", obj.id, data=data)


async def _grow(org: str, obj: store.StudioObject) -> dict[str, str]:
    """The tree of the module docstring, one level for each batch."""
    ids: dict[str, str] = {}
    for level in (
        [("A", E1, None), ("G", E2, None)],
        [("B", E1, "A"), ("C", E2, "A"), ("H", E1, "G")],
        [("D", E1, "B"), ("F", E1, "C")],
    ):
        made = await _put(org, obj, *[_site(obj, n, k, ids.get(p) if p else None)
                                      for n, k, p in level])
        ids.update({n: r.id for (n, _k, _p), r in zip(level, made, strict=True)})
    return ids


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A: two packages of the owner. Org B: one package of a member with
    the owner's email. Each package holds a site tree."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    mid = f"stud18-{TAG}-{uuid.uuid4().hex[:6]}"
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b)

    async def setup() -> None:
        for key, org in (("pkg", w.a), ("pkg2", w.a), ("pkg_b", w.b)):
            async with tenant_session(org) as db:
                made = await packages_store.create_package(db, OWNER, _manifest(mid))
            setattr(w, key, made.id)
        w.site = await _define(w.a, w.pkg, "site", SITE_FIELDS, [SITE_TREE], SITE_ACCESS)
        w.lot = await _define(w.a, w.pkg, "lot", LOT_FIELDS, [SITE_TREE])
        w.flat = await _define(w.a, w.pkg, "flat", FLAT_FIELDS)
        w.ids = await _grow(w.a, w.site)
        w.site2 = await _define(w.a, w.pkg2, "site", SITE_FIELDS, [SITE_TREE])
        w.ids2 = await _grow(w.a, w.site2)
        w.site_b = await _define(w.b, w.pkg_b, "site", SITE_FIELDS, [SITE_TREE])
        w.ids_b = await _grow(w.b, w.site_b)

    _run(w, setup)
    yield w
    with p.admin_engine.begin() as c:
        ids = [w.pkg, w.pkg2, w.pkg_b]
        c.execute(text("DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM studio_records WHERE object_id IN "
                       "(SELECT id FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[])))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})


async def _tree(org: str, obj: store.StudioObject, viewer: Viewer,
                **kw: Any) -> store.TreePage | None:
    async with tenant_session(org) as db:
        return await store.read_tree(db, package_id=obj.package_id, object_id=obj.id,
                                     viewer=viewer, **kw)


def _shape(world: Any, page: store.TreePage) -> list[tuple[str, int, str | None]]:
    """Each node as (name, level, name of the parent)."""
    names = {v: k for k, v in world.ids.items()}
    return [(names[n.record.id], n.level, None if n.parent_id is None else names[n.parent_id])
            for n in page.nodes]


def _audits(world: Any, pid: str) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text("SELECT detail FROM app_audit WHERE app_id = CAST(:p AS uuid) "
                              "ORDER BY id"), {"p": pid}).scalars().all()
    return [r if isinstance(r, dict) else json.loads(r) for r in rows]


# -- the row rules of the store ---------------------------------------------------


def test_r8_a_viewer_reads_only_its_nodes_and_a_hidden_node_cuts_its_subtree(world) -> None:
    async def go() -> tuple[Any, Any, Any]:
        return (await _tree(world.a, world.site, AS_E1),
                await _tree(world.a, world.site, AS_HR),
                await _tree(world.a, world.site, AS_OWNER))

    mine, hr, owner = _run(world, go)
    # C is hidden from E1, so F under it is cut. G is hidden, so H is cut.
    assert _shape(world, mine) == [("A", 1, None), ("B", 2, "A"), ("D", 3, "B")]
    assert not mine.truncated
    # A hidden key leaves each node, and no node names a hidden record.
    assert all("secret" not in n.record.data for n in mine.nodes)
    seen = {n.record.id for n in mine.nodes} | {n.parent_id for n in mine.nodes}
    assert not seen & {world.ids[k] for k in ("C", "F", "G", "H")}
    # hr and the owner read the whole forest, with each key.
    for page in (hr, owner):
        assert sorted(_shape(world, page)) == sorted([
            ("A", 1, None), ("G", 1, None), ("B", 2, "A"), ("C", 2, "A"), ("H", 2, "G"),
            ("D", 3, "B"), ("F", 3, "C")])
        assert all(n.record.data["secret"].startswith("s-") for n in page.nodes)


def test_r8_truncated_counts_only_the_nodes_that_the_viewer_reads(world) -> None:
    async def go() -> tuple[Any, Any, Any]:
        return (await _tree(world.a, world.site, AS_E1, limit=3),
                await _tree(world.a, world.site, AS_E1, limit=2),
                await _tree(world.a, world.site, AS_HR, limit=3))

    three, two, hr = _run(world, go)
    # E1 reads 3 nodes, so a limit of 3 is not cut, though the object holds 7.
    assert len(three.nodes) == 3 and three.truncated is False
    assert len(two.nodes) == 2 and two.truncated is True
    assert len(hr.nodes) == 3 and hr.truncated is True


def test_r8_a_root_that_the_viewer_cannot_read_gives_no_node(world) -> None:
    async def go() -> list[Any]:
        return [await _tree(world.a, world.site, AS_E1, root_id=world.ids["C"]),
                await _tree(world.a, world.site, AS_E1, root_id=world.ids["G"]),
                await _tree(world.a, world.site, AS_E1, root_id=world.ids["F"]),
                await _tree(world.a, world.site, AS_E1, root_id=world.ids["B"])]

    hidden_c, hidden_g, f, b = _run(world, go)
    assert hidden_c.nodes == () and hidden_c.truncated is False
    assert hidden_g.nodes == () and hidden_g.truncated is False
    # A record that the viewer reads is a root of its own read, and names no
    # parent. Its data holds the parent id, as a get of it does (C8a).
    assert _shape(world, f) == [("F", 1, None)]
    assert _shape(world, b) == [("B", 1, None), ("D", 2, "B")]


def test_r8_a_viewer_with_no_grant_reads_no_node(world) -> None:
    page = _run(world, lambda: _tree(world.a, world.site, AS_STRANGER))
    assert page.nodes == () and page.truncated is False


def test_r8_a_parent_field_hidden_from_the_viewer_refuses_the_read(world) -> None:
    async def go() -> QueryError:
        with pytest.raises(QueryError) as caught:
            await _tree(world.a, world.site, AS_HIDER)
        return caught.value

    refused = _run(world, go)
    assert refused.code == "query_op_not_allowed" and refused.field == "parent"


def test_r8_the_store_reads_no_node_of_another_org_or_package(world) -> None:
    async def go() -> list[Any]:
        return [await _tree(world.b, world.site, AS_OWNER),
                await _tree(world.a, world.site_b, AS_OWNER),
                await _tree(world.a, world.site, AS_OWNER, root_id=world.ids_b["A"]),
                await _tree(world.a, world.site, AS_OWNER, root_id=world.ids2["A"])]

    other_org, b_object, b_root, pkg2_root = _run(world, go)
    assert other_org is None and b_object is None
    assert b_root.nodes == () and pkg2_root.nodes == ()


# -- the door ----------------------------------------------------------------------


def test_r8_the_owner_reads_the_tree_through_the_door_with_one_audit_row(world) -> None:
    status, got = _run(world, lambda: _call(world.a, OWNER, world.pkg, "sites"))
    assert status == 200 and set(got) == {"nodes", "truncated"}
    assert got["truncated"] is False and len(got["nodes"]) == 7
    assert {n["record"]["id"] for n in got["nodes"]} == set(world.ids.values())
    assert all(set(n) == {"record", "level", "parent_id"} for n in got["nodes"])
    [row] = _audits(world, world.pkg)
    assert (row["uses"], row["class"], row["outcome"]) == ("object:site.tree", "A", "ran")


def test_r8_a_root_of_org_b_another_package_or_nothing_is_404(world) -> None:
    roots = [world.ids_b["A"], world.ids2["A"], str(uuid.uuid4())]

    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, OWNER, world.pkg, "sites", {"root_id": r}) for r in roots]

    answers = _run(world, go)
    # One answer for each: no empty tree that proves a record exists elsewhere.
    assert answers == [(404, NOT_FOUND)] * 3
    assert [r["outcome"] for r in _audits(world, world.pkg)] == ["not_found"] * 3


def test_r8_org_b_and_another_member_get_404_on_the_tree(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.b, OWNER, world.pkg, "sites"),
                await _call(world.a, BEN, world.pkg, "sites"),
                await _call(world.b, OWNER, world.pkg_b, "sites",
                            {"root_id": world.ids["A"]})]

    b_call, ben, b_guess = _run(world, go)
    assert b_call == (404, NOT_FOUND) and ben == (404, NOT_FOUND)
    # Org B guesses a root id of org A on its own package: not found.
    assert b_guess == (404, NOT_FOUND)


def test_r8_the_depth_cap_holds_and_a_bad_body_is_422(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        bodies = [{"depth": 2}, {"depth": 20}, {"depth": 21}, {"depth": 0},
                  {"depth": True}, {"limit": 2001}, {"root_id": "x"}]
        return [await _call(world.a, OWNER, world.pkg, "sites", b) for b in bodies]

    two, twenty, *bad = _run(world, go)
    assert two[0] == 200 and max(n["level"] for n in two[1]["nodes"]) == 2
    assert len(two[1]["nodes"]) == 5
    assert twenty[0] == 200 and len(twenty[1]["nodes"]) == 7
    assert [(s, d["code"]) for s, d in bad] == [(422, "query_value_invalid")] * 5


def test_r8_an_object_with_no_hierarchy_is_a_plain_422(world) -> None:
    status, detail = _run(world, lambda: _call(world.a, OWNER, world.pkg, "flats"))
    assert status == 422 and detail["code"] == "query_op_not_allowed"
    assert "no hierarchy" in detail["message"]


def test_r8_the_switch_off_is_404_and_reads_nothing(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    assert _run(world, lambda: _call(world.a, OWNER, world.pkg, "sites")) == (404, NOT_FOUND)
    assert _audits(world, world.pkg) == []


async def _plan_report(org: str, pkg: str, oid: str) -> tuple[str, int]:
    """The plan of the tree read, its buffer count and the facts that decide it (task 3308).

    The buffer count is the fence: it is the same on a fast box and on a slow CI runner,
    and a lookup that scans a table row by row shows in it at once. The wrong plan read
    1,363,375 buffers for 2,000 nodes on the runner; the key lookups read about 20,000
    (measured on the box: 20,046 stale, 20,043 never analyzed)."""
    binds = {"oid": oid, "pid": pkg, "fk": "parent", "depth": 20, "limit": 2000}
    try:
        async with tenant_session(org) as db:
            await db.execute(text("SET LOCAL statement_timeout = 60000"))
            facts = [str(r) for r in (await db.execute(text(
                "SELECT current_setting('server_version'), current_setting('jit'), "
                "current_setting('work_mem'), current_setting('shared_buffers'), "
                "current_setting('random_page_cost'), current_setting('effective_cache_size')"
            ))).all()]
            facts += [str(r) for r in (await db.execute(text(
                "SELECT relname, reltuples, relpages FROM pg_class WHERE relname IN "
                "('studio_records','studio_record_hot')"))).all()]
            plan = [r[0] for r in (await db.execute(text(
                "EXPLAIN (ANALYZE, BUFFERS) " + store._TREE_ALL_SQL), binds)).all()]
        top = next((x for x in plan if "Buffers:" in x), "")
        m = re.search(r"Buffers: shared(?: hit=(\d+))?(?: read=(\d+))?", top)
        buffers = int(m.group(1) or 0) + int(m.group(2) or 0) if m else -1
        return "\n".join(facts) + "\n" + "\n".join(plan), buffers
    except Exception as exc:
        return f"plan report failed: {type(exc).__name__}: {str(exc)[:200]}", -1


#: The most buffers that the read of 2,000 nodes may touch. The key lookups read about
#: 20,000; the plan that failed on CI read 1,363,375.
MAX_TREE_READ_BUFFERS = 100_000


@pytest.mark.parametrize("stats", ["stale", "never_analyzed"])
def test_r8_two_thousand_nodes_answer_in_one_door_call(world, stats: str) -> None:
    """One root and 1,999 children: 2,000 nodes are not cut, and one more is."""

    def prepare_stats() -> None:
        # STU-C18 finding (task 3308): a table that is filled in bulk keeps the planner
        # statistics of the time before the fill until autovacuum runs. The statements of
        # the tree read must not depend on them, and the C18 write check must not for a
        # write of a leaf or a small subtree (a write that moves a node with a LARGE subtree
        # is the residual named at `_TREE_KIDS`). Two states:
        # "stale" analyzes both tables while they hold only the fixture rows, and
        # "never_analyzed" wipes every statistic (pg_statistic and reltuples). In both,
        # autovacuum is off and the test fills 2,000 nodes through the real writes.
        with world.p.admin_engine.begin() as c:
            for t in ("studio_records", "studio_record_hot"):
                c.execute(text(f"ALTER TABLE {t} SET (autovacuum_enabled = false)"))
                if stats == "never_analyzed":
                    c.execute(text("DELETE FROM pg_statistic WHERE starelid = CAST(:t AS regclass)"),
                              {"t": t})
                    c.execute(text("UPDATE pg_class SET reltuples = -1, relpages = 0 "
                                   "WHERE oid = CAST(:t AS regclass)"), {"t": t})
        if stats == "stale":
            with world.p.admin_engine.begin() as c:
                c.execute(text("ANALYZE studio_records"))
                c.execute(text("ANALYZE studio_record_hot"))

    def stats_back() -> None:
        with world.p.admin_engine.begin() as c:
            for t in ("studio_records", "studio_record_hot"):
                c.execute(text(f"ALTER TABLE {t} RESET (autovacuum_enabled)"))

    async def build() -> str:
        [root] = await _put(world.a, world.lot, Write("create", world.lot.id,
                                                      data={"name": "root"}))
        for start in range(0, 1999, 50):
            await _put(world.a, world.lot, *[
                Write("create", world.lot.id, data={"name": f"n{i}", "parent": {"id": root.id}})
                for i in range(start, min(start + 50, 1999))])
        return root.id

    async def read() -> tuple[tuple[int, Any], int]:
        seen: list[str] = []
        eng = get_engine().sync_engine

        def _on(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
            seen.append(statement)

        event.listen(eng, "before_cursor_execute", _on)
        try:
            got = await _call(world.a, OWNER, world.pkg, "lots")
        finally:
            event.remove(eng, "before_cursor_execute", _on)
        return got, len([s for s in seen if "WITH RECURSIVE" in s])

    prepare_stats()
    try:
        root = _run(world, build)
        (status, got), walks = _run(world, read)
        report, buffers = _run(world, lambda: _plan_report(world.a, world.pkg, world.lot.id))
        if status != 200 or not 0 <= buffers < MAX_TREE_READ_BUFFERS:
            raise AssertionError(f"status {status}, read touched {buffers} buffers "
                                 f"(limit {MAX_TREE_READ_BUFFERS})\n{report}")
        print(f"tree read buffers ({stats}): {buffers}")
        assert walks == 1, got
        assert len(got["nodes"]) == 2000 and got["truncated"] is False
        _run(world, lambda: _put(world.a, world.lot, Write(
            "create", world.lot.id, data={"name": "one more", "parent": {"id": root}})))
        (status, got), walks = _run(world, read)
        assert status == 200 and walks == 1, got
        assert len(got["nodes"]) == 2000 and got["truncated"] is True
    finally:
        stats_back()


#: The most buffers that the C18 check of a move of a 2,000-node subtree may touch. The lookup
#: per step took the wrong index under stale statistics and read 102,010 (0.8 s on the box);
#: the child-edge join reads 55 (measured, stale and never analyzed).
MAX_TREE_CHECK_BUFFERS = 5_000


async def _check_buffers(org: str, pkg: str, oid: str, rid: str) -> tuple[str, int]:
    binds = {"oid": oid, "pid": pkg, "fk": "parent", "rids": [rid], "cap": 20}
    try:
        async with tenant_session(org) as db:
            await db.execute(text("SET LOCAL statement_timeout = 60000"))
            plan = [r[0] for r in (await db.execute(text(
                "EXPLAIN (ANALYZE, BUFFERS) " + store._TREE_CHECK_SQL), binds)).all()]
        top = next((x for x in plan if "Buffers:" in x), "")
        m = re.search(r"Buffers: shared(?: hit=(\d+))?(?: read=(\d+))?", top)
        return "\n".join(plan), (int(m.group(1) or 0) + int(m.group(2) or 0) if m else -1)
    except Exception as exc:
        return f"check plan failed: {type(exc).__name__}: {str(exc)[:200]}", -1


@pytest.mark.parametrize("stats", ["stale", "never_analyzed"])
def test_r8_a_write_that_moves_a_2000_node_subtree_stays_cheap(world, stats: str) -> None:
    """Task 3346: moving the root of a 2,000-node tree under another record walks the whole
    subtree in the C18 check. The walk must not take a lookup per step (the wrong index under
    stale statistics): the buffer count of the check is the fence, as for the read.    The fence counts buffers: it does not see a CPU-quadratic join over the materialised
    edge set (a nested loop reads no extra buffers). At 2,000 edges that is cheap; the
    100,000-edge case stays open in task 3346.
    """
    with world.p.admin_engine.begin() as c:
        for t in ("studio_records", "studio_record_hot"):
            c.execute(text(f"ALTER TABLE {t} SET (autovacuum_enabled = false)"))
            if stats == "never_analyzed":
                c.execute(text("DELETE FROM pg_statistic WHERE starelid = CAST(:t AS regclass)"),
                          {"t": t})
                c.execute(text("UPDATE pg_class SET reltuples = -1, relpages = 0 "
                               "WHERE oid = CAST(:t AS regclass)"), {"t": t})
    if stats == "stale":
        with world.p.admin_engine.begin() as c:
            c.execute(text("ANALYZE studio_records"))
            c.execute(text("ANALYZE studio_record_hot"))
    ids: dict[str, str] = {}

    async def build() -> None:
        [root] = await _put(world.a, world.lot, Write("create", world.lot.id,
                                                      data={"name": "root"}))
        [new] = await _put(world.a, world.lot, Write("create", world.lot.id,
                                                     data={"name": "new"}))
        ids["root"], ids["new"] = root.id, new.id
        for start in range(0, 1999, 50):
            await _put(world.a, world.lot, *[
                Write("create", world.lot.id, data={"name": f"n{i}", "parent": {"id": root.id}})
                for i in range(start, min(start + 50, 1999))])

    async def move() -> None:
        await _put(world.a, world.lot, Write("update", world.lot.id, record_id=ids["root"],
                                             data={"parent": {"id": ids["new"]}}))

    try:
        _run(world, build)
        report, buffers = _run(world, lambda: _check_buffers(
            world.a, world.pkg, world.lot.id, ids["root"]))
        print(f"tree check buffers ({stats}): {buffers}")
        if not 0 <= buffers < MAX_TREE_CHECK_BUFFERS:
            raise AssertionError(f"the check touched {buffers} buffers "
                                 f"(limit {MAX_TREE_CHECK_BUFFERS})\n{report}")
        _run(world, move)  # the write passes the C18 check inside its deadline
    finally:
        with world.p.admin_engine.begin() as c:
            for t in ("studio_records", "studio_record_hot"):
                c.execute(text(f"ALTER TABLE {t} RESET (autovacuum_enabled)"))


#: The most buffers that the SCOPED read of 2,000 nodes may touch. The plain read touches about
#: 20,000, and the scoped read on the box 18,217 (stale) and 20,146 (never analyzed). A bound
#: that CI passes proves the runner's plan is of the same order; a failure prints the plan.
MAX_SCOPED_READ_BUFFERS = 50_000


class _Recording:
    """A session that remembers each recursive statement and its binds."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    async def execute(self, stmt: Any, params: Any = None, *a: Any, **kw: Any) -> Any:
        sql = str(stmt)
        if "WITH RECURSIVE" in sql:
            self.calls.append((sql, dict(params or {})))
        return await self._db.execute(stmt, params, *a, **kw)


async def _scoped_read(org: str, obj: store.StudioObject, viewer: Viewer) -> tuple[Any, int, str]:
    """Read the tree as *viewer* through the store, then take EXPLAIN (ANALYZE, BUFFERS) of
    the recursive statement that the read ran, with its binds (task 3375)."""
    async with tenant_session(org) as db:
        rec = _Recording(db)
        page = await store.read_tree(rec, package_id=obj.package_id, object_id=obj.id,
                                     viewer=viewer, limit=2000)
        (sql, binds), = rec.calls
    try:
        async with tenant_session(org) as db:
            await db.execute(text("SET LOCAL statement_timeout = 60000"))
            plan = [r[0] for r in (await db.execute(text(
                "EXPLAIN (ANALYZE, BUFFERS) " + sql), binds)).all()]
        top = next((x for x in plan if "Buffers:" in x), "")
        m = re.search(r"Buffers: shared(?: hit=(\d+))?(?: read=(\d+))?", top)
        return page, (int(m.group(1) or 0) + int(m.group(2) or 0) if m else -1), "\n".join(plan)
    except Exception as exc:
        return page, -1, f"plan failed: {type(exc).__name__}: {str(exc)[:200]}"


@pytest.mark.parametrize("stats", ["stale", "never_analyzed"])
def test_r8_the_scoped_read_of_2000_nodes_stays_cheap(world, stats: str) -> None:
    """Task 3375: a viewer with a row rule (a keeper who reads the nodes with keeper = me)
    reads 2,000 nodes. The scoped statement adds the row rule on each root and on each
    step: its plan must stay near the plain read (about 20,000 buffers), in both
    statistics states, as on the runner."""
    with world.p.admin_engine.begin() as c:
        for t in ("studio_records", "studio_record_hot"):
            c.execute(text(f"ALTER TABLE {t} SET (autovacuum_enabled = false)"))
            if stats == "never_analyzed":
                c.execute(text("DELETE FROM pg_statistic WHERE starelid = CAST(:t AS regclass)"),
                          {"t": t})
                c.execute(text("UPDATE pg_class SET reltuples = -1, relpages = 0 "
                               "WHERE oid = CAST(:t AS regclass)"), {"t": t})
    if stats == "stale":
        with world.p.admin_engine.begin() as c:
            c.execute(text("ANALYZE studio_records"))
            c.execute(text("ANALYZE studio_record_hot"))

    async def build() -> None:
        [root] = await _put(world.a, world.site2, _site(world.site2, "bigroot", E1, None))
        for start in range(0, 1999, 50):
            await _put(world.a, world.site2, *[
                Write("create", world.site2.id, data={
                    "name": f"n{i}", "keeper": E1, "secret": f"s{i}", "parent": {"id": root.id}})
                for i in range(start, min(start + 50, 1999))])

    try:
        _run(world, build)
        page, buffers, report = _run(world, lambda: _scoped_read(world.a, world.site2, AS_E1))
        print(f"scoped tree read buffers ({stats}): {buffers}")
        if len(page.nodes) < 2000 or not 0 <= buffers < MAX_SCOPED_READ_BUFFERS:
            raise AssertionError(f"read {len(page.nodes)} nodes and touched {buffers} buffers "
                                 f"(limit {MAX_SCOPED_READ_BUFFERS})\n{report}")
    finally:
        with world.p.admin_engine.begin() as c:
            for t in ("studio_records", "studio_record_hot"):
                c.execute(text(f"ALTER TABLE {t} RESET (autovacuum_enabled)"))


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed."""
    assert "tests/unit/test_studio_tree_door_r8.py" in r8_lists.must_run()
