"""WS-55 EA-17a — the left-out list of a connection (D-EA-12).

Spec: ``project-docs/specs/external_agent_access.md`` §10.8 (D-EA-12 rules 1
to 8, the connection fields) and the EA-17a row of §15 · Metorite task 1586.

The reach of a connection follows the nearest mark. It starts at each pin,
or at each root of the organization when the connection has no pin. It walks
down and stops at a left-out project, so that project and every project
beneath it stay out. A pin beneath a left-out project starts a reach of its
own. The seal and the personal switch still apply to each project it
reaches, and the member's live access still bounds the result.

**Database-free.** The shape of the narrowing: when the walk runs, what it
binds, a positive ``IN``, and the organization filter on each step. The two
checks that keep their test of the pins alone (``tree._refuse_pinned_space``
and the edge copy of it).

**R8 (a real database).** It reuses the promoted catalog of
``test_mcp_edge_authority.py`` (the ``world`` fixture) and seeds three more
organizations into it.

* The rules, one class each, read straight from the SQL of
  ``Visibility.project_clause`` as the catalog superuser. The superuser
  bypasses RLS on purpose: then the organization filter of the SQL is the
  only guard, so a walk without it fails a test (the R5 case).
* The chain: a signed request through the REAL branch 0 and the REAL
  Projects routes, as the non-privileged role of that suite. A left-out
  project hides its tasks on both arms of the task clause.
* The store: ``lookup_token`` and ``read_live_connection`` carry the list.
* ``approve``: it stores ``left_out_projects`` and the requested scopes, it
  refuses more than 50 ids, and it treats a left-out id that the member
  cannot see as it treats such a pin. It stores it, and the id changes
  nothing.

Named mutations that turn this file red (spec §15, EA-17a): the list
ignored, a left-out mark that wins over a pin beneath it, a walk with no
organization filter, and a guard that still runs only for pins.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_left_out_projects.py -v -rs
"""
from __future__ import annotations

import re
import secrets
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import AgentScope, UserContext, UserRole, agent_tokens
from fastapi import HTTPException
from gateway.routes.agent_access import _MAX_PINS
from gateway.routes.projects import core, tree
from sqlalchemy import text

from tests.unit import test_agent_oauth_server as oauth
from tests.unit import test_mcp_edge_authority as auth
from tests.unit._tenant_ladder import tenant_engine_scope

# The fixtures of the two suites. ``world`` is the promoted catalog of the
# authority suite, and ``armed`` arms branch 0 under its tripwire. ``web`` is
# the fake internet of the consent half, and ``redis`` and ``_flag_on`` are
# autouse there, so they are autouse here too.
world = auth.world
armed = auth.armed
web = oauth.web
redis = oauth.redis
_flag_on = oauth._flag_on

_DB_GATE = auth._DB_GATE
MEMBER = auth.MEMBER
OTHER = auth.OTHER


def _scope(**over: Any) -> AgentScope:
    values: dict[str, Any] = {"connection_id": "c", "include_personal": True}
    values.update(over)
    return AgentScope(**values)


def _vis(scope: AgentScope | None, *, org: str = auth.GOLDEN_ORG,
         unrestricted: bool = True) -> core.Visibility:
    return core.Visibility(unrestricted=unrestricted, email=MEMBER, groups=(),
                           organization_id=org, agent=scope)


# ═══════════════════════════════════════════════════════════════════════════
# Database-free: the shape
# ═══════════════════════════════════════════════════════════════════════════


