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

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from gateway.routes.shell import feed
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
DAY_START = datetime.combine(NOW.astimezone(UTC).date(), time.min, tzinfo=UTC)
TODAY = DAY_START.date()


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
    return DAY_START + timedelta(days=day_offset, hours=hour)


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
                "day_end_hour, organization_id) VALUES (:e, 'UTC', 0, 23, "
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
        assert set(away) == {f"tasks:{s['away']}"}, "done-when 8, 9"
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
            ahead = await personal.my_due_ahead(user, DAY_START, DAY_START + timedelta(days=15),
                                               limit=50)
        got = {r["id"]: r["disposition"] for r in quiet["rows"]}
        assert got == {seeded["wait"]: "WAITING", seeded["stale"]: "NEXT"}
        assert quiet["day_start_hour"] == 0 and quiet["timezone"] == "UTC"
        rows = {r["id"]: r["unblocked_by"] for r in free["rows"]}
        assert set(rows) == {seeded["free"], seeded["hid"]}
        assert rows[seeded["hid"]] == {"readable": False, "id": None, "title": None,
                                       "closed_by": None,
                                       "closed_at": rows[seeded["hid"]]["closed_at"]}
        assert rows[seeded["free"]]["readable"] is True
        assert rows[seeded["free"]]["closed_by"] == COLL
        assert {seeded["away"], seeded["coll_day"], seeded["partial_day"]} <= {
            r["id"] for r in ahead["rows"]}

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
