"""Send later (H-375, ``email_app_master_plan.md`` §19).

A member schedules a draft, and the job sends it at the time, through the one
draft-send path, with the recipients that the member's card showed.

Each rule names the mutation that turns it red (R7):

1. **Dark by default.** With ``email_send_later`` off, each route is a 404
   and the job sends nothing. Mutation: drop ``_off()`` ->
   ``test_the_routes_are_off_by_default``.
2. **A time needs an offset, and must be in the future.**
3. **The send carries ``expect``**, so a draft whose recipients changed is
   ``failed``, never sent. Mutation: send with no ``expect`` ->
   ``test_changed_recipients_fail_and_send_nothing``.
4. **No double send.** A due row is claimed into ``sending`` once, a stuck
   ``sending`` row is ``failed`` and never sent again, and a draft has one
   open schedule. Mutation: drop ``SKIP LOCKED`` or the status flip ->
   ``test_a_row_is_claimed_once``.
5. **Tenancy (R5, R8).** A member reads and cancels only their own rows, and
   the table is FORCE RLS. Mutation: drop ``owner_email`` from the cancel ->
   ``test_another_member_cannot_cancel``.
6. **The chat tool** shows a card with the time, and posts what it showed.
"""
from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.email.automation import send_later as sl
from sqlalchemy import text

