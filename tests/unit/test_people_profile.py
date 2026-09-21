"""WS-28g — the person profile and self-service editing.

Spec: `project-docs/specs/people_center_app.md` §3, §4, §5.3 · D-PC-1…D-PC-5.

What this file locks, in the order the spec argues it:

* **The partition is total.** Every column of `people` — discovered from the
  migrations, not from a list somebody maintains — is in exactly one write
  class. A column added next month fails here rather than defaulting into the
  permissive answer (R7).
* **The payload and the class map are the same set**, in both directions. A
  field the payload accepts and the map has never heard of has no gate; a field
  in the map the payload cannot carry is a permission for something nobody can
  do.
* **`email` is admin-only** — the one field whose self-writability would be
  privilege escalation, because the self predicate IS the address (D-PC-2).
* **Self is a second READ door on one row and never on a list.** A self door
  cannot widen a cross-row query, or the search box becomes the oracle the
  projection exists to prevent.
* **Private is keyed to the write grant, not the read grant** (D-PC-3): a
  manager holding `admin:members:read` sees skills and does not see a phone
  number.
* **A refused field is named** (D-PC-5). A save that succeeds and discards half
  the form is worse than a refusal.
* **`/people/me` is registered before `/people/{person_id}`**, because FastAPI
  matches in registration order and the failure mode is a 500 on a route that
  looks fine.

Hermetic: the DB seam is monkeypatched, no Postgres, no TestClient. The SQL the
write builder emits is inspected directly — "which bind, in which shape" is the
claim, and a fake that answered it would prove things about the fake. The
database's own half (the columns exist, the casts are legal, the partial unique
index still holds) is `tests/live/live_ws28g.py`, per R8.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway.routes.people import core as people_core
from gateway.routes.people import fields as people_fields
from gateway.routes.people import profile as people_profile
from gateway.routes.people import router as people_router
from gateway.routes.people import self_router as people_self_router
from gateway.routes.people import selfservice as people_self
from gateway.routes.tasks import people as tasks_people
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "infra" / "postgres"
PROFILE_MIGRATION = MIGRATIONS / "172_people_profile.sql"

PEOPLE_READ = "admin:members:read"
PEOPLE_WRITE = "admin:members:manage"


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


def person_row(**overrides: Any) -> SimpleNamespace:
    """A row shaped like migration 172 leaves it. Every profile column present,
    because the mapper's job is to carry them and a double missing half of them
    would let a dropped field pass."""
    row = dict(
        id="11111111-1111-1111-1111-111111111111",
        name="Priya", email="priya@fracktal.in", role="Engineer",
        title="Firmware lead", department="Engineering", team="Firmware",
        reports_to=None, manager_id=None, status="active",
        skills=["firmware"], skills_source={"firmware": "resume"},
        domain="hardware", resume_summary="ten years of embedded work",
        years_experience=10, capacity_hours_per_week=40,
        current_load_hours_per_week=10, available_hours_per_week=30,
        clickup_user_id=None, email_conflict=None,
        preferred_name="Pri", pronouns="she/her", location="Bengaluru",
        timezone="Asia/Kolkata",
        working_hours={"days": [1, 2, 3, 4, 5], "start": "09:00", "end": "17:00"},
        bio="Firmware, mostly.", links={"github": "priya"},
        languages=["English", "Kannada"], interests=["mechanical design"],
        employee_id="FT-0007", employment_type="employee",
        start_date=date(2021, 6, 1), end_date=None, seniority="lead",
        cost_center="R&D", max_concurrent_tasks=4,
        phone="+91 99999 99999",
        emergency_contact={"name": "R", "relation": "spouse", "phone": "+91 1"},
        personal_email="priya@example.com", birthday="03-14",
    )
    row.update(overrides)
    return SimpleNamespace(**row)


PERSON = person_row()


class FakeDB:
    def __init__(self, row: Any | None = PERSON):
        self.row = row
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        self.params.append(dict(params or {}))
        if statement.startswith("SELECT 1 FROM app_user"):
            return _Result([SimpleNamespace(**{"?column?": 1})])
        if hits(statement, "SELECT name FROM people"):
            return _Result([])
        if hits(statement, "SELECT email FROM people") \
                or hits(statement, "SELECT * FROM people"):
            # The self lookup filters on the address, and the fake has to as
            # well: a double that answers "here is Priya" to
            # `WHERE lower(email) = 'someone@else'` would make the self
            # predicate look like it works when the query does the deciding.
            wanted = (params or {}).get("email")
            if wanted is not None and self.row is not None:
                mine = (getattr(self.row, "email", None) or "").lower()
                return _Result([self.row] if mine == wanted else [])
            return _Result([self.row] if self.row is not None else [])
        return _Result([])

    async def commit(self) -> None:
        return None

    def issued(self, fragment: str) -> bool:
        return any(fragment in s for s in self.statements)


def bind(monkeypatch, database: FakeDB, *modules) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield database
        await database.commit()

    for module in modules or (people_core, people_profile, people_self,
                              tasks_people):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)


def _user(email: str | None, *grants: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = _user("admin@fracktal.in", "feature:people", PEOPLE_WRITE)
MANAGER = _user("lead@fracktal.in", "feature:people", PEOPLE_READ)
STRANGER = _user("someone@fracktal.in", "feature:people")
SUBJECT = _user("priya@fracktal.in", "feature:people")


# ══════════════════════════════════════════════════════════════════════════
# 1. The field map is total, and it is the payload
# ══════════════════════════════════════════════════════════════════════════

_ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+people\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([a-z_][a-z0-9_]*)", re.IGNORECASE)


def discovered_columns() -> set[str]:
    """Every column of ``people``, read out of the migrations.

    Discovered rather than listed: a list in a test is a second place to
    remember, and the whole point of this fence is that nobody has to remember.
    Reads 49's CREATE TABLE body plus every later ADD COLUMN across the ladder.
    """
    create = (MIGRATIONS / "49_gtd_people.sql").read_text(encoding="utf-8")
    # The STATEMENT, at the start of a line — not the first "CREATE TABLE" in
    # the file. 49 opens with the rename prologue (the `gtd_` retirement,
    # 2026-09-21) whose comment explains why a late rename migration fails, and
    # says "CREATE TABLE IF NOT EXISTS" while doing so. Anchoring on the loose
    # fragment read that comment as the table body and reported `BEGIN`,
    # `DECLARE` and `EXECUTE` as unclassified columns.
    start = re.search(r"^CREATE TABLE IF NOT EXISTS people(?!\w)", create, re.M)
    assert start, "49 no longer creates `people` under a name this test knows"
    body = create[start.start():]
    body = body[body.index("(") + 1: body.index("\n);")]
    columns: set[str] = set()
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("--") or line.upper().startswith("UNIQUE"):
            continue
        columns.add(line.split()[0])
    for path in MIGRATIONS.glob("*.sql"):
        columns.update(m.group(1).lower()
                       for m in _ADD_COLUMN.finditer(path.read_text(encoding="utf-8")))
    return columns


def test_the_migrations_are_discoverable_at_all() -> None:
    """A discovery that finds nothing makes every assertion below pass
    vacuously — the failure mode `test_tenant_coverage.py` guards the same way."""
    columns = discovered_columns()
    assert len(columns) > 40, columns
    assert {"name", "email", "timezone", "birthday"} <= columns


def test_every_column_is_in_exactly_one_write_class() -> None:
    """**The fence (R7).** A column added to `people` next month fails here
    until somebody decides who may write it. Without this, the honest-looking
    default is 'nobody classified it, so the admin path writes it and the self
    path does not' — which is a decision made by omission."""
    classified = (people_fields.ADMIN_FIELDS
                  | people_fields.SELF_FIELDS
                  | people_fields.DERIVED_FIELDS)
    missing = discovered_columns() - classified
    assert not missing, (
        f"unclassified people columns: {sorted(missing)}. Put each in "
        "ADMIN_FIELDS, SELF_FIELDS or DERIVED_FIELDS in routes/people/fields.py "
        "— see people_center_app.md §4.3."
    )
    stale = classified - discovered_columns()
    assert not stale, f"classified columns that no migration creates: {sorted(stale)}"


