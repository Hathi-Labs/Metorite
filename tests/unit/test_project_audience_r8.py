"""RX-1: who can open a project, against a real database (R8).

``GET /projects/nodes/{id}/audience`` feeds the card of a chat that read outside
mail. A card that says the wrong people is no guard, and hermetic fakes agree with
whatever SQL they get. This file runs the real route on a promoted catalog, as a
NON-privileged role. The world is the one of ``test_projects_write_authority.py``.

What a fake cannot prove, and this file does:

* a grant to a person, to ``org`` and on a parent each count, through the one
  predicate ``Visibility.project_clause``;
* a member who cannot see the project gets the 404 of any other read (R5);
* ``gains`` compares two projects, as a move does;
* the two chat cards read the real route.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_project_audience_r8.py -v -rs

**The mutations (R7).** Each one turned the named test red. Commit before each.

* A1. ``notifications.project_audience``: ``without_access`` empty.
  ``test_r8_the_audience_names_who_holds_it_and_who_does_not``.
* A2. ``notifications.project_audience``: ``everyone`` always False.
  ``test_r8_an_org_grant_is_everyone``.
* A3. ``tree.read_project_audience``: drop the read of ``load_visible_project``
  for ``beyond``. ``test_r8_a_project_the_member_cannot_see_is_a_404``.
* A4. ``notifications.project_audience``: ``gains`` from *beyond* minus the
  audience. ``test_r8_a_move_names_who_it_opens_the_row_to``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

import acb_skills.egress as eg

from tests.unit import test_mcp_edge_delete as d
from tests.unit import test_mcp_edge_writes as w
from tests.unit._projects_agent_fakes import approve
from tests.unit.test_projects_grant_tools_r8 import OUTSIDER, _grant_rows, _private
from tests.unit.test_projects_write_authority import (  # noqa: F401 — the fixtures
    ADMIN,
    CREATOR,
    LEAD,
    VIEWER,
    _as,
    _chat,
    live,
    live_app,
    redis_fake,
    world,
)

pytestmark = w._DB_GATE


def _name() -> str:
    return f"rx1 audience {uuid.uuid4().hex[:6]}"


async def _audience(wld: Any, who: str, pid: str, **params: str) -> Any:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    path = f"/projects/nodes/{pid}/audience" + (f"?{query}" if query else "")
    return await _as(wld, who, "GET", path)


def _child(wld: Any, parent: str) -> str:
    """A project under *parent*. A root is a space, and a space holds no task."""
    return str(wld.seeder.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses, parent_project_id) VALUES (:n, 'active', 'manual', :c,"
        " CAST(:o AS uuid), 'Asia/Kolkata', true, CAST(:p AS uuid)) RETURNING id",
        n=_name(), c=CREATOR, o=wld.org, p=parent))


def _task(wld: Any) -> str:
    """A task in a project that the four members hold, with its lane."""
    project = _private(wld, _name())
    return str(wld.seeder.task(project, wld.seeder.status(project, "To do", "todo", 0), "One"))


def _held(monkeypatch: Any) -> None:
    async def chat_is_held(_ctx: Any = None) -> bool:
        return True

    monkeypatch.setattr(eg, "chat_is_held", chat_is_held)


async def test_r8_the_audience_names_who_holds_it_and_who_does_not(live) -> None:  # noqa: F811
    pid = _private(live, _name())
    res = await _audience(live, LEAD, pid, people=f"{VIEWER},{OUTSIDER}")
    assert res.status_code == 200, res.text
    got = res.json()
    assert {CREATOR, LEAD, VIEWER} <= set(got["sample"])
    assert OUTSIDER not in got["sample"]
    assert got["without_access"] == [OUTSIDER]
    assert got["everyone"] is False
    assert got["count"] >= 3


async def test_r8_an_org_grant_is_everyone(live) -> None:  # noqa: F811
    pid = _private(live, _name())
    d._grant(live.seeder, pid, "org")
    got = (await _audience(live, LEAD, pid, people=OUTSIDER)).json()
    assert got["everyone"] is True
    assert got["without_access"] == []


async def test_r8_a_grant_on_a_parent_counts(live) -> None:  # noqa: F811
    parent = _private(live, _name())
    d._grant(live.seeder, parent, OUTSIDER)
    child = _child(live, parent)
    got = (await _audience(live, LEAD, child, people=OUTSIDER)).json()
    assert got["without_access"] == [], got


async def test_r8_a_project_the_member_cannot_see_is_a_404(live) -> None:  # noqa: F811
    pid = _private(live, _name())
    other = _private(live, _name())
    d._grant(live.seeder, other, OUTSIDER)
    assert (await _audience(live, OUTSIDER, pid)).status_code == 404
    res = await _audience(live, OUTSIDER, other, beyond=pid)
    assert res.status_code == 404, "a member names no project that they cannot see"


async def test_r8_a_move_names_who_it_opens_the_row_to(live) -> None:  # noqa: F811
    here = _private(live, _name())
    there = _private(live, _name())
    d._grant(live.seeder, there, OUTSIDER)
    got = (await _audience(live, LEAD, there, beyond=here)).json()
    assert OUTSIDER in got["gains"]
    assert got["gains_count"] == len(got["gains"])
    back = (await _audience(live, LEAD, here, beyond=there)).json()
    assert OUTSIDER not in back["gains"], "a move that narrows opens the row to nobody"


async def test_r8_the_read_writes_nothing(live) -> None:  # noqa: F811
    pid = _private(live, _name())
    before = _grant_rows(live, pid)
    await _audience(live, LEAD, pid, people=OUTSIDER)
    assert _grant_rows(live, pid) == before


async def test_r8_a_held_assign_card_names_who_it_opens_the_task_to(live, monkeypatch) -> None:  # noqa: F811
    """The card, through the real route. Mutation: drop the read in ``assign`` -> red."""
    from skill_projects import writes as W

    task = _task(live)
    asked = approve(monkeypatch)
    _held(monkeypatch)
    await _chat(live, monkeypatch, LEAD, lambda: W.assign(task, OUTSIDER))
    assert f"gives access to: «{OUTSIDER}»" in asked[0]["context"]


async def test_r8_a_held_comment_card_names_the_task_audience(live, monkeypatch) -> None:  # noqa: F811
    """``audience`` of the watcher read is the list that ``add_comment`` reads."""
    from skill_projects import writes as W

    task = _task(live)
    assign = await _as(live, LEAD, "PUT", f"/projects/tasks/{task}/assignees",
                       {"assignees": [VIEWER]})
    assert assign.status_code == 200, assign.text
    asked = approve(monkeypatch)
    _held(monkeypatch)
    await _chat(live, monkeypatch, LEAD, lambda: W.comment(task, f"See @{ADMIN}"))
    context = asked[0]["context"]
    assert ADMIN in context and VIEWER in context, context
