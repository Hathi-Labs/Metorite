"""RX-5, R8: a due date is a day, and the morning summary push (frozen clock).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.11.

The reminder loop, ``reach/due_push.py`` and the push channel run on a phase-4
promoted scratch database, as the non-privileged app role, so FORCE RLS binds.
Two orgs. Each tick takes a frozen ``now``. The sender is real, and only the
push service is fake, at the door of ``outbound_guard.request_https``.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_due_push_r8.py -v -rs

**The mutations (R7).** Each turned the named test red. Commit before each.

* M1, the uniqueness. ``due_push.dedupe_key``: add the time of the tick.
  ``test_r8_a_retry_plans_one_row``,
  ``test_r8_two_processes_plan_one_row``.
* M2, the tenant bind. ``due_push._plan_one``: open the write on the session of
  the other org. ``test_r8_each_org_plans_only_its_own_members``.
* M3, the overdue rule. ``due_day.is_overdue``: ``due < now``, the rule of
  D-PM-22. ``test_r8_a_task_due_today_at_noon_at_18_00_local_reads_due_today``.
* M4, the overdue rule in SQL. ``due_day.overdue_sql``: ``due_at < now()``.
  ``test_r8_the_sql_rule_and_the_python_rule_agree_at_the_day_edge``.
* M5, the window. ``due_push._open_slots``: drop the ``too_late`` check.
  ``test_r8_a_day_start_missed_by_hours_plans_nothing``.
* M6, the empty read. ``due_push._plan_one``: drop the mark.
  ``test_r8_nothing_due_is_no_row_and_the_day_stays_quiet``.
* M7, the flag. ``flags.due_push_allowed``: answer True.
  ``test_r8_the_flag_fails_closed``.
* M8, the date seam. ``personal.stored_due``: return the value as it came.
  ``test_r8_each_write_of_a_chat_date_reaches_the_seam`` and
  ``test_r8_a_date_only_write_is_noon_in_the_zone_of_the_member``.
* M9, the reader's day. ``tasks.list_tasks``: drop ``day_start`` from the
  filter. ``analytics.stuck``: bind the UTC day.
  ``test_r8_a_route_counts_late_work_by_the_day_of_its_reader``.
* M10, the case of the zone. ``personal.stored_zone``: match ``user_id = :uid``.
  ``test_r8_a_mixed_case_row_keeps_its_zone``.
* M11, the order of the send. ``outbox._PLANNED_PUSHES_SQL``: drop the first
  sort key. ``test_r8_a_reminder_goes_before_a_morning_of_summaries``.
* M12, the quiet tick. ``due_push._open_slots``: read ``member_day`` for each
  member with no row. ``test_r8_a_quiet_tick_costs_a_few_statements``.
* M13, the seed. ``due_push._open_slots``: read no seed.
  ``test_r8_a_member_with_no_row_starts_at_the_people_seed``.
* M14, the deadline of a plan. ``due_push.plan_org``: drop the check of
  ``stopping``. ``test_r8_a_plan_out_of_time_stops_between_two_members``.
* M15, the seam of a bulk edit. ``bulk.bulk_edit``: drop the ``stored_due``
  call. ``test_r8_each_write_of_a_chat_date_reaches_the_seam``.
* M16, the seam of a mail draft. ``capture_email._draft_due``: return the bare
  ``_parse_dt``. ``test_r8_a_mail_draft_of_a_date_is_noon_of_the_member``.
* M17, the digest. ``digest._overdue``: ``due < at``.
  ``test_r8_a_commitment_reads_late_by_the_zone_of_its_owner``, and
  ``test_email_digest.py::TestTheOneRuleOfLateWork``.
* M18, the share. ``push_channel.send_planned``: drop the check of
  ``summaries_over``. ``test_r8_a_summary_past_its_share_waits_for_a_later_tick``,
  and ``test_due_day.py::TestTheSendShare``.
* M19, the turn. ``due_push._take``: start at 0.
  ``test_r8_with_redis_down_a_long_list_reaches_the_last_member``, and
  ``test_due_day.py::TestALongList``.
* M20, the working day. ``due_push._day_off``: answer an empty set.
  ``test_r8_no_summary_on_a_weekend_and_monday_lists_it``,
  ``test_r8_no_summary_on_a_leave_day_and_the_next_day_lists_it``,
  ``test_r8_no_summary_on_a_company_holiday``.
* M21, the partial day. ``_AWAY_ON_SQL``: add ``'partial'``.
  ``test_r8_a_partial_day_off_still_gets_the_summary``.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import get_current_user
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, get_engine, release_tenant, tenant_session
from fastapi import FastAPI
from gateway import outbound_guard
from gateway.reach import due_push, outbox
from gateway.reach.push_message import DUE_TAG, DUE_TTL_S
from gateway.reminders import loop
from gateway.routes import projects as projects_package
from gateway.routes.email import digest
from gateway.routes.projects import bulk, due_day, intake, personal, tasks
from gateway.routes.projects import reminders as rm
from gateway.routes.projects.analytics import load_sql, overdue_by_project_sql
from gateway.routes.shell import cache, feed, needs
from gateway.routes.shell.state import Day
from gateway.routes.tasks import capture_email
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import _exec_file, tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_push_platform import vapid_pair
from tests.unit.test_push_subscriptions import _Browser
from tests.unit.test_reach_push import _dsn, _endpoint, _PushService, _subscribe
from tests.unit.test_shell_needs_r8 import _project, _stated, _task

pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

IST = ZoneInfo("Asia/Kolkata")
LA = ZoneInfo("America/Los_Angeles")
SUBJECT = "mailto:push@rx5.test"


def _ist(day: int, hour: int, minute: int = 0) -> datetime:
    """An instant of March 2099 in Kolkata. Tuesday 10 March is day 10."""
    return datetime(2099, 3, day, hour, minute, tzinfo=IST)


def _la(day: int, hour: int, minute: int = 0) -> datetime:
    """An instant of March 2099 in Los Angeles, on summer time from day 8."""
    return datetime(2099, 3, day, hour, minute, tzinfo=LA)


# ── The migration ──────────────────────────────────────────────────────────


def _the_migration() -> Path:
    found = [f for f in _MIGRATIONS.glob("[0-9]*_*.sql")
             if "reach_outbox_kind_known" in (body := f.read_text(encoding="utf-8"))
             and "'due_summary'" in body]
    assert len(found) == 1, found
    return found[0]


class TestTheMigration:
    def test_the_check_takes_the_new_kind_and_keeps_the_old(self, promoted):  # noqa: F811
        p = promoted
        ok = ("INSERT INTO reach_outbox (organization_id, member_email, channel, kind, "
              "dedupe_key, body) VALUES (CAST(:o AS uuid), 'mig@rx5.test', :ch, :k, :key, 'x')")
        tag = uuid.uuid4().hex[:8]
        with p.admin_engine.begin() as c:
            for n, kind in enumerate(("due_summary", "reminder_due", "reminder_check",
                                      "reminder_unconfirmed", "brief", "interrupt")):
                c.execute(text(ok), {"o": p.org_a, "ch": "web_push", "k": kind,
                                     "key": f"mig-{tag}-{n}"})
            c.execute(text(ok), {"o": p.org_a, "ch": "whatsapp", "k": "request",
                                 "key": f"mig-{tag}-wa"})
            c.execute(text("DELETE FROM reach_outbox WHERE member_email = 'mig@rx5.test'"))
        with pytest.raises(DBAPIError, match="reach_outbox_kind_known"), \
                p.admin_engine.begin() as c:
            c.execute(text(ok), {"o": p.org_a, "ch": "web_push", "k": "due_summery",
                                 "key": f"mig-{tag}-bad"})

    def test_a_second_run_changes_nothing(self, promoted):  # noqa: F811
        p = promoted
        defs = ("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'reach_outbox'::regclass AND contype = 'c' ORDER BY 1")
        with p.admin_engine.connect() as c:
            before = c.execute(text(defs)).all()
        with p.admin_engine.begin() as c:
            _exec_file(c, str(_the_migration()))
        with p.admin_engine.connect() as c:
            assert c.execute(text(defs)).all() == before

    def test_it_is_expand_only(self, promoted):  # noqa: F811
        body = _the_migration().read_text(encoding="utf-8")
        assert "CREATE TABLE" not in body and "DROP COLUMN" not in body
        # test_reach_push.py finds migration 318 by the two channel names.
        assert "reach_outbox_channel_known" not in body


# ── The world ──────────────────────────────────────────────────────────────


class _Redis:
    """The two commands of the guard, on one dict. Keys carry the org."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.down = False

    async def mget(self, keys: list[Any]) -> list[Any]:
        if self.down:
            raise ConnectionError("redis is down")
        return [self.values.get(k.value) for k in keys]

    async def set(self, k: Any, value: Any, **_: Any) -> None:
        if self.down:
            raise ConnectionError("redis is down")
        self.values[k.value] = value