def test_the_write_classes_do_not_overlap() -> None:
    assert not (people_fields.ADMIN_FIELDS & people_fields.SELF_FIELDS)
    assert not (people_fields.ADMIN_FIELDS & people_fields.DERIVED_FIELDS)
    assert not (people_fields.SELF_FIELDS & people_fields.DERIVED_FIELDS)


def test_the_payload_model_is_exactly_the_writable_set() -> None:
    """Both directions. A field the payload accepts that the map never heard of
    has no gate; a field in the map the payload cannot carry is a permission
    granted for something nobody can do.

    **Less the upload-only ones** (WS-28q): `avatar` is in the self class — the
    authorization question is identical to a timezone's — but it arrives as a
    FILE through its own endpoint, so demanding the JSON payload carry a data
    URI would be the wrong shape for the sake of a tidier assertion. The
    subtraction is explicit rather than a containment check, so a field that
    silently stops being carriable still fails here.
    """
    assert set(tasks_people.PersonWrite.model_fields) == (
        set(people_fields.WRITABLE_FIELDS)
        - set(people_fields.UPLOAD_ONLY_FIELDS))


def test_an_upload_only_field_is_still_in_a_write_class() -> None:
    """Otherwise "upload-only" would be a way to leave a field ungated: the
    transport changed, the authorization question did not."""
    for name in people_fields.UPLOAD_ONLY_FIELDS:
        assert name in people_fields.WRITABLE_FIELDS


