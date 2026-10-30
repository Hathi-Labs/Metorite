"""WS-66 RE-9b, R8: a due reminder sends a real web push (frozen clock).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.6 "Settled
2026-10-30 (RE-9)" and §10.1 RE-9b.

The reminder loop (``gateway/reminders/loop.py``) and the push channel
(``gateway/reach/push_channel.py``) run on a phase-4 promoted scratch
database, as the non-privileged app role, so FORCE RLS binds. Two orgs. Each
tick takes a frozen ``now``. The sender is real: pywebpush encrypts and
``Vapid02`` signs. Only the push service is fake, at the door of
``outbound_guard.request_https``, and a test decrypts what went out.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_reach_push.py -v -rs

**The mutations (R7).** Each turned the named test red. Commit before each.

* M1. ``outbox._CLAIM_PUSH_SQL``: drop ``AND NOT {_HELD}``.
  ``test_r8_two_workers_send_each_push_once``.
* M2. ``push_channel._held_back``: drop the cap check.
  ``test_r8_the_eleventh_due_push_of_a_day_sends_nothing``.
* M3. ``push_channel._stale``: drop the state check.
  ``test_r8_a_done_dropped_or_moved_reminder_sends_nothing``.
* M4. ``outbox._SENT_SINCE_SQL``: drop ``AND channel = 'whatsapp'``.
  ``test_r8_a_push_row_never_counts_in_the_whatsapp_cap``.
* M5. ``push_channel.quiet_blocks``: answer False.
  ``test_r8_a_check_back_and_a_give_up_plan_nothing_in_quiet_hours``.
* M6. ``push_channel.push_on``: drop ``push_flags.enabled()``.
  ``test_r8_with_push_off_no_row_is_planned_so_a_later_flip_sends_no_burst``.
* M7. ``push_channel.read_facts``: a TTL 1,000 times longer.
  ``test_r8_a_planned_row_older_than_its_ttl_expires``.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from gateway import outbound_guard
from gateway.push import sender
from gateway.reach import flags as reach_flags
from gateway.reach import outbox, push_channel, push_message
from gateway.reminders import loop
from gateway.routes.projects import reminders as rm
from gateway.routes.shell import cache, needs
from gateway.routes.shell.state import Day
from requests.structures import CaseInsensitiveDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import _exec_file, tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_push_platform import vapid_pair
from tests.unit.test_push_subscriptions import _Browser

pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

TAG = uuid.uuid4().hex[:8]
#: Org B, UTC, 07:00 to 22:00.
ALICE = f"alice-{TAG}@re9b.test"
BOB = f"bob-{TAG}@re9b.test"
#: Org B, Kolkata, 08:00 to 20:00.
PRIYA = f"priya-{TAG}@re9b.test"
#: Org A, UTC, 07:00 to 22:00.
CAROL = f"carol-{TAG}@re9b.test"
MEMBERS = (ALICE, BOB, PRIYA, CAROL)
#: Org B, UTC. Two settings rows of one member: the session wrote the
#: mixed case, and ``reminders.member_email`` holds the lower case.
RAVI = f"ravi.k-{TAG}@re9b.test"
RAVI_MIXED = f"Ravi.K-{TAG}@re9b.test"
#: Every address that a test writes, for the wipe.
WIPED = (*MEMBERS, RAVI, RAVI_MIXED)
IST = ZoneInfo("Asia/Kolkata")
PRIYA_DAY = Day(IST, 8, 20)
SUBJECT = "mailto:push@re9b.test"


def _utc(day: int, hour: int, minute: int = 0) -> datetime:
    """An instant of March 2099 in UTC. Tuesday 10 March is day 10."""
    return datetime(2099, 3, day, hour, minute, tzinfo=UTC)


def _ist(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2099, 3, day, hour, minute, tzinfo=IST)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


# ── The push service, faked at the one guard ────────────────────────────────


class _PushService:
    """Answers each push with ``status`` and keeps every request. ``delay``
    holds each answer open, so two workers overlap."""

    def __init__(self) -> None:
        self.status = 201
        self.delay = 0.0
        self.posts: list[dict[str, Any]] = []

    async def request_https(self, method, url, *, headers=None, content=None, json=None):
        self.posts.append({"method": method, "url": url, "data": content,
                           "headers": CaseInsensitiveDict(headers or {})})
        if self.delay:
            await asyncio.sleep(self.delay)
        return outbound_guard.Answer(status_code=self.status, body=b"")


# ── The world ──────────────────────────────────────────────────────────────


def _endpoint(member: str) -> str:
    return f"https://fcm.googleapis.com/fcm/send/re9b-{TAG}-{member.split('@')[0]}"


def _subscribe(p: Any, member: str, org: str, browser: _Browser) -> None:
    sub = browser.sub(_endpoint(member))
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO push_subscriptions (organization_id, member_email, endpoint, "
            "p256dh, auth) VALUES (CAST(:o AS uuid), :m, :e, :p, :a)"),
            {"o": org, "m": member, "e": sub.endpoint, "p": sub.p256dh, "a": sub.auth})


def _wipe(p: Any) -> None:
    with p.admin_engine.begin() as c:
        for sql in ("DELETE FROM reminders WHERE member_email = ANY(:m)",
                    "DELETE FROM reach_outbox WHERE member_email = ANY(:m)",
                    "DELETE FROM push_subscriptions WHERE member_email = ANY(:m)",
                    "DELETE FROM user_settings WHERE user_id = ANY(:m)",
                    "DELETE FROM app_user WHERE email = ANY(:m)"):
            c.execute(text(sql), {"m": list(WIPED)})


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Four members in two orgs, each with one device. Reminders and push
    on, every ``REACH_*`` switch off, a fake directory and a fake push
    service."""
    p = promoted
    _wipe(p)
    with p.admin_engine.begin() as c:
        for email, org, zone, start, end in (
                (ALICE, p.org_b, "UTC", 7, 22), (BOB, p.org_b, "UTC", 7, 22),
                (PRIYA, p.org_b, "Asia/Kolkata", 8, 20), (CAROL, p.org_a, "UTC", 7, 22)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
                {"e": email, "o": org})
            c.execute(text(
                "INSERT INTO user_settings (organization_id, user_id, timezone, "
                "day_start_hour, day_end_hour) VALUES (CAST(:o AS uuid), :u, :z, :s, :e)"),
                {"o": org, "u": email, "z": zone, "s": start, "e": end})
        names = dict(c.execute(text(
            "SELECT id::text, display_name FROM organization WHERE id IN "
            "(CAST(:a AS uuid), CAST(:b AS uuid))"), {"a": p.org_a, "b": p.org_b}).all())
    browsers = {m: _Browser() for m in MEMBERS}
    for member in MEMBERS:
        _subscribe(p, member, p.org_a if member == CAROL else p.org_b, browsers[member])

    monkeypatch.setattr(rm, "get_settings", lambda: SimpleNamespace(calendar_reminders=True))
    private, public = vapid_pair()
    s = get_settings()
    for key, value in (("web_push_enabled", True), ("vapid_private_key", private),
                       ("vapid_public_key", public), ("vapid_subject", SUBJECT),
                       ("reach_enabled", False), ("reach_orgs", ""), ("reach_members", ""),
                       ("reach_daily_cap", 3)):
        monkeypatch.setattr(s, key, value)
    svc = _PushService()
    monkeypatch.setattr(outbound_guard, "request_https", svc.request_https)
    ns = SimpleNamespace(p=p, active=set(MEMBERS), service=svc, browsers=browsers,
                         names=names)

    async def is_active(org: str, member: str) -> bool:
        return member in ns.active

    async def orgs() -> list[str]:
        return [p.org_a, p.org_b]

    async def bump(org: Any, email: Any) -> None:
        return None

    monkeypatch.setattr(loop, "_is_active", is_active)
    monkeypatch.setattr(loop, "_orgs", orgs)
    monkeypatch.setattr(cache, "bump_member_safe", bump)
    loop._stopping.clear()
    yield ns
    _wipe(p)


