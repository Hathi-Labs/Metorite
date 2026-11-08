"""The message answer says when the files of a mail could not load (task 1868).

An empty list used to mean two things: "the provider call failed" and "the
mail has only inline pictures". The reader cannot tell them apart, so the
answer carries ``attachments_status``. It is ``"failed"`` only when the
provider call for the files failed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from gateway.routes.email.transport import messages as m

from tests.unit.test_email_no_lock_across_provider import (
    MID,
    USER,
    _open_row,
    _wire,
)


def _row_with_body() -> SimpleNamespace:
    """A mail with a body (so no body fetch runs) that advertises files."""
    row = _open_row()
    row.body_text = "hello"
    row.has_attachments = True
    return row


async def test_a_failed_provider_call_answers_failed_with_no_files(monkeypatch) -> None:
    _db, provider, _open = _wire(monkeypatch, _row_with_body())

    async def _boom(_pmid: str) -> Any:
        raise RuntimeError("401 token expired")

    monkeypatch.setattr(provider, "get_message", _boom)
    msg = await m.get_message(MID, user=USER)
    assert msg.attachments == []
    assert msg.attachments_status == "failed"


async def test_a_failed_sign_in_answers_failed(monkeypatch) -> None:
    _db, provider, _open = _wire(monkeypatch, _row_with_body())

    async def _no() -> bool:
        return False

    monkeypatch.setattr(provider, "authenticate", _no)
    msg = await m.get_message(MID, user=USER)
    assert msg.attachments == []
    assert msg.attachments_status == "failed"


async def test_an_inline_only_mail_answers_ok_with_no_files(monkeypatch) -> None:
    _db, provider, _open = _wire(monkeypatch, _row_with_body())

    async def _inline_only(_pmid: str) -> Any:
        # The provider walked the parts and kept no real file.
        return SimpleNamespace(body_text="hello", body_html=None,
                               has_attachments=False, attachments=[])

    monkeypatch.setattr(provider, "get_message", _inline_only)
    msg = await m.get_message(MID, user=USER)
    assert msg.has_attachments is True
    assert msg.attachments == []
    assert msg.attachments_status == "ok"
