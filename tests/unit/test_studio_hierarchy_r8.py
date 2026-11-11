"""WS-STU STU-C18: the hierarchy on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` section 14, row C18. Code: the rule
kind ``hierarchy`` in ``acb_common/studio_rules.py`` (the pure half is in
``test_studio_hierarchy.py``), and the hierarchy section of
``gateway/studio/objects_store.py`` (``_check_trees``, ``_TREE_CHECK_SQL`` and
``read_tree``).

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_hierarchy_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**Acceptance (C18).** The store refuses a write that makes a cycle, and a
write past depth 20. A tree of depth 10 reads in one query.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``objects_store._run_steps``: remove the call of ``_check_trees``.
  ``test_r8_a_write_that_makes_a_cycle_is_refused_and_writes_nothing`` goes red.
* M2. ``studio_rules.check_tree``: compare ``depth`` with 21.
  ``test_r8_a_write_past_depth_twenty_is_refused`` goes red.
* M3. ``objects_store._check_trees``: drop the levels below the record from
  the depth. ``test_r8_a_move_of_a_subtree_counts_the_levels_below_it`` goes red.
* M4. ``objects_store._tree_read_sql``: drop ``NOT h.record_id = ANY(t.path)``.
  ``test_r8_a_read_stops_at_an_old_cycle`` goes red (it runs to the depth cap
  and reads a record twice).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_rules as sr
from acb_common.db import get_engine, tenant_session
from acb_common.studio_fields import FieldError
from acb_common.studio_rules import ConstraintError, TreeViolation
from gateway.studio import objects_store as store
from gateway.studio.objects_store import BatchError, Write
from sqlalchemy import event, text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc18.test"

_SITE: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "parent", "type": "relation", "to": "site", "hot": True},
]
TREE = {"name": "tree", "kind": "hierarchy", "field": "parent"}

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc18', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc18", "name": "stuc18", "purpose": "The C18 R8 suite."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    """Three packages: two of org A and one of org B."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    pkgs: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("a2", p.org_a), ("b", p.org_b)):
            pkgs[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc18-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=pkgs["a"], pkg_a2=pkgs["a2"],
                          pkg_b=pkgs["b"])
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(pkgs.values())})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc18-{TAG}-{run}-%"})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _site(org: str, pkg: str, rules: list[dict[str, Any]] | None = None) -> store.StudioObject:
    async with tenant_session(org) as db:
        obj = await store.define_object(db, ANA, package_id=pkg, name="site", fields=_SITE)
        assert obj is not None
        done = await store.set_constraints(db, obj.id, [TREE] if rules is None else rules,
                                           package_id=pkg)
    assert done is not None
    return done


async def _put(org: str, obj: store.StudioObject, *writes: Write) -> list[Any]:
    async with tenant_session(org) as db:
        return await store.apply_writes(db, ANA, package_id=obj.package_id, writes=list(writes))


async def _refused(org: str, obj: store.StudioObject, *writes: Write) -> BatchError:
    with pytest.raises(BatchError) as caught:
        await _put(org, obj, *writes)
    return caught.value


def _under(obj: store.StudioObject, parent: str | None, name: str = "n") -> Write:
    data: dict[str, Any] = {"name": name}
    if parent is not None:
        data["parent"] = {"id": parent}
    return Write("create", obj.id, data=data)


def _move(obj: store.StudioObject, rid: str, parent: str | None) -> Write:
    return Write("update", obj.id, rid, {"parent": None if parent is None else {"id": parent}})


async def _chain(org: str, obj: store.StudioObject, levels: int,
                 top: str | None = None) -> list[str]:
    """A chain of *levels* records, the first under *top*. One batch for each."""
    ids: list[str] = []
    parent = top
    for i in range(levels):
        rec = (await _put(org, obj, _under(obj, parent, f"l{i + 1}")))[0]
        ids.append(rec.id)
        parent = rec.id
    return ids


def _parent(world: Any, rid: str) -> Any:
    with world.p.admin_engine.connect() as c:
        raw = c.execute(text("SELECT data FROM studio_records WHERE id = CAST(:r AS uuid)"),
                        {"r": rid}).scalar_one()
    data = raw if isinstance(raw, dict) else json.loads(raw)
    return (data.get("parent") or {}).get("id")