def _seed(world: Any, member: str, fire_at: datetime, *, org: str | None = None,
          **cols: Any) -> str:
    """One reminder, as the superuser, past RLS. ``cols`` sets any column."""
    values = {"title": "Get a haircut", "kind": "action", "state": "scheduled",
              "important": False, "estimate_minutes": None, "cycles": 0,
              "first_fired_at": None, "next_check_at": None, "tz": None, **cols}
    with world.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO reminders (organization_id, member_email, title, kind, fire_at, "
            "state, important, estimate_minutes, cycles, first_fired_at, next_check_at, tz) "
            "VALUES (CAST(:o AS uuid), :m, :title, :kind, :fire_at, :state, :important, "
            ":estimate_minutes, :cycles, :first_fired_at, :next_check_at, :tz) RETURNING id"),
            {"o": org or world.p.org_b, "m": member, "fire_at": fire_at, **values},
        ).scalar_one())


def _outbox(world: Any, member: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT id::text, organization_id::text AS org, channel, kind, state, reason, "
            "dedupe_key, body, signal_ids, local_date, sent_at FROM reach_outbox "
            "WHERE member_email = :m ORDER BY created_at, id"), {"m": member}))


def _ledger(world: Any, rid: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT id::text, event, channel, detail FROM reminder_events "
            "WHERE reminder_id = CAST(:i AS uuid) ORDER BY at, id"), {"i": rid}))


def _ticks(world: Any, *instants: datetime) -> list[dict[str, Counter]]:
    """One tick of the loop at each frozen instant, as the app role."""
    async def go() -> list[dict[str, Counter]]:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return [await loop.run_tick(at) for at in instants]
    return asyncio.run(go())


def _in_org(world: Any, org: str, work: Any) -> Any:
    """Run the coroutine function *work* bound to *org*, as the app role."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            token = bind_tenant(org)
            try:
                return await work()
            finally:
                release_tenant(token)
    return asyncio.run(go())


def _steps(world: Any, org: str, rids: list[str], at: datetime) -> list[str | None]:
    """The loop's step of each row, with no send step: the rows stay planned."""
    async def work() -> list[str | None]:
        return [await loop.step(org, rid, at) for rid in rids]
    return _in_org(world, org, work)


def _send(world: Any, org: str, at: datetime) -> Counter:
    async def work() -> Counter:
        return await push_channel.send_planned(org, lambda: at)
    return _in_org(world, org, work)


def _org_name(world: Any, org: str) -> str:
    """The name that the payload body holds: the org's display name."""
    return (world.names.get(org) or "").strip() or "Metorite"


def _payloads(world: Any, member: str) -> list[dict[str, Any]]:
    mine = [p for p in world.service.posts if p["url"] == _endpoint(member)]
    return [world.browsers[member].decrypt(p["data"]) for p in mine]


# ── The migration ──────────────────────────────────────────────────────────


def _the_migration() -> Path:
    found = [f for f in _MIGRATIONS.glob("[0-9]*_*.sql")
             if "reach_outbox_channel_known" in (body := f.read_text(encoding="utf-8"))
             and "'web_push'" in body]
    assert len(found) == 1, found
    return found[0]


