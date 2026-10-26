"""MCP-G5 - ``GET /projects/tasks`` with ``parent_task_id`` and
``include_descendants``.

Spec: ``project-docs/specs/external_agent_access.md`` section 9.1, ``list_tasks``.

**R8**, a real Postgres, through the real ``/projects`` router as the
non-privileged role. The tree is an epic, two children and three
grandchildren. One child sits in a sealed subproject that the member cannot
open, and a grandchild under it sits in the open project.

* direct children only, with ``parent_task_id``
* the whole subtree with ``include_descendants``, each row with its ``depth``
* the hidden child and the visible grandchild under it are both absent
* an unseen parent answers 404, like a missing task
* ``include_descendants`` with no parent answers 422
* a page cursor walks the subtree in a stable order

R7, the mutations (run after a commit, because ``git checkout`` restores HEAD):

* **M1** ``DESCENDANTS_CTE`` drops ``{visible}`` from the recursive arm
  -> ``test_the_walk_does_not_pass_through_a_hidden_task``.
* **M1b** ``DESCENDANTS_CTE`` drops ``{visible}`` from the first arm
  -> the same test.
* **M2** the recursive arm keeps ``w.depth`` and adds nothing
  -> ``test_descendants_carry_their_depth``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

OWNER = "g5-owner@subtree-r8.test"
MEMBER = "g5-member@subtree-r8.test"

pytestmark = _DB_GATE


def _child_of(p: Any, child: str, parent: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_tasks SET parent_task_id = CAST(:par AS uuid) "
            "WHERE id = CAST(:id AS uuid)"), {"par": parent, "id": child})


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 - a fixture
    p = promoted
    b.member(p, p.org_a, OWNER)
    b.member(p, p.org_a, MEMBER)
    root = b.project(p, p.org_a, OWNER)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
            "created_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :s, :e)"),
            {"o": p.org_a, "p": root["id"], "s": MEMBER, "e": OWNER})
    sealed = b.project(p, p.org_a, OWNER, parent=root, sealed=True)

    def t(proj: dict, title: str, assignee: str | None = None) -> str:
        return b.task(p, p.org_a, OWNER, proj, title=title, assignee=assignee)

    ns = SimpleNamespace(p=p)
    ns.epic = t(root, "epic")
    ns.open_child = t(root, "open child")
    ns.hidden_child = t(sealed, "hidden child", OWNER)  # a sealed task shows only to its assignee
    ns.open_grand = t(root, "grandchild of the open child")
    ns.under_hidden = t(root, "visible grandchild of the hidden child")
    ns.deep = t(root, "great-grandchild of the open child")
    ns.hidden_grand = t(sealed, "hidden grandchild", OWNER)
    ns.under_hidden_grand = t(root, "visible great-grandchild of the hidden grandchild")
    for child, parent in ((ns.open_child, ns.epic), (ns.hidden_child, ns.epic),
                          (ns.open_grand, ns.open_child), (ns.under_hidden, ns.hidden_child),
                          (ns.deep, ns.open_grand), (ns.hidden_grand, ns.open_child),
                          (ns.under_hidden_grand, ns.hidden_grand)):
        _child_of(p, child, parent)
    ns.owner = b._principal(OWNER, p.org_a)
    ns.member = b._principal(MEMBER, p.org_a, unrestricted=False)
    ns.other_org = b._principal("g5-b@subtree-r8.test", p.org_b)
    return ns


async def _list(w: Any, user: Any, query: str) -> Any:
    async with b._session(w.p) as call:
        return await call(user, "GET", f"/projects/tasks?{query}")


def _ids(got: Any) -> list[str]:
    assert got.status_code == 200, got.text
    return [r["id"] for r in got.json()["rows"]]


class TestTheSubtree:
    async def test_direct_children_only(self, world):
        w = world
        got = await _list(w, w.owner, f"parent_task_id={w.epic}")
        assert set(_ids(got)) == {w.open_child, w.hidden_child}
        assert got.json()["total"] == 2
        assert all(r["depth"] is None for r in got.json()["rows"])

    async def test_descendants_carry_their_depth(self, world):
        w = world
        got = await _list(w, w.owner, f"parent_task_id={w.epic}&include_descendants=true")
        depth = {r["id"]: r["depth"] for r in got.json()["rows"]}
        assert depth == {w.open_child: 1, w.hidden_child: 1, w.open_grand: 2,
                         w.under_hidden: 2, w.deep: 3, w.hidden_grand: 2,
                         w.under_hidden_grand: 3}
        parent = {r["id"]: r["parent_task_id"] for r in got.json()["rows"]}
        assert parent[w.deep] == w.open_grand
        assert got.json()["total"] == 7

    async def test_the_walk_does_not_pass_through_a_hidden_task(self, world):
        """The hidden child (level 1) and the hidden grandchild (level 2) each
        hide a visible task below them. Both are absent."""
        w = world
        got = await _list(w, w.member, f"parent_task_id={w.epic}&include_descendants=true")
        assert set(_ids(got)) == {w.open_child, w.open_grand, w.deep}
        assert got.json()["total"] == 3
        direct = await _list(w, w.member, f"parent_task_id={w.epic}")
        assert set(_ids(direct)) == {w.open_child}

    async def test_an_unseen_parent_answers_like_a_missing_task(self, world):
        w = world
        hidden = await _list(w, w.member, f"parent_task_id={w.hidden_child}")
        missing = await _list(w, w.member, f"parent_task_id={uuid.uuid4()}")
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()
        both = await _list(w, w.member,
                           f"parent_task_id={w.hidden_child}&include_descendants=true")
        assert both.status_code == 404
        foreign = await _list(w, w.other_org, f"parent_task_id={w.epic}")
        assert foreign.status_code == 404

    async def test_descendants_without_a_parent_is_refused(self, world):
        got = await _list(world, world.owner, "include_descendants=true")
        assert got.status_code == 422

    async def test_pages_walk_the_subtree_in_a_stable_order(self, world):
        w = world
        base = f"parent_task_id={w.epic}&include_descendants=true&page_size=2"
        seen: list[str] = []
        depths: list[int] = []
        for page in (1, 2, 3, 4):
            got = await _list(w, w.owner, f"{base}&page={page}")
            assert got.status_code == 200, got.text
            assert got.json()["total"] == 7
            seen += [r["id"] for r in got.json()["rows"]]
            depths += [r["depth"] for r in got.json()["rows"]]
        assert len(seen) == 7 and len(set(seen)) == 7
        assert depths == sorted(depths), "shallow rows come first"
        again = await _list(w, w.owner, f"{base}&page=1")
        assert _ids(again) == seen[:2]