def _wipe(p: Any) -> None:
    with p.admin_engine.begin() as c:
        for sql in ("DELETE FROM pm_task_personal", "DELETE FROM pm_task_assignees",
                    "DELETE FROM pm_tasks", "DELETE FROM pm_project_grants",
                    "DELETE FROM pm_task_statuses", "DELETE FROM pm_projects",
                    "DELETE FROM reach_outbox", "DELETE FROM push_subscriptions",
                    "DELETE FROM user_settings", "DELETE FROM people_absences",
                    "DELETE FROM people WHERE email LIKE '%@rx5.test'",
                    "DELETE FROM app_user WHERE email LIKE '%@rx5.test'"):
            c.execute(text(sql))


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Four members with a device in two orgs, and one with no device. Reminders
    and push on, ``DUE_PUSH_ORGS=*``, every ``REACH_*`` switch off, a fake
    directory, a fake Redis and a fake push service."""
    p = promoted
    _wipe(p)
    tag = uuid.uuid4().hex[:8]
    ns = SimpleNamespace(
        p=p, n=1000, service=_PushService(), redis=_Redis(),
        #: Org B, Kolkata, the day starts at 08:00.
        asha=f"asha-{tag}@rx5.test",
        #: Org B, Los Angeles, the day starts at 09:00.
        wendy=f"wendy-{tag}@rx5.test",
        #: Org B, Kolkata, 08:00, and nothing is ever due.
        nima=f"nima-{tag}@rx5.test",
        #: Org B, Kolkata, 08:00, and no device.
        dev=f"dev-{tag}@rx5.test",
        #: Org A, Kolkata, 08:00.
        carl=f"carl-{tag}@rx5.test",
        browsers={}, refused=set(), failing=set(), homes={},
    )
    members = ((ns.asha, p.org_b, "Asia/Kolkata", 8), (ns.wendy, p.org_b, "America/Los_Angeles", 9),
               (ns.nima, p.org_b, "Asia/Kolkata", 8), (ns.dev, p.org_b, "Asia/Kolkata", 8),
               (ns.carl, p.org_a, "Asia/Kolkata", 8))
    with p.admin_engine.begin() as c:
        for email, org, zone, start in members:
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
                {"e": email, "o": org})
            c.execute(text(
                "INSERT INTO user_settings (organization_id, user_id, timezone, "
                "day_start_hour, day_end_hour) VALUES (CAST(:o AS uuid), :u, :z, :s, 22)"),
                {"o": org, "u": email, "z": zone, "s": start})
        ns.homes[p.org_b] = _project(c, org=p.org_b, name=f"B {tag}", grant_org=True)
        ns.homes[p.org_a] = _project(c, org=p.org_a, name=f"A {tag}", grant_org=True)
        ns.names = dict(c.execute(text(
            "SELECT id::text, display_name FROM organization WHERE id IN "
            "(CAST(:a AS uuid), CAST(:b AS uuid))"), {"a": p.org_a, "b": p.org_b}).all())
    for email, org, *_ in members:
        if email != ns.dev:
            ns.browsers[email] = _Browser()
            _subscribe(p, email, org, ns.browsers[email])

    monkeypatch.setattr(rm, "get_settings", lambda: SimpleNamespace(calendar_reminders=True))
    private, public = vapid_pair()
    s = get_settings()
    for key, value in (("web_push_enabled", True), ("vapid_private_key", private),
                       ("vapid_public_key", public), ("vapid_subject", SUBJECT),
                       ("reach_enabled", False), ("reach_orgs", ""), ("reach_members", ""),
                       ("reach_daily_cap", 3), ("due_push_orgs", "*")):
        monkeypatch.setattr(s, key, value)
    monkeypatch.setattr(outbound_guard, "request_https", ns.service.request_https)

    async def is_active(org: str, member: str) -> bool:
        return True

    async def orgs() -> list[str]:
        return [p.org_a, p.org_b]

    async def bump(org: Any, email: Any) -> None:
        return None

    async def may_read(org: str, member: str) -> bool | None:
        if member in ns.failing:
            return None
        return member not in ns.refused

    monkeypatch.setattr(loop, "_is_active", is_active)
    monkeypatch.setattr(loop, "_orgs", orgs)
    monkeypatch.setattr(cache, "bump_member_safe", bump)
    monkeypatch.setattr(due_push, "_may_read", may_read)
    monkeypatch.setattr(due_push.tenant_redis, "get_tenant_redis", lambda: ns.redis)
    loop._stopping.clear()
    yield ns
    _wipe(p)


def _due(world: Any, member: str, title: str, due: datetime, *, org: str | None = None,
         stated: str | None = None) -> str:
    """One open task of *member*, as the superuser, past RLS."""
    org = org or world.p.org_b
    project, lane = world.homes[org]
    world.n += 1
    with world.p.admin_engine.begin() as c:
        task = _task(c, org=org, project=project, lane=lane, title=title, due=due,
                     assignee=member, number=world.n)
        if stated:
            _stated(c, org=org, task=task, email=member, disposition=stated)
    return task


def _close(world: Any, task: str) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_tasks SET completed_at = now() WHERE id = CAST(:t AS uuid)"),
                  {"t": task})


def _rows(world: Any, member: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT organization_id::text AS org, channel, kind, state, reason, dedupe_key, "
            "body, signal_ids, local_date FROM reach_outbox "
            "WHERE member_email = :m ORDER BY created_at, id"), {"m": member}))


def _ticks(world: Any, *instants: datetime) -> list[dict[str, Counter]]:
    async def go() -> list[dict[str, Counter]]:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return [await loop.run_tick(at) for at in instants]
    return asyncio.run(go())


def _in_org(world: Any, org: str, work: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            token = bind_tenant(org)
            try:
                return await work()
            finally:
                release_tenant(token)
    return asyncio.run(go())


def _plan(world: Any, org: str, at: datetime) -> Counter:
    return _in_org(world, org, lambda: due_push.plan_org(org, at))


def _pushes(world: Any, member: str) -> list[dict[str, Any]]:
    mine = [p for p in world.service.posts if p["url"] == _endpoint(member)]
    return [world.browsers[member].decrypt(p["data"]) for p in mine]


def _org_name(world: Any, org: str) -> str:
    return (world.names.get(org) or "").strip() or "Metorite"


# ── The push ───────────────────────────────────────────────────────────────


def test_r8_the_morning_push_says_the_counts_and_the_first_title(world) -> None:
    _due(world, world.asha, "File the GST return", _ist(8, 12))
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    _due(world, world.asha, "Send the deck", _ist(10, 12))
    _due(world, world.asha, "Book the van", _ist(10, 12))

    got = _ticks(world, _ist(10, 8, 0))[0]

    rows = _rows(world, world.asha)
    assert [(r.channel, r.kind, r.state) for r in rows] == [("web_push", "due_summary", "sent")]
    assert rows[0].local_date == date(2099, 3, 10)
    assert rows[0].body == "due_summary" and not rows[0].signal_ids
    assert got[world.p.org_b]["due_planned"] == 1
    pushed = _pushes(world, world.asha)
    assert len(pushed) == 1
    assert pushed[0]["title"] == "3 due today, 1 overdue"
    # The oldest deadline: the first row of My Day's order.
    assert pushed[0]["body"] == "File the GST return and 3 more"
    assert pushed[0]["url"] == "/" and pushed[0]["tag"] == DUE_TAG


def test_r8_one_task_has_no_and_more(world) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    _ticks(world, _ist(10, 8, 0))
    assert _pushes(world, world.asha)[0]["title"] == "1 due today"
    assert _pushes(world, world.asha)[0]["body"] == "Call the Acme buyer"


def test_r8_hide_details_keeps_the_counts_and_drops_the_title(world) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE user_settings SET my_day_prefs = "
            "COALESCE(my_day_prefs, '{}'::jsonb) || '{\"hide_push_details\": true}'::jsonb "
            "WHERE user_id = :u"), {"u": world.asha})
    _ticks(world, _ist(10, 8, 0))
    pushed = _pushes(world, world.asha)[0]
    assert pushed["title"] == "1 due today"
    assert pushed["body"] == _org_name(world, world.p.org_b)
    assert "Acme" not in str(pushed)


def test_r8_no_device_is_the_existing_skip(world) -> None:
    _due(world, world.dev, "Call the Acme buyer", _ist(10, 12))
    _ticks(world, _ist(10, 8, 0))
    assert [(r.state, r.reason) for r in _rows(world, world.dev)] == [("skipped", "no_device")]


def test_r8_a_task_closed_after_the_plan_sends_nothing(world) -> None:
    task = _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    assert _plan(world, world.p.org_b, _ist(10, 8, 0))["due_planned"] == 1
    _close(world, task)
    _ticks(world, _ist(10, 8, 1))
    assert [(r.state, r.reason) for r in _rows(world, world.asha)] == [("skipped", "nothing_due")]
    assert world.service.posts == []


def test_r8_someday_and_a_closed_task_stay_out_as_on_my_day(world) -> None:
    _due(world, world.asha, "Someday task", _ist(8, 12), stated="SOMEDAY")
    _close(world, _due(world, world.asha, "Closed task", _ist(8, 12)))
    _due(world, world.asha, "Real task", _ist(10, 12))
    _ticks(world, _ist(10, 8, 0))
    assert _pushes(world, world.asha)[0]["title"] == "1 due today"
    assert _pushes(world, world.asha)[0]["body"] == "Real task"


# ── When ───────────────────────────────────────────────────────────────────


def test_r8_it_fires_in_the_first_tick_at_or_after_the_day_start(world) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    _ticks(world, _ist(10, 7, 59))
    assert _rows(world, world.asha) == []
    _ticks(world, _ist(10, 8, 0))
    assert len(_rows(world, world.asha)) == 1


def test_r8_a_day_start_missed_by_hours_plans_nothing(world) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    late = _ist(10, 8) + timedelta(seconds=DUE_TTL_S + 60)
    _ticks(world, late)
    assert _rows(world, world.asha) == []
    assert world.service.posts == []
    # Inside the TTL, a late tick still plans.
    _ticks(world, _ist(10, 8) + timedelta(seconds=DUE_TTL_S - 60))
    assert len(_rows(world, world.asha)) == 1


def test_r8_a_zone_west_of_utc_has_its_own_day(world) -> None:
    # 09:00 in Los Angeles is 16:00 UTC. The Kolkata members are at 21:30.
    today = _due(world, world.wendy, "Due today", _la(10, 12))
    _due(world, world.wendy, "Due yesterday", _la(9, 12))
    _due(world, world.wendy, "Due tomorrow", _la(11, 12))
    _ticks(world, _la(10, 8, 59))
    assert _rows(world, world.wendy) == []
    _ticks(world, _la(10, 9, 0))
    rows = _rows(world, world.wendy)
    assert [r.local_date for r in rows] == [date(2099, 3, 10)]
    assert _pushes(world, world.wendy)[0]["title"] == "1 due today, 1 overdue"
    # 18:00 in Los Angeles is already the 11th in UTC. The task stays today's.
    assert _kinds(world, world.wendy, _la(10, 18))[today] == "due_today"


def test_r8_quiet_hours_end_a_planned_row(world) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE user_settings SET day_end_hour = 9 WHERE user_id = :u"),
                  {"u": world.asha})
    assert _plan(world, world.p.org_b, _ist(10, 8, 30))["due_planned"] == 1
    _ticks(world, _ist(10, 9, 30))
    assert [(r.state, r.reason) for r in _rows(world, world.asha)] == [("skipped", "quiet")]


# ── One a day ──────────────────────────────────────────────────────────────


def test_r8_a_retry_plans_one_row(world) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    org = world.p.org_b
    assert _plan(world, org, _ist(10, 8, 0))["due_planned"] == 1
    # The tick that plans again sees the key and does not read the tasks.
    assert "due_planned" not in _plan(world, org, _ist(10, 8, 1))
    _ticks(world, _ist(10, 8, 2), _ist(10, 8, 3), _ist(10, 11, 0))
    assert len(_rows(world, world.asha)) == 1
    assert len(_pushes(world, world.asha)) == 1


def test_r8_two_processes_plan_one_row(world) -> None:
    for n in range(3):
        _due(world, world.asha, f"Task {n}", _ist(10, 12))
    org = world.p.org_b

    async def work() -> list[Counter]:
        return list(await asyncio.gather(*(
            due_push.plan_org(org, _ist(10, 8, 0)) for _ in range(4))))

    got = _in_org(world, org, work)
    assert sum(c["due_planned"] for c in got) == 1
    assert len(_rows(world, world.asha)) == 1


def test_r8_a_new_day_plans_a_new_row(world) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    _ticks(world, _ist(10, 8, 0), _ist(11, 8, 0))
    assert [(r.local_date, r.state) for r in _rows(world, world.asha)] == [
        (date(2099, 3, 10), "sent"), (date(2099, 3, 11), "sent")]
    # The task is overdue on the second morning.
    assert _pushes(world, world.asha)[1]["title"] == "1 overdue"


def test_r8_a_summary_counts_against_no_other_cap(world) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    _ticks(world, _ist(10, 8, 0))
    org = world.p.org_b

    async def work() -> tuple[int, int, int]:
        since = _ist(1, 0)
        whatsapp = await outbox.interrupts_since(org, world.asha, since)
        urgent = await outbox.urgent_since(org, world.asha, since)
        async with tenant_session(org) as db:
            reminder = await outbox.pushes_counted(
                db, world.asha, kind="reminder_due", local_date=date(2099, 3, 10),
                self_id=str(uuid.uuid4()))
        return whatsapp, urgent, reminder

    assert _in_org(world, org, work) == (0, 0, 0)


# ── Nothing due ────────────────────────────────────────────────────────────


def test_r8_nothing_due_is_no_row_and_the_day_stays_quiet(world) -> None:
    got = _ticks(world, _ist(10, 8, 0))[0]
    assert got[world.p.org_b]["due_nothing"] >= 1
    assert _rows(world, world.nima) == []
    assert world.service.posts == []
    # A task that lands after the morning sends no second thought today.
    _due(world, world.nima, "Late arrival", _ist(10, 15))
    _ticks(world, _ist(10, 9, 0))
    assert _rows(world, world.nima) == []
    # The next morning it counts.
    _ticks(world, _ist(11, 8, 0))
    assert _pushes(world, world.nima)[0]["title"] == "1 overdue"


def test_r8_with_redis_down_the_plan_reads_and_the_key_still_holds_one_row(world) -> None:
    world.redis.down = True
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    assert _plan(world, world.p.org_b, _ist(10, 8, 0))["due_planned"] == 1
    assert "due_planned" not in _plan(world, world.p.org_b, _ist(10, 8, 1))
    assert len(_rows(world, world.asha)) == 1


def test_r8_a_refused_member_gets_no_row_and_a_failed_read_tries_again(world) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    _due(world, world.dev, "Call the Acme buyer", _ist(10, 12))
    world.refused.add(world.asha)
    world.failing.add(world.dev)
    got = _plan(world, world.p.org_b, _ist(10, 8, 0))
    assert (got["due_no_feature"], got["due_retry"]) == (1, 1)
    assert _rows(world, world.asha) == [] and _rows(world, world.dev) == []
    world.failing.clear()
    assert _plan(world, world.p.org_b, _ist(10, 8, 1))["due_planned"] == 1
    assert len(_rows(world, world.dev)) == 1 and _rows(world, world.asha) == []


# ── The flag ───────────────────────────────────────────────────────────────


def test_r8_the_flag_fails_closed(world, monkeypatch) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    s = get_settings()
    monkeypatch.setattr(s, "due_push_orgs", "")
    _ticks(world, _ist(10, 8, 0))
    assert _rows(world, world.asha) == []
    # The list opens only the org it names.
    monkeypatch.setattr(s, "due_push_orgs", world.p.org_a)
    _ticks(world, _ist(10, 8, 1))
    assert _rows(world, world.asha) == []
    monkeypatch.setattr(s, "due_push_orgs", world.p.org_b)
    _ticks(world, _ist(10, 8, 2))
    assert len(_rows(world, world.asha)) == 1


def test_r8_the_flag_stops_a_row_that_is_planned(world, monkeypatch) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    assert _plan(world, world.p.org_b, _ist(10, 8, 0))["due_planned"] == 1
    monkeypatch.setattr(get_settings(), "due_push_orgs", "")
    _ticks(world, _ist(10, 8, 1))
    assert [(r.state, r.reason) for r in _rows(world, world.asha)] == [("skipped", "flag_off")]


def test_r8_with_push_off_no_row_is_planned(world, monkeypatch) -> None:
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    monkeypatch.setattr(get_settings(), "web_push_enabled", False)
    _ticks(world, _ist(10, 8, 0))
    assert _rows(world, world.asha) == []


# ── Two orgs ───────────────────────────────────────────────────────────────


def test_r8_each_org_plans_only_its_own_members(world) -> None:
    p = world.p
    _due(world, world.asha, "Org B task", _ist(10, 12))
    _due(world, world.carl, "Org A task", _ist(10, 12), org=p.org_a)
    # A task of org A that names a member of org B: the address is a bare
    # string, so another org can type it. It stays in org A.
    _due(world, world.asha, "Org A task for Asha", _ist(8, 12), org=p.org_a)

    assert _plan(world, p.org_b, _ist(10, 8, 0))["due_planned"] == 1
    assert [r.org for r in _rows(world, world.asha)] == [p.org_b]
    assert _rows(world, world.carl) == []
    _ticks(world, _ist(10, 8, 1))
    assert [r.org for r in _rows(world, world.carl)] == [p.org_a]
    assert _pushes(world, world.asha)[0]["title"] == "1 due today"
    assert _pushes(world, world.asha)[0]["body"] == "Org B task"
    assert _pushes(world, world.carl)[0]["body"] == "Org A task"


def test_r8_a_session_of_one_org_reads_no_row_of_the_other(world) -> None:
    p = world.p
    _due(world, world.asha, "Org B task", _ist(10, 12))
    _ticks(world, _ist(10, 8, 0))
    assert len(_rows(world, world.asha)) == 1

    async def read(org: str) -> tuple[int, int]:
        async with tenant_session(org) as db:
            rows = (await db.execute(text(
                "SELECT count(*) FROM reach_outbox WHERE kind = 'due_summary'"))).scalar()
            slots = await due_push._open_slots(db, _ist(10, 8, 0))
        return int(rows or 0), sum(1 for s in slots if s.member == world.asha)

    assert _in_org(world, p.org_a, lambda: read(p.org_a)) == (0, 0)
    assert _in_org(world, p.org_b, lambda: read(p.org_b)) == (1, 0)


# ── The day rule, on real rows ─────────────────────────────────────────────


def _kinds(world: Any, member: str, at: datetime, org: str | None = None) -> dict[str, Any]:
    """The kind of each of *member*'s due rows at *at*: the read of My Day."""
    org = org or world.p.org_b

    async def work() -> dict[str, Any]:
        async with tenant_session(org) as db:
            answer = await personal.my_due_rows(db, member, limit=50, at=at)
        today = needs.local_today(answer)
        return {r["id"]: needs.due_kind(r, answer["timezone"], today) for r in answer["rows"]}

    return _in_org(world, org, work)