def test_email_is_admin_only_because_the_self_predicate_is_the_email() -> None:
    """D-PC-2. Self-editable identity is privilege escalation with extra steps:
    point your row at a colleague's address, and the next request makes you
    them."""
    assert "email" in people_fields.ADMIN_FIELDS
    assert "email" not in people_fields.SELF_FIELDS
    assert "email" not in people_fields.editable_fields(
        is_admin=False, is_self=True)


@pytest.mark.parametrize("field", [
    "manager_id", "status", "title", "department", "capacity_hours_per_week",
    "employment_type", "seniority", "start_date",
])
def test_the_organisations_claims_are_not_self_writable(field: str) -> None:
    """A product where you can promote yourself is not an org chart (§4.3)."""
    assert field not in people_fields.editable_fields(
        is_admin=False, is_self=True)


@pytest.mark.parametrize("field", [
    "timezone", "working_hours", "phone", "skills", "bio", "birthday",
    "languages", "interests", "max_concurrent_tasks", "emergency_contact",
])
def test_the_persons_own_claims_are_self_writable(field: str) -> None:
    assert field in people_fields.editable_fields(is_admin=False, is_self=True)


def test_admin_is_a_superset_not_a_disjoint_set() -> None:
    """"An admin cannot fix a typo in somebody's timezone" is a support ticket,
    not a policy."""
    admin_only = set(people_fields.editable_fields(is_admin=True, is_self=False))
    assert set(people_fields.SELF_FIELDS) <= admin_only


def test_a_caller_who_is_neither_may_write_nothing() -> None:
    assert people_fields.editable_fields(is_admin=False, is_self=False) == []


def test_editable_fields_is_sorted_so_the_response_is_stable() -> None:
    got = people_fields.editable_fields(is_admin=True, is_self=True)
    assert got == sorted(got)


# ══════════════════════════════════════════════════════════════════════════
# 2. The refusal names the field (D-PC-5)
# ══════════════════════════════════════════════════════════════════════════

def test_a_self_editor_writing_an_admin_field_is_refused_by_name() -> None:
    with pytest.raises(HTTPException) as exc:
        people_fields.authorize_write(["timezone", "manager_id"],
                                      is_admin=False, is_self=True)
    assert exc.value.status_code == 403
    assert "manager_id" in exc.value.detail
    # The field they COULD write is not part of the complaint.
    assert "timezone" not in exc.value.detail


def test_a_stranger_gets_no_field_list_at_all() -> None:
    """Listing what they would be able to write is a permission map for
    somebody with no permissions."""
    with pytest.raises(HTTPException) as exc:
        people_fields.authorize_write(["timezone"], is_admin=False, is_self=False)
    assert exc.value.status_code == 403
    assert "timezone" not in exc.value.detail


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    with pytest.raises(HTTPException) as exc:
        people_fields.authorize_write(["salary"], is_admin=True, is_self=False)
    assert "salary" in exc.value.detail


def test_an_admin_writing_anything_writable_is_allowed() -> None:
    people_fields.authorize_write(sorted(people_fields.WRITABLE_FIELDS),
                                  is_admin=True, is_self=False)


def test_a_self_editor_writing_only_self_fields_is_allowed() -> None:
    people_fields.authorize_write(sorted(people_fields.SELF_FIELDS),
                                  is_admin=False, is_self=True)


# ══════════════════════════════════════════════════════════════════════════
# 3. The self predicate (D-PC-1, D-PC-12)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("caller,row_email,expected", [
    ("priya@fracktal.in", "priya@fracktal.in", True),
    ("PRIYA@Fracktal.IN", "priya@fracktal.in", True),   # R10, both sides
    (" priya@fracktal.in ", "priya@fracktal.in", True),
    ("priya@fracktal.in", "ravi@fracktal.in", False),
    ("priya@fracktal.in", None, False),                 # directory-only person
    (None, "priya@fracktal.in", False),                 # no caller identity
    (None, None, False),
    ("", "", False),                                    # two blanks are not a match
])
def test_the_self_predicate(caller, row_email, expected) -> None:
    assert people_core.is_self(_user(caller), row_email) is expected


def test_the_self_predicate_fails_closed_for_an_unresolved_principal() -> None:
    assert people_core.is_self(object(), "priya@fracktal.in") is False


# ══════════════════════════════════════════════════════════════════════════
# 4. The read tiers
# ══════════════════════════════════════════════════════════════════════════

def payload_for(monkeypatch, user, row=PERSON) -> dict:
    db = FakeDB(row)
    bind(monkeypatch, db)
    return run(people_core.person_payload(db, row, user))


