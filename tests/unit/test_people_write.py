"""WS-28b-write — the person write half, restored and made survivable.

Spec: `project-docs/specs/people_center_app.md` §3.2, §7 (WS-28b-write).

The write routes themselves are old; what is new is that migration 148 changed
the table under them and nobody told them. Three of the claims below are about
that mismatch, and each one is a save button that would otherwise fail at the
database rather than at the route:

* the status vocabulary is ONE tuple, mirrored from 148's CHECK, shared by the
  filter, the facets list, the editor's select and the write validation;
* duplicate NAMES are allowed — 148 dropped `UNIQUE(name)` precisely because
  two real people share a name, and a route-level 409 would have kept the
  behaviour the migration was written to remove;
* duplicate EMAILS are refused with a sentence, because 148's partial unique
  index turns them into an opaque 500 mid-typing.

The fourth is `can_manage`: §3.2 renders write controls absent rather than
disabled, which is only possible if the READ tells the UI whether the caller
may write.

Hermetic: the DB seam is monkeypatched, no Postgres, no TestClient. The
statements the routes issue are inspected directly, because "which query ran"
is the claim — a fake that answered them would prove things about the fake.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway.routes.people import core as people_core
from gateway.routes.people import directory as people_directory
from gateway.routes.tasks import people as tasks_people
from gateway.routes.tasks.core import (
    PEOPLE_STATUSES,
    PEOPLE_WRITE_PERMISSION,
    can_manage_people,
)
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "infra" / "postgres" / "148_people_key_shape.sql"


def run(coro):
    return asyncio.run(coro)


# ── Doubles ─────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


PERSON = SimpleNamespace(
    id="11111111-1111-1111-1111-111111111111",
    name="Priya", email="priya@fracktal.in", role="Engineer", title=None,
    department="Engineering", team="Firmware", reports_to=None, manager_id=None,
    status="active", skills=["firmware"], skills_source={"firmware": "resume"},
    domain=None, resume_summary=None, years_experience=None,
    capacity_hours_per_week=40, current_load_hours_per_week=10,
    available_hours_per_week=30, clickup_user_id=None, email_conflict=None,
)


class FakeDB:
    """Answers by statement fingerprint and records everything it was asked.

    ``email_holder`` is what the duplicate-address lookup finds; ``None`` means
    the address is free.
    """

    def __init__(self, email_holder: str | None = None):
        self.email_holder = email_holder
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        self.params.append(dict(params or {}))
        if hits(statement, "SELECT name FROM people WHERE lower(email)"):
            holder = self.email_holder
            return _Result([SimpleNamespace(name=holder)] if holder else [])
        if hits(statement, "SELECT * FROM people"):
            return _Result([PERSON])
        return _Result([])

    async def commit(self) -> None:
        return None

    async def close(self) -> None:
        return None

    # ── Assertions the tests read ───────────────────────────────────────────

    def issued(self, fragment: str) -> bool:
        return any(fragment in s for s in self.statements)

    def params_for(self, fragment: str) -> dict:
        for statement, params in zip(self.statements, self.params, strict=True):
            if fragment in statement:
                return params
        raise AssertionError(f"no statement contained {fragment!r}")


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


def bind(monkeypatch, database: FakeDB) -> None:
    # H2: routes/tasks/people acquires sessions through the tenant-bound
    # `_tenant_session` context manager (imported from core by name), so that
    # is the seam this fake swaps in — commit-on-clean-exit like the real one.
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield database
        await database.commit()

    monkeypatch.setattr(
        tasks_people, "_tenant_session", _tenant_session, raising=False)


def _user(email: str, *grants: str) -> UserContext:
    """Built with the REAL `build_access`, so the permission the route enforces
    is the one this principal actually resolves."""
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(grants)),
    )


ADMIN = _user("admin@fracktal.in", "feature:people", PEOPLE_WRITE_PERMISSION)
READER = _user("member@fracktal.in", "feature:people", "admin:members:read")


def write(**fields) -> tasks_people.PersonWrite:
    return tasks_people.PersonWrite(**fields)


# ── The vocabulary is one definition, and it matches the database ───────────

def test_the_people_center_and_the_write_routes_share_one_status_tuple():
    """Not "equal" — the SAME object. Two tuples that happen to agree today are
    two tuples, and the next status gets added to one of them."""
    assert people_core.STATUSES is PEOPLE_STATUSES


def test_the_vocabulary_matches_migration_148s_check():
    """Read from the SQL rather than restated here.

    Comments are stripped first: this file's own prose quotes the old
    `'active' | 'inactive'` vocabulary while explaining why it changed, and a
    test that searched the raw text would pass on the explanation of the bug
    instead of on the fix.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    match = re.search(r"CHECK \(status IN \(([^)]*)\)\)", sql)
    assert match, "148 no longer constrains status — this test is now fiction"
    in_sql = tuple(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert set(in_sql) == set(PEOPLE_STATUSES)


@pytest.mark.parametrize("status", PEOPLE_STATUSES)
def test_every_status_in_the_vocabulary_is_accepted(status):
    tasks_people._validate_status(status)  # does not raise


def test_an_unset_status_is_not_a_bad_status():
    """A PATCH that never mentions status must not be refused for it."""
    tasks_people._validate_status(None)


def test_a_status_outside_the_vocabulary_is_refused_before_the_database():
    """`on_leave` is the one that matters: it is what the editor offered before
    148 landed, so it is the value a copy-paste restore would have shipped."""
    with pytest.raises(HTTPException) as exc:
        tasks_people._validate_status("on_leave")
    assert exc.value.status_code == 400
    # The message has to name the alternatives — "invalid status" is a dead end.
    for allowed in PEOPLE_STATUSES:
        assert allowed in str(exc.value.detail)


def test_create_refuses_a_bad_status_without_touching_the_database(monkeypatch, db):
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(tasks_people.create_person(write(name="Ravi", status="on_leave"), ADMIN))
    assert exc.value.status_code == 400
    assert db.statements == []


def test_patch_refuses_a_bad_status(monkeypatch, db):
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(tasks_people.update_person(PERSON.id, write(status="left"), ADMIN))
    assert exc.value.status_code == 400


# ── Names may repeat; the address may not ───────────────────────────────────

def test_two_people_may_share_a_name(monkeypatch, db):
    """The regression 148 exists to fix. Asserted as "no name lookup is issued
    at all", not as "the call succeeds": a create that happened to succeed
    because the fake found no duplicate would pass with the old 409 still in
    place, and prove nothing."""
    bind(monkeypatch, db)
    run(tasks_people.create_person(write(name="Priya"), ADMIN))
    assert not db.issued("LOWER(name)")
    assert not db.issued("lower(name)")
    assert db.issued("INSERT INTO people")


def test_a_duplicate_address_is_refused_and_names_the_other_row(monkeypatch):
    database = FakeDB(email_holder="Priya")
    bind(monkeypatch, database)
    with pytest.raises(HTTPException) as exc:
        run(tasks_people.create_person(
            write(name="Priya S", email="priya@fracktal.in"), ADMIN))
    assert exc.value.status_code == 409
    # Naming the holder is the difference between a message an admin can act on
    # and one that sends them looking through the directory by hand.
    assert "Priya" in str(exc.value.detail)
    assert not database.issued("INSERT INTO people")


def test_the_address_check_is_case_insensitive_on_both_sides(monkeypatch):
    """R10. The index is on `lower(email)`, so a check that compared raw text
    would pass the route and fail the insert."""
    database = FakeDB(email_holder="Priya")
    bind(monkeypatch, database)
    with pytest.raises(HTTPException):
        run(tasks_people.create_person(
            write(name="Ravi", email="  PRIYA@Fracktal.IN "), ADMIN))
    assert database.params_for("lower(email)")["email"] == "priya@fracktal.in"


def test_a_blank_address_is_stored_as_null(monkeypatch, db):
    """`''` is not NULL, so two blank addresses would collide under 148's
    partial unique index — the exact mess the migration had to clean up."""
    bind(monkeypatch, db)
    run(tasks_people.create_person(write(name="Ravi", email="   "), ADMIN))
    assert db.params_for("INSERT INTO people")["email"] is None
    # And a blank is not looked up either: every empty address would "match".
    assert not db.issued("lower(email)")


def test_patch_lets_a_person_keep_their_own_address(monkeypatch):
    """Without `exclude_id` every re-save would report the person as their own
    duplicate, which makes editing anyone's title impossible."""
    database = FakeDB(email_holder=None)
    bind(monkeypatch, database)
    run(tasks_people.update_person(
        PERSON.id, write(email="priya@fracktal.in"), ADMIN))
    params = database.params_for("lower(email)")
    assert params["exclude"] == PERSON.id
    assert database.issued("UPDATE people")


def test_patch_refuses_somebody_elses_address(monkeypatch):
    database = FakeDB(email_holder="Ravi")
    bind(monkeypatch, database)
    with pytest.raises(HTTPException) as exc:
        run(tasks_people.update_person(
            PERSON.id, write(email="ravi@fracktal.in"), ADMIN))
    assert exc.value.status_code == 409
    assert "Ravi" in str(exc.value.detail)
    assert not database.issued("UPDATE people")


def test_patch_that_never_mentions_email_does_not_check_it(monkeypatch):
    """`exclude_unset` is the contract: a title-only edit must not run the
    address lookup, and must not write the column."""
    database = FakeDB(email_holder="Someone Else")
    bind(monkeypatch, database)
    run(tasks_people.update_person(PERSON.id, write(title="Lead"), ADMIN))
    assert not database.issued("lower(email)")
    assert not database.issued("email = :email")
    assert "email" not in database.params_for("UPDATE people")


# ── `can_manage` — the flag that lets the UI hide rather than disable ───────

def test_can_manage_people_reads_the_write_permission():
    assert can_manage_people(ADMIN) is True
    assert can_manage_people(READER) is False


def test_can_manage_people_fails_closed_for_an_unresolved_principal():
    """A UI that cannot tell must draw no control, never an optimistic one."""
    assert can_manage_people(None) is False
    assert can_manage_people(SimpleNamespace()) is False


@pytest.mark.parametrize(
    "who",
    [
        ADMIN,
        READER,
        _user("owner@fracktal.in", "*"),
        _user("nobody@fracktal.in"),
        _user("wildcard@fracktal.in", "admin:members:*"),
    ],
    ids=["admin", "reader", "owner", "nobody", "wildcard"],
)
def test_the_gate_and_the_question_agree_for_every_principal(who):
    """`require_people_write()` enforces and `can_manage_people` reports. If
    they disagreed for any principal the UI would offer a control the route
    refuses — the precise failure the flag exists to prevent.

    Compared by RUNNING the route's real dependency rather than by reading
    either one's source: the wildcard cases (`*`, `admin:members:*`) go through
    `permission_matches`, and a hand-written comparison would have to
    re-implement it to check them — which is how two answers start agreeing
    only about themselves.
    """
    gate = tasks_people.require_people_write().dependency
    try:
        run(gate(user=who))
        allowed = True
    except HTTPException:
        allowed = False
    assert allowed is can_manage_people(who)


def test_the_directory_tells_the_caller_whether_they_may_write(monkeypatch):
    """Read as a whole response, because the flag is only useful if it ships."""
    database = FakeDB()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        yield database
        await database.commit()

    for module in (people_core, people_directory):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)

    for user, expected in ((ADMIN, True), (READER, False)):
        res = run(people_directory.list_directory(user=user))
        assert res.can_manage is expected


def test_the_person_page_carries_can_manage_independently_of_hr_visible(monkeypatch):
    """The two permissions are separate grants. An admin who may edit but not
    read the HR half is a real principal, and the panel has to open with the
    skills strip restricted and the Edit button present."""
    database = FakeDB()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        yield database
        await database.commit()

    for module in (people_core, people_directory):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)

    editor_only = _user("ops@fracktal.in", "feature:people", PEOPLE_WRITE_PERMISSION)
    person = run(people_directory.get_person(PERSON.id, user=editor_only))
    assert person["can_manage"] is True
    assert person["hr_visible"] is False
