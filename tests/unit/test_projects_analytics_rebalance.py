"""WS-27bm S7b — rebalancing: who could help whom, in one scope.

Spec: ``project-docs/specs/projects_ai_chat.md`` §10.4 item 9, §13.4 rule 6.

* **Hermetic** — the one join both suggesters call, the People suggester's
  answer pinned on a hand-built board, the HR gate and the horizon.
* **R8, on a real Postgres through asyncpg** — no task outside the viewer's
  grant appears, an idle person with no work in scope appears, and the
  at-risk list keeps to the scope.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException
from gateway import capacity as cap
from gateway.routes.projects import analytics_rebalance as route
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

YEAR = 2026


# ── Hermetic: one join, two callers (rule 6) ─────────────────────────────────


def test_both_suggester_routes_call_the_one_leaf_join() -> None:
    """§10.4 item 9: "Both suggester routes call the one leaf join"."""
    for rel in ("routes/people/suggestions.py", "routes/projects/analytics_rebalance.py"):
        source = (GATEWAY / rel).read_text(encoding="utf-8")
        assert "rebalance_join(" in source, f"{rel} does not call the leaf join"
    people = (GATEWAY / "routes/people/suggestions.py").read_text(encoding="utf-8")
    # The two loops the join replaced are gone from the People route.
    assert "for suggestion in at_risk:" not in people
    assert "options.sort(" not in people


def _helper(name: str, skill: str, spare: float = 10.0) -> dict[str, Any]:
    return {
        "person_id": f"id-{name}", "name": name, "email": f"{name}@x.in",
        "skill_rows": [{"skill": skill, "level": None, "last_used_year": None}],
        "spare_hours": spare, "away": None,
    }


def _plain_rank(text_: str, pool: list[dict[str, Any]], holder: str):
    from gateway.routes.people.suggestions import rank_candidates

    return rank_candidates(text_, pool, this_year=YEAR, exclude_email=holder), None


def _risk(task_id: str, title: str, holder: str, **over: Any) -> dict[str, Any]:
    row = {
        "task_id": task_id, "title": title, "project_name": "Ops",
        "due_on": "2026-09-30", "shortfall_hours": 4.0,
        "holder": {"person_id": f"id-{holder}", "name": holder, "email": f"{holder}@x.in"},
    }
    row.update(over)
    return row


def test_the_join_never_offers_the_holder_as_their_own_helper() -> None:
    joined = cap.rebalance_join(
        at_risk=[_risk("t1", "firmware fix", "ana")],
        helpers=[_helper("ana", "firmware"), _helper("bo", "firmware")],
        idle=[], unassigned=[], this_year=YEAR, rank=_plain_rank,
        max_at_risk=8, max_pickups=4,
    )
    [only] = joined["at_risk"]
    assert [c.email for c in only["candidates"]] == ["bo@x.in"]
    assert only["hours_note"] is None


def test_the_join_reads_the_match_text_when_a_row_carries_one() -> None:
    joined = cap.rebalance_join(
        at_risk=[_risk("t1", "Fix the mount", "ana", match_text="Fix the mount\ncad")],
        helpers=[_helper("bo", "cad")], idle=[], unassigned=[],
        this_year=YEAR, rank=_plain_rank, max_at_risk=8, max_pickups=4,
    )
    assert [c.email for c in joined["at_risk"][0]["candidates"]] == ["bo@x.in"]


def test_an_idle_persons_options_are_unassigned_work_then_help() -> None:
    """The idle-to-behind join. Bo matches one unassigned task and is a
    helper on ana's at-risk task. The options sort by skill points, stably,
    and the cap holds."""
    idle = [{"person_id": "id-bo", "name": "bo", "email": "BO@x.in",
             "skill_rows": [{"skill": "firmware", "level": None, "last_used_year": None}]}]
    joined = cap.rebalance_join(
        at_risk=[_risk("t1", "firmware fix", "ana")],
        helpers=[_helper("bo", "firmware")],
        idle=idle,
        unassigned=[
            {"task_id": "u1", "title": "firmware update", "project_name": None},
            {"task_id": "u2", "title": "sales deck", "project_name": None},
        ],
        this_year=YEAR, rank=_plain_rank, max_at_risk=8, max_pickups=4,
    )
    [bo] = joined["pickups"]
    assert bo["email"] == "bo@x.in"
    assert [(t["task_id"], t["kind"]) for t in bo["tasks"]] == [
        ("u1", "unassigned"), ("t1", "at_risk_help"),
    ]
    assert bo["tasks"][1]["holder"] == "ana"
    capped = cap.rebalance_join(
        at_risk=[_risk("t1", "firmware fix", "ana")], helpers=[_helper("bo", "firmware")],
        idle=idle, unassigned=[{"task_id": "u1", "title": "firmware", "project_name": None}],
        this_year=YEAR, rank=_plain_rank, max_at_risk=8, max_pickups=1,
    )
    assert len(capped["pickups"][0]["tasks"]) == 1


def test_the_join_caps_the_at_risk_list_and_counts_all_of_it() -> None:
    risky = [_risk(f"t{i}", "firmware", "ana") for i in range(5)]
    joined = cap.rebalance_join(
        at_risk=risky, helpers=[], idle=[], unassigned=[], this_year=YEAR,
        rank=_plain_rank, max_at_risk=2, max_pickups=4,
    )
    assert len(joined["at_risk"]) == 2
    assert joined["total_at_risk"] == 5


# ── Hermetic: the People suggester's answer, pinned ──────────────────────────


def test_the_people_suggester_answer_is_unchanged_on_a_known_board(monkeypatch) -> None:
    """The join moved out of ``suggestions.py``. This board's answer was worked
    out by hand from the pre-S7b code, and the route must still give it.

    Ana holds a firmware task at risk. Bo (idle, firmware, 8 spare hours) and
    Cy (firmware, away, 20 spare hours) can help. Bo also matches one
    unassigned task. Dee is idle and matches nothing, so she has no pickup.
    """
    from gateway.routes.people import dashboard
    from gateway.routes.people import suggestions as sug

    def row(name: str, **over: Any) -> SimpleNamespace:
        base = dict(kind="person", email=f"{name}@x.in", person_id=f"id-{name}",
                    name=name.title(), spare_hours_horizon=0.0, away=None,
                    at_risk=[], pill="on_track")
        base.update(over)
        return SimpleNamespace(**base)

    board = SimpleNamespace(
        rows=[
            row("ana", pill="at_risk", at_risk=[{
                "task_id": "t1", "title": "Extruder firmware", "project_name": "R&D",
                "due_on": "2026-09-30", "shortfall_hours": 6.0}]),
            row("bo", pill="idle", spare_hours_horizon=8.0),
            row("cy", spare_hours_horizon=20.0, away={"kind": "leave", "until": "2026-09-25"}),
            row("dee", pill="idle", spare_hours_horizon=30.0),
        ],
        work_visible=True, partial=False,
    )
    skills = [
        SimpleNamespace(person_id="id-bo", skill="firmware", level=None, last_used_year=None),
        SimpleNamespace(person_id="id-cy", skill="firmware", level="expert", last_used_year=None),
        SimpleNamespace(person_id="id-dee", skill="sales", level=None, last_used_year=None),
    ]
    unassigned = [SimpleNamespace(id="u1", title="Firmware update", project_name="Ops",
                                  due_at=None, estimate_mins=None)]

    class _R:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return list(self._rows)

    class _DB:
        async def execute(self, sql, params=None):
            return _R(skills if "people_skills" in str(sql) else unassigned)

    @asynccontextmanager
    async def _session():
        yield _DB()

    async def _board(_user):
        return board

    async def _vis(_db, _user):
        return None

    monkeypatch.setattr(sug, "_tenant_session", _session)
    monkeypatch.setattr(dashboard, "get_dashboard", _board)
    monkeypatch.setattr(dashboard, "_visibility", _vis)
    monkeypatch.setattr(dashboard, "_scope", lambda _v, _p: "TRUE")
    out = asyncio.run(sug.get_suggestions(SimpleNamespace())).model_dump()

    assert out == {
        "at_risk": [{
            "task_id": "t1", "title": "Extruder firmware", "project_name": "R&D",
            "due_on": "2026-09-30", "shortfall_hours": 6.0,
            "holder": {"person_id": "id-ana", "name": "Ana", "email": "ana@x.in"},
            "candidates": [
                # Bo: 1.0 x 8 x 1 = 8. Cy: 2.0 (expert) x 20 x 0.25 (away) = 10.
                {"person_id": "id-cy", "name": "Cy", "email": "cy@x.in",
                 "skill_points": 2.0, "matched_skills": ["firmware"], "spare_hours": 20.0,
                 "away": {"kind": "leave", "until": "2026-09-25"}, "rank": 10.0},
                {"person_id": "id-bo", "name": "Bo", "email": "bo@x.in",
                 "skill_points": 1.0, "matched_skills": ["firmware"], "spare_hours": 8.0,
                 "away": None, "rank": 8.0},
            ],
        }],
        "pickups": [{
            "person_id": "id-bo", "name": "Bo", "email": "bo@x.in",
            "tasks": [
                {"task_id": "u1", "title": "Firmware update", "project_name": "Ops",
                 "kind": "unassigned", "skill_points": 1.0, "matched_skills": ["firmware"]},
                {"task_id": "t1", "title": "Extruder firmware", "project_name": "R&D",
                 "kind": "at_risk_help", "skill_points": 1.0,
                 "matched_skills": ["firmware"], "holder": "Ana"},
            ],
        }],
        "truncated": False,
        "partial": False,
    }


# ── Hermetic: the HR gate and the horizon ────────────────────────────────────


class _RefusingDB:
    async def execute(self, *_a, **_k):
        raise AssertionError("an HR-less caller ran a statement")


def test_without_the_grant_there_is_no_at_risk_and_no_pickups_key() -> None:
    body = asyncio.run(route.rebalance_body(
        _RefusingDB(), SimpleNamespace(unrestricted=True), hr_visible=False,
        project_id=None, include_subtree=True, horizon_days=14,
    ))
    assert body["hr_visible"] is False
    assert "at_risk" not in body and "pickups" not in body
    assert body["window"]["days"] == 14


@pytest.mark.parametrize("bad", [0, 91, "x"])
def test_a_bad_horizon_is_refused_before_a_session(bad, monkeypatch) -> None:
    def _session(*_a, **_k):
        raise AssertionError("a session opened for a refused horizon")

    monkeypatch.setattr(route, "_tenant_session", _session)
    with pytest.raises(HTTPException) as err:
        asyncio.run(route.rebalance(horizon_days=bad, user=SimpleNamespace()))
    assert err.value.status_code == 422


def test_the_route_is_mounted() -> None:
    from gateway.routes.projects import router

    assert "/projects/analytics/rebalance" in {getattr(r, "path", "") for r in router.routes}


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
    """Two projects: the viewer holds a grant on OPEN and none on SHUT.

    ``weld`` below is ``weld<tag>``, so people other suites leave behind
    never match.

    * ``hal`` holds a 50-hour weld task in OPEN, due in two days — at risk.
    * ``jon`` holds a 50-hour weld task in SHUT, due in two days — at risk,
      but the viewer cannot see it.
    * ``ivy`` knows weld and holds nothing — idle, with no work in scope.
    * One unassigned weld task in OPEN, one in SHUT.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {"weld": f"weld{tag}", "viewer": f"viewer-{tag}@example.test"}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        for key in ("open", "shut"):
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id, owns_statuses)"
                    " VALUES (:n,'active','manual','rb@example.test',"
                    " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
                ),
                {"n": f"rb-{key}-{tag}", "o": org},
            ).scalar_one())
            made[key] = pid
            made[f"{key}:status"] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id,name,color,position,"
                    " category) VALUES (CAST(:p AS uuid),'To do','gray',0,'todo')"
                    " RETURNING id"
                ),
                {"p": pid},
            ).scalar_one())
        c.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s, 'rb@example.test',"
                " CAST(:o AS uuid))"
            ),
            {"p": made["open"], "s": made["viewer"], "o": org},
        )

        def person(key: str, *skills: str) -> None:
            made[key] = f"{key}-{tag}@example.test"
            pid = str(c.execute(
                text(
                    "INSERT INTO people (id, name, email, status, skills, source,"
                    " source_key, organization_id, updated_by, updated_at)"
                    " VALUES (gen_random_uuid(), :n, :e, 'active', ARRAY[]::text[],"
                    " 'manual', :k, CAST(:o AS uuid), 'test', now()) RETURNING id"
                ),
                {"n": f"{key.title()} {tag}", "e": made[key],
                 "k": f"manual:rb-{key}-{tag}", "o": org},
            ).scalar_one())
            made[f"{key}_id"] = pid
            for s in skills:
                c.execute(
                    text(
                        "INSERT INTO people_skills (organization_id, person_id,"
                        " skill, level) VALUES (CAST(:o AS uuid), CAST(:p AS uuid),"
                        " :s, 'expert')"
                    ),
                    {"o": org, "p": pid, "s": s},
                )

        def task(project: str, title: str, *, who: str | None = None,
                 est: int | None = None, due_days: int | None = None) -> str:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, due_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 'rb@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1, :est,"
                    " CASE WHEN CAST(:d AS int) IS NULL THEN NULL"
                    "      ELSE now() + make_interval(days => CAST(:d AS int)) END"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": title, "p": made[project], "s": made[f"{project}:status"],
                 "o": org, "est": est, "d": due_days},
            ).scalar_one())
            if who:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a,"
                        " 'rb@example.test')"
                    ),
                    {"t": tid, "a": who},
                )
            return tid

        person("hal", made["weld"])
        person("jon", made["weld"])
        person("ivy", made["weld"])
        weld = made["weld"]
        made["open_risk"] = task("open", f"{weld} the gantry", who=made["hal"],
                                 est=3000, due_days=2)
        made["shut_risk"] = task("shut", f"{weld} the secret rig", who=made["jon"],
                                 est=3000, due_days=2)
        made["open_free"] = task("open", f"{weld} a jig")
        made["shut_free"] = task("shut", f"{weld} a hidden jig")
    yield made
    with eng.begin() as c:
        ids = [made["open"], made["shut"]]
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_projects WHERE id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM people WHERE id = ANY(CAST(:i AS uuid[]))"),
                  {"i": [made[f"{k}_id"] for k in ("hal", "jon", "ivy")]})
    eng.dispose()