def test_the_subject_reads_their_own_hr_half_without_the_grant(monkeypatch) -> None:
    """Otherwise "edit your own skills" is a form whose values you cannot see."""
    me = payload_for(monkeypatch, SUBJECT)
    assert me["hr_visible"] is True
    assert me["skills"] == ["firmware"]
    assert me["employment_type"] == "employee"


def test_the_subject_reads_their_own_private_half(monkeypatch) -> None:
    me = payload_for(monkeypatch, SUBJECT)
    assert me["phone"] == "+91 99999 99999"
    assert me["emergency_contact"]["relation"] == "spouse"


def test_a_manager_sees_skills_and_NOT_the_phone_number(monkeypatch) -> None:
    """**D-PC-3.** The private tier is keyed to `admin:members:manage`, not to
    `admin:members:read` — a manager seeing capacity is the point of the HR
    tier, and a manager seeing an emergency contact is not."""
    seen = payload_for(monkeypatch, MANAGER)
    assert seen["skills"] == ["firmware"]
    assert seen["capacity_hours_per_week"] == 40
    assert seen["phone"] is None
    assert seen["emergency_contact"] is None
    assert seen["personal_email"] is None
    assert seen["birthday"] is None


def test_an_admin_sees_both_halves(monkeypatch) -> None:
    seen = payload_for(monkeypatch, ADMIN)
    assert seen["phone"] == "+91 99999 99999"
    assert seen["can_manage"] is True


def test_a_stranger_sees_the_directory_half_only(monkeypatch) -> None:
    seen = payload_for(monkeypatch, STRANGER)
    assert seen["hr_visible"] is False
    assert seen["skills"] == []
    assert seen["phone"] is None
    # …but the self-described directory half is theirs to read: that is what a
    # directory IS.
    assert seen["timezone"] == "Asia/Kolkata"
    assert seen["preferred_name"] == "Pri"
    assert seen["pronouns"] == "she/her"


def test_the_response_shape_is_identical_whatever_the_tier(monkeypatch) -> None:
    """Restricted fields come back null/empty, never absent — so one frontend
    mapper and one generated type read every answer."""
    assert (payload_for(monkeypatch, STRANGER).keys()
            == payload_for(monkeypatch, ADMIN).keys())


def test_the_payload_says_what_this_caller_may_edit(monkeypatch) -> None:
    assert payload_for(monkeypatch, SUBJECT)["editable_fields"] == sorted(
        people_fields.SELF_FIELDS)
    assert payload_for(monkeypatch, STRANGER)["editable_fields"] == []
    assert payload_for(monkeypatch, ADMIN)["editable_fields"] == sorted(
        people_fields.WRITABLE_FIELDS)


def test_is_self_travels_on_the_read(monkeypatch) -> None:
    assert payload_for(monkeypatch, SUBJECT)["is_self"] is True
    assert payload_for(monkeypatch, MANAGER)["is_self"] is False


def test_the_directory_list_does_not_widen_hr_for_a_self_row(monkeypatch) -> None:
    """**The one place the second door does not apply (§4.2).** A list is a
    cross-row read: one row being mine cannot license the skills column across
    the other forty, or the search box becomes the oracle."""
    from gateway.routes.people import directory as people_directory

    db = FakeDB()
    bind(monkeypatch, db, people_directory, people_core)
    res = run(people_directory.list_directory(user=SUBJECT))
    assert res.hr_visible is False
    assert res.rows[0]["skills"] == []
    # The private half is never in a list, for anybody.
    assert res.rows[0]["phone"] is None


def test_the_directory_tells_the_caller_which_row_is_theirs(monkeypatch) -> None:
    from gateway.routes.people import directory as people_directory

    db = FakeDB()
    bind(monkeypatch, db, people_directory, people_core)
    assert run(people_directory.list_directory(user=SUBJECT)).self_person_id \
        == PERSON.id
    assert run(people_directory.list_directory(user=STRANGER)).self_person_id is None


# ══════════════════════════════════════════════════════════════════════════
# 5. /people/me — three states, three answers (§5.3)
# ══════════════════════════════════════════════════════════════════════════

