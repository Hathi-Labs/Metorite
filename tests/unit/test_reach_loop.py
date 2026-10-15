"""WS-59 RE-1a — the Reach loop, the channel and the routes, on fakes
(``proactive_reach.md`` §6, §7 and §8.1).

The outbox is an in-memory fake with the outbox module's own functions, so
the loop's decisions are what this file holds. ``test_reach_r8.py`` runs the
real SQL.

* **The first run** logs every card as ``baseline`` and sends nothing.
* **A new needs-reply** sends ONE batched message, and logs it ``sent``.
* **Quiet hours and the cap** log and do not send.
* **The brief** goes once a day, and the cards it named do not interrupt.
* **The channel** skips with no link, and with the window closed.
* **The allowlist** binds the tick and both routes. Another org's member is
  refused.
* **Hold and catch up** (RE-2a). With the window closed and no template, an
  interrupt and a brief are held, and the first pass with the window open
  sends them. A template goes out with its body parameters. After a template
  brief, the full brief follows once.

The fixture marks today's brief as sent at :data:`NOW`, so a test of the
interrupt sees no brief. A test of the brief clears it with ``no_brief_yet``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from acb_auth import UserContext, UserRole, get_current_user
from acb_auth.permissions import EffectiveAccess
from acb_common.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.reach import loop, outbox, plan
from gateway.reach import whatsapp as channel
from gateway.routes.shell import state

ORG = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
ME = "me@reach.test"
NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)  # 13:30 in Kolkata, after the brief window
DAY = state.Day(ZoneInfo("Asia/Kolkata"), 9, 18)


def card(sid: str, kind: str = "needs_reply", fp: str = "fp000001", **kw) -> dict:
    facts = kw.pop("facts", None) or (
        {"who": "Priya", "waiting_since": (NOW - timedelta(minutes=5)).isoformat()}
        if kind == "needs_reply" else {})
    return {"signal_id": sid, "app": sid.split(":")[0], "kind": kind,
            "group": kw.pop("group", "needs_you"), "score": 50, "title": "T", "facts": facts,
            "fingerprint": fp, "state": "new", "at": None, "why": "W", **kw}


class FakeOutbox:
    """The outbox module's functions, on lists."""

    def __init__(self) -> None:
        self.log_rows: dict[tuple[str, str, str, str], str] = {}
        self.rows: list[dict] = []

    async def has_log(self, org, member):
        return any(k[0] == org and k[1] == member for k in self.log_rows)

    async def logged_pairs(self, org, member):
        return {(k[2], k[3]) for k in self.log_rows if k[0] == org and k[1] == member}

    async def log(self, org, member, pairs, outcome, outbox_id=None):
        n = 0
        for s, f in pairs:
            self.log_rows.setdefault((org, member, s, f), outcome)
            n += 1
        return n

    async def plan(self, org, member, *, kind, signal_ids, key, body, local_date):
        if any(r["org"] == org and r["key"] == key for r in self.rows):
            return None
        row = {"id": f"ob-{len(self.rows)}", "org": org, "member": member, "kind": kind,
               "key": key, "body": body, "state": "planned", "local_date": local_date,
               "created_at": NOW}
        self.rows.append(row)
        return row["id"]

    async def finish(self, org, outbox_id, *, state, regime=None, reason=None,
                     provider_message_id=None):
        for r in self.rows:
            if r["id"] == outbox_id and r["state"] == "planned":
                r.update(state=state, regime=regime, reason=reason)
                return True
        return False

    async def interrupts_since(self, org, member, since):
        return sum(1 for r in self.rows if r["kind"] == "interrupt" and r["state"] == "sent"
                   and r["member"] == member and r["created_at"] >= since)

    async def brief_planned(self, org, member, local_date):
        return any(r["kind"] == "brief" and r["local_date"] == local_date
                   and r["member"] == member for r in self.rows)


    async def full_brief_due(self, org, member, local_date, key):
        sent_template = any(
            r["kind"] == "brief" and r["local_date"] == local_date and r["member"] == member
            and ((r["state"] == "sent" and r.get("regime") == "template")
                 or r["state"] == "failed") for r in self.rows)
        return sent_template and not any(r["key"] == key for r in self.rows)


TODAY = date(2026, 10, 15)