async def _body(seeded: dict[str, Any], *, restricted: bool,
                project_id: str | None = None) -> dict[str, Any]:
    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    vis = (
        Visibility(unrestricted=False, email=seeded["viewer"], groups=(),
                   organization_id=seeded["org"])
        if restricted
        else Visibility(unrestricted=True, email="", groups=(),
                        organization_id=seeded["org"])
    )
    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        async with eng.connect() as db:
            return await route.rebalance_body(
                db, vis, hr_visible=True, project_id=project_id,
                include_subtree=True, horizon_days=14,
            )
    finally:
        await eng.dispose()


def _task_ids(body: dict[str, Any]) -> set[str]:
    ids = {t["task_id"] for t in body["at_risk"]}
    for person in body["pickups"]:
        ids |= {t["task_id"] for t in person["tasks"]}
    return ids


@_needs_db
async def test_no_task_outside_the_viewers_grant_appears(seeded) -> None:
    """§10.4 item 9, on a real database. The viewer holds a grant on OPEN
    only. The SHUT project's at-risk task and its unassigned task must not
    appear anywhere in the answer."""
    body = await _body(seeded, restricted=True)
    ids = _task_ids(body)
    assert seeded["open_risk"] in ids
    assert seeded["open_free"] in ids
    assert seeded["shut_risk"] not in ids
    assert seeded["shut_free"] not in ids
    assert body["partial"] is True