class TestTheMigration:
    def test_found_by_content_and_names_no_create_table(self, promoted):  # noqa: F811
        body = _the_migration().read_text(encoding="utf-8")
        # test_reach_r8.py finds the table's own migration by this text.
        assert "CREATE TABLE IF NOT EXISTS reach_outbox" not in body

    def test_the_checks_take_the_new_values_and_refuse_others(self, promoted):  # noqa: F811
        p = promoted
        ok = ("INSERT INTO reach_outbox (organization_id, member_email, channel, kind, "
              "dedupe_key, body) VALUES (CAST(:o AS uuid), :m, :ch, :k, :key, 'reminder')")
        with p.admin_engine.begin() as c:
            for n, kind in enumerate(("reminder_due", "reminder_check",
                                      "reminder_unconfirmed", "brief", "interrupt")):
                c.execute(text(ok), {"o": p.org_a, "m": CAROL, "ch": "web_push", "k": kind,
                                     "key": f"mig-{TAG}-{n}"})
            c.execute(text(ok), {"o": p.org_a, "m": CAROL, "ch": "whatsapp", "k": "request",
                                 "key": f"mig-{TAG}-wa"})
            c.execute(text("DELETE FROM reach_outbox WHERE member_email = :m"), {"m": CAROL})
        for column, row in (("reach_outbox_channel_known", ("sms", "reminder_due")),
                            ("reach_outbox_kind_known", ("web_push", "reminder_snoozed"))):
            with pytest.raises(DBAPIError, match=column), p.admin_engine.begin() as c:
                c.execute(text(ok), {"o": p.org_a, "m": CAROL, "ch": row[0], "k": row[1],
                                     "key": f"mig-{TAG}-bad"})

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


# ── The send (§10.1 RE-9b acceptance) ──────────────────────────────────────


def test_r8_a_due_reminder_sends_one_push_within_90_s_of_fire_at(world, app_engine):  # noqa: F811
    from tests.unit.test_email_keep_separate import _assert_non_priv

    _assert_non_priv(app_engine)
    org = world.p.org_b
    fire = _utc(10, 15)
    rid = _seed(world, ALICE, fire)
    # Reach is off in every way, and the push still goes (decision 1).
    assert not reach_flags.enabled() and not reach_flags.org_allowed(org)
    assert reach_flags.members() == frozenset()

    # The worst case: the tick before the fire, then the next one.
    before, tick = _ticks(world, fire - timedelta(seconds=1),
                          fire + timedelta(seconds=loop.TICK_SECONDS - 1))
    assert before == {}
    assert tick[org]["fired"] == 1 and tick[org]["push_sent"] == 1
    # One tick and the whole send budget fit inside the 90 seconds.
    assert loop.TICK_SECONDS + sender.SEND_BUDGET_S <= 90

    [post] = world.service.posts
    assert post["url"] == _endpoint(ALICE)
    assert post["headers"]["Urgency"] == "high" and post["headers"]["TTL"] == "3600"
    [payload] = _payloads(world, ALICE)
    assert payload == {"title": "Get a haircut at 3:00 PM", "body": _org_name(world, org),
                       "url": f"/chat?reminder={rid}", "tag": rid}

    [row] = _outbox(world, ALICE)
    fired = next(e for e in _ledger(world, rid) if e.event == "fired")
    assert (row.org, row.channel, row.kind, row.state) == (org, "web_push", "reminder_due",
                                                           "sent")
    assert row.dedupe_key == f"reminder:{rid}:{fired.id}"
    assert row.body == "reminder" and row.reason is None and row.sent_at is not None
    assert row.signal_ids == [f"reminders:{rid}"] and str(row.local_date) == "2099-03-10"
    # The ledger: the fire, then its delivery on the push channel.
    assert [(e.event, e.channel) for e in _ledger(world, rid)] == [
        ("fired", None), ("delivered", "push")]
    delivered = _ledger(world, rid)[1].detail
    assert delivered["step_id"] == fired.id and delivered["sent"] == 1
    assert delivered["by"] == "metorite"


def test_r8_a_hidden_details_member_gets_no_title(world) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE user_settings SET my_day_prefs = CAST(:v AS jsonb) WHERE user_id = :u"),
            {"v": json.dumps({"hide_push_details": True}), "u": ALICE})
    rid = _seed(world, ALICE, _utc(10, 15), title="Pick up the biopsy result")
    _ticks(world, _utc(10, 15))
    [payload] = _payloads(world, ALICE)
    assert payload["title"] == push_message.HIDDEN_TITLE
    assert payload["url"] == f"/chat?reminder={rid}"
    words = json.dumps(payload)
    assert "biopsy" not in words and "3:00" not in words
    assert payload["body"] == _org_name(world, world.p.org_b)


def test_r8_hide_under_any_spelling_of_the_address_hides(world) -> None:
    """Repair round 1, finding 1. The session wrote hide ON under
    ``Ravi.K``, and a lower-case row with no key also exists. The reminder
    holds the lower case. One ``true`` under any spelling hides (ruling i)."""
    org = world.p.org_b
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": RAVI, "o": org})
        for uid, prefs in ((RAVI_MIXED, {"hide_push_details": True}),
                           (RAVI, {"open_in_app": True})):
            c.execute(text(
                "INSERT INTO user_settings (organization_id, user_id, timezone, "
                "day_start_hour, day_end_hour, my_day_prefs) VALUES (CAST(:o AS uuid), :u, "
                "'UTC', 7, 22, CAST(:v AS jsonb))"), {"o": org, "u": uid, "v": json.dumps(prefs)})
        rows = c.execute(text(
            "SELECT count(*) FROM user_settings WHERE lower(user_id) = :u"), {"u": RAVI})
        assert rows.scalar_one() == 2  # non-vacuity: two spellings, two rows
    world.browsers[RAVI] = _Browser()
    _subscribe(world.p, RAVI, org, world.browsers[RAVI])
    world.active.add(RAVI)
    rid = _seed(world, RAVI, _utc(10, 15), title="Pick up the biopsy result")
    _ticks(world, _utc(10, 15))
    [payload] = _payloads(world, RAVI)
    assert payload["title"] == push_message.HIDDEN_TITLE and payload["tag"] == rid
    words = json.dumps(payload)
    assert "biopsy" not in words and "3:00" not in words


def test_r8_a_replay_of_the_same_tick_sends_once(world) -> None:
    rid = _seed(world, ALICE, _utc(10, 15))
    _ticks(world, _utc(10, 15), _utc(10, 15), _utc(10, 15, 1))
    assert len(world.service.posts) == 1
    assert [r.state for r in _outbox(world, ALICE)] == ["sent"]
    assert [e.event for e in _ledger(world, rid)] == ["fired", "delivered"]


