"""WS-28e — the directory-backed assignee picker.

Spec: `project-docs/specs/people_center_app.md` §6.1 · D-PM-4 · D-PC-12.

Three claims:

* **Directory-only people are offered, and say so.** A contractor with no
  login can hold a task (D-PC-12); hiding them would make the directory's
  contractor story unusable, and hiding the "no login" fact would promise a
  notification that will never arrive.
* **The HR half follows the caller's grant** (§4.2): load, contracted hours
  and skills are absent — with `hr_visible: false` saying so — for a caller
  without `admin:members:read`. The directory half always renders.
* **Warnings are shown, never enforced.** The endpoint suggests and never
  writes (D-PC-13) — structurally, no INSERT/UPDATE/DELETE in the module.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acb_auth import UserContext, UserRole, build_access
from gateway.routes.people import core as people_core
from gateway.routes.projects import assignees as picker
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "apps/services/gateway/gateway/routes/projects/assignees.py"
          ).read_text(encoding="utf-8")


def run(coro):
    return asyncio.run(coro)


TODAY = date.today()

PRIYA = SimpleNamespace(
    id="11111111-1111-1111-1111-111111111111", name="Priya",
    email="priya@fracktal.in", title="Firmware", department="Engineering",
    avatar=None, end_date=None, working_hours=None,
    skills=["firmware", "altium", "python", "kicad"])
#: Directory-only: a contractor with no app_user row.
NEHA = SimpleNamespace(
    id="22222222-2222-2222-2222-222222222222", name="Neha (contractor)",
    email="neha@contractor.example", title=None, department=None,
    avatar=None, end_date=TODAY + timedelta(days=10), working_hours=None,
    skills=[])
#: No email at all — nothing to assign to.
GHOST = SimpleNamespace(
    id="33333333-3333-3333-3333-333333333333", name="Ghost", email=None,
    title=None, department=None, avatar=None, end_date=None,
    working_hours=None, skills=[])


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, *, people=(PRIYA, NEHA, GHOST), agents=(), absences=()):
        self.people = list(people)
        self.agents = list(agents)
        self.absences = list(absences)
        self.statements: list[str] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = " ".join(str(sql).split())
        self.statements.append(s)
        if hits(s, "FROM people"):
            needle = (params or {}).get("q")
            rows = self.people
            if needle:
                clean = needle.strip("%").lower()
                rows = [r for r in rows
                        if clean in r.name.lower()
                        or clean in (r.email or "").lower()
                        or clean in (r.title or "").lower()]
            return _Result(rows)
        if "FROM people_absences" in s:
            return _Result(self.absences)
        if "FROM app_user" in s:
            # Priya has a login; the contractor does not.
            return _Result([SimpleNamespace(email="priya@fracktal.in")])
        if "FROM org_settings" in s:
            return _Result([])
        if "FROM pm_tasks" in s:
            return _Result([SimpleNamespace(open_tasks=4, mins=3000,
                                            unestimated=1)])
        if "FROM dynamic_agents" in s:
            return _Result(self.agents)
        return _Result([])

    async def commit(self) -> None:
        return None


def bind(monkeypatch, db: FakeDB) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db
        await db.commit()

    monkeypatch.setattr(people_core, "_tenant_session", _tenant_session,
                        raising=False)


def _user(email: str, *grants: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


MEMBER = _user("pm@fracktal.in", "feature:projects")
HR = _user("hr@fracktal.in", "feature:projects", "admin:members:read")


def test_directory_only_people_are_offered_and_say_so(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    out = run(picker.suggest_assignees(q="", due=None, user=MEMBER))
    by_name = {r.name: r for r in out.people}
    assert "Neha (contractor)" in by_name          # offered (D-PC-12)
    assert by_name["Neha (contractor)"].has_login is False
    assert by_name["Priya"].has_login is True


def test_a_row_with_no_email_is_skipped_not_offered_blank(monkeypatch) -> None:
    """An empty assignee string assigns work to nobody — worse than absence."""
    bind(monkeypatch, FakeDB())
    out = run(picker.suggest_assignees(q="", due=None, user=MEMBER))
    assert all(r.assignee for r in out.people)
    assert "Ghost" not in {r.name for r in out.people}


def test_the_hr_half_follows_the_grant(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    plain = run(picker.suggest_assignees(q="priya", due=None, user=MEMBER))
    assert plain.hr_visible is False
    assert plain.people[0].load is None
    assert plain.people[0].top_skills == []

    rich = run(picker.suggest_assignees(q="priya", due=None, user=HR))
    assert rich.hr_visible is True
    assert rich.people[0].load is not None
    assert rich.people[0].top_skills == ["firmware", "altium", "python"]
    assert rich.people[0].contracted_hours == 40.0


def test_overload_is_a_warning_for_the_hr_caller(monkeypatch) -> None:
    """50h committed against a 40h week — §6.1's 'at 140% load' line. Shown,
    never enforced: the row still comes back assignable."""
    bind(monkeypatch, FakeDB())
    out = run(picker.suggest_assignees(q="priya", due=None, user=HR))
    assert any("committed against" in w for w in out.people[0].warnings)


def test_engagement_end_sharpens_against_the_due_date(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    due = (TODAY + timedelta(days=20)).isoformat()
    out = run(picker.suggest_assignees(q="neha", due=due, user=MEMBER))
    warning = next(w for w in out.people[0].warnings
                   if w.startswith("Engagement ends"))
    assert "before this is due" in warning


def test_away_travels_at_directory_tier(monkeypatch) -> None:
    db = FakeDB(absences=[SimpleNamespace(
        person_id=PRIYA.id, kind="holiday",
        ends_on=TODAY + timedelta(days=3))])
    bind(monkeypatch, db)
    out = run(picker.suggest_assignees(q="priya", due=None, user=MEMBER))
    assert out.people[0].away is not None
    assert any(w.startswith("Away") for w in out.people[0].warnings)


def test_agents_share_the_picker_under_their_own_heading(monkeypatch) -> None:
    db = FakeDB(agents=[SimpleNamespace(name="triage",
                                        description="Sorts intake")])
    bind(monkeypatch, db)
    out = run(picker.suggest_assignees(q="", due=None, user=MEMBER))
    assert out.agents[0].assignee == "agent:triage"
    assert out.agents[0].kind == "agent"


def test_a_missing_agent_registry_is_no_agents_not_a_500(monkeypatch) -> None:
    db = FakeDB()

    original = FakeDB.execute

    async def _boom(self, sql, params=None):
        if "dynamic_agents" in str(sql):
            raise RuntimeError("relation does not exist")
        return await original(self, sql, params)

    db.execute = _boom.__get__(db)          # type: ignore[method-assign]
    bind(monkeypatch, db)
    out = run(picker.suggest_assignees(q="", due=None, user=MEMBER))
    assert out.agents == []
    assert len(out.people) >= 1             # the people half still answered


def test_the_picker_never_writes() -> None:
    """D-PC-13, structurally — prose stripped first, the D-PC-14 lesson."""
    from tests.unit.test_people_dashboard import _strip_prose

    code = _strip_prose(SOURCE)
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{verb}\b", code), verb


# ── The label lookup (owner request, 2026-09-21) ────────────────────
#
# `pm_tasks` stores an assignee as an ADDRESS, so a task read carries no name
# and every Projects surface fell back to the local part — or, in the task
# panel, printed the whole address. `priya` reads as a name; `p.sharma@x.com`
# does not.

def test_the_lookup_is_bounded():
    """A board shows a few dozen assignees. A caller asking for thousands is
    not labelling a screen, and one request that can read the whole directory
    is a denial-of-service surface rather than a feature — the same
    reasoning `MAX_BULK` and `MAX_PEOPLE` already carry."""
    from gateway.routes.projects.assignees import MAX_NAME_LOOKUP

    assert MAX_NAME_LOOKUP <= 500
    assert MAX_NAME_LOOKUP >= 50, "too small to label one board, which is the point"


def test_it_touches_no_database_when_there_is_nothing_to_resolve():
    """⚠️ Both of these are the common case, not an edge one.

    A board with no assignees asks for nothing, and a board whose only
    assignees are AGENTS asks for nothing either — an agent's name IS its
    identity, so there is no address to look up. Opening a tenant session to
    answer `{}` is waste on the busiest read the picker package has.
    """
    import asyncio

    from gateway.routes.projects.assignees import person_names

    # `user=None` would explode on any code path that reached the session.
    assert asyncio.run(person_names(emails="", user=None)).names == {}
    assert asyncio.run(person_names(emails="   ", user=None)).names == {}
    assert asyncio.run(
        person_names(emails="agent:builder,agent:researcher", user=None),
    ).names == {}


def test_an_unknown_address_is_ABSENT_rather_than_echoed():
    """The contract the client depends on, stated where it is decided.

    A task can carry somebody the directory has never had a row for — a
    former colleague, a typo, an address from an import. Mapping them to
    themselves would make "no name" and "named after their own address"
    indistinguishable, and only the client knows what to fall back to.
    """
    from gateway.routes.projects.assignees import NamesResponse

    # The model carries a plain mapping, so absence is expressible at all.
    assert NamesResponse(names={}).names == {}
    fields = NamesResponse.model_fields
    assert set(fields) == {"names"}, (
        "a second field here invites a client to read something other than "
        "the mapping, and the fallback lives in the client"
    )
