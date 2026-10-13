"""WS-58 MD-2a, R8: the card state on a REAL Postgres (``my_day_feed.md``
§6.8, §13.1, MD-2a done-when 1, 2, 5 and 7).

``my_day_card_state`` is new, so a hermetic fake would agree with any SQL it
is handed. This runs the REAL route functions, ``POST /shell/feed/state``
and ``GET /shell/feed``, on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``. It connects as the NON-privileged
role with RLS forced (``_as_member``), as ``test_shell_feed_r8.py`` does.

The four negative tests of the MD-2a ticket:

* **(i)** Org B's app role reads none of org A's rows.
* **(ii)** WITH CHECK refuses a row stamped with org A from a session bound
  to org B.
* **(iii)** Member B of the same org posts state for a ``signal_id`` that
  member A also holds. A's row and A's feed do not change.
* **(iv)** A body that names a member or an organization gets 422, and
  writes nothing.

It also writes each action through the real SQL (the expanding ``IN``, the
row lock, the upsert, the ``DATE`` column), and reads the feed after each.
A member with no Calendar pane takes the other path to the day hours
(``stored_zone``).

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from gateway.routes.shell import feed, state
from gateway.routes.shell import needs as shell
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_shell_feed import _a_daytime_zone
from tests.unit.test_shell_needs_r8 import NOW, _Frozen, _project, _task

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
#: Two members of org B, and one of org A.
A = f"state-a-{TAG}@md2.test"
B = f"state-b-{TAG}@md2.test"
X = f"state-x-{TAG}@md2.test"

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


@pytest.fixture(autouse=True)
def _one_instant(monkeypatch):
    """The route's clocks read ``NOW``, as ``test_shell_feed_r8.py`` does."""
    from gateway.routes.projects import personal

    monkeypatch.setattr(shell, "_now", lambda: NOW)
    monkeypatch.setattr(personal, "datetime", _Frozen)


def _user(email: str, *features: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=EffectiveAccess(role_granted=frozenset(
                           f"feature:{f}" for f in features)))


#: A has My Tasks, Email and the Calendar pane. B has Projects only, so the
#: state join reads B's zone from the lens (§6.8 item 8).
def _a() -> UserContext:
    return _user(A, "projects", "email", "tasks")


def _b() -> UserContext:
    return _user(B, "projects")


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """As the superuser: the members, their settings, and one overdue task
    for A and one for B in org B. One state row for X in org A."""
    p = promoted
    zone = _a_daytime_zone(NOW)
    ids: dict[str, str] = {"zone": zone}
    with p.admin_engine.begin() as c:
        for email, org in ((A, p.org_b), (B, p.org_b), (X, p.org_a)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": org})
        c.execute(text(
            "INSERT INTO user_settings (user_id, timezone, day_start_hour, day_end_hour, "
            "organization_id) VALUES (:e, :z, 1, 23, CAST(:o AS uuid))"),
            {"e": A, "z": zone, "o": p.org_b})
        c.execute(text(
            "INSERT INTO user_settings (user_id, timezone, organization_id) "
            "VALUES (:e, :z, CAST(:o AS uuid))"), {"e": B, "z": zone, "o": p.org_b})
        project, lane = _project(c, org=p.org_b, name=f"State {TAG}", grant_org=True)
        ids["a_task"] = _task(c, org=p.org_b, project=project, lane=lane,
                              title="Ship the bracket", due=NOW - timedelta(days=2),
                              assignee=A, number=1)
        ids["b_task"] = _task(c, org=p.org_b, project=project, lane=lane,
                              title="Draw the housing", due=NOW - timedelta(days=1),
                              assignee=B, number=2)
        c.execute(text(
            "INSERT INTO my_day_card_state (organization_id, member_email, signal_id, "
            "kind, state, fingerprint, visits) VALUES (CAST(:o AS uuid), :m, "
            "'tasks:org-a-only', 'overdue', 'seen', 'a41f09c2', 1)"),
            {"o": p.org_a, "m": X})
    yield ids
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM my_day_card_state WHERE member_email IN (:a, :b, :x)"),
                  {"a": A, "b": B, "x": X})


