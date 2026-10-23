"""WS-64 OP-6a — the area tools against the real routes (R8, D-OP-9).

Spec: ``project-docs/specs/one_projects_app.md`` §5a and §9 OP-6a.

``create_area``, ``rename_area`` and ``delete_area`` run through the skill's
own client against the REAL ``/projects/my/areas`` router, on a phase-4
promoted scratch database as the non-privileged ``acb_app`` role. Two
members share one organization. The hermetic fences prove the cards and the
manifest. This file proves what the fakes cannot: the route finds an area by
its OWNER, so one member cannot rename or delete another member's area, and
``DELETE`` hard-deletes an empty area and archives any other.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_area_tools_r8.py -v -rs

**The mutations (R7).** Each turned the named test red. Commit before each.

* M1. ``personal.py`` ``_load_my_area``: drop ``lower(personal_owner) = :who``.
  ``test_r8_another_member_cannot_rename_an_area_by_hand`` and
  ``test_r8_another_member_cannot_delete_an_area_by_hand``.
* M2. ``guarded.py`` ``delete_area``: skip the card. ``test_r8_a_declined_card_writes_nothing``.
* M3. ``inbox.py`` ``_my_area``: take ``near[0]`` when two areas are near-equal.
  ``test_r8_two_near_equal_names_are_a_question_not_a_guess``.
* M4. ``guarded.py`` ``delete_area``: the receipt says "Deleted" for both outcomes.
  ``test_r8_an_area_with_a_task_is_archived_and_the_receipt_says_so``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from fastapi import Request
from skill_projects.client import GatewayRefusal, delete, patch, post
from skill_projects.refusals import refusals_as_text

from tests.unit._projects_agent_fakes import approve, deny
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_projects_agent_grants import _route_tools_to

pytestmark = _DB_GATE

ALICE = "alice@areas-r8.test"
BOB = "bob@areas-r8.test"


@pytest.fixture
def world(promoted):  # noqa: F811
    """Org B of the promoted catalog, with Alice and Bob in the directory."""
    from sqlalchemy import text

    org = promoted.org_b
    with promoted.admin_engine.begin() as c:
        for email in (ALICE, BOB):
            c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                    "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                    "(SELECT 1 FROM app_user WHERE lower(email) = :e)"
                ),
                {"e": email, "o": org},
            )
    yield type("World", (), {"org": org, "promoted": promoted})
    with promoted.admin_engine.begin() as c:
        # The tasks first: a task row names its project.
        c.execute(
            text(
                "DELETE FROM pm_tasks WHERE organization_id = CAST(:o AS uuid) "
                "AND created_by = ANY(:e)"
            ),
            {"o": org, "e": [ALICE, BOB]},
        )
        c.execute(
            text(
                "DELETE FROM pm_projects WHERE organization_id = CAST(:o AS uuid) "
                "AND lower(personal_owner) = ANY(:e) AND parent_project_id IS NOT NULL"
            ),
            {"o": org, "e": [ALICE, BOB]},
        )
        c.execute(
            text(
                "DELETE FROM pm_projects WHERE organization_id = CAST(:o AS uuid) "
                "AND lower(personal_owner) = ANY(:e)"
            ),
            {"o": org, "e": [ALICE, BOB]},
        )
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": [ALICE, BOB]})


def _app():
    """The real Projects router, signed in as whoever the header names."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes.projects import router

    from tests.unit._projects_fakes import member_user

    people = {ALICE: member_user(ALICE), BOB: member_user(BOB)}

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    return app


async def _as(world: Any, monkeypatch, email: str, act):
    from acb_common.db import bind_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    _route_tools_to(monkeypatch, _app(), email)
    token = bind_tenant(world.org)
    try:
        url = world.promoted.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            return await act()
    finally:
        release_tenant(token)


def _areas(world: Any, email: str) -> dict[str, dict[str, Any]]:
    """The member's areas straight from the table, by name, as the superuser."""
    from sqlalchemy import text

    with world.promoted.admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, name, archived_at FROM pm_projects "
                "WHERE organization_id = CAST(:o AS uuid) AND lower(personal_owner) = :e "
                "AND parent_project_id IS NOT NULL"
            ),
            {"o": world.org, "e": email},
        ).fetchall()
    return {r.name: {"id": str(r.id), "archived": r.archived_at is not None} for r in rows}


