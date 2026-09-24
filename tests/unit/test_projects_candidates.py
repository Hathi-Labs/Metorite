"""WS-27bm S7b — fit: who should take one task.

Spec: ``project-docs/specs/projects_ai_chat.md`` §10.4 items 1 to 8, §13.4.

Two halves, as in ``test_projects_analytics_capacity.py``.

* **Hermetic** — the decisions: the match text, the window, rule 2's neutral
  ranking, the three warnings, the HR gate, the 422, and the source fence
  that there is one ranker. These never skip.
* **R8, on a real Postgres through asyncpg** — the pool (a person with no
  open task ranks, and the assignees and agents do not), a skill named only
  in a tag, the mixed list with no ``spare_hours`` on any candidate, and the
  draft route answering the task route's body.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway.routes.projects import candidates as route
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps/services/gateway/gateway"

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

TODAY = date(2026, 9, 23)
YEAR = 2026


def _helper(name: str, *, skill: str = "cad", spare: float = 10.0,
            basis: bool = True, **over: Any) -> dict[str, Any]:
    base = {
        "person_id": f"id-{name}", "name": name, "email": f"{name}@x.in",
        "skill_rows": [{"skill": skill, "level": None, "last_used_year": None}],
        "spare_hours": spare, "hours_basis": basis, "away": None,
        "spans": [], "end_date": None, "max_concurrent_tasks": None,
        "in_progress": 0,
    }
    base.update(over)
    return base


# ── Hermetic: one ranker, and no second one (item 1) ─────────────────────────


def _code(rel: str) -> str:
    return (GATEWAY / rel).read_text(encoding="utf-8")


def test_the_new_modules_rank_through_rank_candidates_and_never_score_skills() -> None:
    """§10.4 item 1. The ranker is the People suggester's, and neither new
    module scores a skill of its own."""
    candidates = _code("routes/projects/candidates.py")
    rebalance = _code("routes/projects/analytics_rebalance.py")
    assert re.search(r"from gateway\.routes\.people\.suggestions import rank_candidates",
                     candidates)
    assert "rank_candidates(" in candidates
    assert "rank_for_text" in rebalance, "rebalance ranks through its own ranker"
    from tests.unit.test_people_dashboard import _strip_prose

    for name, source in (("candidates.py", candidates), ("analytics_rebalance.py", rebalance)):
        # Prose stripped: a docstring may NAME the People ranker's skill half.
        # Code may not import it or call it.
        assert "score_skills" not in _strip_prose(source), f"{name} reaches score_skills"


def test_at_most_three_candidates_come_back() -> None:
    helpers = [_helper(f"h{i}", spare=float(i + 1)) for i in range(6)]
    ranked, note = route.rank_for_text("cad work", helpers, this_year=YEAR)
    assert note is None
    assert len(ranked) == 3
    assert [c.rank for c in ranked] == sorted((c.rank for c in ranked), reverse=True)


# ── Hermetic: the match text (item 2, rule 3) ────────────────────────────────


def test_the_match_text_is_title_tags_and_a_capped_description() -> None:
    body = "x" * 600 + " firmware"
    got = route.match_text("Fix the mount", ["CAD", " ", "print"], body)
    assert got.startswith("Fix the mount\nCAD\nprint\n")
    assert "firmware" not in got, "the description is capped at 500 characters"
    assert len(got.split("\n")[-1]) == route.DESCRIPTION_CHARS


def test_a_skill_named_only_in_a_tag_ranks_a_person() -> None:
    """§10.4 item 2, hermetic half. The title names no skill; the tag does."""
    text_ = route.match_text("Fix the mount", ["CAD"], None)
    ranked, _ = route.rank_for_text(text_, [_helper("cara")], this_year=YEAR)
    assert [c.name for c in ranked] == ["cara"]
    title_only, _ = route.rank_for_text("Fix the mount", [_helper("cara")], this_year=YEAR)
    assert title_only == []


# ── Hermetic: the window (item 4, rule 4) ────────────────────────────────────


@pytest.mark.parametrize(("due", "want"), [
    (None, (14, "default")),
    (TODAY + timedelta(days=5), (5, "due_date")),
    (TODAY, (1, "due_date")),
    (TODAY - timedelta(days=3), (1, "due_date")),
    (TODAY + timedelta(days=400), (90, "due_date")),
])
def test_the_window_runs_to_the_due_date_clamped(due, want) -> None:
    assert route.horizon_for(due, TODAY) == want


# ── Hermetic: rule 2, the whole list (item 5) ────────────────────────────────


def test_one_match_with_no_basis_makes_every_rank_skill_times_away() -> None:
    """§13.4 rule 2. Ana has 30 spare hours, Bo has no estimated work. With
    the real figure, Bo would rank 30 times below Ana from a number the
    reader cannot see. So both rank on the neutral 1, and a note says why."""
    helpers = [
        _helper("ana", spare=30.0),
        _helper("bo", spare=0.0, basis=False),
        _helper("cy", skill="sales", basis=False),   # no match, no effect
    ]
    ranked, note = route.rank_for_text("cad work", helpers, this_year=YEAR)
    assert note == route.HOURS_NOTE
    assert {c.name for c in ranked} == {"ana", "bo"}
    for c in ranked:
        assert c.rank == c.skill_points * 1.0, c.name


def test_a_person_with_no_basis_who_does_not_match_changes_nothing() -> None:
    helpers = [_helper("ana", spare=30.0), _helper("cy", skill="sales", basis=False)]
    ranked, note = route.rank_for_text("cad work", helpers, this_year=YEAR)
    assert note is None
    assert ranked[0].spare_hours == 30.0 and ranked[0].rank == 30.0


def test_the_ranker_itself_is_unchanged_by_this_slice() -> None:
    """``test_people_suggestions.py`` passes unchanged. This pins the call."""
    from gateway.routes.people import suggestions as sug

    [c] = sug.rank_candidates("cad work", [_helper("ana")], this_year=YEAR)
    assert (c.skill_points, c.spare_hours, c.rank) == (1.0, 10.0, 10.0)


# ── Hermetic: the three warnings (item 6, rule 5) ────────────────────────────


def test_away_on_the_due_date_is_a_warning() -> None:
    due = TODAY + timedelta(days=5)
    helper = _helper("ana", spans=[{"starts_on": due, "ends_on": due + timedelta(days=2),
                                    "kind": "leave", "hours_per_day": None}])
    [warning] = route.candidate_warnings(helper, due, today=TODAY)
    assert warning.startswith("Away (leave) on the due date")


def test_an_end_date_before_the_due_date_is_a_warning() -> None:
    due = TODAY + timedelta(days=10)
    helper = _helper("ana", end_date=TODAY + timedelta(days=4))
    [warning] = route.candidate_warnings(helper, due, today=TODAY)
    assert warning.startswith("Engagement ends")
    assert route.candidate_warnings(_helper("bo", end_date=due), due, today=TODAY) == []


def test_more_work_in_progress_than_the_ceiling_is_a_warning() -> None:
    helper = _helper("ana", max_concurrent_tasks=2, in_progress=3)
    [warning] = route.candidate_warnings(helper, None)
    assert warning == "3 tasks in progress, over the limit of 2"
    assert route.candidate_warnings(_helper("bo", max_concurrent_tasks=2, in_progress=2), None) == []


# ── Hermetic: the HR gate (item 7, rule 1) and the 422 (item 8) ─────────────


class _RecordingDB:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, sql: Any, params: dict | None = None) -> Any:
        self.statements.append(str(sql))
        raise AssertionError("an HR-less caller ran a statement")


def test_without_the_grant_the_candidates_key_is_ABSENT_and_nothing_runs() -> None:
    db = _RecordingDB()
    body = asyncio.run(route.candidates_body(
        db, SimpleNamespace(unrestricted=True), hr_visible=False,
        title="cad work", tags=[], description=None,
        due_on=TODAY + timedelta(days=3), exclude=set(), today=TODAY,
    ))
    assert body["hr_visible"] is False
    assert "candidates" not in body
    assert body["window"] == {"starts_on": "2026-09-23", "ends_on": "2026-09-26",
                              "days": 3, "basis": "due_date"}
    assert db.statements == []


@pytest.mark.parametrize("title", ["", " ", "x", " y "])
def test_a_draft_title_under_two_characters_is_422_before_a_session(
    title: str, monkeypatch,
) -> None:
    def _session(*_a, **_k):
        raise AssertionError("a session opened for a refused draft")

    monkeypatch.setattr(route, "_tenant_session", _session)
    with pytest.raises(HTTPException) as err:
        asyncio.run(route.draft_candidates(title=title, user=SimpleNamespace()))
    assert err.value.status_code == 422


def test_a_draft_due_that_is_not_a_date_is_422(monkeypatch) -> None:
    monkeypatch.setattr(route, "_tenant_session", lambda *_a, **_k: None)
    with pytest.raises(HTTPException) as err:
        asyncio.run(route.draft_candidates(title="cad work", due="soon",
                                           user=SimpleNamespace()))
    assert err.value.status_code == 422


def test_both_routes_are_mounted() -> None:
    from gateway.routes.projects import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert {"/projects/tasks/{task_id}/candidates", "/projects/candidates"} <= paths


def test_the_candidates_module_never_writes() -> None:
    from tests.unit.test_people_dashboard import _strip_prose

    code = _strip_prose(_code("routes/projects/candidates.py"))
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{verb}\b", code), verb


# ── R8: a real Postgres, through asyncpg ─────────────────────────────────────


def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture(scope="module")
def _ladder():
    """Apply the tenant ladder ONCE for this module (the S7a lesson: each
    replay spends columns, and per-test replays reached Postgres's 1600)."""
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def seeded(_ladder):
    """One project, four people, and three tasks.

    Skill names carry a per-run tag, so people other suites leave in the
    tenant never match. ``cad`` below is ``cad<tag>``.

    * ``cara`` — active, knows cad and weld, holds NO open task.
    * ``dan`` — active, knows cad, and is an assignee of ``mount``.
    * ``eve`` — active, knows cad, holds an open task with no estimate, so
      her ``hours_basis`` is false.
    * ``fay`` — ALUMNI, knows cad. Never in the pool.

    ``mount`` is titled "Fix the mount" and tagged cad (the title names no
    skill), due in 7 days, assigned to dan and to an agent. ``frame`` is
    "Weld the frame", tagged weld, due in 10 days, with no assignee.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {"tag": tag, "cad": f"cad{tag}", "weld": f"weld{tag}"}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        made["project"] = pid = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','fit@example.test',"
                " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
            ),
            {"n": f"fit-{tag}", "o": org},
        ).scalar_one())
        sid = str(c.execute(
            text(
                "INSERT INTO pm_task_statuses (project_id,name,color,position,"
                " category) VALUES (CAST(:p AS uuid),'To do','gray',0,'todo')"
                " RETURNING id"
            ),
            {"p": pid},
        ).scalar_one())

        def person(key: str, status: str = "active") -> str:
            email = f"{key}-{tag}@example.test"
            made[key] = email
            made[f"{key}_id"] = str(c.execute(
                text(
                    "INSERT INTO people (id, name, email, status, skills, source,"
                    " source_key, organization_id, updated_by, updated_at)"
                    " VALUES (gen_random_uuid(), :n, :e, :s, ARRAY[]::text[],"
                    " 'manual', :k, CAST(:o AS uuid), 'test', now()) RETURNING id"
                ),
                {"n": f"{key.title()} {tag}", "e": email, "s": status,
                 "k": f"manual:fit-{key}-{tag}", "o": org},
            ).scalar_one())
            return made[f"{key}_id"]

        def skill(person_id: str, name: str) -> None:
            c.execute(
                text(
                    "INSERT INTO people_skills (organization_id, person_id, skill,"
                    " level) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :s, 'expert')"
                ),
                {"o": org, "p": person_id, "s": name},
            )

        def task(title: str, *, tags: list[str], who: tuple[str, ...] = (),
                 est: int | None = None, due_days: int | None = None) -> str:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, due_at, tags)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 'fit@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1, :est,"
                    " CAST(:due AS timestamptz), CAST(:tags AS text[])"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": title, "p": pid, "s": sid, "o": org, "est": est,
                 "due": _noon_utc(due_days), "tags": tags},
            ).scalar_one())
            for who_ in who:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a,"
                        " 'fit@example.test')"
                    ),
                    {"t": tid, "a": who_},
                )
            return tid

        cara = person("cara")
        skill(cara, made["cad"])
        skill(cara, made["weld"])
        skill(person("dan"), made["cad"])
        skill(person("eve"), made["cad"])
        skill(person("fay", status="alumni"), made["cad"])
        made["mount"] = task("Fix the mount", tags=[made["cad"]],
                             who=(made["dan"], "agent:fit-bot"), est=60, due_days=7)
        made["frame"] = task("Weld the frame", tags=[made["weld"]], due_days=10)
        task("Eve's unsized work", tags=[], who=(made["eve"],))
    yield made
    with eng.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM people WHERE id = ANY(CAST(:i AS uuid[]))"),
                  {"i": [made[f"{k}_id"] for k in ("cara", "dan", "eve", "fay")]})
    eng.dispose()


def _noon_utc(days: int | None) -> str | None:
    """A due date ``days`` after the route's own ``date.today()``, at noon UTC.

    ⚠️ Counted from the PROCESS's date, never from the database's ``now()``.
    The route reads ``date.today()``, and near midnight the local date and
    the UTC date differ by one. A fixture built on ``now()`` then disagreed
    with the route by one day (measured 2026-09-24, just after midnight IST).
    """
    if days is None:
        return None
    return f"{(date.today() + timedelta(days=days)).isoformat()}T12:00:00+00:00"


def _user(hr: bool) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    grants = ["feature:projects"] + (["admin:members:read"] if hr else [])
    return UserContext(email="fit@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(grants))


async def _via_routes(seeded: dict[str, Any], monkeypatch, *, hr: bool = True,
                      **draft: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The task route for ``mount`` (or ``frame``), and the draft route if
    asked, on one real database with the route's own session seam patched."""
    from contextlib import asynccontextmanager

    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a, **_k):
        async with eng.connect() as conn:
            yield conn

    async def _visibility(_db, _user_):
        return Visibility(unrestricted=True, email="", groups=(),
                          organization_id=seeded["org"])

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_visibility", _visibility)
    try:
        task_id = draft.pop("task_id", seeded["mount"])
        body = await route.task_candidates(task_id, user=_user(hr))
        other = await route.draft_candidates(user=_user(hr), **draft) if draft else None
    finally:
        await eng.dispose()
    return body, other


