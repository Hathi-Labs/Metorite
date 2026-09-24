"""WS-27bm S7d — the plan preview: fit and hours for a plan that does not exist yet.

Spec: ``project-docs/specs/projects_ai_chat.md`` §10.6 items 6 and 7, §13.6.

Two halves, as in ``test_projects_analytics_conflicts.py``.

* **Hermetic** — the 422s before a session, a body key the model does not
  declare, the dependency warnings through the one rule, the HR gate at the
  route and in the body, and the source fence of item 7. These never skip.
* **R8, on a real Postgres through asyncpg** — the named owner's fit, "no
  skill match" rather than an absent fit, two plan rows for one owner where
  the second runs out of hours, the hours equal to ``person_capacity`` with
  the plan rows added, and work outside the viewer's grant with no effect.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass.
"""
from __future__ import annotations

import asyncio
import json
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
from gateway.routes.projects import plan_preview as route
from pydantic import ValidationError
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps/services/gateway/gateway"
SOURCE = GATEWAY / "routes/projects/plan_preview.py"

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

TODAY = date(2026, 9, 24)
HR_KEYS = ("fit", "hours", "marks", "spare_hours", "needed_hours", "name")


def _row(key: str, **over: Any) -> dict[str, Any]:
    base = {"key": key, "title": f"Task {key}", "owner": "a@x.in",
            "effort_mins": 60, "due": "2026-10-10"}
    return {**base, **over}


def _body(*rows: dict[str, Any]) -> route.PlanPreviewIn:
    return route.PlanPreviewIn(rows=[route.PlanRowIn(**r) for r in rows])


def _keys_in(value: Any) -> set[str]:
    """Every dict key anywhere in a JSON body."""
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(k)
            out |= _keys_in(v)
    elif isinstance(value, list):
        for v in value:
            out |= _keys_in(v)
    return out


# ── Hermetic: the refusals, before a session ─────────────────────────────────


def test_more_than_fifty_rows_is_422() -> None:
    body = _body(*[_row(f"t{i}") for i in range(51)])
    with pytest.raises(HTTPException) as err:
        route.checked_rows(body)
    assert err.value.status_code == 422 and "at most 50" in err.value.detail


@pytest.mark.parametrize(
    "rows, words",
    [
        ([_row("t1"), _row("t1")], "same key"),
        ([_row("bad key")], "row key"),
        ([_row("t1", start="2026-10-11")], "after the due date"),
        ([_row("t1", due="soon")], "must be a date"),
        ([_row("t1", after=["t9"])], "no row called"),
        ([_row("t1", after=["t1"])], "cannot block itself"),
    ],
)
def test_a_bad_row_is_422_and_names_it(rows: list[dict[str, Any]], words: str) -> None:
    with pytest.raises(HTTPException) as err:
        route.checked_rows(_body(*rows))
    assert err.value.status_code == 422 and words in err.value.detail


@pytest.mark.parametrize("extra", ["email", "organization_id", "viewer"])
def test_the_body_names_no_member_and_no_tenant(extra: str) -> None:
    """R5. The viewer is the caller. A body that tries to name one is refused."""
    with pytest.raises(ValidationError):
        route.PlanPreviewIn(**{"rows": [_row("t1")], extra: "x@y.in"})
    with pytest.raises(ValidationError):
        route.PlanRowIn(**{**_row("t1"), extra: "x@y.in"})


def test_a_refused_body_opens_no_session(monkeypatch) -> None:
    def _session(*_a, **_k):
        raise AssertionError("a session opened for a refused plan")

    monkeypatch.setattr(route, "_tenant_session", _session)
    body = _body(*[_row(f"t{i}") for i in range(51)])
    with pytest.raises(HTTPException):
        asyncio.run(route.plan_preview(body, user=SimpleNamespace()))


# ── Hermetic: the dependency warnings, through the one rule ─────────────────


def test_a_misordered_pair_gives_one_warning() -> None:
    rows = route.checked_rows(_body(
        _row("t1", due="2026-10-10"),
        _row("t2", start="2026-10-05", due="2026-10-12", after=["t1"]),
    ))
    [w] = route.dependency_warnings(rows)
    assert (w["blocker"], w["blocked"]) == ("t1", "t2")
    assert (w["blocker_ends"], w["blocked_starts"]) == ("2026-10-10", "2026-10-05")
    assert w["kind"] == "dependency_order"