def test_r8_a_task_due_today_at_noon_at_18_00_local_reads_due_today(world) -> None:
    task = _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    at = _ist(10, 18)
    assert _kinds(world, world.asha, at) == {task: "due_today"}
    kind, line = feed.why("due_today", {"due_at": _ist(10, 12).isoformat()}, None, at, IST,
                          date(2099, 3, 10))
    assert (kind, line) == ("due_today", "Due today.")


@pytest.mark.parametrize("hour,minute,day,kind", [
    (23, 59, 10, "due_today"),
    (0, 1, 11, "overdue"),
])
def test_r8_the_day_boundary_at_23_59_and_00_01_local(world, hour, minute, day, kind) -> None:
    task = _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    at = _ist(day, hour, minute)
    assert _kinds(world, world.asha, at)[task] == kind


def test_r8_a_date_only_write_is_noon_in_the_zone_of_the_member(world) -> None:
    org = world.p.org_b

    async def work() -> tuple[Any, Any, Any]:
        async with tenant_session(org) as db:
            return (await personal.stored_due(db, world.asha, "2099-03-10"),
                    await personal.stored_due(db, world.wendy, "2099-03-10"),
                    await personal.stored_due(db, world.wendy, "2099-03-10T17:00:00+05:30"))

    asha, wendy, timed = _in_org(world, org, work)
    assert asha == _ist(10, 12) and wendy == _la(10, 12)
    assert timed == "2099-03-10T17:00:00+05:30"
    # West of UTC, the bare parse was the day before. Noon keeps the day.
    assert due_day.local_date("America/Los_Angeles", wendy) == date(2099, 3, 10)
    assert due_day.local_date("America/Los_Angeles",
                              datetime(2099, 3, 10, tzinfo=UTC)) == date(2099, 3, 9)


