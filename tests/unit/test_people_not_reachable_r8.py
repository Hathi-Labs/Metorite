"""Task 3636 — a known person who cannot open a task, on a real database (R8).

Code: ``people.core.not_reachable`` and ``GET /projects/people/names``
(``routes/projects/assignees.py``, the additive ``not_reachable`` field).
Hermetic twin: ``test_assignee_reach_rows.py``. Run on a PRIVATE ladder
database as ``test_member_action_r8.py`` says.

a. An active person with a login is not listed. A person whose ``app_user`` is
   suspended, and an alumnus, are ``inactive``. A person with no ``app_user``
   row, and an invited one, are ``no_login``. A stranger is absent.
b. A login in ANOTHER organization is no login here (the tenant scope of
   ``has_login``).
c. Org B reads nothing of org A's people.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.routes.people import core
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]


def _e(name: str) -> str:
    return f"{name}.{TAG}@reach.test"


ACTIVE, SUSPENDED, NOLOGIN, ELSEWHERE, STRANGER, ALUMNI, INVITED = (
    _e(n) for n in ("active", "suspended", "nologin", "elsewhere", "stranger", "alumni",
                    "invited"))
_LIKE = f"%.{TAG}@reach.test"


def _person(c: Any, org: str, email: str, status: str) -> None:
    done = c.execute(text(
        "UPDATE people SET status = :s, name = 'Test' WHERE organization_id = CAST(:o AS uuid) "
        "AND lower(email) = lower(:e)"), {"s": status, "o": org, "e": email}).rowcount
    if not done:
        c.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, source_key, "
            "organization_id, updated_by, updated_at) VALUES (gen_random_uuid(), 'Test', :e, "
            ":s, ARRAY[]::text[], 'manual', :k, CAST(:o AS uuid), 'test', now())"),
            {"e": email, "s": status, "k": f"manual:{email}:{org}", "o": org})


def _login(c: Any, org: str, email: str, status: str = "active") -> None:
    c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
        {"e": email, "s": status, "o": org})


@pytest.fixture
def world(promoted):  # noqa: F811
    a, b = promoted.org_a, promoted.org_b
    with promoted.admin_engine.begin() as c:
        _login(c, a, ACTIVE)
        _person(c, a, ACTIVE, "active")
        _login(c, a, SUSPENDED, "suspended")
        _person(c, a, SUSPENDED, "active")
        _login(c, a, ALUMNI)
        _person(c, a, ALUMNI, "alumni")
        _login(c, a, INVITED, "invited")
        _person(c, a, INVITED, "active")
        _person(c, a, NOLOGIN, "active")
        _login(c, b, ELSEWHERE)           # a login, but in ANOTHER org
        _person(c, a, ELSEWHERE, "active")
    yield SimpleNamespace(p=promoted)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM people WHERE lower(email) LIKE :t"), {"t": _LIKE})
        c.execute(text("DELETE FROM app_user WHERE lower(email) LIKE :t"), {"t": _LIKE})


def _reach(world: Any, org: str, emails: list[str]) -> dict[str, str]:
    async def go() -> dict[str, str]:
        dsn = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn), tenant_session(org) as db:
            return await core.not_reachable(db, emails)
    return asyncio.run(go())


def test_r8_each_reason_and_the_people_who_can_open_the_task(world) -> None:
    got = _reach(world, world.p.org_a,
                 [ACTIVE, SUSPENDED.upper(), NOLOGIN, ELSEWHERE, STRANGER, ALUMNI, INVITED])
    assert got == {SUSPENDED: "inactive", ALUMNI: "inactive", NOLOGIN: "no_login",
                   ELSEWHERE: "no_login", INVITED: "no_login"}


def test_r8_org_b_reads_nothing_of_org_a(world) -> None:
    assert _reach(world, world.p.org_b, [SUSPENDED, NOLOGIN, ELSEWHERE]) == {}


def test_r8_no_address_reads_nothing(world) -> None:
    assert _reach(world, world.p.org_a, []) == {}