@pytest.fixture(autouse=True)
def _clean_state(promoted):  # noqa: F811
    """Each test starts with no state row for A or B."""
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM my_day_card_state WHERE member_email IN (:a, :b)"),
                  {"a": A, "b": B})


def _rows(p, member: str) -> dict[str, dict]:
    with p.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT signal_id, organization_id::text AS org, kind, state, dismiss_reason, "
            "fingerprint, visits, visits_day, last_seen_at, acted_at, not_useful_at, "
            "snoozed_until, "
            "snooze_on_change FROM my_day_card_state WHERE member_email = :m"),
            {"m": member})
        return {r.signal_id: dict(r._mapping) for r in got}


async def _feed(p, monkeypatch, user: UserContext) -> dict:
    # Room for a cold database, as test_shell_feed_r8.py gives it.
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
    monkeypatch.setattr(shell, "MAILBOX_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_BUDGET_S", 90.0)
    async with _as_member(p, p.org_b):
        return await feed.shell_feed(limit=80, user=user)


async def _post(p, user: UserContext, items: list[dict], org: str | None = None) -> dict:
    body = state.StateBody.model_validate({"items": items})
    async with _as_member(p, org or p.org_b):
        return await state.post_feed_state(body=body, user=user)


def _of(answer: dict, sid: str) -> dict | None:
    found = [c for c in answer["cards"] if c["signal_id"] == sid]
    assert len(found) <= 1
    return found[0] if found else None


def _item(sid: str, fp: str, action: str, until: str | None = None) -> dict:
    out = {"signal_id": sid, "state_fp": fp, "kind": "overdue", "action": action}
    if until:
        out["until"] = until
    return out


class TestTheTable:
    def test_the_migration_is_found_by_content_and_forces_rls(self, promoted):  # noqa: F811
        found = [f.name for f in _MIGRATIONS.glob("[0-9]*_*.sql")
                 if "CREATE TABLE IF NOT EXISTS my_day_card_state"
                 in f.read_text(encoding="utf-8")]
        assert len(found) == 1, found
        with promoted.admin_engine.connect() as c:
            row = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'my_day_card_state'::regclass")).one()
        assert tuple(row) == (True, True)

    def test_the_checks_refuse_an_unknown_state_and_reason(self, promoted):  # noqa: F811
        for column, value in (("state", "hidden"), ("dismiss_reason", "boring")):
            with (pytest.raises(DBAPIError, match="my_day_card_state_"),
                  promoted.admin_engine.begin() as c):
                c.execute(text(
                    "INSERT INTO my_day_card_state (organization_id, member_email, "
                    "signal_id, kind, state, dismiss_reason) VALUES (CAST(:o AS uuid), "
                    ":m, 'tasks:check', 'overdue', :s, :r)"),
                    {"o": promoted.org_b, "m": A,
                     "s": value if column == "state" else "seen",
                     "r": value if column == "dismiss_reason" else None})


