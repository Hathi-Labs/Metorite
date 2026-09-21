"""WS-28m — skills coverage & data quality (§5.10).

Three claims:

* **One matcher.** "Declared but never used on a task" is decided by the
  capability ranker's own word boundary — asserted by IDENTITY, so a skill
  cannot count as matched by §5.5 and unused by §5.10 at once.
* **Defects in the record, never in people** (D-PC-14): every list is
  alphabetical, there is no score, and the module never writes (D-PC-13).
* **Nothing is silently partial**: caps travel as pre-cap totals, an empty
  task scan proves nothing, and a scoped scan says it was scoped.
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
from gateway.routes.people import dashboard as people_dashboard
from gateway.routes.people import quality as quality_mod
from gateway.routes.people import search as people_search
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "apps/services/gateway/gateway/routes/people/quality.py"
          ).read_text(encoding="utf-8")


def run(coro):
    return asyncio.run(coro)


def person(**over: Any) -> SimpleNamespace:
    base = dict(id="p1", name="Priya", title=None, status="active",
                email="priya@fracktal.in", email_conflict=None,
                manager_id="boss", timezone="Asia/Kolkata",
                working_hours={"start": "09:30"}, skills=["python"])
    base.update(over)
    return SimpleNamespace(**base)


BOSS = person(id="boss", name="Asha", manager_id=None)


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    def __init__(self, *, roster=(), skills=(), tasks=()):
        self.roster = list(roster) or [BOSS, person()]
        self.skills = list(skills)
        self.tasks = list(tasks)

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = " ".join(str(sql).split())
        if hits(s, "FROM people"):
            return _Result(self.roster)
        if "FROM people_skills" in s:
            return _Result(self.skills)
        if "FROM pm_tasks" in s:
            return _Result(self.tasks)
        return _Result([])

    async def commit(self) -> None:
        return None


def bind(monkeypatch, db: FakeDB, *, unrestricted: bool = True) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db

    monkeypatch.setattr(quality_mod, "_tenant_session", _tenant_session,
                        raising=False)

    async def _visibility(_db, _user):
        return SimpleNamespace(unrestricted=unrestricted, params={})

    monkeypatch.setattr(people_dashboard, "_visibility", _visibility)
    monkeypatch.setattr(people_dashboard, "_scope",
                        lambda vis, params, column="t.root_project_id": "TRUE")


def _user(*grants: str) -> UserContext:
    return UserContext(email="hr@fracktal.in", role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


HR = _user("feature:people", "admin:members:read", "feature:projects")
COLLEAGUE = _user("feature:people")


def skill_row(pid: str, skill: str) -> SimpleNamespace:
    return SimpleNamespace(person_id=pid, skill=skill)


# ══════════════════════════════════════════════════════════════════════════
# 1. The gate
# ══════════════════════════════════════════════════════════════════════════

def test_the_whole_surface_is_gated(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    with pytest.raises(HTTPException) as exc:
        run(quality_mod.get_quality(user=COLLEAGUE))
    assert exc.value.status_code == 403


# ══════════════════════════════════════════════════════════════════════════
# 2. Coverage
# ══════════════════════════════════════════════════════════════════════════

def test_bus_factor_of_one_names_the_skill_and_its_only_holder(monkeypatch) -> None:
    db = FakeDB(skills=[skill_row("p1", "Marlin"), skill_row("p1", "python"),
                        skill_row("boss", "python")])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    solo = {s.skill: s.person.name for s in out.coverage.single_holder}
    assert solo == {"Marlin": "Priya"}


def test_alumni_skills_do_not_count_as_coverage(monkeypatch) -> None:
    db = FakeDB(roster=[BOSS, person(), person(id="p9", name="Gone",
                                            status="alumni")],
                skills=[skill_row("p9", "cobol")])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    assert out.coverage.single_holder == []


def test_title_terms_drop_role_words_and_declared_skills() -> None:
    terms = quality_mod.title_terms(
        [("Priya", "Senior Firmware Engineer"), ("Ravi", "Firmware Intern")],
        declared={"python"})
    assert terms == [{"term": "firmware", "people": ["Priya", "Ravi"]}]


def test_a_declared_title_term_is_not_a_finding() -> None:
    assert quality_mod.title_terms(
        [("Priya", "Firmware Engineer")], declared={"firmware"}) == []


def test_unused_skills_use_the_ranker_boundary() -> None:
    # 'java' inside 'javascript' is NOT a use — the same boundary that stops
    # the ranker matching it; 'c++' with its punctuation is a use.
    out = quality_mod.unused_skills(
        {"java": 2, "c++": 1, "marlin": 1},
        "rewrite the javascript build\nport c++ driver")
    assert [u["skill"] for u in out] == ["java", "marlin"]


def test_one_matcher_by_identity() -> None:
    assert quality_mod.skill_pattern is people_search.skill_pattern


def test_an_empty_scan_proves_nothing(monkeypatch) -> None:
    db = FakeDB(skills=[skill_row("p1", "python")], tasks=[])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    assert out.coverage.tasks_scanned == 0
    assert out.coverage.scan_ran is True
    assert out.coverage.scan_error is False
    assert out.coverage.unused_skills == []


def test_a_failed_scan_says_so_not_no_visible_tasks(monkeypatch) -> None:
    """Adversarial-review finding: a broken query must not render as 'no
    visible tasks' forever with nothing logged anywhere."""

    class BrokenDB(FakeDB):
        async def execute(self, sql: Any, params: dict | None = None):
            if "FROM pm_tasks" in str(sql):
                raise RuntimeError("column does not exist")
            return await super().execute(sql, params)

    db = BrokenDB(skills=[skill_row("p1", "python")])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    assert out.coverage.scan_error is True
    assert out.coverage.scan_ran is False
    assert out.coverage.unused_skills == []


