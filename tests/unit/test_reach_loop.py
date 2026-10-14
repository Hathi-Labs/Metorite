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
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


class Sent:
    def __init__(self, state: str = "sent", reason: str | None = None) -> None:
        self.calls: list[dict] = []
        self.state, self.reason = state, reason

    async def __call__(self, org, member, *, kind, body, outbox_id, template=None):
        self.calls.append({"org": org, "member": member, "kind": kind, "body": body})
        return channel.Delivery(self.state, "free_text" if self.state == "sent" else None,
                                self.reason)


@pytest.fixture
def world(monkeypatch):
    fake, sent = FakeOutbox(), Sent()
    for name in ("has_log", "logged_pairs", "log", "plan", "finish", "interrupts_since",
                 "brief_planned"):
        monkeypatch.setattr(outbox, name, getattr(fake, name))
    monkeypatch.setattr(channel, "deliver", sent)
    s = get_settings()
    monkeypatch.setattr(s, "reach_enabled", True)
    monkeypatch.setattr(s, "reach_orgs", ORG)
    monkeypatch.setattr(s, "reach_members", ME)
    monkeypatch.setattr(s, "reach_daily_cap", 3)
    monkeypatch.setattr(s, "reach_brief_template", "")
    w = SimpleNamespace(outbox=fake, sent=sent, cards=[], now=NOW, working=True)

    async def resolve(org, member):
        return SimpleNamespace(email=member, organization_id=org)

    async def snapshot(org, user, now=None):
        return loop.Snapshot(list(w.cards), now or w.now, DAY, w.working)

    monkeypatch.setattr(loop, "resolve", resolve)
    monkeypatch.setattr(loop, "snapshot", snapshot)
    return w


class TestTheFirstRun:
    async def test_it_logs_a_baseline_and_sends_nothing(self, world):
        world.cards = [card("email:a:t1"), card("tasks:1", "overdue")]
        assert await loop.member_pass(ORG, ME) == "baseline"
        assert world.sent.calls == [] and world.outbox.rows == []
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
        assert world.outbox.log_rows[(ORG, ME, "email:a:t1", "fp000001")] == "sent"
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
        assert world.outbox.log_rows[(ORG, ME, "email:a:t1", "fp000001")] == "quiet"

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
        assert world.outbox.log_rows[(ORG, ME, "email:a:t2", "fp000001")] == "capped"

    async def test_a_skipped_send_is_logged_and_not_retried(self, world):
        world.sent.state, world.sent.reason = "skipped", "window_closed"
        await loop.member_pass(ORG, ME)
        world.cards = [card("email:a:t1")]
        assert await loop.member_pass(ORG, ME) == "interrupt:skipped:window_closed"
        assert world.outbox.log_rows[(ORG, ME, "email:a:t1", "fp000001")] == "skipped"
        assert await loop.member_pass(ORG, ME) == "nothing"


class TestTheBrief:
    async def test_once_a_day_in_the_morning_and_it_covers_the_interrupts(self, world):
        await loop.member_pass(ORG, ME)
        world.now = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)  # 09:30 in Kolkata
        world.cards = [card("email:a:t1", facts={"who": "P", "waiting_since":
                                                 (world.now - timedelta(minutes=5)).isoformat()}),
                       card("tasks:1", "overdue")]
        assert await loop.member_pass(ORG, ME) == "brief:sent"
        assert len(world.sent.calls) == 1
        assert world.sent.calls[0]["body"].startswith("*Your day, Thu 15 Oct*")
        assert world.outbox.log_rows[(ORG, ME, "email:a:t1", "fp000001")] == "deferred"
        assert await loop.member_pass(ORG, ME) == "nothing"
        assert len(world.sent.calls) == 1


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
        provider = SimpleNamespace(texts=[], templates=[])

        async def send_text(wa_id, body):
            provider.texts.append((wa_id, body))
            return "wamid.1"

        async def send_template(wa_id, name, lang):
            provider.templates.append((wa_id, name, lang))
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

    async def test_a_closed_window_skips_an_interrupt(self, wa, monkeypatch):
        self._phone(monkeypatch, "919800000001", False)
        got = await channel.deliver(ORG, ME, kind="interrupt", body="b", outbox_id="x",
                                    template="daily_brief")
        assert got.reason == "window_closed" and wa.texts == [] and wa.templates == []

    async def test_a_closed_window_sends_a_brief_as_the_template(self, wa, monkeypatch):
        self._phone(monkeypatch, "919800000001", False)
        got = await channel.deliver(ORG, ME, kind="brief", body="b", outbox_id="x",
                                    template="daily_brief")
        assert (got.state, got.regime) == ("sent", "template")
        assert wa.templates == [("919800000001", "daily_brief", "en")]

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
        world.cards = [card("email:a:t1")]
        got = self._client(ME, ORG).get("/reach/preview")
        assert got.status_code == 200
        body = got.json()
        assert body["interrupts"].startswith("*Needs you now*")
        assert body["linked"] is False and body["window_open"] is False
        assert world.sent.calls == [] and world.outbox.rows == []

    def test_the_brief_now_sends_through_the_channel(self, world):
        world.cards = [card("tasks:1", "overdue")]
        got = self._client(ME, ORG).post("/reach/brief")
        assert got.json() == {"result": "sent"}
        assert world.sent.calls[0]["kind"] == "brief"
        assert world.sent.calls[0]["member"] == ME


def test_the_settings_default_to_off():
    from acb_common.settings import Settings

    fields = Settings.model_fields
    assert fields["reach_enabled"].default is False
    assert fields["reach_orgs"].default == "" and fields["reach_members"].default == ""
    assert plan.MAX_CHARS <= 1000
