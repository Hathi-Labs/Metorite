"""WS-28k — availability, and deliberately not leave management.

Spec: `project-docs/specs/people_center_app.md` §5.8 · **D-PC-7**.

Three claims:

* **The arithmetic.** "How many working hours does this person have before the
  deadline" is what §5.7.2's *at risk* is built on, and it is wrong by a whole
  week if it counts days somebody is on holiday. Tested as numbers.
* **The scope.** There is no approver, no status, no balance and no
  entitlement — and a **structural fence** says so, because the way this
  becomes leave management is one reasonable-looking column at a time.
* **The door.** A person records their own absence (requiring an admin to type
  it is how the data ends up missing, which makes every capacity figure that
  reads it quietly wrong), and an id belonging to a colleague deletes nothing.
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
from gateway import work_schedule as ws
from gateway.routes.people import absences as people_absences
from gateway.routes.people import core as people_core
from gateway.routes.people import selfservice as people_self
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "infra" / "postgres" / "174_people_absences.sql"

WEEK = ws.effective_schedule({}, None)          # Mon-Fri, 8h


def run(coro):
    return asyncio.run(coro)


def span(start: str, end: str, kind: str = "away",
         hours: float | None = None) -> dict[str, Any]:
    return {"starts_on": date.fromisoformat(start),
            "ends_on": date.fromisoformat(end),
            "kind": kind, "hours_per_day": hours}


# ══════════════════════════════════════════════════════════════════════════
# 1. The arithmetic "at risk" is built on
# ══════════════════════════════════════════════════════════════════════════

MON = "2026-08-10"      # a Monday
FRI = "2026-08-14"
SUN = "2026-08-16"


def test_a_full_week_is_five_days() -> None:
    assert ws.working_days_between(WEEK, date(2026, 8, 10), date(2026, 8, 16)) == 5.0


def test_the_weekend_is_not_working_time() -> None:
    assert ws.working_days_between(WEEK, date(2026, 8, 15), date(2026, 8, 16)) == 0.0


def test_a_holiday_week_leaves_no_hours() -> None:
    """The difference between "the deadline is five days away" and "they have
    no time before it" — which is the whole point of the surface."""
    assert ws.working_hours_between(
        WEEK, date(2026, 8, 10), date(2026, 8, 16), [span(MON, SUN)]) == 0.0


def test_two_days_away_leaves_three() -> None:
    assert ws.working_days_between(
        WEEK, date(2026, 8, 10), date(2026, 8, 16),
        [span("2026-08-11", "2026-08-12")]) == 3.0


def test_an_absence_over_the_weekend_costs_nothing_extra() -> None:
    """Somebody "away Sat-Sun" has not lost working time, and counting it would
    make a Friday deadline look unreachable."""
    assert ws.working_days_between(
        WEEK, date(2026, 8, 10), date(2026, 8, 16),
        [span("2026-08-15", "2026-08-16")]) == 5.0


def test_a_partial_day_costs_a_fraction() -> None:
    """A half day is half a day. Rounding it either way makes a week's
    arithmetic wrong by more than the half day."""
    assert ws.working_days_between(
        WEEK, date(2026, 8, 10), date(2026, 8, 10),
        [span(MON, MON, "partial", 0.5)]) == 0.5


def test_a_partial_with_no_figure_is_half_a_day() -> None:
    assert ws.working_days_between(
        WEEK, date(2026, 8, 10), date(2026, 8, 10),
        [span(MON, MON, "partial")]) == 0.5


def test_a_full_absence_beats_an_overlapping_partial() -> None:
    """Otherwise somebody on holiday who also logged a half day shows as
    half-available, and a picker offers them."""
    assert ws.working_days_between(
        WEEK, date(2026, 8, 10), date(2026, 8, 10),
        [span(MON, MON, "partial", 0.5), span(MON, MON, "away")]) == 0.0


def test_two_overlapping_partials_take_the_smallest() -> None:
    """Two claims on one day are two reasons to be less available, not an
    average of them."""
    assert ws.working_days_between(
        WEEK, date(2026, 8, 10), date(2026, 8, 10),
        [span(MON, MON, "partial", 0.5),
         span(MON, MON, "partial", 0.25)]) == 0.25


def test_a_half_timer_and_an_absence_compound() -> None:
    half = ws.effective_schedule({}, {"fraction": 0.5})
    assert ws.working_days_between(
        half, date(2026, 8, 10), date(2026, 8, 16),
        [span("2026-08-11", "2026-08-11")]) == 2.0


def test_hours_follow_the_schedule_not_a_constant() -> None:
    six = ws.effective_schedule({"hours_per_day": 6}, None)
    assert ws.working_hours_between(
        six, date(2026, 8, 10), date(2026, 8, 16)) == 30.0


