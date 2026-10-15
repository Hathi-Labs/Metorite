"""WS-58 MD-3c, R8: the task kinds of My Day on a REAL Postgres
(``my_day_feed.md`` §5.9 item 11, the MD-3c ticket).

The three new lens reads (``personal.my_quiet_tasks``,
``my_unblocked_since`` and ``my_due_ahead``) and the self absence read run
their real SQL on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``, as the NON-privileged role with RLS
forced (``_as_member``). The hermetic half is ``test_feed_task_kinds.py``.

The members of ONE organization:

* **ME** holds one task for each rule, and one task just outside each rule:
  a wait of exactly 5 days, a wait with no ``delegated_at``, a next action
  that changed 13 days ago, a Someday task with no change for 30 days, a
  blocker that closed before my day start, a task with one closed and one
  open blocker, a blocker that I cannot read, a task due on a day that a
  COLLEAGUE is away, and a task due on a day of my ``partial`` absence.
* **COLL** is the colleague, with a People row and an absence of their own.
* **NOROW** has no People row. The absence read answers 404, and the other
  three kinds still come.

Each source that the member holds must read ``ok``, because a failed
provider also gives no card.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from gateway.routes.shell import feed, state
from gateway.routes.shell import needs as shell
from sqlalchemy import text

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_shell_needs_r8 import NOW, _Frozen, _lane, _project, _task

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"kinds-me-{TAG}@md3c.test"
COLL = f"kinds-coll-{TAG}@md3c.test"
NOROW = f"kinds-norow-{TAG}@md3c.test"
#: My day starts at midnight UTC, so "my day start" is the UTC midnight of NOW.
#: The UTC midnight of NOW's own date: the due times and the days away.
MIDNIGHT = datetime.combine(NOW.astimezone(UTC).date(), time.min, tzinfo=UTC)
TODAY = MIDNIGHT.date()
#: My day starts at 01:00 UTC. A stored 0 would read 7 (`tasks.settings._load`,
#: the rule of the state join), so the seed stores 1. "My day start" is the
#: start of my current day, by `state.Day`.
DAY_START_HOUR = 1
DAY_START = state.Day(ZoneInfo("UTC"), DAY_START_HOUR).day_start(NOW)


@pytest.fixture(autouse=True)
def _one_instant(monkeypatch):
    """The feed's clock and the lens read's "today" read ``NOW``."""
    from gateway.routes.projects import personal

    monkeypatch.setattr(shell, "_now", lambda: NOW)
    monkeypatch.setattr(personal, "datetime", _Frozen)


def _member(email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(
            {"feature:projects", "feature:email", "feature:tasks"})),
    )


def _at(day_offset: int, hour: int = 11) -> datetime:
    """A local instant on today + ``day_offset`` (the zone is UTC)."""
    return MIDNIGHT + timedelta(days=day_offset, hours=hour)


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """The rows, seeded as the superuser, so the route's reads are the first
    the app role makes."""
    p = promoted
    org = p.org_b
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for email in (ME, COLL, NOROW):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": org})
            c.execute(text(
                "INSERT INTO user_settings (user_id, timezone, day_start_hour, "
                "day_end_hour, organization_id) VALUES (:e, 'UTC', 1, 23, "
                "CAST(:o AS uuid))"), {"e": email, "o": org})
        # NOROW has no directory row: the app_user insert made one, so it goes.
        gone = c.execute(text("DELETE FROM people WHERE lower(email) = :e"), {"e": NOROW})
        assert gone.rowcount == 1, "no directory row to remove for NOROW"

        project, lane = _project(c, org=org, name=f"Kinds {TAG}", grant_org=True)
        done = _lane(c, project=project, name="Done", category="done", position=9)
        someday = _lane(c, project=project, name="Backlog", category="backlog", position=0)
        # A project that ME holds no grant on: its tasks are unreadable to ME.
        hidden, hidden_done = _project(c, org=org, name=f"Hidden {TAG}", owner=COLL)
        hidden_lane = _lane(c, project=hidden, name="Done", category="done", position=9)
        del hidden_done
        n = iter(range(1, 100))

        def task(title: str, *, due=None, assignee=ME, lane_id=lane, proj=project,
                 updated=None) -> str:
            tid = _task(c, org=org, project=proj, lane=lane_id, title=title, due=due,
                        assignee=assignee, number=next(n))
            if updated is not None:
                c.execute(text("UPDATE pm_tasks SET updated_at = :u WHERE id = CAST(:t AS uuid)"),
                          {"u": updated, "t": tid})
            return tid

        def overlay(tid: str, *, disposition=None, delegated=None, updated=None) -> None:
            c.execute(text(
                "INSERT INTO pm_task_personal (task_id, member_email, disposition, "
                "delegated_at, waiting_on, organization_id, updated_at) VALUES "
                "(CAST(:t AS uuid), :e, :d, :dl, CAST(:w AS jsonb), CAST(:o AS uuid), "
                ":u)"),
                {"t": tid, "e": ME, "d": disposition, "dl": delegated, "o": org,
                 "u": updated or NOW,
                 "w": json.dumps({"name": COLL, "email": COLL}) if delegated else None})

        def blocks(blocker: str, target: str) -> None:
            c.execute(text(
                "INSERT INTO pm_task_links (source_task_id, target_task_id, link_type, "
                "created_by, organization_id) VALUES (CAST(:s AS uuid), CAST(:t AS uuid), "
                "'blocks', :by, CAST(:o AS uuid))"),
                {"s": blocker, "t": target, "by": ME, "o": org})

        def closed(tid: str, at: datetime, by: str) -> None:
            c.execute(text("UPDATE pm_tasks SET completed_at = :at WHERE id = CAST(:t AS uuid)"),
                      {"at": at, "t": tid})
            c.execute(text(
                "INSERT INTO pm_activities (task_id, type, created_by, created_at, "
                "organization_id) VALUES (CAST(:t AS uuid), 'status_change', :by, :at, "
                "CAST(:o AS uuid))"), {"t": tid, "by": by, "at": at, "o": org})

        old = NOW - timedelta(days=40)
        # ── waiting_quiet ───────────────────────────────────────────────────
        ids["wait"] = task("Get the quote", assignee=COLL, updated=old)
        overlay(ids["wait"], disposition="WAITING", delegated=NOW - timedelta(days=5, minutes=1),
                updated=old)
        ids["wait_5"] = task("Exactly five", assignee=COLL, updated=old)
        overlay(ids["wait_5"], disposition="WAITING", delegated=NOW - timedelta(days=5),
                updated=old)
        ids["wait_none"] = task("No delegation", assignee=COLL, updated=old)
        overlay(ids["wait_none"], disposition="WAITING", updated=old)
        # ── stale_next ──────────────────────────────────────────────────────
        ids["stale"] = task("Draft the manual", updated=NOW - timedelta(days=20))
        ids["stale_13"] = task("Changed lately", updated=NOW - timedelta(days=13))
        ids["someday"] = task("Some day", lane_id=someday, updated=NOW - timedelta(days=30))
        # A NEXT task whose OWN overlay changed lately is not stale.
        ids["stale_mine"] = task("I touched it", updated=NOW - timedelta(days=30))
        overlay(ids["stale_mine"], disposition="NEXT", updated=NOW - timedelta(days=2))
        # ── unblocked ───────────────────────────────────────────────────────
        ids["free"] = task("Mount the sensor")
        ids["free_blocker"] = task("Ship the API", lane_id=done, assignee=COLL)
        closed(ids["free_blocker"], DAY_START, COLL)
        blocks(ids["free_blocker"], ids["free"])
        ids["early"] = task("Closed yesterday")
        ids["early_blocker"] = task("Old blocker", lane_id=done, assignee=COLL)
        closed(ids["early_blocker"], DAY_START - timedelta(seconds=1), COLL)
        blocks(ids["early_blocker"], ids["early"])
        ids["half"] = task("Half free")
        ids["half_closed"] = task("Closed one", lane_id=done, assignee=COLL)
        closed(ids["half_closed"], DAY_START + timedelta(seconds=1), COLL)
        ids["half_open"] = task("Open one", assignee=COLL)
        blocks(ids["half_closed"], ids["half"])
        blocks(ids["half_open"], ids["half"])
        ids["hid"] = task("Behind a secret")
        ids["hid_blocker"] = task("Secret work zebra", lane_id=hidden_lane, proj=hidden,
                                  assignee=COLL)
        closed(ids["hid_blocker"], DAY_START, COLL)
        blocks(ids["hid_blocker"], ids["hid"])
        # ── absent_on_due ───────────────────────────────────────────────────
        ids["away"] = task("Pay the vendor", due=_at(3))
        ids["coll_day"] = task("Due on their day off", due=_at(4))
        ids["partial_day"] = task("Due on my half day", due=_at(5))
        # A second day away AFTER the two days above, so the window of the
        # due read (my first day away to my last) holds both of them. Only
        # the rule of the provider keeps them out, not the window.
        ids["away_late"] = task("Sign the lease", due=_at(6, hour=9))

        def absence(email: str, day: int, kind: str) -> None:
            c.execute(text(
                "INSERT INTO people_absences (person_id, starts_on, ends_on, kind, "
                "hours_per_day, created_by, organization_id) SELECT id, :d, :d, :k, :h, "
                ":e, CAST(:o AS uuid) FROM people WHERE lower(email) = :e"),
                {"d": TODAY + timedelta(days=day), "k": kind, "e": email, "o": org,
                 "h": 4 if kind == "partial" else None})

        absence(ME, 3, "holiday")
        absence(COLL, 4, "away")
        absence(ME, 5, "partial")
        absence(ME, 6, "away")
        # NOROW: one task of each of the other three kinds.
        ids["nr_wait"] = task("Their quiet wait", assignee=COLL, updated=old)
        c.execute(text(
            "INSERT INTO pm_task_personal (task_id, member_email, disposition, delegated_at, "
            "organization_id, updated_at) VALUES (CAST(:t AS uuid), :e, 'WAITING', :dl, "
            "CAST(:o AS uuid), :u)"),
            {"t": ids["nr_wait"], "e": NOROW, "dl": NOW - timedelta(days=8), "o": org,
             "u": old})
        ids["nr_stale"] = task("Their stale one", assignee=NOROW,
                               updated=NOW - timedelta(days=21))
        ids["nr_free"] = task("Their freed one", assignee=NOROW)
        blocks(ids["free_blocker"], ids["nr_free"])
        ids["nr_due"] = task("Their due one", assignee=NOROW, due=_at(3))
    return ids


def _list(monkeypatch, value: str) -> None:
    from acb_common.settings import get_settings

    monkeypatch.setattr(get_settings(), "my_day_providers", value)


async def _feed(p, monkeypatch, user: UserContext) -> dict:
    # Room for a cold database. The `sources` check proves each provider ran.
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
    monkeypatch.setattr(shell, "MAILBOX_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_BUDGET_S", 90.0)
    monkeypatch.setattr(feed, "FEED_TOTAL_S", 90.0)
    _list(monkeypatch, "tasks_followups")
    async with _as_member(p, p.org_b):
        return await feed.shell_feed(limit=80, user=user)


def _by_kind(answer: dict, kind: str) -> dict[str, dict]:
    return {c["signal_id"]: c for c in answer["cards"]
            if c["kind"] == kind or kind in c["folded"]}


class TestTheKindsOnARealDatabase:
    async def test_each_kind_gives_its_card_and_no_other(self, promoted, seeded, monkeypatch):  # noqa: F811
        answer = await _feed(promoted, monkeypatch, _member(ME))
        assert answer["sources"]["tasks_followups"] == "ok"
        assert answer["sources"]["tasks"] == "ok"
        s = seeded
        wait = _by_kind(answer, "waiting_quiet")
        assert set(wait) == {f"tasks:{s['wait']}"}, "done-when 3"
        c = wait[f"tasks:{s['wait']}"]
        assert (c["group"], c["why_key"], c["source"]) == ("needs_you", "waited",
                                                          "tasks_followups")
        # Whole local days: 5 or 6, by the time of day of NOW (the zone is UTC).
        n = (TODAY - (NOW - timedelta(days=5, minutes=1)).astimezone(UTC).date()).days
        assert c["why"] == f"You have waited {n} days on {COLL.split('@')[0]}."
        stale = _by_kind(answer, "stale_next")
        assert set(stale) == {f"tasks:{s['stale']}"}, "done-when 4"
        assert stale[f"tasks:{s['stale']}"]["why"] == "No change for 20 days."
        free = _by_kind(answer, "unblocked")
        assert set(free) == {f"tasks:{s['free']}", f"tasks:{s['hid']}"}, "done-when 5, 6"
        c = free[f"tasks:{s['free']}"]
        assert c["why"] == f"{COLL.split('@')[0]} closed Ship the API, which you waited on."
        assert c["facts"]["blocker_ids"] == [s["free_blocker"]]
        away = _by_kind(answer, "absent_on_due")
        assert set(away) == {f"tasks:{s['away']}", f"tasks:{s['away_late']}"}, "done-when 8, 9"
        assert away[f"tasks:{s['away']}"]["facts"]["absence_day"] == \
            (TODAY + timedelta(days=3)).isoformat()

    async def test_an_unreadable_blocker_gives_a_card_with_no_title_and_no_id(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        """Done-when 7."""
        answer = await _feed(promoted, monkeypatch, _member(ME))
        c = _by_kind(answer, "unblocked")[f"tasks:{seeded['hid']}"]
        assert (c["why_key"], c["why"]) == ("unblocked_hidden",
                                            "A task that blocked this is closed.")
        got = json.dumps(answer, default=str)
        assert "Secret work zebra" not in got
        assert seeded["hid_blocker"] not in got

    async def test_the_reads_hold_the_rules(self, promoted, seeded, monkeypatch):  # noqa: F811
        """The three reads alone, on the instant of the feed."""
        from gateway.routes.projects import personal

        user = _member(ME)
        async with _as_member(promoted, promoted.org_b):
            quiet = await personal.my_quiet_tasks(user, limit=50, now=NOW)
            free = await personal.my_unblocked_since(user, DAY_START, limit=50)
            ahead = await personal.my_due_ahead(
                user, MIDNIGHT, MIDNIGHT + timedelta(days=15), limit=50,
                days=[TODAY + timedelta(days=d) for d in range(15)], zone="UTC")
        got = {r["id"]: r["disposition"] for r in quiet["rows"]}
        assert got == {seeded["wait"]: "WAITING", seeded["stale"]: "NEXT"}
        assert quiet["day_start_hour"] == DAY_START_HOUR and quiet["timezone"] == "UTC"
        rows = {r["id"]: r["unblocked_by"] for r in free["rows"]}
        assert set(rows) == {seeded["free"], seeded["hid"]}
        assert rows[seeded["hid"]] == {"readable": False, "id": None, "title": None,
                                       "closed_by": None,
                                       "closed_at": rows[seeded["hid"]]["closed_at"]}
        assert rows[seeded["free"]]["readable"] is True
        assert rows[seeded["free"]]["closed_by"] == COLL
        assert {seeded["away"], seeded["coll_day"], seeded["partial_day"],
                seeded["away_late"]} <= {r["id"] for r in ahead["rows"]}

    async def test_a_member_with_no_people_row_gets_the_other_three(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        """Done-when 10."""
        answer = await _feed(promoted, monkeypatch, _member(NOROW))
        assert answer["sources"]["tasks_followups"] == "ok"
        kinds = {c["signal_id"]: c["kind"] for c in answer["cards"]}
        assert kinds.get(f"tasks:{seeded['nr_wait']}") == "waiting_quiet"
        assert kinds.get(f"tasks:{seeded['nr_stale']}") == "stale_next"
        assert kinds.get(f"tasks:{seeded['nr_free']}") == "unblocked"
        assert f"tasks:{seeded['nr_due']}" not in kinds

    async def test_the_provider_opens_four_sessions_at_most(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        """Done-when 13: a counting wrapper of the one seam."""
        from gateway.routes.people import selfservice
        from gateway.routes.projects import personal

        opened: list[str] = []

        def _counting(name, real):
            @contextlib.asynccontextmanager
            async def _session():
                opened.append(name)
                async with real() as db:
                    yield db
            return _session

        monkeypatch.setattr(personal, "_tenant_session",
                            _counting("personal", personal._tenant_session))
        monkeypatch.setattr(selfservice, "_tenant_session",
                            _counting("selfservice", selfservice._tenant_session))
        async with _as_member(promoted, promoted.org_b):
            found = await feed._tasks_followups(_member(ME))
        assert opened == ["personal", "personal", "selfservice", "personal"]
        assert len(opened) <= 4
        assert {s.kind for s in found.signals()} == {
            "waiting_quiet", "stale_next", "unblocked", "absent_on_due"}

    async def test_with_the_list_empty_nothing_runs(self, promoted, seeded, monkeypatch):  # noqa: F811
        """Done-when 1, on the real database."""
        answer = await _feed(promoted, monkeypatch, _member(ME))
        assert "tasks_followups" in answer["sources"]
        _list(monkeypatch, "")
        async with _as_member(promoted, promoted.org_b):
            dark = await feed.shell_feed(limit=80, user=_member(ME))
        assert "tasks_followups" not in dark["sources"]
        assert all("source" not in c for c in dark["cards"])
        assert not {c["kind"] for c in dark["cards"]} & set(feed.TASK_FOLLOWUP_KINDS)


# ── Fix round 1: the tenant of each read, under the OWNER role ──────────────
#
# The owner role of production bypasses RLS. So each read must name the
# tenant itself, or a member of two organizations reads the other one's rows.
# The app role above cannot see this: FORCE RLS hides a missing predicate.

TWO = f"kinds-two-{TAG}@md3c.test"
THREE = f"kinds-three-{TAG}@md3c.test"
D0 = f"kinds-d0-{TAG}@md3c.test"
DSEED = f"kinds-dseed-{TAG}@md3c.test"
DNOTASK = f"kinds-dnotask-{TAG}@md3c.test"
LIM = f"kinds-lim-{TAG}@md3c.test"


@contextlib.asynccontextmanager
async def _as_owner(p, org: str):
    """Bind ``org``, and point the shared engine at the SUPERUSER, which
    bypasses RLS, as the owner role of production does."""
    from acb_common.db import bind_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(p.admin_engine.url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


def _settings(c, *, email: str, org: str, zone: str, hour: int) -> None:
    c.execute(text(
        "INSERT INTO user_settings (user_id, timezone, day_start_hour, day_end_hour, "
        "organization_id) VALUES (:e, :z, :h, 23, CAST(:o AS uuid))"),
        {"e": email, "z": zone, "h": hour, "o": org})


@pytest.fixture(scope="module")
def two_orgs(promoted, seeded):  # noqa: F811
    """TWO is a member of org B with a settings row in org A too, inserted
    first, and four tasks in org A that name TWO. THREE has a settings row in
    org A only. The day-start members and LIM live in org B."""
    p = promoted
    a, b = p.org_a, p.org_b
    ids: dict[str, str] = {}
    old = NOW - timedelta(days=40)
    with p.admin_engine.begin() as c:
        for email in (TWO, THREE, D0, DSEED, DNOTASK, LIM):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": email, "o": b})
        _settings(c, email=TWO, org=a, zone="Asia/Tokyo", hour=13)
        _settings(c, email=TWO, org=b, zone="UTC", hour=2)
        _settings(c, email=THREE, org=a, zone="Asia/Tokyo", hour=13)
        _settings(c, email=D0, org=b, zone="UTC", hour=0)
        _settings(c, email=DNOTASK, org=b, zone="UTC", hour=5)
        _settings(c, email=LIM, org=b, zone="UTC", hour=0)
        made = c.execute(text(
            "UPDATE people SET working_hours = CAST(:wh AS jsonb) WHERE lower(email) = :e "
            "AND organization_id = CAST(:o AS uuid)"),
            {"e": DSEED, "o": b, "wh": json.dumps({"days": [1, 2, 3, 4, 5, 6, 7],
                                                   "start": "10:00", "end": "18:00"})})
        assert made.rowcount == 1, "no directory row for DSEED"

        # Org A: one task of each read that names TWO.
        proj, lane = _project(c, org=a, name=f"OrgA {TAG}", grant_org=True)
        done = _lane(c, project=proj, name="Done", category="done", position=9)
        n = iter(range(500, 600))

        def task(title, *, due=None, assignee=TWO, lane_id=lane):
            return _task(c, org=a, project=proj, lane=lane_id, title=title, due=due,
                         assignee=assignee, number=next(n))

        ids["a_stale"] = task("ORGA stale")
        c.execute(text("UPDATE pm_tasks SET updated_at = :u WHERE id = CAST(:t AS uuid)"),
                  {"u": old, "t": ids["a_stale"]})
        ids["a_wait"] = task("ORGA wait", assignee=COLL)
        c.execute(text(
            "INSERT INTO pm_task_personal (task_id, member_email, disposition, delegated_at, "
            "organization_id, updated_at) VALUES (CAST(:t AS uuid), :e, 'WAITING', :dl, "
            "CAST(:o AS uuid), :u)"),
            {"t": ids["a_wait"], "e": TWO, "dl": NOW - timedelta(days=8), "o": a, "u": old})
        ids["a_free"] = task("ORGA free")
        blocker = task("ORGA blocker", assignee=COLL, lane_id=done)
        c.execute(text("UPDATE pm_tasks SET completed_at = :at WHERE id = CAST(:t AS uuid)"),
                  {"at": DAY_START, "t": blocker})
        c.execute(text(
            "INSERT INTO pm_task_links (source_task_id, target_task_id, link_type, created_by, "
            "organization_id) VALUES (CAST(:s AS uuid), CAST(:t AS uuid), 'blocks', :by, "
            "CAST(:o AS uuid))"), {"s": blocker, "t": ids["a_free"], "by": TWO, "o": a})
        ids["a_due"] = task("ORGA due", due=_at(3))

        # LIM (org B): away on day 1 and day 10, and 17 tasks due on the work
        # days between, more than the LIMIT of the due read.
        bproj, blane = _project(c, org=b, name=f"Lim {TAG}", grant_org=True)
        m = iter(range(700, 800))
        for day in (1, 10):
            c.execute(text(
                "INSERT INTO people_absences (person_id, starts_on, ends_on, kind, "
                "created_by, organization_id) SELECT id, :d, :d, 'away', :e, CAST(:o AS uuid) "
                "FROM people WHERE lower(email) = :e AND organization_id = CAST(:o AS uuid)"),
                {"d": TODAY + timedelta(days=day), "e": LIM, "o": b})
        for k in range(17):
            _task(c, org=b, project=bproj, lane=blane, title=f"Work day {k}",
                  due=_at(2 + k % 8, hour=1 + k // 8), assignee=LIM, number=next(m))
        ids["lim_far"] = _task(c, org=b, project=bproj, lane=blane, title="Far away task",
                               due=_at(10), assignee=LIM, number=next(m))
        # Non-vacuity: the org A rows exist, for the owner to leak.
        assert c.execute(text(
            "SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:o AS uuid) "
            "AND title LIKE 'ORGA %'"), {"o": a}).scalar_one() == 5
    return ids


class TestTheTenantUnderTheOwnerRole:
    async def test_the_lens_reads_return_nothing_of_another_org(
        self, promoted, two_orgs, monkeypatch,  # noqa: F811
    ):
        from gateway.routes.projects import personal

        user = _member(TWO)
        async with _as_owner(promoted, promoted.org_b):
            quiet = await personal.my_quiet_tasks(user, limit=50, now=NOW)
            free = await personal.my_unblocked_since(user, DAY_START, limit=50)
            ahead = await personal.my_due_ahead(
                user, MIDNIGHT, MIDNIGHT + timedelta(days=15), limit=50,
                days=[TODAY + timedelta(days=d) for d in range(15)], zone="UTC")
        got = {r["id"] for r in quiet["rows"] + free["rows"] + ahead["rows"]}
        assert not got & {two_orgs[k] for k in ("a_stale", "a_wait", "a_free", "a_due")}
        assert "ORGA" not in json.dumps([quiet, free, ahead], default=str)

    async def test_the_day_start_and_the_zone_are_this_orgs(
        self, promoted, two_orgs, monkeypatch,  # noqa: F811
    ):
        """The org A row of TWO (Tokyo, 13:00) was inserted first. The org B
        row (UTC, 02:00) is the one that counts."""
        from gateway.routes.projects import personal

        async with _as_owner(promoted, promoted.org_b):
            quiet = await personal.my_quiet_tasks(_member(TWO), limit=50, now=NOW)
        assert (quiet["timezone"], quiet["day_start_hour"]) == ("UTC", 2)

    async def test_stored_zone_reads_only_this_orgs_row(
        self, promoted, two_orgs, monkeypatch,  # noqa: F811
    ):
        from acb_common.db import tenant_session
        from gateway.routes.projects.personal import stored_zone

        async with _as_owner(promoted, promoted.org_b), tenant_session() as s:
            assert await stored_zone(s, TWO) == "UTC"
            assert await stored_zone(s, THREE) is None


class TestTheDayStartRule:
    """Fix round 1: the provider's day start is the one rule of
    ``state._hours``, read inside the quiet read's own session."""

    async def test_a_stored_0_reads_7(self, promoted, two_orgs, monkeypatch):  # noqa: F811
        from gateway.routes.projects import personal

        async with _as_member(promoted, promoted.org_b):
            quiet = await personal.my_quiet_tasks(_member(D0), limit=5, now=NOW)
        assert quiet["day_start_hour"] == 7

    async def test_no_row_seeds_from_the_people_schedule(
        self, promoted, two_orgs, monkeypatch,  # noqa: F811
    ):
        from gateway.routes.projects import personal

        async with _as_member(promoted, promoted.org_b):
            quiet = await personal.my_quiet_tasks(_member(DSEED), limit=5, now=NOW)
        assert quiet["day_start_hour"] == 9  # start 10:00 less one hour (D-PC-16)

    async def test_no_calendar_pane_reads_the_column_default(
        self, promoted, two_orgs, monkeypatch,  # noqa: F811
    ):
        from gateway.routes.projects import personal

        plain = UserContext(email=DNOTASK, role=UserRole.EMPLOYEE,
                            access=EffectiveAccess(role_granted=frozenset({"feature:projects"})))
        async with _as_member(promoted, promoted.org_b):
            quiet = await personal.my_quiet_tasks(plain, limit=5, now=NOW)
        # The stored 5 is not read: the state join reads 7 for this member.
        assert (quiet["day_start_hour"], quiet["timezone"]) == (None, "UTC")


class TestTheDueReadAsksTheDaysAway:
    async def test_a_late_day_away_keeps_its_task_past_the_limit(
        self, promoted, two_orgs, monkeypatch,  # noqa: F811
    ):
        """17 tasks due on the work days between two days away. The read
        asks only the days away, so the LIMIT of 16 keeps the far task."""
        async with _as_member(promoted, promoted.org_b):
            found = await feed._tasks_followups(_member(LIM))
        away = [s.signal_id for s in found.signals() if s.kind == "absent_on_due"]
        assert away == [f"tasks:{two_orgs['lim_far']}"]
