"""EM-G11f1b — the mailbox teardown in the member purge, on a real database.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.13.9.2 (PR-G7b),
acceptance 6, 7 and 10. Acceptance 8 and 9 are the org purge, PR-G7c.

R7 fence named here: ``email-purge-wiring``.

**R8.** The real SQL on the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS), under FORCE RLS. The REAL operator door
(``DELETE /internal/operator/organizations/{slug}/members/{email}``,
``mode=purge``) runs the REAL shared body, ``purge_member_rows``. The
providers, Google, the scheduler and Redis are recorders, because no test
reaches a network.

* ``test_the_purge_tears_down_each_mailbox_of_the_member`` (acceptance 6): a
  member with one Gmail and one Outlook mailbox. The purge writes two purge
  rows in its transaction, each child of each mailbox is empty after it, and
  each Metorite filter goes after the commit. A mailbox of another member of
  the same org keeps its rows and its filters.
* ``test_a_purge_that_rolls_back_leaves_no_purge_row`` (acceptance 7).
* ``test_a_sync_after_the_commit_writes_no_child_row`` (acceptance 10).

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_purge_wiring.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_llm.key_store as key_store_mod
import email_ingestion.scheduler as sched
from acb_common.db import bind_tenant, release_tenant, tenant_session
from email_ingestion.persist import upsert_message
from email_ingestion.providers.base import EmailAddress, EmailMessage
from gateway.routes import operator as operator_door
from gateway.routes.email import teardown
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TOKEN = "rt-em-g11f1b-must-not-leak"
OP_TOKEN = "op-em-g11f1b"


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed")


def _admin_read(admin, sql: str, **params: Any) -> list[Any]:
    with admin.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


def _grant_check_to_app_role(admin) -> None:
    """Migration 263 grants its function to ``acb_app`` only. The rehearsal
    role has a suite-private name, so it gets the same grant here."""
    from tests.unit.test_h3_rls_promotion_rehearsal import _APP_ROLE

    with admin.begin() as c:
        c.execute(text(
            "GRANT EXECUTE ON FUNCTION public.email_gmail_grant_shared(text, uuid) "
            f"TO {_APP_ROLE}"))


def _org_slug(admin, org: str) -> str:
    [(slug,)] = _admin_read(admin, "SELECT slug FROM organization "
                                   "WHERE id = CAST(:o AS uuid)", o=org)
    return slug


def seed_member(admin, org: str, email: str, *, owner: bool = False) -> str:
    """One active ``app_user`` of ``org``. An owner also holds the ``owner``
    role, so ``assert_owner_survives`` passes for a purge of somebody else."""
    with admin.begin() as c:
        uid = str(c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
            {"e": email, "o": org}).scalar_one())
        if owner:
            role = c.execute(text(
                "INSERT INTO org_role (organization_id, slug, display_name, is_system, rank) "
                "VALUES (CAST(:o AS uuid), 'owner', 'Owner', true, 0) "
                "ON CONFLICT (organization_id, slug) DO UPDATE SET slug = EXCLUDED.slug "
                "RETURNING id"), {"o": org}).scalar_one()
            c.execute(text("INSERT INTO user_role (user_id, role_id, organization_id) "
                           "VALUES (CAST(:u AS uuid), :r, CAST(:o AS uuid))"),
                      {"u": uid, "r": role, "o": org})
    return uid


def seed_mailbox(admin, org: str, owner: str, *, provider: str,
                 filters: tuple[str, ...] = (), sub: str | None = None) -> str:
    """One mailbox with one message and one sender row for each filter id."""
    with admin.begin() as c:
        acc = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, is_default, webhook_subscription_id, organization_id) "
            "VALUES (:u, :p, :m, 'blob-g7b', false, :s, CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "p": provider, "s": sub, "o": org,
             "m": f"box-{uuid.uuid4().hex[:8]}@em-g11f1b.test"}).scalar_one())
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "from_address, to_addresses, subject, organization_id) "
            "VALUES (CAST(:a AS uuid), :pm, CAST(:f AS jsonb), CAST(:t AS jsonb), "
            "'hello', CAST(:o AS uuid))"),
            {"a": acc, "pm": f"pm-{uuid.uuid4().hex[:12]}", "o": org,
             "f": json.dumps({"email": "s@sender.test"}),
             "t": json.dumps([{"email": "to@em-g11f1b.test"}])})
        for fid in filters:
            c.execute(text(
                "INSERT INTO email_newsletters (account_id, email, status, "
                "auto_archive_filter_id, organization_id) VALUES (CAST(:a AS uuid), "
                ":e, 'AUTO_ARCHIVED', :f, CAST(:o AS uuid))"),
                {"a": acc, "e": f"news-{uuid.uuid4().hex[:8]}@sender.test",
                 "f": fid, "o": org})
    return acc


def wipe(admin, *emails: str) -> None:
    with admin.begin() as c:
        for email in emails:
            c.execute(text("DELETE FROM email_mailbox_purges WHERE lower(owner_email) = "
                           "lower(:e)"), {"e": email})
            c.execute(text("DELETE FROM email_accounts WHERE lower(user_id) = lower(:e)"),
                      {"e": email})
            c.execute(text("DELETE FROM app_user WHERE lower(email) = lower(:e)"),
                      {"e": email})


class _Recorder:
    """The provider, Google, the scheduler and Redis, as recorders."""

    def __init__(self, admin) -> None:
        self.admin = admin
        self.events: list[tuple[Any, ...]] = []

    def gone(self, account_id: str) -> bool:
        return _admin_read(self.admin, "SELECT 1 FROM email_accounts "
                                       "WHERE id = CAST(:a AS uuid)", a=account_id) == []


@pytest.fixture()
def recorder(promoted, monkeypatch):  # noqa: F811
    rec = _Recorder(promoted.admin_engine)

    async def _stop(account_id: str) -> None:
        rec.events.append(("stop", account_id, rec.gone(account_id)))

    class _Provider:
        def __init__(self, name: str) -> None:
            self.name = name

        async def delete_filter(self, filter_id: str) -> None:
            rec.events.append(("filter_delete", self.name, filter_id))

        async def delete_subscription(self, sub: str) -> int:
            rec.events.append(("graph_delete", sub))
            return 204

    async def _revoke(token: str) -> int:
        rec.events.append(("revoke", token))
        return 200

    class _NoKeys:
        async def scan_iter(self, pattern: Any, *, count: int = 200):
            for _ in ():
                yield _

        async def delete(self, *keys: Any) -> int:
            return 0

    monkeypatch.setenv("GATEWAY_OPERATOR_TOKEN", OP_TOKEN)
    monkeypatch.setattr(sched, "remove_account_sync", _stop)
    monkeypatch.setattr(sched, "get_scheduler_status", lambda: {"accounts": []})
    monkeypatch.setattr(teardown, "_instantiate_provider",
                        lambda name, creds: _Provider(name))
    monkeypatch.setattr(teardown, "_post_revoke", _revoke)
    monkeypatch.setattr(teardown, "_redis", _NoKeys)
    monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
        decrypt=lambda blob: json.dumps({"refresh_token": TOKEN})))
    return rec


async def member_purge(p, email: str) -> dict[str, Any]:
    """The REAL operator door with ``mode=purge``, as the app role."""
    app_dsn = p.app_url.render_as_string(hide_password=False)
    slug = _org_slug(p.admin_engine, p.org_b)
    async with tenant_engine_scope(app_dsn):
        return await operator_door.remove_organization_member(
            slug, email, confirm=email, mode="purge", x_operator_token=OP_TOKEN)


def _children(admin) -> list[Any]:
    from tests.unit.test_email_mailbox_purge_durable import _CHILDREN_SQL

    with admin.connect() as c:
        return list(c.execute(text(_CHILDREN_SQL)).fetchall())


def _rows_of(admin, children: list[Any], account_id: str) -> dict[str, int]:
    """The rows of each direct child of one mailbox, by table."""
    out = {}
    with admin.connect() as c:
        for r in children:
            if r.parent != "email_accounts":
                continue
            n = c.execute(text(f'SELECT count(*) FROM "{r.child}" WHERE "{r.col}" = '
                               "CAST(:a AS uuid)"), {"a": account_id}).scalar_one()
            if n:
                out[r.child] = n
    return out


class TestTheMemberPurgeTearsDownEachMailbox:

    async def test_the_purge_tears_down_each_mailbox_of_the_member(
        self, promoted, app_engine, recorder,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        _grant_check_to_app_role(p.admin_engine)
        tag = uuid.uuid4().hex[:8]
        keeper = f"keeper-{tag}@em-g11f1b.test"
        gone = f"Gone-{tag}@EM-G11f1b.test"
        stay = f"stay-{tag}@em-g11f1b.test"
        seed_member(p.admin_engine, p.org_b, keeper, owner=True)
        seed_member(p.admin_engine, p.org_b, gone)
        seed_member(p.admin_engine, p.org_b, stay)
        gmail = seed_mailbox(p.admin_engine, p.org_b, gone, provider="gmail",
                             filters=("flt-g1", "flt-g2"))
        outlook = seed_mailbox(p.admin_engine, p.org_b, gone, provider="microsoft",
                               filters=("rule-o1", "rule-o2"), sub="sub-g7b")
        kept = seed_mailbox(p.admin_engine, p.org_b, stay, provider="gmail",
                            filters=("flt-stay",))
        children = _children(p.admin_engine)
        try:
            assert _rows_of(p.admin_engine, children, gmail), "the seed put no child row"
            out = await member_purge(p, gone.lower())
            # Two purge rows of org B, keyed by the stored user_id, due now.
            rows = _admin_read(
                p.admin_engine,
                "SELECT organization_id, owner_email, account_id, state, "
                "next_try_at <= now() AS due FROM email_mailbox_purges "
                "WHERE account_id IN (CAST(:g AS uuid), CAST(:o AS uuid)) "
                "ORDER BY account_id", g=gmail, o=outlook)
            assert len(rows) == 2, "the purge did not write a row for each mailbox"
            assert {str(r.organization_id) for r in rows} == {p.org_b}
            assert {r.owner_email for r in rows} == {gone}, (
                "owner_email is not the user_id of the mailbox row")
            assert all(r.state == "open" and r.due for r in rows)
            # Each child of each mailbox is empty, and the mailboxes are gone.
            for acc in (gmail, outlook):
                assert _rows_of(p.admin_engine, children, acc) == {}
                assert recorder.gone(acc)
            assert out["deleted"]["email_accounts"] == 2
            assert out["mailboxes"] == {"planned": 2, "finished": 2, "continuing": 0}
            # Each leg after the commit, with the rule ids read under RLS.
            stops = sorted(e for e in recorder.events if e[0] == "stop")
            assert stops == sorted([("stop", gmail, True), ("stop", outlook, True)]), (
                "a loop stopped before the commit, or did not stop")
            filters = sorted((e[1], e[2]) for e in recorder.events
                             if e[0] == "filter_delete")
            assert filters == [("gmail", "flt-g1"), ("gmail", "flt-g2"),
                               ("microsoft", "rule-o1"), ("microsoft", "rule-o2")]
            assert ("graph_delete", "sub-g7b") in recorder.events
            assert [e for e in recorder.events if e[0] == "revoke"] == [
                ("revoke", TOKEN)], "the Gmail grant was not revoked once"
            # The other member keeps the mailbox, its rows and its filter.
            assert not recorder.gone(kept)
            assert _rows_of(p.admin_engine, children, kept).get("email_newsletters") == 1
            assert _admin_read(p.admin_engine, "SELECT 1 FROM email_mailbox_purges "
                               "WHERE account_id = CAST(:a AS uuid)", a=kept) == []
            assert "flt-stay" not in [e[2] for e in recorder.events
                                      if e[0] == "filter_delete"]
        finally:
            wipe(p.admin_engine, keeper, gone, stay)

    async def test_a_purge_that_rolls_back_leaves_no_purge_row(
        self, promoted, app_engine, recorder,  # noqa: F811
    ):
        """Acceptance 7. A trigger refuses the delete of the member row, the
        LAST statement of ``_PURGE_DELETES``. The purge rows were written
        before it, in the same transaction, so they roll back with it, and
        no leg of the teardown runs."""
        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        keeper = f"keeper-{tag}@em-g11f1b.test"
        gone = f"gone-{tag}@em-g11f1b.test"
        seed_member(p.admin_engine, p.org_b, keeper, owner=True)
        seed_member(p.admin_engine, p.org_b, gone)
        acc = seed_mailbox(p.admin_engine, p.org_b, gone, provider="gmail",
                           filters=("flt-1",))
        with p.admin_engine.begin() as c:
            c.execute(text(
                "CREATE OR REPLACE FUNCTION g7b_refuse_purge() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'purge refused'; "
                "END $$"))
            c.execute(text(
                "CREATE TRIGGER g7b_refuse_purge BEFORE DELETE ON app_user "
                "FOR EACH ROW EXECUTE FUNCTION g7b_refuse_purge()"))
        try:
            with pytest.raises(Exception, match="purge refused"):
                await member_purge(p, gone)
            assert _admin_read(p.admin_engine, "SELECT 1 FROM email_mailbox_purges "
                               "WHERE account_id = CAST(:a AS uuid)", a=acc) == [], (
                "a purge row outlived the rollback of its purge")
            assert not recorder.gone(acc), "the mailbox went without its purge"
            assert recorder.events == [], "a rolled-back purge reached a provider"
        finally:
            with p.admin_engine.begin() as c:
                c.execute(text("DROP TRIGGER IF EXISTS g7b_refuse_purge ON app_user"))
                c.execute(text("DROP FUNCTION IF EXISTS g7b_refuse_purge()"))
            wipe(p.admin_engine, keeper, gone)

    async def test_a_sync_after_the_commit_writes_no_child_row(
        self, promoted, app_engine, recorder,  # noqa: F811
    ):
        """Acceptance 10. The member purge stops the loop after its commit,
        so a sync can run between the two. The next cycle finds no row and
        answers ``gone``, and a write of a phase in flight fails on the
        foreign key. Neither leaves a child row."""
        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        keeper = f"keeper-{tag}@em-g11f1b.test"
        gone = f"gone-{tag}@em-g11f1b.test"
        seed_member(p.admin_engine, p.org_b, keeper, owner=True)
        seed_member(p.admin_engine, p.org_b, gone)
        acc = seed_mailbox(p.admin_engine, p.org_b, gone, provider="imap")
        children = _children(p.admin_engine)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            await member_purge(p, gone)
            token = bind_tenant(p.org_b)
            try:
                async with tenant_engine_scope(app_dsn):
                    result = await sched._sync_cycle(acc, organization_id=p.org_b)
                    assert result == sched.ACCOUNT_GONE
                    late = EmailMessage(
                        provider_message_id=f"pm-late-{tag}", thread_id=None,
                        folder="INBOX", subject="late",
                        from_address=EmailAddress("s", "s@sender.test"))
                    with pytest.raises(Exception, match=r"foreign key|violates"):
                        async with tenant_session(p.org_b) as db:
                            await upsert_message(db, acc, late)
            finally:
                release_tenant(token)
            assert _rows_of(p.admin_engine, children, acc) == {}, (
                "a sync after the commit wrote a child row")
        finally:
            wipe(p.admin_engine, keeper, gone)
