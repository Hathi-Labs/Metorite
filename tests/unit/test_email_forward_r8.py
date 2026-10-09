"""R8 — ``POST /email/forward`` on a real database.

The hermetic half is ``test_email_forward.py``. This half runs the route's
real SQL (the owner read, the list of files, the one owned fetch of each file
and the signature read) as the NON-privileged role ``acb_app_h3rls`` under
FORCE ROW LEVEL SECURITY, so a fake that agrees with any SQL cannot pass it.
Only the two provider sessions are stubbed.

Fence ``email-forward-r8``:

* the owner forwards, and the bytes of both files reach the send;
* a second member of the SAME organization gets 404, before any fetch;
* the owner's mail read under ANOTHER organization's tenant gets 404;
* a file of another mail of the owner, named in ``attachment_ids``, is 404.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_forward_r8.py -v -rs
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.email.transport import attachments as att_mod
from gateway.routes.email.transport import forward as fwd
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


def _assert_non_priv(engine) -> None:
    with engine.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed")


@pytest.fixture()
def seeded(promoted, app_engine, monkeypatch):  # noqa: F811
    """One member of org B with one mailbox. Mail A has two files, mail B one."""
    _assert_non_priv(app_engine)
    p = promoted
    owner = f"owner-{uuid.uuid4().hex[:8]}@em-fwd.test"
    with p.admin_engine.begin() as c:
        box = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, initial_sync_done, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', true, CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:6]}@em-fwd.test",
             "o": p.org_b}).scalar_one())

        def _mail(pid: str, subject: str) -> str:
            return str(c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "thread_id, folder, from_address, to_addresses, subject, "
                "body_text, body_html, received_at, organization_id) VALUES "
                "(CAST(:a AS uuid), :pid, :tid, 'inbox', "
                "'{\"name\": \"Ravi\", \"email\": \"ravi@contoso.test\"}'::jsonb, "
                "'[]'::jsonb, :s, 'The BQ quote.', '<p>The BQ quote.</p>', now(), "
                "CAST(:o AS uuid)) RETURNING id"),
                {"a": box, "pid": pid, "tid": f"t-{pid}", "s": subject,
                 "o": p.org_b}).scalar_one())

        mail_a = _mail("pm-a", "Re: BQ quote")
        mail_b = _mail("pm-b", "Another mail")

        def _file(mail: str, name: str, size: int) -> str:
            return str(c.execute(text(
                "INSERT INTO email_attachments (message_id, filename, mime_type, "
                "size_bytes, provider_attachment_id, organization_id) VALUES "
                "(CAST(:m AS uuid), :n, 'application/pdf', :s, :pa, "
                "CAST(:o AS uuid)) RETURNING id"),
                {"m": mail, "n": name, "s": size, "pa": f"prov-{name}",
                 "o": p.org_b}).scalar_one())

        pdf = _file(mail_a, "quote.pdf", 14)
        sheet = _file(mail_a, "rates.xlsx", 8)
        other = _file(mail_b, "other.pdf", 5)

    payloads = {"prov-quote.pdf": b"%PDF-1.7 quote", "prov-rates.xlsx": b"PK sheet",
                "prov-other.pdf": b"other"}
    rec = SimpleNamespace(sent=[], fetched=[])

    class _AttProvider:
        async def get_attachment(self, _msg: str, att: str) -> bytes:
            rec.fetched.append(att)
            return payloads[att]

    @asynccontextmanager
    async def _att_session(*_a: Any, **_k: Any):
        yield SimpleNamespace(provider=_AttProvider())

    class _SendProvider:
        async def send_message(self, **kw: Any) -> str:
            rec.sent.append(kw)
            return "sent-1"

        async def get_message_body(self, _pmid: str) -> Any:
            raise AssertionError("a stored body needs no provider read")

    @asynccontextmanager
    async def _send_session(db: Any, user_email: str, account_id: str):
        yield SimpleNamespace(provider=_SendProvider())

    monkeypatch.setattr(att_mod, "provider_session", _att_session)
    monkeypatch.setattr(fwd, "provider_session", _send_session)
    app_dsn = p.app_url.render_as_string(hide_password=False)

    async def _forward(user_email: str = owner, org: str = p.org_b, **body: Any):
        # No organization on the caller: the owned fetch then reads no cache,
        # and the test touches no Redis. The tenant comes from the bind.
        user = UserContext(email=user_email, role=UserRole.EMPLOYEE, organization_id=None)
        req = fwd.ForwardEmailRequest(**{"message_id": mail_a, "to": ["geo@fracktal.test"], **body})
        token = bind_tenant(org)
        try:
            async with tenant_engine_scope(app_dsn):
                return await fwd.forward_email(req, user=user)
        finally:
            release_tenant(token)

    try:
        yield SimpleNamespace(forward=_forward, rec=rec, owner=owner, org_a=p.org_a,
                              org_b=p.org_b, pdf=pdf, sheet=sheet, other=other)
    finally:
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_attachments WHERE message_id IN "
                           "(SELECT id FROM email_messages WHERE account_id = CAST(:a AS uuid))"),
                      {"a": box})
            c.execute(text("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
                      {"a": box})
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"), {"a": box})


class TestTheForwardRouteOnARealDatabase:
    async def test_the_owner_forwards_and_both_files_go(self, seeded) -> None:
        out = await seeded.forward(note="See below.")
        assert out["ok"] is True
        [call] = seeded.rec.sent
        assert sorted((a["filename"], a["content"]) for a in call["attachments"]) == [
            ("quote.pdf", b"%PDF-1.7 quote"), ("rates.xlsx", b"PK sheet")]
        assert call["subject"] == "Fwd: BQ quote"
        assert "<p>The BQ quote.</p>" in call["body_html"]

    async def test_a_second_member_of_the_same_org_gets_404(self, seeded) -> None:
        with pytest.raises(HTTPException) as exc:
            await seeded.forward(user_email="colleague@em-fwd.test")
        assert exc.value.status_code == 404
        assert seeded.rec.fetched == [] and seeded.rec.sent == []

    async def test_another_organization_cannot_see_the_mail(self, seeded) -> None:
        with pytest.raises(HTTPException) as exc:
            await seeded.forward(org=seeded.org_a)
        assert exc.value.status_code == 404
        assert seeded.rec.sent == []

    async def test_a_file_of_another_mail_is_404(self, seeded) -> None:
        with pytest.raises(HTTPException) as exc:
            await seeded.forward(attachment_ids=[seeded.pdf, seeded.other])
        assert exc.value.status_code == 404
        assert seeded.rec.fetched == [] and seeded.rec.sent == []

    async def test_named_files_go_alone(self, seeded) -> None:
        out = await seeded.forward(attachment_ids=[seeded.sheet])
        assert out["attachments"] == ["rates.xlsx"]
        assert seeded.rec.fetched == ["prov-rates.xlsx"]
