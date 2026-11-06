"""WS-42 PST-4 — a lane moves by SLOT inside its stage, never by a sort key (R8).

Spec: ``project-docs/specs/projects_settings.md`` §7.1 (PST-4) and §9.4.

``POST /projects/nodes/{id}/statuses`` and ``PATCH /projects/statuses/{id}``
take ``slot`` (1-based, inside the stage group of the lane) or ``after`` (a lane
name of the same stage). The server renumbers the stage group to 10, 20, 30 and
keeps the one order rule: stage, then position, then name (task 1792). A slot
outside the stage group is refused with the allowed range. The old ``position``
keeps working, and the change is additive, so the server has no flag.
``GET /projects/nodes/{id}/status-set`` returns ``guard_on`` for the chat tools.

**Hermetic half** (no database): the placement rule and the flag reader.

**R8 half.** The REAL ``/projects`` router runs as the non-privileged role of
the phase-4 rehearsal, on a promoted catalog with two organizations, so FORCE
RLS binds every read and write. A hermetic fake agrees with any SQL it gets.

The set of every test (a fresh space for each, ``*`` = the lane that the test
moves)::

    todo         Backlog 10
    in_progress  Doing 10, Review 20
    done         Done* 1, Shipped 5, Released 7, Closed 9   (Done has the LOWEST position)
    cancelled    Dropped 10

Mutations (R7), each turns the named tests red. ``A`` is ``routes/projects/admin.py``.

* M1. ``A`` ``stage_placement``: take the ``slot > size`` test out. The tests
  ``test_a_slot_past_the_stage_is_refused_with_the_range`` and
  ``test_a_slot_counts_inside_the_stage_and_not_the_set``.
* M2. ``A`` ``_renumber_stage``: read the whole set and not the stage group.
  ``test_slot_4_of_4_puts_done_last_and_the_order_rule_holds``.
* M3. The chat tool half is in ``test_projects_status_chat.py``.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_status_slot_r8.py -v -rs
"""

from __future__ import annotations

import contextlib
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import HTTPException, Request
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]


# ── Hermetic ────────────────────────────────────────────────────────────────


def test_this_suite_is_named_in_both_ci_skip_guards() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(
            r"^\s+tests/unit/test_projects_status_slot_r8\.py \\?$", workflow, re.M,
        ), name


def test_a_slot_is_one_based_inside_the_stage() -> None:
    from gateway.routes.projects.admin import stage_placement

    others = [("a", "Shipped"), ("b", "Released"), ("c", "Closed")]
    # The lane itself is the 4th of 4. Its place is an index into the others.
    assert stage_placement(others, "done", slot=1, after=None) == 0
    assert stage_placement(others, "done", slot=4, after=None) == 3
    assert stage_placement([], "done", slot=1, after=None) == 0


def test_a_slot_outside_the_stage_is_refused_with_the_range() -> None:
    from gateway.routes.projects.admin import stage_placement

    others = [("a", "Shipped"), ("b", "Released"), ("c", "Closed")]
    for bad in (0, -1, 5, 9):
        with pytest.raises(HTTPException) as err:
            stage_placement(others, "done", slot=bad, after=None)
        assert err.value.status_code == 422
        detail = str(err.value.detail)
        assert f"Slot {bad} is outside the stage" in detail
        assert "slot from 1 to 4" in detail and "Shipped, Released, Closed" in detail


def test_after_names_a_lane_of_the_same_stage() -> None:
    from gateway.routes.projects.admin import stage_placement

    others = [("a", "Shipped"), ("b", "Released")]
    assert stage_placement(others, "done", slot=None, after=" released ") == 2
    with pytest.raises(HTTPException) as err:
        stage_placement(others, "done", slot=None, after="Doing")
    assert err.value.status_code == 422 and "Doing" in str(err.value.detail)
    with pytest.raises(HTTPException):
        stage_placement(others, "done", slot=1, after="Shipped")


def test_the_flag_reader_is_the_org_list_idiom(monkeypatch) -> None:
    from acb_common import get_settings
    from gateway.routes.projects.admin import status_set_guard_allowed

    settings = get_settings()
    monkeypatch.setattr(settings, "status_set_guard_orgs", "")
    assert status_set_guard_allowed("acme") is False
    monkeypatch.setattr(settings, "status_set_guard_orgs", "x, ACME")
    assert status_set_guard_allowed("acme") is True
    assert status_set_guard_allowed("globex") is False
    assert status_set_guard_allowed(None) is False
    monkeypatch.setattr(settings, "status_set_guard_orgs", "*")
    assert status_set_guard_allowed("globex") is True
    assert status_set_guard_allowed("") is False


