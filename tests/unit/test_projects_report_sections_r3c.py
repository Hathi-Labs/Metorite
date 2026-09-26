"""WS-27bn R3c — the hygiene section and T13, and the four H-186 items.

Spec: ``project-docs/specs/projects_reports.md`` §5 ("Stale") and §8 R3c.

**The claim is one computation over Load's "open".** ``render_body`` awaits
``hygiene_body``, which reads open work through ``load_open_where`` and
``load_params``. On a real database:

* (a) the section equals ``hygiene_body`` for one scope and one reader;
* (b) a seeded task shows in each of the four kinds, and a task whose only
  assignee is ``agent:<name>`` is not in ``no_assignee``;
* (c) triage, archived and closed tasks, and the work of a stopped project,
  are in no kind;
* (d) a task in progress that has not changed for 15 days is stale, and one
  for 13 days is not;
* (e) a private ``pm_task_personal.time_estimate_mins`` does not fill the
  shared estimate, and a source test proves the SQL never names the table;
* (f) a task the reader cannot see is in no count;
* (g) 23 undated tasks give 20 rows and ``by_kind.no_due_date`` 23.

The hermetic half pins the order, the template, the chat and H-186.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects import analytics as ana
from gateway.routes.projects import reports as rep
from sqlalchemy import text

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_needs_db = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)

KINDS = ("no_assignee", "no_due_date", "no_estimate", "stale_in_progress")


# ── Hermetic: the rule and the source ────────────────────────────────────────


def test_stale_days_is_the_lower_bound_of_the_14_to_30_band() -> None:
    """§5. One number for "stale", and the ageing chart agrees with it."""
    assert ana.STALE_DAYS == 14
    band = {name: low for name, low, _ in ana.STALE_BANDS}
    assert band["days_14_to_30"] == ana.STALE_DAYS


def test_the_kinds_are_the_four_in_order() -> None:
    assert tuple(kind for kind, _ in ana.HYGIENE_KINDS) == KINDS


def _hygiene_sql() -> str:
    return "\n".join([
        ana.hygiene_counts_sql("TRUE"),
        ana.hygiene_rows_sql("TRUE"),
        inspect.getsource(ana.hygiene_body),
    ])


def test_the_sql_never_names_the_private_overlay() -> None:
    """(e), the source half. D53: a private estimate is not the shared one."""
    assert "pm_task_personal" not in _hygiene_sql()
    for _, predicate in ana.HYGIENE_KINDS:
        assert "pm_task_personal" not in predicate


def test_the_fence_would_fire_on_the_private_overlay() -> None:
    """The fence's own fence: a predicate that joins the overlay is caught."""
    bad = "t.estimate_mins IS NULL AND NOT EXISTS (SELECT 1 FROM pm_task_personal)"
    assert "pm_task_personal" in bad


def test_the_stale_interval_goes_through_make_interval() -> None:
    """The asyncpg trap `stale_bands_sql` records: never a string interval."""
    sql = _hygiene_sql()
    assert "make_interval(days => :stale_days)" in sql
    assert "AS interval" not in sql


def test_the_no_assignee_predicate_does_not_filter_on_the_value() -> None:
    """(b), the source half. Any assignee row counts, `agent:` included."""
    predicate = dict(ana.HYGIENE_KINDS)["no_assignee"]
    assert "pm_task_assignees" in predicate
    assert "agent" not in predicate and "assignee" not in predicate.split("WHERE")[1]


def test_the_body_reads_loads_open_predicate_and_the_cap() -> None:
    body = inspect.getsource(ana.hygiene_body)
    assert "load_open_where(scope_sql, vis)" in body
    assert "load_params(vis, project_id)" in body
    assert "MAX_NAMED" in body
    # One cap. A second literal 20 would drift from MAX_NAMED.
    assert "20" not in body