def test_a_shared_day_is_the_handover_and_gives_no_warning() -> None:
    rows = route.checked_rows(_body(
        _row("t1", due="2026-10-10"),
        _row("t2", start="2026-10-10", due="2026-10-12", after=["t1"]),
    ))
    assert route.dependency_warnings(rows) == []


def test_the_warnings_agree_with_dependency_conflict() -> None:
    """One rule. Every pair the preview checks is decided by it."""
    from gateway.conflicts import dependency_conflict

    rows = route.checked_rows(_body(
        _row("a", due="2026-10-01"),
        _row("b", start="2026-09-28", due="2026-10-03", after=["a"]),
        _row("c", due="2026-10-04", after=["a", "b"]),
    ))
    by_key = {r["key"]: r for r in rows}
    want = {
        (a, r["key"])
        for r in rows for a in r["after"]
        if dependency_conflict(
            {"start_date": by_key[a]["start"], "due_at": by_key[a]["due"]},
            {"start_date": r["start"], "due_at": r["due"]},
        )
    }
    got = {(w["blocker"], w["blocked"]) for w in route.dependency_warnings(rows)}
    assert got == want and got


def test_the_window_runs_to_the_last_due_date_within_90_days() -> None:
    rows = route.checked_rows(_body(_row("t1", due="2026-10-10"), _row("t2", due="2026-10-04")))
    days, window = route.plan_window(rows, TODAY)
    assert days == 16 and window["ends_on"] == "2026-10-10" and window["basis"] == "plan"
    far = route.checked_rows(_body(_row("t1", due="2027-06-01")))
    assert route.plan_window(far, TODAY)[0] == 90
    past = route.checked_rows(_body(_row("t1", due="2026-09-01")))
    assert route.plan_window(past, TODAY)[0] == 1


# ── Hermetic: the HR gate (item 6) ───────────────────────────────────────────


def test_without_the_grant_the_body_carries_no_fit_or_hours_key() -> None:
    """§13.6 rule 4. No HR statement runs, so no database is needed."""
    rows = route.checked_rows(_body(
        _row("t1", due="2026-10-10"),
        _row("t2", start="2026-10-05", due="2026-10-12", after=["t1"]),
    ))
    body = asyncio.run(route.plan_preview_body(
        None, SimpleNamespace(unrestricted=True), hr_visible=False, rows=rows, today=TODAY,
    ))
    assert body["hr_visible"] is False
    assert body["hr_note"] == route.HR_NOTE
    assert not set(HR_KEYS) & _keys_in(body)
    assert body["rows"] == [{"key": "t1"}, {"key": "t2"}]
    # The dependency warnings are not HR data.
    assert len(body["dependency_warnings"]) == 1


def test_the_route_passes_the_callers_grant_not_a_constant(monkeypatch) -> None:
    """R7 fence for rule 4. Hard-coding ``hr_visible=True`` fails this."""
    from contextlib import asynccontextmanager

    from acb_auth import UserContext, UserRole, build_access

    seen: list[bool] = []

    @asynccontextmanager
    async def _session(*_a, **_k):
        yield None

    async def _vis(_db, _user):
        return SimpleNamespace(unrestricted=True)

    async def _answer(_db, _vis, *, hr_visible, **_k):
        seen.append(hr_visible)
        return {}

    monkeypatch.setattr(route, "_tenant_session", _session)
    monkeypatch.setattr(route, "resolve_visibility", _vis)
    monkeypatch.setattr(route, "plan_preview_body", _answer)
    for grants, want in ((["feature:projects"], False),
                         (["feature:projects", "admin:members:read"], True)):
        user = UserContext(email="c@example.test", role=UserRole.EMPLOYEE,
                           access=build_access(grants))
        asyncio.run(route.plan_preview(_body(_row("t1")), user=user))
        assert seen[-1] is want


# ── Hermetic: the source fences (item 7) ─────────────────────────────────────


