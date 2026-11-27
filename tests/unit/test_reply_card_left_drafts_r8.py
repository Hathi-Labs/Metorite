"""The SQL of a reply card whose reply left, on a real database (R8, FORCE RLS).

Owner report, 2026-11-03 (task 1526, ``email_app_master_plan.md`` §18.9).
This file runs the two reads of the guard as a non-privileged role, on a
phase-4-promoted catalog, in the world of ``test_email_contact_suggest_r8``:

* ``reply_card._DRAFT_STATE_SQL`` through ``draft_in_drafts`` and the route
  ``GET /email/reply-card/draft-state``. Member A sees the draft. Member B
  of the same organization, and organization A, see nothing. A draft that
  ``send_draft_endpoint`` deleted, or that a delete moved to Trash, is not in
  Drafts.
* ``reply_card._reply_left``, the guard of the act, with ``RECENT_REPLY_SQL``
  of the thread and the time of the card.

The hermetic half is ``test_reply_card_left_drafts.py``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_reply_card_left_drafts_r8.py -v -rs
"""
from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.automation import reply_card as rc
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_contact_suggest_r8 import _assert_non_priv, _World

# ``promoted`` and ``app_engine`` are used by name for fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


def _row(c: Any, w: _World, *, folder: str, frm: str, thread: str, when: datetime,
         tag: str) -> str:
    return str(c.execute(text(
        "INSERT INTO email_messages (account_id, provider_message_id, folder, thread_id, "
        "from_address, to_addresses, subject, received_at, organization_id) VALUES "
        "(CAST(:a AS uuid), :pm, :f, :t, CAST(:fr AS jsonb), CAST(:to AS jsonb), 'Re: s', "
        ":r, CAST(:o AS uuid)) RETURNING id"),
        {"a": w.account, "pm": f"pm-{tag}-{w.tag}", "f": folder, "t": thread,
         "fr": json.dumps({"email": frm}), "to": json.dumps([{"email": "kim@c.test"}]),
         "r": when, "o": w.p.org_b}).scalar_one())


async def _in(w: _World, org: str, fn: Any) -> Any:
    """Run *fn* with *org* bound, on the app role (NOBYPASSRLS)."""
    app_dsn = w.p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(app_dsn):
            return await fn()
    finally:
        release_tenant(token)


async def _state(w: _World, org: str, member: str, draft_id: str) -> bool:
    async def read() -> bool:
        async with rc._tenant_session() as db:
            return await rc.draft_in_drafts(db, member, draft_id)

    return await _in(w, org, read)


async def test_the_draft_state_is_the_members_own_and_this_tenants(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _World(promoted)
    thread = f"th-left-{w.tag}"
    now = datetime.now(UTC)
    try:
        w.seed()
        with w.p.admin_engine.begin() as c:
            draft = _row(c, w, folder="drafts", frm=w.owner, thread=thread, when=now,
                         tag="draft")
            trashed = _row(c, w, folder="trash", frm=w.owner, thread=thread, when=now,
                           tag="trash")
        org_a, org_b = w.p.org_a, w.p.org_b
        assert await _state(w, org_b, w.owner, draft) is True
        # Member B of the same organization: the mailbox seam keeps it out.
        assert await _state(w, org_b, w.kiran, draft) is False
        # Organization A with the same member email: FORCE RLS keeps it out.
        assert await _state(w, org_a, w.owner, draft) is False
        # A delete in the Email app moves a draft to Trash. It is not in Drafts.
        assert await _state(w, org_b, w.owner, trashed) is False

        # The route answers the session's own member and tenant only.
        me = UserContext(email=w.owner, role=UserRole.EMPLOYEE, organization_id=org_b)
        other = UserContext(email=w.kiran, role=UserRole.EMPLOYEE, organization_id=org_b)
        out = await _in(w, org_b, lambda: rc.reply_card_draft_state(draft, me))
        assert out == {"draft_id": draft, "in_drafts": True}
        out = await _in(w, org_b, lambda: rc.reply_card_draft_state(draft, other))
        assert out["in_drafts"] is False

        # ``send_draft_endpoint`` deletes the local row of a sent draft.
        with w.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_messages WHERE id = CAST(:i AS uuid)"),
                      {"i": draft})
        assert await _state(w, org_b, w.owner, draft) is False
    finally:
        w.purge()


async def test_the_guard_of_the_act_on_a_real_database(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _World(promoted)
    thread = f"th-guard-{w.tag}"
    now = datetime.now(UTC)
    try:
        w.seed()
        with w.p.admin_engine.begin() as c:
            draft = _row(c, w, folder="drafts", frm=w.owner, thread=thread, when=now,
                         tag="draft")
            # A reply of the thread an hour before the card: it does not block.
            _row(c, w, folder="sent", frm=w.owner, thread=thread,
                 when=now - timedelta(hours=1), tag="before")
        mail = types.SimpleNamespace(account_id=w.account, thread_id=thread)
        org_b = w.p.org_b
        made = now.timestamp() - 60
        card = rc._CardRecord([], [], draft, made)
        assert await _in(w, org_b, lambda: rc._reply_left(mail, w.owner, card)) is None

        # Member B holds no such draft: the guard reads it as gone.
        left = await _in(w, org_b, lambda: rc._reply_left(mail, w.kiran, card))
        assert left == (rc.DRAFT_GONE, rc.REPLY_LEFT_DRAFTS)

        # The member sent a reply in the thread after the card.
        with w.p.admin_engine.begin() as c:
            _row(c, w, folder="sent", frm=w.owner, thread=thread, when=now, tag="after")
        no_draft = rc._CardRecord([], [], "", made)
        left = await _in(w, org_b, lambda: rc._reply_left(mail, w.owner, no_draft))
        assert left == (rc.ALREADY_REPLIED, rc.REPLY_ALREADY_SENT)
        # A card made after that reply is not blocked by it.
        later = rc._CardRecord([], [], draft, now.timestamp() + 60)
        assert await _in(w, org_b, lambda: rc._reply_left(mail, w.owner, later)) is None

        # The draft left Drafts: the draft check answers first.
        with w.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM email_messages WHERE id = CAST(:i AS uuid)"),
                      {"i": draft})
        left = await _in(w, org_b, lambda: rc._reply_left(mail, w.owner, later))
        assert left == (rc.DRAFT_GONE, rc.REPLY_LEFT_DRAFTS)
    finally:
        w.purge()