class TestTheShape:
    """When the walk runs, where it starts and what it binds (rule 8)."""

    def test_a_connection_with_no_mark_gets_no_walk_and_no_bind(self):
        clause = core._narrow_for_agent("X", "c", _scope())
        assert "WITH RECURSIVE reach" not in clause
        params = _vis(_scope()).params
        assert "vis_pins" not in params and "vis_left_out" not in params

    def test_a_left_out_list_alone_walks_from_the_roots(self):
        """Rule 8: the narrowing runs for pins OR a left-out list."""
        scope = _scope(left_out_projects=("l1",))
        clause = core._narrow_for_agent("X", "c", scope)
        assert "WITH RECURSIVE reach" in clause
        assert core._AGENT_REACH_FROM_ROOTS in clause
        assert core._AGENT_REACH_FROM_PINS not in clause
        params = _vis(scope).params
        assert params["vis_left_out"] == ["l1"]
        assert params["vis_pins"] == []

    def test_pins_walk_from_the_pins(self):
        scope = _scope(pinned_projects=("p1", "p2"))
        clause = core._narrow_for_agent("X", "c", scope)
        assert core._AGENT_REACH_FROM_PINS in clause
        assert core._AGENT_REACH_FROM_ROOTS not in clause
        params = _vis(scope).params
        assert params["vis_pins"] == ["p1", "p2"]
        assert params["vis_left_out"] == []

    def test_pins_and_a_list_bind_both(self):
        scope = _scope(pinned_projects=("p1",), left_out_projects=("l1", "l2"))
        params = _vis(scope).params
        assert params["vis_pins"] == ["p1"]
        assert params["vis_left_out"] == ["l1", "l2"]

    def test_the_walk_is_a_positive_in_with_union(self):
        clause = core._narrow_for_agent(
            "X", "c", _scope(left_out_projects=("l1",)))
        assert not re.search(r"NOT\s+IN", clause), "a reach is a POSITIVE IN"
        assert "UNION\n" in clause and "UNION ALL" not in clause

    def test_each_step_of_the_walk_filters_by_the_organization(self):
        """R5: the start and the step each name ``:vis_org``."""
        body = core._AGENT_REACH_SQL
        start, _, step = body.partition("UNION")
        org = "organization_id = CAST(:vis_org AS uuid)"
        assert start.count(org) == 1, "the start has no organization filter"
        assert step.count(org) == 1, "the step has no organization filter"

    def test_the_seal_and_the_personal_switch_stay_outside_the_walk(self):
        """Rules 6 and 7: the walk only narrows the set that already holds
        the seal and the switch, so it comes after them."""
        clause = core._narrow_for_agent(
            "X", "c", _scope(include_personal=False, left_out_projects=("l1",)))
        sealed = clause.index("sealed_at IS NULL")
        personal = clause.index("personal_owner IS NULL")
        walk = clause.index("WITH RECURSIVE reach")
        assert sealed < walk and personal < walk


def _user(scope: AgentScope) -> UserContext:
    return UserContext(email=MEMBER, role=UserRole.EMPLOYEE, agent=scope)


class TestThePinOnlyChecksKeepThePins:
    """§10.8: ``_refuse_pinned_space`` and the edge copy of it test the pins
    alone. A connection with only a left-out list reaches wide."""

    def test_a_left_out_list_alone_may_make_a_space(self):
        tree._refuse_pinned_space(_user(_scope(left_out_projects=("l1",))), None)

    def test_a_pin_still_may_not(self):
        with pytest.raises(HTTPException) as caught:
            tree._refuse_pinned_space(
                _user(_scope(pinned_projects=("p1",), left_out_projects=("l1",))),
                None)
        assert caught.value.status_code == 403

    def test_the_edge_copy_reads_the_pins_alone(self):
        from agent_edge import tools

        def ctx(**over: Any) -> Any:
            lookup = SimpleNamespace(pinned_projects=(), left_out_projects=())
            for name, value in over.items():
                setattr(lookup, name, value)
            return SimpleNamespace(caller=SimpleNamespace(lookup=lookup))

        space = {"name": "New", "level": "space"}
        tools._refuse_bad_project_call(ctx(left_out_projects=("l1",)), space)
        with pytest.raises(tools.ToolError):
            tools._refuse_bad_project_call(ctx(pinned_projects=("p1",)), space)


# ═══════════════════════════════════════════════════════════════════════════
# R8: the seeds
# ═══════════════════════════════════════════════════════════════════════════


def _new_org(world, slug: str) -> str:
    org = str(uuid.uuid4())
    with world.admin.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, :slug, :slug)"), {"id": org, "slug": slug})
    return org


