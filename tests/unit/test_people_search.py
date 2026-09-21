"""WS-28d — the capability search, and what keeps it out of the eval lock.

Spec: `project-docs/specs/people_center_app.md` §5.5 · D-PC-13.

Three claims:

* **The ranking is arithmetic with named weights, not a prompt.** §5.5
  EVAL-LOCKS the ranking prompt; this build ranks by constants every result
  shows, so there is no prompt to lock — and a structural fence keeps an LLM
  call from arriving in this module unnoticed.
* **The surface never assigns** (D-PC-13) — structurally: no INSERT, UPDATE or
  DELETE anywhere in the module.
* **The gate is the whole surface** (§4.2): without `admin:members:read` the
  search box would be an oracle for the very columns the projection hides.
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
from gateway.routes.people import search as people_search
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "apps/services/gateway/gateway/routes/people/search.py"
          ).read_text(encoding="utf-8")

THIS_YEAR = 2026


def run(coro):
    return asyncio.run(coro)


def srow(**over: Any) -> dict[str, Any]:
    return {"skill": "python", "level": None, "last_used_year": None,
            "evidence": "manual", **over}


# ══════════════════════════════════════════════════════════════════════════
# 1. The scorer — deterministic, shown, arguable
# ══════════════════════════════════════════════════════════════════════════

def test_a_level_weights_the_hit() -> None:
    expert, _ = people_search.score_skills(
        "extruder firmware in python", [srow(level="expert")], THIS_YEAR)
    plain, _ = people_search.score_skills(
        "extruder firmware in python", [srow()], THIS_YEAR)
    learning, _ = people_search.score_skills(
        "extruder firmware in python", [srow(level="learning")], THIS_YEAR)
    assert expert > plain > learning
    assert expert == 2.0 and plain == 1.0 and learning == 0.5


def test_staleness_decays_the_hit() -> None:
    fresh, _ = people_search.score_skills(
        "python", [srow(last_used_year=THIS_YEAR - 1)], THIS_YEAR)
    aging, _ = people_search.score_skills(
        "python", [srow(last_used_year=THIS_YEAR - 4)], THIS_YEAR)
    stale, _ = people_search.score_skills(
        "python", [srow(last_used_year=THIS_YEAR - 9)], THIS_YEAR)
    assert fresh > aging > stale


def test_unknown_recency_is_not_punished() -> None:
    """Most rows predate the column; decaying missing data would rank people
    by form-filling, not by capability."""
    unknown, _ = people_search.score_skills("python", [srow()], THIS_YEAR)
    fresh, _ = people_search.score_skills(
        "python", [srow(last_used_year=THIS_YEAR)], THIS_YEAR)
    assert unknown == fresh


def test_the_match_is_word_boundary_not_substring() -> None:
    # "c" must not match inside "cad" — the résumé parser's own rule, kept.
    score, _ = people_search.score_skills(
        "cad cleanup for the fixture", [srow(skill="c")], THIS_YEAR)
    assert score == 0.0


def test_every_matched_signal_carries_its_own_points() -> None:
    """The argument for the ranking, not just its output — a ranking whose
    reasoning is hidden cannot be argued with (§5.5)."""
    total, matched = people_search.score_skills(
        "python and altium", [srow(level="expert"),
                              srow(skill="altium", level="working")],
        THIS_YEAR)
    assert {m["skill"]: m["points"] for m in matched} == {
        "python": 2.0, "altium": 1.0}
    assert total == sum(m["points"] for m in matched)


def test_resume_quote_is_the_line_not_the_document() -> None:
    text = "Career\nBuilt extruder firmware for 4 years at Acme.\nEducation"
    quote = people_search.resume_quote(text, ["firmware"])
    assert quote == "Built extruder firmware for 4 years at Acme."


def test_resume_quote_is_capped() -> None:
    quote = people_search.resume_quote("firmware " + "x" * 500, ["firmware"])
    assert len(quote) <= 240


def test_no_match_no_quote() -> None:
    assert people_search.resume_quote("nothing relevant", ["firmware"]) is None


# ══════════════════════════════════════════════════════════════════════════
# 2. The endpoint
# ══════════════════════════════════════════════════════════════════════════

PRIYA = SimpleNamespace(
    id="11111111-1111-1111-1111-111111111111", name="Priya", title="Firmware",
    department="Engineering", avatar=None, timezone="Asia/Kolkata",
    domain="firmware", email="priya@fracktal.in", end_date=None,
    working_hours=None)
RAVI = SimpleNamespace(
    id="22222222-2222-2222-2222-222222222222", name="Ravi", title=None,
    department="Sales", avatar=None, timezone=None, domain=None,
    email="ravi@fracktal.in", end_date=None, working_hours=None)


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, *, skills=(), resumes=(), absences=()):
        self.skills = list(skills)
        self.resumes = list(resumes)
        self.absences = list(absences)
        self.statements: list[str] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = " ".join(str(sql).split())
        self.statements.append(s)
        if hits(s, "FROM people"):
            return _Result([PRIYA, RAVI])
        if "FROM people_skills" in s:
            return _Result(self.skills)
        if "FROM people_resumes" in s:
            return _Result(self.resumes)
        if "FROM people_absences" in s:
            return _Result(self.absences)
        if "FROM org_settings" in s:
            return _Result([])
        if "FROM pm_tasks" in s:
            return _Result([SimpleNamespace(open_tasks=3, mins=600,
                                            unestimated=1)])
        return _Result([])

    async def commit(self) -> None:
        return None


def bind(monkeypatch, db: FakeDB, semantic: dict | None = None) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db
        await db.commit()

    for module in (people_core, people_search):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)

    async def _scores(_db, _q):
        return dict(semantic or {})

    import gateway.routes.tasks.capability as capability
    monkeypatch.setattr(capability, "semantic_scores", _scores)


def _user(email: str, *grants: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


HR = _user("hr@fracktal.in", "feature:people", "admin:members:read")
COLLEAGUE = _user("someone@fracktal.in", "feature:people")


def test_the_whole_surface_is_gated(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    with pytest.raises(HTTPException) as exc:
        run(people_search.search_people("firmware", user=COLLEAGUE))
    assert exc.value.status_code == 403
    assert "admin:members:read" in exc.value.detail


def test_an_empty_query_is_a_400_not_a_full_roster(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    with pytest.raises(HTTPException) as exc:
        run(people_search.search_people("  ", user=HR))
    assert exc.value.status_code == 400


def test_results_name_their_signals(monkeypatch) -> None:
    db = FakeDB(
        skills=[SimpleNamespace(person_id=PRIYA.id, skill="firmware",
                                level="expert", years=6, last_used_year=2026,
                                evidence="manual")],
        resumes=[SimpleNamespace(person_id=PRIYA.id,
                                 parsed_text="Shipped extruder firmware.")])
    bind(monkeypatch, db, semantic={"priya": 0.8})
    out = run(people_search.search_people("extruder firmware", user=HR))
    assert out.total == 1
    row = out.rows[0]
    # §6.4: the assignee value travels so "Assign to…" can hand it on.
    assert row.email == "priya@fracktal.in"
    kinds = {s["kind"] for s in row.signals}
    # skill + domain (PRIYA.domain='firmware' appears in the query) + resume
    # + semantic — all four, each labelled.
    assert kinds == {"skill", "domain", "resume", "semantic"}
    assert row.score == sum(s["points"] for s in row.signals)
    quote = next(s for s in row.signals if s["kind"] == "resume")
    assert "extruder firmware" in quote["quote"]


def test_load_and_availability_travel_with_the_result(monkeypatch) -> None:
    """A perfect skill match at 45/40 hours, or away all week, is the wrong
    answer — and the READER decides that, not the ranker (§5.5)."""
    db = FakeDB(
        skills=[SimpleNamespace(person_id=PRIYA.id, skill="firmware",
                                level=None, years=None, last_used_year=None,
                                evidence="manual")],
        absences=[SimpleNamespace(kind="holiday", ends_on=date(2026, 8, 21))])
    bind(monkeypatch, db)
    row = run(people_search.search_people("firmware", user=HR)).rows[0]
    assert row.load is not None and row.load["open_tasks"] == 3
    assert row.away == {"kind": "holiday", "until": "2026-08-21"}
    assert any("Away" in w for w in row.warnings)


def test_no_semantic_is_reported_not_silent(monkeypatch) -> None:
    db = FakeDB(skills=[SimpleNamespace(
        person_id=PRIYA.id, skill="firmware", level=None, years=None,
        last_used_year=None, evidence="manual")])
    bind(monkeypatch, db, semantic={})
    out = run(people_search.search_people("firmware", user=HR))
    assert out.semantic_available is False
    assert out.total == 1                     # the deterministic signals carry


def test_nobody_matching_is_an_empty_list_not_the_roster(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    out = run(people_search.search_people("juggling", user=HR))
    assert out.total == 0


# ══════════════════════════════════════════════════════════════════════════
# 3. The fences
# ══════════════════════════════════════════════════════════════════════════

def test_the_surface_never_writes() -> None:
    """D-PC-13 structurally: a suggester with an UPDATE in it has become an
    assigner, and that is a management decision this system is not entitled
    to make.

    Prose is stripped first — the module's own docstring NAMES the forbidden
    verbs in order to explain the refusal, and a fence that punishes the
    explanation makes deleting the reasoning the cheapest way to go green.
    The same correction the D-PC-14 fence needed, reused rather than
    relearned.
    """
    from tests.unit.test_people_dashboard import _strip_prose

    code = _strip_prose(SOURCE)
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{verb}\b", code), verb


def test_no_llm_enters_the_ranking() -> None:
    """§5.5 EVAL-LOCKS the ranking prompt. This module ranks by arithmetic and
    contains no model call — so the lock is not touched. An LLM ranker, if one
    ever comes, arrives through the eval, and this fence makes sure it cannot
    arrive here quietly."""
    from tests.unit.test_people_dashboard import _strip_prose

    code = _strip_prose(SOURCE)
    for marker in ("acompletion", "acb_llm", "completion(", "messages="):
        assert marker not in code, marker


def test_the_weights_are_declared_not_inline() -> None:
    """Every constant the ranking uses has a NAME at module top — that is what
    makes the arithmetic arguable, and what a review can see change."""
    for name in ("LEVEL_WEIGHT", "DOMAIN_BONUS", "RESUME_BONUS",
                 "SEMANTIC_SCALE", "RECENCY_STALE"):
        assert name in SOURCE, name


def test_search_is_a_literal_path_before_the_person_pattern() -> None:
    """`/people/search` vs `/people/{person_id}` — the same shadow the
    dashboard fence generalised; asserted through the same fresh-process probe
    (`test_people_dashboard.test_no_literal_people_path_is_shadowed_by_the_pattern`),
    which covers every literal path including this one. This test pins only
    that the route exists, so a rename cannot silently drop the coverage."""
    from tests.unit.test_people_dashboard import _package_route_order

    routes = _package_route_order()
    assert ["/people/search", ["GET"]] in [list(r) for r in routes]
