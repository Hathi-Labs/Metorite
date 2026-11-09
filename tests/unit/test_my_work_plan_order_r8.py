"""SW-68a — ``my_work`` ranks the tasks under an epic by the plan engine (R8).

Spec: ``project-docs/specs/agent_coordination.md`` SW-68 (item 2a) and
``project-docs/specs/project_planning.md`` §3.4 (readers).

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_my_work_plan_order_r8.py -v -rs

The MCP tool ``my_work`` runs on the real edge, over the real gateway app, as a
non-privileged role. The chat tool ``my_work`` runs with its gateway client
pointed at the same app. The tests prove three things on a real database.

1. In a diamond epic the critical-path slice comes first.
2. A slice that the plan names as blocked is never the top row, and its row says
   what blocks it.
3. A hidden slice never appears, and it never changes the order of the rest.

The MCP answer and the chat answer agree on the order.

Mutations (R7). ``G`` is ``apps/services/gateway/gateway/routes/projects``.

* O1. ``G/plan_order.py`` ``reorder`` returns ``ids``. ``test_the_critical_slice_comes_first``,
  ``test_a_blocked_slice_is_never_the_top_row``.
* O2. ``G/plan_order.py`` ``reorder`` drops its last step, the move of blocked
  slices to the end. ``test_a_lone_blocked_slice_never_leads`` (pure).
* O3. ``G/plan_order.py`` ``plan_tasks`` runs ``compute_plan`` without the visibility
  of the caller (``_TREE_SQL`` ``vis`` is true). ``test_a_hidden_slice_changes_nothing``.
* O4. ``agent_edge/tools.py`` ``_my_work`` skips ``_plan_of``. ``test_the_critical_slice_comes_first``.
* O5. ``skill_projects/reads.py`` ``_plan_ordered`` returns ``rows``.
  ``test_the_mcp_and_the_chat_agree``.
* O6. ``agent_edge/tools.py`` ``_my_work`` sends only the offered rows to the plan
  (probe V7 of the verifier). ``test_the_mcp_and_the_chat_agree`` (the whole order).
* O7. ``G/plan_order.py`` ``plan_order`` skips ``plan_order_allowed``.
  ``test_with_the_flag_off_the_route_answers_the_input_order``.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects.plan_order import MAX_EPICS, MAX_TASKS, TaskPlan, reorder

from tests.unit import test_mcp_edge_task_writes as t
from tests.unit import test_mcp_edge_writes as w
from tests.unit.test_mcp_edge_assignees import _assign, world  # noqa: F401  (the fixture)
from tests.unit.test_plan_analysis_r8 import _block, _pinned, _under

_DB_GATE = w._DB_GATE
live_app = t.live_app
live = t.live
redis_fake = t.redis_fake

ORDER = "/projects/plan/order"


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    """The plan order is dark by default. These tests turn it on for every org."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "my_work_plan_order_orgs", "*")


def _epic(live, label: str) -> str:
    """An Epic-typed root task in the team project."""
    kind = live.seeder.one(
        "INSERT INTO pm_task_types (project_id, name, is_epic, organization_id)"
        " VALUES (CAST(:p AS uuid), :n, true, CAST(:o AS uuid)) RETURNING id",
        p=live.team, n=f"Epic {label}", o=live.org)
    epic = live.task(live.team, live.s.todo, f"{label} epic")
    live.seeder.one(
        "UPDATE pm_tasks SET type_id = CAST(:k AS uuid) WHERE id = CAST(:t AS uuid)"
        " RETURNING id", k=str(kind), t=epic)
    return epic


def _slice(live, epic: str, label: str, mins: int | None, *, mine: bool = True) -> str:
    task = _under(live, epic, live.team, live.s.todo, label, mins)
    if mine:
        _assign(live, task, w.MEMBER)
    return task


def _plain(live, label: str) -> str:
    """A task outside every epic, assigned to the member."""
    task = live.task(live.team, live.s.todo, label)
    _assign(live, task, w.MEMBER)
    return task


async def _mcp_rows(live) -> list[dict[str, Any]]:
    result = await t._mcp(live, "write", "my_work", {"limit": 25})
    assert result["isError"] is False, result
    return result["structuredContent"]["rows"]


