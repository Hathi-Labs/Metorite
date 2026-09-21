"""Tasks people directory — N4 scoping (colleague_onboarding.md §4 N4).

Owner decision, 2026-08-04: **directory open, HR fields restricted.** The org
chart stays readable by anyone holding ``feature:tasks``; the HR-sensitive half
(``people.HR_FIELDS``) is projected away for a caller without
``admin:members:read``; and all four write routes require
``admin:members:manage``.

Convention: the ``test_admin_groups.py`` / ``test_app_grants.py`` shape — no
TestClient, no live Postgres. The read routes are called as plain async
functions with the DB seam (``people._tenant_session``, the H2 tenant-bound
context manager) monkeypatched on the SUT submodule; the write gates are
exercised by executing the route's own permission dependency, so a route that
loses its gate fails here rather than in production.

What is locked:

1. A non-admin member gets the directory WITHOUT skills, skills_source,
   resume_summary, years_experience or capacity/load/available — and the
   response SHAPE is unchanged, so no client breaks.
2. An admin (and the owner, who holds ``*``) gets all of it.
3. All FOUR write routes refuse a plain member — ``POST /people``,
   ``PATCH /people/{id}``, ``POST /people/{id}/resume`` (people.py) and
   ``POST /people/embed`` (capability.py). Count routes, not call sites.
4. An admin is allowed those writes.
5. ``fetch_people_for_clarify`` is untouched and still returns full data — it
   is the in-process delegation path, and narrowing it would silently degrade
   agent delegation rather than protect anyone.
"""
from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway.routes.tasks import capability as tasks_capability  # noqa: F401
from gateway.routes.tasks import people
from gateway.routes.tasks.core import (
    PEOPLE_HR_READ_PERMISSION,
    PEOPLE_WRITE_PERMISSION,
    router,
)
from gateway.routes.tasks.people import (
    HR_FIELDS,
    OrgPersonModel,
    fetch_people_for_clarify,
    list_people,
)

# ── Principals ──────────────────────────────────────────────────────────────

#: The default role for a new employee (130_org_access_control.sql:228-239 +
#: 131:70-78). Holds `feature:tasks` — which is the whole point of N4.
MEMBER = UserContext(
    email="colleague@fracktal.in", role=UserRole.EMPLOYEE,
    access=build_access(
        ["feature:chat", "feature:tasks", "feature:notes", "agents:run:*",
         "apps:use:*", "integrations:use:*", "memory:read_org"],
        roles=["member"],
    ),
)

#: Reads the member directory, cannot manage it (130:210-223) — the pair that
#: proves the read floor and the write floor are two different permissions.
MANAGER = UserContext(
    email="manager@fracktal.in", role=UserRole.EMPLOYEE,
    access=build_access(
        ["feature:tasks", "admin:members:read", "data:org:read"],
        roles=["manager"],
    ),
)

ADMIN = UserContext(
    email="admin@fracktal.in", role=UserRole.EXECUTIVE,
    access=build_access(
        ["feature:*", "admin:members:read", "admin:members:manage"],
        roles=["admin"],
    ),
)

#: The owner holds `*` and must lose nothing — verified, not assumed.
OWNER = UserContext(
    email="vjvarada@fracktal.in", role=UserRole.EXECUTIVE,
    access=build_access(["*"], roles=["owner"]),
)

ANONYMOUS = UserContext(email=None, role=UserRole.EMPLOYEE,
                        access=build_access([]))


# ── Fakes ───────────────────────────────────────────────────────────────────