def test_the_render_awaits_hygiene_body_with_no_period() -> None:
    source = inspect.getsource(rep.render_body)
    start = source.index("await hygiene_body(") + len("await hygiene_body(")
    call = source[start: source.index(")", start)]
    assert "include_subtree" in call, call
    assert "weeks" not in call and "skip" not in call, call


def test_hygiene_is_opt_in_between_stuck_and_conflicts() -> None:
    """(h)."""
    order = list(rep.SECTIONS)
    assert order.index("stuck") + 1 == order.index("hygiene")
    assert order.index("hygiene") + 1 == order.index("conflicts")
    assert "hygiene" not in rep.DEFAULT_SECTIONS
    assert "hygiene" not in rep.normalise_report_config({})["sections"]
    got = rep.normalise_report_config({"sections": ["conflicts", "hygiene", "stuck"]})
    assert got["sections"] == ["stuck", "hygiene", "conflicts"]


def test_t13_is_live_and_t7_waits_for_a_today_period() -> None:
    """(i)."""
    t13 = rep.TEMPLATES["data_hygiene"]
    assert t13["available"] is True
    assert t13["sections"] == ["hygiene"]
    assert (t13["weeks"], t13["skip_current_week"]) == (1, False)
    assert t13["scope_kinds"] == ["project", "org"]
    live = [k for k, t in rep.TEMPLATES.items() if t["available"]]
    assert live == ["weekly_delivery", "project_status", "data_hygiene"]
    t7 = rep.TEMPLATES["exceptions"]
    assert t7["available"] is False
    assert t7["waits_for"] == (
        "A today period, and a rule for which hygiene rows are high"
    )
    assert rep.normalise_report_config({"template": "data_hygiene"})["template"] == (
        "data_hygiene"
    )


# ── Hermetic: the chat card and the chat text ───────────────────────────────


def _section() -> dict[str, Any]:
    return {
        "open_total": 29, "stale_days": 14,
        "by_kind": {"no_assignee": 2, "no_due_date": 23, "no_estimate": 2,
                    "stale_in_progress": 1},
        "rows": [{
            "kind": "no_due_date",
            "id": "0b8c6f0e-1111-4c1e-9a54-000000000001",
            "title": "Order steel", "task_number": 7,
            "project_id": "0b8c6f0e-2222-4c1e-9a54-000000000002",
            "project_name": "Rig", "due_at": None,
            "updated_at": "2026-09-20T08:00:00+00:00",
        }],
    }


def test_the_hygiene_card_shows_four_counts_and_one_row_per_task() -> None:
    from skill_projects.views import REPORT_CARD_SECTIONS, _card_section

    stats, table = _card_section("hygiene", _section())
    assert stats == [
        {"label": "No assignee", "value": 2},
        {"label": "No due date", "value": 23},
        {"label": "No estimate", "value": 2},
        {"label": "Stale in progress", "value": 1},
    ]
    assert table is not None
    assert table["title"] == REPORT_CARD_SECTIONS["hygiene"]["title"] == "Data hygiene"
    assert table["columns"] == ["Missing", "Task", "Project", "Last change"]
    assert table["rows"] == [{"cells": ["No due date", "Order steel", "Rig", "2026-09-20"]}]


def test_the_chat_text_prints_plain_labels_and_no_id() -> None:
    from skill_projects.reads import _report_section

    lines = _report_section("hygiene", _section())
    assert lines[0] == (
        "hygiene: open tasks 29, no assignee 2, no due date 23, no estimate 2,"
        " stale in progress 1, stale after days 14"
    )
    assert lines[1] == (
        "- «Order steel» · kind no_due_date, project «Rig»,"
        " last change 2026-09-20T08:00:00+00:00"
    )
    text_out = "\n".join(lines)
    assert "0b8c6f0e" not in text_out


# ── H-186: four follow-ups from the R3b review ──────────────────────────────