def test_r8_two_workers_send_each_push_once(world) -> None:
    org, at = world.p.org_b, _utc(10, 15)
    rids = [_seed(world, who, at, title=f"Row {n}")
            for n, who in enumerate((ALICE, BOB) * 3)]
    assert _steps(world, org, rids, at) == ["fired"] * len(rids)
    world.service.delay = 0.2  # each send holds its claim open

    async def race() -> list[Counter]:
        return list(await asyncio.gather(push_channel.send_planned(org, lambda: at),
                                         push_channel.send_planned(org, lambda: at)))

    first, second = _in_org(world, org, race)
    assert first["push_sent"] + second["push_sent"] == len(rids)
    # Non-vacuity: each worker sent, and each met a row that the other held.
    assert first["push_sent"] > 0 and second["push_sent"] > 0
    assert first["push_held"] + second["push_held"] > 0
    tags = Counter(p["tag"] for m in (ALICE, BOB) for p in _payloads(world, m))
    assert tags == Counter({rid: 1 for rid in rids})


def test_r8_two_whole_loops_fire_and_send_each_reminder_once(world) -> None:
    """The fire claim (``FOR UPDATE SKIP LOCKED``) and the push claim
    together: two ``run_tick`` at once, each with its steps and its send
    phase."""
    org, at = world.p.org_b, _utc(10, 15)
    rids = [_seed(world, who, at, title=f"Loop {n}")
            for n, who in enumerate((ALICE, BOB) * 3)]
    world.service.delay = 0.1

    async def race() -> list[Counter]:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            ticks = await asyncio.gather(loop.run_tick(at), loop.run_tick(at))
        return [tick.get(org, Counter()) for tick in ticks]

    first, second = asyncio.run(race())
    assert first["fired"] + second["fired"] == len(rids)
    assert first["push_sent"] + second["push_sent"] == len(rids)
    tags = Counter(p["tag"] for m in (ALICE, BOB) for p in _payloads(world, m))
    assert tags == Counter({rid: 1 for rid in rids})
    for rid in rids:
        assert [e.event for e in _ledger(world, rid)] == ["fired", "delivered"]


def test_r8_the_push_of_the_second_cycle_is_sent(world) -> None:
    rid = _seed(world, ALICE, _utc(10, 15))
    # Fire, check back, brief and move, then the fire of the second cycle.
    _ticks(world, _utc(10, 15), _utc(10, 16), _utc(11, 7), _utc(11, 15))
    rows = _outbox(world, ALICE)
    assert [(r.kind, r.state) for r in rows] == [
        ("reminder_due", "sent"), ("reminder_check", "sent"), ("reminder_due", "sent")]
    assert len({r.dedupe_key for r in rows}) == 3
    steps = [e.id for e in _ledger(world, rid) if e.event in ("fired", "check_back")]
    assert [r.dedupe_key for r in rows] == [f"reminder:{rid}:{s}" for s in steps]
    assert [str(r.local_date) for r in rows] == ["2099-03-10", "2099-03-10", "2099-03-11"]
    assert len(world.service.posts) == 3
    assert [e.event for e in _ledger(world, rid)].count("delivered") == 3


def test_r8_a_done_dropped_or_moved_reminder_sends_nothing(world) -> None:
    org, at = world.p.org_b, _utc(10, 15)
    done, dropped, moved, refired, keep = (
        _seed(world, ALICE, at, title=f"R{n}") for n in range(5))
    _steps(world, org, [done, dropped, moved, refired, keep], at)
    with world.p.admin_engine.begin() as c:
        for rid, state in ((done, "done"), (dropped, "dropped"), (moved, "scheduled")):
            c.execute(text("UPDATE reminders SET state = :s WHERE id = CAST(:i AS uuid)"),
                      {"s": state, "i": rid})
        for rid, event in ((done, "done"), (dropped, "dropped"), (moved, "rescheduled"),
                           (refired, "rescheduled"), (refired, "fired")):
            c.execute(text(
                "INSERT INTO reminder_events (organization_id, reminder_id, event, at) "
                "VALUES (CAST(:o AS uuid), CAST(:i AS uuid), :e, clock_timestamp())"),
                {"o": org, "i": rid, "e": event})
    got = _send(world, org, at)
    assert got == Counter(push_closed=2, push_moved=2, push_sent=1)
    [payload] = _payloads(world, ALICE)
    assert payload["tag"] == keep
    reasons = {r.dedupe_key.split(":")[1]: (r.state, r.reason) for r in _outbox(world, ALICE)}
    assert reasons == {done: ("skipped", "closed"), dropped: ("skipped", "closed"),
                       moved: ("skipped", "moved"), refired: ("skipped", "moved"),
                       keep: ("sent", None)}
    for rid in (done, dropped, moved, refired):
        assert "delivered" not in [e.event for e in _ledger(world, rid)]


# ── Quiet hours ────────────────────────────────────────────────────────────


def test_r8_a_due_reminder_sends_in_quiet_hours(world) -> None:
    """``reminder_due`` is urgent: the member's own time breaks quiet hours."""
    rid = _seed(world, PRIYA, _ist(10, 23))
    [tick] = _ticks(world, _ist(10, 23))
    assert tick[world.p.org_b]["push_sent"] == 1
    [payload] = _payloads(world, PRIYA)
    assert payload["tag"] == rid and payload["title"] == "Get a haircut at 5:30 PM"


