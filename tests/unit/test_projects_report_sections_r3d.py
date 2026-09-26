"""WS-27bn R3d — the pulse section and T1.

Spec: ``project-docs/specs/projects_reports.md`` §8 R3d, done-when (a) to (m).

**The claim is one computation over the capacity rows.** ``render_body``
awaits ``analytics_pulse.pulse_body``, which keeps the ``kind == "person"``
rows of ``capacity_body`` for the same scope, reader and UTC ``today``. On a
real database:

* (a) each card of an admin reader has the ``pill`` of ``capacity_body``;
* (b) a full absence today gives ``status`` ``on_leave`` and ``has_room``
  false, and a partial absence keeps the pill;
* (c) owner Q6: an admin never sees another member's waiting items, or a
  task that member scheduled today. The member's own render shows both;
* (d) edit E5: a reader with no grant gets their own row only, and no other
  address appears anywhere in the body;
* (e) an admin sees ``hidden_people`` 0, and a reader who holds no work gets
  no row;
* (g) a task that a closed task blocks is not blocked;
* (h) 15 days in progress is stale, and 13 days is not;
* (i) each reason fires, and ``help_note`` travels;
* (j) seven focus tasks give five rows and ``focus_total`` 7.

The hermetic half pins (f), (g) and (h) in the source, (k) T1, and the chat.

⚠️ The R8 half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not
a pass. ``bash scripts/dev_db.sh`` brings the database up.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects import analytics as ana
from gateway.routes.projects import analytics_capacity as cap_mod
from gateway.routes.projects import analytics_pulse as pul
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

TODAY = date(2026, 9, 26)


def _cap_row(**over: Any) -> dict[str, Any]:
    """A capacity row with its HR half, as `capacity_body` sends it."""
    row: dict[str, Any] = {
        "assignee": "ana@example.test", "name": "Ana", "kind": "person",
        "in_directory": True, "open_tasks": 4, "overdue": 1, "due_next_7d": 1,
        "later": 2, "estimated_hours_left": 3.0, "estimated": 2,
        "all_work": {"open_tasks": 4, "overdue": 1, "unestimated": 0,
                     "in_progress": 1},
        "contracted_hours_per_week": 40.0, "working_hours_this_week": 40.0,
        "working_hours_horizon": 80.0, "committed_hours_this_week": 3.0,
        "committed_hours_horizon": 3.0, "spare_hours_this_week": 37.0,
        "spare_hours_horizon": 77.0, "hours_basis": True, "hours_note": None,
        "absences": [], "end_date": None, "leaving_in_window": False,
        "at_risk": [], "pill": "idle", "pill_reason": "3h of 40h.",
        "flags": ["idle"], "max_concurrent_tasks": None,
        "over_concurrency": False, "skills": [],
    }
    row.update(over)
    return row


# ── Hermetic: the order and T1 ───────────────────────────────────────────────


def test_pulse_is_opt_in_between_capacity_and_stuck() -> None:
    order = list(rep.SECTIONS)
    assert order.index("capacity") + 1 == order.index("pulse")
    assert order.index("pulse") + 1 == order.index("stuck")
    assert "pulse" not in rep.DEFAULT_SECTIONS
    assert "pulse" not in rep.normalise_report_config({})["sections"]
    got = rep.normalise_report_config({"sections": ["stuck", "pulse", "capacity"]})
    assert got["sections"] == ["capacity", "pulse", "stuck"]


def test_t1_is_live_with_pulse_conflicts_and_rebalance() -> None:
    """(k)."""
    t1 = rep.TEMPLATES["team_pulse"]
    assert t1["available"] is True
    assert t1["sections"] == ["pulse", "conflicts", "rebalance"]
    assert (t1["weeks"], t1["skip_current_week"]) == (1, False)
    assert t1["scope_kinds"] == ["project", "org"]
    assert "waits_for" not in t1
    live = [k for k, t in rep.TEMPLATES.items() if t["available"]]
    assert live == ["team_pulse", "weekly_delivery", "project_status", "data_hygiene"]
    assert rep.normalise_report_config({"template": "team_pulse"})["template"] == (
        "team_pulse"
    )


def test_the_render_awaits_pulse_body_with_the_reader_and_no_period() -> None:
    source = inspect.getsource(rep.render_body)
    start = source.index("await pulse_body(") + len("await pulse_body(")
    call = source[start: source.index(")", start)]
    assert "user" in call and "include_subtree" in call, call
    assert "weeks" not in call and "skip" not in call, call


# ── Hermetic: (g) blocked, (h) stale, has room, one rule each ────────────────


def _code(source: str) -> str:
    """The source without comments and docstrings, so prose cannot pass."""
    source = re.sub(r'"""[\s\S]*?"""', "", source)
    return re.sub(r"#[^\n]*", "", source)


def test_blocked_is_one_predicate_in_analytics() -> None:
    """(g), the source half. The link type is written once, in
    `blocked_clause`, and the route and the section both call it."""
    module = inspect.getsource(ana)
    assert module.count("link_type = 'blocks'") == 1
    assert "link_type = 'blocks'" in inspect.getsource(ana.blocked_clause)
    assert "blocked_clause()" in _code(inspect.getsource(ana.stuck))
    assert "blocked_clause()" in _code(inspect.getsource(pul.pulse_counts_sql))
    assert "pm_task_links" not in _code(inspect.getsource(pul))


def test_stale_is_the_hygiene_predicate_and_pulse_names_no_day_number() -> None:
    """(h), the source half."""
    assert pul._STALE == dict(ana.HYGIENE_KINDS)["stale_in_progress"]
    code = _code(inspect.getsource(pul))
    assert "STALE_DAYS" in code
    # No day count of its own: no 13, 14, 15, 30, and no interval literal.
    assert not re.search(r"\b(1[0-9]|[2-9][0-9])\b", code), re.findall(
        r"\b(1[0-9]|[2-9][0-9])\b", code
    )
    assert "make_interval" not in code and "interval '" not in code


def test_has_room_is_the_idle_flag_and_adds_no_threshold() -> None:
    code = _code(inspect.getsource(pul))
    assert '"idle" in' in code
    assert "IDLE_FRACTION" not in code and "0.25" not in code


def test_the_private_overlay_is_read_for_the_reader_only() -> None:
    """(c), the source half. Every read of `pm_task_personal` binds `:me`,
    which is the reader, and never the address of the row."""
    sql = "\n".join([
        pul._MINE_TODAY, pul.pulse_waiting_sql("TRUE"),
        pul.pulse_focus_sql("TRUE"), pul.pulse_counts_sql("TRUE"),
    ])
    reads = sql.count("FROM pm_task_personal")
    assert reads > 0
    assert sql.count("lower(tp.member_email) = :me") == reads
    body = _code(inspect.getsource(pul.pulse_body))
    assert '"me": me' in body and "getattr(user, \"email\"" in body


# ── Hermetic: (f) and (i), the row builder ───────────────────────────────────


def test_a_row_without_the_grant_carries_the_task_half_only() -> None:
    """(f). No key of HR_KEYS, no status, no has_room, no private key."""
    row = pul.pulse_row(_cap_row(), hr=False, today=TODAY, blocked=1)
    assert not set(row) & set(cap_mod.HR_KEYS), row
    assert "status" not in row and "has_room" not in row
    assert not set(row) & set(pul.PRIVATE_KEYS)
    assert set(pul.PULSE_HR_KEYS[:5]) <= set(cap_mod.HR_KEYS)
    assert row["help_reasons"] == ["blocked"]


def test_the_fence_would_fire_on_a_leaked_hr_key() -> None:
    row = pul.pulse_row(_cap_row(), hr=True, today=TODAY)
    assert set(row) & set(cap_mod.HR_KEYS)


def test_a_row_with_the_grant_carries_the_hr_half() -> None:
    row = pul.pulse_row(_cap_row(), hr=True, today=TODAY)
    assert row["pill"] == "idle" and row["status"] == "idle"
    assert row["has_room"] is True
    assert row["committed_hours_this_week"] == 3.0
    assert row["working_hours_this_week"] == 40.0


@pytest.mark.parametrize(
    ("kwargs", "want"),
    [
        ({"blocked": 1}, ["blocked"]),
        ({"stale": 2}, ["stale"]),
        ({"waiting": [{"id": "w"}], "waiting_count": 1}, ["waiting_overdue"]),
        ({}, []),
        ({"waiting": [], "waiting_count": 0}, []),
    ],
)
def test_each_reason_fires_alone(kwargs: dict[str, Any], want: list[str]) -> None:
    """(i)."""
    row = pul.pulse_row(_cap_row(), hr=True, today=TODAY, **kwargs)
    assert row["help_reasons"] == want
    assert row["needs_help"] is bool(want)


def test_the_leave_check_parses_the_iso_strings() -> None:
    """(b), the unit half. `capacity_body` sends strings, and `absent_on`
    drops a span that does not hold dates."""
    full = [{"kind": "away", "starts_on": "2026-09-25", "ends_on": "2026-09-27"}]
    part = [{"kind": "partial", "starts_on": "2026-09-26", "ends_on": "2026-09-26"}]
    assert pul.on_leave(full, TODAY) is True
    assert pul.on_leave(part, TODAY) is False
    assert pul.on_leave([], TODAY) is False
    row = pul.pulse_row(_cap_row(absences=full), hr=True, today=TODAY)
    assert row["status"] == "on_leave" and row["has_room"] is False
    assert row["pill"] == "idle"
    row = pul.pulse_row(_cap_row(absences=part), hr=True, today=TODAY)
    assert row["status"] == "idle" and row["has_room"] is True


# ── Hermetic: the chat card and the chat text ───────────────────────────────


def _section(hidden: int = 2) -> dict[str, Any]:
    row = pul.pulse_row(
        _cap_row(assignee="bea@example.test", name="Bea"), hr=True,
        today=TODAY, blocked=1, focus=[], focus_total=3,
        waiting=[{"id": "w"}], waiting_count=1,
    )
    return {
        "today": "2026-09-26", "stale_days": 14, "hr_visible": False,
        "people_total": 3, "hidden_people": hidden,
        "help_note": pul.HELP_NOTE, "rows": [row],
    }


def test_the_pulse_card_shows_one_row_per_card_and_the_hidden_line() -> None:
    """(d), the card half."""
    from skill_projects.views import REPORT_CARD_SECTIONS, _card_section

    stats, table = _card_section("pulse", _section())
    assert stats == [{"label": "People", "value": 3}]
    assert table is not None
    assert table["title"] == REPORT_CARD_SECTIONS["pulse"]["title"] == "Team pulse"
    assert table["columns"] == ["Person", "Status", "Open", "Focus", "Needs help"]
    assert table["rows"] == [
        {"cells": ["Bea", "Idle", "4", "3", "Blocked, Waiting past its date"]},
        {"cells": ["This report hides 2 other people", "", "", "", ""]},
    ]


def test_the_card_prints_no_status_for_a_row_without_one() -> None:
    from skill_projects.views import _card_section

    section = _section(hidden=0)
    section["rows"] = [pul.pulse_row(_cap_row(), hr=False, today=TODAY)]
    _, table = _card_section("pulse", section)
    assert table is not None
    assert table["rows"] == [{"cells": ["Ana", "", "4", "0", ""]}]


def test_the_chat_text_prints_the_hidden_line_and_no_id() -> None:
    """(d), the chat half."""
    from skill_projects.reads import _report_section

    lines = _report_section("pulse", _section())
    assert lines[0] == "pulse: people 3, hidden people 2"
    assert lines[1] == "  This report hides 2 other people"
    assert lines[2].startswith("- «Bea» · status idle, open 4, overdue 1, blocked 1")
    assert "waiting past its date 1" in lines[2]
    one = _report_section("pulse", _section(hidden=1))
    assert one[1] == "  This report hides 1 other person"
    assert not any("hides" in line for line in _report_section("pulse", _section(0)))


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
    """Two projects and six people.

    * ``ana`` is in the directory, holds one task and is AWAY today.
    * ``cy`` is in the directory, holds two tasks and has a PARTIAL absence
      today. One of her tasks is the one ``bea`` waits on.
    * ``bea`` is the member reader. She holds a task in progress that an
      open task blocks and that changed 13 days ago, one that a CLOSED task
      blocks, one in progress that changed 15 days ago, and one she
      scheduled for today. She waits on ``cy`` for a task, past its date.
    * ``dan`` holds seven focus tasks and one task due in three days.
    * ``boss`` is the admin reader and ``nobody`` holds no work.
    * ``trio`` is a second project with three holders: bea, cy and ana.
    """
    from sqlalchemy import create_engine

    eng = create_engine(_TENANT_URL, future=True)
    tag = uuid.uuid4().hex[:8]
    who = {k: f"{k}-{tag}@example.test"
           for k in ("ana", "bea", "cy", "dan", "boss", "nobody")}
    made: dict[str, Any] = {"who": who, "projects": [], "people": [], "tag": tag}
    with eng.begin() as c:
        org = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org"] = org

        def project(name: str) -> tuple[str, dict[str, str]]:
            pid = str(c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by,"
                    " organization_id, timezone, parent_project_id, owns_statuses)"
                    " VALUES (:n, 'active', 'manual', :me, CAST(:o AS uuid), 'UTC',"
                    " NULL, true) RETURNING id"
                ),
                {"n": f"{name}-{tag}", "me": who["boss"], "o": org},
            ).scalar_one())
            made["projects"].append(pid)
            st = {}
            for pos, (label, cat) in enumerate((("To do", "todo"),
                                                ("Doing", "in_progress"),
                                                ("Done", "done"))):
                st[cat] = str(c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id, name, color,"
                        " position, category) VALUES (CAST(:p AS uuid), :n,"
                        " 'gray', :pos, :cat) RETURNING id"
                    ),
                    {"p": pid, "n": label, "pos": pos, "cat": cat},
                ).scalar_one())
            return pid, st

        main, st = project("pulse")
        trio, trio_st = project("trio")
        made["project"], made["trio"] = main, trio

        def task(title: str, *, pid: str = main, cat: str = "todo",
                 holders: tuple[str, ...] = (), due_days: int | None = None,
                 statuses: dict[str, str] | None = None) -> str:
            tid = str(c.execute(
                text(
                    "INSERT INTO pm_tasks (title, project_id, root_project_id,"
                    " status_id, created_by, organization_id, task_number,"
                    " estimate_mins, due_at)"
                    " SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid),"
                    " CAST(:s AS uuid), :me, CAST(:o AS uuid),"
                    " COALESCE(MAX(task_number), 0) + 1, 60,"
                    " CASE WHEN CAST(:d AS int) IS NULL THEN NULL"
                    "      ELSE now() + make_interval(days => CAST(:d AS int)) END"
                    " FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"
                    " RETURNING id"
                ),
                {"t": f"{title} {tag}", "p": pid, "s": (statuses or st)[cat],
                 "me": who["boss"], "o": org, "d": due_days},
            ).scalar_one())
            for h in holders:
                c.execute(
                    text(
                        "INSERT INTO pm_task_assignees (task_id, assignee,"
                        " assigned_by) VALUES (CAST(:t AS uuid), :a, :me)"
                    ),
                    {"t": tid, "a": who[h], "me": who["boss"]},
                )
            return tid

        def age(tid: str, days: int) -> None:
            c.execute(
                text(
                    "UPDATE pm_tasks SET updated_at ="
                    " now() - make_interval(days => CAST(:d AS int))"
                    " WHERE id = CAST(:t AS uuid)"
                ),
                {"t": tid, "d": days},
            )

        def link(source: str, target: str) -> None:
            c.execute(
                text(
                    "INSERT INTO pm_task_links (source_task_id, target_task_id,"
                    " link_type, created_by) VALUES (CAST(:s AS uuid),"
                    " CAST(:t AS uuid), 'blocks', :me)"
                ),
                {"s": source, "t": target, "me": who["boss"]},
            )

        def person(key: str) -> str:
            pid = str(c.execute(
                text(
                    "INSERT INTO people (id, name, email, status, skills, source,"
                    " source_key, organization_id, updated_by, updated_at)"
                    " VALUES (gen_random_uuid(), :n, :e, 'active', ARRAY[]::text[],"
                    " 'manual', :k, CAST(:o AS uuid), 'test', now()) RETURNING id"
                ),
                {"n": f"{key.title()} {tag}", "e": who[key],
                 "k": f"manual:{key}-{tag}", "o": org},
            ).scalar_one())
            made["people"].append(pid)
            return pid

        def absence(person_id: str, kind: str, first: int, last: int) -> None:
            c.execute(
                text(
                    "INSERT INTO people_absences (person_id, organization_id,"
                    " starts_on, ends_on, kind, created_by) VALUES"
                    " (CAST(:p AS uuid), CAST(:o AS uuid),"
                    " (now() AT TIME ZONE 'UTC')::date + CAST(:a AS int),"
                    " (now() AT TIME ZONE 'UTC')::date + CAST(:b AS int),"
                    " :k, 'test')"
                ),
                {"p": person_id, "o": org, "a": first, "b": last, "k": kind},
            )

        def overlay(tid: str, member: str, **cols: str) -> None:
            names = ", ".join(cols)
            values = ", ".join(cols.values())
            c.execute(
                text(
                    f"INSERT INTO pm_task_personal (task_id, member_email,"
                    f" organization_id, {names}) VALUES (CAST(:t AS uuid), :m,"
                    f" CAST(:o AS uuid), {values})"
                ),
                {"t": tid, "m": who[member], "o": org},
            )

        absence(person("ana"), "away", 0, 1)
        absence(person("cy"), "partial", 0, 0)
        person("bea")

        made["ana_task"] = task("ana plans", holders=("ana",), due_days=5)
        made["cy_task"] = task("cy builds", holders=("cy",), due_days=9)
        made["delegated"] = task("cy delegated", holders=("cy",), due_days=10)

        blocker = task("open blocker", due_days=20)
        done_blocker = task("closed blocker", cat="done")
        made["bea_doing"] = task("bea doing", cat="in_progress", holders=("bea",))
        made["bea_free"] = task("bea free", holders=("bea",), due_days=10)
        made["bea_stale"] = task("bea stale", cat="in_progress", holders=("bea",))
        made["bea_sched"] = task("bea scheduled", holders=("bea",), due_days=10)
        link(blocker, made["bea_doing"])
        link(done_blocker, made["bea_free"])
        age(made["bea_doing"], 13)
        age(made["bea_stale"], 15)
        overlay(made["bea_sched"], "bea",
                scheduled_start="date_trunc('day', now() AT TIME ZONE 'UTC')"
                                " AT TIME ZONE 'UTC' + interval '12 hours'")
        overlay(made["delegated"], "bea",
                waiting_on="""'{"name": "Cy"}'::jsonb""",
                delegated_at="now() - interval '3 days'",
                expected_by="now() - interval '1 day'")

        made["dan_focus"] = (
            [task(f"dan doing {i}", cat="in_progress", holders=("dan",))
             for i in range(3)]
            + [task(f"dan due {i}", holders=("dan",), due_days=1) for i in range(4)]
        )
        made["dan_later"] = task("dan later", holders=("dan",), due_days=3)

        for h in ("bea", "cy", "ana"):
            task(f"trio {h}", pid=trio, holders=(h,), due_days=8,
                 statuses=trio_st)
    yield made
    with eng.begin() as c:
        for pid in made["projects"]:
            c.execute(text("DELETE FROM pm_tasks WHERE root_project_id = CAST(:p AS uuid)"),
                      {"p": pid})
            c.execute(text("DELETE FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"),
                      {"p": pid})
            c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"),
                      {"p": pid})
        for pid in made["people"]:
            c.execute(text("DELETE FROM people WHERE id = CAST(:p AS uuid)"), {"p": pid})
    eng.dispose()


def _user(email: str, *, admin: bool) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    grants = ["feature:projects"] + (["admin:members:read"] if admin else [])
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(grants))


@pytest.fixture
def wired(seeded, monkeypatch):
    """Bind the report route to one engine, with a whole-tenant visibility."""
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


def _report(seeded: dict[str, Any], reader: str, *, admin: bool,
            project: str | None) -> dict[str, Any]:
    return asyncio.run(
        rep.preview_report(
            {"project_id": project, "config": {"sections": ["pulse"]}},
            user=_user(seeded["who"][reader], admin=admin),
        )
    )["sections"]["pulse"]


def _card(section: dict[str, Any], seeded: dict[str, Any], key: str) -> dict[str, Any]:
    address = seeded["who"][key]
    return next(r for r in section["rows"] if r["assignee"] == address)


def _ids(items: list[dict[str, Any]]) -> set[str]:
    return {i["id"] for i in items}


@_needs_db
def test_each_card_has_the_pill_of_capacity_body(seeded, wired) -> None:
    """(a) One computation: same person, scope, reader and today."""
    got = _report(seeded, "boss", admin=True, project=seeded["project"])
    today = datetime.now(UTC).date()

    async def run() -> dict[str, Any]:
        async with wired["engine"].connect() as db:
            return await cap_mod.capacity_body(
                db, wired["vis"], hr_visible=True, project_id=seeded["project"],
                include_subtree=True, today=today,
            )
    cap = asyncio.run(run())
    pills = {r["assignee"]: r["pill"] for r in cap["rows"] if r["kind"] == "person"}
    assert got["rows"], "no card came back"
    assert {r["assignee"] for r in got["rows"]} == set(pills)
    for row in got["rows"]:
        assert row["pill"] == pills[row["assignee"]], row["assignee"]
    # The agent and the unassigned row get no card, and the blocker is
    # unassigned work in this scope.
    assert all(r["assignee"] for r in got["rows"])
    assert got["today"] == today.isoformat()


@_needs_db
def test_a_full_absence_is_on_leave_and_a_partial_one_keeps_the_pill(seeded, wired) -> None:
    """(b) On real absences, which `capacity_body` sends as ISO strings."""
    got = _report(seeded, "boss", admin=True, project=seeded["project"])
    ana_card = _card(got, seeded, "ana")
    assert ana_card["status"] == "on_leave"
    assert ana_card["has_room"] is False
    assert ana_card["pill"] != "on_leave"
    cy = _card(got, seeded, "cy")
    assert cy["status"] == cy["pill"]


@_needs_db
def test_an_admin_never_sees_another_members_private_notes(seeded, wired) -> None:
    """(c) Owner Q6. The admin's view of Bea, then Bea's own view."""
    admin = _card(_report(seeded, "boss", admin=True, project=seeded["project"]),
                  seeded, "bea")
    assert "waiting" not in admin and "waiting_count" not in admin
    assert seeded["bea_sched"] not in _ids(admin["focus"])
    assert "waiting_overdue" not in admin["help_reasons"]
    assert "scheduled_today" not in json.dumps(admin)

    own = _report(seeded, "bea", admin=False, project=seeded["project"])
    bea = _card(own, seeded, "bea")
    assert bea["waiting_count"] == 1
    assert _ids(bea["waiting"]) == {seeded["delegated"]}
    assert seeded["bea_sched"] in _ids(bea["focus"])
    sched = next(f for f in bea["focus"] if f["id"] == seeded["bea_sched"])
    assert sched["scheduled_today"] is True
    assert bea["help_reasons"] == ["blocked", "stale", "waiting_overdue"]
    # The self door: the member reads the HR half of her own card.
    assert "pill" in bea and "status" in bea and "has_room" in bea


@_needs_db
@pytest.mark.parametrize("scope", ["trio", "portfolio"])
def test_a_member_sees_only_her_own_card_and_no_other_address(seeded, wired, scope) -> None:
    """(d) Edit E5. The filter runs before the body leaves the server."""
    project = seeded["trio"] if scope == "trio" else None
    got = _report(seeded, "bea", admin=False, project=project)
    assert [r["assignee"] for r in got["rows"]] == [seeded["who"]["bea"]]
    assert got["hidden_people"] == got["people_total"] - 1
    if scope == "trio":
        assert (got["people_total"], got["hidden_people"]) == (3, 2)
    body = json.dumps(got)
    for other in ("ana", "cy", "dan", "boss"):
        assert seeded["who"][other] not in body, other
        assert f"{other.title()} {seeded['tag']}" not in body, other


@_needs_db
def test_an_admin_hides_nobody_and_a_reader_with_no_work_gets_no_row(seeded, wired) -> None:
    """(e)."""
    admin = _report(seeded, "boss", admin=True, project=seeded["project"])
    assert admin["hidden_people"] == 0
    assert admin["people_total"] == len(admin["rows"]) == 4
    nobody = _report(seeded, "nobody", admin=False, project=seeded["project"])
    assert nobody["rows"] == []
    assert nobody["hidden_people"] == nobody["people_total"] == 4


@_needs_db
def test_a_closed_blocker_does_not_block(seeded, wired) -> None:
    """(g) `bea doing` waits on an open task. `bea free` waits on a closed one."""
    bea = _card(_report(seeded, "boss", admin=True, project=seeded["project"]),
                seeded, "bea")
    assert bea["blocked_count"] == 1


@_needs_db
def test_the_14_day_boundary(seeded, wired) -> None:
    """(h) 15 days in progress is stale. 13 days is not."""
    bea = _card(_report(seeded, "boss", admin=True, project=seeded["project"]),
                seeded, "bea")
    assert bea["stale_count"] == 1


@_needs_db
def test_the_reasons_and_the_help_note(seeded, wired) -> None:
    """(i) On the real body: a person with no reason needs no help."""
    got = _report(seeded, "boss", admin=True, project=seeded["project"])
    assert got["help_note"] == pul.HELP_NOTE
    assert "two runs in a row" in got["help_note"]
    bea = _card(got, seeded, "bea")
    assert bea["help_reasons"] == ["blocked", "stale"]
    assert bea["needs_help"] is True
    ana_card = _card(got, seeded, "ana")
    assert ana_card["help_reasons"] == [] and ana_card["needs_help"] is False


@_needs_db
def test_seven_focus_tasks_give_five_rows(seeded, wired) -> None:
    """(j) A task due in three days, and not in progress, is not focus."""
    dan = _card(_report(seeded, "boss", admin=True, project=seeded["project"]),
                seeded, "dan")
    assert dan["focus_total"] == 7
    assert len(dan["focus"]) == pul.FOCUS_MAX == 5
    assert _ids(dan["focus"]) <= set(seeded["dan_focus"])
    assert seeded["dan_later"] not in _ids(dan["focus"])
    # The soonest due date first, and undated work last.
    assert dan["focus"][0]["due_at"] is not None