def test_me_resolves_the_callers_own_row(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    res = run(people_self.get_me(user=SUBJECT))
    assert res.state == "resolved"
    assert res.person["is_self"] is True
    assert res.person["phone"] == "+91 99999 99999"


def test_me_says_when_no_row_carries_the_address(monkeypatch) -> None:
    """Not a 404 and not an empty form: a form that silently saves nothing is
    the worst of the three answers."""
    db = FakeDB(row=None)
    bind(monkeypatch, db)
    res = run(people_self.get_me(user=STRANGER))
    assert res.state == "no_directory_row"
    assert res.person is None
    assert "someone@fracktal.in" in res.detail


def test_me_distinguishes_having_no_address_at_all(monkeypatch) -> None:
    """A sign-in state, not a People problem — and nothing in this app fixes
    it, so it must not be reported as "the directory has no row for you"."""
    db = FakeDB()
    bind(monkeypatch, db)
    res = run(people_self.get_me(user=_user(None, "feature:people")))
    assert res.state == "no_identity"
    assert not db.statements, "no lookup should run without an address"


# ══════════════════════════════════════════════════════════════════════════
# 5b. WS-28g-2 — your own row is not behind the directory's gate (D-PC-15)
# ══════════════════════════════════════════════════════════════════════════

MEMBER = _user("colleague@fracktal.in")   # signed in, holds NOTHING


def test_the_self_router_carries_no_feature_gate() -> None:
    """The omission IS the ticket. `feature:people` is `is_default false`, so
    gating the self surface on it made an ordinary colleague unable to open
    their own profile — the one thing that surface exists for."""
    deps = getattr(people_self_router, "dependencies", [])
    names = [getattr(getattr(d, "dependency", None), "__qualname__", "")
             for d in deps]
    assert not any(n.startswith("require_feature_router") for n in names)


def test_the_directory_router_still_carries_its_gate() -> None:
    """The other half of D-PC-15, and the one that would be a leak: opening the
    self surface must not have opened the roster."""
    deps = getattr(people_router, "dependencies", [])
    names = [getattr(getattr(d, "dependency", None), "__qualname__", "")
             for d in deps]
    assert any(n.startswith("require_feature_router") for n in names)


def test_no_self_route_can_address_another_person() -> None:
    """The structural guarantee (§4.5): **the person never comes from the
    request**. No ungated path names a person, and every ungated endpoint
    resolves the row through the self predicate — so there is no id to validate
    and nothing a later refactor can forget.

    ⚠️ This asserted "no path parameter at all" until WS-28k, which needed
    `/me/absences/{absence_id}` — a SPAN, not a person. The invariant is what
    is asserted now; `test_org_access_enforcement.UNGATED_ROUTERS` owns the
    stronger half and applies it to every ungated router, not just this one.
    """
    for route in people_self_router.routes:
        for param in re.findall(r"\{([a-z_]+)\}", route.path):
            assert "person" not in param, route.path
        assert "/me" in route.path, route.path


def test_a_member_with_no_grants_may_edit_their_own_row(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    calls: list[Any] = []

    async def _update(person_id, payload, actor):
        calls.append(person_id)
        return tasks_people._row_to_person(PERSON, include_hr=True,
                                           include_private=True)

    monkeypatch.setattr(tasks_people, "update_person", _update)
    out = run(people_self.update_me(
        tasks_people.PersonWrite(timezone="Europe/Berlin"), user=SUBJECT))
    assert calls == [PERSON.id]
    assert out["is_self"] is True


def test_the_self_door_still_refuses_an_admin_field(monkeypatch) -> None:
    """Ungated is not unchecked: the field classes apply exactly as before."""
    db = FakeDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_self.update_me(
            tasks_people.PersonWrite(status="alumni"), user=SUBJECT))
    assert exc.value.status_code == 403
    assert "status" in exc.value.detail


def test_an_admin_editing_their_OWN_row_keeps_the_admin_class(monkeypatch) -> None:
    """Otherwise the ungated door would be the NARROWER one for exactly the
    people who hold the grant, and an admin would have to find another URL to
    fix their own department."""
    db = FakeDB(person_row(email="admin@fracktal.in"))
    bind(monkeypatch, db)

    async def _update(person_id, payload, actor):
        return tasks_people._row_to_person(PERSON, include_hr=True,
                                           include_private=True)

    monkeypatch.setattr(tasks_people, "update_person", _update)
    run(people_self.update_me(
        tasks_people.PersonWrite(title="Principal"), user=ADMIN))


def test_a_member_with_no_row_is_404_not_a_silent_no_op(monkeypatch) -> None:
    db = FakeDB(row=None)
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_self.update_me(
            tasks_people.PersonWrite(timezone="UTC"), user=MEMBER))
    assert exc.value.status_code == 404


def test_me_is_registered_before_the_person_id_pattern() -> None:
    """FastAPI matches in REGISTRATION order, ACROSS routers. Included the
    other way round, `/people/me` is matched by the gated router's
    `/people/{person_id}` — and an ungranted member is refused at their own
    profile by the directory's gate, which is the exact defect WS-28g-2 fixed,
    reintroduced by an include order.

    Asserted against `main.py`'s source because building the whole gateway app
    in a unit test needs an environment this suite deliberately does not have —
    and the include order is a source fact, so a source assertion is not a
    weaker claim about it, just a cheaper one.
    """
    main = (REPO / "apps" / "services" / "gateway" / "gateway"
            / "main.py").read_text(encoding="utf-8")
    self_at = main.index("app.include_router(_people_self_router)")
    gated_at = main.index("app.include_router(_people_router)")
    assert self_at < gated_at, (
        "main.py includes the gated people router before the ungated self "
        "router — /people/me is now behind feature:people again"
    )