def _count(world: Any, obj: store.StudioObject) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM studio_records "
                                  "WHERE object_id = CAST(:o AS uuid)"), {"o": obj.id}).scalar_one())


# -- the declaration ---------------------------------------------------------------


def test_r8_a_hierarchy_needs_a_hot_parent_field_and_keeps_it_hot(world) -> None:
    async def go() -> tuple[str, str]:
        obj = await _site(world.a, world.pkg_a)
        async with tenant_session(world.a) as db:
            with pytest.raises(store.ObjectStoreError) as cold:
                await store.set_field_hot(db, obj.id, "parent", False)
        async with tenant_session(world.a) as db:
            other = await store.define_object(db, ANA, package_id=world.pkg_a2, name="site",
                                              fields=[{**f, "hot": False} for f in _SITE])
            assert other is not None
            with pytest.raises(ConstraintError) as refused:
                await store.set_constraints(db, other.id, [TREE], package_id=world.pkg_a2)
        return str(cold.value), refused.value.message

    cold, refused = _go(world, go)
    assert "tree" in cold
    assert "must be hot" in refused


# -- the cycle -----------------------------------------------------------------------


def test_r8_a_write_that_makes_a_cycle_is_refused_and_writes_nothing(world) -> None:
    async def go() -> tuple[list[str], BatchError]:
        obj = await _site(world.a, world.pkg_a)
        ids = await _chain(world.a, obj, 3)
        err = await _refused(world.a, obj, _move(obj, ids[0], ids[2]))
        return ids, err

    ids, err = _go(world, go)
    assert isinstance(err.cause, TreeViolation) and err.cause.code == sr.TREE_CYCLE
    assert err.cause.field == "parent" and err.index == 0
    assert _parent(world, ids[0]) is None


def test_r8_a_record_cannot_be_its_own_parent(world) -> None:
    async def go() -> tuple[str, BatchError]:
        obj = await _site(world.a, world.pkg_a)
        (rid,) = await _chain(world.a, obj, 1)
        return rid, await _refused(world.a, obj, _move(obj, rid, rid))

    rid, err = _go(world, go)
    assert isinstance(err.cause, TreeViolation) and err.cause.code == sr.TREE_CYCLE
    assert _parent(world, rid) is None


def test_r8_a_cycle_of_two_writes_in_one_batch_is_refused(world) -> None:
    """Each write alone is fine. The net of the batch is a cycle."""
    async def go() -> tuple[list[str], BatchError]:
        obj = await _site(world.a, world.pkg_a)
        a = (await _put(world.a, obj, _under(obj, None, "a")))[0].id
        b = (await _put(world.a, obj, _under(obj, None, "b")))[0].id
        return [a, b], await _refused(world.a, obj, _move(obj, a, b), _move(obj, b, a))

    (a, b), err = _go(world, go)
    assert isinstance(err.cause, TreeViolation) and err.cause.code == sr.TREE_CYCLE
    assert _parent(world, a) is None and _parent(world, b) is None


def test_r8_a_move_to_another_branch_passes(world) -> None:
    async def go() -> tuple[list[str], str]:
        obj = await _site(world.a, world.pkg_a)
        ids = await _chain(world.a, obj, 3)
        other = (await _put(world.a, obj, _under(obj, ids[0], "side")))[0].id
        await _put(world.a, obj, _move(obj, ids[2], other))
        return ids, other

    ids, other = _go(world, go)
    assert _parent(world, ids[2]) == other


# -- the depth -----------------------------------------------------------------------


def test_r8_a_write_past_depth_twenty_is_refused(world) -> None:
    async def go() -> tuple[list[str], BatchError, int]:
        obj = await _site(world.a, world.pkg_a)
        ids = await _chain(world.a, obj, 20)
        err = await _refused(world.a, obj, _under(obj, ids[-1], "l21"))
        return ids, err, _count(world, obj)

    ids, err, count = _go(world, go)
    assert len(ids) == 20
    assert isinstance(err.cause, TreeViolation) and err.cause.code == sr.TREE_TOO_DEEP
    assert "20 levels" in err.cause.message
    assert count == 20