def test_r8_each_write_of_a_chat_date_reaches_the_seam(world) -> None:
    """The routes behind ``create_task``, ``create_tasks``, ``update_task`` and
    ``create_personal_task``, on real rows, for a member west of UTC. A value
    with a time and an offset stays as given."""
    org = world.p.org_b
    space, _lane = world.homes[org]
    project = str(uuid.uuid4())  # a top node is a space, and a space holds no task
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
            "parent_project_id) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), 'Work', "
            "'manual', :by, CAST(:s AS uuid))"),
            {"p": project, "o": org, "by": world.wendy, "s": space})
    user = UserContext(email=world.wendy, role=UserRole.EMPLOYEE,
                       access=EffectiveAccess(role_granted=frozenset({"feature:projects"})))

    async def stored(task_id: str) -> datetime:
        async with tenant_session(org) as db:
            return (await db.execute(text(
                "SELECT due_at FROM pm_tasks WHERE id = CAST(:t AS uuid)"), {"t": task_id})
            ).scalar_one()

    async def work() -> list[datetime]:
        made = await tasks.create_task(
            tasks.TaskCreateIn(project_id=project, title="By chat", due_at="2099-03-10"),
            user=user)
        kept = [await stored(made["id"])]
        await tasks.patch_task(made["id"], tasks.TaskIn(due_at="2099-03-12"), user=user,
                               if_match=None, include_subtasks=False)
        kept.append(await stored(made["id"]))
        await tasks.patch_task(made["id"], tasks.TaskIn(due_at="2099-03-12T17:00:00+05:30"),
                               user=user, if_match=None, include_subtasks=False)
        kept.append(await stored(made["id"]))
        mine = await personal.capture(personal.CaptureIn(title="Private", due_at="2099-03-10"),
                                      user=user)
        kept.append(await stored(mine["id"]))
        taken = await intake.capture_intake(
            intake.IntakeIn(project_id=project, title="By chat too", due_at="2099-03-10"),
            user=user)
        kept.append(await stored(taken["task"]["id"]))
        # The chat's `bulk_update` and the board's bulk edit: one request.
        await bulk.bulk_edit(bulk.BulkIn(task_ids=[made["id"], taken["task"]["id"]],
                                         patch={"due_at": "2099-03-13"}), user=user)
        kept += [await stored(made["id"]), await stored(taken["task"]["id"])]
        return kept

    created, patched, timed, captured, in_intake, bulk_one, bulk_two = _in_org(world, org, work)
    assert created == captured == in_intake == _la(10, 12)
    assert patched == _la(12, 12)
    assert timed == datetime(2099, 3, 12, 17, 0, tzinfo=IST)
    # A New York or Los Angeles member keeps the day, and reads no day early.
    assert bulk_one == bulk_two == _la(13, 12)
    assert due_day.local_date("America/Los_Angeles", bulk_one) == date(2099, 3, 13)