def _names(body: dict[str, Any]) -> list[str]:
    return [c["email"] for c in body.get("candidates") or []]


@_needs_db
async def test_the_pool_ranks_a_person_with_no_open_task_and_never_an_assignee(
    seeded, monkeypatch,
) -> None:
    """§10.4 item 3. cara holds nothing and ranks. dan and the agent hold the
    task and do not. fay is alumni and is not in the pool."""
    body, _ = await _via_routes(seeded, monkeypatch)
    emails = _names(body)
    assert seeded["cara"] in emails
    assert seeded["dan"] not in emails
    assert seeded["fay"] not in emails
    assert not any(e.startswith("agent:") for e in emails)
    assert len(emails) <= 3


@_needs_db
async def test_a_skill_named_only_in_a_tag_ranks_a_person_on_a_real_task(
    seeded, monkeypatch,
) -> None:
    """§10.4 item 2. "Fix the mount" names no skill; the tag does."""
    body, _ = await _via_routes(seeded, monkeypatch)
    cara = next(c for c in body["candidates"] if c["email"] == seeded["cara"])
    assert cara["matched_skills"] == [seeded["cad"]]


@_needs_db
async def test_a_mixed_list_carries_no_spare_hours_on_ANY_candidate(
    seeded, monkeypatch,
) -> None:
    """§10.4 item 5. cara has spare hours and eve has no estimated work, and
    both match. So the key is absent on every candidate, not zero."""
    body, _ = await _via_routes(seeded, monkeypatch)
    emails = _names(body)
    assert {seeded["cara"], seeded["eve"]} <= set(emails)
    for c in body["candidates"]:
        assert "spare_hours" not in c, c["email"]
        assert c["rank"] == c["skill_points"], c["email"]
    assert body["hours_basis"] is False
    assert body["hours_note"] == route.HOURS_NOTE


