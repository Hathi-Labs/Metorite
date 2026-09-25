"""WS-27bn R3b — the rebalance section, and the three H-185 items.

Spec: ``project-docs/specs/projects_reports.md`` §8 R3b.

**The claim is one computation.** ``render_body`` awaits ``rebalance_body``,
the function the ``GET /projects/analytics/rebalance`` route awaits. For one
scope and one caller the section equals the route's body, apart from the
pickups cap:

* (a) the section equals ``rebalance_body``, with ``pickups`` cut to
  ``MAX_PEOPLE`` and ``pickups_total`` beside it;
* a task two people hold is ONE entry, and ``at_risk_total`` counts it once;
* (b) a reader without ``admin:members:read`` gets ``hr_visible: false`` and
  neither list;
* (c) ``pickups`` holds ``MAX_PEOPLE`` rows at most, and ``pickups_total``
  counts every idle person with a match;
* (d) the section is opt-in and last.

The chat half pins the card, the chat text, ``_dig``'s list index and the
three H-185 items.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects import analytics as ana
from gateway.routes.projects import analytics_rebalance as reb
from gateway.routes.projects import reports as rep
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[2]
_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

HR_HINT = "Rebalancing needs HR read access. An admin can see it."


# ── Hermetic: the source says one computation ───────────────────────────────


def _call_args(fn: Any, name: str) -> str:
    body = inspect.getsource(fn)
    start = body.index(f"await {name}(") + len(f"await {name}(")
    depth, end = 1, start
    while depth:
        depth += {"(": 1, ")": -1}.get(body[end], 0)
        end += 1
    return body[start:end]


def test_the_render_awaits_the_routes_own_rebalance_body() -> None:
    call = _call_args(rep.render_body, "rebalance_body")
    assert "can_read_hr_fields(user)" in call, call
    assert "include_subtree" in call, call
    # Rebalancing reads its own horizon ahead of today, never the period.
    assert "weeks" not in call and "horizon" not in call, call
    assert "await rebalance_body(" in inspect.getsource(reb.rebalance)


def test_rebalance_is_an_opt_in_section_and_the_last() -> None:
    """(d). §8 R3 declares the order, and `rebalance` closes it."""
    assert rep.SECTIONS[-1] == "rebalance"
    assert "rebalance" not in rep.DEFAULT_SECTIONS
    assert "rebalance" not in rep.normalise_report_config({})["sections"]
    got = rep.normalise_report_config({"sections": ["rebalance", "stuck", "load"]})
    assert got["sections"] == ["load", "stuck", "rebalance"]


def test_no_template_goes_live_and_team_pulse_waits_for_pulse_only() -> None:
    live = [k for k, t in rep.TEMPLATES.items() if t["available"]]
    assert live == ["weekly_delivery", "project_status"]
    assert rep.TEMPLATES["team_pulse"]["waits_for"] == (
        "The pulse section and a today period"
    )


# ── Hermetic: the chat card and the chat text ───────────────────────────────

def _section() -> dict[str, Any]:
    return {
        "hr_visible": True, "horizon_days": 14,
        "at_risk": [{
            "task_id": "0b8c6f0e-1111-4c1e-9a54-000000000001",
            "title": "Weld the gantry", "project_name": "Rig",
            "due_on": "2026-09-27", "shortfall_hours": 12.0,
            "holder": {"person_id": "p1", "name": "Hal", "email": "hal@x.in"},
            "holders": [{"person_id": "p1", "name": "Hal", "email": "hal@x.in"}],
            "candidates": [{"name": "Ivy", "email": "ivy@x.in"},
                           {"name": "Bo", "email": "bo@x.in"}],
            "hours_basis": True,
        }],
        "at_risk_total": 1,
        "pickups": [{"name": "Ivy", "email": "ivy@x.in",
                     "tasks": [{"task_id": "t2", "title": "Weld a jig"}]}],
        "pickups_total": 1, "idle_total": 3,
    }


def test_dig_reads_a_list_index() -> None:
    from skill_projects.reads import _MISSING, _dig

    row = _section()["at_risk"][0]
    assert _dig(row, "candidates.0.name") == "Ivy"
    assert _dig(row, "candidates.1.name") == "Bo"
    assert _dig(row, "candidates.5.name") is _MISSING
    assert _dig({"candidates": []}, "candidates.0.name") is _MISSING
    # A dotted dict path still reads as before (R3a).
    assert _dig({"plan": {"slip_days": 3}}, "plan.slip_days") == 3


def test_the_rebalance_card_draws_tasks_and_counts_the_idle() -> None:
    from skill_projects.views import REPORT_CARD_SECTIONS, _card_section

    stats, table = _card_section("rebalance", _section())
    assert stats == [{"label": "At risk", "value": 1},
                     {"label": "Idle people", "value": 3}]
    assert table is not None
    assert table["title"] == REPORT_CARD_SECTIONS["rebalance"]["title"] == "Who could help"
    assert table["columns"] == ["Task", "Held by", "Due", "Could help"]
    assert table["rows"] == [{"cells": ["Weld the gantry", "Hal", "2026-09-27", "Ivy"]}]
    # Pickups travel as the idle count only: a pickup row names skills.
    assert "Weld a jig" not in str(table)


def test_the_rebalance_card_without_the_grant_says_why_and_draws_no_zero() -> None:
    """(b), the card half."""
    from skill_projects.views import _card_section

    stats, table = _card_section("rebalance", {"hr_visible": False, "horizon_days": 14})
    assert stats == []
    assert table == {"title": "Who could help", "columns": ["Note"],
                     "rows": [{"cells": [HR_HINT]}]}


def test_the_chat_text_prints_plain_labels_and_no_task_id() -> None:
    from skill_projects.reads import _report_section

    lines = _report_section("rebalance", _section())
    assert lines[0] == (
        "rebalance: tasks at risk 1, idle people 3, people who could take work 1,"
        " HR access True, horizon days 14"
    )
    assert lines[1] == (
        "- «Weld the gantry» · project «Rig», due 2026-09-27, hours short 12.0,"
        " held by «Hal», first helper «Ivy»"
    )
    assert "0b8c6f0e" not in "\n".join(lines)


def test_the_chat_text_without_the_grant_says_why() -> None:
    from skill_projects.reads import _report_section

    lines = _report_section("rebalance", {"hr_visible": False, "horizon_days": 14})
    assert lines == ["rebalance: HR access False, horizon days 14", f"  {HR_HINT}"]


# ── H-185: three polish items from the R3a review ───────────────────────────


def test_h185_1_the_card_prints_a_timestamp_as_its_date() -> None:
    from skill_projects.views import _card_cell

    row = {"plan": {"planned_finish": "2026-11-02T00:00:00+00:00"}, "due_on": "2026-09-27"}
    assert _card_cell("plan.planned_finish", row) == "2026-11-02"
    assert _card_cell("due_on", row) == "2026-09-27"
    # A title that only starts like a date is not cut.
    assert _card_cell("title", {"title": "2026-11-02 launch"}) == "2026-11-02 launch"


def test_h185_2_the_outlook_says_slip_days_once_and_uses_plain_labels() -> None:
    from skill_projects.reads import _REPORT_LABELS, _REPORT_SECTIONS
    from skill_projects.views import REPORT_CARD_SECTIONS

    spec = REPORT_CARD_SECTIONS["outlook"]
    labels = [label for _, label in [*spec["stats"], *spec["fields"]]]
    assert labels.count("Slip days") == 1, labels
    for key in _REPORT_SECTIONS["outlook"][2]:
        assert "." not in _REPORT_LABELS[key], key


@pytest.mark.parametrize(
    ("remaining", "finished", "created"),
    [(10, [3, 3, 3], [1, 1, 1]), (1, [4, 0, 2], [0, 0, 0]), (200, [5] * 6, [4] * 6)],
)
def test_h185_3_the_forecast_is_today_plus_whole_weeks(
    remaining: int, finished: list[int], created: list[int],
) -> None:
    """`lib/outlook.ts` `slipRange` places the plan at `weeks_remaining` times 7
    less `slip_days`. That holds only while the forecast is today plus whole
    weeks, so this pins it."""
    got = ana.project_forecast(
        remaining_tasks=remaining, finished=finished, created=created,
    )
    assert got["verdict"] == "converging"
    assert got["finish_date"] == (
        date.today() + timedelta(days=7 * got["weeks_remaining"])
    ).isoformat()


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


#: More idle people than the report's cap, so the cap has work to do.
IDLE = ana.MAX_PEOPLE + 2


@pytest.fixture
def seeded(_ladder):
    """One project with a task TWO people hold, and a crowd of idle welders.

    ``weld`` below is ``weld<tag>``, so people other suites leave behind
    never match.

    * ``hal`` and ``kay`` both hold a 50-hour weld task due in two days, so
      each is at risk on it.
    * ``ivy`` and ``IDLE`` more people know weld and hold nothing, so each is
      idle, and each can take the one unassigned weld task.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    weld = f"weld{tag}"
    made: dict[str, Any] = {"weld": weld, "people": []}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org
        pid = str(c.execute(
            text(
                "INSERT INTO pm_projects (name, status, source, created_by,"
                " organization_id, timezone, parent_project_id, owns_statuses)"
                " VALUES (:n,'active','manual','r3b@example.test',"
                " CAST(:o AS uuid),'UTC',NULL,true) RETURNING id"
            ),
            {"n": f"r3b-{tag}", "o": org},
        ).scalar_one())
        made["project"] = pid
        status = str(c.execute(
            text(
                "INSERT INTO pm_task_statuses (project_id,name,color,position,"
                " category) VALUES (CAST(:p AS uuid),'To do','gray',0,'todo')"
                " RETURNING id"
            ),
            {"p": pid},
        ).scalar_one())

        def person(key: str, *skills: str) -> str:
            email = f"{key}-{tag}@example.test"
            person_id = str(c.execute(
                text(
                    "INSERT INTO people (id, name, email, status, skills, source,"
                    " source_key, organization_id, updated_by, updated_at)"
                    " VALUES (gen_random_uuid(), :n, :e, 'active', ARRAY[]::text[],"
                    " 'manual', :k, CAST(:o AS uuid), 'test', now()) RETURNING id"
                ),
                {"n": f"{key.title()} {tag}", "e": email,
                 "k": f"manual:r3b-{key}-{tag}", "o": org},
            ).scalar_one())
            made["people"].append(person_id)
            for s in skills:
                c.execute(
                    text(
                        "INSERT INTO people_skills (organization_id, person_id,"
                        " skill, level) VALUES (CAST(:o AS uuid), CAST(:p AS uuid),"
                        " :s, 'expert')"
                    ),
                    {"o": org, "p": person_id, "s": s},
                )
            return email

        def task(title: str, *, who: tuple[str, ...] = (), est: int | None = None,
                 due_days: int | None = None) -> str:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, due_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), 'r3b@example.test', CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number),0)+1, :est,"
                    " CASE WHEN CAST(:d AS int) IS NULL THEN NULL"
                    "      ELSE now() + make_interval(days => CAST(:d AS int)) END"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": title, "p": pid, "s": status, "o": org, "est": est,
                 "d": due_days},
            ).scalar_one())
            for email in who:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a,"
                        " 'r3b@example.test')"
                    ),
                    {"t": tid, "a": email},
                )
            return tid

        made["hal"] = person("hal")
        made["kay"] = person("kay")
        made["ivy"] = person("ivy", weld)
        made["idle"] = [made["ivy"]] + [person(f"idle{i}", weld) for i in range(IDLE)]
        made["shared"] = task(f"{weld} the gantry", who=(made["hal"], made["kay"]),
                              est=3000, due_days=2)
        made["free"] = task(f"{weld} a jig")
    yield made
    with eng.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        c.execute(text("DELETE FROM people WHERE id = ANY(CAST(:i AS uuid[]))"),
                  {"i": made["people"]})
    eng.dispose()