@_needs_db
async def test_an_unrestricted_viewer_does_see_both_so_the_test_can_fail(seeded) -> None:
    """The control. With the whole tenant in view the SHUT tasks appear, so
    the grant test above measures the grant and not an empty fixture."""
    body = await _body(seeded, restricted=False)
    ids = _task_ids(body)
    assert {seeded["open_risk"], seeded["shut_risk"], seeded["shut_free"]} <= ids


@_needs_db
async def test_an_idle_person_with_no_work_in_scope_appears(seeded) -> None:
    """ivy holds nothing anywhere, so the scope's own rows would never name
    her. She comes from the pool, is idle, and picks up OPEN's jig."""
    body = await _body(seeded, restricted=True, project_id=seeded["open"])
    ivy = next(p for p in body["pickups"] if p["email"] == seeded["ivy"])
    assert seeded["open_free"] in {t["task_id"] for t in ivy["tasks"]}
    [risk] = [t for t in body["at_risk"] if t["task_id"] == seeded["open_risk"]]
    helpers = {c["email"] for c in risk["candidates"]}
    assert seeded["ivy"] in helpers
    assert seeded["hal"] not in helpers, "the holder helped themselves"


@_needs_db
async def test_the_at_risk_list_keeps_to_the_scope(seeded) -> None:
    """An unrestricted viewer scoped to OPEN sees OPEN's risk, not SHUT's."""
    body = await _body(seeded, restricted=False, project_id=seeded["open"])
    risky = {t["task_id"] for t in body["at_risk"]}
    assert seeded["open_risk"] in risky
    assert seeded["shut_risk"] not in risky