def _task_in(world: Any, email: str, area_id: str, done: bool = False) -> str:
    """One task in an area, made as the superuser. It lands on the root's lane."""
    from sqlalchemy import text

    with world.promoted.admin_engine.begin() as c:
        root = c.execute(
            text(
                "SELECT id FROM pm_projects WHERE organization_id = CAST(:o AS uuid) "
                "AND lower(personal_owner) = :e AND parent_project_id IS NULL"
            ),
            {"o": world.org, "e": email},
        ).scalar_one()
        lane = c.execute(
            text(
                "SELECT id FROM pm_task_statuses WHERE project_id = CAST(:r AS uuid) "
                "AND category = :c ORDER BY position LIMIT 1"
            ),
            {"r": str(root), "c": "done" if done else "todo"},
        ).scalar_one()
        number = c.execute(
            text("SELECT coalesce(max(task_number), 0) + 1 FROM pm_tasks")
        ).scalar_one()
        tid = str(uuid.uuid4())
        c.execute(
            text(
                "INSERT INTO pm_tasks (id, title, project_id, root_project_id, status_id, "
                "created_by, organization_id, task_number) VALUES (CAST(:t AS uuid), 'A task', "
                "CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid), :e, CAST(:o AS uuid), :n)"
            ),
            {"t": tid, "p": area_id, "r": str(root), "s": str(lane), "e": email,
             "o": world.org, "n": number},
        )
    return tid


async def test_r8_create_area_makes_a_private_area(world, monkeypatch) -> None:
    pytest.importorskip("asyncpg")
    asked = approve(monkeypatch)
    out = await _as(world, monkeypatch, ALICE,
                    lambda: refusals_as_text(skill_projects.create_area)("Garden"))
    assert len(asked) == 1 and out.startswith("Made the area «Garden»"), out
    assert set(_areas(world, ALICE)) == {"Garden"}
    assert _areas(world, BOB) == {}
    # Bob's read does not list it, and Bob may make the same name for himself.
    listed = await _as(world, monkeypatch, BOB, skill_projects.my_areas)
    assert "Garden" not in listed
    out = await _as(world, monkeypatch, BOB,
                    lambda: refusals_as_text(skill_projects.create_area)("Garden"))
    assert out.startswith("Made the area"), out


async def test_r8_rename_area_changes_the_name_and_the_card_shows_both(
    world, monkeypatch,
) -> None:
    pytest.importorskip("asyncpg")
    approve(monkeypatch)
    await _as(world, monkeypatch, ALICE, lambda: skill_projects.create_area("Home"))
    asked = approve(monkeypatch)
    out = await _as(world, monkeypatch, ALICE,
                    lambda: refusals_as_text(skill_projects.rename_area)("home", "House"))
    assert out.startswith("Renamed the area to «House»"), out
    assert "«Home» → «House»" in asked[0]["detail"]
    assert set(_areas(world, ALICE)) == {"House"}


async def test_r8_another_member_cannot_rename_an_area_by_hand(world, monkeypatch) -> None:
    """The route, with Alice's real area id, from Bob. NOT FOUND, and no change."""
    pytest.importorskip("asyncpg")
    approve(monkeypatch)
    await _as(world, monkeypatch, ALICE, lambda: skill_projects.create_area("Home"))
    alice_area = _areas(world, ALICE)["Home"]["id"]
    # Bob needs a personal root of his own, so the 404 is not a missing-root accident.
    await _as(world, monkeypatch, BOB, lambda: skill_projects.create_area("Mine"))

    async def by_hand() -> Any:
        return await patch(f"/projects/my/areas/{alice_area}", {"name": "Hijacked"})

    with pytest.raises(GatewayRefusal) as err:
        await _as(world, monkeypatch, BOB, by_hand)
    assert err.value.status == 404
    assert set(_areas(world, ALICE)) == {"Home"}


async def test_r8_another_member_cannot_delete_an_area_by_hand(world, monkeypatch) -> None:
    pytest.importorskip("asyncpg")
    approve(monkeypatch)
    await _as(world, monkeypatch, ALICE, lambda: skill_projects.create_area("Home"))
    alice_area = _areas(world, ALICE)["Home"]["id"]
    await _as(world, monkeypatch, BOB, lambda: skill_projects.create_area("Mine"))

    async def by_hand() -> Any:
        return await delete(f"/projects/my/areas/{alice_area}")

    with pytest.raises(GatewayRefusal) as err:
        await _as(world, monkeypatch, BOB, by_hand)
    assert err.value.status == 404
    assert _areas(world, ALICE)["Home"] == {"id": alice_area, "archived": False}


