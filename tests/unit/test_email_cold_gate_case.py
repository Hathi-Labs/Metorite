"""The cold gate compares addresses without case (EM-T5b-1 fix round 1).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.8, the note on
the one live path that EM-T5b-1 changes.

``_maybe_block_cold`` asks the model only about a sender that the owner has
never written to. It lowercased the sender and then tested
``to_addresses @> [{"email": <lower>}]``. JSONB containment is
case-sensitive, and Outlook keeps the case of an address. So a contact
stored as ``Jo.Smith@Acme.com`` read as never contacted, the model was asked,
and the ``decide`` state said ``prior_contact: false`` as a fact. The gate
now runs ``_PRIOR_CONTACT_SQL``, which compares lowercased addresses.

R8: the SQL runs on a real Postgres, as the non-owner role under FORCE RLS
(the phase-4 ``promoted`` two-org catalog). The plan is checked: the account
and the sent folder bound the scan before any array is opened.

Run (real Postgres for the R8 class)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_cold_gate_case.py -v -rs
"""
from __future__ import annotations

import inspect
import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email import core as email_core
from gateway.routes.email.automation import senders as snd
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _seed_account

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

#: The old statement, kept here as the evidence of the defect.
_OLD_SQL = """SELECT 1 FROM email_messages
           WHERE account_id = :aid AND LOWER(folder) = 'sent'
             AND to_addresses @> :tojson LIMIT 1"""


# ── Hermetic: the gate uses the one helper ─────────────────────────────────


def test_the_gate_reads_prior_contact_through_the_helper() -> None:
    src = inspect.getsource(snd._maybe_block_cold)
    assert "_has_prior_contact(" in src
    assert "@>" not in src, "the case-sensitive containment test is back"
    assert "LOWER(t.addr->>'email') = :e" in snd._PRIOR_CONTACT_SQL


async def test_a_prior_contact_never_reaches_the_model(monkeypatch) -> None:
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=None))
    monkeypatch.setattr(snd, "_has_prior_contact", AsyncMock(return_value=True))
    model = AsyncMock(return_value=(True, "r"))
    monkeypatch.setattr(snd, "_llm_is_cold", model)
    await snd._maybe_block_cold(db, object(), "acc", "m1", "pm1",
                                {"from": "Jo.Smith@Acme.example"}, "LABEL")
    model.assert_not_awaited()


async def test_the_helper_lowercases_the_sender() -> None:
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=None))
    await snd._has_prior_contact(db, "acc", "  Jo.Smith@Acme.EXAMPLE ")
    params = db.execute.await_args.args[1]
    assert params == {"aid": "acc", "e": "jo.smith@acme.example"}


# ── R8: the SQL on a real database ─────────────────────────────────────────


@contextmanager
def _bound(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _seed_message(admin, *, org: str, account_id: str, folder: str,
                  to_addresses: str) -> None:
    """Seed as the superuser. ``to_addresses`` is raw JSON text, so a test
    can seed a value that is not an array."""
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, body_text, "
            "snippet, received_at, organization_id) "
            "VALUES (CAST(:a AS uuid), :pm, :tid, :f, CAST(:frm AS jsonb), "
            "CAST(:to AS jsonb), 'hello', 'body', 'body', :rcv, CAST(:o AS uuid))"),
            {"a": account_id, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "tid": f"t-{uuid.uuid4().hex[:8]}", "f": folder,
             "frm": json.dumps({"email": "owner@cold-gate.test", "name": "O"}),
             "to": to_addresses, "rcv": datetime.now(UTC), "o": org})


async def _ask(p, *, org: str, account_id: str, sender: str) -> bool:
    app_dsn = p.app_url.render_as_string(hide_password=False)
    async with tenant_engine_scope(app_dsn):
        with _bound(org):
            async with email_core._tenant_session() as db:
                return await snd._has_prior_contact(db, account_id, sender)


async def _old_answer(p, *, org: str, account_id: str, sender: str) -> bool:
    app_dsn = p.app_url.render_as_string(hide_password=False)
    async with tenant_engine_scope(app_dsn):
        with _bound(org):
            async with email_core._tenant_session() as db:
                row = (await db.execute(text(_OLD_SQL), {
                    "aid": account_id,
                    "tojson": json.dumps([{"email": sender.lower()}])})).fetchone()
                return row is not None


def _tag() -> str:
    return uuid.uuid4().hex[:8]