@pytest.fixture(scope="module")
def trees(world):
    """Three organizations of their own in the authority catalog.

    ``clean`` holds a 4-level tree, a sealed tree and two personal trees::

        R1 (granted to MEMBER)        R2         S (sealed)    P (MEMBER's)
        ├── A                         └── C      └── S1        └── P1
        │   ├── A1                                             PO (OTHER's)
        │   │   └── A1x
        │   └── A2
        └── B
            └── B1

    ``other`` holds Y and its child Y1. ``broken`` holds K, K2, a cycle
    Cy1 <-> Cy2 with no root, and X, whose parent is Y of ``other``. The
    trigger of migration 161 refuses that last link, so the seed writes it
    with the triggers off, as broken data would look.
    """
    clean = _new_org(world, f"leftout-clean-{uuid.uuid4().hex[:6]}")
    other = _new_org(world, f"leftout-other-{uuid.uuid4().hex[:6]}")
    broken = _new_org(world, f"leftout-broken-{uuid.uuid4().hex[:6]}")
    a = auth._Seeder(world.admin, clean)
    o = auth._Seeder(world.admin, other)
    k = auth._Seeder(world.admin, broken)
    p: dict[str, str] = {}
    p["R1"] = a.project("R1", grant_to=MEMBER)
    p["A"] = a.project("A", parent=p["R1"])
    p["A1"] = a.project("A1", parent=p["A"])
    p["A1x"] = a.project("A1x", parent=p["A1"])
    p["A2"] = a.project("A2", parent=p["A"])
    p["B"] = a.project("B", parent=p["R1"])
    p["B1"] = a.project("B1", parent=p["B"])
    p["R2"] = a.project("R2")
    p["C"] = a.project("C", parent=p["R2"])
    p["S"] = a.project("S", grant_to=MEMBER)
    p["S1"] = a.project("S1", parent=p["S"])
    p["P"] = a.project("P", personal_owner=MEMBER, grant_to=MEMBER)
    p["P1"] = a.project("P1", parent=p["P"], personal_owner=MEMBER)
    p["PO"] = a.project("PO", personal_owner=OTHER)
    a.one("UPDATE pm_projects SET sealed_at = now()"
          " WHERE id IN (CAST(:s AS uuid), CAST(:s1 AS uuid)) RETURNING id",
          s=p["S"], s1=p["S1"])

    p["Y"] = o.project("Y")
    p["Y1"] = o.project("Y1", parent=p["Y"])

    p["K"] = k.project("K")
    p["K2"] = k.project("K2")
    p["Cy1"] = k.project("Cy1")
    p["Cy2"] = k.project("Cy2", parent=p["Cy1"])
    k.one("UPDATE pm_projects SET parent_project_id = CAST(:c2 AS uuid)"
          " WHERE id = CAST(:c1 AS uuid) RETURNING id", c1=p["Cy1"], c2=p["Cy2"])
    p["X"] = k.project("X")
    with world.admin.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text("UPDATE pm_projects SET parent_project_id = CAST(:y AS uuid)"
                       " WHERE id = CAST(:x AS uuid)"), {"y": p["Y"], "x": p["X"]})
    return SimpleNamespace(clean=clean, other=other, broken=broken, p=p, seed=a)


def _reach(world, trees, org: str, *, unrestricted: bool = True,
           **over: Any) -> set[str]:
    """The names of the projects that ``project_clause`` lets through.

    It runs as the catalog superuser, so RLS hides nothing, and the SQL's own
    organization filters are the only guard (R5).
    """
    scope = None if over.pop("human", False) else _scope(**_ids_of(trees, over))
    vis = _vis(scope, org=org, unrestricted=unrestricted)
    with world.admin.connect() as c:
        rows = c.execute(
            text(f"SELECT CAST(id AS TEXT) FROM pm_projects WHERE {vis.project_clause()}"),
            vis.params).all()
    names = {v: n for n, v in trees.p.items()}
    return {names.get(r[0], r[0]) for r in rows}


def _ids_of(trees, over: dict[str, Any]) -> dict[str, Any]:
    """``pins=`` and ``left_out=`` name projects of :func:`trees`."""
    out = dict(over)
    if "pins" in out:
        out["pinned_projects"] = tuple(trees.p.get(n, n) for n in out.pop("pins"))
    if "left_out" in out:
        out["left_out_projects"] = tuple(trees.p.get(n, n) for n in out.pop("left_out"))
    return out


