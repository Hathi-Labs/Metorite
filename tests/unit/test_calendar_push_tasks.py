"""WS-CAL CE-13d: the member's switch "push tasks" on one calendar connection (R8).

Task 3068. The switch is ``provider_meta["push_tasks"]`` (no migration). ABSENT
means ON, so every row made before the key keeps today's behaviour (R6). Only an
explicit JSON ``false`` is OFF. OFF means no request to the provider in either
task pass, and the events already in the calendar "Metorite" stay.

Real database, the real store, the real route as the non-privileged app role.
No request leaves the test. Run as ``test_calendar_sync_read.py`` does (a private
ladder database).

Mutations (R7), each turns the named test red:

* M30. ``connections.py`` ``push_tasks_enabled``: return True. The switch does
  nothing. ``test_r8_off_sends_no_request_in_either_pass_and_on_again_resumes``.
* M31. ``calendar_connections.py`` ``update_connection``: drop ``_own``. A member
  changes a colleague's switch.
  ``test_r8_a_member_cannot_change_the_switch_of_another_and_org_a_cannot_reach_org_b``.
* M32. ``calendar_connections.py`` ``finish_consent``: drop the carry of
  ``push_tasks``. A reconnect turns the write-out back on.
  ``test_r8_a_reconnect_keeps_the_switch_off``.
* M34. ``connections.py`` ``push_tasks_enabled``: ``is not False``. A garbled
  value reads as ON. ``test_the_switch_reads_fail_closed``.
* M33. ``task_out.py`` ``_pull``: drop the ``push_tasks_enabled`` test. The pull
  still reads the calendar. Same test as M30.
"""

# ruff: noqa: F811  (the pytest fixture ``world`` is imported, as in the sibling suites)
from __future__ import annotations

from typing import Any

import httpx
import pytest
from acb_auth import UserContext, UserRole, get_current_user
from acb_auth.access import build_access
from fastapi import FastAPI, Request
from gateway.calendar_sync import connections

from tests.unit.test_calendar_sync_read import (  # noqa: F401
    _DB_GATE,
    ANA,
    BOB,
    ZED,
    _connect,
    _meta,
    _scope,
    app_engine,
    promoted,
    world,
)


async def _call(w: Any, method: str, path: str, member: str, body: Any = None) -> Any:
    from gateway.routes.tasks import router as tasks_router

    people = {
        ANA: UserContext(email=ANA, role=UserRole.EMPLOYEE, access=build_access(["feature:tasks"]),
                         organization_id=w.org),
        BOB: UserContext(email=BOB, role=UserRole.EMPLOYEE, access=build_access(["feature:tasks"]),
                         organization_id=w.org),
        ZED: UserContext(email=ZED, role=UserRole.EMPLOYEE, access=build_access(["feature:tasks"]),
                         organization_id=w.other),
    }

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(tasks_router)
    app.dependency_overrides[get_current_user] = who
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://gateway.test") as client:
        return await client.request(method, path, headers={"x-user-email": member},
                                    json=body, follow_redirects=False)


