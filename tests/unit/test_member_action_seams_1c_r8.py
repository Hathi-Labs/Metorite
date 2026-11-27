"""WS-PZ MM-1c — the feature reads, on a real database (R8).

Code: ``routes/email/act_features.py`` over ``email_messages``. Hermetic
twin: ``test_member_action_seams_1c.py``. Run on a PRIVATE ladder database as
``test_member_action_r8.py`` says.

a. ``of_message`` and ``of_thread`` read the sender domain of the right mail
   (the newest INBOUND mail of a thread), and the casts match the uuid columns.
b. Org B reads nothing of org A's mail (RLS).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from acb_common.db import tenant_session
from gateway.routes.email import act_features
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@mm1c.test"
_LIKE = f"%.{TAG}@mm1c.test"
ACC, M_IN, M_OLD, M_SENT = (str(uuid.uuid4()) for _ in range(4))
THREAD = f"thread-{TAG}"


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "member_actions_orgs", "*", raising=False)
    a = promoted.org_a
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_accounts (id, organization_id, user_id, provider, "
            "email_address, credentials_encrypted) VALUES (CAST(:i AS uuid), "
            "CAST(:o AS uuid), :u, 'imap', :e, 'x')"),
            {"i": ACC, "o": a, "u": ANA, "e": f"mb.{TAG}@mm1c.test"})
        for mid, folder, frm, age in ((M_IN, "INBOX", "Bob@GE.com", 1),
                                      (M_OLD, "INBOX", "old@old.com", 5),
                                      (M_SENT, "Sent", ANA, 0)):
            c.execute(text(
                "INSERT INTO email_messages (id, organization_id, account_id, "
                "provider_message_id, thread_id, folder, from_address, to_addresses, "
                "received_at) VALUES (CAST(:i AS uuid), CAST(:o AS uuid), CAST(:a AS uuid), "
                ":p, :t, :f, CAST(:fr AS jsonb), '[]', now() - make_interval(days => :d))"),
                {"i": mid, "o": a, "a": ACC, "p": f"p-{mid[:6]}", "t": THREAD, "f": folder,
                 "fr": json.dumps({"email": frm, "name": "x"}), "d": age})
    yield SimpleNamespace(p=promoted)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM member_action WHERE member_email LIKE :t"), {"t": _LIKE})
        c.execute(text("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
                  {"a": ACC})
        c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"), {"a": ACC})


def _go(world: Any, org: str, fn: Any) -> Any:
    async def go() -> Any:
        dsn = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn), tenant_session(org) as db:
            return await fn(db)
    return asyncio.run(go())


def test_r8_the_sender_domain_of_a_mail_and_of_a_thread(world) -> None:
    a = world.p.org_a
    assert _go(world, a, lambda db: act_features.of_message(db, a, M_IN)) == {
        "sender_domain": "ge.com"}
    # The newest inbound mail of the thread, never the member's own sent mail.
    assert _go(world, a, lambda db: act_features.of_thread(db, a, ACC, THREAD)) == {
        "sender_domain": "ge.com"}
    assert _go(world, a, lambda db: act_features.of_message(db, a, str(uuid.uuid4()))) == {}


def test_r8_org_b_reads_nothing_of_org_a(world) -> None:
    b = world.p.org_b
    assert _go(world, b, lambda db: act_features.of_message(db, b, M_IN)) == {}
    assert _go(world, b, lambda db: act_features.of_thread(db, b, ACC, THREAD)) == {}