def test_r8_a_check_back_and_a_give_up_plan_nothing_in_quiet_hours(world) -> None:
    """The loop already waits in quiet hours (``step`` answers ``quiet``), so
    this calls the plan step itself at a quiet instant."""
    org, night = world.p.org_b, _ist(10, 23)
    rid = _seed(world, PRIYA, _ist(10, 18), state="due", first_fired_at=_ist(10, 18))
    with world.p.admin_engine.begin() as c:
        step_id = str(c.execute(text(
            "INSERT INTO reminder_events (organization_id, reminder_id, event) VALUES "
            "(CAST(:o AS uuid), CAST(:i AS uuid), 'check_back') RETURNING id"),
            {"o": org, "i": rid}).scalar_one())
    row = SimpleNamespace(id=rid, member_email=PRIYA)

    async def plan_all() -> dict[str, str | None]:
        out = {}
        async with tenant_session(org) as db:
            for kind in ("reminder_check", "reminder_unconfirmed", "reminder_due"):
                out[kind] = await push_channel.plan_reminder_push(
                    db, row, step_id, kind, PRIYA_DAY, night, due_at=night)
        return out

    planned = _in_org(world, org, plan_all)
    assert planned["reminder_check"] is None and planned["reminder_unconfirmed"] is None
    assert planned["reminder_due"] is not None  # urgent: the control
    assert [r.kind for r in _outbox(world, PRIYA)] == ["reminder_due"]


def test_r8_a_check_back_planned_before_quiet_hours_does_not_send_in_them(world) -> None:
    org = world.p.org_b
    rid = _seed(world, PRIYA, _ist(10, 18), state="due", first_fired_at=_ist(10, 18),
                next_check_at=_ist(10, 19, 59))
    assert _steps(world, org, [rid], _ist(10, 19, 59)) == ["check_back"]
    assert _send(world, org, _ist(10, 20, 30)) == Counter(push_quiet=1)
    assert world.service.posts == []
    [row] = _outbox(world, PRIYA)
    assert (row.kind, row.state, row.reason) == ("reminder_check", "skipped", "quiet")


# ── A late step (ruling j) ─────────────────────────────────────────────────


def _state(world: Any, rid: str) -> str:
    with world.p.admin_engine.connect() as c:
        return str(c.execute(text("SELECT state FROM reminders WHERE id = CAST(:i AS uuid)"),
                             {"i": rid}).scalar_one())


def test_r8_a_fire_hours_late_in_quiet_hours_sends_no_push(world, monkeypatch) -> None:
    """Repair round 1, finding 2. An outage or a flag flip runs the fire
    three hours late, at night. ``reminder_due`` may break quiet hours, so
    without ruling j the member gets an old reminder at 22:30. The fire and
    the card stay. The control fires 30 s late and sends."""
    from tests.unit._projects_fakes import member_user

    org = world.p.org_b
    at = _ist(10, 22, 30) + timedelta(seconds=30)
    late = _seed(world, PRIYA, _ist(10, 19, 30), title="Late one")
    fresh = _seed(world, PRIYA, _ist(10, 22, 30), title="Fresh one")
    [tick] = _ticks(world, at)
    assert tick[org]["fired"] == 2 and tick[org]["push_sent"] == 1
    [payload] = _payloads(world, PRIYA)
    assert payload["tag"] == fresh
    # No row for the late fire, so it takes no slot of the cap of the day.
    [row] = _outbox(world, PRIYA)
    assert row.dedupe_key.split(":")[1] == fresh and row.state == "sent"
    assert _state(world, late) == "due"
    assert [e.event for e in _ledger(world, late)] == ["fired"]
    # The card of the late fire is in My Day.
    monkeypatch.setattr(needs, "_now", lambda: at + timedelta(minutes=1))

    async def cards() -> needs.Found:
        return await needs._reminders(member_user(PRIYA))

    kinds = {s.entity["id"]: s.kind for s in _in_org(world, org, cards).signals()}
    assert kinds[late] == "reminder_due"


def test_r8_a_late_check_back_or_give_up_plans_no_push(world) -> None:
    """Ruling j for the other two kinds: the lateness counts from
    ``next_check_at``. A check-back 4 h 45 min late (TTL 4 h) and a give-up
    16 h late (TTL 12 h) plan nothing. The controls run 30 s late."""
    org, at = world.p.org_b, _utc(10, 13)
    just = at - timedelta(seconds=30)
    late_check = _seed(world, ALICE, _utc(10, 8), state="due", first_fired_at=_utc(10, 8),
                       next_check_at=_utc(10, 8, 15))
    late_give_up = _seed(world, ALICE, _utc(7, 8), state="due", first_fired_at=_utc(7, 8),
                         next_check_at=_utc(9, 21))
    check = _seed(world, BOB, _utc(10, 12), state="due", first_fired_at=_utc(10, 12),
                  next_check_at=just)
    give_up = _seed(world, BOB, _utc(7, 8), state="due", first_fired_at=_utc(7, 8),
                    next_check_at=just)
    got = _steps(world, org, [late_check, late_give_up, check, give_up], at)
    assert got == ["check_back", "gave_up", "check_back", "gave_up"]
    assert _outbox(world, ALICE) == []
    planned = {r.dedupe_key.split(":")[1]: r.kind for r in _outbox(world, BOB)}
    assert planned == {check: "reminder_check", give_up: "reminder_unconfirmed"}


def test_r8_a_due_push_never_goes_more_than_its_ttl_after_fire_at(world, monkeypatch) -> None:
    """Ruling j at the send step. The fire planned on time, and no send came
    for an hour (for example, the keys were missing). The row is young on
    its own clock, but the send comes more than the TTL after ``fire_at``, so
    it ends ``expired``. The control fired one minute before that send."""
    org, at = world.p.org_b, _utc(10, 15)
    ttl = push_message.ttl_of("reminder_due")
    old = _seed(world, ALICE, at)
    _steps(world, org, [old], at)
    send_at = at + timedelta(seconds=ttl + 60)
    fresh = _seed(world, BOB, send_at - timedelta(seconds=60))
    _steps(world, org, [fresh], send_at - timedelta(seconds=60))
    assert _send(world, org, send_at) == Counter(push_expired=1, push_sent=1)
    assert [(r.state, r.reason) for r in _outbox(world, ALICE)] == [("skipped", "expired")]
    assert [p["tag"] for p in _payloads(world, BOB)] == [fresh]
    assert _payloads(world, ALICE) == []