def test_h186_1_a_title_that_starts_like_a_timestamp_prints_whole() -> None:
    from skill_projects.views import _card_cell

    title = "2026-10-01T-minus checklist"
    assert _card_cell("title", {"title": title}) == title
    row = {"updated_at": "2026-10-01T09:30:00+00:00"}
    assert _card_cell("updated_at", row) == "2026-10-01"


def _holderless() -> dict[str, Any]:
    return {
        "hr_visible": True, "horizon_days": 14, "at_risk_total": 1,
        "idle_total": 0, "pickups_total": 0,
        "at_risk": [{
            "task_id": "t1", "title": "Weld the gantry", "project_name": "Rig",
            "due_on": "2026-09-27", "shortfall_hours": 4.0,
            "holder": {"person_id": None, "name": "", "email": "hal@x.in"},
            "candidates": [],
        }],
        "pickups": [],
    }


def test_h186_4_a_holder_with_no_name_prints_the_address() -> None:
    from skill_projects.reads import _report_section
    from skill_projects.views import _card_section

    lines = _report_section("rebalance", _holderless())
    assert "held by «hal@x.in»" in lines[1], lines
    assert "«»" not in "\n".join(lines)
    _, table = _card_section("rebalance", _holderless())
    assert table is not None
    assert table["rows"][0]["cells"][1] == "hal@x.in"


def test_h186_4_every_label_is_scoped_to_its_section() -> None:
    """A bare key would label the same key in every section."""
    from skill_projects.reads import _REPORT_LABELS, _REPORT_SECTIONS

    for key in _REPORT_LABELS:
        section, _, figure = key.partition(":")
        assert figure, f"{key} is not scoped to a section"
        assert section in _REPORT_SECTIONS, key
        assert figure in _REPORT_SECTIONS[section][2], key


def test_h186_4_a_label_does_not_leak_into_another_section() -> None:
    from skill_projects.reads import _report_section

    lines = _report_section("mystery", {"at_risk_total": 3})
    assert lines == ["mystery: at_risk_total 3"]


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


#: Undated tasks beside the reader's one, so the project holds 23 (spec (g)).
UNDATED = ana.MAX_NAMED + 2