@pytest.mark.parametrize("junk", [
    [{"starts_on": "2026-08-10", "ends_on": "2026-08-11"}],   # strings, not dates
    [{"starts_on": date(2026, 8, 12), "ends_on": date(2026, 8, 10)}],  # backwards
    [{}], [None], "not a list",
])
def test_an_unusable_span_costs_only_itself(junk: Any) -> None:
    """Tolerant on the way in for the same reason `normalise_policy` is: one bad
    row should not take a week's arithmetic with it."""
    assert ws.working_days_between(
        WEEK, date(2026, 8, 10), date(2026, 8, 16), junk) == 5.0


def test_a_backwards_window_is_zero_not_negative() -> None:
    assert ws.working_days_between(WEEK, date(2026, 8, 16), date(2026, 8, 10)) == 0.0


def test_absent_on_answers_which_span_covers_a_day() -> None:
    assert ws.absent_on(date(2026, 8, 11), [span(MON, FRI)])["kind"] == "away"
    assert ws.absent_on(date(2026, 8, 20), [span(MON, FRI)]) is None
    assert ws.absent_on(date(2026, 8, 11), None) is None


def test_absent_on_prefers_the_full_absence() -> None:
    """"Away" and "half a day" on one date is away — answering "partial" would
    put somebody on a picker as available."""
    both = [span(MON, MON, "partial", 0.5), span(MON, MON, "holiday")]
    assert ws.absent_on(date(2026, 8, 10), both)["kind"] == "holiday"


# ══════════════════════════════════════════════════════════════════════════
# 2. Availability, NOT leave management (D-PC-7)
# ══════════════════════════════════════════════════════════════════════════