def seed_brief(fake: FakeOutbox, day: date = TODAY, regime: str = "free_text") -> None:
    fake.rows.append({"id": f"ob-seed-{day}", "org": ORG, "member": ME, "kind": "brief",
                      "key": plan.brief_key(ME, day), "body": "b", "state": "sent",
                      "regime": regime, "local_date": day, "created_at": NOW})


def no_brief_yet(world) -> None:
    world.outbox.rows[:] = [r for r in world.outbox.rows if r["kind"] != "brief"]


class Sent:
    def __init__(self, state: str = "sent", reason: str | None = None) -> None:
        self.calls: list[dict] = []
        self.state, self.reason = state, reason

    async def __call__(self, org, member, *, kind, body, outbox_id, template=None,
                       template_params=None, reach=None):
        self.calls.append({"org": org, "member": member, "kind": kind, "body": body,
                           "template": template, "params": template_params,
                           "reach": reach})
        regime = None
        if self.state == "sent":
            regime = "template" if template else "free_text"
        return channel.Delivery(self.state, regime, self.reason)


@pytest.fixture
def world(monkeypatch):
    fake, sent = FakeOutbox(), Sent()
    for name in ("has_log", "logged_pairs", "log", "plan", "finish", "interrupts_since",
                 "brief_planned", "full_brief_due"):
        monkeypatch.setattr(outbox, name, getattr(fake, name))
    monkeypatch.setattr(channel, "deliver", sent)
    seed_brief(fake)
    # A fresh stop event: a gateway lifespan in an earlier test stops the
    # loop, and run_tick returns at once while the event is set (CI order).
    monkeypatch.setattr(loop, "_stopping", asyncio.Event())
    s = get_settings()
    monkeypatch.setattr(s, "reach_enabled", True)
    monkeypatch.setattr(s, "reach_orgs", ORG)
    monkeypatch.setattr(s, "reach_members", ME)
    monkeypatch.setattr(s, "reach_daily_cap", 3)
    monkeypatch.setattr(s, "reach_brief_template", "")
    monkeypatch.setattr(s, "reach_needs_you_template", "")
    monkeypatch.setattr(s, "reach_template_language", "en")
    w = SimpleNamespace(outbox=fake, sent=sent, cards=[], now=NOW, working=True,
                        complete=True, wa_id="919800000001", open=True)

    async def reach_state(org, member):
        return w.wa_id, (w.open if w.wa_id else False)

    async def display_name(org, member):
        return "Priya Shah"

    monkeypatch.setattr(channel, "reach_state", reach_state)
    monkeypatch.setattr(channel, "display_name", display_name)

    async def resolve(org, member):
        return SimpleNamespace(email=member, organization_id=org)

    async def snapshot(org, user, now=None):
        return loop.Snapshot(list(w.cards), now or w.now, DAY, w.working, w.complete)

    monkeypatch.setattr(loop, "resolve", resolve)
    monkeypatch.setattr(loop, "snapshot", snapshot)
    return w


class TestTheFirstRun:
    async def test_it_logs_a_baseline_and_sends_nothing(self, world):
        world.cards = [card("email:a:t1"), card("tasks:1", "overdue")]
        assert await loop.member_pass(ORG, ME) == "baseline"
        assert world.sent.calls == [] and len(world.outbox.rows) == 1  # the seeded brief
        assert set(world.outbox.log_rows.values()) == {"baseline"}
        # The second run sees nothing new, so it sends nothing either.
        assert await loop.member_pass(ORG, ME) == "nothing"
        assert world.sent.calls == []

    async def test_a_first_run_with_no_card_still_ends_the_first_run(self, world):
        assert await loop.member_pass(ORG, ME) == "baseline"
        world.cards = [card("email:a:t1")]
        assert await loop.member_pass(ORG, ME) == "interrupt:sent"


