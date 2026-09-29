"""WS-27bn R5f: Overview of the one Reports app shows the Analytics numbers.

Spec: ``project-docs/specs/projects_reports.md`` §8 R5f, done-when (g) and (h).

Overview renders ``overviewState()`` through ``POST /projects/reports/preview``,
so ``render_body`` computes each section. The Analytics app read the analytics
routes. The claim is that the two give one answer:

* (g) for one reader and one scope, the preview of the Overview config equals
  the analytics routes: the throughput series, the finished total, the
  period, Load, the stuck bands, Capacity, Conflicts and the Forecast;
* (h) a member previews the Overview config. The ``load`` rows hold the
  member only, and ``hidden_people`` counts the others (§7.1 rule 3).

The config below is ``overviewState()`` in ``lib/reportBuilder.ts``, sent as
``configFor`` sends it. ``reportsOverview.test.ts`` pins that side.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects import analytics as ana
from gateway.routes.projects import analytics_capacity as cap_mod
from gateway.routes.projects import analytics_conflicts as conf_mod
from gateway.routes.projects import reports as rep

# The fixtures of R5a: one organization, two teams, a second organization.
from tests.unit.test_projects_report_scope_r5 import (  # noqa: F401
    _ladder,
    _needs_db,
    _people,
    _sql,
    _user,
    seeded,
    wired,
)

#: ``overviewState()`` as ``configFor`` sends it (§8 R5f rules 3 and 4).
OVERVIEW_CONFIG: dict[str, Any] = {
    "weeks": 12,
    "skip_current_week": False,
    "include_subtree": True,
    "sections": [
        "finished", "throughput", "outlook", "load", "capacity", "stuck",
        "conflicts",
    ],
}


def test_the_overview_config_is_one_the_server_accepts() -> None:
    """Hermetic. The seven sections are in ``SECTIONS`` order, and the
    normalised config keeps the period of the Analytics routes."""
    got = rep.normalise_report_config(dict(OVERVIEW_CONFIG))
    assert got["sections"] == OVERVIEW_CONFIG["sections"]
    order = list(rep.SECTIONS)
    assert [order.index(s) for s in got["sections"]] == sorted(
        order.index(s) for s in got["sections"]
    )
    assert got["weeks"] == ana.DEFAULT_WEEKS == 12
    assert got["skip_current_week"] is False


def _overview(seeded: dict[str, Any], reader: str, *, admin: bool = False,  # noqa: F811
              project: str | None = None) -> dict[str, Any]:
    return asyncio.run(rep.preview_report(
        {"project_id": project, "config": dict(OVERVIEW_CONFIG)},
        user=_user(seeded["who"][reader], admin=admin),
    ))


def _finish(seeded: dict[str, Any], task_key: str, days_ago: int) -> None:  # noqa: F811
    """Record a start and a finish on the activity spine, which is what the
    throughput and finished reads count."""
    for to, hours in (("in_progress", 30), ("done", 0)):
        _sql(
            seeded,
            "INSERT INTO pm_activities (task_id, organization_id, type,"
            " created_by, body, meta, created_at) VALUES"
            " (CAST(:t AS uuid), CAST(:o AS uuid), 'status_change', :me, :b,"
            " CAST(:m AS jsonb),"
            " now() - make_interval(days => :d, hours => :h))",
            t=seeded[task_key], o=seeded["org"], me=seeded["who"]["boss"],
            b=f"to {to}", m=f'{{"to_category": "{to}"}}', d=days_ago, h=hours,
        )


def _clear_spine(seeded: dict[str, Any]) -> None:  # noqa: F811
    _sql(
        seeded,
        "DELETE FROM pm_activities WHERE task_id IN"
        " (SELECT id FROM pm_tasks WHERE root_project_id = ANY(CAST(:p AS uuid[])))",
        p=seeded["projects"],
    )


#: More than ``MAX_PEOPLE`` (20), so a cap on the rows shows as a difference.
CROWD = 23


def _crowd(seeded: dict[str, Any]) -> None:  # noqa: F811
    """Seed ``CROWD`` people, each with one overdue task that blocks ``m1``.

    Each person gives a Load and a Capacity row, and each overdue blocker
    gives a ``blocker_late`` conflict row. So each list holds more than 20
    rows. The fixture deletes the tasks and their links by root project.
    """
    for i in range(CROWD):
        _sql(
            seeded,
            "WITH st AS (SELECT id FROM pm_task_statuses"
            "  WHERE project_id = CAST(:p AS uuid) AND category = 'todo'),"
            " t AS (INSERT INTO pm_tasks (title, project_id, root_project_id,"
            "  status_id, created_by, organization_id, task_number,"
            "  estimate_mins, due_at)"
            "  SELECT :title, CAST(:p AS uuid), CAST(:p AS uuid), st.id, :me,"
            "   CAST(:o AS uuid), 1000 + :i, 30 + :i, now() - interval '2 days'"
            "  FROM st RETURNING id),"
            " a AS (INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
            "  SELECT t.id, :who, :me FROM t)"
            " INSERT INTO pm_task_links (source_task_id, target_task_id, link_type,"
            "  created_by) SELECT t.id, CAST(:m1 AS uuid), 'blocks', :me FROM t",
            p=seeded["project"], title=f"crowd {i} {seeded['tag']}",
            me=seeded["who"]["boss"], o=seeded["org"], i=i,
            who=f"crowd{i:02d}-{seeded['tag']}@example.test", m1=seeded["m1"],
        )


@_needs_db
@pytest.mark.parametrize("scope_key", ["project", None])
def test_g_overview_equals_the_analytics_routes(seeded, wired, scope_key) -> None:  # noqa: F811
    """(g) One reader, one scope: the numbers the Analytics app drew.

    Repair round 1. Each section equals its route row for row, with the
    fields each panel draws. No list is cut short of the route's own list.
    """
    who = seeded["who"]
    project = seeded[scope_key] if scope_key else None
    admin = _user(who["boss"], admin=True)
    _finish(seeded, "m1", 3)
    _finish(seeded, "l1", 20)
    _crowd(seeded)
    try:
        body = _overview(seeded, "boss", admin=True, project=project)
        sections = body["sections"]
        assert list(sections) == OVERVIEW_CONFIG["sections"]

        thr = asyncio.run(ana.throughput(
            project_id=project, include_subtree=True, weeks=12, user=admin,
        ))
        assert sections["throughput"]["series"] == thr["series"]
        assert sum(w["completed"] for w in thr["series"]) == 2
        for key in ("completed", "cancelled", "measured", "no_start",
                    "median_hours", "p90_hours"):
            assert sections["throughput"][key] == thr["summary"][key], key
        assert sections["throughput"]["measured"] == 2

        fin = asyncio.run(ana.finished(
            project_id=project, include_subtree=True, weeks=12,
            skip_current_week=False, user=admin,
        ))
        assert sections["finished"]["total_completed"] == fin["total_completed"] == 2
        assert sections["finished"]["projects"] == [
            {k: p[k] for k in ("project_id", "name", "completed", "cancelled",
                               "median_hours")}
            for p in fin["projects"]
        ]
        assert (body["period_start"], body["period_end"]) == (
            fin["period_start"], fin["period_end"],
        )

        # The Load route caps its rows at 20 too, so both lists are 20 long.
        load = asyncio.run(ana.load(project_id=project, include_subtree=True, user=admin))
        assert load["people_total"] > 20
        assert sections["load"]["people"] == load["people"]
        for key in ("total_tasks", "people_total", "effort"):
            assert sections["load"][key] == load[key], key
        assert sections["load"]["effort"]["left_mins"] > 0

        stuck = asyncio.run(ana.stuck(project_id=project, include_subtree=True, user=admin))
        assert sections["stuck"]["stale"] == stuck["stale"]
        assert sections["stuck"]["overdue_total"] == stuck["overdue_total"] >= 1
        assert sections["stuck"]["overdue"] == stuck["overdue"]
        assert stuck["blocked_total"] >= 1
        assert sections["stuck"]["blocked"] == stuck["blocked"]
        assert sections["stuck"]["blocked_total"] == stuck["blocked_total"]

        cap = asyncio.run(cap_mod.capacity(project_id=project, user=admin))
        assert len(cap["rows"]) > 20
        assert sections["capacity"]["people"] == cap["rows"]
        assert sections["capacity"]["total_tasks"] == cap["total_tasks"]
        assert sections["capacity"]["people_total"] == cap["people_total"]

        conf = asyncio.run(conf_mod.conflicts(project_id=project, user=admin))
        assert len(conf["rows"]) > 20
        assert sections["conflicts"]["rows"] == conf["rows"]
        assert sections["conflicts"]["total"] == conf["total"]
        assert sections["conflicts"]["by_kind"] == conf["by_kind"]

        out = asyncio.run(ana.outlook(project_id=project, include_subtree=True, user=admin))
        assert sections["outlook"]["velocity"] == out["velocity"]
    finally:
        _clear_spine(seeded)


@_needs_db
def test_h_a_member_sees_their_own_load_row_only(seeded, wired) -> None:  # noqa: F811
    """(h) The Overview of a member: their row, and a count of the others."""
    who = seeded["who"]
    sections = _overview(seeded, "m")["sections"]
    people = {p for p in _people(sections["load"]) if p and not p.startswith("agent:")}
    assert people == {who["m"]}
    assert sections["load"]["hidden_people"] == 2
    cap_people = {
        p for p in _people(sections["capacity"]) if p and not p.startswith("agent:")
    }
    assert cap_people == {who["m"]}
    assert sections["capacity"]["hidden_people"] == 2
    # The totals still count every person's work.
    admin = _overview(seeded, "boss", admin=True)["sections"]
    assert sections["load"]["total_tasks"] == admin["load"]["total_tasks"]
    assert "hidden_people" not in admin["load"]