@_needs_db
async def test_the_window_is_printed_from_the_due_date(seeded, monkeypatch) -> None:
    """§10.4 item 4."""
    body, _ = await _via_routes(seeded, monkeypatch)
    assert body["window"]["basis"] == "due_date"
    assert body["window"]["days"] == 7
    assert body["window"]["starts_on"] == date.today().isoformat()
    assert body["due_on"] == (date.today() + timedelta(days=7)).isoformat()


@_needs_db
async def test_a_list_with_hours_shows_them(seeded, monkeypatch) -> None:
    """Only cara knows weld, and she has the hours basis, so her spare
    hours travel and the rank uses them."""
    body, _ = await _via_routes(seeded, monkeypatch, task_id=seeded["frame"])
    [cara] = body["candidates"]
    assert cara["email"] == seeded["cara"]
    assert cara["spare_hours"] > 0
    assert cara["rank"] == round(cara["skill_points"] * cara["spare_hours"], 2)
    assert body["hours_basis"] is True and "hours_note" not in body


@_needs_db
async def test_the_draft_route_answers_the_task_routes_body(seeded, monkeypatch) -> None:
    """§10.4 item 8. ``frame`` has no assignee, because the task route leaves
    its assignees out of the pool and the draft has none to leave out."""
    due = (date.today() + timedelta(days=10)).isoformat()
    body, draft = await _via_routes(
        seeded, monkeypatch, task_id=seeded["frame"],
        title="Weld the frame", tags=seeded["weld"], due=due,
    )
    assert draft == body
    assert body["candidates"], "an empty pair would agree about nothing"