# ══════════════════════════════════════════════════════════════════════════
# 6. The write door
# ══════════════════════════════════════════════════════════════════════════

def patch(monkeypatch, user, **body) -> Any:
    db = FakeDB()
    bind(monkeypatch, db)
    calls: list[Any] = []

    async def _update(person_id, payload, actor):
        calls.append((person_id, payload, actor))
        return tasks_people._row_to_person(PERSON, include_hr=True,
                                           include_private=True)

    monkeypatch.setattr(tasks_people, "update_person", _update)
    result = run(people_profile.update_profile(
        PERSON.id, tasks_people.PersonWrite(**body), user=user))
    return SimpleNamespace(result=result, calls=calls, db=db)


def test_the_subject_may_change_their_own_timezone(monkeypatch) -> None:
    out = patch(monkeypatch, SUBJECT, timezone="Europe/Berlin")
    assert len(out.calls) == 1
    assert out.result["is_self"] is True


def test_the_subject_may_not_change_their_own_title(monkeypatch) -> None:
    with pytest.raises(HTTPException) as exc:
        patch(monkeypatch, SUBJECT, title="Head of Everything")
    assert exc.value.status_code == 403
    assert "title" in exc.value.detail


def test_a_mixed_payload_is_refused_whole_not_half_applied(monkeypatch) -> None:
    """The half that would have been dropped is the half the person cares
    about, and they would never learn it did not land."""
    with pytest.raises(HTTPException):
        patch(monkeypatch, SUBJECT, timezone="Europe/Berlin", status="alumni")


def test_a_stranger_may_not_edit_somebody_else(monkeypatch) -> None:
    with pytest.raises(HTTPException) as exc:
        patch(monkeypatch, STRANGER, timezone="Europe/Berlin")
    assert exc.value.status_code == 403


def test_a_manager_with_only_the_read_grant_may_not_edit(monkeypatch) -> None:
    """`admin:members:read` reads. Writing is `admin:members:manage`, and the
    two being separate grants is the whole reason §4 has two axes."""
    with pytest.raises(HTTPException) as exc:
        patch(monkeypatch, MANAGER, timezone="Europe/Berlin")
    assert exc.value.status_code == 403


def test_an_admin_may_edit_anyone(monkeypatch) -> None:
    out = patch(monkeypatch, ADMIN, title="Principal engineer", status="active")
    assert len(out.calls) == 1


def test_an_empty_patch_is_refused_rather_than_silently_succeeding(
        monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_profile.update_profile(
            PERSON.id, tasks_people.PersonWrite(), user=ADMIN))
    assert exc.value.status_code == 400


def test_a_patch_that_never_mentions_a_field_does_not_authorize_it(
        monkeypatch) -> None:
    """`exclude_unset` is what makes the class check mean anything: a dump that
    filled every unset field with None would read a timezone change as an
    attempt to rewrite the org chart."""
    out = patch(monkeypatch, SUBJECT, timezone="Europe/Berlin")
    _, payload, _ = out.calls[0]
    assert list(payload.model_dump(exclude_unset=True)) == ["timezone"]


def test_the_write_answers_in_THIS_callers_shape(monkeypatch) -> None:
    """`update_person` answers in the admin shape because its own door is
    admin-only. Returned unchanged, it would hand a self-editor the full record
    of whoever they patched."""
    out = patch(monkeypatch, SUBJECT, timezone="Europe/Berlin")
    assert set(out.result) >= {"hr_visible", "can_manage", "is_self",
                               "editable_fields"}


def test_a_missing_person_is_404_not_403(monkeypatch) -> None:
    """403 on a non-existent id would let a caller enumerate which ids exist by
    reading the refusal."""
    db = FakeDB(row=None)
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_profile.update_profile(
            PERSON.id, tasks_people.PersonWrite(timezone="UTC"), user=ADMIN))
    assert exc.value.status_code == 404


def test_the_cv_upload_is_authorized_as_a_self_write(monkeypatch) -> None:
    """"Their CV … can be edited" was explicit in the directive, and the check
    is the PATCH's, not a new rule."""
    db = FakeDB()
    bind(monkeypatch, db)
    seen: list[Any] = []

    async def _ingest(person_id, file, user):
        seen.append(person_id)
        return tasks_people.ResumeIngestResult(
            resume_id="r1", added_skills=["modbus"], extracted={},
            person=tasks_people._row_to_person(PERSON, include_hr=True))

    monkeypatch.setattr(tasks_people, "ingest_resume", _ingest)
    out = run(people_profile.upload_resume(PERSON.id, file=None, user=SUBJECT))
    assert seen == [PERSON.id]
    assert out["added_skills"] == ["modbus"]

    with pytest.raises(HTTPException):
        run(people_profile.upload_resume(PERSON.id, file=None, user=STRANGER))


