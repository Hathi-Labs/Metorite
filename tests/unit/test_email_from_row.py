"""WS-17 EM-T8c — the From row: which mailbox sends, and the warnings.

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.4 and §11.7.3.
Decision D-EM-20.

R7 fences named here:

* ``email-work-domain``: :func:`work_domain` gives the domain of an
  organization address and ``None`` for a consumer domain, and every account
  read returns it. The UI keeps no domain list of its own.
* ``email-needs-reconnect``: only a sync error of the sign-in sets
  ``needs_reconnect``. A 429 or a 503 during an import does not, because a
  send still works then.
* ``email-sent-from`` (R8): ``GET /email/contacts/sent-from`` maps each
  address to the mailbox of the member that wrote to it last. It reads only
  the mailboxes of the member, it ignores case, and a mailbox of another
  member never answers.

The UI half is fenced in
``workbench/control_plane/src/app/email/lib/fromRow.test.ts``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_from_row.py -v -rs
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.mailbox_identity import work_domain
from gateway.routes.email.transport import accounts, contacts
from gateway.routes.email.transport.accounts import needs_reconnect
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)


@pytest.mark.parametrize(("address", "want"), [
    ("vj@fracktal.in", "fracktal.in"),
    ("VJ@Fracktal.IN ", "fracktal.in"),
    ("dana@outlook.com", None),
    ("dana@gmail.com", None),
    ("not-an-address", None),
    ("", None),
    (None, None),
])
def test_the_work_domain(address, want) -> None:
    assert work_domain(address) == want


@pytest.mark.parametrize(("status", "error", "want"), [
    ("error", "Provider authentication failed", True),
    ("error", "Client error '400 Bad Request' for url "
              "'https://login.microsoftonline.com/common/oauth2/v2.0/token'", True),
    ("error", "Missing OAuth credentials for token refresh", True),
    ("error", "invalid_grant: AADSTS70008 the refresh token has expired", True),
    ("error", "Server error '503 Service Unavailable' for url "
              "'https://graph.microsoft.com/v1.0/me/messages'", False),
    ("error", "Client error '429 Too Many Requests'", False),
    ("idle", "Provider authentication failed", False),
    ("error", None, False),
])
def test_only_a_failed_sign_in_needs_a_reconnect(status, error, want) -> None:
    assert needs_reconnect(status, error) is want


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _account(admin_engine, *, org: str, owner: str, address: str) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": address, "o": org}).scalar_one())


def _sent(admin_engine, *, org: str, account_id: str, to: list[str],
          minutes_ago: int, folder: str = "sent") -> None:
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, folder, "
            "from_address, to_addresses, subject, received_at, organization_id) "
            "VALUES (CAST(:a AS uuid), :p, :f, '{}'::jsonb, CAST(:to AS jsonb), "
            "'s', :r, CAST(:o AS uuid))"),
            {"a": account_id, "p": f"pm-{uuid.uuid4().hex[:10]}", "f": folder,
             "to": "[" + ",".join(f'{{"email": "{t}"}}' for t in to) + "]",
             "r": datetime.now(UTC) - timedelta(minutes=minutes_ago),
             "o": org})


def _purge(admin_engine, *account_ids: str) -> None:
    with admin_engine.begin() as c:
        for a in account_ids:
            c.execute(text("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
                      {"a": a})
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                      {"a": a})


@_DB_GATE
class TestSentFromOnARealDatabase:

    async def test_the_mailbox_that_wrote_last_answers(self, promoted, app_engine):  # noqa: F811
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@em-t8c.test"
        work = _account(p.admin_engine, org=p.org_b, owner=owner,
                        address=f"w-{uuid.uuid4().hex[:6]}@fracktal.in")
        home = _account(p.admin_engine, org=p.org_b, owner=owner,
                        address=f"h-{uuid.uuid4().hex[:6]}@outlook.com")
        other = _account(p.admin_engine, org=p.org_b, owner="mallory@em-t8c.test",
                         address=f"m-{uuid.uuid4().hex[:6]}@evil.test")
        # Ravi heard from work an hour ago and from home a minute ago: home
        # answers. Kim heard only from work. Lee heard from the mailbox of
        # ANOTHER member, and a received mail to Lee does not count.
        _sent(p.admin_engine, org=p.org_b, account_id=work, to=["Ravi@Contoso.test"],
              minutes_ago=60)
        _sent(p.admin_engine, org=p.org_b, account_id=home, to=["ravi@contoso.test"],
              minutes_ago=1)
        _sent(p.admin_engine, org=p.org_b, account_id=work, to=["kim@contoso.test"],
              minutes_ago=5)
        _sent(p.admin_engine, org=p.org_b, account_id=other, to=["lee@contoso.test"],
              minutes_ago=5)
        _sent(p.admin_engine, org=p.org_b, account_id=work, to=["lee@contoso.test"],
              minutes_ago=5, folder="inbox")
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                got = await contacts.sent_from(
                    emails=" RAVI@contoso.test, kim@contoso.test,lee@contoso.test,bad",
                    user=me)
                assert got == {"ravi@contoso.test": home, "kim@contoso.test": work}

                [first] = [a for a in await accounts.list_accounts(user=me)
                           if a.id == work]
                assert first.work_domain == "fracktal.in"
        finally:
            release_tenant(token)
            _purge(p.admin_engine, work, home, other)


async def test_no_address_reads_nothing(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("an empty request opened a session")

    monkeypatch.setattr(contacts, "_tenant_session", _boom)
    me = UserContext(email="a@b.test", role=UserRole.EMPLOYEE)
    assert await contacts.sent_from(emails=" , nope", user=me) == {}


async def test_a_failure_answers_empty(monkeypatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("database down")

    monkeypatch.setattr(contacts, "_tenant_session", _boom)
    me = UserContext(email="a@b.test", role=UserRole.EMPLOYEE)
    assert await contacts.sent_from(emails="x@y.test", user=me) == {}


def test_the_request_takes_at_most_twenty_addresses() -> None:
    assert contacts.SENT_FROM_MAX == 20
