"""WS-28b — the People Center's directory and person page.

Spec: `project-docs/specs/people_center_app.md` §3.1, §3.2, §5.2, §6.

The claims worth testing are about the *shape of the answer*, because this
app's whole permission story is a projection rather than a refusal:

* the HR half comes back nulled without `admin:members:read`, and the response
  SAYS SO — "you may not see this" and "nobody filled it in" are different
  facts and a blank field cannot tell them apart;
* the search box cannot become an oracle for the field the projection hides;
* the work panel is scoped by the VIEWER's project grants, not the subject's;
* load is computed from open assigned tasks, never read from the typed column.

**Why a local double rather than `_projects_fakes`.** That fake models the
statement shapes the Projects routes emit; these routes emit aggregates,
`GROUP BY`s and a cross-table existence check it was never built for. Teaching
it those shapes to serve one suite is how a shared fake becomes a second, worse
implementation of Postgres — the exact mirror-failure its own docstring warns
about. The double below answers by statement fingerprint and does nothing else.

The genuinely interesting decisions — *which clauses exist at all* — are pure
and are tested directly against `build_directory_filters`, because proving them
through any fake would prove them about the fake.

Hermetic: no Postgres, no network.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.people import core as people_core
from gateway.routes.people import directory as people_directory
from gateway.routes.people.directory import build_directory_filters

REPO = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


# ── The double ──────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    """Answers by statement fingerprint. Deliberately dumb.

    Every canned answer is set by the test that needs it, so a test cannot pass
    on a row some earlier fixture happened to leave lying around.
    """

    def __init__(self) -> None:
        self.people: list[Any] = []
        self.logins: set[str] = set()
        self.load_row: Any = SimpleNamespace(open_tasks=0, mins=0, unestimated=0)
        self.work: list[Any] = []
        self.facets: list[Any] = []
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        self.params.append(dict(params or {}))
        if "FROM app_user" in statement:
            wanted = (params or {}).get("email")
            return _Result([SimpleNamespace(x=1)] if wanted in self.logins else [])
        if "AS open_tasks" in statement:
            return _Result([self.load_row])
        if "GROUP BY department" in statement:
            return _Result(self.facets)
        if "FROM pm_tasks" in statement:
            return _Result(self.work)
        if "SELECT email FROM gtd_people" in statement or "FROM gtd_people" in statement:
            return _Result(self.people)
        return _Result([])

    async def commit(self) -> None:  # pragma: no cover - reads only
        return None

    async def close(self) -> None:
        return None


def person_row(**columns) -> SimpleNamespace:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Rahul", "email": "r@x.in", "role": "Engineer", "title": None,
        "department": "Engineering", "team": "Firmware", "reports_to": None,
        "manager_id": None, "status": "active", "skills": ["Firmware"],
        "skills_source": {}, "domain": None, "resume_summary": "5y embedded",
        "years_experience": 5, "capacity_hours_per_week": 40,
        "current_load_hours_per_week": 30, "available_hours_per_week": 10,
        "clickup_user_id": None, "email_conflict": None,
    }
    base.update(columns)
    return SimpleNamespace(**base)


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def bind(monkeypatch, db):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        # Commit-on-clean-exit, like the real `tenant_session` wrapper (H2).
        yield db
        await db.commit()

    for module in (people_core, people_directory):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)


def _user(email: str, *grants: str):
    """A principal built with the REAL `build_access`, so the permissions the
    routes check are the ones this caller actually resolves — a hand-stubbed
    `has_permission` would prove only that the stub agrees with itself."""
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(grants)),
    )


def hr_reader(email: str = "admin@fracktal.in"):
    return _user(email, "feature:people", "feature:projects", "admin:members:read")


def plain(email: str = "member@fracktal.in"):
    """A directory holder with neither HR read nor the Projects app.

    The principal every projection test needs: without it the suite proves only
    that a `*`-holder sees everything, which is true of an unprojected route.
    """
    return _user(email, "feature:people")


# ── The filter rules (pure) ─────────────────────────────────────────────────

def test_search_matches_the_basic_directory_for_everyone():
    clauses, params = build_directory_filters(hr=False, q="rahul")
    joined = " ".join(clauses)
    assert "name ILIKE :q" in joined and "department ILIKE :q" in joined
    assert params["q"] == "%rahul%"


def test_search_cannot_become_an_oracle_for_the_hidden_field():
    """Matching on a column that is then stripped from the response would let a
    caller confirm a skill they may not read."""
    allowed = " ".join(build_directory_filters(hr=True, q="crypto")[0])
    denied = " ".join(build_directory_filters(hr=False, q="crypto")[0])
    assert "unnest(skills)" in allowed
    assert "unnest(skills)" not in denied


def test_the_skill_filter_is_dropped_without_the_hr_permission():
    allowed, allowed_params = build_directory_filters(hr=True, skill="crypto")
    denied, denied_params = build_directory_filters(hr=False, skill="crypto")
    assert "unnest(skills)" in " ".join(allowed)
    assert allowed_params["skill"] == "%crypto%"
    assert "unnest(skills)" not in " ".join(denied)
    assert "skill" not in denied_params


def test_has_capacity_is_dropped_without_the_hr_permission():
    """Derived from the restricted capacity trio, so it is the same rule."""
    assert "available_hours_per_week" in " ".join(
        build_directory_filters(hr=True, has_capacity=True)[0]
    )
    assert "available_hours_per_week" not in " ".join(
        build_directory_filters(hr=False, has_capacity=True)[0]
    )


def test_the_basic_filters_apply_regardless_of_hr_permission():
    for hr in (True, False):
        clauses, params = build_directory_filters(
            hr=hr, department="Engineering", team="Firmware", status="active",
        )
        joined = " ".join(clauses)
        assert "lower(department)" in joined
        assert "lower(team)" in joined
        assert "status = :status" in joined
        # Case-folded, so a filter chip typed either way finds the same people.
        assert params["department"] == "engineering"


def test_no_filters_means_no_predicates():
    clauses, params = build_directory_filters(hr=True)
    assert clauses == ["true"]
    assert params == {}


def test_a_blank_search_is_not_a_search():
    assert build_directory_filters(hr=True, q="   ")[0] == ["true"]


# ── The directory route ─────────────────────────────────────────────────────

def test_the_directory_states_whether_the_hr_half_is_visible(db):
    db.people = [person_row()]
    assert run(people_directory.list_directory(user=hr_reader())).hr_visible is True
    assert run(people_directory.list_directory(user=plain())).hr_visible is False


def test_skills_come_back_nulled_without_the_hr_permission(db):
    db.people = [person_row(skills=["Firmware", "C++"])]
    denied = run(people_directory.list_directory(user=plain()))
    assert denied.rows[0]["skills"] == []
    assert denied.rows[0]["resume_summary"] is None
    # …and the basic half survives: this is a projection, not a refusal. Someone
    # who cannot see skills can still find the person.
    assert denied.rows[0]["name"] == "Rahul"
    assert denied.rows[0]["department"] == "Engineering"


def test_an_unknown_status_filter_is_a_422_not_an_empty_list(db):
    """An empty list would answer a typo as a fact — "nobody is a contracter"."""
    with pytest.raises(HTTPException) as err:
        run(people_directory.list_directory(user=hr_reader(), status="contracter"))
    assert err.value.status_code == 422


# ── The person page ─────────────────────────────────────────────────────────

def test_a_person_with_an_app_user_row_is_badged_as_having_a_login(db):
    db.people = [person_row(email="R@X.in")]
    db.logins = {"r@x.in"}
    detail = run(people_directory.get_person("pid", user=hr_reader()))
    # Case-insensitive on both sides (R10) — the badge must not depend on how
    # the address happened to be typed.
    assert detail["has_login"] is True


def test_a_directory_only_person_is_badged_as_such(db):
    db.people = [person_row(email="contractor@x.in")]
    detail = run(people_directory.get_person("pid", user=hr_reader()))
    assert detail["has_login"] is False


def test_a_quarantined_address_is_surfaced_not_hidden(db):
    """Migration 148 moves a duplicate aside; a human still has to decide which
    row is the real person, and they cannot if nothing says so."""
    db.people = [person_row(email=None, email_conflict="shared@x.in")]
    detail = run(people_directory.get_person("pid", user=hr_reader()))
    assert detail["email_conflict"] == "shared@x.in"


def test_a_missing_person_is_a_404(db):
    with pytest.raises(HTTPException) as err:
        run(people_directory.get_person("nope", user=hr_reader()))
    assert err.value.status_code == 404


def test_load_is_absent_entirely_without_the_hr_permission(db):
    db.people = [person_row()]
    detail = run(people_directory.get_person("pid", user=plain()))
    assert detail["load"] is None
    assert detail["hr_visible"] is False


# ── Computed load (§5.2) ────────────────────────────────────────────────────

def test_load_is_computed_from_open_assigned_tasks(db):
    """`current_load_hours_per_week` is a number somebody typed once and is
    stale the moment anyone assigns anything."""
    db.load_row = SimpleNamespace(open_tasks=3, mins=120, unestimated=0)
    load = run(people_directory.compute_load(db, "R@X.in"))
    assert load == {"open_tasks": 3, "estimated_hours": 2.0, "unestimated": 0}
    # …and it asks about the right person, case-folded.
    assert db.params[-1]["who"] == "r@x.in"


def test_the_load_query_excludes_closed_work(db):
    run(people_directory.compute_load(db, "r@x.in"))
    statement = db.statements[-1]
    assert "NOT IN ('done', 'cancelled')" in statement
    assert "archived_at IS NULL" in statement


def test_unestimated_tasks_are_counted_even_though_they_add_no_hours(db):
    """Without this count, a bar built from the sum shows somebody holding
    thirty un-estimated tasks as completely free."""
    db.load_row = SimpleNamespace(open_tasks=30, mins=0, unestimated=30)
    load = run(people_directory.compute_load(db, "r@x.in"))
    assert load["estimated_hours"] == 0.0
    assert load["unestimated"] == 30


def test_somebody_with_no_address_has_no_computable_load(db):
    """Assignment targets are addresses, so there is nothing to look up — and
    no query is issued at all."""
    load = run(people_directory.compute_load(db, None))
    assert load == {"open_tasks": 0, "estimated_hours": 0.0, "unestimated": 0}
    assert db.statements == []


# ── The work panel ──────────────────────────────────────────────────────────

def test_the_work_panel_says_unavailable_without_the_projects_feature(db):
    """"This surface is not yours" and "they have nothing open" must not render
    identically."""
    work = run(people_directory.get_person_work("pid", user=plain()))
    assert work.available is False
    assert work.rows == []
    # And it does not even ask the database.
    assert db.statements == []


def test_the_work_panel_is_available_but_empty_for_an_addressless_person(db):
    db.people = [SimpleNamespace(email=None)]
    work = run(people_directory.get_person_work("pid", user=hr_reader()))
    assert work.available is True
    assert work.total == 0


def test_the_work_panel_lists_open_tasks(db):
    db.people = [SimpleNamespace(email="r@x.in")]
    db.work = [SimpleNamespace(
        id="t1", title="Ship it", task_number=7, due_at=None,
        project_id="p1", project_name="Delivery",
        status_name="Doing", status_category="in_progress",
    )]
    work = run(people_directory.get_person_work("pid", user=hr_reader()))
    assert work.total == 1
    assert work.rows[0]["project_name"] == "Delivery"


def test_the_work_panel_is_scoped_by_the_VIEWERS_grants(db, monkeypatch):
    """A Sales lead looking at an Operations colleague sees the work they
    share, not that person's whole life — so a restricted viewer's query must
    carry the grant-closure predicate."""
    db.people = [SimpleNamespace(email="r@x.in")]
    work_sql: list[str] = []

    async def fake_resolve(_db, _user):
        from gateway.routes.projects.core import Visibility

        return Visibility(unrestricted=False, email="me@x.in", groups=("group:sales",))

    monkeypatch.setattr(
        "gateway.routes.projects.core.resolve_visibility", fake_resolve
    )
    run(people_directory.get_person_work("pid", user=hr_reader()))
    work_sql = [s for s in db.statements if "FROM pm_tasks" in s]
    assert "root_project_id IN" in work_sql[-1]
    assert db.params[-1]["vis_email"] == "me@x.in"


def test_an_unrestricted_viewer_STILL_GETS_THE_TENANT_PREDICATE(db, monkeypatch):
    """🔴 **This test asserted the opposite until 2026-09-21, and that is how
    a cross-tenant leak survived five weeks.**

    The old version read *"`data:org:read` is the full-portfolio view; adding
    the closure anyway would put a recursive join on every read for no
    effect"* — plausible, and wrong in the one way that mattered.
    `Visibility.project_clause` does NOT return `TRUE` for an unrestricted
    caller. It returns ``column IN (<tenant projects>)``
    (`projects/core.py:1049-1050`). The clause IS the tenant fence, so
    skipping it left this query with an assignee predicate and nothing else.

    What that cost: `data:org:read` is held by the `manager` role. A manager
    opening a colleague's work saw every open task in ANY organization
    assigned to that address. WS-28j measured the same shortcut elsewhere on
    the scratch cluster — no FORCE RLS, which is production's state while
    H-104 is open — and a second organization's task landed on the row.

    `data:org:read` means unrestricted *within* a tenant. Never across.
    """
    db.people = [SimpleNamespace(email="r@x.in")]

    async def fake_resolve(_db, _user):
        from gateway.routes.projects.core import Visibility

        return Visibility(unrestricted=True, email="", groups=())

    monkeypatch.setattr(
        "gateway.routes.projects.core.resolve_visibility", fake_resolve
    )
    run(people_directory.get_person_work("pid", user=hr_reader()))
    work_sql = [s for s in db.statements if "FROM pm_tasks" in s][-1]
    assert "root_project_id IN" in work_sql


def test_the_work_panel_scopes_with_the_DASHBOARDS_helper(db, monkeypatch):
    """Asserted by identity, not by resemblance.

    Two answers to "what may this viewer see" are two answers waiting to
    drift, and the drift is what produced the leak above: the dashboard
    applied the clause unconditionally while this endpoint did not, for five
    weeks, with the dashboard's own docstring naming it.
    """
    from gateway.routes.people import dashboard, directory as d

    assert d._scope is dashboard._scope


def test_the_work_panel_404s_for_a_person_who_does_not_exist(db):
    with pytest.raises(HTTPException) as err:
        run(people_directory.get_person_work("nope", user=hr_reader()))
    assert err.value.status_code == 404


# ── Facets ──────────────────────────────────────────────────────────────────

def test_facets_are_derived_from_the_rows(db):
    """A department list somebody has to maintain goes stale the first time the
    org changes."""
    db.facets = [
        SimpleNamespace(department="Engineering", team="Firmware", total=2),
        SimpleNamespace(department="Engineering", team="Cloud", total=1),
        SimpleNamespace(department="Sales", team=None, total=1),
    ]
    facets = run(people_directory.list_facets(user=hr_reader()))
    assert {d["department"]: d["total"] for d in facets["departments"]} == {
        "Engineering": 3, "Sales": 1,
    }
    assert {t["team"] for t in facets["teams"]} == {"Firmware", "Cloud"}
    assert facets["statuses"] == list(people_core.STATUSES)


# ── Registration (§6) ───────────────────────────────────────────────────────

def test_the_feature_catalog_row_exists_and_points_at_the_app():
    """Found by CONTENT, not by migration number — R1 forbids writing an
    absolute future number anywhere, so a test pinned to `149_` would be the
    same mistake in a different file."""
    hits = [
        p for p in (REPO / "infra" / "postgres").glob("*.sql")
        if "'people', 'People'" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, hits
    raw = hits[0].read_text(encoding="utf-8")
    # Comments stripped before asserting — this file's header *explains* the
    # is_default rule at length, so a naive substring check passes on the prose
    # even after the statement stops honouring it. Same trap WS-28a's migration
    # fence hit; assertions must read what Postgres will execute.
    sql = "\n".join(re.sub(r"--.*$", "", line) for line in raw.splitlines())
    assert "'/people'" in sql
    # Same posture as crm and projects: reachable only once granted.
    assert "false)" in sql
    # A redeploy must not stomp an admin's retuned default set.
    assert "is_default" not in sql.split("ON CONFLICT")[1]


def test_the_router_gates_on_its_own_feature():
    """Not `feature:tasks`: the People Center is a different audience."""
    assert people_core.router.prefix == "/people"
    assert people_core.router.dependencies, "the router carries no gate at all"


def test_the_hr_permission_is_imported_not_redefined():
    """§6: "a restriction that already exists and must not be re-implemented
    here". Two definitions of "may this caller see skills" are two answers
    waiting to drift."""
    from gateway.routes.tasks.core import can_read_hr_fields as source

    assert people_core.can_read_hr_fields is source


def test_the_person_row_shape_is_not_reimplemented():
    """The directory renders `_row_to_person`'s projection, so a field added to
    the restricted set is hidden here automatically rather than by memory."""
    assert people_directory._row_to_person.__module__ == "gateway.routes.tasks.people"


def test_the_status_vocabulary_matches_the_migrations_check():
    assert people_core.STATUSES == ("active", "contractor", "alumni", "invited")


def test_has_login_is_false_for_a_blank_address(db):
    assert run(people_core.has_login(db, "   ")) is False
    assert run(people_core.has_login(db, None)) is False
    assert db.statements == []