# ══════════════════════════════════════════════════════════════════════════
# 7. The SQL the shared builder emits
# ══════════════════════════════════════════════════════════════════════════

def build(**fields) -> tuple[str, dict]:
    parts, params = tasks_people.build_person_update(
        fields, PERSON, actor="admin@fracktal.in")
    return ", ".join(parts), params


def test_jsonb_is_cast_and_encoded() -> None:
    """asyncpg has no codec for a bare dict, and `text()` declares no column
    type — the pair of defects `tests/live/live_ws27l.py` exists to pin."""
    sql, params = build(working_hours={"start": "09:00"})
    assert "working_hours = CAST(:working_hours AS JSONB)" in sql
    assert params["working_hours"] == '{"start": "09:00"}'


def test_clearing_a_jsonb_field_binds_null_not_the_string_null() -> None:
    _, params = build(links=None)
    assert params["links"] is None


def test_arrays_are_bound_as_lists_never_json() -> None:
    """json-encoding a list would store the literal '["a"]' in a text array."""
    _, params = build(languages=["English", " Kannada ", "", "  "])
    assert params["languages"] == ["English", "Kannada"]


def test_dates_are_bound_as_dates_not_as_a_cast_over_a_string() -> None:
    """`CAST(:x AS date)` over a bound string is the shape asyncpg REFUSES —
    the WS-27k defect in tests/live/README.md."""
    sql, params = build(start_date="2026-06-01")
    assert "CAST(:start_date" not in sql
    assert params["start_date"] == date(2026, 6, 1)


def test_a_malformed_date_is_a_400_naming_the_column() -> None:
    with pytest.raises(HTTPException) as exc:
        build(end_date="the first of June")
    assert exc.value.status_code == 400
    assert "end_date" in exc.value.detail


def test_a_field_not_in_the_payload_is_not_in_the_update() -> None:
    sql, params = build(timezone="Asia/Kolkata")
    assert "phone" not in sql and "phone" not in params
    assert "timezone = :timezone" in sql


def test_the_builder_always_stamps_who_and_when() -> None:
    sql, params = build(bio="hello")
    assert "updated_at = now()" in sql
    assert params["updated_by"] == "admin@fracktal.in"


@pytest.mark.parametrize("column", sorted(
    set(tasks_people.PersonWrite.model_fields) - {"skills"}))
def test_every_writable_column_reaches_the_update(column: str) -> None:
    """A field the payload accepts and the builder drops is exactly the silent
    discard D-PC-5 refuses — and the create path's INSERT already had one
    (which is why it now runs the builder for the profile half)."""
    sample: dict[str, Any] = {
        "working_hours": {}, "links": {}, "emergency_contact": {},
        "languages": [], "interests": [],
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "years_experience": 3, "capacity_hours_per_week": 40,
        "current_load_hours_per_week": 4, "max_concurrent_tasks": 3,
    }
    sql, _ = build(**{column: sample.get(column, "x")})
    assert column in sql, f"{column} never reaches the UPDATE"


def test_the_create_path_applies_the_profile_half_too() -> None:
    """The 2026-07 INSERT knows nothing of §3's columns. What keeps them from
    being dropped on create is that the set is DERIVED from the payload model,
    not listed a second time."""
    expected = (set(tasks_people.PersonWrite.model_fields)
                - tasks_people._CREATE_INSERT_COLUMNS)
    assert expected == tasks_people._PROFILE_ONLY_COLUMNS
    assert "timezone" in tasks_people._PROFILE_ONLY_COLUMNS
    assert "name" not in tasks_people._PROFILE_ONLY_COLUMNS


# ══════════════════════════════════════════════════════════════════════════
# 8. The vocabularies the migration deliberately did not put in a CHECK
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("value", tasks_people.PersonWrite.model_fields
                         and ("employee", "contractor", "intern", "vendor", "agent"))
def test_every_employment_type_in_the_vocabulary_is_accepted(value: str) -> None:
    people_fields.validate_person_vocabularies({"employment_type": value})


def test_an_employment_type_outside_the_vocabulary_is_a_400_listing_them(
) -> None:
    with pytest.raises(HTTPException) as exc:
        people_fields.validate_person_vocabularies({"employment_type": "freelance"})
    assert exc.value.status_code == 400
    assert "employee" in exc.value.detail


def test_a_seniority_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(HTTPException):
        people_fields.validate_person_vocabularies({"seniority": "rockstar"})


def test_an_unset_vocabulary_field_is_not_a_bad_one() -> None:
    people_fields.validate_person_vocabularies({"name": "Priya"})
    people_fields.validate_person_vocabularies({"seniority": None})


@pytest.mark.parametrize("value", ["01-01", "12-31", "02-29", "03-14"])
def test_a_birthday_is_month_and_day(value: str) -> None:
    people_fields.validate_person_vocabularies({"birthday": value})