# ── The caps ───────────────────────────────────────────────────────────────


def test_r8_the_eleventh_due_push_of_a_day_sends_nothing(world) -> None:
    org = world.p.org_b
    rids = [_seed(world, ALICE, _utc(10, 15) + timedelta(seconds=n), title=f"R{n:02d}")
            for n in range(push_channel.DUE_CAP + 1)]
    [tick] = _ticks(world, _utc(10, 15, 1))
    assert tick[org]["fired"] == 11
    assert tick[org]["push_sent"] == 10 and tick[org]["push_capped"] == 1
    assert len(world.service.posts) == 10
    states = [(r.state, r.reason) for r in _outbox(world, ALICE)]
    assert states == [("sent", None)] * 10 + [("skipped", "capped")]
    assert "delivered" not in [e.event for e in _ledger(world, rids[-1])]
    # Another member's day is their own.
    _seed(world, BOB, _utc(10, 15))
    assert _ticks(world, _utc(10, 15, 2))[0][org]["push_sent"] == 1


def test_r8_a_check_back_over_the_cap_sends_nothing_and_its_card_stays(
    world, monkeypatch,
) -> None:
    from tests.unit._projects_fakes import member_user

    org, at = world.p.org_b, _utc(10, 16)
    assert reach_flags.daily_cap() == 3
    rids = [_seed(world, ALICE, _utc(10, 15), title=f"C{n}", state="due",
                  first_fired_at=_utc(10, 15), next_check_at=at + timedelta(seconds=n))
            for n in range(4)]
    [tick] = _ticks(world, at + timedelta(seconds=5))
    assert tick[org]["check_back"] == 4
    assert tick[org]["push_sent"] == 3 and tick[org]["push_capped"] == 1
    assert {p["tag"] for p in _payloads(world, ALICE)} == set(rids[:3])
    # The card of the fourth stays in My Day.
    monkeypatch.setattr(needs, "_now", lambda: at + timedelta(minutes=1))

    async def cards() -> needs.Found:
        return await needs._reminders(member_user(ALICE))

    found = _in_org(world, org, cards)
    kinds = {s.entity["id"]: s.kind for s in found.signals()}
    assert kinds[rids[3]] == "reminder_check"


def test_r8_the_cap_counts_the_local_date_of_the_member(world) -> None:
    """Repair round 1, finding 5. Priya is in Kolkata (UTC+5:30), and her day
    starts at 01:00 (``Day.feed_day``). Ten due pushes go on her 10 March.
    At 01:30 on her 11 March it is still 10 March in UTC, and her eleventh
    push of that UTC date sends, because the cap counts her own date.
    Notices have no check-back, so no other push goes. (A start hour of 0
    reads as 7 in ``tasks.settings._load``, so the test uses 1.)"""
    org = world.p.org_b
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE user_settings SET day_start_hour = 1 WHERE user_id = :u"),
                  {"u": PRIYA})
    for n in range(push_channel.DUE_CAP):
        _seed(world, PRIYA, _ist(10, 10) + timedelta(seconds=n), title=f"D{n:02d}",
              kind="notice")
    [first] = _ticks(world, _ist(10, 10, 1))
    assert first[org]["push_sent"] == push_channel.DUE_CAP
    next_day = _ist(11, 1, 30)
    assert next_day.astimezone(UTC).date() == _ist(10, 10).astimezone(UTC).date()
    late = _seed(world, PRIYA, next_day, title="Next day", kind="notice")
    [second] = _ticks(world, next_day)
    assert second[org]["fired"] == 1
    assert second[org]["push_sent"] == 1 and second[org]["push_capped"] == 0
    rows = _outbox(world, PRIYA)
    assert [str(r.local_date) for r in rows] == ["2099-03-10"] * 10 + ["2099-03-11"]
    assert rows[-1].dedupe_key.split(":")[1] == late and rows[-1].state == "sent"