def _person_row(**over: Any) -> SimpleNamespace:
    """One `people` row, HR half populated so a leak is visible."""
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Asha Rao",
        "email": "asha@fracktal.in",
        "role": "Firmware Engineer",
        "title": "Senior Firmware Engineer",
        "department": "Engineering",
        "team": "Controls",
        "reports_to": "Vijay Varada",
        "manager_id": "22222222-2222-2222-2222-222222222222",
        "status": "active",
        "skills": ["firmware", "C++", "RTOS"],
        "skills_source": {"firmware": "resume", "C++": "orgchart"},
        "domain": "robotics",
        "resume_summary": "Nine years building motion-control firmware.",
        "years_experience": 9,
        "capacity_hours_per_week": 40,
        "current_load_hours_per_week": 31,
        "available_hours_per_week": 9,
        "clickup_user_id": "8804321",
    }
    row.update(over)
    return SimpleNamespace(**row)


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Records every statement so the WHERE clause itself can be asserted."""

    def __init__(self, rows: list[Any]):
        self.rows = rows
        self.statements: list[str] = []
        self.committed = 0
        self.closed = False

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(str(statement))
        return _Result(self.rows)

    async def commit(self) -> None:
        self.committed += 1

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB([_person_row()])

    # H2: the seam is the tenant-bound context manager, patched per module —
    # commit-on-clean-exit mirrors the real wrapper (see _projects_fakes).
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db
        await db.commit()

    monkeypatch.setattr(people, "_tenant_session", _tenant_session)
    return db


# ── 1 + 2. The read projection ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_member_does_not_receive_the_hr_half(fake_db: _FakeDB) -> None:
    """The exposure N4 names: HR data to the default role."""
    [person] = await list_people(user=MEMBER)

    assert person.skills == []
    assert person.skills_source == {}
    assert person.resume_summary is None
    assert person.years_experience is None
    assert person.capacity_hours_per_week is None
    assert person.current_load_hours_per_week is None
    assert person.available_hours_per_week is None
    # Nothing of the résumé survives anywhere in the payload.
    assert "motion-control" not in person.model_dump_json()


@pytest.mark.asyncio
async def test_a_member_still_sees_the_basic_directory(fake_db: _FakeDB) -> None:
    """Open directory, restricted fields — not a hidden directory."""
    [person] = await list_people(user=MEMBER)

    assert person.name == "Asha Rao"
    assert person.email == "asha@fracktal.in"
    assert person.role == "Firmware Engineer"
    assert person.title == "Senior Firmware Engineer"
    assert person.department == "Engineering"
    assert person.team == "Controls"
    assert person.reports_to == "Vijay Varada"
    assert person.manager_id == "22222222-2222-2222-2222-222222222222"
    assert person.provider_user_id == "8804321"
    assert person.status == "active"


@pytest.mark.parametrize("who", [ADMIN, OWNER, MANAGER],
                         ids=["admin", "owner", "manager"])
@pytest.mark.asyncio
async def test_an_admin_receives_the_hr_half(
    fake_db: _FakeDB, who: UserContext
) -> None:
    """`admin:members:read` is the read floor; the owner's `*` matches it."""
    assert who.has_permission(PEOPLE_HR_READ_PERMISSION)
    [person] = await list_people(user=who)

    assert person.skills == ["firmware", "C++", "RTOS"]
    assert person.skills_source == {"firmware": "resume", "C++": "orgchart"}
    assert person.resume_summary == "Nine years building motion-control firmware."
    assert person.years_experience == 9
    assert person.capacity_hours_per_week == 40
    assert person.current_load_hours_per_week == 31
    assert person.available_hours_per_week == 9


@pytest.mark.asyncio
async def test_the_response_shape_is_identical_for_both(fake_db: _FakeDB) -> None:
    """Fields are nulled, never removed — a changed shape breaks the client."""
    [restricted] = await list_people(user=MEMBER)
    [full] = await list_people(user=ADMIN)

    assert restricted.model_dump().keys() == full.model_dump().keys()
    assert set(HR_FIELDS) <= set(OrgPersonModel.model_fields)


@pytest.mark.asyncio
async def test_a_members_search_cannot_probe_the_skills_column(
    fake_db: _FakeDB,
) -> None:
    """Matching on a stripped column would make the search box an oracle."""
    await list_people(q="firmware", user=MEMBER)
    [sql] = fake_db.statements

    assert "unnest(skills)" not in sql
    assert "name ILIKE :q" in sql


@pytest.mark.asyncio
async def test_an_admins_search_still_matches_skills(fake_db: _FakeDB) -> None:
    await list_people(q="firmware", user=ADMIN)
    [sql] = fake_db.statements

    assert "unnest(skills)" in sql


# ── 3 + 4. The write gates — all four routes ────────────────────────────────

#: (method, route template). The undercount of request paths is what produced
#: the P0 in PR #346, so this list is the routes, enumerated.
WRITE_ROUTES: list[tuple[str, str]] = [
    ("POST", "/tasks/people"),
    ("PATCH", "/tasks/people/{person_id}"),
    ("POST", "/tasks/people/{person_id}/resume"),
    ("POST", "/tasks/people/embed"),
]