@pytest.mark.parametrize("value", [
    "1990-03-14",   # D-PC-9: the year is the thing being refused
    "3-14", "14-03", "13-01", "02-30", "00-10", "march", "",
])
def test_a_birthday_that_is_a_date_of_birth_or_nonsense_is_refused(
        value: str) -> None:
    if value == "":
        # An empty string clears the field rather than failing validation —
        # "I would rather not say" has to be expressible.
        people_fields.validate_person_vocabularies({"birthday": value})
        return
    with pytest.raises(HTTPException) as exc:
        people_fields.validate_person_vocabularies({"birthday": value})
    assert "MM-DD" in exc.value.detail


def test_the_vocabularies_are_one_object_not_two_that_agree() -> None:
    from gateway.routes.tasks import core as tasks_core

    assert people_fields.EMPLOYMENT_TYPES is tasks_core.EMPLOYMENT_TYPES
    assert people_fields.SENIORITY_LEVELS is tasks_core.SENIORITY_LEVELS
    assert people_fields.PRIVATE_FIELDS is tasks_people.PRIVATE_FIELDS


# ══════════════════════════════════════════════════════════════════════════
# 9. The migration is the EXPAND half (R6)
# ══════════════════════════════════════════════════════════════════════════

def migration_sql() -> str:
    return PROFILE_MIGRATION.read_text(encoding="utf-8")


def test_the_migration_exists_and_only_adds() -> None:
    sql = migration_sql()
    body = "\n".join(line for line in sql.splitlines()
                     if not line.strip().startswith("--"))
    assert "DROP COLUMN" not in body.upper()
    assert "RENAME" not in body.upper()
    assert "SET NOT NULL" not in body.upper()
    assert "ALTER COLUMN" not in body.upper()


def test_the_migration_adds_no_check_over_live_data() -> None:
    """R6 keeps a constraint over existing data out of the expand half, which is
    exactly why the two vocabularies are validated in the route (D-PC-8/P-6).
    A CHECK here would be a migration that can fail a deploy — main has been
    bitten twice."""
    body = "\n".join(line for line in migration_sql().splitlines()
                     if not line.strip().startswith("--"))
    # `CHECK (` — the constraint, not the word, which appears in the COMMENT
    # explaining why there is no constraint.
    assert not re.search(r"CHECK\s*\(", body, re.IGNORECASE)
    assert "ADD CONSTRAINT" not in body.upper()


def test_every_column_the_migration_adds_is_idempotent() -> None:
    for line in migration_sql().splitlines():
        if line.strip().upper().startswith("ALTER TABLE"):
            assert "IF NOT EXISTS" in line.upper(), line


def test_every_column_the_migration_adds_is_classified() -> None:
    """The narrow version of the partition fence, aimed at this ticket: a
    column shipped in 171 with no class is a column with no gate."""
    added = {m.group(1).lower() for m in _ADD_COLUMN.finditer(migration_sql())}
    assert len(added) == 20, sorted(added)
    classified = (people_fields.ADMIN_FIELDS | people_fields.SELF_FIELDS
                  | people_fields.DERIVED_FIELDS)
    assert added <= classified


def test_the_migration_stores_no_date_of_birth() -> None:
    """D-PC-9, asserted against the SQL rather than trusted to the review: the
    column is TEXT and named `birthday`, and no DATE column carries a birth
    date."""
    sql = migration_sql()
    assert re.search(r"birthday\s+TEXT", sql, re.IGNORECASE)
    assert "date_of_birth" not in sql.lower()
    assert not re.search(r"birth\w*\s+DATE", sql, re.IGNORECASE)


def test_the_private_columns_are_the_ones_the_projection_hides() -> None:
    """The migration's §3.5 block and `PRIVATE_FIELDS` have to name the same
    columns, or a field lands in the database with the private tier's comment
    above it and the HR tier's enforcement around it."""
    assert set(people_fields.PRIVATE_FIELDS) == {
        "phone", "emergency_contact", "personal_email", "birthday"}
    for column in people_fields.PRIVATE_FIELDS:
        assert column in migration_sql()


def test_the_projection_blanks_every_private_field() -> None:
    blanked = tasks_people._blank_private()
    assert set(blanked) == set(tasks_people.PRIVATE_FIELDS)
    assert all(v is None for v in blanked.values())


def test_the_projection_blanks_every_hr_field() -> None:
    """Built from the tuple rather than typed out, so adding a field to
    HR_FIELDS cannot leave it visible to a caller the tuple says may not see
    it — the silent direction."""
    blanked = tasks_people._blank_hr()
    assert set(blanked) == set(tasks_people.HR_FIELDS)
    assert blanked["skills"] == [] and blanked["interests"] == []
    assert blanked["employment_type"] is None