@pytest.fixture
def seeded(_ladder):
    """One project, a stopped child project, and one task for each case.

    * ``bare`` has no assignee and no estimate: two kinds.
    * ``undated`` is 22 assigned, estimated tasks with no due date, each
      created one minute before the next. With ``seen`` there are 23.
    * ``agent`` has only an ``agent:`` assignee.
    * ``sub`` is a subtask of ``agent`` with no assignee.
    * ``stale`` and ``fresh`` are in progress, last changed 15 and 13 days
      ago.
    * ``private`` has no shared estimate and a private one.
    * ``triage``, ``archived``, ``closed`` and ``stopped`` break every rule
      and must count nowhere.
    * ``seen`` is undated, and it is the one task the restricted reader holds.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    me = f"r3c-{tag}@example.test"
    reader = f"reader-{tag}@example.test"
    made: dict[str, Any] = {"me": me, "reader": reader, "projects": []}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org

        def project(name: str, *, parent: str | None, status: str) -> str:
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id, owns_statuses)"
                    " VALUES (:n, :st, 'manual', :me, CAST(:o AS uuid), 'UTC',"
                    " CAST(:par AS uuid), true) RETURNING id"
                ),
                {"n": f"{name}-{tag}", "st": status, "me": me, "o": org,
                 "par": parent},
            ).scalar_one())
            made["projects"].append(pid)
            return pid

        def status(pid: str, name: str, category: str, position: int) -> str:
            return str(c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id, name, color,"
                    " position, category) VALUES (CAST(:p AS uuid), :n, 'gray',"
                    " :pos, :cat) RETURNING id"
                ),
                {"p": pid, "n": name, "pos": position, "cat": category},
            ).scalar_one())

        root = project("r3c", parent=None, status="active")
        stopped = project("r3c-stopped", parent=root, status="stopped")
        made["project"] = root
        todo = status(root, "To do", "todo", 0)
        doing = status(root, "Doing", "in_progress", 1)
        done = status(root, "Done", "done", 2)
        triage = status(root, "Triage", "triage", 3)
        stopped_todo = status(stopped, "To do", "todo", 0)

        def task(title: str, *, st: str = todo, pid: str = root,
                 who: tuple[str, ...] = (me,), est: int | None = 60,
                 due: bool = True, parent: str | None = None,
                 age_days: int | None = None, born_mins_ago: int = 0) -> str:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, due_at, parent_task_id, created_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:r AS uuid),"
                    " CAST(:s AS uuid), :me, CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number), 0) + 1, :est,"
                    " CASE WHEN :due THEN now() + interval '5 days' END,"
                    " CAST(:parent AS uuid),"
                    " now() - make_interval(mins => CAST(:born AS int))"
                    " FROM pm_tasks WHERE root_project_id = CAST(:r AS uuid)"
                    " RETURNING id"
                ),
                {"t": f"{title} {tag}", "p": pid, "r": root, "s": st,
                 "me": me, "o": org, "est": est, "due": due,
                 "parent": parent, "born": born_mins_ago},
            ).scalar_one())
            for email in who:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a, :me)"
                    ),
                    {"t": tid, "a": email, "me": me},
                )
            if age_days is not None:
                c.execute(
                    text(
                        "UPDATE pm_tasks SET updated_at ="
                        " now() - make_interval(days => CAST(:d AS int))"
                        " WHERE id = CAST(:t AS uuid)"
                    ),
                    {"t": tid, "d": age_days},
                )
            return tid

        made["bare"] = task("bare", who=(), est=None)
        made["undated"] = [
            task(f"undated{i:02d}", due=False, born_mins_ago=100 + i)
            for i in range(UNDATED)
        ]
        made["agent"] = task("agent", who=("agent:planner",))
        made["sub"] = task("sub", who=(), parent=made["agent"])
        made["stale"] = task("stale", st=doing, age_days=15)
        made["fresh"] = task("fresh", st=doing, age_days=13)
        made["private"] = task("private", est=None)
        c.execute(
            text(
                "INSERT INTO pm_task_personal (task_id, member_email,"
                " time_estimate_mins, organization_id)"
                " VALUES (CAST(:t AS uuid), :m, 90, CAST(:o AS uuid))"
            ),
            {"t": made["private"], "m": me, "o": org},
        )
        made["excluded"] = {
            "triage": task("triage", st=triage, who=(), est=None, due=False),
            "archived": task("archived", who=(), est=None, due=False),
            "closed": task("closed", st=done, who=(), est=None, due=False),
            "stopped": task("stopped", st=stopped_todo, pid=stopped, who=(),
                            est=None, due=False),
        }
        c.execute(
            text("UPDATE pm_tasks SET archived_at = now() WHERE id = CAST(:t AS uuid)"),
            {"t": made["excluded"]["archived"]},
        )
        # The one task the restricted reader holds. It is undated too.
        made["seen"] = task("seen", who=(reader,), due=False)
    yield made
    with eng.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"),
                  {"p": made["project"]})
        for pid in reversed(made["projects"]):
            c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                      {"p": pid})
            c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                      {"p": pid})
    eng.dispose()


def _user() -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email="r3c@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))


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


def _report(project: str | None) -> dict[str, Any]:
    return asyncio.run(
        rep.preview_report(
            {"project_id": project, "config": {"sections": ["hygiene"]}},
            user=_user(),
        )
    )["sections"]["hygiene"]


def _body(wired: dict[str, Any], project: str | None, vis: Any = None) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with wired["engine"].connect() as db:
            return await ana.hygiene_body(
                db, vis or wired["vis"], project_id=project, include_subtree=True,
            )
    return asyncio.run(run())


def _ids(section: dict[str, Any], kind: str) -> set[str]:
    return {r["id"] for r in section["rows"] if r["kind"] == kind}


@_needs_db
@pytest.mark.parametrize("scope", ["project", "portfolio"])
def test_the_section_equals_hygiene_body(seeded, wired, scope) -> None:
    """(a) One computation, for one scope and one reader."""
    pid = seeded["project"] if scope == "project" else None
    got = _report(pid)
    assert got == _body(wired, pid)
    # Real rows, not two empty lists that agree by accident.
    assert got["open_total"] > 0 and got["rows"]


@_needs_db
def test_each_kind_holds_its_task_and_an_agent_is_an_assignee(seeded, wired) -> None:
    """(b), and the subtask counts as `load` counts it."""
    got = _report(seeded["project"])
    assert _ids(got, "no_assignee") == {seeded["bare"], seeded["sub"]}
    assert _ids(got, "no_estimate") == {seeded["bare"], seeded["private"]}
    assert _ids(got, "stale_in_progress") == {seeded["stale"]}
    # The oldest undated task: the cap keeps the oldest twenty.
    assert seeded["undated"][-1] in _ids(got, "no_due_date")
    # The agent's task breaks no rule, so it is in no row at all.
    assert seeded["agent"] not in {r["id"] for r in got["rows"]}
    # A task counts in each kind it breaks, so the kinds pass the total.
    assert got["by_kind"] == {
        "no_assignee": 2, "no_due_date": UNDATED + 1, "no_estimate": 2,
        "stale_in_progress": 1,
    }
    assert got["open_total"] == UNDATED + 7
    assert got["stale_days"] == ana.STALE_DAYS


@_needs_db
def test_triage_archived_closed_and_stopped_count_nowhere(seeded, wired) -> None:
    """(c) Each of the four breaks every rule, and none is in a row."""
    got = _report(seeded["project"])
    shown = {r["id"] for r in got["rows"]}
    for why, tid in seeded["excluded"].items():
        assert tid not in shown, why


@_needs_db
def test_the_14_day_boundary(seeded, wired) -> None:
    """(d) 15 days is stale. 13 days is not."""
    got = _report(seeded["project"])
    stale = _ids(got, "stale_in_progress")
    assert seeded["stale"] in stale
    assert seeded["fresh"] not in stale


@_needs_db
def test_a_private_estimate_does_not_fill_the_shared_one(seeded, wired) -> None:
    """(e) `pm_task_personal.time_estimate_mins` is 90, and the task still
    has no estimate."""
    got = _report(seeded["project"])
    assert seeded["private"] in _ids(got, "no_estimate")


@_needs_db
def test_a_task_the_reader_cannot_see_is_in_no_count(seeded, wired) -> None:
    """(f) The reader holds no grant and one task. Only that task counts."""
    from gateway.routes.projects.core import Visibility

    vis = Visibility(unrestricted=False, email=seeded["reader"], groups=(),
                     organization_id=seeded["org"])
    got = _body(wired, None, vis)
    assert got["open_total"] == 1
    assert got["by_kind"] == {
        "no_assignee": 0, "no_due_date": 1, "no_estimate": 0,
        "stale_in_progress": 0,
    }
    assert [r["id"] for r in got["rows"]] == [seeded["seen"]]


@_needs_db
def test_the_rows_are_capped_and_the_count_is_not(seeded, wired) -> None:
    """(g) 23 undated tasks, 20 rows, oldest first."""
    got = _report(seeded["project"])
    rows = [r for r in got["rows"] if r["kind"] == "no_due_date"]
    assert len(rows) == ana.MAX_NAMED == 20
    assert got["by_kind"]["no_due_date"] == 23
    # Oldest first: undated22 was created first, then undated21, and so on.
    want = list(reversed(seeded["undated"]))[: ana.MAX_NAMED]
    assert [r["id"] for r in rows] == want
    # The kinds come in HYGIENE_KINDS order.
    order = [r["kind"] for r in got["rows"]]
    assert order == sorted(order, key=KINDS.index)