class TestTheInterrupt:
    async def test_new_cards_send_one_batched_message(self, world):
        await loop.member_pass(ORG, ME)
        world.cards = [card("email:a:t1"),
                       card("approvals:9", "approval", facts={}, title="Refund")]
        assert await loop.member_pass(ORG, ME) == "interrupt:sent"
        assert len(world.sent.calls) == 1
        body = world.sent.calls[0]["body"]
        assert body.startswith("*Needs you now*") and "Approve: Refund" in body
        assert world.outbox.log_rows[(ORG, ME, *plan.pair(card("email:a:t1")))] == "sent"
        # The same cards never interrupt twice.
        assert await loop.member_pass(ORG, ME) == "nothing"
        assert len(world.sent.calls) == 1

    async def test_a_brief_class_card_does_not_interrupt(self, world):
        await loop.member_pass(ORG, ME)
        world.cards = [card("tasks:1", "overdue")]
        assert await loop.member_pass(ORG, ME) == "nothing"
        assert world.sent.calls == []

    async def test_quiet_hours_log_and_do_not_send(self, world):
        await loop.member_pass(ORG, ME)
        world.now = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)  # 20:30 in Kolkata
        world.cards = [card("email:a:t1", facts={"who": "P", "waiting_since":
                                                 (world.now - timedelta(minutes=5)).isoformat()})]
        assert await loop.member_pass(ORG, ME) == "quiet"
        assert world.sent.calls == []
        assert world.outbox.log_rows[(ORG, ME, *plan.pair(card("email:a:t1")))] == "quiet"

    async def test_a_day_off_is_quiet(self, world):
        await loop.member_pass(ORG, ME)
        world.working = False
        world.cards = [card("email:a:t1")]
        assert await loop.member_pass(ORG, ME) == "quiet"
        assert world.sent.calls == []

    async def test_over_the_cap_logs_capped(self, world, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_daily_cap", 1)
        await loop.member_pass(ORG, ME)
        world.cards = [card("email:a:t1")]
        assert await loop.member_pass(ORG, ME) == "interrupt:sent"
        world.cards = [card("email:a:t1"), card("email:a:t2")]
        assert await loop.member_pass(ORG, ME) == "capped"
        assert len(world.sent.calls) == 1
        assert world.outbox.log_rows[(ORG, ME, *plan.pair(card("email:a:t2")))] == "capped"

    async def test_a_skipped_send_is_logged_and_not_retried(self, world):
        # RE-2a: a closed window no longer reaches the channel (the loop holds
        # it, TestHoldAndCatchUp). A skip of the channel itself still logs.
        world.sent.state, world.sent.reason = "skipped", "channel_off"
        await loop.member_pass(ORG, ME)
        world.cards = [card("email:a:t1")]
        assert await loop.member_pass(ORG, ME) == "interrupt:skipped:channel_off"
        assert world.outbox.log_rows[(ORG, ME, *plan.pair(card("email:a:t1")))] == "skipped"
        assert await loop.member_pass(ORG, ME) == "nothing"


class TestTheBrief:
    async def test_once_a_day_in_the_morning_and_it_covers_the_interrupts(self, world):
        no_brief_yet(world)
        await loop.member_pass(ORG, ME)
        world.now = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)  # 09:30 in Kolkata
        world.cards = [card("email:a:t1", facts={"who": "P", "waiting_since":
                                                 (world.now - timedelta(minutes=5)).isoformat()}),
                       card("tasks:1", "overdue")]
        assert await loop.member_pass(ORG, ME) == "brief:sent"
        assert len(world.sent.calls) == 1
        assert world.sent.calls[0]["body"].startswith("*Your day, Thu 15 Oct*")
        assert world.outbox.log_rows[(ORG, ME, *plan.pair(card("email:a:t1")))] == "deferred"
        assert await loop.member_pass(ORG, ME) == "nothing"
        assert len(world.sent.calls) == 1