@_DB_GATE
class TestThePriorContactReadUnderForceRls:

    async def test_a_mixed_case_recipient_is_a_prior_contact(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        t = _tag()
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=f"b-{t}@cold-gate.test")
        stored = f"Jo.Smith-{t}@Acme-Cold.example"
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                      to_addresses=json.dumps([{"name": "Jo", "email": stored}]))
        for asked in (stored.lower(), stored, stored.upper()):
            assert await _ask(p, org=p.org_b, account_id=acc, sender=asked), asked
        assert not await _ask(p, org=p.org_b, account_id=acc,
                              sender=f"someone-else-{t}@acme-cold.example")

    async def test_the_gate_asks_no_model_about_a_mixed_case_contact(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """End to end through `_maybe_block_cold`: the stored recipient has
        capitals, the sender arrives in lower case, and the model is not
        asked. A sender with no prior contact still reaches the model."""
        p = promoted
        t = _tag()
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=f"b-{t}@cold-gate.test")
        stored = f"Jo.Smith-{t}@Acme-Cold.example"
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                      to_addresses=json.dumps([{"email": stored}]))
        model = AsyncMock(return_value=(False, ""))
        monkeypatch.setattr(snd, "_llm_is_cold", model)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                async with email_core._tenant_session() as db:
                    await snd._maybe_block_cold(
                        db, object(), acc, str(uuid.uuid4()), "pm",
                        {"from": stored.lower()}, "LABEL")
                    model.assert_not_awaited()
                    await snd._maybe_block_cold(
                        db, object(), acc, str(uuid.uuid4()), "pm",
                        {"from": f"stranger-{t}@acme-cold.example"}, "LABEL")
        model.assert_awaited_once()

    async def test_the_old_containment_missed_the_same_contact(
        self, promoted, app_engine,  # noqa: F811
    ):
        """The defect, shown on the same data: `@>` finds no row."""
        p = promoted
        t = _tag()
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=f"b-{t}@cold-gate.test")
        stored = f"Jo.Smith-{t}@Acme-Cold.example"
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="sent",
                      to_addresses=json.dumps([{"email": stored}]))
        assert not await _old_answer(p, org=p.org_b, account_id=acc, sender=stored)
        assert await _ask(p, org=p.org_b, account_id=acc, sender=stored)

    async def test_only_sent_mail_counts(self, promoted, app_engine):  # noqa: F811
        p = promoted
        t = _tag()
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=f"b-{t}@cold-gate.test")
        address = f"inbox-only-{t}@cold-gate.example"
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="inbox",
                      to_addresses=json.dumps([{"email": address}]))
        assert not await _ask(p, org=p.org_b, account_id=acc, sender=address)
        _seed_message(p.admin_engine, org=p.org_b, account_id=acc, folder="Sent",
                      to_addresses=json.dumps([{"email": address.upper()}]))
        assert await _ask(p, org=p.org_b, account_id=acc, sender=address)

    async def test_a_value_that_is_not_an_array_raises_nothing(
        self, promoted, app_engine,  # noqa: F811
    ):
        """`jsonb_array_elements` raises on an object or a scalar, and that
        would abort the session of the runner. The CASE guard stops it."""
        p = promoted
        t = _tag()
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=f"b-{t}@cold-gate.test")
        address = f"odd-{t}@cold-gate.example"
        for odd in (json.dumps({"email": address}), json.dumps(address), "null"):
            _seed_message(p.admin_engine, org=p.org_b, account_id=acc,
                          folder="sent", to_addresses=odd)
        assert not await _ask(p, org=p.org_b, account_id=acc, sender=address)

    async def test_the_other_organization_is_invisible(
        self, promoted, app_engine,  # noqa: F811
    ):
        p = promoted
        t = _tag()
        acc_a = _seed_account(p.admin_engine, org=p.org_a, owner=f"a-{t}@cold-gate.test")
        address = f"Shared-{t}@Cold-Gate.example"
        _seed_message(p.admin_engine, org=p.org_a, account_id=acc_a, folder="sent",
                      to_addresses=json.dumps([{"email": address}]))
        assert await _ask(p, org=p.org_a, account_id=acc_a, sender=address)
        assert not await _ask(p, org=p.org_b, account_id=acc_a, sender=address), (
            "org B read the sent mail of org A"
        )

    async def test_the_plan_bounds_the_scan_before_the_array(
        self, promoted, app_engine,  # noqa: F811
    ):
        """The account and the folder filter the messages first. The array
        of each message that passes is opened in the inner loop."""
        p = promoted
        t = _tag()
        acc = _seed_account(p.admin_engine, org=p.org_b, owner=f"b-{t}@cold-gate.test")
        app_dsn = p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(app_dsn):
            with _bound(p.org_b):
                async with email_core._tenant_session() as db:
                    rows = (await db.execute(
                        text("EXPLAIN " + snd._PRIOR_CONTACT_SQL),
                        {"aid": acc, "e": f"x-{t}@cold-gate.example"})).fetchall()
        plan = "\n".join(r[0] for r in rows)
        print(plan)
        scan = plan.find("on email_messages")
        unnest = plan.find("Function Scan on jsonb_array_elements")
        assert scan != -1 and unnest != -1, plan
        assert "Nested Loop" in plan, plan
        assert scan < unnest, "the array is opened before the messages are bound"
        outer = plan[scan:unnest]
        assert "account_id" in outer and "folder" in outer, plan
