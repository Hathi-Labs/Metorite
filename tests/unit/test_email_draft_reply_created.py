"""WS-17 EM-G11c1b review round 1: ``created`` follows the PROVIDER draft.

``POST /email/draft-reply`` with ``create_draft=true`` makes a provider draft,
then mirrors it into ``email_messages``. Before this fix, ``created`` became
True only after the mirror. A failed mirror then answered ``created: False``
for a draft that the provider held. The chat card of ``draft_reply`` started
``idle`` (``draftCardStart``), and its Save made a SECOND provider draft.

Now ``created`` is True as soon as the provider answers, and the mirror runs
inside a savepoint, so its failure is logged and changes nothing else.

Hermetic: the session, the provider and the mirror are fakes. The mirror SQL
itself is unchanged, and ``test_email_drafts_keep_html.py`` runs it for real.

Mutation this file catches (R7): ``created = True`` moved back below the
mirror -> ``test_a_failed_mirror_still_answers_created``.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")

from gateway.routes.email.automation import drafting as drafting_mod

from tests.unit._email_fakes import bind_db


class _Db:
    async def execute(self, *_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [],
                               scalar=lambda: None, first=lambda: None)


def _wire(monkeypatch: pytest.MonkeyPatch, *, mirror: AsyncMock,
          create: AsyncMock) -> None:
    monkeypatch.setattr(drafting_mod, "_tenant_session", bind_db(_Db()))
    monkeypatch.setattr(drafting_mod, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(drafting_mod, "_build_reply_context", AsyncMock(return_value={
        "subject": "Quote", "body": "b", "thread_id": "t1",
        "from": "kim@contoso.test", "self": "jane@acme.test"}))
    monkeypatch.setattr(drafting_mod, "_load_assistant_about",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr(drafting_mod, "_agent_draft_reply",
                        AsyncMock(return_value="Hi Kim"))
    monkeypatch.setattr(drafting_mod, "_store_ai_draft", AsyncMock())
    monkeypatch.setattr(drafting_mod, "_upsert_local_draft", mirror)

    @asynccontextmanager
    async def _session(*_a: Any, **_k: Any):
        yield SimpleNamespace(
            authed=True, account_id="acc-1", provider_message_id="pm-orig",
            provider=SimpleNamespace(create_draft=create))

    monkeypatch.setattr(drafting_mod, "provider_session", _session)


async def _call() -> dict[str, Any]:
    return await drafting_mod.draft_reply_smart(
        drafting_mod.DraftReplyRequest(
            account_id="acc-1", message_id="m1", create_draft=True),
        SimpleNamespace(email="jane@acme.test"))


async def test_a_failed_mirror_still_answers_created(monkeypatch) -> None:
    create = AsyncMock(return_value="pm-draft-1")
    mirror = AsyncMock(side_effect=RuntimeError("mirror down"))
    _wire(monkeypatch, mirror=mirror, create=create)
    out = await _call()
    create.assert_awaited_once()
    mirror.assert_awaited_once()
    assert out["created"] is True, out
    assert out["draft"].startswith("Hi Kim")


async def test_a_good_mirror_answers_created(monkeypatch) -> None:
    create = AsyncMock(return_value="pm-draft-1")
    mirror = AsyncMock(return_value="local-1")
    _wire(monkeypatch, mirror=mirror, create=create)
    out = await _call()
    assert out["created"] is True, out


async def test_a_failed_provider_draft_answers_not_created(monkeypatch) -> None:
    create = AsyncMock(side_effect=RuntimeError("provider down"))
    mirror = AsyncMock(return_value="local-1")
    _wire(monkeypatch, mirror=mirror, create=create)
    out = await _call()
    mirror.assert_not_awaited()
    assert out["created"] is False, out