#: The reach of a connection with no mark in ``clean``: every project that is
#: not sealed and not another member's personal project.
BASELINE = {"R1", "A", "A1", "A1x", "A2", "B", "B1", "R2", "C", "P", "P1"}
SUBTREE_A = {"A", "A1", "A1x", "A2"}


# ═══════════════════════════════════════════════════════════════════════════
# R8: the rules of D-EA-12, read from the SQL
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestRuleOneTheStarts:
    """Rule 1: with no pin, the reach starts from every project. With pins,
    from each pin."""

    def test_no_pin_and_no_mark_reaches_every_project(self, world, trees):
        assert _reach(world, trees, trees.clean) == BASELINE

    def test_a_pin_reaches_itself_and_everything_beneath(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["A"]) == SUBTREE_A

    def test_three_levels_beneath_a_pin(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["R1"]) == {
            "R1", "A", "A1", "A1x", "A2", "B", "B1"}


@_DB_GATE
class TestRuleTwoTheWalkStops:
    """Rule 2: the reach stops at a left-out project. It and its subtree stay
    out."""

    def test_a_left_out_child_of_a_pin(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["R1"], left_out=["A"]) == {
            "R1", "B", "B1"}

    def test_a_left_out_project_two_levels_down(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["R1"], left_out=["A1"]) == {
            "R1", "A", "A2", "B", "B1"}

    def test_a_pin_that_is_left_out_too_is_out(self, world, trees):
        """Both marks on one project: the narrower one holds."""
        assert _reach(world, trees, trees.clean, pins=["A"], left_out=["A"]) == set()


@_DB_GATE
class TestRuleThreeTheNearestMarkWins:
    """Rule 3: a pin beneath a left-out project starts its own reach."""

    def test_a_pin_under_a_left_out_parent_is_in(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["R1", "A1"], left_out=["A"]) == {
            "R1", "B", "B1", "A1", "A1x"}

    def test_a_left_out_project_under_that_pin_stops_again(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["R1", "A1"],
                      left_out=["A", "A1x"]) == {"R1", "B", "B1", "A1"}


@_DB_GATE
class TestRuleFourAnIdThatNoStartReaches:
    """Rule 4: an unreachable left-out id, or one of another organization,
    changes nothing."""

    def test_an_unreachable_left_out_id(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["A"], left_out=["C"]) == SUBTREE_A

    def test_a_random_id(self, world, trees):
        assert _reach(world, trees, trees.clean,
                      left_out=[str(uuid.uuid4())]) == BASELINE

    @pytest.mark.parametrize("pins", [[], ["R1"]], ids=["no-pin", "pin"])
    def test_a_left_out_id_of_another_organization(self, world, trees, pins):
        alone = _reach(world, trees, trees.clean, pins=pins)
        assert _reach(world, trees, trees.clean, pins=pins, left_out=["Y"]) == alone

    def test_a_pin_of_another_organization_reaches_nothing(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["Y"]) == set()


@_DB_GATE
class TestRuleFiveAProjectAddedLater:
    """Rule 5: a new project is in when its nearest mark is a pin, or when no
    mark is above it and the connection has no pin. It is out when its
    nearest mark is a left-out project."""

    @contextmanager
    def _later(self, trees, **parents: str):
        made: dict[str, str] = {}
        try:
            for name, parent in parents.items():
                pid = trees.seed.project(name, parent=trees.p[parent] if parent else None)
                made[name] = pid
                trees.p[name] = pid
            yield made
        finally:
            for name, pid in made.items():
                trees.p.pop(name, None)
                trees.seed.one("DELETE FROM pm_projects WHERE id = CAST(:i AS uuid)"
                               " RETURNING id", i=pid)

    def test_with_pins(self, world, trees):
        with self._later(trees, N_pin="A1x", N_left="A2", N_free="B", N_root=""):
            got = _reach(world, trees, trees.clean, pins=["R1", "A1"], left_out=["A"])
        assert got == {"R1", "B", "B1", "A1", "A1x", "N_pin", "N_free"}

    def test_with_no_pin(self, world, trees):
        with self._later(trees, N_root="", N_left="A", N_free="C"):
            got = _reach(world, trees, trees.clean, left_out=["A"])
        assert got == (BASELINE - SUBTREE_A) | {"N_root", "N_free"}


