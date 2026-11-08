"""PJP-6a — ``GET /projects/plan/analysis`` on a real database.

Spec: ``project-docs/specs/project_planning.md`` §3.4 and §10.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_plan_analysis_r8.py -v -rs

The route calls the real gateway app as a NON-privileged role. It proves:
a tree gives its waves and path, a hidden slice is counted and never named,
a pinned agent is narrowed, an open blocker outside the tree holds its task,
and another tenant's task answers the text of a task that does not exist.

Mutations (R7). ``G`` is ``apps/services/gateway/gateway/routes/projects``.

* A1. ``G/plan_analysis.py``: ``_TREE_SQL`` loses ``({visible}) AS vis``. ``test_a_hidden_slice_is_counted_and_not_named``.
* A2. ``G/plan_analysis.py``: the root is not loaded with ``load_visible_task``. ``test_another_tenant_and_a_hidden_root_read_as_missing``.
* A3. ``G/plan_analysis.py``: the pinned agent's ``vis.params`` are dropped. ``test_a_pinned_agent_is_narrowed``.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")


from tests.unit import test_mcp_edge_task_writes as t
from tests.unit import test_mcp_edge_writes as w
from tests.unit.test_mcp_edge_assignees import world  # noqa: F401  (the fixture)

_DB_GATE = w._DB_GATE
live_app = t.live_app
live = t.live
redis_fake = t.redis_fake

PATH = "/projects/plan/analysis"


def _under(live, parent: str, project: str, status: str, title: str, mins: int | None) -> str:
    task = live.task(project, status, title)
    live.seeder.one(
        "UPDATE pm_tasks SET parent_task_id = CAST(:p AS uuid), estimate_mins = :m"
        " WHERE id = CAST(:t AS uuid) RETURNING id", p=parent, m=mins, t=task)
    return task


def _block(live, blocker: str, blocked: str) -> None:
    live.seeder.one(
        "INSERT INTO pm_task_links (source_task_id, target_task_id, link_type, created_by,"
        " organization_id) VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 'blocks', :w,"
        " CAST(:o AS uuid)) RETURNING id", a=blocker, b=blocked, w=w.OWNER, o=live.org)


async def _read(live, root: str, *, signed: str | None = None, extra: str = ""):
    return await t._send(
        live, "GET", PATH, query=f"root_task_id={root}{extra}", signed=signed)


def _pinned(live) -> None:
    if "pinned" not in live.c:
        live.c["pinned"], live.tok["pinned"] = live.seeder.connection(
            scopes=("projects:read",), pins=(live.team,))


def _tree(live, label: str) -> dict[str, str]:
    """An epic in the team project with 4 slices: a -> b -> c, and a lone d."""
    epic = live.task(live.team, live.s.todo, f"{label} epic")
    ids = {"epic": epic}
    for key, mins in (("a", 60), ("b", 120), ("c", 30), ("d", 30)):
        ids[key] = _under(live, epic, live.team, live.s.todo, f"{label} {key}", mins)
    _block(live, ids["a"], ids["b"])
    _block(live, ids["b"], ids["c"])
    return ids


class TestThePlanRoute:
    pytestmark = _DB_GATE

    async def test_a_tree_gives_waves_and_a_path(self, live):
        ids = _tree(live, "pjp6a tree")
        r = await _read(live, ids["epic"], extra="&start=2026-11-02")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["root_task_id"] == ids["epic"]
        assert body["ready"] == sorted([ids["a"], ids["d"]])
        assert body["waves"][1:] == [[ids["b"]], [ids["c"]]]
        assert body["critical_path"] == [ids["a"], ids["b"], ids["c"]]
        assert body["finish"] == {"mins": 60 + 120 + 30 + 180, "working_days": 2,
                                  "date": "2026-11-03"}
        assert body["missing"]["no_owner"] == 4
        assert body["hidden"] == {"slices": 0, "outside_blockers": 0}
        # Ids and numbers only: no title is in the answer.
        assert "pjp6a tree" not in r.text

    async def test_a_hidden_slice_is_counted_and_not_named(self, live):
        ids = _tree(live, "pjp6a hidden")
        seeder = live.seeder
        hidden_project = seeder.project("pjp6a hidden project", in_space=True)
        for node in (hidden_project, seeder.root(hidden_project)):
            seeder.one("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"
                       " AND subject = :s RETURNING project_id", p=node, s=w.MEMBER)
        status = seeder.status(hidden_project, "To do", "todo", 0)
        secret = _under(live, ids["epic"], hidden_project, status, "pjp6a secret slice", 10)
        _block(live, ids["c"], secret)
        r = await _read(live, ids["epic"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["hidden"]["slices"] == 1
        assert secret not in r.text and "pjp6a secret" not in r.text
        assert body["critical_path"] == [ids["a"], ids["b"], ids["c"]]

    async def test_a_pinned_agent_is_narrowed(self, live, monkeypatch):
        # The route is not on the MCP edge allowlist yet (PJP-6b adds it with the
        # tool). The test opens it for this one test, to prove the narrowing that
        # the one visibility helper gives a signed agent.
        from acb_auth import agent_oauth

        monkeypatch.setattr(
            agent_oauth, "MCP_ROUTES", agent_oauth.MCP_ROUTES | {("GET", PATH)})
        _pinned(live)
        ids = _tree(live, "pjp6a pin")
        outside = live.task(live.other, live.s.other, "pjp6a pin outside")
        far = _under(live, ids["epic"], live.other, live.s.other, "pjp6a pin far", 10)
        _block(live, ids["c"], far)
        _block(live, outside, ids["d"])
        pinned = await _read(live, ids["epic"], signed="pinned")
        assert pinned.status_code == 200, pinned.text
        body = pinned.json()
        assert body["hidden"]["slices"] == 1
        assert far not in pinned.text and outside not in pinned.text
        # The member without a pin sees the far slice, and the outside blocker.
        person = (await _read(live, ids["epic"])).json()
        assert person["hidden"]["slices"] == 0
        assert far in json.dumps(person)
        assert person["outside_blockers"] == [{"task_id": ids["d"], "blocked_by": [outside]}]
        # The pinned agent holds `d` back too, but names nothing.
        assert body["outside_blockers"] == []
        assert body["hidden"]["outside_blockers"] == 1
        assert ids["d"] in body["ready"]

    async def test_an_open_outside_blocker_holds_its_task(self, live):
        ids = _tree(live, "pjp6a outside")
        blocker = live.task(live.team, live.s.todo, "pjp6a outside blocker")
        _block(live, blocker, ids["a"])
        body = (await _read(live, ids["epic"])).json()
        assert body["ready"] == [ids["d"]]
        assert body["outside_blockers"] == [{"task_id": ids["a"], "blocked_by": [blocker]}]
        assert ids["a"] in body["held"]

    async def test_another_tenant_and_a_hidden_root_read_as_missing(self, live):
        org2 = live.seeder.one("SELECT id FROM organization WHERE slug = 'mcpassign2'")
        other = w._Seeder(live.admin, str(org2))
        project = other.project("pjp6a org2")
        status = other.status(project, "To do", "todo", 0)
        foreign = other.task(project, status, "pjp6a org2 task")
        made_up = str(uuid.uuid4())
        a = await _read(live, foreign)
        b = await _read(live, made_up)
        assert a.status_code == b.status_code == 404
        assert a.json() == b.json()
        bad = await _read(live, "not-a-uuid")
        assert bad.status_code == 404 and bad.json() == a.json()
        # The tree of another tenant never leaks into a tree of this one.
        ids = _tree(live, "pjp6a iso")
        body = (await _read(live, ids["epic"])).json()
        assert foreign not in json.dumps(body)

    async def test_a_cycle_reports_ids_and_no_path(self, live):
        ids = _tree(live, "pjp6a cycle")
        _block(live, ids["c"], ids["a"])
        body = (await _read(live, ids["epic"])).json()
        assert body["cycle"]["task_ids"] == sorted([ids["a"], ids["b"], ids["c"]])
        assert body["critical_path"] == [] and body["waves"] == []


def test_the_route_is_in_the_manifest_without_a_tool():
    from skill_projects import manifest

    rows: list[Any] = [r for r in manifest.MANIFEST
                       if (r.method, r.path) == ("GET", PATH)]
    assert len(rows) == 1
    assert rows[0].cls == "X" and rows[0].tool == "" and "PJP-6b" in rows[0].reason