def test_the_preview_imports_the_one_rule_and_the_one_ranker() -> None:
    """§10.6 item 7. One arithmetic (§13.2 rule 1): the preview ranks through
    ``rank_candidates`` and never scores a skill itself."""
    code = SOURCE.read_text(encoding="utf-8")
    assert re.search(r"from gateway\.conflicts import [^\n]*\bdependency_conflict\b", code)
    assert re.search(r"from gateway\.routes\.people\.suggestions import rank_candidates", code)
    assert "rank_candidates(" in code and "dependency_conflict(" in code
    assert "score_skills" not in code
    assert "at_risk_tasks(" not in code and "working_hours_between" not in code


def test_the_preview_never_writes() -> None:
    from tests.unit.test_people_dashboard import _strip_prose

    code = _strip_prose(SOURCE.read_text(encoding="utf-8"))
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{verb}\b", code), verb


def test_the_route_is_mounted_as_a_post() -> None:
    from gateway.routes.projects import router

    found = {
        (m, getattr(r, "path", ""))
        for r in router.routes for m in (getattr(r, "methods", None) or set())
    }
    assert ("POST", "/projects/plan/preview") in found


def test_the_chat_manifest_reads_it_as_a_preview() -> None:
    pytest.importorskip("skill_projects")
    from skill_projects import manifest as m

    assert ("POST", "/projects/plan/preview") in m.READ_ONLY_POSTS
    assert m.is_read("POST", "/projects/plan/preview")
    assert "link_tasks" in m.COMPOSITE["propose_plan"]


def test_the_row_cap_is_the_chats_batch_limit() -> None:
    pytest.importorskip("skill_projects")
    from skill_projects.writes import MAX_BATCH

    assert route.MAX_PLAN_ROWS == MAX_BATCH


# ── R8: a real Postgres, through asyncpg ─────────────────────────────────────


