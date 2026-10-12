"""WS-57 MD-1a, R8: GET /shell/feed on a REAL Postgres (``my_day_feed.md``
MD-1a, done-when 8).

The feed adds no SQL of its own. It calls each app's own authorized read:
the needs providers, then the Calendar reads ``day_summary``,
``get_user_settings``, ``my_calendar`` and ``get_me``. So this runs the WHOLE
route, with each app's real SQL, on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``. It connects as the NON-privileged
role with RLS forced (``_as_member``), as ``test_shell_needs_r8.py`` does.

Three members of ONE organization:

* **ME** has an overdue task with a block that starts in 20 minutes, and a
  second block that overlaps it. That is the fold seed of §4.5: one card,
  with ``block_soon`` and ``block_overlap`` folded into the overdue card.
* **OTHER** has blocks of their own, at times that would make each Calendar
  kind fire for ME: a block in 10 minutes, an overlap, and a day planned
  past its capacity. OTHER also put a block of their own on ME's task, so a
  read that joined the wrong overlay row would show OTHER's time on ME's
  card. None of it reaches ME's cards.
* **PLANLESS** has no block today, and shares a task with OTHER that OTHER
  blocked for today. PLANLESS still gets ``no_plan_yet``, and no
  ``block_soon`` for the shared task.

Each source the member holds must read ``ok``. A provider that FAILED also
gives no card, so an empty answer alone would pass on a broken build.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

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
from tests.unit.test_shell_feed import _a_daytime_zone
from tests.unit.test_shell_needs_r8 import _project, _task

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"feed-me-{TAG}@md1.test"
OTHER = f"feed-other-{TAG}@md1.test"
PLANLESS = f"feed-planless-{TAG}@md1.test"
#: The sources of a member with Projects, Email and the Calendar pane.
SOURCES = {"tasks": "ok", "projects": "ok", "email": "ok", "approvals": "absent",
           "calendar": "ok"}


def _member(email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(
            {"feature:projects", "feature:email", "feature:tasks"})),
    )


def _block(c, *, org: str, task: str, email: str, start: datetime, minutes: int) -> None:
    """One member's block on one task: their own overlay row (D53.7)."""
    c.execute(text(
        "INSERT INTO pm_task_personal (task_id, member_email, scheduled_start, "
        "scheduled_end, organization_id) VALUES (CAST(:t AS uuid), :e, :s, :end, "
        "CAST(:o AS uuid))"),
        {"t": task, "e": email, "s": start, "end": start + timedelta(minutes=minutes),
         "o": org})


