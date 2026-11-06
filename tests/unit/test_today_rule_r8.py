"""WS-MDC CC-2: the one Today predicate, its read and its flag (R8).

Spec: ``project-docs/specs/my_day_command_centre.md`` §9.2 and the first row of
§10 (D-MD-2). Metorite task 1969.

Two halves. The hermetic tests need no database: the route, the flag, and the
``due`` arm of the shared fixture against ``mine.in_today``. The R8 tests run
the real ``/projects`` and ``/tasks`` routers on the phase-4-promoted catalog
as the NON-privileged app role, so FORCE RLS holds.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_today_rule_r8.py -v -rs

**The mutations (R7).** Each one turned the named test red on 2026-11-07, and
each was restored. ``R`` is ``routes/projects/today_rule.py`` and ``M`` is
``routes/projects/mine.py``.

* T1. ``R`` ``ARMS_SQL``: no ``FOCUS_SQL``. The focus cases of
  ``test_each_case_of_the_fixture_reads_its_why_through_the_route``.
* T2. ``R`` ``SCHEDULED_SQL``: ``>`` for ``>=`` on ``:day_start``. The case
  "my block starts at my midnight of today" of
  ``test_each_case_of_the_fixture_reads_its_why_through_the_route``.
* T3. ``R`` ``ONE_THING_SQL``: no ``lower(d.user_id) = :who``.
  ``test_the_marks_of_another_member_never_reach_mine``.
* T4. ``R`` ``today_where``: no ``task_visibility_clause``.
  ``test_a_task_that_i_cannot_see_is_out_even_with_my_mark``. FORCE RLS still
  hides org B under this mutation, so ``test_organization_b_never_shows_in_a``
  holds the tenant and not the grant.
* T5. ``R`` ``OPEN_SQL``: no trash test. "a focus mark on a task in my trash".
* T6. ``R`` ``to_fit``: every row. ``test_the_tray_is_the_set_minus_the_blocks_of_today``.
* T7. ``M`` ``my_summary``: the count reads ``in_today`` with the flag on.
  ``test_with_the_flag_on_the_chip_counts_and_lists_the_set``.
* T8. ``M`` ``my_summary``: the ids are sent with the flag off.
  ``test_with_the_flag_off_the_chip_keeps_the_old_rule``.
* T9. ``R`` ``due_arm_sql``: ``mine_clause`` with no arm (all three).
  ``test_the_due_arm_reuses_the_assigned_arm_of_mine``.
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import env_guard, get_settings
from acb_common.settings import Settings
from gateway.routes.projects import core, mine, personal, today_rule
from sqlalchemy import text

from tests.unit import _today_rule_r8 as h
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
FIXTURE = h.cases()
ZONE, AT = FIXTURE["zone"], h.instant(FIXTURE["at"])
TODAY = date.fromisoformat(FIXTURE["today"])

MEMBER = "cc2-member@today.test"
OTHER = "cc2-other@today.test"
OWNER = "cc2-owner@today.test"
B_MEMBER = "cc2-b-member@today.test"
SUMMARY = "cc2-summary@today.test"


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic: the route, the flag, and the due arm of the fixture
# ═══════════════════════════════════════════════════════════════════════════


def test_the_read_is_mounted_with_one_new_parameter() -> None:
    from gateway.routes.projects import router

    mounted = {(m, r.path) for r in router.routes for m in getattr(r, "methods", set())}
    assert ("GET", "/projects/my/today") in mounted
    # R5, R11: the member comes from the session. The one new word is a switch.
    assert set(inspect.signature(personal.my_today).parameters) == {"user", "with_tasks"}


def test_the_flag_ships_empty_and_a_tenant_cannot_write_it() -> None:
    assert Settings.model_fields["today_rule_orgs"].default == ""
    assert "TODAY_RULE_ORGS" in set(env_guard.PLATFORM_ENV_NAMES)
    env = (_REPO / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^TODAY_RULE_ORGS=$", env, re.MULTILINE)
    doc = (_REPO / "docs" / "flag_reload.md").read_text(encoding="utf-8")
    assert "TODAY_RULE_ORGS" in doc


def test_the_flag_reads_the_org_or_the_star(monkeypatch) -> None:
    settings = get_settings()
    org = "6f1d0b8a-1c2e-4a4b-9d3e-2f6a1b0c9d8e"
    monkeypatch.setattr(settings, "today_rule_orgs", "")
    assert not today_rule.today_rule_allowed(org)
    monkeypatch.setattr(settings, "today_rule_orgs", f"x, {org.upper()}")
    assert today_rule.today_rule_allowed(org)
    assert not today_rule.today_rule_allowed("another")
    assert not today_rule.today_rule_allowed(None)
    monkeypatch.setattr(settings, "today_rule_orgs", "*")
    assert today_rule.today_rule_allowed(org)
    assert not today_rule.today_rule_allowed(None), "no org never opens it"


def test_the_due_arm_reuses_the_assigned_arm_of_mine() -> None:
    """The arm of ``in_today`` is "assigned to me", the ``assigned`` arm of the
    one definition of mine, never a copy of it."""
    arm = today_rule.due_arm_sql("t")
    assert core.mine_clause("t", arm="assigned") in arm
    assert core.mine_clause("t", arm="personal") not in arm
    assert today_rule.DUE_SQL in today_rule.ARMS_SQL


def test_the_window_is_midnight_to_midnight_of_the_zone() -> None:
    window = today_rule.window_of(ZONE, AT)
    assert window.today == TODAY
    assert window.day_start == datetime(2026, 11, 6, 18, 30, tzinfo=UTC)
    assert window.day_end == datetime(2026, 11, 7, 18, 30, tzinfo=UTC)
    assert today_rule.window_of("Mars/Olympus", AT).zone == "UTC"


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_the_fixture_agrees_with_in_today_and_the_disposition(case: dict[str, Any]) -> None:
    """The ``due`` arm alone is the old rule, ``mine.in_today``. The fixture's
    effective disposition is ``effective_disposition`` of its stated one, so
    the client half reads what the server sends."""
    t = case["task"]
    mine_now = t["assignee"] == "me"
    effective = personal.effective_disposition(
        t["stated"], status_category=t["category"], is_mine=mine_now,
        has_assignee=t["assignee"] is not None,
    )
    assert effective == t["effective"]
    assert mine.in_today(
        effective=effective, is_mine=mine_now,
        completed_at=AT if t["completed"] else None,
        defer_until=h.instant(t["defer_until"]), start_date=h.day(t["start_date"]),
        due_at=h.instant(t["due_at"]), today=TODAY, zone=ZONE,
    ) is ("due" in case["why"])
    assert case["why"] == [w for w in today_rule.WHY_ORDER if w in case["why"]]


def test_the_fixture_holds_each_arm_alone() -> None:
    alone = {c["why"][0] for c in FIXTURE["cases"] if len(c["why"]) == 1}
    assert alone == set(today_rule.WHY_ORDER)


# ═══════════════════════════════════════════════════════════════════════════
# R8: the predicate on Postgres, as the app role
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Org A: the member, another member and the owner of a shared project.
    Org B: one member. Each case of the fixture is a task in the shared
    project, which grants the member and the other member."""
    p = promoted
    for email in (MEMBER, OTHER, OWNER, SUMMARY):
        h.member(p, p.org_a, email)
    h.member(p, p.org_b, B_MEMBER)
    h.settings(p, p.org_a, MEMBER, zone=ZONE)
    h.settings(p, p.org_a, OTHER, zone=ZONE)
    shared = h.project(p, p.org_a, OWNER, grants=(MEMBER, OTHER))
    hidden = h.project(p, p.org_a, OWNER)
    ns = type("World", (), {})()
    ns.p, ns.shared, ns.hidden = p, shared, hidden
    ns.member = h.person(MEMBER, p.org_a)
    ns.other = h.person(OTHER, p.org_a)
    ns.b_member = h.person(B_MEMBER, p.org_b)
    ns.cases: dict[str, dict[str, Any]] = {}
    for case in FIXTURE["cases"]:
        t, m = case["task"], case["mine"]
        assignee = {"me": MEMBER, "other": OTHER, None: None}[t["assignee"]]
        tid = h.task(
            p, p.org_a, shared, by=OWNER, category=t["category"], assignee=assignee,
            completed_at=AT if t["completed"] else None, start_date=h.day(t["start_date"]),
            due_at=h.instant(t["due_at"]), title=case["name"][:80],
        )
        cols = {
            "disposition": t["stated"], "defer_until": h.instant(t["defer_until"]),
            "focus_on": h.day(m["focus_on"]), "scheduled_start": h.instant(m["scheduled_start"]),
            "scheduled_end": h.instant(m["scheduled_end"]),
        }
        if any(v is not None for v in cols.values()):
            h.overlay(p, p.org_a, tid, MEMBER, **cols)
        ns.cases[tid] = case
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """The world at the fixture's instant, with the flag off and no One Thing."""
    monkeypatch.setattr(today_rule, "clock", lambda: AT)
    monkeypatch.setattr(get_settings(), "today_rule_orgs", "")
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM calendar_day_state"))
    return world