def test_array_only_skills_still_count_as_declared(monkeypatch) -> None:
    """Adversarial-review finding: the importer and every pre-176 write fill
    only `people.skills`; coverage over the child table alone asserted
    "nobody claims firmware" about a record whose array declares it."""
    db = FakeDB(roster=[BOSS, person(skills=["firmware"],
                                     title="Firmware Wizard")],
                skills=[])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    solo = {s.skill: s.person.name for s in out.coverage.single_holder}
    assert solo["firmware"] == "Priya"
    # …and the declared term is not reported as hired-for-but-unclaimed.
    assert all(t.term != "firmware" for t in out.coverage.title_terms)
    # …and the person is not flagged as missing skills.
    assert all("skills" not in r.missing
               for r in out.quality.missing_ai_fields)


def test_without_the_projects_feature_the_scan_does_not_run(monkeypatch) -> None:
    db = FakeDB(skills=[skill_row("p1", "python")],
                tasks=[SimpleNamespace(title="python fix")])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(
        user=_user("feature:people", "admin:members:read")))
    assert out.coverage.tasks_scanned == 0


def test_a_scoped_scan_says_so(monkeypatch) -> None:
    db = FakeDB(skills=[skill_row("p1", "python")],
                tasks=[SimpleNamespace(title="ship the python fix")])
    bind(monkeypatch, db, unrestricted=False)
    out = run(quality_mod.get_quality(user=HR))
    assert out.coverage.scope_partial is True
    assert out.coverage.unused_skills == []


# ══════════════════════════════════════════════════════════════════════════
# 3. Quality — the quarantine gets paid off
# ══════════════════════════════════════════════════════════════════════════

def test_the_148_quarantine_is_surfaced(monkeypatch) -> None:
    db = FakeDB(roster=[BOSS, person(email=None,
                                     email_conflict="priya@fracktal.in")])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    assert [c.email_conflict for c in out.quality.email_conflict] == [
        "priya@fracktal.in"]
    # Quarantined is not "missing": the address exists and a human must choose.
    assert out.quality.no_email == []


def test_no_email_lists_the_unreachable(monkeypatch) -> None:
    db = FakeDB(roster=[BOSS, person(email=None)])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    assert [p.name for p in out.quality.no_email] == ["Priya"]


def test_a_status_outside_the_vocabulary_is_listed(monkeypatch) -> None:
    db = FakeDB(roster=[BOSS, person(status="on sabbatical")])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    assert [(r.name, r.status) for r in out.quality.bad_status] == [
        ("Priya", "on sabbatical")]


def test_a_null_status_is_one_story_not_three(monkeypatch) -> None:
    """Adversarial-review finding: a NULL status (49 has no NOT NULL, 148's
    CHECK passes NULL) was counted as active by headcount, listed as “” by
    bad_status, and hidden from every other quality list at once."""
    db = FakeDB(roster=[BOSS, person(status=None, email=None)])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    assert [(r.name, r.status) for r in out.quality.bad_status] == [
        ("Priya", "(none)")]
    # Still treated as part of the working org, so the row's OTHER defects
    # keep surfacing rather than vanishing with the status.
    assert [p.name for p in out.quality.no_email] == ["Priya"]


def test_a_manager_who_left_is_listed(monkeypatch) -> None:
    db = FakeDB(roster=[SimpleNamespace(**{**BOSS.__dict__,
                                           "status": "alumni"}), person()])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    assert [(r.name, r.manager_name) for r in out.quality.manager_alumni] == [
        ("Priya", "Asha")]


def test_unmanaged_roots_are_listed_not_scored(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    out = run(quality_mod.get_quality(user=HR))
    assert [p.name for p in out.quality.no_manager] == ["Asha"]


def test_missing_ai_fields_name_what_is_missing(monkeypatch) -> None:
    db = FakeDB(roster=[BOSS, person(timezone=None, skills=[])])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    rows = {r.name: r.missing for r in out.quality.missing_ai_fields}
    assert rows == {"Priya": ["timezone", "skills"]}


# ══════════════════════════════════════════════════════════════════════════
# 4. Honesty of the panel itself
# ══════════════════════════════════════════════════════════════════════════

def test_caps_travel_as_pre_cap_totals(monkeypatch) -> None:
    many = [person(id=f"p{i}", name=f"P{i:03d}", email=None)
            for i in range(60)]
    bind(monkeypatch, FakeDB(roster=[BOSS] + many))
    out = run(quality_mod.get_quality(user=HR))
    assert out.counts["no_email"] == 60
    assert len(out.quality.no_email) == quality_mod.MAX_ROWS_PER_LIST
    assert out.truncated is True


def test_the_panel_never_writes() -> None:
    """D-PC-13 structurally, prose stripped — the reused D-PC-14 lesson."""
    from tests.unit.test_people_dashboard import _strip_prose

    code = _strip_prose(SOURCE)
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{verb}\b", code), verb


def test_no_ranking_of_people_in_the_quality_panel() -> None:
    """D-PC-14: a data-quality panel is one aggregation away from a 'worst
    profile' leaderboard, so the refusal is structural."""
    from tests.unit.test_people_dashboard import _PERFORMANCE, _strip_prose

    assert not _PERFORMANCE.findall(_strip_prose(SOURCE))


def test_every_list_is_alphabetical_not_scored(monkeypatch) -> None:
    db = FakeDB(roster=[BOSS, person(id="p2", name="Zara", email=None),
                        person(id="p3", name="Anil", email=None)])
    bind(monkeypatch, db)
    out = run(quality_mod.get_quality(user=HR))
    names = [p.name for p in out.quality.no_email]
    assert names == sorted(names)