from tests.unit.test_email_keep_separate import (
    _account,
    _as_member,
    _assert_non_priv,
    _purge,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_MIGRATION = next(
    p for p in (Path(__file__).resolve().parents[2] / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS email_scheduled_sends" in p.read_text(encoding="utf-8"))

_AGENT = (Path(__file__).resolve().parents[2]
          / "apps" / "agents" / "agent-email-assistant" / "agents.py")


def _load_agents():
    spec = importlib.util.spec_from_file_location("ea_send_later", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_agents()
BOX = "a0000000-0000-4000-8000-000000000001"


# ── 1 and 2. Off by default, and the time ────────────────────────────────────


async def test_the_routes_are_off_by_default(monkeypatch) -> None:
    monkeypatch.setattr(sl, "get_settings", lambda: SimpleNamespace())
    user = UserContext(email="dana@fracktal.test", role=UserRole.EMPLOYEE)
    for call in (sl.list_scheduled(user),
                 sl.cancel_scheduled("s1", user),
                 sl.schedule_draft(sl.ScheduleRequest(
                     account_id=BOX, draft_id="d1", send_at="2099-01-01T09:00:00+05:30",
                     expect={"to": ["x@y.test"]}), user)):
        with pytest.raises(HTTPException) as err:
            await call
        assert err.value.status_code == 404
    assert await sl.run_tick() == {}
    assert await sl.start_send_later_loop() is False


@pytest.mark.parametrize(("value", "why"), [
    ("2099-01-05T09:00:00", "offset"),
    ("2001-01-05T09:00:00+05:30", "passed"),
    ("next friday", "ISO 8601"),
])
def test_a_time_needs_an_offset_and_the_future(value: str, why: str) -> None:
    with pytest.raises(HTTPException) as err:
        sl._when(value)
    assert err.value.status_code == 422 and why in err.value.detail


def test_the_migration_is_tenant_scoped_and_keeps_history() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "FORCE  ROW LEVEL SECURITY" in sql and "WITH CHECK" in sql
    # SET NULL: a Gmail web edit deletes the old draft row, and the schedule
    # must fail with a reason, never vanish (review of #1209, P1).
    assert "draft_id         UUID NULL REFERENCES email_messages (id) ON DELETE SET NULL" in sql
    assert "WHERE status IN ('queued', 'sending')" in sql


# ── 6. The chat tool ─────────────────────────────────────────────────────────


@pytest.fixture()
def gw(monkeypatch) -> SimpleNamespace:
    g = SimpleNamespace(posts=[], cards=[], answer=True)

    async def get(path, params=None):
        if path == "/email/accounts":
            return [{"id": BOX, "email_address": "dana@fracktal.test",
                     "display_label": "Fracktal"}]
        if path == "/email/messages/d1":
            return {"id": "d1", "account_id": BOX, "folder": "drafts", "subject": "Quote",
                    "to_addresses": [{"email": "ravi@contoso.test"}],
                    "cc_addresses": [], "bcc_addresses": [],
                    "body_text": "Hi Ravi,\n\nThe quote."}
        return {}

    async def post(path, body):
        g.posts.append((path, body))
        return {"id": "s1"}

    async def card(**kw):
        g.cards.append(kw)
        return g.answer

    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    return g


async def test_the_tool_shows_the_time_and_posts_what_it_showed(gw) -> None:
    out = await agents.schedule_send(BOX, "d1", "2099-01-05T09:00:00+05:30")
    [card] = gw.cards
    assert "Mon 05 Jan 2099, 09:00 (+0530)" in card["title"]
    assert "- To: ravi@contoso.test" in card["context"] and "The quote." in card["context"]
    assert card["close_on_steer"] is True
    [(path, body)] = gw.posts
    assert path == "/email/drafts/schedule"
    assert body["expect"] == {"to": ["ravi@contoso.test"], "cc": [], "bcc": []}
    assert body["send_at"] == "2099-01-05T09:00:00+05:30"
    assert "schedule_id s1" in out


async def test_the_tool_refuses_a_time_with_no_offset(gw) -> None:
    out = await agents.schedule_send(BOX, "d1", "2099-01-05T09:00:00")
    assert out.startswith("Not scheduled.") and gw.cards == [] and gw.posts == []


# ── 3 to 5 on a real database (R8) ───────────────────────────────────────────


def _draft(admin, *, org: str, account_id: str, to: str) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, folder, "
            "from_address, to_addresses, subject, body_text, received_at, organization_id) "
            "VALUES (CAST(:a AS uuid), :p, 'drafts', CAST(:frm AS jsonb), "
            "CAST(:to AS jsonb), 'Quote', 'Hi', now(), CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "p": f"pm-{uuid.uuid4().hex[:8]}",
             "frm": json.dumps({"email": "me@x.test"}),
             "to": json.dumps([{"email": to}]), "o": org}).scalar())


def _status(admin, sid: str) -> tuple[str, str | None]:
    with admin.begin() as c:
        r = c.execute(text("SELECT status, error FROM email_scheduled_sends "
                           "WHERE id = CAST(:i AS uuid)"), {"i": sid}).fetchone()
    return r.status, r.error


def _due(admin, sid: str, *, minutes: int = 1, status: str | None = None) -> None:
    with admin.begin() as c:
        c.execute(text(
            "UPDATE email_scheduled_sends SET send_at = now() - make_interval(mins => :m), "
            "status = COALESCE(:st, status), updated_at = now() - make_interval(mins => :m) "
            "WHERE id = CAST(:i AS uuid)"), {"m": minutes, "st": status, "i": sid})


@_DB_GATE
class TestSendLaterOnARealDatabase:

    def _seed(self, p) -> SimpleNamespace:
        tag = uuid.uuid4().hex[:8]
        s = SimpleNamespace(tag=tag, owner=f"member-{tag}@t8g1.test",
                            other=f"mallory-{tag}@t8g1.test")
        s.box = _account(p.admin_engine, org=p.org_b, owner=s.owner, default=True)
        s.draft = _draft(p.admin_engine, org=p.org_b, account_id=s.box, to="ravi@contoso.test")
        s.me = UserContext(email=s.owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        s.them = UserContext(email=s.other, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        return s

    def _on(self, monkeypatch, p, sent: list[Any], *, fail: Exception | None = None,
            active: bool = True):
        monkeypatch.setattr(sl, "send_later_enabled", lambda: True)

        async def member(owner, org):
            if not active:
                return "The member is no longer active in the organization, so it was not sent."
            return UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=org)

        monkeypatch.setattr(sl, "_member", member)

        async def orgs():
            return [p.org_b]

        async def send(req, background, user):
            if fail is not None:
                raise fail
            sent.append((req, user))
            return {"ok": True}

        monkeypatch.setattr(sl, "_orgs", orgs)
        monkeypatch.setattr(sl, "send_draft_endpoint", send)

    async def _schedule(self, s, at: datetime | None = None) -> str:
        row = await sl.schedule_draft(sl.ScheduleRequest(
            account_id=s.box, draft_id=s.draft,
            send_at=(at or datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            expect={"to": ["ravi@contoso.test"]}), s.me)
        return row.id

    async def test_a_due_draft_goes_once_with_expect(self, promoted, app_engine, monkeypatch):  # noqa: F811
        _assert_non_priv(app_engine)
        p, sent = promoted, []
        s = self._seed(p)
        self._on(monkeypatch, p, sent)
        try:
            async with _as_member(p, p.org_b):
                sid = await self._schedule(s)
                assert await sl.run_tick() == {}  # not due yet
                _due(p.admin_engine, sid)
                assert await sl.run_tick() == {p.org_b: 1}
                assert await sl.run_tick() == {}  # never twice
            [(req, user)] = sent
            assert req.draft_id == s.draft and req.expect.to == ["ravi@contoso.test"]
            assert user.email == s.owner and user.organization_id == p.org_b
            assert _status(p.admin_engine, sid) == ("sent", None)
            # The send dropped the draft row. SET NULL keeps the history.
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_messages WHERE id = CAST(:i AS uuid)"),
                          {"i": s.draft})
            assert _status(p.admin_engine, sid) == ("sent", None)
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")

    async def test_changed_recipients_fail_and_send_nothing(self, promoted, app_engine, monkeypatch):  # noqa: F811
        p, sent = promoted, []
        s = self._seed(p)
        self._on(monkeypatch, p, sent, fail=HTTPException(status_code=409, detail="changed"))
        try:
            async with _as_member(p, p.org_b):
                sid = await self._schedule(s)
                _due(p.admin_engine, sid)
                assert await sl.run_tick() == {}
            status, error = _status(p.admin_engine, sid)
            assert status == "failed" and "recipients of the draft changed" in error
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")

    async def test_a_row_is_claimed_once(self, promoted, app_engine, monkeypatch):  # noqa: F811
        p, sent = promoted, []
        s = self._seed(p)
        self._on(monkeypatch, p, sent)
        try:
            async with _as_member(p, p.org_b):
                sid = await self._schedule(s)
                _due(p.admin_engine, sid)
                first = await sl._claim(p.org_b)
                second = await sl._claim(p.org_b)
                assert [str(r.id) for r in first] == [sid] and second == []
                # A second open schedule of the same draft is refused.
                with pytest.raises(HTTPException) as err:
                    await self._schedule(s)
                assert err.value.status_code == 409
                # A send that stopped part way is failed, never sent again.
                _due(p.admin_engine, sid, minutes=30, status="sending")
                assert await sl._claim(p.org_b) == []
            status, error = _status(p.admin_engine, sid)
            assert status == "failed" and "not sent again" in error and sent == []
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")

    async def test_another_member_cannot_cancel(self, promoted, app_engine, monkeypatch):  # noqa: F811
        p, sent = promoted, []
        s = self._seed(p)
        self._on(monkeypatch, p, sent)
        try:
            async with _as_member(p, p.org_b):
                sid = await self._schedule(s)
                with pytest.raises(HTTPException) as err:
                    await sl.cancel_scheduled(sid, s.them)
                assert err.value.status_code == 404
                assert await sl.list_scheduled(s.them) == []
                assert [r.id for r in await sl.list_scheduled(s.me)] == [sid]
                done = await sl.cancel_scheduled(sid, s.me)
                assert done.status == "cancelled"
                _due(p.admin_engine, sid)
                assert await sl.run_tick() == {}
            assert sent == []
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")

    async def test_a_deleted_or_edited_draft_fails_with_a_reason(self, promoted, app_engine, monkeypatch):  # noqa: F811
        """A Gmail web edit gives a draft a new id and deletes the old row
        (review of #1209, P1). The schedule fails, and says why."""
        p, sent = promoted, []
        s = self._seed(p)
        self._on(monkeypatch, p, sent)
        try:
            async with _as_member(p, p.org_b):
                sid = await self._schedule(s)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_messages WHERE id = CAST(:i AS uuid)"),
                          {"i": s.draft})
            _due(p.admin_engine, sid)
            async with _as_member(p, p.org_b):
                assert await sl.run_tick() == {}
                [listed] = await sl.list_scheduled(s.me)
            status, error = _status(p.admin_engine, sid)
            assert status == "failed" and "changed or was deleted" in error and sent == []
            assert listed.status == "failed" and listed.subject == "Quote"
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")

    async def test_a_member_who_left_sends_nothing(self, promoted, app_engine, monkeypatch):  # noqa: F811
        p, sent = promoted, []
        s = self._seed(p)
        self._on(monkeypatch, p, sent, active=False)
        try:
            async with _as_member(p, p.org_b):
                sid = await self._schedule(s)
                _due(p.admin_engine, sid)
                assert await sl.run_tick() == {}
            status, error = _status(p.admin_engine, sid)
            assert status == "failed" and "no longer active" in error and sent == []
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")

    async def test_the_owner_keeps_the_case_of_the_session(self, promoted, app_engine, monkeypatch):  # noqa: F811
        """The send checks the mailbox owner exactly, so the row keeps the
        session's spelling, and the reads compare lower case (P1)."""
        p, sent = promoted, []
        s = self._seed(p)
        self._on(monkeypatch, p, sent)
        mixed = UserContext(email=s.owner.upper(), role=UserRole.EMPLOYEE,
                            organization_id=p.org_b)
        try:
            async with _as_member(p, p.org_b):
                sid = await self._schedule(s)
                assert [r.id for r in await sl.list_scheduled(mixed)] == [sid]
                _due(p.admin_engine, sid)
                await sl.run_tick()
            [(_req, user)] = sent
            assert user.email == s.owner
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")


async def test_a_bad_id_is_a_404_not_a_500(monkeypatch) -> None:
    monkeypatch.setattr(sl, "send_later_enabled", lambda: True)
    user = UserContext(email="dana@fracktal.test", role=UserRole.EMPLOYEE)
    with pytest.raises(HTTPException) as err:
        await sl.cancel_scheduled("not-a-uuid", user)
    assert err.value.status_code == 404


async def test_the_member_check_fails_closed(monkeypatch) -> None:
    """A removed or suspended member resolves to no access (P1)."""
    from acb_auth import access as acc
    from acb_auth.permissions import EffectiveAccess

    async def inactive(email, **kw):
        return EffectiveAccess(is_active=False)

    async def ident(email):
        return "u1", "org-1"

    monkeypatch.setattr(acc, "resolve_access", inactive)
    monkeypatch.setattr(acc, "resolve_identity", ident)
    assert "no longer active" in await sl._member("dana@fracktal.test", "org-1")

    async def active(email, **kw):
        return EffectiveAccess()

    monkeypatch.setattr(acc, "resolve_access", active)
    assert "no longer in this organization" in await sl._member("dana@fracktal.test", "org-2")
    member = await sl._member("Dana@fracktal.test", "org-1")
    assert member.email == "Dana@fracktal.test" and member.organization_id == "org-1"
