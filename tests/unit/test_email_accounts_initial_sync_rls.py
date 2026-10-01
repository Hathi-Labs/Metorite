"""EM-T3a item 5 — the three account reads return ``initial_sync_done`` (R8).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.3, EM-T3a.

A fake session agrees with any SQL it is handed, so this file runs the REAL
handlers of ``transport/accounts.py`` against the phase-4-promoted two-org
catalog of ``test_h3_rls_promotion_rehearsal``, as the non-privileged role
``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS).

R7 fence named here:

* ``email-initial-sync-flag``: ``GET /email/accounts``, the set-default
  ``RETURNING`` and the update ``RETURNING`` each carry the column value of
  ``initial_sync_done`` under FORCE RLS.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_accounts_initial_sync_rls.py -v -rs
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.transport import accounts
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed(admin_engine, *, org: str, owner: str, done: bool) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, initial_sync_done, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', :d, CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@contoso.test",
             "d": done, "o": org}).scalar_one())


@pytest.fixture()
def no_sync(monkeypatch):
    import email_ingestion.scheduler as sched

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(sched, "refresh_account_sync", _noop)
    monkeypatch.setattr(sched, "remove_account_sync", _noop)


@_DB_GATE
class TestTheAccountReadsReturnTheFirstSyncFlag:

    async def test_list_default_and_update_return_the_column(
        self, promoted, app_engine, no_sync,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t3a.test"
        done_id = _seed(p.admin_engine, org=p.org_b, owner=owner, done=True)
        fresh_id = _seed(p.admin_engine, org=p.org_b, owner=owner, done=False)
        user = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                listed = {a.id: a for a in await accounts.list_accounts(user=user)}
                assert set(listed) == {done_id, fresh_id}, (
                    "the list did not read both rows of org B under FORCE RLS"
                )
                assert listed[done_id].initial_sync_done is True
                assert listed[fresh_id].initial_sync_done is False

                made_default = await accounts.set_default_account(done_id, user=user)
                assert made_default.initial_sync_done is True

                updated = await accounts.update_account(
                    done_id, accounts.AccountUpdateModel(label="Work"), user=user,
                )
                assert updated.initial_sync_done is True
                updated = await accounts.update_account(
                    fresh_id, accounts.AccountUpdateModel(label="New"), user=user,
                )
                assert updated.initial_sync_done is False
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                          {"u": owner})

    async def test_org_a_lists_no_account_of_org_b(
        self, promoted, app_engine, no_sync,  # noqa: F811
    ):
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@em-t3a.test"
        _seed(p.admin_engine, org=p.org_b, owner=owner, done=True)
        user = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_a)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_a)
        try:
            async with tenant_engine_scope(app_dsn):
                assert await accounts.list_accounts(user=user) == []
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_accounts WHERE user_id = :u"),
                          {"u": owner})