def test_r8_a_move_of_a_subtree_counts_the_levels_below_it(world) -> None:
    """A subtree of 3 levels under level 18 makes 21 levels, so it is refused.
    The same subtree under level 17 makes 20, so it passes."""
    async def go() -> tuple[BatchError, list[str], list[str]]:
        obj = await _site(world.a, world.pkg_a)
        deep = await _chain(world.a, obj, 18)
        sub = await _chain(world.a, obj, 3)
        err = await _refused(world.a, obj, _move(obj, sub[0], deep[17]))
        await _put(world.a, obj, _move(obj, sub[0], deep[16]))
        return err, deep, sub

    err, deep, sub = _go(world, go)
    assert isinstance(err.cause, TreeViolation) and err.cause.code == sr.TREE_TOO_DEEP
    assert _parent(world, sub[0]) == deep[16]


# -- the parent ----------------------------------------------------------------------


def test_r8_a_parent_must_be_a_record_of_this_object_in_this_org(world) -> None:
    async def go() -> list[BatchError]:
        obj = await _site(world.a, world.pkg_a)
        other = await _site(world.a, world.pkg_a2)
        theirs = await _site(world.b, world.pkg_b)
        (in_other,) = await _chain(world.a, other, 1)
        (in_b,) = await _chain(world.b, theirs, 1)
        return [await _refused(world.a, obj, _under(obj, ref)) for ref in
                (in_other, in_b, str(uuid.uuid4()), "not-an-id")]

    errs = _go(world, go)
    for err in errs:
        assert isinstance(err.cause, FieldError) and err.cause.field == "parent", err.cause