def test_r8_a_mail_draft_of_a_date_is_noon_of_the_member(world) -> None:
    """Both capture paths of ``capture_email`` call ``_draft_due``. A value with
    a time parses as it always did, and a value that does not parse is None."""
    org = world.p.org_b

    async def work() -> list[Any]:
        async with tenant_session(org) as db:
            return [await capture_email._draft_due(db, world.wendy, value)
                    for value in ("2099-03-10", "2099-03-10T17:00:00+05:30", "", "soon")]

    plain, timed, empty, junk = _in_org(world, org, work)
    assert plain == _la(10, 12)
    assert timed == datetime(2099, 3, 10, 17, 0, tzinfo=IST)
    assert empty is None and junk is None


def _commitments(world: Any, member: str, due: datetime, now: datetime,
                 monkeypatch: Any) -> list[dict[str, Any]]:
    """The digest's read of one commitment of *member*, on a real session, with
    the task seam faked: the rule under test is the read of the zone."""
    from gateway.routes.tasks import item_source as seam

    task = SimpleNamespace(id=uuid.uuid4(), title="Send the quote", due_at=due, origin={})

    class Source:
        async def items_by_origin(self, db: Any, uid: str, key: str, value: str) -> list[Any]:
            return [task]

    monkeypatch.setattr(seam, "item_source", Source)
    org = world.p.org_b

    async def work() -> list[dict[str, Any]]:
        async with tenant_session(org) as db:
            zone, at, picked, _latest = await digest._commitments_core(
                db, str(uuid.uuid4()), horizon_days=3, limit=8, now=now, owner=member)
        return [{"overdue": digest._overdue(d, zone, at),
                 "day": digest._due_day(d, zone).isoformat()} for _, d in picked]

    return _in_org(world, org, work)