async def _today(call: Any, user: Any) -> dict[str, Any]:
    got = await call(user, "GET", "/projects/my/today?with_tasks=true")
    assert got.status_code == 200, got.text
    return got.json()


@_DB_GATE
class TestTheTodayPredicateOnARealDatabase:
    async def test_the_route_runs_as_a_role_that_cannot_bypass_rls(self, w, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).first()
            forced = c.execute(text(
                "SELECT count(*) FROM pg_class WHERE relname IN "
                "('pm_tasks', 'pm_task_personal', 'calendar_day_state', 'user_settings') "
                "AND relrowsecurity AND relforcerowsecurity")).scalar()
        assert role is not None and not role[0] and not role[1]
        assert forced == 4

    async def test_each_case_of_the_fixture_reads_its_why_through_the_route(self, w):
        """The union of the four arms, case by case. A One Thing case reads
        with that task as the day's One Thing, and every other case with none."""
        async with h.session(w.p) as call:
            got = h.rows_by_task(await _today(call, w.member))
            for tid, case in w.cases.items():
                if case["mine"]["one_thing"]:
                    continue
                assert got.get(tid, {}).get("why", []) == case["why"], case["name"]
            for tid, case in w.cases.items():
                if not case["mine"]["one_thing"]:
                    continue
                h.one_thing(w.p, w.p.org_a, MEMBER, TODAY, tid)
                again = h.rows_by_task(await _today(call, w.member))
                assert again.get(tid, {}).get("why", []) == case["why"], case["name"]

    async def test_a_row_carries_the_task_its_block_and_why(self, w):
        async with h.session(w.p) as call:
            answer = await _today(call, w.member)
        assert {"rows", "total", "to_fit", "focus_cap", "focus_count"} <= set(answer)
        assert answer["today"] == FIXTURE["today"] and answer["timezone"] == ZONE
        assert answer["total"] == len(answer["rows"])
        assert answer["focus_cap"] == today_rule.DEFAULT_FOCUS_CAP
        by_id = h.rows_by_task(answer)
        for tid, case in w.cases.items():
            row = by_id.get(tid)
            if row is None:
                continue
            assert set(row) == {"task", "block", "why"}
            assert row["task"]["id"] == tid and row["task"]["disposition"] == case["task"]["effective"]
            assert row["task"]["focus_on"] == case["mine"]["focus_on"]
            start = case["mine"]["scheduled_start"]
            if start is None:
                assert row["block"] is None
            else:
                assert h.instant(row["block"]["start"]) == h.instant(start)
                assert h.instant(row["block"]["end"]) == h.instant(case["mine"]["scheduled_end"])
        # The count of the cap: each mark of today, open or closed, and no One
        # Thing (none is set). A case in the trash and a closed case count.
        marks = h.rows(
            w.p, "SELECT count(*) AS n FROM pm_task_personal f JOIN pm_tasks t ON t.id = f.task_id"
            " WHERE f.member_email = :m AND f.focus_on = :d AND t.organization_id = CAST(:o AS uuid)",
            m=MEMBER, d=TODAY, o=w.p.org_a)[0].n
        assert answer["focus_count"] == marks
        assert marks >= sum(c["mine"]["focus_on"] == FIXTURE["today"] for c in FIXTURE["cases"])

    async def test_the_tray_is_the_set_minus_the_blocks_of_today(self, w):
        async with h.session(w.p) as call:
            answer = await _today(call, w.member)
        in_set = [str(r["task"]["id"]) for r in answer["rows"]]
        want = [str(r["task"]["id"]) for r in answer["rows"] if "scheduled" not in r["why"]]
        assert answer["to_fit"] == want and want
        for tid, case in w.cases.items():
            if "scheduled" in case["why"]:
                assert tid in in_set and tid not in answer["to_fit"], case["name"]
            elif case["why"] and not case["mine"]["one_thing"]:
                assert tid in answer["to_fit"], case["name"]

    async def test_without_with_tasks_the_answer_keeps_its_five_keys(self, w):
        async with h.session(w.p) as call:
            got = await call(w.member, "GET", "/projects/my/today")
        assert got.status_code == 200
        assert set(got.json()) == {"today", "timezone", "stored", "valid", "day_start_hour"}

    async def test_an_archived_or_parked_task_is_out(self, w):
        p = w.p
        archived = h.task(p, p.org_a, w.shared, by=OWNER, assignee=MEMBER, due_at=AT,
                          archived=True)
        shelf = h.project(p, p.org_a, OWNER, grants=(MEMBER,), archived=True)
        on_shelf = h.task(p, p.org_a, shelf, by=OWNER, assignee=MEMBER, due_at=AT)
        parked = h.task(p, p.org_a, w.shared, by=OWNER, category="triage", assignee=MEMBER,
                        due_at=AT)
        for tid in (archived, on_shelf, parked):
            h.overlay(p, p.org_a, tid, MEMBER, focus_on=TODAY)
        async with h.session(p) as call:
            got = h.rows_by_task(await _today(call, w.member))
        assert not {archived, on_shelf, parked} & set(got)

    async def test_a_task_that_i_cannot_see_is_out_even_with_my_mark(self, w):
        """The overlay narrows and never widens: my row on a task of a project
        I hold no grant on, and am not assigned in, shows nothing."""
        p = w.p
        tid = h.task(p, p.org_a, w.hidden, by=OWNER, due_at=AT)
        h.overlay(p, p.org_a, tid, MEMBER, focus_on=TODAY,
                  scheduled_start=AT, scheduled_end=AT + timedelta(hours=1))
        h.one_thing(p, p.org_a, MEMBER, TODAY, tid)
        async with h.session(p) as call:
            got = h.rows_by_task(await _today(call, w.member))
        assert tid not in got

    async def test_a_reader_of_the_whole_org_reads_the_same_arms(self, w):
        """``data:org:read`` widens what the member may see, inside the tenant.
        The set binds every name of that clause too, and each case keeps its
        arms."""
        wide = h.person(MEMBER, w.p.org_a, unrestricted=True)
        async with h.session(w.p) as call:
            got = h.rows_by_task(await _today(call, wide))
        for tid, case in w.cases.items():
            if not case["mine"]["one_thing"]:
                assert got.get(tid, {}).get("why", []) == case["why"], case["name"]

    async def test_organization_b_never_shows_in_a(self, w):
        """Org B holds a task assigned to the address of A's member, with a
        mark, a block and a day state of that address. A's member reads none
        of it, and B's member reads none of A."""
        p = w.p
        proj_b = h.project(p, p.org_b, B_MEMBER, grants=(B_MEMBER,))
        tid_b = h.task(p, p.org_b, proj_b, by=B_MEMBER, assignee=MEMBER, due_at=AT)
        h.overlay(p, p.org_b, tid_b, MEMBER, focus_on=TODAY, scheduled_start=AT,
                  scheduled_end=AT + timedelta(hours=1))
        own_b = h.task(p, p.org_b, proj_b, by=B_MEMBER, assignee=B_MEMBER, due_at=AT)
        async with h.session(p) as call:
            a_rows = h.rows_by_task(await _today(call, w.member))
            b_rows = h.rows_by_task(await _today(call, w.b_member))
        assert tid_b not in a_rows and own_b not in a_rows
        assert set(b_rows) == {own_b}, "B reads only its own task"
        assert not set(w.cases) & set(b_rows)

    async def test_the_marks_of_another_member_never_reach_mine(self, w):
        """The other member's mark, block and One Thing on shared tasks put the
        tasks in THEIR set, never in mine."""
        p = w.p
        marked = h.task(p, p.org_a, w.shared, by=OWNER, assignee=OTHER)
        blocked = h.task(p, p.org_a, w.shared, by=OWNER, assignee=OTHER)
        chosen = h.task(p, p.org_a, w.shared, by=OWNER, assignee=OTHER)
        h.overlay(p, p.org_a, marked, OTHER, focus_on=TODAY)
        h.overlay(p, p.org_a, blocked, OTHER, scheduled_start=AT,
                  scheduled_end=AT + timedelta(hours=1))
        h.one_thing(p, p.org_a, OTHER, TODAY, chosen)
        async with h.session(p) as call:
            mine_rows = h.rows_by_task(await _today(call, w.member))
            theirs = await _today(call, w.other)
        assert not {marked, blocked, chosen} & set(mine_rows)
        by_id = h.rows_by_task(theirs)
        assert by_id[marked]["why"] == ["focus"]
        assert by_id[blocked]["why"] == ["scheduled"]
        assert by_id[chosen]["why"] == ["one_thing"]
        assert theirs["focus_count"] == 2, "the mark plus the One Thing with no mark"

    async def test_with_the_flag_off_the_chip_keeps_the_old_rule(self, w):
        """``by_disposition.today`` is the count of ``in_today``, and the answer
        names no set, so nothing a member sees changes."""
        world = await _summary_world(w)
        async with h.session(w.p) as call:
            got = await call(world.user, "GET", "/projects/my/summary")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["by_disposition"]["today"] == 1, "the overdue task only"
        assert "today_rule" not in body and "today_ids" not in body

    async def test_with_the_flag_on_the_chip_counts_and_lists_the_set(self, w, monkeypatch):
        world = await _summary_world(w)
        monkeypatch.setattr(get_settings(), "today_rule_orgs", w.p.org_a)
        # The summary reads the database clock, so the read does too.
        monkeypatch.setattr(today_rule, "clock", lambda: datetime.now(UTC))
        async with h.session(w.p) as call:
            got = await call(world.user, "GET", "/projects/my/summary")
            today = await _today(call, world.user)
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["today_rule"] is True
        assert sorted(body["today_ids"]) == sorted(world.in_set)
        assert body["by_disposition"]["today"] == len(world.in_set) == today["total"]
        assert sorted(str(r["task"]["id"]) for r in today["rows"]) == sorted(world.in_set)