def _async_url() -> str:
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _day(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _noon_utc(days: int) -> str:
    return f"{_day(days)}T12:00:00+00:00"


@pytest.fixture(scope="module")
def _ladder():
    """Apply the tenant ladder ONCE for this module (the S7a lesson)."""
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
    """Two projects and three people.

    ``A`` is granted to the restricted viewer, ``H`` is not.

    * ``wel`` has the skill ``welding`` (expert) and no open work.
    * ``nos`` has no skills and no open work.
    * ``busy`` holds EST in ``A`` (30 h, due +3) and HID in ``H`` (200 h, due
      +2). The restricted viewer sees EST only.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    made: dict[str, Any] = {"tag": tag, "viewer": f"viewer-{tag}@example.test"}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        for key in ("A", "H"):
            made[key] = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, owns_statuses)"
                    " VALUES (:n, 'active', 'manual', 'pp@example.test', CAST(:o AS uuid),"
                    " 'UTC', TRUE) RETURNING id"
                ),
                {"n": f"pp-{key}-{tag}", "o": org},
            ).scalar_one())
            made[f"{key}:todo"] = str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                    " category) VALUES (CAST(:p AS uuid), 'To do', 'gray', 0, 'todo')"
                    " RETURNING id"
                ),
                {"p": made[key]},
            ).scalar_one())
        c.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s, 'pp@example.test',"
                " CAST(:o AS uuid))"
            ),
            {"p": made["A"], "s": made["viewer"], "o": org},
        )
        for key in ("wel", "nos", "busy"):
            made[key] = f"{key}-{tag}@example.test"
            made[f"{key}_id"] = str(c.execute(
                text(
                    "INSERT INTO people (id, name, email, status, skills, source,"
                    " source_key, organization_id, updated_by, updated_at)"
                    " VALUES (gen_random_uuid(), :n, :e, 'active', ARRAY[]::text[],"
                    " 'manual', :k, CAST(:o AS uuid), 'test', now()) RETURNING id"
                ),
                {"n": f"{key.title()} {tag}", "e": made[key],
                 "k": f"manual:pp-{key}-{tag}", "o": org},
            ).scalar_one())
        c.execute(
            text(
                "INSERT INTO people_skills (organization_id, person_id, skill, level)"
                " VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'welding', 'expert')"
            ),
            {"o": org, "p": made["wel_id"]},
        )

        def task(key: str, where: str, est: int, due: int, who: str) -> None:
            made[key] = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, due_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid),"
                    " 'pp@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1, :est, CAST(:due AS timestamptz)"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": f"{key} {tag}", "p": made[where], "s": made[f"{where}:todo"],
                 "o": org, "est": est, "due": _noon_utc(due)},
            ).scalar_one())
            c.execute(
                text(
                    "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
                    " VALUES (CAST(:t AS uuid), :a, 'pp@example.test')"
                ),
                {"t": made[key], "a": made[who]},
            )

        task("EST", "A", 30 * 60, 3, "busy")
        task("HID", "H", 200 * 60, 2, "busy")
    yield made
    with eng.begin() as c:
        ids = [made["A"], made["H"]]
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_project_grants WHERE project_id = ANY(CAST(:p AS uuid[]))"),
                  {"p": ids})
        c.execute(text("DELETE FROM pm_projects WHERE id = ANY(CAST(:p AS uuid[]))"), {"p": ids})
        c.execute(text("DELETE FROM people WHERE id = ANY(CAST(:i AS uuid[]))"),
                  {"i": [made[f"{k}_id"] for k in ("wel", "nos", "busy")]})
    eng.dispose()


def _vis(seeded: dict[str, Any], *, restricted: bool) -> Any:
    from gateway.routes.projects.core import Visibility

    if restricted:
        return Visibility(unrestricted=False, email=seeded["viewer"], groups=(),
                          organization_id=seeded["org"])
    return Visibility(unrestricted=True, email="", groups=(), organization_id=seeded["org"])


async def _preview(seeded: dict[str, Any], rows: list[dict[str, Any]], *,
                   restricted: bool = False, hr: bool = True) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        async with eng.connect() as db:
            return await route.plan_preview_body(
                db, _vis(seeded, restricted=restricted), hr_visible=hr,
                rows=route.checked_rows(_body(*rows)),
            )
    finally:
        await eng.dispose()


async def _expected_walk(seeded: dict[str, Any], plan: list[dict[str, Any]],
                         existing: list[tuple[int, int]], horizon: int) -> dict[str, Any]:
    """``person_capacity`` for ``busy``, by hand: the seeded tasks the
    viewer may see, plus the plan rows. The rule the route must match."""
    from gateway.capacity import person_capacity
    from gateway.work_schedule import load_policy, person_schedule
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        async with eng.connect() as db:
            policy = await load_policy(db)
            record = (await db.execute(
                text("SELECT id, working_hours FROM people WHERE id = CAST(:i AS uuid)"),
                {"i": seeded["busy_id"]},
            )).fetchone()
    finally:
        await eng.dispose()
    today = date.today()
    dated = [
        {"id": f"x{i}", "title": f"x{i}", "due_at": today + timedelta(days=due),
         "estimate_mins": mins, "_due": today + timedelta(days=due)}
        for i, (mins, due) in enumerate(existing)
    ] + [
        {"id": f"plan:{r['key']}", "title": r["title"],
         "due_at": date.fromisoformat(r["due"]), "estimate_mins": r["effort_mins"],
         "_due": date.fromisoformat(r["due"])}
        for r in plan
    ]
    totals = {
        "open_tasks": len(dated), "mins": sum(d["estimate_mins"] for d in dated),
        "unestimated": 0, "overdue": 0, "next_due": None,
    }
    return person_capacity(
        schedule=person_schedule(policy or {}, record), spans=[], totals=totals,
        dated=dated, today=today, horizon_days=horizon,
    )


def _by_key(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in body["rows"]}


@_needs_db
async def test_the_named_owners_fit_and_no_skill_match_on_a_real_database(seeded) -> None:
    """Item 6 and §13.6 rule 1. The fit is the NAMED owner's. An owner with
    no matching skill says "no skill match", and never gets the top three."""
    body = await _preview(seeded, [
        _row("t1", title="Do the welding on the frame", owner=seeded["wel"], due=_day(7)),
        _row("t2", title="Do the welding on the frame", owner=seeded["nos"], due=_day(7)),
    ])
    rows = _by_key(body)
    assert rows["t1"]["fit"]["matched"] is True and rows["t1"]["fit"]["skills"] == ["welding"]
    assert rows["t1"]["marks"] == []
    assert rows["t2"]["fit"] == {"matched": False, "skills": [], "text": "no skill match",
                                 "warnings": []}
    assert rows["t2"]["marks"] == ["no_skill_match"]
    assert seeded["wel"] not in json.dumps(rows["t2"])


@_needs_db
async def test_a_skilled_owner_with_no_spare_hours_keeps_the_skill_match(seeded) -> None:
    """Dispatch gap 1. ``rank_candidates`` drops a person with no spare hours,
    so the fit uses the neutral figure, and the hours are reported apart."""
    body = await _preview(seeded, [
        _row("t1", title="Do the welding on the frame", owner=seeded["wel"], effort_mins=500 * 60,
             due=_day(3)),
    ])
    row = _by_key(body)["t1"]
    assert row["fit"]["matched"] is True
    assert row["marks"] == ["short_of_hours"]


@_needs_db
async def test_two_rows_for_one_owner_mark_the_second_when_the_hours_run_out(seeded) -> None:
    """Item 6 and §13.6 rule 2. Each fits alone. Together they do not."""
    body = await _preview(seeded, [
        _row("t1", owner=seeded["nos"], effort_mins=60, due=_day(7)),
        _row("t2", owner=seeded["nos"], effort_mins=100 * 60, due=_day(8)),
    ])
    rows = _by_key(body)
    assert rows["t1"]["hours"]["fits"] is True and "short_of_hours" not in rows["t1"]["marks"]
    assert rows["t2"]["hours"]["fits"] is False and "short_of_hours" in rows["t2"]["marks"]
    alone = await _preview(seeded, [
        _row("t2", owner=seeded["nos"], effort_mins=60, due=_day(8)),
    ])
    assert _by_key(alone)["t2"]["hours"]["fits"] is True


@_needs_db
async def test_the_hours_equal_person_capacity_plus_the_plan_rows(seeded) -> None:
    """Item 6. The restricted viewer sees EST (30 h, +3) and not HID. The
    route's figures are ``person_capacity`` over EST plus the plan rows, so
    the hidden 200 hours have no effect."""
    plan = [_row("t1", owner=seeded["busy"], effort_mins=20 * 60, due=_day(5))]
    body = await _preview(seeded, plan, restricted=True)
    days = body["window"]["days"]
    want = await _expected_walk(seeded, plan, [(30 * 60, 3)], days)
    risky = {a["task_id"]: a for a in want["at_risk"]}
    row = _by_key(body)["t1"]
    if "plan:t1" in risky:
        got = risky["plan:t1"]
        assert row["hours"]["fits"] is False
        for key in ("needed_hours", "available_hours", "shortfall_hours"):
            assert row["hours"][key] == got[key], key
    else:
        assert row["hours"]["fits"] is True
    base = await _expected_walk(seeded, [], [(30 * 60, 3)], days)
    assert row["hours"]["spare_hours"] == base["spare_horizon"]

    # The unrestricted viewer sees HID too, and then the plan row is short.
    wide = await _preview(seeded, plan, restricted=False)
    wide_row = _by_key(wide)["t1"]
    assert wide_row["hours"]["fits"] is False
    want_wide = await _expected_walk(seeded, plan, [(30 * 60, 3), (200 * 60, 2)], days)
    got = {a["task_id"]: a for a in want_wide["at_risk"]}["plan:t1"]
    assert wide_row["hours"]["needed_hours"] == got["needed_hours"]
    assert row["hours"].get("needed_hours") != wide_row["hours"]["needed_hours"]


@_needs_db
async def test_without_the_grant_no_hr_key_is_in_the_body_on_a_real_database(seeded) -> None:
    body = await _preview(seeded, [
        _row("t1", owner=seeded["busy"], due=_day(5)),
        _row("t2", owner=seeded["wel"], start=_day(2), due=_day(6), after=["t1"]),
    ], hr=False)
    assert body["hr_visible"] is False
    assert not set(HR_KEYS) & _keys_in(body)
    assert seeded["busy"] not in json.dumps(body)
    assert [(w["blocker"], w["blocked"]) for w in body["dependency_warnings"]] == [("t1", "t2")]


@_needs_db
async def test_an_owner_outside_the_directory_and_an_agent(seeded) -> None:
    body = await _preview(seeded, [
        _row("t1", owner=f"ghost-{seeded['tag']}@example.test", due=_day(5)),
        _row("t2", owner="agent:projects-assistant", due=_day(5)),
    ])
    rows = _by_key(body)
    assert rows["t1"]["fit"]["text"] == "not in the directory"
    assert rows["t1"]["marks"] == ["not_in_directory"]
    assert rows["t2"]["fit"]["text"] == "an agent" and rows["t2"]["marks"] == []