# ── Review round 1 ───────────────────────────────────────────────────────────


def test_a_task_with_two_holders_is_one_entry_and_neither_holder_helps() -> None:
    """P2, rule 4. Ana and Bo both hold t1 and both are at risk on it. The
    join used to make two entries, one per holder, and offer Bo as Ana's
    helper and Ana as Bo's. Now t1 is one entry, and every holder is left out
    of its helpers and of the pickups."""
    idle_bo = [{"person_id": "id-bo", "name": "bo", "email": "bo@x.in",
                "skill_rows": [{"skill": "firmware", "level": None, "last_used_year": None}]}]
    joined = cap.rebalance_join(
        at_risk=[_risk("t1", "firmware fix", "ana"), _risk("t1", "firmware fix", "bo")],
        helpers=[_helper("ana", "firmware"), _helper("bo", "firmware"),
                 _helper("cy", "firmware")],
        idle=idle_bo, unassigned=[], this_year=YEAR, rank=_plain_rank,
        max_at_risk=8, max_pickups=4,
    )
    [only] = joined["at_risk"]
    assert [c.email for c in only["candidates"]] == ["cy@x.in"]
    assert [h["email"] for h in only["task"]["holders"]] == ["ana@x.in", "bo@x.in"]
    assert joined["total_at_risk"] == 1
    assert joined["pickups"] == [], "a holder was told to help on their own task"