# ── R8: the world ───────────────────────────────────────────────────────────

ADMIN = ("feature:projects", "data:org:read", "projects:settings:write")


def _principal(email: str, org: str, *, perms: tuple[str, ...]) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _app(users: dict[str, Any]) -> Any:
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes.projects import router as projects_router

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(projects_router)
    app.dependency_overrides[get_current_user] = _caller
    return app


class _Client:
    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client, self.users = client, users

    async def send(self, method: str, user: Any, path: str, body: Any = None) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        return await self.client.request(
            method, path, json=body, headers={"x-test-as": key},
        )


@contextlib.asynccontextmanager
async def _session(p: Any):
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(users)),
                base_url="http://status-slot.test",
            ) as client,
        ):
            yield _Client(client, users)
    finally:
        release_tenant(scope)


_LANES = (
    ("Backlog", "todo", 10),
    ("Doing", "in_progress", 10),
    ("Review", "in_progress", 20),
    # The lowest position of the set, in the stage that draws late.
    ("Done", "done", 1),
    ("Shipped", "done", 5),
    ("Released", "done", 7),
    ("Closed", "done", 9),
    ("Dropped", "cancelled", 10),
)


def _project(c: Any, org: str, name: str, *, parent: str | None, owns: bool) -> str:
    pid = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, "
        "parent_project_id, status, kind) "
        "VALUES (:id, :o, :n, 'seed@statusslot.test', :own, CAST(:par AS uuid), 'active',"
        " 'project')"),
        {"id": pid, "o": org, "n": name, "own": owns, "par": parent})
    return pid


def _user(c: Any, org: str, email: str) -> None:
    c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
        {"e": email, "o": org})


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    a, b = promoted.org_a, promoted.org_b
    ns = SimpleNamespace(p=promoted, a=a, b=b)
    ns.admin, ns.b_admin = "admin@statusslot.test", "admin-b@statusslot.test"
    with promoted.admin_engine.begin() as c:
        _user(c, a, ns.admin)
        _user(c, b, ns.b_admin)
    ns.admin_u = _principal(ns.admin, a, perms=ADMIN)
    ns.b_admin_u = _principal(ns.b_admin, b, perms=ADMIN)
    return ns


@pytest.fixture
def space(world):
    """A fresh space with the lanes above, and one subproject that uses them."""
    w = world
    with w.p.admin_engine.begin() as c:
        sp = _project(c, w.a, f"Slot space {uuid.uuid4().hex[:6]}", parent=None, owns=True)
        sub = _project(c, w.a, "Sub", parent=sp, owns=False)
        for name, category, position in _LANES:
            c.execute(text(
                "INSERT INTO pm_task_statuses (project_id, name, color, position, category) "
                "VALUES (CAST(:p AS uuid), :n, 'gray', :pos, :c)"),
                {"p": sp, "n": name, "pos": position, "c": category})
    return SimpleNamespace(id=sp, sub=sub)


def _read(w: Any, owner: str) -> list[tuple[str, str, int]]:
    """The set in the ONE order (``core.status_order_sql``), as the server ranks it."""
    from gateway.routes.projects.core import status_order_sql

    with w.p.admin_engine.connect() as c:
        return [
            (r.name, r.category, int(r.position)) for r in c.execute(text(
                "SELECT name, category, position FROM pm_task_statuses "
                f" WHERE project_id = CAST(:p AS uuid) ORDER BY {status_order_sql()}"),
                {"p": owner})
        ]


def _names(w: Any, owner: str, category: str) -> list[str]:
    return [n for n, c, _p in _read(w, owner) if c == category]


def _id(w: Any, owner: str, name: str) -> str:
    with w.p.admin_engine.connect() as c:
        return str(c.execute(text(
            "SELECT id FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid) AND name = :n"),
            {"p": owner, "n": name}).scalar_one())


# ── R8: the slot ────────────────────────────────────────────────────────────


@_DB_GATE
async def test_slot_4_of_4_puts_done_last_and_the_order_rule_holds(world, space) -> None:
    """Done holds the lowest position, so it draws first IN its stage. Slot 4
    of the 4 lanes of the done stage puts it last there. The set still reads
    stage, then position, then name (M2)."""
    w = world
    before = _read(w, space.id)
    async with _session(w.p) as call:
        res = await call.send(
            "PATCH", w.admin_u, f"/projects/statuses/{_id(w, space.id, 'Done')}", {"slot": 4})
    assert res.status_code == 200, res.text
    assert _names(w, space.id, "done") == ["Shipped", "Released", "Closed", "Done"]
    after = _read(w, space.id)
    # The stage order of the whole set did not move.
    assert [c for _n, c, _p in after] == [c for _n, c, _p in before]
    # The other stages kept their positions.
    keep = lambda rows, cat: [r for r in rows if r[1] == cat]  # noqa: E731
    for stage in ("todo", "in_progress", "cancelled"):
        assert keep(after, stage) == keep(before, stage), stage
    # The group is numbered 10, 20, 30, 40: no gap and no tie.
    assert [p for _n, c, p in after if c == "done"] == [10, 20, 30, 40]