def _assign(c, *, org: str, task: str, email: str) -> None:
    c.execute(text(
        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
        "organization_id) VALUES (CAST(:t AS uuid), :a, :by, CAST(:o AS uuid))"),
        {"t": task, "a": email, "by": ME, "o": org})


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """The rows, seeded as the superuser, so the route's reads are the first
    the app role makes. Each member's day runs 01:00 to 23:00 in a zone
    where it is daytime now, so the blocks stay on the member's today."""
    p = promoted
    org = p.org_b
    zone = _a_daytime_zone()
    now = datetime.now(UTC)
    ids: dict[str, str] = {"zone": zone}
    with p.admin_engine.begin() as c:
        for email, capacity in ((ME, 360), (OTHER, 30), (PLANLESS, 360)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": org})
            c.execute(text(
                "INSERT INTO user_settings (user_id, timezone, day_start_hour, "
                "day_end_hour, daily_capacity_mins, organization_id) VALUES "
                "(:e, :z, 1, 23, :cap, CAST(:o AS uuid))"),
                {"e": email, "z": zone, "cap": capacity, "o": org})
        # PLANLESS works every day, so `no_plan_yet` does not hang on the
        # weekday of the run. The People app's own self read reads this. The
        # directory row exists already: the app_user insert made it.
        made = c.execute(text(
            "UPDATE people SET working_hours = CAST(:wh AS jsonb) "
            "WHERE lower(email) = :e AND organization_id = CAST(:o AS uuid)"),
            {"e": PLANLESS, "o": org, "wh": json.dumps({"days": [1, 2, 3, 4, 5, 6, 7]})})
        assert made.rowcount == 1, "no directory row for PLANLESS"

        project, lane = _project(c, org=org, name=f"Feed {TAG}", grant_org=True)

        def task(title: str, number: int, *, due: datetime | None, assignee: str) -> str:
            return _task(c, org=org, project=project, lane=lane, title=title,
                         due=due, assignee=assignee, number=number)

        ids["late"] = task("Ship the bracket", 1, due=now - timedelta(days=2), assignee=ME)
        ids["draw"] = task("Draw the housing", 2, due=None, assignee=ME)
        _block(c, org=org, task=ids["late"], email=ME, start=now + timedelta(minutes=20),
               minutes=60)
        _block(c, org=org, task=ids["draw"], email=ME, start=now + timedelta(minutes=50),
               minutes=60)
        ids["me_start"] = (now + timedelta(minutes=20)).isoformat()

        ids["theirs_a"] = task("Their private block", 3, due=None, assignee=OTHER)
        ids["theirs_b"] = task("Their other block", 4, due=None, assignee=OTHER)
        _block(c, org=org, task=ids["theirs_a"], email=OTHER,
               start=now + timedelta(minutes=10), minutes=60)
        _block(c, org=org, task=ids["theirs_b"], email=OTHER,
               start=now + timedelta(minutes=30), minutes=60)
        # OTHER's own block on ME's task, at another time.
        _block(c, org=org, task=ids["late"], email=OTHER, start=now + timedelta(minutes=5),
               minutes=20)

        ids["shared"] = task("Their shared work", 5, due=None, assignee=PLANLESS)
        _assign(c, org=org, task=ids["shared"], email=OTHER)
        _block(c, org=org, task=ids["shared"], email=OTHER,
               start=now + timedelta(minutes=15), minutes=30)
    return ids


async def _feed(p, monkeypatch, user: UserContext) -> dict:
    # Room for a cold database. The `sources` check proves each provider ran.
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
    monkeypatch.setattr(shell, "MAILBOX_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_BUDGET_S", 90.0)
    async with _as_member(p, p.org_b):
        return await feed.shell_feed(limit=80, user=user)


def _card(answer: dict, signal_id: str) -> dict:
    found = [c for c in answer["cards"] if c["signal_id"] == signal_id]
    assert len(found) == 1, (signal_id, [c["signal_id"] for c in answer["cards"]])
    return found[0]


def _today(seeded) -> str:
    return datetime.now(UTC).astimezone(ZoneInfo(seeded["zone"])).date().isoformat()


class TestTheFeedOnARealDatabase:
    async def test_the_fold_seed_is_one_card(self, promoted, seeded, monkeypatch):  # noqa: F811
        answer = await _feed(promoted, monkeypatch, _member(ME))
        assert answer["sources"] == SOURCES
        late = _card(answer, f"tasks:{seeded['late']}")
        assert late["kind"] == "overdue"
        assert sorted(late["folded"]) == ["block_overlap", "block_soon"]
        assert late["facts"]["overlaps"] == ["Draw the housing"]
        assert late["facts"]["days_late"] == 2
        assert [a["id"] for a in late["acts"]][:1] == ["done"]
        # The needs view holds the same task, under its needs id.
        assert f"tasks:{seeded['late']}" in [i["id"] for i in answer["needs"]["items"]]

    async def test_my_card_carries_my_block_not_anothers_on_the_same_task(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        answer = await _feed(promoted, monkeypatch, _member(ME))
        assert answer["sources"] == SOURCES
        late = _card(answer, f"tasks:{seeded['late']}")
        assert datetime.fromisoformat(late["facts"]["block_start"]) == \
            datetime.fromisoformat(seeded["me_start"])

    async def test_another_members_blocks_reach_no_card(self, promoted, seeded, monkeypatch):  # noqa: F811
        answer = await _feed(promoted, monkeypatch, _member(ME))
        assert answer["sources"] == SOURCES
        got = json.dumps(answer["cards"], default=str)
        for key in ("theirs_a", "theirs_b", "shared"):
            assert seeded[key] not in got, key
        assert "Their" not in got
        # block_soon: only my own block, folded into my overdue card.
        soon = [c["signal_id"] for c in answer["cards"]
                if c["kind"] == "block_soon" or "block_soon" in c["folded"]]
        assert soon == [f"tasks:{seeded['late']}"]
        # day_overbooked: OTHER is past capacity, and I am not.
        assert not [c for c in answer["cards"] if c["kind"] == "day_overbooked"]
        # block_overlap: only my own pair.
        overlap = [c for c in answer["cards"]
                   if c["kind"] == "block_overlap" or "block_overlap" in c["folded"]]
        assert [c["signal_id"] for c in overlap] == [f"tasks:{seeded['late']}"]

    async def test_no_plan_yet_reads_my_blocks_not_a_colleagues(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        answer = await _feed(promoted, monkeypatch, _member(PLANLESS))
        assert answer["sources"] == SOURCES
        day = _card(answer, f"calendar:{_today(seeded)}")
        assert (day["kind"], day["group"], day["app"]) == ("no_plan_yet", "needs_you",
                                                           "calendar")
        got = json.dumps(answer["cards"], default=str)
        assert seeded["shared"] not in got  # OTHER's block on our shared task

    async def test_the_other_member_sees_their_own_blocks(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        # Non-vacuity: the blocks held out above are real, and their owner
        # reads them.
        answer = await _feed(promoted, monkeypatch, _member(OTHER))
        assert answer["sources"] == SOURCES
        ids = [c["signal_id"] for c in answer["cards"]]
        assert f"tasks:{seeded['theirs_a']}" in ids
        day = _card(answer, f"calendar:{_today(seeded)}")
        assert day["kind"] == "day_overbooked"

    async def test_the_needs_view_is_the_needs_route(self, promoted, seeded, monkeypatch):  # noqa: F811
        answer = await _feed(promoted, monkeypatch, _member(ME))
        async with _as_member(promoted, promoted.org_b):
            alone = await shell.shell_needs(limit=shell.DEFAULT_LIMIT, user=_member(ME))
        assert answer["needs"] == alone
        assert set(alone["sources"]) == {"tasks", "projects", "email", "approvals"}