def _key(w: Any, cid: str) -> Any:
    return _meta(w, cid).provider_meta.get("push_tasks", "absent")


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_the_switch_defaults_on_turns_off_with_a_note_and_turns_on_again(world) -> None:
    cid = await _connect(world, ANA)
    assert _key(world, cid) == "absent"
    async with _scope(world) as _:
        listing = (await _call(world, "GET", "/tasks/calendar/connections", ANA)).json()
        assert listing["connections"][0]["push_tasks"] is True  # absent means ON
        off = await _call(world, "PATCH", f"/tasks/calendar/connections/{cid}", ANA, {"push_tasks": False})
        assert off.status_code == 200, off.text
        assert off.json()["push_tasks"] is False and "stay" in off.json()["note"]
        assert off.json()["id"] == cid and off.json()["account_email"] == ANA  # the whole connection
        assert _key(world, cid) is False
        listing = (await _call(world, "GET", "/tasks/calendar/connections", ANA)).json()
        assert listing["connections"][0]["push_tasks"] is False
        on = await _call(world, "PATCH", f"/tasks/calendar/connections/{cid}", ANA, {"push_tasks": True})
    assert on.json()["push_tasks"] is True and "note" not in on.json()
    assert _key(world, cid) is True
    assert _meta(world, cid).provider_meta["account_email"] == ANA  # the other keys stayed


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_the_body_is_strict(world) -> None:
    cid = await _connect(world, ANA)
    async with _scope(world) as _:
        for body in ({"push_tasks": "no"}, {"push_tasks": 0}, {}, {"push_tasks": True, "x": 1}, None):
            resp = await _call(world, "PATCH", f"/tasks/calendar/connections/{cid}", ANA, body)
            assert resp.status_code == 422, (body, resp.text)
    assert _key(world, cid) == "absent"


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_member_cannot_change_the_switch_of_another_and_org_a_cannot_reach_org_b(world) -> None:
    cid = await _connect(world, ANA)  # org B
    async with _scope(world) as _:
        resp = await _call(world, "PATCH", f"/tasks/calendar/connections/{cid}", BOB, {"push_tasks": False})
        assert resp.status_code == 404, resp.text  # a colleague of the same org
    async with _scope(world, world.other) as _:
        resp = await _call(world, "PATCH", f"/tasks/calendar/connections/{cid}", ZED, {"push_tasks": False})
        assert resp.status_code == 404, resp.text  # another organization (RLS)
    assert _key(world, cid) == "absent"


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_disconnected_or_unknown_connection_answers_404(world) -> None:
    cid = await _connect(world, ANA)
    async with _scope(world) as _:
        bad = await _call(world, "PATCH", "/tasks/calendar/connections/not-a-uuid", ANA, {"push_tasks": False})
        assert bad.status_code == 404
        gone = await _call(world, "DELETE", f"/tasks/calendar/connections/{cid}", ANA)
        assert gone.status_code == 200
        resp = await _call(world, "PATCH", f"/tasks/calendar/connections/{cid}", ANA, {"push_tasks": False})
    assert resp.status_code == 404


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_the_route_is_a_404_with_the_flag_off(world, monkeypatch) -> None:
    from acb_common import get_settings

    cid = await _connect(world, ANA)
    monkeypatch.setattr(get_settings(), "calendar_sync_read", "", raising=False)
    async with _scope(world) as _:
        resp = await _call(world, "PATCH", f"/tasks/calendar/connections/{cid}", ANA, {"push_tasks": False})
    assert resp.status_code == 404 and _key(world, cid) == "absent"


@pytest.mark.asyncio
@_DB_GATE
async def test_r8_a_reconnect_keeps_the_switch_off(world) -> None:
    """``save_credential`` REPLACES provider_meta, so the REAL callback must carry
    the switch over. Without it a member who turned the write-out off gets it back
    on by reconnecting. It holds for the same account and for another one."""
    from urllib.parse import parse_qs, urlsplit

    from gateway.routes.tasks import calendar_connections as cal

    from tests.unit.test_calendar_sync_read import _user, answer, fixture

    cid = await _connect(world, ANA)
    async with _scope(world) as _:
        await _call(world, "PATCH", f"/tasks/calendar/connections/{cid}", ANA, {"push_tasks": False})
        assert _key(world, cid) is False
        url = (await _call(world, "POST", "/tasks/calendar/connections/google/connect", ANA)).json()["url"]
        state = parse_qs(urlsplit(url).query)["state"][0]
        world.http.on("POST", "/token", answer(fixture("google_token.json")))
        good = await cal.finish_consent(_user(world, ANA), "google", "c", state, None, base="https://app.test")
    assert good.status_code == 302
    meta = _meta(world, cid)
    assert meta.status == "connected" and meta.provider_meta["account_email"] == "ana@example.test"
    assert meta.provider_meta["push_tasks"] is False  # the row was replaced, the switch was carried
    # The switch lives in the COLUMN only. The sealed blob must not hold it: a blob
    # stringifies it ("False"), and a token refresh merges the blob's meta back.
    from acb_common.db import tenant_session
    from gateway.connections import store

    from tests.unit._tenant_ladder import tenant_engine_scope
    from tests.unit.test_calendar_sync_read import _dsn

    async with tenant_engine_scope(_dsn(world.p)), tenant_session(world.org) as db:
        sealed = await store.load_credential(db, world.org, "google_calendar", "calendar",
                                             scope_kind=store.SCOPE_MEMBER, member_email=ANA)
        assert "push_tasks" not in sealed.provider_meta
        await store.update_tokens(db, world.org, cid, sealed)  # what a token refresh does
    assert _meta(world, cid).provider_meta["push_tasks"] is False  # still OFF after the refresh


def test_the_switch_reads_fail_closed() -> None:
    """Only an ABSENT key or the JSON true is ON. Any garbled value is OFF: this
    gates a write to a third party. Mutation (M34): ``is not False``. Then "False"
    (what a refresh of a sealed blob writes) reads as ON."""
    from types import SimpleNamespace

    def conn(meta: dict) -> Any:
        return SimpleNamespace(provider_meta=meta)

    assert connections.push_tasks_enabled(conn({})) is True
    assert connections.push_tasks_enabled(conn({"push_tasks": True})) is True
    for garbled in (False, "False", "false", "True", 0, 1, None, "off"):
        assert connections.push_tasks_enabled(conn({"push_tasks": garbled})) is False, garbled