class TestTheTenancyBoundary:
    async def test_i_org_b_reads_none_of_org_as_rows(self, promoted, seeded):  # noqa: F811
        from gateway.db import tenant_session

        async with _as_member(promoted, promoted.org_b), tenant_session() as db:
            in_b = (await db.execute(text(
                "SELECT count(*) FROM my_day_card_state WHERE member_email = :m"),
                {"m": X})).scalar_one()
            via_route = await state._read_rows(db, X, ["tasks:org-a-only"])
        async with _as_member(promoted, promoted.org_a), tenant_session() as db:
            in_a = (await db.execute(text(
                "SELECT count(*) FROM my_day_card_state WHERE member_email = :m"),
                {"m": X})).scalar_one()
        assert (in_b, via_route) == (0, {})
        assert in_a == 1  # non-vacuity: the row is there for its own org

    async def test_ii_with_check_refuses_org_as_row_from_a_session_bound_to_b(
        self, promoted, seeded,  # noqa: F811
    ):
        from gateway.db import tenant_session

        with pytest.raises(DBAPIError, match="row-level security"):
            async with _as_member(promoted, promoted.org_b), tenant_session() as db:
                await db.execute(text(
                    "INSERT INTO my_day_card_state (organization_id, member_email, "
                    "signal_id, kind, state) VALUES (CAST(:o AS uuid), :m, "
                    "'tasks:smuggled', 'overdue', 'dismissed')"),
                    {"o": promoted.org_a, "m": X})
        with promoted.admin_engine.connect() as c:
            assert c.execute(text(
                "SELECT count(*) FROM my_day_card_state WHERE signal_id = 'tasks:smuggled'"
            )).scalar_one() == 0

    async def test_iii_member_b_cannot_change_member_as_row(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        sid = f"tasks:{seeded['a_task']}"
        late = _of(await _feed(promoted, monkeypatch, _a()), sid)
        assert late is not None
        await _post(promoted, _a(), [_item(sid, late["state_fp"], "seen")])
        before = _rows(promoted, A)[sid]
        await _post(promoted, _b(), [_item(sid, late["state_fp"], "dismiss")])
        assert _rows(promoted, A)[sid] == before
        assert _rows(promoted, B)[sid]["state"] == "dismissed"  # B's own row
        after = _of(await _feed(promoted, monkeypatch, _a()), sid)
        assert after is not None and after["state"] == "seen"

    @pytest.mark.parametrize("extra", [{"member_email": B}, {"organization_id": "x"}])
    def test_iv_a_body_that_names_a_member_or_an_org_gets_422(
        self, promoted, seeded, extra,  # noqa: F811
    ):
        from acb_auth import get_current_user
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from gateway.routes.shell import router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = _a
        item = _item(f"tasks:{seeded['a_task']}", "a41f09c2", "dismiss")
        client = TestClient(app)
        assert client.post("/shell/feed/state",
                           json={"items": [item], **extra}).status_code == 422
        assert client.post("/shell/feed/state",
                           json={"items": [{**item, **extra}]}).status_code == 422
        assert _rows(promoted, A) == {} and _rows(promoted, B) == {}


class TestTheFirstWriteOfACardIsNeverLost:
    async def test_a_seen_that_waits_on_a_dismiss_does_not_erase_it(
        self, promoted, seeded,  # noqa: F811
    ):
        """Fix round 1, P2-b. Tab 1 dismisses a card that has no row yet, and
        has not committed. Tab 2 posts a seen of the same card. The seen
        must wait for the dismiss and then read it, so the card stays
        dismissed. Before the claim, the seen read no row, waited only at
        its upsert, and then wrote ``seen`` over the dismiss."""
        import asyncio

        from gateway.db import tenant_session

        sid = f"tasks:{seeded['a_task']}"
        fp = "a41f09c2"
        async with _as_member(promoted, promoted.org_b):
            first = tenant_session()
            db = await first.__aenter__()
            try:
                dismissed = state.next_row(
                    None, state.StateItem(signal_id=sid, state_fp=fp, kind="overdue",
                                          action="dismiss"), NOW, state.Day(ZoneInfo("UTC")))
                await db.execute(text(state._UPSERT_SQL), {
                    "member": A, "signal_id": sid,
                    **{c: dismissed[c] for c in state.COLUMNS}})
                second = asyncio.create_task(state.write_state(
                    _a(), [state.StateItem(signal_id=sid, state_fp=fp, kind="overdue",
                                           action="seen")], NOW))
                await asyncio.sleep(1.5)
                assert not second.done(), "the seen did not wait for the dismiss"
            finally:
                await first.__aexit__(None, None, None)  # commit tab 1
            written = await asyncio.wait_for(second, 30)
        assert written == 0  # a seen of a dismissed card writes nothing
        row = _rows(promoted, A)[sid]
        assert (row["state"], row["dismiss_reason"]) == ("dismissed", "dismiss")


class TestEachActionOnARealDatabase:
    async def test_seen_writes_the_visit_and_the_feed_reads_it(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        sid = f"tasks:{seeded['a_task']}"
        late = _of(await _feed(promoted, monkeypatch, _a()), sid)
        # Due two days ago: an old crossing keeps its group (§6.8 item 6).
        assert late["state"] == "new" and late["group"] == "needs_you"
        assert await _post(promoted, _a(), [_item(sid, late["state_fp"], "seen")]) == {
            "written": 1}
        row = _rows(promoted, A)[sid]
        assert (row["org"], row["state"], row["visits"]) == (promoted.org_b, "seen", 1)
        assert isinstance(row["visits_day"], date)
        assert row["last_seen_at"] == NOW
        again = _of(await _feed(promoted, monkeypatch, _a()), sid)
        assert (again["state"], again["visits"], again["group"]) == ("seen", 1, "needs_you")
        assert again["score"] == late["score"] - 8  # one visit of fatigue

    @pytest.mark.parametrize("until", ["later_today", "tomorrow", "next_week", "on_change"])
    async def test_each_snooze_hides_the_card_and_the_bell_keeps_it(
        self, promoted, seeded, monkeypatch, until,  # noqa: F811
    ):
        sid = f"tasks:{seeded['a_task']}"
        late = _of(await _feed(promoted, monkeypatch, _a()), sid)
        await _post(promoted, _a(), [_item(sid, late["state_fp"], "snooze", until)])
        row = _rows(promoted, A)[sid]
        assert row["state"] == "snoozed"
        assert row["snooze_on_change"] is (until == "on_change")
        if until == "on_change":
            assert row["snoozed_until"] is None
        else:
            assert row["snoozed_until"] > NOW
            local = row["snoozed_until"].astimezone(ZoneInfo(seeded["zone"]))
            if until != "later_today":
                assert (local.hour, local.minute) == (1, 0)  # A's day starts at 01:00
        answer = await _feed(promoted, monkeypatch, _a())
        assert _of(answer, sid) is None
        assert sid in [i["id"] for i in answer["needs"]["items"]]  # never in the bell

    @pytest.mark.parametrize("action", ["dismiss", "not_useful"])
    async def test_dismiss_and_not_useful_hide_the_card(
        self, promoted, seeded, monkeypatch, action,  # noqa: F811
    ):
        sid = f"tasks:{seeded['a_task']}"
        late = _of(await _feed(promoted, monkeypatch, _a()), sid)
        await _post(promoted, _a(), [_item(sid, late["state_fp"], action)])
        row = _rows(promoted, A)[sid]
        assert (row["state"], row["dismiss_reason"]) == ("dismissed", action)
        assert (row["not_useful_at"] == NOW) is (action == "not_useful")
        answer = await _feed(promoted, monkeypatch, _a())
        assert _of(answer, sid) is None
        assert sid in [i["id"] for i in answer["needs"]["items"]]

    async def test_acted_is_recorded_and_the_card_stays(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        sid = f"tasks:{seeded['a_task']}"
        late = _of(await _feed(promoted, monkeypatch, _a()), sid)
        await _post(promoted, _a(), [_item(sid, late["state_fp"], "acted")])
        row = _rows(promoted, A)[sid]
        assert (row["state"], row["acted_at"]) == ("acted", NOW)
        assert _of(await _feed(promoted, monkeypatch, _a()), sid) is not None

    async def test_a_member_with_no_calendar_pane_reads_and_writes_state(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        # §6.8 item 8: B's zone comes from the lens, with the column defaults.
        sid = f"tasks:{seeded['b_task']}"
        answer = await _feed(promoted, monkeypatch, _b())
        assert answer["sources"]["tasks"] == "ok"
        mine = _of(answer, sid)
        assert mine is not None and mine["state"] == "new"
        await _post(promoted, _b(), [_item(sid, mine["state_fp"], "seen")])
        assert _rows(promoted, B)[sid]["state"] == "seen"
        assert _of(await _feed(promoted, monkeypatch, _b()), sid)["state"] == "seen"

    async def test_the_feed_read_writes_no_row(self, promoted, seeded, monkeypatch):  # noqa: F811
        # §6.1: a card that leaves, or a card that is only read, writes nothing.
        await _feed(promoted, monkeypatch, _a())
        await _feed(promoted, monkeypatch, _b())
        assert _rows(promoted, A) == {} and _rows(promoted, B) == {}

    async def test_the_needs_view_is_the_needs_route_with_state_rows(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        sid = f"tasks:{seeded['a_task']}"
        late = _of(await _feed(promoted, monkeypatch, _a()), sid)
        await _post(promoted, _a(), [_item(sid, late["state_fp"], "dismiss")])
        answer = await _feed(promoted, monkeypatch, _a())
        async with _as_member(promoted, promoted.org_b):
            alone = await shell.shell_needs(limit=shell.DEFAULT_LIMIT, user=_a())
        assert answer["needs"] == alone
        assert sid in [i["id"] for i in alone["items"]]