def test_r8_a_commitment_reads_late_by_the_zone_of_its_owner(world, monkeypatch) -> None:
    """A task of the 10th stored at 00:00Z: "Due today" on My Day at 08:00 in
    Kolkata, so the digest must not say overdue."""
    stored = datetime(2099, 3, 10, tzinfo=UTC)
    task = _due(world, world.asha, "Send the quote", stored)
    assert _kinds(world, world.asha, _ist(10, 8))[task] == "due_today"
    got = _commitments(world, world.asha, stored, _ist(10, 8), monkeypatch)
    assert got == [{"overdue": False, "day": "2099-03-10"}]
    # The next morning both call it late.
    assert _kinds(world, world.asha, _ist(11, 8))[task] == "overdue"
    assert _commitments(world, world.asha, stored, _ist(11, 8), monkeypatch)[0]["overdue"] is True


def test_r8_the_sql_rule_and_the_python_rule_agree_at_the_day_edge(world) -> None:
    """``due_at < :day_start`` is the Python rule, one second either side of
    the member's midnight. The statement runs on a real Postgres, so a
    statement that compares with ``now()`` fails here: the clock of this run
    is decades before these rows."""
    edge = _ist(10, 0)
    before = _due(world, world.asha, "Last second of the 9th", edge - timedelta(seconds=1))
    at = _due(world, world.asha, "First second of the 10th", edge)
    now = _ist(10, 18)
    org = world.p.org_b

    async def work() -> set[str]:
        async with tenant_session(org) as db:
            start = await personal.member_day_start(db, world.asha, now)
            rows = (await db.execute(
                text(f"SELECT t.id::text FROM pm_tasks t WHERE {due_day.overdue_sql()}"),
                {"day_start": start})).all()
        return {str(r[0]) for r in rows}

    assert _in_org(world, org, work) == {before}
    assert _kinds(world, world.asha, now) == {before: "overdue", at: "due_today"}


def test_r8_the_counts_of_a_board_use_the_same_rule(world) -> None:
    """The statements that gained ``:day_start`` run on a real Postgres, with a
    reader's day start, and they split one task each side of the edge."""
    org = world.p.org_b
    _due(world, world.asha, "Late", _ist(9, 12))
    _due(world, world.asha, "Today", _ist(10, 12))
    where = "t.organization_id = CAST(:o AS uuid) AND t.completed_at IS NULL"

    async def work() -> tuple[int, int, int]:
        async with tenant_session(org) as db:
            start = await personal.member_day_start(db, world.asha, _ist(10, 18))
            binds = {"o": org, "day_start": start}
            by_project = (await db.execute(text(overdue_by_project_sql(where)), binds)).all()
            load = (await db.execute(text(load_sql(where)), binds)).all()
        row = next(r for r in load if r.who == world.asha)
        return sum(int(r.overdue) for r in by_project), int(row.overdue), int(row.due_next_7d)

    assert _in_org(world, org, work) == (1, 1, 1)


def test_r8_a_mixed_case_row_keeps_its_zone(world) -> None:
    """A row keys on the session's spelling, and a due read holds the lower
    case. So the member's zone and their day come from that row."""
    org = world.p.org_b
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE user_settings SET user_id = :mixed WHERE user_id = :u"),
                  {"mixed": world.wendy.title(), "u": world.wendy})

    async def work() -> tuple[Any, datetime]:
        async with tenant_session(org) as db:
            return (await personal.stored_zone(db, world.wendy),
                    await personal.stored_due(db, world.wendy, "2099-03-10"))

    zone, due = _in_org(world, org, work)
    assert zone == "America/Los_Angeles"
    assert due == _la(10, 12)


def test_r8_a_mixed_case_row_loses_to_the_exact_spelling(world) -> None:
    """Two spellings of one address: the exact one wins, as ``_load`` rules."""
    org = world.p.org_b
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, timezone, updated_at) "
            "VALUES (CAST(:o AS uuid), :u, 'Pacific/Auckland', now() + interval '1 day')"),
            {"o": org, "u": world.wendy.title()})

    async def work() -> Any:
        async with tenant_session(org) as db:
            return await personal.stored_zone(db, world.wendy)

    assert _in_org(world, org, work) == "America/Los_Angeles"


async def _read_as(user: UserContext, org: str, *paths: str) -> list[Any]:
    """Each path through the real routes, as *user* in *org*."""
    app = FastAPI()
    app.include_router(projects_package.router)
    app.dependency_overrides[get_current_user] = lambda: user
    token = bind_tenant(org)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://projects.test") as client:
            answers = [await client.get(path) for path in paths]
    finally:
        release_tenant(token)
    assert [a.status_code for a in answers] == [200] * len(paths), answers[0].text[:300]
    return [a.json() for a in answers]