class _ClaimRace:
    """Two send steps each count the cap, then wait here for the other. Each
    waits until the other arrives too, or until the other waits on the
    member lock. So without the lock both counts come before either commit."""

    def __init__(self, world: Any) -> None:
        self.world = world
        self.arrived = 0
        self.saw_the_lock_wait = False

    def _lock_waiters(self) -> int:
        with self.world.p.admin_engine.connect() as c:
            return int(c.execute(text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND NOT granted")).scalar_one())

    async def after_count(self) -> None:
        self.arrived += 1
        for _ in range(200):  # 10 s at most
            if self.arrived >= 2:
                return
            if self._lock_waiters() > 0:
                self.saw_the_lock_wait = True
                return
            await asyncio.sleep(0.05)
        raise AssertionError("the second send step never came")


def test_r8_the_member_lock_stops_two_claims_from_passing_the_cap(world, monkeypatch) -> None:
    """Repair round 1, finding 4 (ruling d). Alice has 9 due pushes sent
    today and two planned rows. Two send steps claim the two rows at once.
    Without the member lock, both counts read 9 and both send, so 11 go.
    With it, the second claim waits for the first commit, counts 10, and
    ends ``capped``."""
    org, at = world.p.org_b, _utc(10, 15)
    with world.p.admin_engine.begin() as c:
        for n in range(push_channel.DUE_CAP - 1):
            c.execute(text(
                "INSERT INTO reach_outbox (organization_id, member_email, channel, kind, "
                "dedupe_key, body, state, local_date, sent_at) VALUES (CAST(:o AS uuid), :m, "
                "'web_push', 'reminder_due', :k, 'reminder', 'sent', :d, now())"),
                {"o": org, "m": ALICE, "k": f"sent-{TAG}-{n}", "d": at.date()})
    x = _seed(world, ALICE, at, title="X")
    y = _seed(world, ALICE, at + timedelta(seconds=1), title="Y")
    assert _steps(world, org, [x, y], at + timedelta(seconds=1)) == ["fired", "fired"]
    ids = [r.id for r in _outbox(world, ALICE) if r.state == "planned"]
    assert len(ids) == 2
    race = _ClaimRace(world)
    real = push_channel.read_facts

    async def read_then_wait(db: Any, row: Any, now: datetime) -> Any:
        facts = await real(db, row, now)  # the cap count is in here
        await race.after_count()
        return facts

    monkeypatch.setattr(push_channel, "read_facts", read_then_wait)

    async def both() -> list[str]:
        return list(await asyncio.gather(*(push_channel._send_one(org, i, at) for i in ids)))

    got = _in_org(world, org, both)
    assert sorted(got) == ["capped", "sent"]
    assert len(world.service.posts) == 1
    # Non-vacuity: the second claim waited on the lock while the first held it.
    assert race.saw_the_lock_wait


def test_r8_a_push_row_never_counts_in_the_whatsapp_cap(world) -> None:
    org = world.p.org_b
    since = datetime.now(UTC) - timedelta(hours=1)
    row = ("INSERT INTO reach_outbox (organization_id, member_email, channel, kind, "
           "dedupe_key, body, state) VALUES (CAST(:o AS uuid), :m, :ch, 'interrupt', "
           ":k, 'b', 'sent')")
    with world.p.admin_engine.begin() as c:
        c.execute(text(row), {"o": org, "m": ALICE, "ch": "web_push", "k": f"urgent:p-{TAG}"})

    async def counts() -> tuple[int, int]:
        return (await outbox.interrupts_since(org, ALICE, since),
                await outbox.urgent_since(org, ALICE, since))

    assert _in_org(world, org, counts) == (0, 0)
    with world.p.admin_engine.begin() as c:
        c.execute(text(row), {"o": org, "m": ALICE, "ch": "whatsapp", "k": f"urgent:w-{TAG}"})
    assert _in_org(world, org, counts) == (1, 1)  # the control


# ── The bounds of a retry ──────────────────────────────────────────────────


def test_r8_with_push_off_no_row_is_planned_so_a_later_flip_sends_no_burst(
    world, monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "web_push_enabled", False)
    rid = _seed(world, ALICE, _utc(10, 15))
    [tick] = _ticks(world, _utc(10, 15))
    assert tick == {world.p.org_b: Counter(fired=1)}
    assert _outbox(world, ALICE) == []
    monkeypatch.setattr(get_settings(), "web_push_enabled", True)
    _ticks(world, _utc(10, 15, 1))
    assert world.service.posts == [] and _outbox(world, ALICE) == []
    assert [e.event for e in _ledger(world, rid)] == ["fired"]


def test_r8_a_planned_row_older_than_its_ttl_expires(world) -> None:
    org, at = world.p.org_b, _utc(10, 15)
    rid = _seed(world, ALICE, at)
    _steps(world, org, [rid], at)
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE reach_outbox SET created_at = now() - make_interval(secs => :s) "
            "WHERE member_email = :m"), {"s": push_message.ttl_of("reminder_due") + 5,
                                         "m": ALICE})
    assert _send(world, org, at) == Counter(push_expired=1)
    assert world.service.posts == []
    assert [(r.state, r.reason) for r in _outbox(world, ALICE)] == [("skipped", "expired")]


def test_r8_no_keys_keeps_the_row_and_a_later_tick_sends_it(world, monkeypatch) -> None:
    org, at = world.p.org_b, _utc(10, 15)
    rid = _seed(world, ALICE, at)
    _steps(world, org, [rid], at)
    key = get_settings().vapid_private_key
    monkeypatch.setattr(get_settings(), "vapid_private_key", "")
    assert _send(world, org, at) == Counter(push_no_keys=1)
    [row] = _outbox(world, ALICE)
    assert (row.state, row.reason, row.sent_at) == ("planned", "no_keys", None)
    assert world.service.posts == []
    monkeypatch.setattr(get_settings(), "vapid_private_key", key)
    assert _send(world, org, at) == Counter(push_sent=1)
    assert [r.state for r in _outbox(world, ALICE)] == ["sent"]


def test_r8_a_crashed_claim_is_sent_again_once_it_runs_out(world) -> None:
    org, at = world.p.org_b, _utc(10, 15)
    rid = _seed(world, ALICE, at)
    _steps(world, org, [rid], at)
    with world.p.admin_engine.begin() as c:
        c.execute(text("UPDATE reach_outbox SET reason = 'sending', sent_at = now() "
                       "WHERE member_email = :m"), {"m": ALICE})
    assert _send(world, org, at) == Counter()  # a live claim: nothing to do
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE reach_outbox SET sent_at = now() - make_interval(secs => :s) "
            "WHERE member_email = :m"), {"s": outbox.PUSH_LEASE_S + 1, "m": ALICE})
    assert _send(world, org, at) == Counter(push_sent=1)
    assert [e.event for e in _ledger(world, rid)] == ["fired", "delivered"]


def test_r8_no_device_skips_and_every_device_failing_fails(world) -> None:
    at = _utc(10, 15)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM push_subscriptions WHERE member_email = :m"), {"m": BOB})
    nobody = _seed(world, BOB, at)
    broken = _seed(world, ALICE, at)
    world.service.status = 500
    _ticks(world, at)
    [bobs] = _outbox(world, BOB)
    assert (bobs.state, bobs.reason) == ("skipped", "no_device")
    assert [e.event for e in _ledger(world, nobody)] == ["fired"]
    [alices] = _outbox(world, ALICE)
    assert (alices.state, alices.reason) == ("failed", "send_failed")
    assert [(e.event, e.channel) for e in _ledger(world, broken)] == [
        ("fired", None), ("failed", "push")]


# ── The send phase (ruling k) ──────────────────────────────────────────────


