"""Task 3978 (R8): the live check of ``DELETE /email/chat-drafts/{id}`` on a real
database (``email_app_master_plan.md`` §18.13.1, fix round 3).

The SQL of block A (``drafting._STORED_DRAFT_SQL`` with ``mailbox_ids_sql``)
runs as the app role, with RLS. The provider is a fake.

1. The member's own draft passes, and the trash runs.
2. Another member's draft of the same org reads as no draft (404), and
   nothing is read at the provider.
3. A stored draft whose live copy differs answers 409, and nothing is trashed.

The SQL of block A and ``_provider_for_message`` each carry the owner
predicate, so ``test_another_members_draft_is_no_draft`` fails only when both
are gone. ``test_the_live_check_route`` fences the SQL of block A alone.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``world``, ``promoted`` and ``app_engine`` are used by name for fixture
# injection, so the imports are load-bearing even though they read as unused.
from tests.unit.test_email_html_hot_only import (  # noqa: F401
    _bound,
    _user,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


def _seed_draft(w: Any) -> str:
    with w.admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
            "folder, from_address, to_addresses, cc_addresses, subject, body_text, "
            "received_at, organization_id) VALUES (CAST(:a AS uuid), :pm, :t, 'drafts', "
            "CAST(:f AS jsonb), CAST(:to AS jsonb), '[]'::jsonb, 'Re: Proposal', "
            "'Dear Salil,', :r, CAST(:o AS uuid)) RETURNING id"),
            {"a": w.account, "pm": f"pm-{uuid.uuid4().hex[:12]}",
             "t": f"t-{uuid.uuid4().hex[:8]}",
             "f": json.dumps({"email": w.owner, "name": ""}),
             "to": json.dumps([{"email": "salil@client.test", "name": ""}]),
             "r": datetime.now(UTC), "o": w.org}).scalar_one())


class _Live:
    def __init__(self, body: str) -> None:
        self.body = body
        self.reads = 0

    async def authenticate(self) -> bool:
        return True

    async def get_message(self, _pmid: str) -> Any:
        self.reads += 1
        return SimpleNamespace(
            body_text=self.body, body_html="", subject="Re: Proposal",
            to_addresses=[SimpleNamespace(email="salil@client.test")],
            cc_addresses=[], bcc_addresses=[], has_attachments=False)

    def credentials_dirty(self) -> bool:
        return False


def _wire(monkeypatch: pytest.MonkeyPatch, body: str) -> tuple[_Live, list[str]]:
    from gateway.routes.email import core
    from gateway.routes.email.transport import messages

    live = _Live(body)
    monkeypatch.setattr(core, "_instantiate_provider", lambda _n, _c: live)
    trashed: list[str] = []

    async def delete(mid: str, _user: Any, report: bool = False) -> Any:
        trashed.append(mid)
        return {"id": mid, "provider": "confirmed"}

    monkeypatch.setattr(messages, "delete_message", delete)
    return live, trashed


async def _call(w: Any, draft_id: str, member: str) -> Any:
    from gateway.routes.email.automation import drafting as dr

    async with tenant_engine_scope(w.dsn):
        with _bound(w.org):
            return await asyncio.wait_for(
                dr.trash_chat_draft_live(draft_id, _user(member, w.org)), timeout=20)


async def test_the_members_own_draft_passes(world, monkeypatch) -> None:  # noqa: F811
    did = _seed_draft(world)
    live, trashed = _wire(monkeypatch, "Dear Salil,")
    assert (await _call(world, did, world.owner))["provider"] == "confirmed"
    assert trashed == [did] and live.reads == 1


async def test_another_members_draft_is_no_draft(world, monkeypatch) -> None:  # noqa: F811
    did = _seed_draft(world)
    live, trashed = _wire(monkeypatch, "Dear Salil,")
    with pytest.raises(HTTPException) as exc:
        await _call(world, did, f"other-{uuid.uuid4().hex[:6]}@em-s3.test")
    assert exc.value.status_code == 404
    assert trashed == [] and live.reads == 0


async def test_a_draft_edited_at_the_mailbox_stays(world, monkeypatch) -> None:  # noqa: F811
    did = _seed_draft(world)
    _live, trashed = _wire(monkeypatch, "Dear Salil, I changed this in Outlook.")
    with pytest.raises(HTTPException) as exc:
        await _call(world, did, world.owner)
    assert exc.value.status_code == 409 and trashed == []