def test_r8_a_route_counts_late_work_by_the_day_of_its_reader(world) -> None:
    """A task on a day edge of the reader reads late or not by THEIR day, on
    the list filter and on Stuck.

    At any instant one of the two zones below has a date other than the UTC
    date, so the reader's rule and the UTC rule split this task. Whichever way
    it falls, a route that binds the UTC day answers the other way."""
    org = world.p.org_b
    now = datetime.now(UTC)
    zone = "Pacific/Kiritimati" if now.hour >= 11 else "Pacific/Pago_Pago"
    reader, utc = due_day.day_start(zone, now), due_day.day_start("UTC", now)
    assert reader != utc
    due = min(reader, utc) + abs(reader - utc) / 2
    late = due < reader
    assert late is not (due < utc)
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE user_settings SET timezone = :z WHERE user_id = :u"),
                  {"z": zone, "u": world.asha})
    task = _due(world, world.asha, "On the edge", due)
    project = world.homes[org][0]
    user = UserContext(email=world.asha, role=UserRole.EMPLOYEE,
                       access=EffectiveAccess(role_granted=frozenset({"feature:projects"})))

    async def work() -> list[Any]:
        return await _read_as(
            user, org, f"/projects/tasks?overdue=true&project_id={project}",
            f"/projects/analytics/stuck?project_id={project}")

    listed, stuck = _in_org(world, org, work)
    assert (task in [r["id"] for r in listed["rows"]]) is late
    assert sum(r["n"] for r in stuck["overdue"]) == (1 if late else 0)


def test_r8_a_reminder_goes_before_a_morning_of_summaries(world) -> None:
    """Older summaries of members who sort first still wait behind a reminder
    row, so one read of a short batch takes the reminders."""
    org = world.p.org_b
    insert = ("INSERT INTO reach_outbox (organization_id, member_email, channel, kind, "
              "dedupe_key, body, state, created_at) VALUES (CAST(:o AS uuid), :m, 'web_push', "
              ":k, :key, :k, 'planned', now() - make_interval(mins => :ago))")
    with world.p.admin_engine.begin() as c:
        for n in range(5):
            c.execute(text(insert), {"o": org, "m": f"aaa{n}@rx5.test", "k": "due_summary",
                                     "key": f"due_summary:aaa{n}", "ago": 60})
        for n, kind in enumerate(("reminder_due", "reminder_check")):
            c.execute(text(insert), {"o": org, "m": f"zzz{n}@rx5.test", "k": kind,
                                     "key": f"{kind}:zzz{n}", "ago": 1})
        kinds = dict(c.execute(text(
            "SELECT id::text, kind FROM reach_outbox WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org}).all())

    async def work() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        return (await outbox.planned_pushes(org, limit=2),
                await outbox.planned_pushes(org, limit=7))

    first_two, every = _in_org(world, org, work)
    assert {kind for _, kind in first_two} == {"reminder_due", "reminder_check"}
    assert [kind for _, kind in every[2:]] == ["due_summary"] * 5
    # The read gives the kind of the row, and it is the kind of the row.
    assert all(kinds[i] == kind for i, kind in every)


def test_r8_a_summary_past_its_share_waits_for_a_later_tick(world, monkeypatch) -> None:
    """A share of no time starts no summary, the row stays planned, and a later
    tick sends it. A reminder never waits on this: ``test_due_day.py``."""
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    monkeypatch.setattr(loop, "SUMMARY_SEND_S", 0)
    _ticks(world, _ist(10, 8, 0))
    assert [r.state for r in _rows(world, world.asha)] == ["planned"]
    assert world.service.posts == []
    monkeypatch.setattr(loop, "SUMMARY_SEND_S", 8)
    _ticks(world, _ist(10, 8, 1))
    assert [r.state for r in _rows(world, world.asha)] == ["sent"]


def _quiet_members(world: Any, count: int) -> list[str]:
    """*count* active members of org B with no settings row."""
    tag = uuid.uuid4().hex[:6]
    made = [f"quiet{n}-{tag}@rx5.test" for n in range(count)]
    with world.p.admin_engine.begin() as c:
        for email in made:
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
                {"e": email, "o": world.p.org_b})
    return made


def _plan_counted(world: Any, org: str, at: datetime) -> tuple[Counter, int]:
    """One plan of *org*, and the statements that it sent."""
    sent: list[str] = []

    def note(_conn: Any, _cur: Any, statement: str, *_rest: Any) -> None:
        sent.append(statement)

    async def go() -> Counter:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            engine = get_engine().sync_engine
            event.listen(engine, "before_cursor_execute", note)
            token = bind_tenant(org)
            try:
                return await due_push.plan_org(org, at)
            finally:
                release_tenant(token)
                event.remove(engine, "before_cursor_execute", note)

    counts = asyncio.run(go())
    return counts, len(sent)


def test_r8_a_quiet_tick_costs_a_few_statements(world) -> None:
    """A hundred members with no settings row, and no slot open. The count of
    the statements does not grow with the members."""
    org = world.p.org_b
    at = datetime(2099, 3, 10, 13, 0, tzinfo=UTC)  # no member's day just started
    _, few = _plan_counted(world, org, at)
    _quiet_members(world, 100)
    counts, many = _plan_counted(world, org, at)
    assert not counts
    assert many <= 8, many
    assert many <= few + 4, (few, many)


def test_r8_a_member_with_no_row_starts_at_the_people_seed(world) -> None:
    """The set read gives the day of one member, the same as ``member_day``."""
    org = world.p.org_b
    email = _quiet_members(world, 1)[0]
    _due(world, email, "Call the Acme buyer", datetime(2099, 3, 10, 12, tzinfo=UTC))
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE people SET working_hours = CAST(:wh AS jsonb) "
            "WHERE organization_id = CAST(:o AS uuid) AND email = :e"),
            {"e": email, "o": org, "wh": json.dumps({"start": "11:00", "end": "19:00"})})

    async def day_of() -> Any:
        async with tenant_session(org) as db:
            return await loop.member_day(db, email)

    assert _in_org(world, org, day_of).start_hour == 10
    assert not _plan(world, org, datetime(2099, 3, 10, 9, 59, tzinfo=UTC))
    assert _plan(world, org, datetime(2099, 3, 10, 10, 0, tzinfo=UTC))["due_planned"] == 1


def test_r8_a_plan_out_of_time_stops_between_two_members(world) -> None:
    """A plan that runs out of time starts no other member, and the next tick
    plans the rest."""
    org = world.p.org_b
    _due(world, world.asha, "One", _ist(10, 12))
    _due(world, world.dev, "Two", _ist(10, 12))
    answers = iter([False, True])

    def stopping() -> bool:
        return next(answers, True)

    at = _ist(10, 8, 0)
    first = _in_org(world, org, lambda: due_push.plan_org(org, at, stopping))
    assert first["due_planned"] == 1
    assert _plan(world, org, at)["due_planned"] == 1
    assert len(_rows(world, world.asha)) == len(_rows(world, world.dev)) == 1