def test_r8_an_edit_that_keeps_the_parent_runs_no_walk(world) -> None:
    async def go() -> int:
        obj = await _site(world.a, world.pkg_a)
        ids = await _chain(world.a, obj, 2)
        async with tenant_session(world.a) as db:
            seen: list[str] = []
            eng = get_engine().sync_engine

            def _on(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
                seen.append(statement)

            event.listen(eng, "before_cursor_execute", _on)
            try:
                await store.apply_writes(db, ANA, package_id=obj.package_id, writes=[
                    Write("update", obj.id, ids[1], {"name": "renamed"})])
            finally:
                event.remove(eng, "before_cursor_execute", _on)
        return sum("WITH RECURSIVE" in s for s in seen)

    assert _go(world, go) == 0


# -- the read ------------------------------------------------------------------------


def _tree_of_depth_ten(org: str, obj: store.StudioObject) -> Any:
    """A chain of 10 levels, with a second child on each of the first 9."""
    async def build() -> tuple[list[str], list[str]]:
        spine = await _chain(org, obj, 10)
        side = [(await _put(org, obj, _under(obj, rid, f"s{i}")))[0].id
                for i, rid in enumerate(spine[:9])]
        return spine, side
    return build()


def test_r8_a_tree_of_depth_ten_reads_in_one_query(world) -> None:
    async def go() -> tuple[list[str], list[str], store.TreePage, list[str]]:
        obj = await _site(world.a, world.pkg_a)
        spine, side = await _tree_of_depth_ten(world.a, obj)
        async with tenant_session(world.a) as db:
            seen: list[str] = []
            eng = get_engine().sync_engine

            def _on(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
                seen.append(statement)

            event.listen(eng, "before_cursor_execute", _on)
            try:
                page = await store.read_tree(db, package_id=obj.package_id, object_id=obj.id)
            finally:
                event.remove(eng, "before_cursor_execute", _on)
        assert page is not None
        return spine, side, page, [s for s in seen if "studio_records" in s]

    spine, side, page, reads = _go(world, go)
    assert len(reads) == 1 and "WITH RECURSIVE" in reads[0]
    assert not page.truncated and len(page.nodes) == 19
    levels = {n.record.id: n.level for n in page.nodes}
    parents = {n.record.id: n.parent_id for n in page.nodes}
    assert [levels[r] for r in spine] == list(range(1, 11))
    assert [levels[r] for r in side] == list(range(2, 11))
    assert parents[spine[0]] is None
    assert all(parents[spine[i]] == spine[i - 1] for i in range(1, 10))
    assert [n.level for n in page.nodes] == sorted(n.level for n in page.nodes)


def test_r8_a_read_takes_a_root_a_depth_and_a_limit(world) -> None:
    async def go() -> tuple[list[str], Any, Any, Any, Any]:
        obj = await _site(world.a, world.pkg_a)
        spine, _side = await _tree_of_depth_ten(world.a, obj)
        async with tenant_session(world.a) as db:
            sub = await store.read_tree(db, package_id=obj.package_id, object_id=obj.id,
                                        root_id=spine[7])
            shallow = await store.read_tree(db, package_id=obj.package_id, object_id=obj.id,
                                            depth=2)
            cut = await store.read_tree(db, package_id=obj.package_id, object_id=obj.id,
                                        limit=5)
            none = await store.read_tree(db, package_id=obj.package_id, object_id=obj.id,
                                         root_id=str(uuid.uuid4()))
        return spine, sub, shallow, cut, none

    spine, sub, shallow, cut, none = _go(world, go)
    # The read from level 8 holds 1 root, then 2 records, then 2 records.
    assert [n.record.id for n in sub.nodes if n.level == 1] == [spine[7]]
    assert len(sub.nodes) == 5 and max(n.level for n in sub.nodes) == 3
    assert len(shallow.nodes) == 3 and max(n.level for n in shallow.nodes) == 2
    assert len(cut.nodes) == 5 and cut.truncated
    assert none.nodes == () and not none.truncated


def test_r8_a_read_of_another_org_or_package_finds_nothing(world) -> None:
    async def go() -> tuple[Any, Any]:
        obj = await _site(world.a, world.pkg_a)
        await _chain(world.a, obj, 2)
        async with tenant_session(world.b) as db:
            theirs = await store.read_tree(db, package_id=obj.package_id, object_id=obj.id)
        async with tenant_session(world.a) as db:
            wrong = await store.read_tree(db, package_id=world.pkg_a2, object_id=obj.id)
        return theirs, wrong

    assert _go(world, go) == (None, None)


def test_r8_a_child_of_a_deleted_parent_reads_as_a_root(world) -> None:
    async def go() -> tuple[list[str], Any]:
        obj = await _site(world.a, world.pkg_a)
        ids = await _chain(world.a, obj, 3)
        await _put(world.a, obj, Write("delete", obj.id, ids[0]))
        async with tenant_session(world.a) as db:
            return ids, await store.read_tree(db, package_id=obj.package_id, object_id=obj.id)

    ids, page = _go(world, go)
    assert [(n.record.id, n.level) for n in page.nodes] == [(ids[1], 1), (ids[2], 2)]


def test_r8_a_read_stops_at_an_old_cycle(world) -> None:
    """Raw SQL skips the store, so an old cycle can exist. A read from a record
    of the cycle reads each record once."""
    async def go() -> tuple[list[str], Any]:
        obj = await _site(world.a, world.pkg_a)
        ids = await _chain(world.a, obj, 3)
        # The root takes the last record as its parent, so the three make a cycle.
        with world.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO studio_record_hot (organization_id, object_id, record_id, field_key, "
                "v_text) VALUES (CAST(:o AS uuid), CAST(:ob AS uuid), CAST(:r AS uuid), 'parent', :p) "
                "ON CONFLICT (organization_id, record_id, field_key) DO UPDATE SET v_text = :p"),
                {"o": world.a, "ob": obj.id, "r": ids[0], "p": ids[2]})
        async with tenant_session(world.a) as db:
            return ids, await store.read_tree(db, package_id=obj.package_id, object_id=obj.id,
                                              root_id=ids[0])

    ids, page = _go(world, go)
    assert [n.record.id for n in page.nodes] == ids


def test_r8_a_read_of_an_object_with_no_hierarchy_is_refused(world) -> None:
    async def go() -> str:
        obj = await _site(world.a, world.pkg_a, rules=[])
        async with tenant_session(world.a) as db:
            with pytest.raises(store.ObjectStoreError) as caught:
                await store.read_tree(db, package_id=obj.package_id, object_id=obj.id)
        return str(caught.value)

    assert "no hierarchy" in _go(world, go)


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+tests/unit/test_studio_hierarchy_r8\.py \\?$", workflow, re.M)