def _user(*, hr: bool) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    grants = ["feature:projects"] + (["admin:members:read"] if hr else [])
    return UserContext(email="r3b@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(grants))


@pytest.fixture
def wired(seeded, monkeypatch):
    """Bind the report route to one engine, with a whole-tenant reader."""
    from gateway.routes.projects.core import Visibility
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    vis = Visibility(unrestricted=True, email="", groups=(),
                     organization_id=seeded["org"])

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        async with eng.begin() as conn:
            yield conn

    async def _resolve(_db: Any, _user: Any) -> Any:
        return vis

    monkeypatch.setattr(rep, "_tenant_session", _session)
    monkeypatch.setattr(rep, "resolve_visibility", _resolve)
    yield {"engine": eng, "vis": vis}
    asyncio.run(eng.dispose())


def _report(project: str | None, *, hr: bool) -> dict[str, Any]:
    return asyncio.run(
        rep.preview_report(
            {"project_id": project, "config": {"sections": ["rebalance"]}},
            user=_user(hr=hr),
        )
    )["sections"]["rebalance"]


def _route_body(wired: dict[str, Any], project: str | None) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with wired["engine"].connect() as db:
            return await reb.rebalance_body(
                db, wired["vis"], hr_visible=True, project_id=project,
                include_subtree=True,
            )
    return asyncio.run(run())


@_needs_db
def test_a_shared_task_counts_once(seeded, wired) -> None:
    """Hal and Kay both hold the gantry task, and both are at risk on it. It
    is one entry, `at_risk_total` counts it once, and neither holder helps."""
    got = _report(seeded["project"], hr=True)
    entries = [t for t in got["at_risk"] if t["task_id"] == seeded["shared"]]
    assert len(entries) == 1, got["at_risk"]
    [entry] = entries
    assert {h["email"] for h in entry["holders"]} == {seeded["hal"], seeded["kay"]}
    assert got["at_risk_total"] == 1
    helpers = {c["email"] for c in entry["candidates"]}
    assert not helpers & {seeded["hal"], seeded["kay"]}


@_needs_db
@pytest.mark.parametrize("scope", ["project", "portfolio"])
def test_the_section_equals_the_rebalance_body_apart_from_the_cap(
    seeded, wired, scope,
) -> None:
    """(a) One computation, for one scope and one caller."""
    pid = seeded["project"] if scope == "project" else None
    got = _report(pid, hr=True)
    want = _route_body(wired, pid)
    assert got["pickups"] == want["pickups"][: ana.MAX_PEOPLE]
    assert got["pickups_total"] == len(want["pickups"])
    rest = {k: v for k, v in got.items() if k not in ("pickups", "pickups_total")}
    assert rest == {k: v for k, v in want.items() if k != "pickups"}
    # Real rows, not two empty lists that agree by accident.
    assert seeded["shared"] in {t["task_id"] for t in got["at_risk"]}


@_needs_db
def test_the_pickups_are_capped_and_the_total_counts_them_all(seeded, wired) -> None:
    """(c) Twenty-three idle welders, twenty rows, and a total of 23."""
    got = _report(seeded["project"], hr=True)
    assert len(got["pickups"]) == ana.MAX_PEOPLE
    assert got["pickups_total"] == len(seeded["idle"]) == IDLE + 1
    assert {p["email"] for p in got["pickups"]} <= set(seeded["idle"])


@_needs_db
def test_a_reader_without_the_grant_gets_no_lists(seeded, wired) -> None:
    """(b) Through the render with a real UserContext that lacks
    `admin:members:read`, so a hard-coded `hr_visible=True` fails. The admin
    control beside it proves the rows exist."""
    hidden = _report(seeded["project"], hr=False)
    assert hidden["hr_visible"] is False
    for key in ("at_risk", "pickups", "pickups_total", "at_risk_total", "idle_total"):
        assert key not in hidden, key
    shown = _report(seeded["project"], hr=True)
    assert shown["hr_visible"] is True and shown["at_risk"]