def _among(rows: list[dict[str, Any]], *ids: str) -> list[str]:
    """The ids of *ids* in the order of *rows*. The world is shared by the
    tests of this file, so a test reads only the rows it planted."""
    return [r["task_id"] for r in rows if r["task_id"] in ids]


def _top_is_not_blocked(rows: list[dict[str, Any]]) -> None:
    assigned = [r for r in rows if r["reason"] == "assigned"]
    assert assigned and assigned[0].get("plan", {}).get("ready", True) is True


async def _chat(live, monkeypatch) -> tuple[list[str], str]:
    """The chat ``my_work`` text, and its task titles in the order printed."""
    from acb_common.tool_refusals import GatewayRefusal
    from skill_projects import reads

    async def get(path: str, params: dict[str, Any] | None = None) -> Any:
        query = urlencode([(k, str(v)) for k, v in (params or {}).items()])
        r = await t._send(live, "GET", path, query=query, signed=None)
        if r.status_code != 200:
            raise GatewayRefusal("refused", status=r.status_code)
        return r.json()

    monkeypatch.setattr(reads, "get", get)
    text_ = await reads.my_work()
    titles = [line for line in text_.splitlines() if line.startswith("- ")]
    return titles, text_


def _where(titles: list[str], label: str) -> int:
    hits = [n for n, line in enumerate(titles) if label in line]
    assert len(hits) == 1, (label, titles)
    return hits[0]


class TestTheRuleWithNoDatabase:
    """``reorder`` is the pure half. It needs no database."""

    @staticmethod
    def _plan(blocked: bool, slack: int | None, rank: int | None = None) -> TaskPlan:
        return TaskPlan(epic_id="e", blocked=blocked, slack_mins=slack, blocked_by=(),
                        blocked_by_hidden=0, rank=rank)

    def test_a_task_outside_an_epic_keeps_its_slot(self):
        plans = {"b": self._plan(False, 0), "d": self._plan(False, 90)}
        assert reorder(["d", "x", "b", "y"], plans) == ["b", "x", "d", "y"]

    def test_the_order_is_slack_then_priority_then_id(self):
        plans = {
            "c": self._plan(False, 0, rank=3), "a": self._plan(False, 0, rank=3),
            "b": self._plan(False, 0, rank=1), "z": self._plan(False, 10, rank=1),
        }
        assert reorder(["z", "c", "a", "b"], plans) == ["b", "a", "c", "z"]

    def test_a_blocked_slice_goes_after_every_other_task(self):
        plans = {"held": self._plan(True, 0), "ready": self._plan(False, 500)}
        assert reorder(["held", "plain", "ready"], plans) == ["ready", "plain", "held"]

    def test_a_lone_blocked_slice_never_leads(self):
        plans = {"held": self._plan(True, 0)}
        assert reorder(["held", "plain", "other"], plans) == ["plain", "other", "held"]

    def test_no_plan_is_no_change(self):
        assert reorder(["c", "a", "b"], {}) == ["c", "a", "b"]

    def test_the_caps_are_the_documented_numbers(self):
        assert (MAX_TASKS, MAX_EPICS) == (100, 10)

    def test_a_slice_with_no_slack_ranks_after_a_known_slack(self):
        plans = {"a": self._plan(False, None), "b": self._plan(False, 400)}
        assert reorder(["a", "b"], plans) == ["b", "a"]


