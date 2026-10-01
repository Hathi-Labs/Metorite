"""EM-T1a — the OAuth callback and the Graph webhook under FORCE RLS (R8).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.1, items 4 and 5.

The hermetic suites (``test_email_oauth_state.py``, ``test_email_webhook.py``)
prove the order of the checks with fake sessions. A fake session agrees with
any SQL it is handed, so this file runs the REAL handlers against the
phase-4-promoted two-org catalog of ``test_h3_rls_promotion_rehearsal``, as the
non-privileged role ``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS). The process
engine is pointed at that role with ``tenant_engine_scope``.

R7 fences named here:

* ``email-callback-writes-its-own-tenant``: a callback for a member of org B
  writes a row of org B, and a session of org A cannot read that row. A
  reconnect takes the UPDATE path under RLS and still leaves one row.
* ``email-callback-refuses-a-foreign-org``: a state that names org A for a
  member of org B writes nothing.
* ``email-webhook-matches-only-its-tenant``: a webhook signed for org A with a
  subscription id of org B matches nothing. Signed for org B, it matches.
* the mechanism proof: an UNBOUND read of ``email_accounts`` as the same role
  sees nothing, so the fences above are not vacuous.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_tenant_bind_rls.py -v -rs
"""
from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from fastapi import BackgroundTasks
from gateway.routes.email.transport import oauth, signing
from gateway.routes.email.transport import sync as sync_mod
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import tenant_engine_scope

# Reuse the two-org phase-4 fixture + its DB gate (non-priv role acb_app_h3rls).
# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

SECRET = "em-t1a-r8-secret"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gateway_session_secret", SECRET, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.example.test")
    # The callback asks resolve_identity for the member's organization. Under
    # FORCE RLS only the cutover read (the RLS-EXEMPT shadow tables) can answer
    # an unbound question, which is the production posture.
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")


def _seed_identity(admin_engine, *, org_id: str, email: str) -> str:
    """An ACTIVE membership in the RLS-EXEMPT shadow, as the admin."""
    with admin_engine.begin() as c:
        ident = str(c.execute(text(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
            "RETURNING id"), {"e": email}).scalar_one())
        c.execute(text(
            "INSERT INTO org_membership (organization_id, user_id, status) "
            "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active')"),
            {"o": org_id, "u": ident})
    return ident


def _purge(admin_engine, *, email: str, mailbox: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM email_accounts WHERE email_address = :m"),
                  {"m": mailbox})
        c.execute(text(
            "DELETE FROM org_membership m USING user_identity ui "
            " WHERE m.user_id = ui.id AND lower(ui.email) = lower(:e)"),
            {"e": email})
        c.execute(text("DELETE FROM user_identity WHERE lower(email) = lower(:e)"),
                  {"e": email})


def _rows_as(app_url, org: str | None, mailbox: str) -> int:
    """How many rows for ``mailbox`` the non-priv role sees, bound to ``org``
    (or unbound when ``org`` is None)."""
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            if org is not None:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": org})
            return c.execute(text(
                "SELECT count(*) FROM email_accounts WHERE email_address = :m"),
                {"m": mailbox}).scalar_one()
    finally:
        eng.dispose()


def _admin_rows(admin_engine, mailbox: str) -> list:
    with admin_engine.connect() as c:
        return c.execute(text(
            "SELECT user_id, organization_id::text AS org, sync_status, "
            "credentials_encrypted FROM email_accounts WHERE email_address = :m"),
            {"m": mailbox}).mappings().all()


class _Store:
    def __init__(self) -> None:
        self.n = 0

    def encrypt(self, raw: str) -> str:
        self.n += 1
        return f"enc-{self.n}:{raw}"


@pytest.fixture()
def provider_fakes(monkeypatch):
    """The provider legs of the callback, faked. The DB legs stay real."""
    import email_ingestion.scheduler as sched
    from acb_llm import key_store

    state = {"mailbox": f"box-{uuid.uuid4().hex[:8]}@contoso.test"}

    async def _exchange(code, redirect_uri):
        return {"access_token": "at", "refresh_token": "rt"}

    async def _mailbox(provider, token):
        return state["mailbox"]

    async def _no_sync(account_id):
        return None

    store = _Store()
    monkeypatch.setattr(oauth, "_exchange_msft_token", _exchange)
    monkeypatch.setattr(oauth, "_get_provider_email", _mailbox)
    monkeypatch.setattr(key_store, "get_key_store", lambda: store)
    monkeypatch.setattr(sched, "refresh_account_sync", _no_sync)
    return state


def _error(resp) -> str | None:
    assert resp.status_code == 302
    return parse_qs(urlparse(resp.headers["location"]).query).get("error", [None])[0]


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