async def test_r8_the_tools_refuse_another_members_area_id_before_any_card(
    world, monkeypatch,
) -> None:
    """Through the tools: Bob passes Alice's area id. No card, no change."""
    pytest.importorskip("asyncpg")
    approve(monkeypatch)
    await _as(world, monkeypatch, ALICE, lambda: skill_projects.create_area("Home"))
    alice_area = _areas(world, ALICE)["Home"]["id"]
    await _as(world, monkeypatch, BOB, lambda: skill_projects.create_area("Mine"))
    asked = approve(monkeypatch)
    for tool, args in (
        (skill_projects.rename_area, (alice_area, "Hijacked")),
        (skill_projects.delete_area, (alice_area,)),
    ):
        out = await _as(world, monkeypatch, BOB, lambda t=tool, a=args: refusals_as_text(t)(*a))
        assert out.startswith("Refused:") and "no live area with that id" in out, out
    assert asked == []
    assert _areas(world, ALICE)["Home"] == {"id": alice_area, "archived": False}


async def test_r8_an_empty_area_is_deleted_for_good(world, monkeypatch) -> None:
    pytest.importorskip("asyncpg")
    approve(monkeypatch)
    await _as(world, monkeypatch, ALICE, lambda: skill_projects.create_area("Scratch"))
    asked = approve(monkeypatch)
    out = await _as(world, monkeypatch, ALICE,
                    lambda: refusals_as_text(skill_projects.delete_area)("Scratch"))
    assert out.startswith("Deleted the empty area «Scratch»"), out
    assert "impact: 0 open tasks" in asked[0]["context"]
    assert _areas(world, ALICE) == {}


async def test_r8_an_area_with_a_task_is_archived_and_the_receipt_says_so(
    world, monkeypatch,
) -> None:
    """A DONE task still holds the area, though the open count reads 0."""
    pytest.importorskip("asyncpg")
    approve(monkeypatch)
    await _as(world, monkeypatch, ALICE, lambda: skill_projects.create_area("Finance"))
    area = _areas(world, ALICE)["Finance"]["id"]
    _task_in(world, ALICE, area, done=True)
    asked = approve(monkeypatch)
    out = await _as(world, monkeypatch, ALICE,
                    lambda: refusals_as_text(skill_projects.delete_area)("Finance"))
    assert out.startswith("Archived the area «Finance»"), out
    assert "It held 1 task, and they stay" in out
    assert "impact: 0 open tasks" in asked[0]["context"]
    assert "archived and the tasks stay" in asked[0]["context"]
    assert _areas(world, ALICE)["Finance"] == {"id": area, "archived": True}


async def test_r8_a_declined_card_writes_nothing(world, monkeypatch) -> None:
    pytest.importorskip("asyncpg")
    approve(monkeypatch)
    await _as(world, monkeypatch, ALICE, lambda: skill_projects.create_area("Home"))
    before = _areas(world, ALICE)
    asked = deny(monkeypatch)
    for tool, args in (
        (skill_projects.rename_area, ("Home", "House")),
        (skill_projects.delete_area, ("Home",)),
        (skill_projects.create_area, ("Garden",)),
    ):
        out = await _as(world, monkeypatch, ALICE, lambda t=tool, a=args: t(*a))
        assert out.startswith("Cancelled"), out
    assert len(asked) == 3
    assert _areas(world, ALICE) == before


async def test_r8_two_near_equal_names_are_a_question_not_a_guess(world, monkeypatch) -> None:
    pytest.importorskip("asyncpg")
    approve(monkeypatch)
    await _as(world, monkeypatch, ALICE, lambda: skill_projects.create_area("Home"))

    async def second() -> Any:
        # The tool refuses a twin, so the second area is made by the route, by hand.
        return await post("/projects/my/areas", {"name": "home!"})

    await _as(world, monkeypatch, ALICE, second)
    assert set(_areas(world, ALICE)) == {"Home", "home!"}
    asked = approve(monkeypatch)
    before = _areas(world, ALICE)
    for tool, args in (
        (skill_projects.delete_area, ("Home",)),
        (skill_projects.rename_area, ("home", "House")),
    ):
        out = await _as(world, monkeypatch, ALICE, lambda t=tool, a=args: refusals_as_text(t)(*a))
        assert out.startswith("Refused:") and "more than one area" in out, out
    assert asked == []
    assert _areas(world, ALICE) == before