def test_the_table_has_no_approval_machinery() -> None:
    """**The structural fence.** This becomes leave management one
    reasonable-looking column at a time — `approved_by` first, because somebody
    will want to know who said yes. A half-built approval chain is worse than
    none: it looks like a control, so people stop checking with each other, and
    then it turns out nothing was ever enforced.

    A ticket that needs one of these words has become a different product and
    needs §10's decision first (D-PC-7).
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(line for line in sql.splitlines()
                     if not line.strip().startswith("--"))
    for word in ("approv", "balance", "accrual", "entitlement", "carry_over",
                 "status", "requested", "rejected"):
        assert word not in body.lower(), (
            f"'{word}' in the absences table — this is availability, not leave "
            "management (D-PC-7)."
        )


def test_the_vocabulary_is_three_words_and_matches_the_check() -> None:
    """Every additional kind is a policy question wearing a vocabulary
    disguise: "sick" invites "how many days left"."""
    sql = MIGRATION.read_text(encoding="utf-8")
    in_check = set(re.findall(r"kind IN \(([^)]+)\)", sql)[0].replace("'", "")
                   .replace(" ", "").split(","))
    assert in_check == set(people_absences.ABSENCE_KINDS)


def test_the_table_refuses_an_end_before_its_start() -> None:
    """Held at the database too, because an importer or a hand-run statement is
    a writer the route never sees."""
    assert "ends_on >= starts_on" in MIGRATION.read_text(encoding="utf-8")


def test_the_table_is_tenant_scoped_by_construction() -> None:
    """R5a: a new table is discovered by the generator and NOT exempt."""
    import importlib.util
    import sys

    path = REPO / "scripts" / "gen_tenant_migration.py"
    spec = importlib.util.spec_from_file_location("gen_tenant_migration", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_tenant_migration"] = module
    spec.loader.exec_module(module)

    assert "people_absences" in module.discover_tables()
    assert "people_absences" not in module.EXEMPT


# ══════════════════════════════════════════════════════════════════════════
# 3. Validation
# ══════════════════════════════════════════════════════════════════════════

def body(**over: Any) -> people_absences.AbsenceIn:
    return people_absences.AbsenceIn(
        **{"starts_on": MON, "ends_on": FRI, **over})


def test_a_valid_span_passes() -> None:
    assert people_absences.validate(body())["starts_on"] == date(2026, 8, 10)


def test_a_backwards_span_is_refused_in_words() -> None:
    with pytest.raises(HTTPException) as exc:
        people_absences.validate(body(starts_on=FRI, ends_on=MON))
    assert exc.value.status_code == 400
    assert "before it starts" in exc.value.detail


def test_a_date_that_is_not_a_date_names_the_field() -> None:
    with pytest.raises(HTTPException) as exc:
        people_absences.validate(body(ends_on="next friday"))
    assert "ends_on" in exc.value.detail


def test_an_unknown_kind_lists_the_three() -> None:
    with pytest.raises(HTTPException) as exc:
        people_absences.validate(body(kind="sick"))
    assert "away" in exc.value.detail


def test_hours_on_a_full_absence_is_refused_rather_than_stored() -> None:
    """Storing it silently would leave a number the arithmetic ignores — which
    is how somebody comes to believe their half-day was recorded."""
    with pytest.raises(HTTPException) as exc:
        people_absences.validate(body(kind="away", hours_per_day=4))
    assert "partial" in exc.value.detail


@pytest.mark.parametrize("hours", [0, -1, 25])
def test_an_impossible_day_length_is_refused(hours: float) -> None:
    with pytest.raises(HTTPException):
        people_absences.validate(body(kind="partial", hours_per_day=hours))


def test_a_blank_note_is_stored_as_null() -> None:
    assert people_absences.validate(body(note="   "))["note"] is None


# ══════════════════════════════════════════════════════════════════════════
# 4. The doors
# ══════════════════════════════════════════════════════════════════════════

class _Result:
    def __init__(self, rows: list[Any], rowcount: int = 0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


PERSON = SimpleNamespace(
    id="11111111-1111-1111-1111-111111111111", name="Priya",
    email="priya@fracktal.in", role=None, title=None, department=None,
    team=None, reports_to=None, manager_id=None, status="active",
    skills=[], skills_source={}, domain=None, resume_summary=None,
    years_experience=None, capacity_hours_per_week=None,
    current_load_hours_per_week=None, available_hours_per_week=None,
    clickup_user_id=None, email_conflict=None, working_hours=None,
)


class FakeDB:
    def __init__(self, deleted: int = 1):
        self.deleted = deleted
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        self.params.append(dict(params or {}))
        if statement.startswith("DELETE FROM people_absences"):
            return _Result([], rowcount=self.deleted)
        if "INSERT INTO people_absences" in statement:
            return _Result([SimpleNamespace(id="aaaaaaaa-0000-0000-0000-000000000000")])
        if "FROM people_absences" in statement:
            return _Result([])
        if hits(statement, "FROM people"):
            wanted = (params or {}).get("email")
            if wanted is not None:
                return _Result([PERSON] if PERSON.email == wanted else [])
            return _Result([PERSON])
        return _Result([])

    async def commit(self) -> None:
        return None

    def issued(self, fragment: str) -> bool:
        return any(fragment in s for s in self.statements)


def bind(monkeypatch, db: FakeDB) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db
        await db.commit()

    for module in (people_core, people_self, people_absences):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)


def _user(email: str | None, *grants: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


SUBJECT = _user("priya@fracktal.in")                    # no grants at all
ADMIN = _user("admin@fracktal.in", "feature:people", "admin:members:manage")
STRANGER = _user("someone@fracktal.in", "feature:people")


def test_a_member_with_no_grants_records_their_own_absence(monkeypatch) -> None:
    """Requiring an admin to type it is how the data ends up absent — and then
    every capacity figure that reads it is quietly wrong."""
    db = FakeDB()
    bind(monkeypatch, db)
    out = run(people_self.add_my_absence(body(), user=SUBJECT))
    assert out["kind"] == "away"
    assert db.issued("INSERT INTO people_absences")


def test_a_stranger_may_not_record_one_for_somebody_else(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_absences.add_absence(PERSON.id, body(), user=STRANGER))
    assert exc.value.status_code == 403
    assert not db.issued("INSERT INTO people_absences")


def test_an_admin_may_record_one_for_anybody(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_absences.add_absence(PERSON.id, body(), user=ADMIN))
    assert db.issued("INSERT INTO people_absences")


def test_the_delete_is_scoped_to_the_person_not_just_the_id(monkeypatch) -> None:
    """**The control, not belt-and-braces.** The id alone identifies a row
    belonging to anybody; the caller was authorized against a PERSON."""
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_self.remove_my_absence("aaaa-bbbb", user=SUBJECT))
    [sql] = [s for s in db.statements if s.startswith("DELETE FROM people_absences")]
    assert "person_id = CAST(:pid AS uuid)" in sql


def test_an_absence_that_is_not_yours_is_a_404_not_a_deletion(monkeypatch) -> None:
    db = FakeDB(deleted=0)
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_self.remove_my_absence("somebody-elses", user=SUBJECT))
    assert exc.value.status_code == 404


def test_seeing_when_somebody_else_is_away_needs_the_hr_grant(monkeypatch) -> None:
    """When and why somebody is off is capacity information — the same tier
    skills have been behind since WS-24 N4. The bare "they are away" fact is
    directory tier and travels on the person read instead."""
    db = FakeDB()
    bind(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        run(people_absences.list_absences(PERSON.id, user=STRANGER))
    assert exc.value.status_code == 403


def test_you_may_always_see_your_own(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    assert run(people_absences.list_absences(PERSON.id, user=SUBJECT)) == {"rows": []}


def test_a_missing_person_is_404(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)

    async def _empty(sql, params=None):
        return _Result([])

    monkeypatch.setattr(db, "execute", _empty)
    with pytest.raises(HTTPException) as exc:
        run(people_absences.list_absences(PERSON.id, user=ADMIN))
    assert exc.value.status_code == 404


def test_the_dates_are_bound_as_dates_not_cast_over_a_string(monkeypatch) -> None:
    """`CAST(:x AS date)` over a bound string is the shape asyncpg refuses —
    the WS-27k defect. The route parses, exactly as the profile's start_date
    does."""
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_self.add_my_absence(body(), user=SUBJECT))
    insert = next(p for s, p in zip(db.statements, db.params, strict=True)
                  if "INSERT INTO people_absences" in s)
    assert isinstance(insert["starts_on"], date)
    sql = next(s for s in db.statements if "INSERT INTO people_absences" in s)
    assert "CAST(:starts_on" not in sql
