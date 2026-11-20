"""HRD-10d-2 on a real database: ``send_draft`` and ``schedule_send`` check the
files of the draft, and a changed draft gives the HRD-7 words.

Spec: ``project-docs/specs/chat_failure_modes.md`` §6 row HRD-10d-2. The
hermetic half is in ``tests/unit/test_email_card_precondition.py``.

Each test runs the real tool through the real ``/email`` router on a real
Postgres, as a role that cannot bypass RLS, with a fake provider. The card
waits while the test changes the draft as another writer (the sync, or the
member in the Email app) would. Then the member approves::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_send_files_precondition_r8.py -v -rs

Mutations (R7). Each one turns the named tests red:

* M6. ``agents._card_expect``: leave ``files_digest`` out of ``expect``.
  ``test_a_file_added_while_the_card_waited_sends_nothing`` and the schedule
  test. The provider then sends the draft with the new file.
* M7. ``drafting.check_draft_files``: fold the request in place of the stored
  rows. The same tests.
* M8. ``agents._send_refusal``: give the text of before with the flag on.
  Each test that reads "Changed since the card".
* M9. ``agents._card_expect``: send ``files_digest`` with the flag off.
  ``test_with_the_flag_off_a_file_added_still_sends``.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import acb_skills.ask_tools as at
import httpx
import pytest
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import Request
from gateway.routes.email.automation import drafting as dr
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _purge
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_AGENT = (Path(__file__).resolve().parents[2]
          / "apps" / "agents" / "agent-email-assistant" / "agents.py")


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("ea_send_files_r8_agent", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()

RAVI = "ravi@hrd10d2.test"
KIM = "kim@hrd10d2.test"
SEED_TEXT = "Hello Ravi. The quote is attached."
SEED_SUBJECT = "Quote"
#: A time in the future and inside the year that the route takes.
WHEN = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0).isoformat()
_SEND_HEAD = at.card_outcome_text(
    at.CARD_CHANGED_SINCE, f"Nothing was sent. The draft «{SEED_SUBJECT}» stays in Drafts.")
_SCHEDULE_HEAD = at.card_outcome_text(
    at.CARD_CHANGED_SINCE,
    f"Nothing was scheduled. The draft «{SEED_SUBJECT}» stays in Drafts.")


class _FakeProvider:
    """The provider of each request of the mailbox. It holds the live draft
    and records each send."""

    def __init__(self, sent: list[str]) -> None:
        self._sent = sent

    async def get_message_body(self, _provider_message_id: str) -> Any:
        return SimpleNamespace(body_text=SEED_TEXT, body_html="")

    async def get_draft_recipients(self, _provider_message_id: str) -> dict[str, list[str]]:
        return {"to": [RAVI], "cc": [KIM], "bcc": []}

    async def send_draft(self, provider_message_id: str) -> None:
        self._sent.append(provider_message_id)

    async def trash_message(self, _provider_message_id: str) -> None:  # pragma: no cover
        return None


class _Routed:
    """Stands in for ``httpx.AsyncClient`` in the agent module. Each request
    of the tool goes to the real router."""

    def __init__(self, client: Any, calls: list[dict[str, Any]]) -> None:
        self._client = client
        self._calls = calls

    async def __aenter__(self) -> _Routed:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        path = "/" + url.split("/", 3)[-1]
        self._calls.append({"method": method, "path": path, "json": kwargs.get("json")})
        return await self._client.request(method, path, **kwargs)


def _file(c: Any, draft: str, org: str, name: str, size: int) -> None:
    c.execute(text(
        "INSERT INTO email_attachments (message_id, filename, mime_type, size_bytes, "
        "provider_attachment_id, organization_id) VALUES (CAST(:m AS uuid), :n, "
        "'application/pdf', :s, :p, CAST(:o AS uuid))"),
        {"m": draft, "n": name, "s": size, "p": f"pa-{uuid.uuid4().hex[:8]}", "o": org})


@pytest.fixture
def box(promoted, app_engine, monkeypatch):  # noqa: F811 - fixtures
    """One Outlook mailbox of one member in org A, and one draft in it with
    one file, ``quote.pdf``. The draft has no thread, so a send queues no
    background task."""
    _assert_non_priv(app_engine)
    p = promoted
    owner = f"hrd10d2-{uuid.uuid4().hex[:8]}@email-send-files.test"
    with p.admin_engine.begin() as c:
        aid = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, sync_enabled, sync_interval_secs, sync_status, "
            "organization_id) VALUES (:u, 'microsoft', :u, 'x', true, 300, 'idle', "
            "CAST(:o AS uuid)) RETURNING id"), {"u": owner, "o": p.org_a}).scalar_one())
        draft = str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "folder, subject, body_text, from_address, to_addresses, cc_addresses, "
            "bcc_addresses, has_attachments, is_read, organization_id) VALUES "
            "(CAST(:a AS uuid), :pm, 'drafts', :sub, :bt, CAST(:f AS jsonb), "
            "CAST(:to AS jsonb), CAST(:cc AS jsonb), '[]'::jsonb, true, true, "
            "CAST(:o AS uuid)) RETURNING id::text"),
            {"a": aid, "pm": f"pm-{uuid.uuid4().hex[:8]}", "sub": SEED_SUBJECT,
             "bt": SEED_TEXT, "o": p.org_a,
             "f": json.dumps({"name": "", "email": owner}),
             "to": json.dumps([{"name": "Ravi", "email": RAVI}]),
             "cc": json.dumps([{"name": "", "email": KIM}])}).scalar_one())
        _file(c, draft, p.org_a, "quote.pdf", 12_345)
    sent: list[str] = []

    @asynccontextmanager
    async def _provider_session(*_a: Any, **_kw: Any) -> Any:
        yield SimpleNamespace(provider=_FakeProvider(sent), owner_email=owner,
                              account_id=aid, authed=True)

    monkeypatch.setattr(dr, "provider_session", _provider_session)
    try:
        yield SimpleNamespace(p=p, owner=owner, aid=aid, draft=draft, sent=sent)
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_scheduled_sends WHERE account_id = "
                           "CAST(:a AS uuid)"), {"a": aid})
            c.execute(text("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
                      {"a": aid})
        _purge(p.admin_engine, [aid])


def _admin(m: SimpleNamespace, sql: str, **params: Any) -> None:
    with m.p.admin_engine.begin() as c:
        c.execute(text(sql), {"d": m.draft, **params})


def _add_terms(m: SimpleNamespace) -> None:
    with m.p.admin_engine.begin() as c:
        _file(c, m.draft, m.p.org_a, "terms.pdf", 3_000)


def _draft_left(m: SimpleNamespace) -> bool:
    with m.p.admin_engine.connect() as c:
        return c.execute(text("SELECT 1 FROM email_messages WHERE id = CAST(:d AS uuid)"),
                         {"d": m.draft}).first() is not None


def _schedules(m: SimpleNamespace) -> list[Any]:
    with m.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT expect FROM email_scheduled_sends WHERE draft_id = CAST(:d AS uuid)"),
            {"d": m.draft}).fetchall())


class _Session:
    """The real ``/email`` router, as the member of the mailbox."""

    def __init__(self, m: SimpleNamespace) -> None:
        self.m = m

    async def __aenter__(self) -> _Session:
        from acb_auth import UserContext, UserRole, build_access, get_current_user
        from fastapi import FastAPI
        from gateway.routes import email as email_pkg

        user = UserContext(email=self.m.owner, role=UserRole.EMPLOYEE,
                           access=build_access(["feature:email"]),
                           organization_id=self.m.p.org_a)

        async def _caller(request: Request) -> Any:
            bind_tenant(user.organization_id)
            return user

        self.app = FastAPI()
        self.app.include_router(email_pkg.router)
        self.app.dependency_overrides[get_current_user] = _caller
        self._scope = tenant_engine_scope(
            self.m.p.app_url.render_as_string(hide_password=False))
        await self._scope.__aenter__()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://email.test")
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> bool:
        await self.client.__aexit__(*exc)
        await self._scope.__aexit__(*exc)
        return False


def _tool(m: SimpleNamespace, s: _Session, monkeypatch: pytest.MonkeyPatch,
          calls: list[dict[str, Any]], *, flag: bool,
          while_the_card_waits: Any = None) -> None:
    """The tool's requests go to the real router as the member. *flag* puts
    org A on ``CHAT_CARD_PRECONDITION_ORGS``. The card runs
    *while_the_card_waits* and then approves."""
    monkeypatch.setattr(get_settings(), "chat_card_precondition_orgs",
                        m.p.org_a if flag else "", raising=False)
    monkeypatch.setattr(get_settings(), "email_send_later", True, raising=False)
    monkeypatch.setattr(agents, "_current_user_email", lambda: m.owner)
    monkeypatch.setattr(agents, "httpx", SimpleNamespace(
        AsyncClient=lambda **_kw: _Routed(s.client, calls)))
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)

    async def label(_account_id: str) -> str:
        return f"Fracktal · {m.owner}"

    async def noted(*_a: Any, **_kw: Any) -> None:
        calls.append({"method": "NOTE", "path": "sent", "json": None})

    async def card(**_kwargs: Any) -> bool:
        calls.append({"method": "CARD", "path": "", "json": _kwargs})
        if while_the_card_waits is not None:
            while_the_card_waits(m)
        return True

    monkeypatch.setattr(agents, "_mailbox_name", label)
    monkeypatch.setattr(agents, "_note_sent", noted)
    monkeypatch.setattr(at, "confirmation_channel_open", lambda: True)
    monkeypatch.setattr(at, "request_confirmation", card)


async def _bound(m: SimpleNamespace, coro: Any) -> Any:
    """Await *coro* with org A bound as the tenant of the run."""
    token = bind_tenant(m.p.org_a)
    try:
        return await coro
    finally:
        release_tenant(token)


def _claims_no_send(out: str) -> None:
    for claim in ("Draft sent", "Scheduled:", "is scheduled"):
        assert claim not in out, out


def _post(calls: list[dict[str, Any]], path: str) -> dict[str, Any]:
    [one] = [c for c in calls if c["method"] == "POST" and c["path"] == path]
    return one["json"]


@_DB_GATE
class TestTheSendCardsOnARealDatabase:
    async def test_a_draft_that_did_not_change_sends_with_the_files_digest(
        self, box, monkeypatch,
    ):
        """The control: no other writer, and the send goes. The route folds
        the stored rows with the digest of the tool's read, so the two
        digests agree."""
        m = box
        calls: list[dict[str, Any]] = []
        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=True)
            out = await _bound(m, agents.send_draft(m.aid, m.draft))
        assert out == "Draft sent.", out
        assert _post(calls, "/email/drafts/send")["expect"]["files_digest"]
        [card] = [c for c in calls if c["method"] == "CARD"]
        assert 'file "quote.pdf" (12 KB)' in card["json"]["context"]
        assert len(m.sent) == 1
        assert not _draft_left(m)

    async def test_a_file_added_while_the_card_waited_sends_nothing(
        self, box, monkeypatch,
    ):
        m = box
        calls: list[dict[str, Any]] = []
        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=True, while_the_card_waits=_add_terms)
            out = await _bound(m, agents.send_draft(m.aid, m.draft))
        assert out.startswith(_SEND_HEAD), out
        assert "  Files: «quote.pdf (12 KB)» → «quote.pdf (12 KB), terms.pdf (3 KB)»" in out
        _claims_no_send(out)
        assert m.sent == []
        assert _draft_left(m)
        assert not [c for c in calls if c["method"] == "NOTE"]

    async def test_a_file_removed_while_the_card_waited_sends_nothing(
        self, box, monkeypatch,
    ):
        """A fence of the route logic, not of a production path. No path of
        production deletes a row of ``email_attachments``, and
        ``has_attachments`` stays true. So a file removed at the provider is
        not seen today (spec row HRD-10d-2, known limits). The test deletes
        the row as an admin, and proves that the route compares each stored
        row."""
        m = box
        calls: list[dict[str, Any]] = []

        def remove(x: SimpleNamespace) -> None:
            _admin(x, "DELETE FROM email_attachments WHERE message_id = CAST(:d AS uuid)")

        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=True, while_the_card_waits=remove)
            out = await _bound(m, agents.send_draft(m.aid, m.draft))
        assert out.startswith(_SEND_HEAD), out
        assert "  Files: «quote.pdf (12 KB)» → (empty)" in out
        assert m.sent == []
        assert _draft_left(m)

    async def test_a_subject_changed_while_the_card_waited_still_refuses(
        self, box, monkeypatch,
    ):
        """The seal of the text still refuses with the flag on, and the answer
        has the HRD-7 words."""
        m = box
        calls: list[dict[str, Any]] = []

        def retitle(x: SimpleNamespace) -> None:
            _admin(x, "UPDATE email_messages SET subject = 'Quote, by hand' "
                      "WHERE id = CAST(:d AS uuid)")

        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=True, while_the_card_waits=retitle)
            out = await _bound(m, agents.send_draft(m.aid, m.draft))
        assert out.startswith(_SEND_HEAD), out
        assert "  Subject: «Quote» → «Quote, by hand»" in out
        assert m.sent == []

    async def test_a_person_added_while_the_card_waited_still_refuses(
        self, box, monkeypatch,
    ):
        m = box
        calls: list[dict[str, Any]] = []

        def add_bcc(x: SimpleNamespace) -> None:
            _admin(x, "UPDATE email_messages SET bcc_addresses = "
                      "'[{\"email\": \"eve@hrd10d2.test\"}]'::jsonb WHERE id = CAST(:d AS uuid)")

        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=True, while_the_card_waits=add_bcc)
            out = await _bound(m, agents.send_draft(m.aid, m.draft))
        assert out.startswith(_SEND_HEAD), out
        assert "  Bcc: (empty) → «eve@hrd10d2.test»" in out
        assert m.sent == []

    async def test_with_the_flag_off_a_file_added_still_sends(self, box, monkeypatch):
        """Flag off: the ``expect`` of before, with no files, so the send of
        before goes. This is the behaviour that the flag gates."""
        m = box
        calls: list[dict[str, Any]] = []
        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=False, while_the_card_waits=_add_terms)
            out = await _bound(m, agents.send_draft(m.aid, m.draft))
        assert out == "Draft sent.", out
        assert "files_digest" not in _post(calls, "/email/drafts/send")["expect"]
        assert len(m.sent) == 1

    async def test_with_the_flag_off_a_409_gives_the_text_of_before(self, box, monkeypatch):
        m = box
        calls: list[dict[str, Any]] = []

        def add_bcc(x: SimpleNamespace) -> None:
            _admin(x, "UPDATE email_messages SET bcc_addresses = "
                      "'[{\"email\": \"eve@hrd10d2.test\"}]'::jsonb WHERE id = CAST(:d AS uuid)")

        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=False, while_the_card_waits=add_bcc)
            out = await _bound(m, agents.send_draft(m.aid, m.draft))
        assert out == ("Not sent. The draft changed after the card showed it. Read the "
                       "draft again, and ask the user before you send it.")
        assert m.sent == []


@_DB_GATE
class TestTheScheduleCardOnARealDatabase:
    async def test_a_draft_that_did_not_change_is_scheduled(self, box, monkeypatch):
        m = box
        calls: list[dict[str, Any]] = []
        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=True)
            out = await _bound(m, agents.schedule_send(m.aid, m.draft, WHEN))
        assert out.startswith("Scheduled:"), out
        [row] = _schedules(m)
        expect = row.expect if isinstance(row.expect, dict) else json.loads(row.expect)
        assert expect["files_digest"] == _post(calls, "/email/drafts/schedule")[
            "expect"]["files_digest"]

    async def test_a_file_added_while_the_card_waited_stores_no_schedule(
        self, box, monkeypatch,
    ):
        m = box
        calls: list[dict[str, Any]] = []
        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=True, while_the_card_waits=_add_terms)
            out = await _bound(m, agents.schedule_send(m.aid, m.draft, WHEN))
        assert out.startswith(_SCHEDULE_HEAD), out
        assert "  Files: «quote.pdf (12 KB)» → «quote.pdf (12 KB), terms.pdf (3 KB)»" in out
        _claims_no_send(out)
        assert _schedules(m) == []
        assert m.sent == []

    async def test_a_person_added_while_the_card_waited_stores_no_schedule(
        self, box, monkeypatch,
    ):
        """With the flag on, the schedule route checks the people too, before
        it stores the row."""
        m = box
        calls: list[dict[str, Any]] = []

        def add_bcc(x: SimpleNamespace) -> None:
            _admin(x, "UPDATE email_messages SET bcc_addresses = "
                      "'[{\"email\": \"eve@hrd10d2.test\"}]'::jsonb WHERE id = CAST(:d AS uuid)")

        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=True, while_the_card_waits=add_bcc)
            out = await _bound(m, agents.schedule_send(m.aid, m.draft, WHEN))
        assert out.startswith(_SCHEDULE_HEAD), out
        assert "  Bcc: (empty) → «eve@hrd10d2.test»" in out
        assert _schedules(m) == []

    async def test_the_fire_of_a_schedule_refuses_a_file_added_after_it(
        self, box, monkeypatch,
    ):
        """End to end: the tool stores the schedule with ``files_digest``. Then
        a file goes onto the draft, and the schedule falls due. The job
        (``send_later.run_tick``) builds ``DraftExpect`` from the stored row
        and sends through the real ``send_draft_endpoint``. It refuses: the
        row is ``failed`` with the files reason, and the provider sends
        nothing."""
        from acb_auth import UserContext, UserRole
        from gateway.routes.email.automation import send_later as sl

        m = box
        calls: list[dict[str, Any]] = []
        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=True)
            out = await _bound(m, agents.schedule_send(m.aid, m.draft, WHEN))
            assert out.startswith("Scheduled:"), out
            _add_terms(m)
            _admin(m, "UPDATE email_scheduled_sends SET send_at = now() - interval '1 minute' "
                      "WHERE draft_id = CAST(:d AS uuid)")

            async def orgs() -> list[str]:
                return [m.p.org_a]

            async def member(owner: str, org: str) -> Any:
                return UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=org)

            monkeypatch.setattr(sl, "_orgs", orgs)
            monkeypatch.setattr(sl, "_member", member)
            assert await sl.run_tick() == {}
        with m.p.admin_engine.connect() as c:
            row = c.execute(text(
                "SELECT status, error, expect FROM email_scheduled_sends "
                "WHERE draft_id = CAST(:d AS uuid)"), {"d": m.draft}).one()
        expect = row.expect if isinstance(row.expect, dict) else json.loads(row.expect)
        assert expect["files_digest"]
        assert row.status == "failed"
        assert row.error == ("The files of the draft changed after it was scheduled, so "
                             "it was not sent.")
        assert m.sent == []
        assert _draft_left(m)

    async def test_with_the_flag_off_the_schedule_stores_the_expect_of_before(
        self, box, monkeypatch,
    ):
        m = box
        calls: list[dict[str, Any]] = []
        async with _Session(m) as s:
            _tool(m, s, monkeypatch, calls, flag=False, while_the_card_waits=_add_terms)
            out = await _bound(m, agents.schedule_send(m.aid, m.draft, WHEN))
        assert out.startswith("Scheduled:"), out
        [row] = _schedules(m)
        expect = row.expect if isinstance(row.expect, dict) else json.loads(row.expect)
        assert set(expect) == {"to", "cc", "bcc", "text_digest"}