def kolkata(hour: int, minute: int = 0) -> datetime:
    """An instant of 15 Oct 2026 at *hour* in Kolkata."""
    return datetime(2026, 10, 15, hour, minute, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(UTC)


def approval(sid: str, title: str) -> dict:
    return card(sid, "approval", facts={}, title=title)


class TestHoldAndCatchUp:
    """RE-2a. Measured on the box on 2026-10-15: a brief and an interrupt were
    dropped with ``window_closed``. Now they wait for the window."""

    async def test_a_held_interrupt_goes_as_free_text_when_the_window_opens(self, world):
        await loop.member_pass(ORG, ME)
        world.open = False
        world.cards = [approval("approvals:1", "Refund"), approval("approvals:2", "Leave")]
        assert await loop.member_pass(ORG, ME) == "held"
        assert await loop.member_pass(ORG, ME) == "held"
        assert world.sent.calls == []
        # Not logged and not planned: no row may stop the catch-up.
        assert set(world.outbox.log_rows.values()) == {"baseline"}
        assert [r["kind"] for r in world.outbox.rows] == ["brief"]
        world.open = True
        assert await loop.member_pass(ORG, ME) == "interrupt:sent"
        assert len(world.sent.calls) == 1
        call = world.sent.calls[0]
        assert call["template"] is None and call["reach"] == ("919800000001", True)
        assert "Approve: Refund" in call["body"] and "Approve: Leave" in call["body"]
        assert world.outbox.log_rows[(ORG, ME, *plan.pair(world.cards[0]))] == "sent"
        assert await loop.member_pass(ORG, ME) == "nothing"

    async def test_a_needs_you_template_sends_now_with_its_params(self, world, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_needs_you_template", "metorite_needs_you")
        await loop.member_pass(ORG, ME)
        world.open = False
        world.cards = [approval("approvals:1", "the refund for Acme"),
                       approval("approvals:2", "Leave")]
        assert await loop.member_pass(ORG, ME) == "interrupt:sent"
        call = world.sent.calls[0]
        assert call["template"] == "metorite_needs_you"
        assert call["params"] == ["Priya", "Approve: the refund for Acme (and 1 more)"]
        assert call["reach"] == ("919800000001", False)
        assert {world.outbox.log_rows[(ORG, ME, *plan.pair(c))] for c in world.cards} == {"sent"}
        assert world.outbox.rows[-1]["regime"] == "template"

    async def test_a_refused_needs_you_template_holds_then_sends_free_text(
            self, world, monkeypatch):
        # Review of RE-2a, P2: a refused template logs nothing, a closed
        # window does not retry it, and the open window sends free text.
        monkeypatch.setattr(get_settings(), "reach_needs_you_template", "not_approved")
        await loop.member_pass(ORG, ME)
        world.open = False
        world.cards = [approval("approvals:1", "Refund")]
        world.sent.state, world.sent.reason = "failed", "send_failed"
        assert await loop.member_pass(ORG, ME) == "interrupt:failed:send_failed"
        assert (ORG, ME, *plan.pair(world.cards[0])) not in world.outbox.log_rows
        assert await loop.member_pass(ORG, ME) == "interrupt:duplicate"
        world.sent.state, world.sent.reason = "sent", None
        world.open = True
        assert await loop.member_pass(ORG, ME) == "interrupt:sent"
        assert world.sent.calls[-1]["template"] is None
        assert len(world.sent.calls) == 2

    async def test_with_an_open_window_the_needs_you_template_is_not_used(self, world,
                                                                          monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_needs_you_template", "metorite_needs_you")
        await loop.member_pass(ORG, ME)
        world.cards = [approval("approvals:1", "Refund")]
        assert await loop.member_pass(ORG, ME) == "interrupt:sent"
        assert world.sent.calls[0]["template"] is None

    async def test_a_member_with_no_link_is_not_held(self, world):
        world.sent.state, world.sent.reason = "skipped", "no_link"
        world.wa_id = None
        await loop.member_pass(ORG, ME)
        world.cards = [approval("approvals:1", "Refund")]
        assert await loop.member_pass(ORG, ME) == "interrupt:skipped:no_link"
        assert world.outbox.log_rows[(ORG, ME, *plan.pair(world.cards[0]))] == "skipped"
        assert await loop.member_pass(ORG, ME) == "nothing"

    async def test_a_catch_up_in_quiet_hours_is_logged_quiet(self, world):
        await loop.member_pass(ORG, ME)
        world.open, world.now = False, kolkata(17)
        world.cards = [approval("approvals:1", "Refund")]
        assert await loop.member_pass(ORG, ME) == "held"
        world.open, world.now = True, kolkata(19)
        assert await loop.member_pass(ORG, ME) == "quiet"
        assert world.sent.calls == []

    async def test_a_catch_up_over_the_cap_is_logged_capped(self, world, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_daily_cap", 1)
        await loop.member_pass(ORG, ME)
        world.cards = [approval("approvals:1", "Refund")]
        assert await loop.member_pass(ORG, ME) == "interrupt:sent"
        world.open = False
        world.cards = [approval("approvals:1", "Refund"), approval("approvals:2", "Leave")]
        assert await loop.member_pass(ORG, ME) == "held"
        world.open = True
        assert await loop.member_pass(ORG, ME) == "capped"
        assert len(world.sent.calls) == 1

    async def test_a_held_brief_goes_when_the_window_opens_later_that_day(self, world):
        no_brief_yet(world)
        await loop.member_pass(ORG, ME)
        world.open, world.now = False, kolkata(9, 30)
        world.cards = [card("tasks:1", "overdue")]
        assert await loop.member_pass(ORG, ME) == "brief:held"
        assert world.outbox.rows == []
        world.open, world.now = True, kolkata(14)
        assert await loop.member_pass(ORG, ME) == "brief:sent"
        assert world.sent.calls[0]["body"].startswith("*Your day, Thu 15 Oct*")
        assert world.sent.calls[0]["template"] is None
        assert await loop.member_pass(ORG, ME) == "nothing"

    async def test_an_empty_morning_then_an_afternoon_card_is_an_interrupt(self, world):
        # Review of RE-2a, P1, with no seeded brief: the empty morning keeps a
        # row, so the 15:00 approval is a capped interrupt, not a brief.
        no_brief_yet(world)
        await loop.member_pass(ORG, ME)
        world.now = kolkata(9, 30)
        assert await loop.member_pass(ORG, ME) == "brief:empty"
        assert world.outbox.rows[-1]["state"] == "skipped"
        world.now = kolkata(15)
        world.cards = [approval("approvals:1", "Refund")]
        assert await loop.member_pass(ORG, ME) == "interrupt:sent"
        assert world.sent.calls[0]["kind"] == "interrupt"

    async def test_a_refused_template_brief_gets_the_full_brief_on_open(
            self, world, monkeypatch):
        # Review of RE-2a, P2: Meta refuses the template, and the day's brief
        # still goes once the member writes.
        monkeypatch.setattr(get_settings(), "reach_brief_template", "not_approved")
        no_brief_yet(world)
        await loop.member_pass(ORG, ME)
        world.open, world.now = False, kolkata(9, 30)
        world.cards = [card("tasks:1", "overdue")]
        world.sent.state, world.sent.reason = "failed", "send_failed"
        assert await loop.member_pass(ORG, ME) == "brief:failed:send_failed"
        world.sent.state, world.sent.reason = "sent", None
        world.open, world.now = True, kolkata(10)
        assert await loop.member_pass(ORG, ME) == "brief_full:sent"
        assert await loop.member_pass(ORG, ME) == "nothing"

    async def test_one_pass_sends_one_template_not_two(self, world, monkeypatch):
        # Review of RE-2a, P2: with both templates set, the brief template
        # goes and the approval waits for the full brief.
        monkeypatch.setattr(get_settings(), "reach_brief_template", "daily_brief")
        monkeypatch.setattr(get_settings(), "reach_needs_you_template", "needs_you")
        no_brief_yet(world)
        await loop.member_pass(ORG, ME)
        world.open, world.now = False, kolkata(9, 30)
        world.cards = [approval("approvals:1", "Refund")]
        assert await loop.member_pass(ORG, ME) == "brief:sent,held"
        assert [c["template"] for c in world.sent.calls] == ["daily_brief"]

    async def test_no_brief_after_the_day_end(self, world):
        no_brief_yet(world)
        await loop.member_pass(ORG, ME)
        world.now = kolkata(18, 30)
        world.cards = [card("tasks:1", "overdue")]
        assert await loop.member_pass(ORG, ME) == "nothing"
        assert world.sent.calls == [] and world.outbox.rows == []

    async def test_after_the_morning_a_closed_window_holds_even_with_a_template(
            self, world, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_brief_template", "daily_brief")
        no_brief_yet(world)
        await loop.member_pass(ORG, ME)
        world.open, world.now = False, kolkata(12, 30)
        world.cards = [card("tasks:1", "overdue")]
        assert await loop.member_pass(ORG, ME) == "brief:held"
        assert world.sent.calls == []

    async def test_a_template_brief_then_one_full_brief_when_the_window_opens(
            self, world, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_brief_template", "daily_brief")
        no_brief_yet(world)
        await loop.member_pass(ORG, ME)
        world.open, world.now = False, kolkata(9, 30)
        world.cards = [card("tasks:1", "overdue", title="Ship it", facts={"days_late": 1}),
                       approval("approvals:1", "Refund")]
        assert await loop.member_pass(ORG, ME) == "brief:sent,held"
        first = world.sent.calls[0]
        assert first["template"] == "daily_brief"
        assert first["params"][:3] == ["Priya", "Thu 15 Oct", "2"]
        # A template named one item, so the approval is NOT deferred.
        assert "deferred" not in world.outbox.log_rows.values()
        world.open, world.now = True, kolkata(10)
        assert await loop.member_pass(ORG, ME) == "brief_full:sent"
        full = world.sent.calls[1]
        assert full["template"] is None and full["body"].startswith("*Your day, Thu 15 Oct*")
        assert world.outbox.rows[-1]["key"] == plan.full_brief_key(ME, TODAY)
        # The full brief named the approval, so it does not interrupt too.
        assert world.outbox.log_rows[(ORG, ME, *plan.pair(world.cards[1]))] == "deferred"
        assert await loop.member_pass(ORG, ME) == "nothing"
        assert len(world.sent.calls) == 2

    async def test_a_free_text_brief_has_no_full_follow_up(self, world):
        await loop.member_pass(ORG, ME)
        world.cards = [card("tasks:1", "overdue")]
        assert await loop.member_pass(ORG, ME) == "nothing"
        assert world.sent.calls == []

    async def test_a_template_brief_with_nothing_to_say_sends_no_full_brief(
            self, world):
        no_brief_yet(world)
        seed_brief(world.outbox, regime="template")
        await loop.member_pass(ORG, ME)
        world.cards = [card("tasks:1", "overdue", group="fyi")]
        assert await loop.member_pass(ORG, ME) == "nothing"
        assert world.sent.calls == []


class TestTheTick:
    async def test_it_runs_only_the_allowlisted_member_of_an_allowed_org(self, world, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_orgs", f"{ORG},*")
        monkeypatch.setattr(get_settings(), "reach_members", f"{ME},*")
        done = await loop.run_tick()
        assert list(done) == [(ORG, ME)]

    async def test_it_does_nothing_when_off(self, world, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_enabled", False)
        assert await loop.run_tick() == {}

    async def test_empty_lists_reach_nobody(self, world, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_members", "")
        assert await loop.run_tick() == {}


class TestWhoIsReached:
    async def test_another_orgs_member_is_refused(self, monkeypatch):
        import acb_auth.deps as deps
        import gateway.my_day_loop as mdl

        async def departed(org, member):
            return None

        async def member_context(member):
            return SimpleNamespace(email=member, organization_id=OTHER)

        monkeypatch.setattr(mdl, "departed", departed)
        monkeypatch.setattr(deps, "member_context", member_context)
        assert await loop.resolve(ORG, ME) == "other_org"

    async def test_a_departed_member_is_skipped(self, monkeypatch):
        import gateway.my_day_loop as mdl

        async def departed(org, member):
            return "status"

        monkeypatch.setattr(mdl, "departed", departed)
        assert await loop.resolve(ORG, ME) == "departed"


class TestTheChannel:
    @pytest.fixture
    def wa(self, monkeypatch):
        from gateway.routes.whatsapp_channel import flags as wa_flags

        monkeypatch.setattr(wa_flags, "org_allowed", lambda org: org == ORG)
        monkeypatch.setattr(wa_flags, "bot_credentials",
                            lambda: {"access_token": "t", "phone_number_id": "1"})
        provider = SimpleNamespace(texts=[], templates=[], components=[])

        async def send_text(wa_id, body):
            provider.texts.append((wa_id, body))
            return "wamid.1"

        async def send_template(wa_id, name, lang, *, components=None):
            provider.templates.append((wa_id, name, lang))
            provider.components.append(components)
            return "wamid.2"

        provider.send_text, provider.send_template = send_text, send_template
        import whatsapp_ingestion.providers.factory as factory

        monkeypatch.setattr(factory, "build_provider", lambda kind, creds: provider)
        recorded: list = []

        async def record(*a):
            recorded.append(a)

        monkeypatch.setattr(channel, "_record", record)
        provider.recorded = recorded
        return provider

    def _phone(self, monkeypatch, wa_id, is_open):
        async def linked(org, member):
            return wa_id

        async def window(org, w):
            return is_open

        monkeypatch.setattr(channel, "linked_phone", linked)
        monkeypatch.setattr(channel, "window_open", window)

    async def test_no_link_skips(self, wa, monkeypatch):
        self._phone(monkeypatch, None, True)
        got = await channel.deliver(ORG, ME, kind="interrupt", body="b", outbox_id="x")
        assert got.state == "skipped" and got.reason == "no_link"
        assert wa.texts == []

    async def test_an_open_window_sends_free_text_and_records(self, wa, monkeypatch):
        self._phone(monkeypatch, "919800000001", True)
        got = await channel.deliver(ORG, ME, kind="interrupt", body="b", outbox_id="x")
        assert (got.state, got.regime) == ("sent", "free_text")
        assert wa.texts == [("919800000001", "b")] and len(wa.recorded) == 1

    async def test_a_closed_window_skips_an_interrupt_with_no_template(self, wa, monkeypatch):
        self._phone(monkeypatch, "919800000001", False)
        got = await channel.deliver(ORG, ME, kind="interrupt", body="b", outbox_id="x")
        assert got.reason == "window_closed" and wa.texts == [] and wa.templates == []

    async def test_a_closed_window_sends_the_needs_you_template_with_its_params(
            self, wa, monkeypatch):
        # RE-2a changed this on purpose: RE-1a refused any template for an
        # interrupt. The loop now names the needs-you template.
        self._phone(monkeypatch, "919800000001", False)
        got = await channel.deliver(ORG, ME, kind="interrupt", body="b", outbox_id="x",
                                    template="metorite_needs_you",
                                    template_params=["Priya", "Approve: the refund for Acme"])
        assert (got.state, got.regime) == ("sent", "template")
        assert wa.templates == [("919800000001", "metorite_needs_you", "en")]
        assert wa.components == [[{"type": "body", "parameters": [
            {"type": "text", "text": "Priya"},
            {"type": "text", "text": "Approve: the refund for Acme"}]}]]
        assert wa.recorded[0][4] == ("(Sent the WhatsApp template metorite_needs_you: "
                                     "Approve: the refund for Acme.)")

    async def test_the_language_comes_from_the_setting(self, wa, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_template_language", "en_GB")
        self._phone(monkeypatch, "919800000001", False)
        await channel.deliver(ORG, ME, kind="brief", body="b", outbox_id="x",
                              template="daily_brief")
        assert wa.templates == [("919800000001", "daily_brief", "en_GB")]

    async def test_a_given_reach_state_is_used_and_not_read_again(self, wa, monkeypatch):
        async def boom(*a):
            raise AssertionError("read again")

        monkeypatch.setattr(channel, "linked_phone", boom)
        got = await channel.deliver(ORG, ME, kind="interrupt", body="b", outbox_id="x",
                                    reach=("919800000001", True))
        assert got.state == "sent" and wa.texts == [("919800000001", "b")]

    async def test_a_closed_window_sends_a_brief_as_the_template(self, wa, monkeypatch):
        self._phone(monkeypatch, "919800000001", False)
        got = await channel.deliver(ORG, ME, kind="brief", body="b", outbox_id="x",
                                    template="daily_brief")
        assert (got.state, got.regime) == ("sent", "template")
        assert wa.templates == [("919800000001", "daily_brief", "en")]
        assert wa.components == [None]
        assert wa.recorded[0][4] == "(Sent the WhatsApp template daily_brief.)"

    async def test_a_closed_window_with_no_template_skips_a_brief(self, wa, monkeypatch):
        self._phone(monkeypatch, "919800000001", False)
        got = await channel.deliver(ORG, ME, kind="brief", body="b", outbox_id="x")
        assert got.reason == "window_closed"

    async def test_an_org_off_the_channel_skips(self, wa, monkeypatch):
        got = await channel.deliver(OTHER, ME, kind="brief", body="b", outbox_id="x")
        assert got.reason == "channel_off"


class TestTheRoutes:
    def _client(self, email: str, org: str) -> TestClient:
        from gateway.reach.routes import router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: UserContext(
            email=email, role=UserRole.EMPLOYEE, organization_id=org,
            access=EffectiveAccess(role_granted=frozenset({"feature:chat"})))
        return TestClient(app)

    def test_off_the_allowlist_is_404(self, world):
        client = self._client("other@reach.test", ORG)
        assert client.get("/reach/preview").status_code == 404
        assert client.post("/reach/brief").status_code == 404

    def test_another_org_is_404(self, world):
        assert self._client(ME, OTHER).get("/reach/preview").status_code == 404

    def test_reach_off_is_404(self, world, monkeypatch):
        monkeypatch.setattr(get_settings(), "reach_enabled", False)
        assert self._client(ME, ORG).get("/reach/preview").status_code == 404

    def test_the_preview_sends_nothing(self, world, monkeypatch):
        async def linked(org, member):
            return None

        monkeypatch.setattr(channel, "linked_phone", linked)
        world.wa_id = None
        world.cards = [card("email:a:t1")]
        got = self._client(ME, ORG).get("/reach/preview")
        assert got.status_code == 200
        body = got.json()
        assert body["interrupts"].startswith("*Needs you now*")
        assert body["linked"] is False and body["window_open"] is False
        assert body["held"] is False
        assert world.sent.calls == [] and [r["kind"] for r in world.outbox.rows] == ["brief"]

    def test_the_preview_says_held_when_the_window_is_closed(self, world, monkeypatch):
        world.open = False
        world.cards = [card("email:a:t1")]
        body = self._client(ME, ORG).get("/reach/preview").json()
        assert (body["linked"], body["window_open"], body["held"]) == (True, False, True)
        # With a needs-you template the interrupt goes now, so it is not held.
        monkeypatch.setattr(get_settings(), "reach_needs_you_template", "metorite_needs_you")
        assert self._client(ME, ORG).get("/reach/preview").json()["held"] is False
        # No candidate: nothing is held.
        world.cards = []
        monkeypatch.setattr(get_settings(), "reach_needs_you_template", "")
        assert self._client(ME, ORG).get("/reach/preview").json()["held"] is False

    def test_the_brief_now_sends_through_the_channel(self, world):
        world.cards = [card("tasks:1", "overdue")]
        got = self._client(ME, ORG).post("/reach/brief")
        assert got.json() == {"state": "sent", "reason": None}
        assert world.sent.calls[0]["kind"] == "brief"
        assert world.sent.calls[0]["member"] == ME


class TestAnIncompleteRead:
    """Review of RE-1a, P2: a read with a failed provider or a failed state
    join neither plans nor logs, and the first run writes no baseline."""

    async def test_no_baseline_from_an_incomplete_read(self, world):
        world.cards = [card("email:a:t1")]
        world.complete = False
        assert await loop.member_pass(ORG, ME) == "incomplete"
        assert world.outbox.log_rows == {} and world.sent.calls == []
        # The next complete read takes the baseline, so nothing old goes out.
        world.complete = True
        assert await loop.member_pass(ORG, ME) == "baseline"
        assert world.sent.calls == []

    def test_the_brief_now_refuses_an_incomplete_read(self, world):
        world.complete = False
        got = TestTheRoutes._client(None, ME, ORG).post("/reach/brief")
        assert got.json() == {"state": "skipped", "reason": "read_incomplete"}
        assert world.sent.calls == []


@pytest.mark.parametrize(("outcome", "body"), [
    ("sent", {"state": "sent", "reason": None}),
    ("empty", {"state": "skipped", "reason": "nothing_to_say"}),
    ("duplicate", {"state": "skipped", "reason": "already_sent"}),
    ("skipped:window_closed", {"state": "skipped", "reason": "window_closed"}),
    ("failed:send_failed", {"state": "failed", "reason": "send_failed"}),
])
def test_the_brief_answer_is_state_and_reason(outcome, body):
    # The client reads exactly these keys (people/lib/reach.ts, sendResult).
    from gateway.reach.routes import result_body
    assert result_body(outcome) == body


async def test_the_loop_does_not_start_while_reach_is_off(monkeypatch):
    # Verifier, M14: with REACH_ENABLED off no task starts.
    monkeypatch.setattr(get_settings(), "reach_enabled", False)
    assert await loop.start_reach_loop() is False
    assert loop._task is None


def test_the_settings_default_to_off():
    from acb_common.settings import Settings

    fields = Settings.model_fields
    assert fields["reach_enabled"].default is False
    assert fields["reach_orgs"].default == "" and fields["reach_members"].default == ""
    assert plan.MAX_CHARS <= 1000