@_DB_GATE
async def test_a_slot_past_the_stage_is_refused_with_the_range(world, space) -> None:
    w = world
    before = _read(w, space.id)
    async with _session(w.p) as call:
        done = _id(w, space.id, "Done")
        too_far = await call.send("PATCH", w.admin_u, f"/projects/statuses/{done}", {"slot": 5})
        zero = await call.send("PATCH", w.admin_u, f"/projects/statuses/{done}", {"slot": 0})
    for res in (too_far, zero):
        assert res.status_code == 422, res.text
        assert "slot from 1 to 4" in res.json()["detail"]
        assert "done stage" in res.json()["detail"]
    # Nothing moved.
    assert _read(w, space.id) == before


@_DB_GATE
async def test_a_slot_counts_inside_the_stage_and_not_the_set(world, space) -> None:
    """The model once sent slot 3 to 6 for a set of 8, and that put Done first.
    Review is in the in_progress stage of 2 lanes: slot 3 is out of range
    although the set holds 8 lanes (M1)."""
    w = world
    async with _session(w.p) as call:
        review = _id(w, space.id, "Review")
        res = await call.send("PATCH", w.admin_u, f"/projects/statuses/{review}", {"slot": 3})
        ok = await call.send("PATCH", w.admin_u, f"/projects/statuses/{review}", {"slot": 1})
    assert res.status_code == 422 and "slot from 1 to 2" in res.json()["detail"]
    assert ok.status_code == 200
    assert _names(w, space.id, "in_progress") == ["Review", "Doing"]
    assert _names(w, space.id, "done") == ["Done", "Shipped", "Released", "Closed"]


@_DB_GATE
async def test_after_places_a_lane_behind_a_named_lane_of_its_stage(world, space) -> None:
    w = world
    async with _session(w.p) as call:
        done = _id(w, space.id, "Done")
        ok = await call.send("PATCH", w.admin_u, f"/projects/statuses/{done}", {"after": "released"})
        elsewhere = await call.send(
            "PATCH", w.admin_u, f"/projects/statuses/{done}", {"after": "Doing"})
        itself = await call.send(
            "PATCH", w.admin_u, f"/projects/statuses/{done}", {"after": "Done"})
        both = await call.send(
            "PATCH", w.admin_u, f"/projects/statuses/{done}", {"after": "Shipped", "slot": 1})
        mixed = await call.send(
            "PATCH", w.admin_u, f"/projects/statuses/{done}", {"slot": 2, "position": 3})
    assert ok.status_code == 200, ok.text
    assert _names(w, space.id, "done") == ["Shipped", "Released", "Done", "Closed"]
    # A name from another stage, the lane itself, two ways and a mix: all 422.
    for res in (elsewhere, itself, both, mixed):
        assert res.status_code == 422, res.text
    assert "Doing" in elsewhere.json()["detail"]
    assert _names(w, space.id, "done") == ["Shipped", "Released", "Done", "Closed"]


@_DB_GATE
async def test_a_new_lane_takes_a_slot_in_its_stage(world, space) -> None:
    w = world
    async with _session(w.p) as call:
        first = await call.send(
            "POST", w.admin_u, f"/projects/nodes/{space.id}/statuses",
            {"name": "Triaged", "category": "in_progress", "slot": 1})
        last = await call.send(
            "POST", w.admin_u, f"/projects/nodes/{space.id}/statuses",
            {"name": "Verifying", "category": "in_progress", "slot": 4})
        past = await call.send(
            "POST", w.admin_u, f"/projects/nodes/{space.id}/statuses",
            {"name": "Nowhere", "category": "in_progress", "slot": 6})
        behind = await call.send(
            "POST", w.admin_u, f"/projects/nodes/{space.id}/statuses",
            {"name": "Mid", "category": "in_progress", "after": "doing"})
    assert first.status_code == 201 and last.status_code == 201, (first.text, last.text)
    assert past.status_code == 422 and "slot from 1 to 5" in past.json()["detail"]
    assert behind.status_code == 201
    assert _names(w, space.id, "in_progress") == [
        "Triaged", "Doing", "Mid", "Review", "Verifying"]
    assert [p for _n, c, p in _read(w, space.id) if c == "in_progress"] == [
        10, 20, 30, 40, 50]
    # Other stages did not move, and the new lanes are not in them.
    assert _names(w, space.id, "done") == ["Done", "Shipped", "Released", "Closed"]
    assert "Nowhere" not in [n for n, _c, _p in _read(w, space.id)]