@_needs_db
async def test_without_the_grant_the_real_route_carries_no_candidates(
    seeded, monkeypatch,
) -> None:
    """§10.4 item 7, end to end: 200, hr_visible false, the key absent."""
    body, draft = await _via_routes(
        seeded, monkeypatch, hr=False, title="Weld the frame",
    )
    for answer in (body, draft):
        assert answer["hr_visible"] is False
        assert "candidates" not in answer


# ── Review round 1 ───────────────────────────────────────────────────────────


def test_an_overdue_task_checks_availability_today_not_on_the_past_due_date(
    monkeypatch,
) -> None:
    """P1. The task was due three days ago. Ana is on leave from today, so she
    cannot take it now. The old code checked the PAST due date, found no
    absence, and in note mode (neutral spare 1) ranked her first."""
    overdue = TODAY - timedelta(days=3)
    leave = [{"starts_on": TODAY, "ends_on": TODAY + timedelta(days=5),
              "kind": "leave", "hours_per_day": None}]
    helpers = [
        {**_helper("ana", basis=False, spans=leave), "email": "ana@x.in",
         "skill_rows": [{"skill": "cad", "level": "expert", "last_used_year": None}],
         "in_pool": True},
        {**_helper("bo", basis=False), "in_pool": True},
    ]

    async def _pool(*_a, **_k):
        return [dict(h) for h in helpers]

    monkeypatch.setattr(route, "pool_capacity", _pool)
    body = asyncio.run(route.candidates_body(
        object(), SimpleNamespace(unrestricted=True), hr_visible=True,
        title="cad work", tags=[], description=None, due_on=overdue,
        exclude=set(), today=TODAY,
    ))
    assert body["hours_note"] == route.HOURS_NOTE
    ana = next(c for c in body["candidates"] if c["email"] == "ana@x.in")
    bo = next(c for c in body["candidates"] if c["email"] == "bo@x.in")
    assert ana["away"] is not None, "the away factor was not applied"
    assert ana["rank"] < bo["rank"], "a person on leave today ranked first"
    assert any(w.startswith("Away (leave)") for w in ana["warnings"]), ana["warnings"]


def test_the_warning_for_an_overdue_task_checks_today() -> None:
    leave = [{"starts_on": TODAY, "ends_on": TODAY + timedelta(days=2),
              "kind": "leave", "hours_per_day": None}]
    [warning] = route.candidate_warnings(_helper("ana", spans=leave),
                                         TODAY - timedelta(days=3), today=TODAY)
    assert warning.startswith("Away (leave) today")


def test_the_draft_route_without_the_grant_carries_no_candidates(monkeypatch) -> None:
    """R7 fence. A caller without admin:members:read, through the ROUTE
    function, so a hard-coded `hr_visible=True` fails here."""
    from contextlib import asynccontextmanager

    from acb_auth import UserContext, UserRole, build_access

    @asynccontextmanager
    async def _session(*_a, **_k):
        yield _RecordingDB()

    async def _vis(_db, _user):
        return SimpleNamespace(unrestricted=True)

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_visibility", _vis)
    user = UserContext(email="x@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))
    body = asyncio.run(route.draft_candidates(title="cad work", user=user))
    assert body["hr_visible"] is False
    assert "candidates" not in body
