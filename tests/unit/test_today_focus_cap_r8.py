"""WS-MDC CC-2: the today's-focus mark and its cap (R8).

Spec: ``project-docs/specs/my_day_command_centre.md`` §9.2 and the third row of
§10 (D-MD-3). Metorite task 1969.

The mark is ``pm_task_personal.focus_on`` (migration 349). It takes the
member's today or null, and another date is a 422. ONE statement counts the
marks of the day and writes the mark, under a transaction lock of the org, the
member and the day (``personal.mark_focus``). A mark past the cap is a 409
``focus_cap_reached``. The One Thing never refuses, but it counts. A lower cap
keeps the marks that exist. No cap control ships, so the cap is set by SQL,
and ``PUT /tasks/settings`` clamps a write.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_today_focus_cap_r8.py -v -rs

**The mutations (R7).** Each one turned the named test red on 2026-11-07, and
each was restored. ``R`` is ``routes/projects/today_rule.py``, ``P`` is
``routes/projects/personal.py`` and ``S`` is ``routes/tasks/settings.py``.

* C1. ``R`` ``focus_room_sql``: ``<=`` for ``<``.
  ``test_a_mark_past_the_cap_answers_409_and_writes_nothing``.
* C2. ``R`` ``focus_room_sql``: no first ``EXISTS``.
  ``test_a_lower_cap_keeps_the_marks_and_refuses_new_ones`` (the re-mark).
* C3. ``R`` ``focus_count_sql``: no One Thing term.
  ``test_the_one_thing_counts_and_never_refuses``.
* C4. ``R`` ``focus_count_sql``: no ``NOT EXISTS`` of a marked One Thing.
  ``test_a_marked_one_thing_counts_once``.
* C10 (fix round 1, item 2). ``R`` ``focus_count_sql``: the One Thing term
  outside ``live_where``. ``test_a_one_thing_on_an_unseen_task_counts_as_a_random_id``.
* C11 (item 3). ``R`` ``focus_count_sql``: ``OPEN_SQL`` dropped.
  ``test_a_closed_mark_frees_its_place``.
* C12 (item 4). ``P`` ``check_focus_on``: no flag check.
  ``test_with_the_flag_off_a_mark_and_a_clear_are_refused``.
* C5. ``P`` ``mark_focus``: no ``FOCUS_LOCK_SQL``.
  ``test_a_second_mark_waits_for_the_first_to_commit``. The in-process client
  of ``test_marks_at_once_take_the_last_place_once`` did not race under this
  mutation, so that test is no fence of the lock.
* C6. ``P`` ``check_focus_on``: no date check.
  ``test_another_date_answers_422_and_writes_nothing``.
* C7. ``R`` ``clamp_focus_cap``: no clamp. ``test_the_cap_reads_null_as_3_and_clamps``.
* C8. ``S`` ``write_member_settings``: no clamp.
  ``test_the_settings_write_clamps_the_cap``.
* C9. ``R`` ``focus_count_sql``: no ``lower(f.member_email) = :focus_who``.
  ``test_the_mark_is_the_members_own``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings
from fastapi import HTTPException
from gateway.routes.projects import bulk, personal, today_rule
from sqlalchemy import text

from tests.unit import _today_rule_r8 as h
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

FIXTURE = h.cases()
ZONE, AT = FIXTURE["zone"], h.instant(FIXTURE["at"])
TODAY = date.fromisoformat(FIXTURE["today"])

MEMBER = "cc2cap-member@today.test"
OTHER = "cc2cap-other@today.test"
OWNER = "cc2cap-owner@today.test"
B_MEMBER = "cc2cap-b-member@today.test"


# ═══════════════════════════════════════════════════════════════════════════
# Hermetic: the clamp, the one writer, the bulk refusal and the order
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(("value", "cap"), [
    (None, 3), (0, 1), (-4, 1), (1, 1), (5, 5), (10, 10), (11, 10), (99, 10),
    ("7", 7), ("junk", 3), (True, 3),
])
def test_the_clamp_reads_null_as_3_and_holds_1_to_10(value: Any, cap: int) -> None:
    assert today_rule.clamp_focus_cap(value) == cap


async def test_the_one_writer_refuses_a_mark_with_no_guard() -> None:
    """A mark goes through ``mark_focus`` only. The refusal comes before any
    statement, so no database is needed."""
    with pytest.raises(ValueError, match="mark_focus"):
        await personal._upsert_personal(None, "t", "m@x.test", {"focus_on": TODAY})


def test_the_bulk_route_refuses_a_mark_and_a_clear() -> None:
    for value in ("2026-11-07", None):
        with pytest.raises(HTTPException) as refused:
            bulk.validate_personal("personal", personal.PersonalIn(focus_on=value))
        assert refused.value.status_code == 422


async def test_the_bulk_tool_refuses_focus_before_the_card() -> None:
    from skill_projects import guarded

    out = await guarded._bulk_personal(["a", "b"], json.dumps({"focus": "yes"}))
    assert "one task at a time" in out


def test_the_count_reads_the_filter_of_the_set() -> None:
    """Items 2 and 3 of fix round 1, the text half: the count of the cap and
    the set read ONE filter, so a closed or an unseen task never counts."""
    from gateway.routes.projects.core import Visibility

    vis = Visibility(unrestricted=False, email="m@x.test", groups=(), organization_id="o")
    assert today_rule.live_where(vis) in today_rule.focus_count_sql(vis)
    assert today_rule.live_where(vis) in today_rule.today_where(vis)
    assert today_rule.focus_count_sql(vis) in today_rule.focus_room_sql(vis)


# ═══════════════════════════════════════════════════════════════════════════
# R8: the mark and the cap on Postgres, as the app role
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    p = promoted
    for email in (MEMBER, OTHER, OWNER):
        h.member(p, p.org_a, email)
    h.member(p, p.org_b, B_MEMBER)
    shared = h.project(p, p.org_a, OWNER, grants=(MEMBER, OTHER))
    ns = type("World", (), {})()
    ns.p = p
    ns.tasks = [h.task(p, p.org_a, shared, by=OWNER, assignee=MEMBER, title=f"Task {i}")
                for i in range(12)]
    ns.member = h.person(MEMBER, p.org_a)
    ns.other = h.person(OTHER, p.org_a)
    proj_b = h.project(p, p.org_b, B_MEMBER, grants=(B_MEMBER,))
    ns.b_tasks = [h.task(p, p.org_b, proj_b, by=B_MEMBER, assignee=MEMBER) for _ in range(4)]
    return ns


@pytest.fixture
def w(world, monkeypatch):
    """Each test starts at the fixture's instant with no mark, no One Thing,
    the default cap and the flag ON for org A (fix round 1, item 4: a mark
    needs the flag)."""
    monkeypatch.setattr(today_rule, "clock", lambda: AT)
    monkeypatch.setattr(get_settings(), "today_rule_orgs", world.p.org_a)
    p = world.p
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_task_personal"))
        c.execute(text("DELETE FROM calendar_day_state"))
    h.settings(p, p.org_a, MEMBER, zone=ZONE)
    h.settings(p, p.org_a, OTHER, zone=ZONE)
    return world


async def _mark(call: Any, user: Any, tid: str, value: Any = FIXTURE["today"], **more: Any):
    return await call(user, "PATCH", f"/projects/tasks/{tid}/personal",
                      {"focus_on": value, **more})


def _marks(w: Any, email: str = MEMBER) -> set[str]:
    found = h.rows(w.p, "SELECT task_id FROM pm_task_personal WHERE member_email = :m"
                   " AND focus_on = :d", m=email, d=TODAY)
    return {str(r.task_id) for r in found}


async def _state(call: Any, user: Any) -> dict[str, Any]:
    got = await call(user, "GET", "/projects/my/today?with_tasks=true")
    assert got.status_code == 200, got.text
    return got.json()


def _cap(w: Any, value: int | None) -> None:
    h.settings(w.p, w.p.org_a, MEMBER, zone=ZONE, cap=value)


@_DB_GATE
class TestTheMarkOnARealDatabase:
    async def test_the_route_runs_as_a_role_that_cannot_bypass_rls(self, w, app_engine):  # noqa: F811
        with app_engine.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).first()
        assert role is not None and not role[0] and not role[1]

    async def test_a_mark_of_today_lands_and_reads_back(self, w):
        tid = w.tasks[0]
        async with h.session(w.p) as call:
            got = await _mark(call, w.member, tid)
            assert got.status_code == 200, got.text
            assert got.json()["focus_on"] == FIXTURE["today"]
            state = await _state(call, w.member)
        assert _marks(w) == {tid}
        assert h.rows_by_task(state)[tid]["why"] == ["focus"]
        assert (state["focus_cap"], state["focus_count"]) == (3, 1)

    async def test_another_date_answers_422_and_writes_nothing(self, w):
        tid = w.tasks[0]
        async with h.session(w.p) as call:
            for value in ("2026-11-06", "2026-11-08", "2026-11-07T00:00:00", "junk", 7):
                got = await _mark(call, w.member, tid, value, context="@desk")
                assert got.status_code == 422, (value, got.text)
        assert not h.rows(w.p, "SELECT 1 FROM pm_task_personal WHERE task_id = :t",
                          t=tid), "a refused mark writes no overlay row"

    async def test_null_clears_the_mark(self, w):
        tid = w.tasks[0]
        async with h.session(w.p) as call:
            assert (await _mark(call, w.member, tid)).status_code == 200
            cleared = await _mark(call, w.member, tid, None)
            assert cleared.status_code == 200 and cleared.json()["focus_on"] is None
        assert _marks(w) == set()

    async def test_a_mark_past_the_cap_answers_409_and_writes_nothing(self, w):
        first, last = w.tasks[:3], w.tasks[3]
        async with h.session(w.p) as call:
            for tid in first:
                assert (await _mark(call, w.member, tid)).status_code == 200
            refused = await _mark(call, w.member, last, context="@desk")
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert detail["error"] == today_rule.FOCUS_CAP_REACHED
        assert (detail["focus_cap"], detail["focus_count"]) == (3, 3)
        assert "Unmark one first" in detail["message"]
        assert _marks(w) == set(first)
        # The whole PATCH rolls back: no row, so no context either.
        assert not h.rows(w.p, "SELECT 1 FROM pm_task_personal WHERE task_id = :t", t=last)

    async def test_the_cap_reads_null_as_3_and_clamps(self, w):
        async with h.session(w.p) as call:
            for stored, read in ((None, 3), (0, 1), (5, 5), (50, 10)):
                _cap(w, stored)
                assert (await _state(call, w.member))["focus_cap"] == read, stored
            _cap(w, 1)
            assert (await _mark(call, w.member, w.tasks[0])).status_code == 200
            assert (await _mark(call, w.member, w.tasks[1])).status_code == 409

    async def test_a_lower_cap_keeps_the_marks_and_refuses_new_ones(self, w):
        a, b, c, d = w.tasks[:4]
        async with h.session(w.p) as call:
            for tid in (a, b, c):
                assert (await _mark(call, w.member, tid)).status_code == 200
            _cap(w, 2)
            state = await _state(call, w.member)
            assert (state["focus_cap"], state["focus_count"]) == (2, 3)
            rows = h.rows_by_task(state)
            assert all(rows[t]["why"] == ["focus"] for t in (a, b, c)), "the marks stay"
            assert (await _mark(call, w.member, d)).status_code == 409
            assert (await _mark(call, w.member, a)).status_code == 200, "a kept mark is not new"
            assert (await _mark(call, w.member, a, None)).status_code == 200
            assert (await _mark(call, w.member, d)).status_code == 409, "2 is not under 2"
            assert (await _mark(call, w.member, b, None)).status_code == 200
            assert (await _mark(call, w.member, d)).status_code == 200
        assert _marks(w) == {c, d}

    async def test_the_one_thing_counts_and_never_refuses(self, w):
        a, b, chosen, d = w.tasks[:4]
        _cap(w, 2)
        async with h.session(w.p) as call:
            assert (await _mark(call, w.member, a)).status_code == 200
            assert (await _mark(call, w.member, b)).status_code == 200
            # The day is full, and the One Thing still lands (the real route).
            put = await call(w.member, "PUT", "/tasks/calendar/day-state",
                             {"day": FIXTURE["today"], "one_thing_id": chosen})
            assert put.status_code == 200, put.text
            state = await _state(call, w.member)
            assert state["focus_count"] == 3
            assert h.rows_by_task(state)[chosen]["why"] == ["one_thing"]
            assert (await _mark(call, w.member, d)).status_code == 409
            # A mark on the One Thing adds nothing to the count, so it lands.
            assert (await _mark(call, w.member, chosen)).status_code == 200
            assert (await _state(call, w.member))["focus_count"] == 3

    async def test_a_marked_one_thing_counts_once(self, w):
        a = w.tasks[0]
        h.one_thing(w.p, w.p.org_a, MEMBER, TODAY, a)
        async with h.session(w.p) as call:
            assert (await _state(call, w.member))["focus_count"] == 1
            assert (await _mark(call, w.member, a)).status_code == 200
            state = await _state(call, w.member)
        assert state["focus_count"] == 1
        assert h.rows_by_task(state)[a]["why"] == ["one_thing", "focus"]

    async def test_marks_at_once_take_the_last_place_once(self, w):
        """With one place left, eight marks at once give one 200 and seven 409s."""
        taken, racing = w.tasks[:2], w.tasks[2:10]
        async with h.session(w.p) as call:
            for tid in taken:
                assert (await _mark(call, w.member, tid)).status_code == 200
            answers = await asyncio.gather(*(_mark(call, w.member, t) for t in racing))
        codes = sorted(a.status_code for a in answers)
        assert codes == [200] + [409] * 7, [a.text for a in answers]
        assert len(_marks(w)) == 3

    async def test_a_second_mark_waits_for_the_first_to_commit(self, w):
        """Two marks of a day never count at the same time. The first mark holds
        the lock of the day in an open transaction. The second mark waits for
        it, then counts the first, and the day is full (409). With no lock, the
        second mark counts before the first commits, and the day holds 4."""
        from acb_common.db import tenant_session
        from gateway.routes.projects.core import Visibility

        org = w.p.org_a
        vis = Visibility(unrestricted=False, email=MEMBER, groups=(), organization_id=org)
        first, second = w.tasks[2], w.tasks[3]
        async with h.session(w.p) as call:
            for tid in w.tasks[:2]:
                assert (await _mark(call, w.member, tid)).status_code == 200
            opened = tenant_session(org)
            db = await opened.__aenter__()
            try:
                await personal.mark_focus(db, vis, first, MEMBER, TODAY)

                async def _second() -> None:
                    async with tenant_session(org) as other:
                        await personal.mark_focus(other, vis, second, MEMBER, TODAY)

                waiting = asyncio.create_task(_second())
                await asyncio.sleep(1.0)
                assert not waiting.done(), "the second mark ran before the first committed"
            finally:
                await opened.__aexit__(None, None, None)
            with pytest.raises(HTTPException) as refused:
                await asyncio.wait_for(waiting, timeout=30)
            assert refused.value.status_code == 409
        assert _marks(w) == {*w.tasks[:2], first}

    async def test_the_mark_is_the_members_own(self, w):
        """The other member fills their own day. It takes no place of mine, and
        my read shows no mark of theirs."""
        async with h.session(w.p) as call:
            for tid in w.tasks[:3]:
                assert (await _mark(call, w.other, tid)).status_code == 200
            mine = await _state(call, w.member)
            assert mine["focus_count"] == 0
            assert not any("focus" in r["why"] for r in mine["rows"])
            for tid in w.tasks[3:6]:
                assert (await _mark(call, w.member, tid)).status_code == 200
        assert _marks(w, OTHER) == set(w.tasks[:3])
        assert _marks(w, MEMBER) == set(w.tasks[3:6])

    async def test_marks_of_organization_b_never_count_in_a(self, w):
        """Org B holds rows of my address with a mark of today. They take no
        place of mine in A, and a mark in A never reaches a task of B."""
        for tid in w.b_tasks:
            h.overlay(w.p, w.p.org_b, tid, MEMBER, focus_on=TODAY)
        async with h.session(w.p) as call:
            assert (await _state(call, w.member))["focus_count"] == 0
            for tid in w.tasks[:3]:
                assert (await _mark(call, w.member, tid)).status_code == 200
            assert (await _mark(call, w.member, w.b_tasks[0])).status_code == 404

    async def test_the_settings_write_clamps_the_cap(self, w):
        async with h.session(w.p) as call:
            for sent, stored in ((50, 10), (0, 1), (4, 4)):
                got = await call(w.member, "PUT", "/tasks/settings", {"today_focus_cap": sent})
                assert got.status_code == 200, got.text
                assert got.json()["today_focus_cap"] == stored
                row = h.rows(w.p, "SELECT today_focus_cap FROM user_settings WHERE user_id = :u"
                             " AND organization_id = CAST(:o AS uuid)", u=MEMBER, o=w.p.org_a)
                assert row[0].today_focus_cap == stored
            _cap(w, 99)
            got = await call(w.member, "GET", "/tasks/settings")
            assert got.json()["today_focus_cap"] == 10, "a read clamps again"

    async def test_with_the_flag_off_a_mark_and_a_clear_are_refused(self, w, monkeypatch):
        """Fix round 1, item 4: ship dark. With the org off the list, the one
        write seam refuses a mark and a clear, says why and what to do, and
        writes nothing. The read still answers."""
        tid = w.tasks[0]
        h.overlay(w.p, w.p.org_a, tid, MEMBER, focus_on=TODAY)
        monkeypatch.setattr(get_settings(), "today_rule_orgs", "")
        async with h.session(w.p) as call:
            for value in (FIXTURE["today"], None):
                got = await _mark(call, w.member, tid, value, context="@desk")
                assert got.status_code == 403, got.text
                detail = got.json()["detail"]
                assert detail["error"] == today_rule.FOCUS_OFF
                assert detail["message"].startswith(
                    "Today's focus is not on for your organization yet.")
            # A PATCH with no focus_on still works with the flag off.
            assert (await call(w.member, "PATCH", f"/projects/tasks/{tid}/personal",
                               {"context": "@desk"})).status_code == 200
            assert (await _state(call, w.member))["focus_count"] == 1
        assert _marks(w) == {tid}, "the refused clear kept the mark"

    async def test_a_one_thing_on_an_unseen_task_counts_as_a_random_id(self, w):
        """Fix round 1, item 2. ``put_day_state`` stores any id. A One Thing on
        a task that I cannot see must count, and refuse, exactly as a random
        id does, so the cap tells nothing of the unseen task."""
        import uuid

        p = w.p
        hidden = h.project(p, p.org_a, OWNER)
        unseen = h.task(p, p.org_a, hidden, by=OWNER)
        outcomes = []
        _cap(w, 2)
        async with h.session(p) as call:
            for one_thing in (str(uuid.uuid4()), unseen):
                with w.p.admin_engine.begin() as c:
                    c.execute(text("UPDATE pm_task_personal SET focus_on = NULL"))
                put = await call(w.member, "PUT", "/tasks/calendar/day-state",
                                 {"day": FIXTURE["today"], "one_thing_id": one_thing})
                assert put.status_code == 200, put.text
                first = (await _mark(call, w.member, w.tasks[0])).status_code
                count = (await _state(call, w.member))["focus_count"]
                second = (await _mark(call, w.member, w.tasks[1])).status_code
                outcomes.append((first, count, second))
        assert outcomes[0] == outcomes[1] == (200, 1, 200), outcomes

    async def test_a_closed_mark_frees_its_place(self, w):
        """Fix round 1, item 3. Cap 3, marks on A, B and C, all three
        completed: a mark on D lands, because the count reads only open tasks."""
        p = w.p
        shared = h.project(p, p.org_a, OWNER, grants=(MEMBER,))
        a, b, c, d = (h.task(p, p.org_a, shared, by=OWNER, assignee=MEMBER) for _ in range(4))
        async with h.session(p) as call:
            for tid in (a, b, c):
                assert (await _mark(call, w.member, tid)).status_code == 200
            assert (await _mark(call, w.member, d)).status_code == 409
            for tid in (a, b, c):
                done = await call(w.member, "POST", f"/projects/tasks/{tid}/complete", {})
                assert done.status_code == 200, done.text
            assert (await _state(call, w.member))["focus_count"] == 0
            assert (await _mark(call, w.member, d)).status_code == 200