class TestTheOrderOnARealDatabase:
    pytestmark = _DB_GATE

    async def test_the_critical_slice_comes_first(self, live):
        """A diamond: the short slice ``y`` and the long slice ``x`` are both
        ready, and both feed ``j``. ``x`` is on the critical path."""
        epic = _epic(live, "sw68a diamond")
        # The list reads newest first, so the order of the old code is y, x.
        x = _slice(live, epic, "sw68a diamond x", 300)
        y = _slice(live, epic, "sw68a diamond y", 30)
        j = _slice(live, epic, "sw68a diamond j", 60)
        _block(live, x, j)
        _block(live, y, j)
        rows = [r for r in await _mcp_rows(live) if r["reason"] == "assigned"]
        ids = [r["task_id"] for r in rows]
        assert _among(rows, x, y) == [x, y], ids
        by_id = {r["task_id"]: r for r in rows}
        assert by_id[x]["plan"]["ready"] is True and by_id[x]["plan"]["slack_mins"] == 0
        assert by_id[y]["plan"]["slack_mins"] > 0 and by_id[y]["plan"]["blocked_by"] == []
        assert j not in ids  # an open direct blocker still keeps a task off the list
        assert all(set(r["plan"]) == {"epic_id", "ready", "slack_mins", "blocked_by",
                                      "blocked_by_hidden"} for r in rows)

    async def test_a_blocked_slice_is_never_the_top_row(self, live):
        """``z`` is blocked by a whole epic (a link on the parent), so it has no
        ``blocked_by_count`` of its own. The plan names it blocked."""
        first, second = _epic(live, "sw68a first"), _epic(live, "sw68a second")
        _slice(live, first, "sw68a first p", 60, mine=False)
        # The list reads newest first, so the order of the old code is z, w.
        w_ = _plain(live, "sw68a plain w")
        z = _slice(live, second, "sw68a second z", 60)
        _block(live, first, second)
        rows = [r for r in await _mcp_rows(live) if r["reason"] == "assigned"]
        assert _among(rows, w_, z) == [w_, z]
        by_id = {r["task_id"]: r for r in rows}
        blocked = by_id[z]["plan"]
        assert blocked["ready"] is False and blocked["blocked_by"] == [first]
        assert "plan" not in by_id[w_]  # a task under no epic carries no plan
        _top_is_not_blocked(rows)

    async def test_a_member_with_no_epic_keeps_the_order(self, live):
        a, b, c = (_plain(live, f"sw68a plain {n}") for n in "abc")
        r = await t._send(live, "GET", ORDER, query=f"task_ids={c},{a},{b}", signed=None)
        assert r.status_code == 200, r.text
        assert r.json() == {"order": [c, a, b], "plan": {}, "labels": {}}

    async def test_a_hidden_slice_changes_nothing(self, live, monkeypatch):
        """``u`` blocks a hidden slice ``h`` of 5000 minutes. Were ``h`` in the
        plan, ``u`` would be critical and lead. It is not, so ``x2`` leads. A
        slice blocked by a hidden task says so with no name."""
        seeder = live.seeder
        epic = _epic(live, "sw68a hidden")
        x2 = _slice(live, epic, "sw68a hidden x2", 300)
        v = _slice(live, epic, "sw68a hidden v", 30)
        u = _slice(live, epic, "sw68a hidden u", 30)
        tail = _slice(live, epic, "sw68a hidden tail", 60, mine=False)
        _block(live, x2, tail)  # two visible slices are linked, so slack is known
        hidden_project = seeder.project("sw68a hidden project", in_space=True)
        for node in (hidden_project, seeder.root(hidden_project)):
            seeder.one("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"
                       " AND subject = :s RETURNING project_id", p=node, s=w.MEMBER)
        status = seeder.status(hidden_project, "To do", "todo", 0)
        secret = _under(live, epic, hidden_project, status, "sw68a secret slice", 5000)
        also = _under(live, epic, hidden_project, status, "sw68a secret blocker", 10)
        _block(live, u, secret)
        _block(live, also, v)
        rows = await _mcp_rows(live)
        ids = [r["task_id"] for r in rows if r["reason"] == "assigned"]
        assert _among(rows, x2, u) == [x2, u]
        assert v not in ids  # a hidden blocker keeps v off the MCP list, as before
        body = json.dumps(rows)
        assert secret not in body and also not in body and "sw68a secret" not in body

        titles, text_ = await _chat(live, monkeypatch)
        assert _where(titles, "hidden x2") < _where(titles, "hidden u")
        assert _where(titles, "hidden v") > _where(titles, "hidden u")  # blocked: after
        assert "blocked by a task hidden from you" in text_
        assert secret not in text_ and also not in text_ and "sw68a secret" not in text_
        r = await t._send(live, "GET", ORDER, query=f"task_ids={v},{u},{x2}", signed=None)
        plan = r.json()["plan"][v]
        assert plan["ready"] is False and plan["blocked_by"] == []
        assert plan["blocked_by_hidden"] == 1
        assert secret not in r.text and also not in r.text

    async def test_a_pinned_agent_does_not_plan_what_its_pin_hides(self, live):
        """An agent pinned to the team project plans no slice of another project
        (the pin narrows the input of the engine). The slice stays in the order."""
        _pinned(live)
        epic = _epic(live, "sw68a pin")
        a = _slice(live, epic, "sw68a pin a", 30)
        b = _slice(live, epic, "sw68a pin b", 30)
        _block(live, a, b)
        far = _under(live, epic, live.other, live.s.other, "sw68a pin far", 30)
        r = await t._send(live, "GET", ORDER, query=f"task_ids={far},{b},{a}",
                          signed="pinned")
        assert r.status_code == 200, r.text
        body = r.json()
        assert far not in body["plan"] and far in body["order"]
        assert set(body["plan"]) == {a, b}
        assert "sw68a pin far" not in r.text

    async def test_a_task_of_another_tenant_never_joins_the_plan(self, live):
        """A tenant boundary: a task of another tenant never joins the plan."""
        epic = _epic(live, "sw68a tenant")
        a = _slice(live, epic, "sw68a tenant a", 30)
        _slice(live, epic, "sw68a tenant b", 30)
        org2 = live.seeder.one("SELECT id FROM organization WHERE slug = 'mcpassign2'")
        other = w._Seeder(live.admin, str(org2))
        project = other.project("sw68a org2")
        status = other.status(project, "To do", "todo", 0)
        foreign = other.task(project, status, "sw68a org2 task")
        r = await t._send(live, "GET", ORDER, query=f"task_ids={foreign},{a}", signed=None)
        assert r.status_code == 200, r.text
        assert foreign not in r.json()["plan"] and foreign in r.json()["order"]

    async def test_with_the_flag_off_the_route_answers_the_input_order(
            self, live, monkeypatch):
        from acb_common import get_settings

        epic = _epic(live, "sw68a flag")
        y = _slice(live, epic, "sw68a flag y", 30)
        x = _slice(live, epic, "sw68a flag x", 300)
        j = _slice(live, epic, "sw68a flag j", 60)
        _block(live, x, j)
        _block(live, y, j)
        monkeypatch.setattr(get_settings(), "my_work_plan_order_orgs", "")
        r = await t._send(live, "GET", ORDER, query=f"task_ids={y},{x},{j}", signed=None)
        assert r.json() == {"order": [y, x, j], "plan": {}, "labels": {}}
        assert all("plan" not in row for row in await _mcp_rows(live))
        monkeypatch.setattr(get_settings(), "my_work_plan_order_orgs", "*")
        r = await t._send(live, "GET", ORDER, query=f"task_ids={y},{x},{j}", signed=None)
        assert r.json()["order"][0] == x

    async def test_two_hidden_blockers_count_twice(self, live):
        seeder = live.seeder
        epic = _epic(live, "sw68a hidden count")
        v = _slice(live, epic, "sw68a hc v", 30)
        _slice(live, epic, "sw68a hc other", 30, mine=False)
        hidden_project = seeder.project("sw68a hc project", in_space=True)
        for node in (hidden_project, seeder.root(hidden_project)):
            seeder.one("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"
                       " AND subject = :s RETURNING project_id", p=node, s=w.MEMBER)
        status = seeder.status(hidden_project, "To do", "todo", 0)
        for n in (1, 2):
            _block(live, _under(live, epic, hidden_project, status, f"sw68a hc h{n}", 5), v)
        r = await t._send(live, "GET", ORDER, query=f"task_ids={v}", signed=None)
        assert r.json()["plan"][v]["blocked_by_hidden"] == 2
        assert "sw68a hc h" not in r.text

    @staticmethod
    def _hidden_task(live, label: str, epic: str) -> str:
        """An open task in a project that the member cannot see."""
        seeder = live.seeder
        project = seeder.project(f"{label} project", in_space=True)
        for node in (project, seeder.root(project)):
            seeder.one("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"
                       " AND subject = :s RETURNING project_id", p=node, s=w.MEMBER)
        status = seeder.status(project, "To do", "todo", 0)
        return _under(live, epic, project, status, label, 5)

    async def test_a_hidden_blocker_of_a_mid_task_holds_the_slice(self, live):
        epic = _epic(live, "sw68a mid")
        mid = _under(live, epic, live.team, live.s.todo, "sw68a mid task", None)
        s1 = _under(live, mid, live.team, live.s.todo, "sw68a mid slice", 30)
        _assign(live, s1, w.MEMBER)
        _block(live, self._hidden_task(live, "sw68a mid secret", epic), mid)
        r = await t._send(live, "GET", ORDER, query=f"task_ids={s1}", signed=None)
        plan = r.json()["plan"][s1]
        assert plan["ready"] is False and plan["blocked_by_hidden"] == 1
        assert "sw68a mid secret" not in r.text
        rows = {x["task_id"]: x for x in await _mcp_rows(live)}
        assert rows[s1]["plan"]["ready"] is False

    async def test_a_hidden_blocker_of_the_epic_holds_every_slice(self, live):
        epic = _epic(live, "sw68a epic block")
        s1 = _slice(live, epic, "sw68a epic block s1", 30)
        s2 = _slice(live, epic, "sw68a epic block s2", 30)
        _block(live, self._hidden_task(live, "sw68a epic secret", epic), epic)
        r = await t._send(live, "GET", ORDER, query=f"task_ids={s1},{s2}", signed=None)
        plans = r.json()["plan"]
        assert all(plans[x]["ready"] is False and plans[x]["blocked_by_hidden"] == 1
                   for x in (s1, s2))
        assert "sw68a epic secret" not in r.text

    async def test_one_hidden_blocker_of_the_slice_and_its_parent_counts_once(self, live):
        epic = _epic(live, "sw68a once")
        mid = _under(live, epic, live.team, live.s.todo, "sw68a once mid", None)
        s1 = _under(live, mid, live.team, live.s.todo, "sw68a once slice", 30)
        _assign(live, s1, w.MEMBER)
        secret = self._hidden_task(live, "sw68a once secret", epic)
        _block(live, secret, mid)
        _block(live, secret, s1)
        r = await t._send(live, "GET", ORDER, query=f"task_ids={s1}", signed=None)
        assert r.json()["plan"][s1]["blocked_by_hidden"] == 1

    async def test_the_epic_slices_have_the_same_relative_order_on_both_surfaces(
            self, live, monkeypatch):
        """The slices of one epic get the same relative order in the MCP and in
        the chat. Plain rows keep the base order of each surface, and the chat
        puts a task with a due date first (``assigned-to-me``), so a plain row
        may sit elsewhere. A blocked slice is never ahead of a ready slice."""
        from sqlalchemy import text

        epic = _epic(live, "sw68a agree")
        x = _slice(live, epic, "sw68a agree x", 300)
        pa = _plain(live, "sw68a agree plain a")
        y = _slice(live, epic, "sw68a agree y", 30)
        first, second = _epic(live, "sw68a agree first"), _epic(live, "sw68a agree second")
        _slice(live, first, "sw68a agree p", 60, mine=False)
        pb = _plain(live, "sw68a agree plain b")
        z = _slice(live, second, "sw68a agree z", 60)
        j = _slice(live, epic, "sw68a agree j", 60)
        _block(live, x, j)
        _block(live, y, j)
        _block(live, first, second)
        # A due date on one plain task moves it first in the chat only.
        live.seeder.one("UPDATE pm_tasks SET due_at = now() + interval '1 day'"
                        " WHERE id = CAST(:t AS uuid) RETURNING id", t=pb)
        mcp = [r for r in await _mcp_rows(live) if r["reason"] == "assigned"]
        titles, text_ = await _chat(live, monkeypatch)
        ids = [r["task_id"] for r in mcp]
        assert {x, y, z, pa, pb} <= set(ids) and j not in ids
        with live.admin.begin() as c:
            name = {str(r[0]): r[1] for r in c.execute(
                text("SELECT id, title FROM pm_tasks WHERE id = ANY(CAST(:i AS uuid[]))"),
                {"i": ids})}
        slices = [x, y, z]
        mcp_order = [i for i in ids if i in slices]
        chat_order = sorted(slices, key=lambda i: _where(titles, name[i]))
        assert chat_order == mcp_order, (chat_order, mcp_order)
        assert mcp_order.index(x) < mcp_order.index(y) and mcp_order[-1] == z
        _top_is_not_blocked(mcp)
        assert "plan: blocked by #" in text_ and "sw68a agree first epic" in text_
        assert "plan: ready · slack 0 mins" in text_