@_DB_GATE
class TestRuleSixTheSeal:
    """Rule 6: a sealed project is always out. A mark on it changes nothing."""

    def test_a_pin_on_a_sealed_project_reaches_nothing(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["S"]) == set()

    @pytest.mark.parametrize("pins", [[], ["R1"]], ids=["no-pin", "pin"])
    def test_a_left_out_mark_on_a_sealed_project(self, world, trees, pins):
        alone = _reach(world, trees, trees.clean, pins=pins)
        assert _reach(world, trees, trees.clean, pins=pins, left_out=["S"]) == alone


@_DB_GATE
class TestRuleSevenThePersonalSwitchComesLast:
    """Rule 7: with the switch off, a mark on a personal project changes
    nothing. With it on, a mark works as on any project."""

    def test_switch_off_a_pin_on_a_personal_project(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["P"],
                      include_personal=False) == set()

    def test_switch_off_a_left_out_personal_project(self, world, trees):
        alone = _reach(world, trees, trees.clean, include_personal=False)
        assert alone == BASELINE - {"P", "P1"}
        assert _reach(world, trees, trees.clean, left_out=["P"],
                      include_personal=False) == alone

    def test_switch_on_the_marks_work(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["P"]) == {"P", "P1"}
        assert _reach(world, trees, trees.clean, left_out=["P"]) == BASELINE - {"P", "P1"}

    def test_another_members_personal_project_stays_out(self, world, trees):
        assert _reach(world, trees, trees.clean, pins=["PO"]) == set()


@_DB_GATE
class TestRuleEightALeftOutListWithNoPin:
    """Rule 8: with no pin, the reach is every project less each left-out
    subtree."""

    def test_everything_less_the_subtree(self, world, trees):
        assert _reach(world, trees, trees.clean, left_out=["A"]) == BASELINE - SUBTREE_A

    def test_two_subtrees(self, world, trees):
        assert _reach(world, trees, trees.clean, left_out=["A", "R2"]) == (
            BASELINE - SUBTREE_A - {"R2", "C"})

    def test_the_members_live_access_still_bounds_it(self, world, trees):
        """§7.1: the list never adds a project. MEMBER holds a grant on R1,
        S and P only."""
        human = _reach(world, trees, trees.clean, unrestricted=False, human=True)
        assert human == {"R1", "A", "A1", "A1x", "A2", "B", "B1", "P", "P1"}
        assert _reach(world, trees, trees.clean, unrestricted=False,
                      left_out=["A"]) == human - SUBTREE_A
        assert _reach(world, trees, trees.clean, unrestricted=False,
                      pins=["R2"]) == set()


@_DB_GATE
class TestABrokenTreeNarrowsAndNeverWidens:
    """A cycle ends, and a parent in another organization starts nothing."""

    def test_the_baseline_of_the_broken_organization(self, world, trees):
        assert _reach(world, trees, trees.broken) == {"K", "K2", "Cy1", "Cy2", "X"}

    def test_a_cycle_that_ends(self, world, trees):
        assert _reach(world, trees, trees.broken, pins=["Cy1"]) == {"Cy1", "Cy2"}
        assert _reach(world, trees, trees.broken, pins=["Cy1"],
                      left_out=["Cy2"]) == {"Cy1"}

    def test_a_walk_from_the_roots_leaves_a_rootless_tree_out(self, world, trees):
        """No root starts the cycle, and X hangs under another organization.
        Both stay out: a broken tree narrows the reach."""
        assert _reach(world, trees, trees.broken, left_out=["K"]) == {"K2"}

    def test_a_foreign_pin_never_walks_into_this_organization(self, world, trees):
        """R5. Y is a project of ``other``, and X of ``broken`` hangs under
        it. A pin on Y must not reach X."""
        assert _reach(world, trees, trees.broken, pins=["Y"]) == set()
        assert _reach(world, trees, trees.broken, pins=["Y", "K"]) == {"K"}


# ═══════════════════════════════════════════════════════════════════════════
# R8: the chain, through branch 0 and the Projects routes
# ═══════════════════════════════════════════════════════════════════════════