@_DB_GATE
async def test_a_lane_that_changes_stage_takes_its_slot_in_the_new_stage(world, space) -> None:
    w = world
    async with _session(w.p) as call:
        review = _id(w, space.id, "Review")
        res = await call.send(
            "PATCH", w.admin_u, f"/projects/statuses/{review}",
            {"category": "done", "slot": 2})
    assert res.status_code == 200, res.text
    assert _names(w, space.id, "done") == ["Done", "Review", "Shipped", "Released", "Closed"]
    assert _names(w, space.id, "in_progress") == ["Doing"]


@_DB_GATE
async def test_the_old_position_still_works(world, space) -> None:
    """Additive: the server has no flag, and `position` acts as it did."""
    w = world
    async with _session(w.p) as call:
        done = _id(w, space.id, "Done")
        res = await call.send("PATCH", w.admin_u, f"/projects/statuses/{done}", {"position": 99})
        made = await call.send(
            "POST", w.admin_u, f"/projects/nodes/{space.id}/statuses",
            {"name": "Parked", "category": "todo", "position": 15})
    assert res.status_code == 200 and made.status_code == 201
    assert _names(w, space.id, "done") == ["Shipped", "Released", "Closed", "Done"]
    assert _names(w, space.id, "todo") == ["Backlog", "Parked"]


# ── R8: a subproject, the shared set and the copy ───────────────────────────


@_DB_GATE
async def test_own_set_first_then_the_copy_changes_and_the_shared_set_does_not(
    world, space,
) -> None:
    """Acceptance 8. "Set the stages of Sub" on a shared set: `mode=own` copies
    the shared set, and the later add lands on the copy. The shared set is the
    same as before."""
    w = world
    shared_before = _read(w, space.id)
    async with _session(w.p) as call:
        own = await call.send(
            "POST", w.admin_u, f"/projects/nodes/{space.sub}/status-set", {"mode": "own"})
        read = (await call.send(
            "GET", w.admin_u, f"/projects/nodes/{space.sub}/status-set")).json()
        added = await call.send(
            "POST", w.admin_u, f"/projects/nodes/{space.sub}/statuses",
            {"name": "QA", "category": "in_progress", "slot": 2})
    assert own.status_code == 200, own.text
    assert read["owns"] is True and read["owner_id"] == space.sub
    assert added.status_code == 201, added.text
    assert _names(w, space.sub, "in_progress") == ["Doing", "QA", "Review"]
    assert _read(w, space.id) == shared_before


# ── R8: the flag and the tenant ─────────────────────────────────────────────


@_DB_GATE
async def test_guard_on_follows_the_flag_for_the_callers_org(world, space, monkeypatch) -> None:
    from acb_common import get_settings

    w = world
    settings = get_settings()
    path = f"/projects/nodes/{space.sub}/status-set"
    out = {}
    async with _session(w.p) as call:
        for label, value in (("off", ""), ("other", w.b), ("own", w.a), ("star", "*")):
            monkeypatch.setattr(settings, "status_set_guard_orgs", value)
            out[label] = (await call.send("GET", w.admin_u, path)).json()["guard_on"]
    assert out == {"off": False, "other": False, "own": True, "star": True}


@_DB_GATE
async def test_tenant_b_cannot_move_a_lane_of_tenant_a(world, space) -> None:
    w = world
    before = _read(w, space.id)
    async with _session(w.p) as call:
        done = _id(w, space.id, "Done")
        patched = await call.send("PATCH", w.b_admin_u, f"/projects/statuses/{done}", {"slot": 4})
        posted = await call.send(
            "POST", w.b_admin_u, f"/projects/nodes/{space.id}/statuses",
            {"name": "Intruder", "category": "done", "slot": 1})
    assert patched.status_code == 404 and posted.status_code == 404
    assert _read(w, space.id) == before


@_DB_GATE
def test_the_seed_has_teeth(world, space) -> None:
    """The superuser DOES see a lane of org A. So the 404 above comes from RLS,
    and not from an empty seed."""
    assert _id(world, space.id, "Done")