def test_r8_a_slow_push_service_delays_no_fire_and_the_phase_has_a_deadline(
    world, monkeypatch,
) -> None:
    """Repair round 1, finding 3. Each push of org A costs 12 s on the
    stopwatch of the send phase. Org B still fires before the first push of
    org A. No row starts after ``SEND_PHASE_S`` (30 s), so the fourth row of
    org A and the row of org B stay planned, and the next tick sends them."""
    a, b, at = world.p.org_a, world.p.org_b, _utc(10, 15)
    assert loop.SEND_PHASE_S == 30
    stopwatch = SimpleNamespace(t=0.0)
    monkeypatch.setattr(loop, "_monotonic", lambda: stopwatch.t)
    theirs = [_seed(world, CAROL, at, org=a, title=f"A{n}") for n in range(4)]
    ours = _seed(world, ALICE, at)
    seen_at_first_post: list[str] = []
    real = world.service.request_https

    async def slow(method, url, **kw):
        if url == _endpoint(CAROL):
            if not seen_at_first_post:
                seen_at_first_post.append(_state(world, ours))
            stopwatch.t += 12
        return await real(method, url, **kw)

    monkeypatch.setattr(outbound_guard, "request_https", slow)
    [first] = _ticks(world, at)
    assert first[a] == Counter(fired=4, push_sent=3)
    assert first[b] == Counter(fired=1)
    # Org B fired before org A sent anything.
    assert seen_at_first_post == ["due"]
    left = [r for r in _outbox(world, CAROL) if r.state == "planned"]
    assert len(left) == 1 and left[0].reason is None
    assert [(r.state, r.reason) for r in _outbox(world, ALICE)] == [("planned", None)]

    [second] = _ticks(world, at + timedelta(seconds=loop.TICK_SECONDS))
    assert second == {a: Counter(push_sent=1), b: Counter(push_sent=1)}
    tags = Counter(p["tag"] for m in (CAROL, ALICE) for p in _payloads(world, m))
    assert tags == Counter({rid: 1 for rid in (*theirs, ours)})


def test_r8_the_send_phase_reads_the_clock_of_each_row(world, monkeypatch) -> None:
    """Ruling k: the quiet hours of a row read the time of its send, not the
    start of the tick. The tick starts at 19:59:50 in Kolkata, and the
    stopwatch passes 20:00 before the check-back of Priya starts."""
    org = world.p.org_b
    stopwatch = SimpleNamespace(t=0.0)
    monkeypatch.setattr(loop, "_monotonic", lambda: stopwatch.t)
    at = _ist(10, 19, 59) + timedelta(seconds=50)
    rid = _seed(world, PRIYA, _ist(10, 18), state="due", first_fired_at=_ist(10, 18),
                next_check_at=at)
    real_send_planned = push_channel.send_planned

    async def later(org_id, clock, stopping=None):
        if org_id == org:
            stopwatch.t += 15  # the sends of org A ran past 20:00
            assert clock() == at + timedelta(seconds=15)
        return await real_send_planned(org_id, clock, stopping)

    monkeypatch.setattr(push_channel, "send_planned", later)
    [tick] = _ticks(world, at)
    assert tick[org] == Counter(check_back=1, push_quiet=1)
    assert world.service.posts == []
    [row] = _outbox(world, PRIYA)
    assert (row.kind, row.state, row.reason) == ("reminder_check", "skipped", "quiet")
    assert [e.event for e in _ledger(world, rid)] == ["check_back"]


def test_r8_a_send_that_raises_keeps_the_row_and_the_loop_goes_on(world, monkeypatch) -> None:
    """Repair round 1, finding 6. A send that raises an error that is not
    ``RuntimeError`` releases the row at once, reason ``error``. The next
    row and the next tick still go."""
    org, at = world.p.org_b, _utc(10, 15)
    first = _seed(world, ALICE, at)
    second = _seed(world, BOB, at)
    real = sender.send_to_member
    calls: list[str] = []

    async def flaky(org_id, member, message, **kw):
        calls.append(member)
        if member == ALICE and calls.count(ALICE) == 1:
            raise ValueError("a bad answer")
        return await real(org_id, member, message, **kw)

    monkeypatch.setattr(sender, "send_to_member", flaky)
    [tick] = _ticks(world, at)
    assert tick[org] == Counter(fired=2, push_error=1, push_sent=1)
    [row] = _outbox(world, ALICE)
    assert (row.state, row.reason, row.sent_at) == ("planned", "error", None)
    assert [p["tag"] for p in _payloads(world, BOB)] == [second]
    [again] = _ticks(world, at + timedelta(seconds=loop.TICK_SECONDS))
    assert again == {org: Counter(push_sent=1)}
    assert [p["tag"] for p in _payloads(world, ALICE)] == [first]


# ── Two orgs ───────────────────────────────────────────────────────────────


def test_r8_org_b_reads_nothing_of_org_a(world) -> None:
    a, b, at = world.p.org_a, world.p.org_b, _utc(10, 15)
    theirs = _seed(world, CAROL, at, org=a)
    _steps(world, a, [theirs], at)
    [row] = _outbox(world, CAROL)
    assert row.org == a and row.state == "planned"

    async def never(db: Any, got: Any) -> None:
        raise AssertionError("org B claimed a row of org A")

    async def as_org_b() -> tuple[list[str], Any, Counter, int]:
        listed = await outbox.planned_pushes(b, limit=100)
        claimed = await outbox.claim_push(b, row.id, never)
        sent = await push_channel.send_planned(b, lambda: at)
        async with tenant_session(b) as db:
            seen = (await db.execute(text(
                "SELECT count(*) FROM reach_outbox WHERE member_email = :m"),
                {"m": CAROL})).scalar_one()
        return listed, claimed, sent, int(seen)

    listed, claimed, sent, seen = _in_org(world, b, as_org_b)
    assert row.id not in listed and claimed is None and sent == Counter() and seen == 0
    assert world.service.posts == [] and _outbox(world, CAROL)[0].state == "planned"
    # Org A sends its own row, to its own member's device.
    assert _send(world, a, at) == Counter(push_sent=1)
    [payload] = _payloads(world, CAROL)
    assert payload["tag"] == theirs and payload["body"] == _org_name(world, a)