def test_r8_with_redis_down_a_long_list_reaches_the_last_member(world) -> None:
    """More members in the window than one batch holds, and Redis down, so no
    read leaves a mark. The last address still gets its turn in the window."""
    org = world.p.org_b
    world.redis.down = True
    # A member with no settings row takes the People seed of the default week:
    # 08:00 in UTC, one hour before 09:30. No other member's day just started.
    at = datetime(2099, 3, 10, 8, 0, tzinfo=UTC)
    members = sorted(_quiet_members(world, due_push.PLAN_BATCH + 5))
    last = members[-1]
    _due(world, last, "The last address", datetime(2099, 3, 10, 12, tzinfo=UTC))
    due_push._next_start.clear()
    ticks = 0
    while not _rows(world, last) and ticks < 7:
        got = _plan(world, org, at + timedelta(minutes=ticks))
        assert sum(got.values()) <= due_push.FAULT_BATCH
        ticks += 1
    assert len(_rows(world, last)) == 1


# ── A working day ──────────────────────────────────────────────────────────


def _person(world: Any, member: str, *, org: str | None = None, hours: Any = None) -> str:
    """The ``people`` row of *member*, which a trigger makes for an ``app_user``,
    with these working hours. Answers its id."""
    org = org or world.p.org_b
    binds = {"o": org, "e": member, "wh": json.dumps(hours or {})}
    with world.p.admin_engine.begin() as c:
        found = c.execute(text(
            "UPDATE people SET working_hours = CAST(:wh AS jsonb) "
            "WHERE organization_id = CAST(:o AS uuid) AND lower(email) = :e RETURNING id"),
            binds).scalar()
        if found is None:
            found = c.execute(text(
                "INSERT INTO people (organization_id, name, email, status, working_hours) "
                "VALUES (CAST(:o AS uuid), :e, :e, 'active', CAST(:wh AS jsonb)) RETURNING id"),
                binds).scalar_one()
    return str(found)


def _leave(world: Any, person: str, first: date, last: date, kind: str = "away") -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people_absences (organization_id, person_id, starts_on, ends_on, kind, "
            "created_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :a, :b, :k, 'hr@rx5.test')"),
            {"o": world.p.org_b, "p": person, "a": first, "b": last, "k": kind})


def test_r8_no_summary_on_a_weekend_and_monday_lists_it(world) -> None:
    """Saturday 14 and Sunday 15 March 2099. The org's default week."""
    _due(world, world.asha, "Call the Acme buyer", _ist(14, 12))
    got = _ticks(world, _ist(14, 8, 0), _ist(15, 8, 0))
    assert got[0][world.p.org_b]["due_day_off"] >= 1
    assert _rows(world, world.asha) == [] and world.service.posts == []
    _ticks(world, _ist(16, 8, 0))
    assert _pushes(world, world.asha)[0]["title"] == "1 overdue"


def test_r8_a_day_off_costs_one_read_of_the_day(world) -> None:
    """The mark quiets the rest of the window, so a later tick reads nothing
    more for that member."""
    org = world.p.org_b
    assert _plan(world, org, _ist(14, 8, 0))["due_day_off"] >= 1
    assert "due_day_off" not in _plan(world, org, _ist(14, 8, 1))


def test_r8_a_member_who_works_on_saturday_gets_it(world) -> None:
    _person(world, world.asha, hours={"days": [2, 3, 4, 5, 6]})
    _due(world, world.asha, "Open the shop", _ist(14, 12))
    _ticks(world, _ist(14, 8, 0))
    assert _pushes(world, world.asha)[0]["title"] == "1 due today"


def test_r8_no_summary_on_a_leave_day_and_the_next_day_lists_it(world) -> None:
    person = _person(world, world.asha)
    _leave(world, person, date(2099, 3, 10), date(2099, 3, 10))
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    _due(world, world.dev, "Call the Acme buyer", _ist(10, 12))
    _ticks(world, _ist(10, 8, 0))
    assert _rows(world, world.asha) == []
    assert len(_rows(world, world.dev)) == 1  # the leave of one member
    _ticks(world, _ist(11, 8, 0))
    assert _pushes(world, world.asha)[0]["title"] == "1 overdue"


def test_r8_a_partial_day_off_still_gets_the_summary(world) -> None:
    person = _person(world, world.asha)
    _leave(world, person, date(2099, 3, 10), date(2099, 3, 10), kind="partial")
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    _ticks(world, _ist(10, 8, 0))
    assert _pushes(world, world.asha)[0]["title"] == "1 due today"


def test_r8_no_summary_on_a_company_holiday(world) -> None:
    org = world.p.org_b
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO org_settings (organization_id, key, value, updated_by) "
            "VALUES (CAST(:o AS uuid), 'work_schedule', CAST(:v AS jsonb), 'hr@rx5.test') "
            "ON CONFLICT DO NOTHING"),
            {"o": org, "v": json.dumps({"holidays": ["2099-03-10"]})})
    try:
        _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
        _ticks(world, _ist(10, 8, 0))
        assert _rows(world, world.asha) == []
    finally:
        with world.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM org_settings WHERE organization_id = CAST(:o AS uuid) "
                           "AND key = 'work_schedule'"), {"o": org})


def test_r8_the_leave_of_another_org_says_nothing(world) -> None:
    """One address may hold a directory row in two orgs. The read names the
    bound tenant."""
    person = _person(world, world.asha, org=world.p.org_a)
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people_absences (organization_id, person_id, starts_on, ends_on, kind, "
            "created_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :a, :a, 'away', 'h@rx5.test')"),
            {"o": world.p.org_a, "p": person, "a": date(2099, 3, 10)})
    _due(world, world.asha, "Call the Acme buyer", _ist(10, 12))
    _ticks(world, _ist(10, 8, 0))
    assert _pushes(world, world.asha)[0]["title"] == "1 due today"


def test_the_day_of_a_member_is_the_day_of_the_row() -> None:
    day = Day(IST, 8, 20)
    assert due_push.push_channel.too_late(due_push.KIND, day.day_start(_ist(10, 8)),
                                          _ist(10, 11, 59)) is False
    assert day.feed_day(_ist(10, 8)) == date(2099, 3, 10)
    assert due_push.dedupe_key("a@b", date(2099, 3, 10)) == "due_summary:a@b:2099-03-10"