def _connection_with(world, *, pins: tuple[str, ...] = (),
                     left_out: tuple[str, ...] = ()) -> str:
    """A live connection of MEMBER in org A of the authority world."""
    return str(world.seeder_a.one(
        "INSERT INTO agent_connection (organization_id, member_email,"
        " client_id, client_name, client_origin, scopes, pinned_projects,"
        " left_out_projects, include_personal, expires_at) VALUES"
        " (CAST(:o AS uuid), :m, 'https://client.example/meta.json', 'Client',"
        " :origin, ARRAY['projects:read'], CAST(:pins AS uuid[]),"
        " CAST(:left AS uuid[]), true, now() + interval '1 day') RETURNING id",
        o=world.org_a, m=MEMBER, origin=auth.ORIGIN, pins=list(pins),
        left=list(left_out)))


async def _hidden(world, connection: str, targets: list[str], kept: list[str]) -> None:
    rows, total = await auth._list(world, "agent", connection=connection)
    for target in targets:
        assert target not in rows, target
    for task in kept:
        assert task in rows, task
    assert total == len(rows)
    missing = await auth._agent(world, connection, auth._MISSING_TASK)
    for target in targets:
        r = await auth._agent(world, connection, f"/projects/tasks/{target}")
        assert r.status_code == 404, (target, r.text)
        assert r.json() == missing.json(), "one not-found text"
    for task in kept:
        r = await auth._agent(world, connection, f"/projects/tasks/{task}")
        assert r.status_code == 200, (task, r.text)


@_DB_GATE
class TestTheChain:
    """Branch 0 copies the list into ``AgentScope``, and the seam narrows
    both arms of the task clause with it."""

    async def test_a_left_out_project_hides_its_grant_arm_task(self, armed):
        t, p = armed.t, armed.p
        cid = _connection_with(armed, left_out=(p["team_child"],))
        await _hidden(armed, cid, targets=[t["child"]],
                      kept=[t["team"], t["other"], t["foreign"]])

    async def test_a_left_out_project_hides_its_assignee_arm_task(self, armed):
        t, p = armed.t, armed.p
        cid = _connection_with(armed, left_out=(p["foreign"],))
        await _hidden(armed, cid, targets=[t["foreign"]],
                      kept=[t["team"], t["child"], t["other"]])

    async def test_a_pin_with_a_left_out_child(self, armed):
        t, p = armed.t, armed.p
        cid = _connection_with(armed, pins=(p["team"],), left_out=(p["team_child"],))
        await _hidden(armed, cid, targets=[t["child"], t["other"], t["foreign"]],
                      kept=[t["team"]])

    async def test_the_tree_route_leaves_the_project_out(self, armed):
        p = armed.p
        cid = _connection_with(armed, left_out=(p["team_child"],))
        r = await auth._agent(armed, cid, "/projects/tree")
        assert r.status_code == 200, r.text
        seen = auth._ids(r.json())
        assert p["team"] in seen and p["other"] in seen
        assert p["team_child"] not in seen

    async def test_a_foreign_left_out_id_changes_nothing(self, armed):
        open_rows, _ = await auth._list(armed, "open", connection=armed.c["open"])
        cid = _connection_with(armed, left_out=(armed.p["b_root"],))
        rows, _ = await auth._list(armed, "foreign", connection=cid)
        assert rows == open_rows


@_DB_GATE
class TestTheStoreCarriesTheList:
    """``lookup_token`` (the edge) and ``read_live_connection`` (branch 0)."""

    async def test_lookup_token_and_the_live_read(self, armed):
        p = armed.p
        cid = _connection_with(armed, pins=(p["team"],), left_out=(p["team_child"],))
        raw = secrets.token_urlsafe(32)
        armed.seeder_a.one(
            "INSERT INTO agent_token (organization_id, connection_id, kind,"
            " token_hash, expires_at) VALUES (CAST(:o AS uuid), CAST(:c AS uuid),"
            " 'access', :h, now() + interval '1 hour') RETURNING id",
            o=armed.org_a, c=cid, h=agent_tokens.hash_token(raw))
        async with tenant_engine_scope(armed.app_dsn):
            found = await agent_tokens.lookup_token(raw)
            live = await agent_tokens.read_live_connection(armed.org_a, cid)
        assert found is not None and live is not None
        assert found.left_out_projects == (p["team_child"],)
        assert found.pinned_projects == (p["team"],)
        assert live.left_out_projects == (p["team_child"],)

    async def test_a_connection_with_no_list_reads_empty(self, armed):
        async with tenant_engine_scope(armed.app_dsn):
            live = await agent_tokens.read_live_connection(armed.org_a, armed.c["open"])
        assert live is not None and live.left_out_projects == ()