@_DB_GATE
class TestTheCallbackWritesItsOwnTenant:

    async def test_a_member_of_org_b_writes_an_org_b_row_org_a_cannot_read(
        self, promoted, app_engine, provider_fakes,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        email = f"member-{uuid.uuid4().hex[:8]}@em-t1a.test"
        mailbox = provider_fakes["mailbox"]
        _seed_identity(p.admin_engine, org_id=p.org_b, email=email)
        user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                state = signing.sign_oauth_state(
                    org=p.org_b, member=email, provider="microsoft",
                )
                first = await oauth.oauth_callback(
                    "microsoft", user=user, code="c1", state=state, error=None,
                )
            assert _error(first) is None, (
                f"the callback refused a valid member: {first.headers['location']}"
            )
            rows = _admin_rows(p.admin_engine, mailbox)
            assert len(rows) == 1, "the INSERT did not land under FORCE RLS"
            assert rows[0]["org"] == p.org_b
            assert rows[0]["user_id"] == email
            assert _rows_as(p.app_url, p.org_b, mailbox) == 1
            assert _rows_as(p.app_url, p.org_a, mailbox) == 0, (
                "org A read the mailbox row of org B"
            )

            # Reconnect: the UPDATE path, under RLS, in the same tenant.
            with p.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE email_accounts SET sync_status = 'error' "
                    "WHERE email_address = :m"), {"m": mailbox})
            async with tenant_engine_scope(app_dsn):
                second = await oauth.oauth_callback(
                    "microsoft", user=user, code="c2",
                    state=signing.sign_oauth_state(
                        org=p.org_b, member=email, provider="microsoft",
                    ),
                    error=None,
                )
            assert _error(second) is None
            rows = _admin_rows(p.admin_engine, mailbox)
            assert len(rows) == 1, "a reconnect must update, never add a row"
            assert rows[0]["sync_status"] == "idle", (
                "the reconnect UPDATE matched nothing under RLS"
            )
            assert rows[0]["credentials_encrypted"].startswith("enc-2:")
        finally:
            _purge(p.admin_engine, email=email, mailbox=mailbox)

    async def test_a_state_for_another_org_writes_nothing(
        self, promoted, provider_fakes,  # noqa: F811
    ):
        p = promoted
        email = f"member-{uuid.uuid4().hex[:8]}@em-t1a.test"
        mailbox = provider_fakes["mailbox"]
        _seed_identity(p.admin_engine, org_id=p.org_b, email=email)
        user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                resp = await oauth.oauth_callback(
                    "microsoft", user=user, code="c",
                    state=signing.sign_oauth_state(
                        org=p.org_a, member=email, provider="microsoft",
                    ),
                    error=None,
                )
            assert _error(resp) == "invalid_state"
            assert _admin_rows(p.admin_engine, mailbox) == []
        finally:
            _purge(p.admin_engine, email=email, mailbox=mailbox)


@_DB_GATE
class TestTheWebhookMatchesOnlyItsTenant:

    async def test_org_a_cannot_reach_a_subscription_of_org_b(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        mailbox = f"box-{uuid.uuid4().hex[:8]}@contoso.test"
        sub_id = f"sub-{uuid.uuid4().hex}"
        with p.admin_engine.begin() as c:
            account_id = str(c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, webhook_subscription_id, "
                "webhook_client_state, organization_id) "
                "VALUES ('owner@em-t1a.test', 'microsoft', :m, 'x', :s, 'cs-b', "
                "CAST(:o AS uuid)) RETURNING id"),
                {"m": mailbox, "s": sub_id, "o": p.org_b}).scalar_one())

        class _Req:
            def __init__(self, org: str) -> None:
                self.query_params = {"org": org, "sig": signing.sign_webhook_org(org)}

            async def json(self):
                return {"value": [{"subscriptionId": sub_id, "clientState": "cs-b"}]}

        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                wrong = BackgroundTasks()
                resp = await sync_mod.microsoft_webhook(_Req(p.org_a), wrong)
                assert resp.status_code == 202
                assert len(wrong.tasks) == 0, (
                    "a webhook signed for org A matched a subscription of org B"
                )

                right = BackgroundTasks()
                await sync_mod.microsoft_webhook(_Req(p.org_b), right)
                assert len(right.tasks) == 1, (
                    "the webhook of org B did not match its own subscription "
                    "under FORCE RLS"
                )
                assert right.tasks[0].args == (account_id, p.org_b)
        finally:
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE email_address = :m"),
                          {"m": mailbox})

    def test_an_unbound_read_sees_no_account(
        self, promoted,  # noqa: F811
    ):
        """The mechanism: the old unbound session read nothing under FORCE
        RLS, so a revert to it turns the fences above red."""
        p = promoted
        mailbox = f"box-{uuid.uuid4().hex[:8]}@contoso.test"
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO email_accounts (user_id, provider, email_address, "
                "credentials_encrypted, organization_id) "
                "VALUES ('owner@em-t1a.test', 'microsoft', :m, 'x', "
                "CAST(:o AS uuid))"), {"m": mailbox, "o": p.org_b})
        try:
            assert _rows_as(p.app_url, None, mailbox) == 0
            assert _rows_as(p.app_url, p.org_b, mailbox) == 1
        finally:
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE email_address = :m"),
                          {"m": mailbox})
