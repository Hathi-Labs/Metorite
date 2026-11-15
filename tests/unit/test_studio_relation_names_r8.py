"""WS-STU REL-R1: the names of a relation to an object of the same app, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` section 14, row ``REL-R1``. Code:
``objects_store.record_titles`` (the one reader) and ``invoke._relation_names``
(the door). The title rule is ``studio_fields.title_key_of`` and ``record_title``.
The pure half is ``test_studio_relation_names.py``. The door answer is in
``test_studio_action_door_r8.py``.

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_relation_names_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**The world.** A ``team`` has a ``name`` (the title), a ``lead`` (a hot person
field) and ``notes``. Role ``all`` reads every team. Role ``lead`` reads the
teams where ``lead = $me``. Role ``blind`` reads every team, and ``name`` is
hidden from it. A ``task`` holds a relation ``team`` to the team.

**Acceptance (REL-R1).** The owner reads the names of a page of cells in a
constant count of statements. A viewer under a row rule gets None for a team
that the rule hides. A hidden title gives None. A missing id, a hidden id and
the id of another org give the same answer. A non-UUID gives None and no
statement. The 200 first ids resolve and the rest give None. A viewer of an
install whose version does not declare the target gets None for each id.

**The mutations (R7).** Commit before each, and restore with git after.
Each result was run on 2026-11-15.

* M1. ``record_titles``: build the SQL with ``_GET_RECORDS_SQL`` always (no row
  rule). ``test_r8_a_row_rule_on_the_target_hides_a_record_and_shows_the_other`` goes red.
* M2. ``record_titles``: build the view with ``Viewer(<the owner>)`` (read as
  the owner). The same test, and the hide test, go red.
* M3. ``record_titles``: answer the id of a hidden record as text. The test of
  the identical answer goes red.
* M4. ``_GET_RECORDS_SQL``: drop the org bind. The pure fence
  ``test_each_statement_of_the_row_rules_names_the_org_and_the_package`` goes
  red. RLS still binds the statement here, so no R8 case turns red (R5, two layers).
* M5. ``_relation_names``: skip ``_installed_object`` on the target. The install
  test goes red.
* M6. ``record_titles``: one statement for each id. The statement count test goes red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import get_engine, tenant_session
from acb_common.studio_access import Viewer
from gateway.package_host import viewer as package_viewer
from gateway.routes.studio import invoke
from gateway.studio import objects_store as store
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
OWNER = f"owner.{TAG}@rel.test"
L1, L2 = (f"l{i}.{TAG}@rel.test" for i in (1, 2))
AS_OWNER = Viewer(OWNER)
AS_ALL = Viewer(f"all.{TAG}@rel.test", frozenset({"all"}))
AS_L1 = Viewer(L1, frozenset({"lead"}))
AS_BLIND = Viewer(f"blind.{TAG}@rel.test", frozenset({"blind"}))
AS_NOBODY = Viewer(f"nobody.{TAG}@rel.test", frozenset({"intern"}))

TEAM_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "lead", "type": "person", "hot": True},
    {"key": "notes", "type": "text"},
]
TEAM_ACCESS: list[dict[str, Any]] = [
    {"role": "all", "read": "all"},
    {"role": "lead", "read": {"where": "lead = $me"}},
    {"role": "blind", "read": "all", "hide": ["name"]},
]
TASK_FIELDS: list[dict[str, Any]] = [
    {"key": "title", "type": "text"},
    {"key": "team", "type": "relation", "to": "team"},
    {"key": "helpers", "type": "relation", "to": "team", "many": True},
    {"key": "company", "type": "relation", "to": "crm.company"},
]

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'rel', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps({"studio": 1, "id": "rel", "name": "rel", "purpose": "REL-R1."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _go(w: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(w.p.app_url)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """A package of org A with a team object (3 teams) and a task object, and
    a package of org B with one team."""
    p = promoted
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("b", p.org_b)):
            ids[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"rel-{TAG}-{key}", "oe": OWNER, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())

    async def build() -> SimpleNamespace:
        pkg, pkg_b = ids["a"], ids["b"]
        async with tenant_session(p.org_a) as db:
            team = await store.define_object(db, OWNER, package_id=pkg, name="team",
                                             fields=TEAM_FIELDS)
            task = await store.define_object(db, OWNER, package_id=pkg, name="task",
                                             fields=TASK_FIELDS)
            assert team is not None and task is not None
            assert await store.set_access(db, team.id, TEAM_ACCESS, package_id=pkg)
            t1, t2, t3 = await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER, writes=[
                Write("create", team.id, data={"name": "Plant 4", "lead": L1}),
                Write("create", team.id, data={"name": "Plant 9", "lead": L2}),
                Write("create", team.id, data={"name": "  " + "x" * 200, "lead": L2})])
        async with tenant_session(p.org_b) as db:
            team_b = await store.define_object(db, OWNER, package_id=pkg_b, name="team",
                                               fields=TEAM_FIELDS)
            assert team_b is not None
            (b1,) = await store.apply_writes(db, OWNER, package_id=pkg_b, viewer=AS_OWNER, writes=[
                Write("create", team_b.id, data={"name": "Org B team", "lead": L1})])
        return SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg=pkg, pkg_b=pkg_b, team=team,
                               task=task, team_b=team_b, t1=t1.id, t2=t2.id, t3=t3.id, b1=b1.id)

    w = _go(SimpleNamespace(p=p), build)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"rel-{TAG}-%"})


async def _titles(w: Any, viewer: Viewer, ids: list[str], *, org: str | None = None,
                  oid: str | None = None, pkg: str | None = None,
                  only_keys: Any = None) -> tuple[dict[str, str | None], list[str]]:
    """The titles, and the statements that the call ran."""
    seen: list[str] = []
    eng = get_engine().sync_engine

    def _on(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
        seen.append(statement)

    async with tenant_session(org or w.a) as db:
        event.listen(eng, "before_cursor_execute", _on)
        try:
            got = await store.record_titles(db, oid or w.team.id, ids,
                                            package_id=pkg or w.pkg, viewer=viewer,
                                            only_keys=only_keys)
        finally:
            event.remove(eng, "before_cursor_execute", _on)
    return got, seen


def _read_statements(seen: list[str]) -> list[str]:
    return [s for s in seen if "FROM studio_records" in s]


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    assert "tests/unit/test_studio_relation_names_r8.py" in r8_lists.must_run()


def test_r8_the_owner_reads_a_page_of_three_names_in_one_read_and_a_constant_count(world) -> None:
    async def go() -> Any:
        one, s1 = await _titles(world, AS_OWNER, [world.t1])
        three, s3 = await _titles(world, AS_OWNER, [world.t1, world.t2, world.t3])
        return one, s1, three, s3

    one, s1, three, s3 = _go(world, go)
    assert one == {world.t1: "Plant 4"}
    assert three[world.t1] == "Plant 4" and three[world.t2] == "Plant 9"
    # A long title is cut at 160 characters, after the trim.
    assert three[world.t3] == "x" * 160
    assert len(_read_statements(s3)) == 1 and len(s3) == len(s1)


def test_r8_a_row_rule_on_the_target_hides_a_record_and_shows_the_other(world) -> None:
    got, seen = _go(world, lambda: _titles(world, AS_L1, [world.t1, world.t2]))
    assert got == {world.t1: "Plant 4", world.t2: None}
    # Two layers hide it: the row rule joins the statement, and the view hides each key of a
    # record that no grant reads. The statement of the owner holds no row rule.
    assert any("ac.v_text" in s for s in _read_statements(seen))
    _, owner_seen = _go(world, lambda: _titles(world, AS_OWNER, [world.t1, world.t2]))
    assert not any("ac.v_text" in s for s in _read_statements(owner_seen))
    # A viewer whose roles read no record gets None for each id.
    none, _ = _go(world, lambda: _titles(world, AS_NOBODY, [world.t1, world.t2]))
    assert none == {world.t1: None, world.t2: None}


def test_r8_a_hide_on_the_title_field_gives_none(world) -> None:
    got, _ = _go(world, lambda: _titles(world, AS_BLIND, [world.t1, world.t2]))
    assert got == {world.t1: None, world.t2: None}
    # A viewer who reads all and hides nothing sees both.
    seen, _ = _go(world, lambda: _titles(world, AS_ALL, [world.t1, world.t2]))
    assert seen == {world.t1: "Plant 4", world.t2: "Plant 9"}


def test_r8_a_missing_id_and_a_hidden_id_give_the_identical_answer(world) -> None:
    missing = str(uuid.uuid4())

    async def go() -> Any:
        hidden = await _titles(world, AS_L1, [world.t2])
        gone = await _titles(world, AS_L1, [missing])
        return hidden, gone

    (hidden, hidden_sql), (gone, gone_sql) = _go(world, go)
    assert list(hidden.values()) == list(gone.values()) == [None]
    assert len(hidden_sql) == len(gone_sql)
    # A title that `hide` hides on a record that the viewer reads answers the same.
    blind, _ = _go(world, lambda: _titles(world, AS_BLIND, [world.t1]))
    assert list(blind.values()) == [None]
    # The id of the hidden record is nowhere in a value.
    assert world.t2 not in json.dumps(list(hidden.values()))


def test_r8_an_id_of_another_org_gives_none(world) -> None:
    async def go() -> Any:
        # The record of org B, asked from org A against the team object of A.
        foreign, _ = await _titles(world, AS_OWNER, [world.b1])
        # The team object of org B, asked in a session of org A.
        other, _ = await _titles(world, AS_OWNER, [world.b1], oid=world.team_b.id, pkg=world.pkg_b)
        return foreign, other

    foreign, other = _go(world, go)
    assert foreign == {world.b1: None} and other == {world.b1: None}


def test_r8_a_non_uuid_id_gives_none_and_runs_no_statement(world) -> None:
    got, seen = _go(world, lambda: _titles(world, AS_OWNER, ["not-a-uuid", "12", ""]))
    assert got == {"not-a-uuid": None, "12": None, "": None}
    assert seen == []
    # A bad id beside a good one does not fail the page.
    mixed, _ = _go(world, lambda: _titles(world, AS_OWNER, ["nope", world.t1]))
    assert mixed == {"nope": None, world.t1: "Plant 4"}


def test_r8_an_id_in_the_32_hex_form_resolves_under_the_id_as_given(world) -> None:
    bare = world.t1.replace("-", "")
    got, _ = _go(world, lambda: _titles(world, AS_OWNER, [bare]))
    assert got == {bare: "Plant 4"}


def test_r8_the_first_200_ids_resolve_and_the_rest_give_none(world) -> None:
    fillers = [str(uuid.uuid4()) for _ in range(199)]
    ids = [*fillers, world.t1, world.t2]
    assert len(ids) == 201
    got, seen = _go(world, lambda: _titles(world, AS_OWNER, ids))
    assert got[world.t1] == "Plant 4"
    assert got[world.t2] is None  # the 201st id exists, and the cap leaves it out
    assert len(got) == 201 and len(_read_statements(seen)) == 1


def test_r8_an_install_whose_version_does_not_declare_the_target_gets_none(world) -> None:
    """The door helper, with an InstalledApp. The version declares `task` and
    not `team`, so each id of the relation is None."""
    async def go() -> Any:
        def app(declares_team: bool) -> package_viewer.InstalledApp:
            objects: list[dict[str, Any]] = [
                {"name": "task", "fields": [{"key": "title", "type": "text"},
                                             {"key": "team", "type": "relation", "to": "team"}]}]
            if declares_team:
                objects.append({"name": "team", "fields": [{"key": "name", "type": "text"}]})
            return package_viewer.InstalledApp(id=world.pkg, version=1,
                                               manifest={"objects": objects})

        async with tenant_session(world.a) as db:
            task = await store.get_object(db, world.task.id)
            assert task is not None
            rec = store.Record(id=str(uuid.uuid4()), object_id=task.id,
                               data={"team": {"id": world.t1}}, created_by="", updated_by="",
                               created_at=None, updated_at=None)  # type: ignore[arg-type]
            denied = await invoke._relation_names(db, app(False), task, [rec], AS_OWNER)
            allowed = await invoke._relation_names(db, app(True), task, [rec], AS_OWNER)
        return denied, allowed

    denied, allowed = _go(world, go)
    assert denied == {world.t1: None}
    assert allowed == {world.t1: "Plant 4"}


def test_r8_a_many_relation_and_a_core_target_in_the_door_helper(world) -> None:
    async def go() -> Any:
        async with tenant_session(world.a) as db:
            task = await store.get_object(db, world.task.id)
            assert task is not None
            rec = store.Record(id=str(uuid.uuid4()), object_id=task.id, data={
                "team": {"id": world.t1}, "helpers": [{"id": world.t2}, {"id": world.t3}],
                "company": {"id": str(uuid.uuid4())}}, created_by="", updated_by="",
                created_at=None, updated_at=None)  # type: ignore[arg-type]
            return await invoke._relation_names(db, SimpleNamespace(id=world.pkg), task, [rec],
                                                 AS_OWNER)

    got = _go(world, go)
    # The core id (crm.company) has no entry, and the three teams have one.
    assert set(got) == {world.t1, world.t2, world.t3}
    assert got[world.t1] == "Plant 4" and got[world.t2] == "Plant 9"