# ═══════════════════════════════════════════════════════════════════════════
# R8: approve
# ═══════════════════════════════════════════════════════════════════════════


def _as_promoted(world) -> SimpleNamespace:
    """The authority catalog in the shape that the consent helpers read."""
    return SimpleNamespace(org_a=world.org_a, org_b=world.org_b,
                           app_dsn=world.app_dsn, admin_engine=world.admin)


def _row(promoted, connection_id: str):
    return oauth._admin_one(
        promoted,
        "SELECT CAST(left_out_projects AS TEXT[]) AS left_out, requested_scopes,"
        "       scopes, CAST(pinned_projects AS TEXT[]) AS pins"
        "  FROM agent_connection WHERE id = CAST(:i AS uuid)", i=connection_id)


def _count(promoted) -> int:
    return oauth._admin_one(promoted, "SELECT count(*) FROM agent_connection")[0]


async def _approve(world, **over) -> Any:
    promoted = _as_promoted(world)
    async with tenant_engine_scope(world.app_dsn):
        return await oauth._approve(promoted, **over)


def _connection_of(world, response) -> str:
    assert response.status_code == 200, response.text
    code = oauth._query(response.json()["redirect_to"])["code"]
    return oauth._connection_id_of_code(_as_promoted(world), code)


@_DB_GATE
class TestApprove:

    async def test_it_stores_the_list_and_the_requested_scopes(self, world, web):
        left = [str(uuid.uuid4()), str(uuid.uuid4())]
        r = await _approve(world, scope="projects:read projects:write offline_access",
                           scopes=["projects:read"], left_out_projects=left)
        row = _row(_as_promoted(world), _connection_of(world, r))
        assert row.left_out == left
        assert row.scopes == ["projects:read"]
        assert row.requested_scopes == ["projects:read", "projects:write", "offline_access"]

    async def test_no_list_stores_an_empty_list(self, world, web):
        row = _row(_as_promoted(world), _connection_of(world, await _approve(world)))
        assert row.left_out == []
        assert row.requested_scopes == ["projects:read", "offline_access"]

    async def test_the_cap_is_the_cap_of_the_pins(self, world, web):
        assert _MAX_PINS == 50
        before = _count(_as_promoted(world))
        many = await _approve(world, left_out_projects=[
            str(uuid.uuid4()) for _ in range(_MAX_PINS + 1)])
        assert many.status_code == 422, many.text
        assert _count(_as_promoted(world)) == before, "a refused body made a connection"
        full = await _approve(world, left_out_projects=[
            str(uuid.uuid4()) for _ in range(_MAX_PINS)])
        assert len(_row(_as_promoted(world), _connection_of(world, full)).left_out) == 50

    @pytest.mark.parametrize("field", ["pinned_projects", "left_out_projects"])
    async def test_a_value_that_is_not_an_id_is_refused_as_a_bad_pin(self, world, web, field):
        before = _count(_as_promoted(world))
        r = await _approve(world, **{field: ["not-a-uuid"]})
        assert r.status_code == 422, r.text
        assert _count(_as_promoted(world)) == before

    @pytest.mark.parametrize("field", ["pinned_projects", "left_out_projects"])
    async def test_an_id_the_member_cannot_see_is_stored_as_a_bad_pin_is(
            self, world, web, field):
        """Today ``approve`` does not check that the member sees a pin: the
        member's live access bounds the reach (§7.1). A left-out id gets the
        same treatment, and rule 4 makes it change nothing."""
        foreign = world.p["b_root"]
        row = _row(_as_promoted(world),
                   _connection_of(world, await _approve(world, **{field: [foreign]})))
        stored = row.pins if field == "pinned_projects" else row.left_out
        assert stored == [foreign]