async def _summary_world(w: Any) -> Any:
    """A member in UTC with three tasks of the real today: one overdue (both
    rules), one marked (the new rule only) and one blocked (the new rule
    only), and one with no date (neither)."""
    p = w.p
    with p.admin_engine.begin() as c:
        # A world of an earlier test goes on the shelf, so its tasks leave
        # both the summary and the set.
        c.execute(text("UPDATE pm_projects SET archived_at = now() WHERE created_by = :e"),
                  {"e": SUMMARY})
    h.settings(p, p.org_a, SUMMARY, zone="UTC")
    proj = h.project(p, p.org_a, SUMMARY, grants=(SUMMARY,))
    now = datetime.now(UTC)
    real_today = now.date()
    overdue = h.task(p, p.org_a, proj, by=SUMMARY, assignee=SUMMARY,
                     due_at=now - timedelta(days=2))
    marked = h.task(p, p.org_a, proj, by=SUMMARY, assignee=SUMMARY)
    blocked = h.task(p, p.org_a, proj, by=SUMMARY, assignee=SUMMARY)
    h.task(p, p.org_a, proj, by=SUMMARY, assignee=SUMMARY)
    h.overlay(p, p.org_a, marked, SUMMARY, focus_on=real_today)
    noon = datetime.combine(real_today, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12)
    h.overlay(p, p.org_a, blocked, SUMMARY, scheduled_start=noon,
              scheduled_end=noon + timedelta(hours=1))
    ns = type("Summary", (), {})()
    ns.user = h.person(SUMMARY, p.org_a)
    ns.in_set = [overdue, marked, blocked]
    return ns