def test_the_people_suggester_lists_a_shared_task_once(monkeypatch) -> None:
    """P2 through the People route. This CHANGES its output for a task with
    two holders, on purpose: before S7b review round 1 the task appeared once
    per holder, with each holder offered as the other's helper."""
    from gateway.routes.people import dashboard
    from gateway.routes.people import suggestions as sug

    task = {"task_id": "t1", "title": "Extruder firmware", "project_name": "R&D",
            "due_on": "2026-09-30", "shortfall_hours": 6.0}

    def row(name: str, **over: Any) -> SimpleNamespace:
        base = dict(kind="person", email=f"{name}@x.in", person_id=f"id-{name}",
                    name=name.title(), spare_hours_horizon=10.0, away=None,
                    at_risk=[], pill="on_track")
        base.update(over)
        return SimpleNamespace(**base)

    board = SimpleNamespace(
        rows=[row("ana", at_risk=[task]), row("bo", at_risk=[task]), row("cy")],
        work_visible=False, partial=False,
    )
    skills = [SimpleNamespace(person_id=f"id-{n}", skill="firmware", level=None,
                              last_used_year=None) for n in ("ana", "bo", "cy")]

    class _DB:
        async def execute(self, sql, params=None):
            return SimpleNamespace(fetchall=lambda: list(skills))

    @asynccontextmanager
    async def _session():
        yield _DB()

    async def _board(_user):
        return board

    monkeypatch.setattr(sug, "_tenant_session", _session)
    monkeypatch.setattr(dashboard, "get_dashboard", _board)
    out = asyncio.run(sug.get_suggestions(SimpleNamespace())).model_dump()
    [only] = out["at_risk"]
    assert only["holder"]["email"] == "ana@x.in"
    assert [c["email"] for c in only["candidates"]] == ["cy@x.in"]


def test_the_rebalance_route_without_the_grant_carries_no_lists(monkeypatch) -> None:
    """R7 fence. Through the ROUTE function with a real UserContext that
    lacks admin:members:read, so a hard-coded `hr_visible=True` fails."""
    from acb_auth import UserContext, UserRole, build_access

    @asynccontextmanager
    async def _session(*_a, **_k):
        yield _RefusingDB()

    async def _vis(_db, _user):
        return SimpleNamespace(unrestricted=True)

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_visibility", _vis)
    user = UserContext(email="x@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))
    body = asyncio.run(route.rebalance(user=user))
    assert body["hr_visible"] is False
    assert "at_risk" not in body and "pickups" not in body