def _permission_gates(method: str, path: str) -> list[Any]:
    """The route's permission dependencies.

    Selected by signature: ``require_permission``'s checker takes exactly
    ``user``; the router-level ``require_feature_router`` checker also takes a
    ``Request``, so it is skipped rather than special-cased by name.
    """
    for route in router.routes:
        if getattr(route, "path", None) != path:
            continue
        if method not in getattr(route, "methods", set()):
            continue
        gates = []
        for dep in getattr(route, "dependencies", []):
            fn = getattr(dep, "dependency", None)
            if fn is None:
                continue
            try:
                params = set(inspect.signature(fn).parameters)
            except (TypeError, ValueError):
                continue
            if params == {"user"}:
                gates.append(fn)
        return gates
    raise AssertionError(f"no route {method} {path} on the /tasks router")


@pytest.mark.parametrize("method,path", WRITE_ROUTES)
def test_every_write_route_carries_the_admin_gate(method: str, path: str) -> None:
    """Wiring: a route that loses its dependency fails here, not in prod."""
    assert _permission_gates(method, path), (
        f"{method} {path} has no permission gate — it is reachable by any "
        f"holder of feature:tasks"
    )


@pytest.mark.parametrize("method,path", WRITE_ROUTES)
@pytest.mark.parametrize("who", [MEMBER, MANAGER], ids=["member", "manager"])
@pytest.mark.asyncio
async def test_a_non_admin_is_refused_every_write(
    method: str, path: str, who: UserContext
) -> None:
    """`member` is the default role; `manager` may READ the directory and
    still must not edit HR records."""
    gates = _permission_gates(method, path)
    assert gates
    with pytest.raises(HTTPException) as exc:
        for gate in gates:
            await gate(user=who)
    assert exc.value.status_code == 403
    assert PEOPLE_WRITE_PERMISSION in str(exc.value.detail)


@pytest.mark.parametrize("method,path", WRITE_ROUTES)
@pytest.mark.parametrize("who", [ADMIN, OWNER], ids=["admin", "owner"])
@pytest.mark.asyncio
async def test_an_admin_is_allowed_every_write(
    method: str, path: str, who: UserContext
) -> None:
    """The owner holds `*` and must not lose the HR write surface."""
    for gate in _permission_gates(method, path):
        assert await gate(user=who) is who


@pytest.mark.parametrize("method,path", WRITE_ROUTES)
@pytest.mark.asyncio
async def test_an_anonymous_caller_gets_401_not_403(
    method: str, path: str
) -> None:
    """"Not signed in" and "not allowed" route differently in the UI."""
    with pytest.raises(HTTPException) as exc:
        for gate in _permission_gates(method, path):
            await gate(user=ANONYMOUS)
    assert exc.value.status_code == 401


def test_the_write_permission_is_an_existing_capability() -> None:
    """A new slug would be nobody's grant — including the owner's admins'."""
    from acb_auth.permissions import CAPABILITIES

    assert PEOPLE_WRITE_PERMISSION in CAPABILITIES
    assert PEOPLE_HR_READ_PERMISSION in CAPABILITIES


# ── 5. The delegation path must be untouched ────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_people_for_clarify_still_returns_full_data() -> None:
    """The in-process delegation feed: no user, no projection, full rows.

    If a future change applies the projection at the query layer instead of
    the serialization layer, this is what catches it.
    """
    row = _person_row()
    row.manager_name = "Vijay Varada"
    db = _FakeDB([row])

    [entry] = await fetch_people_for_clarify(db)

    assert entry["skills"] == ["firmware", "C++", "RTOS"]
    assert entry["years_experience"] == 9
    assert entry["available_hours_per_week"] == 9
    assert entry["capacity_hours_per_week"] == 40
    assert entry["current_load_hours_per_week"] == 31
    assert entry["reports_to"] == "Vijay Varada"
    assert entry["provider_user_id"] == "8804321"


def test_fetch_people_for_clarify_takes_no_user() -> None:
    """Its signature IS the contract: it is never reached through the router."""
    assert list(inspect.signature(fetch_people_for_clarify).parameters) == ["db"]
