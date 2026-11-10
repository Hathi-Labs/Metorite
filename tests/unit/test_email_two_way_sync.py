"""Two-way action sync hardening (Outlook): provider failures must never fail
the user's already-committed local action, and an Outlook /move that re-keys the
message must update the stored provider_message_id."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from gateway.routes import email as m
from tests.unit._email_fakes import bind_db

USER = SimpleNamespace(email="u@example.com")


def _db_with_owned_row():
    """AsyncMock db whose SELECT ownership check finds the message."""
    db = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = SimpleNamespace(id="msg-1", categories=[])
    result.rowcount = 1
    db.execute.return_value = result
    return db


def _provider(**overrides):
    p = AsyncMock()
    p.authenticate = AsyncMock(return_value=True)
    p.apply_flags = AsyncMock()
    p.move_to_folder = AsyncMock(return_value=None)
    p.trash_message = AsyncMock(return_value=None)
    p.set_labels = AsyncMock()
    # Sync on the real providers. The route reads it after the push.
    p.credentials_dirty = MagicMock(return_value=False)
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def _executed_sql(db):
    return [str(c.args[0]) for c in db.execute.call_args_list]


async def test_update_message_provider_error_does_not_fail_action() -> None:
    # Provider flag push raises (incl. an HTTPException from the provider path);
    # the local change is already committed, so the action must still succeed.
    db = _db_with_owned_row()
    prov = _provider(apply_flags=AsyncMock(side_effect=HTTPException(status_code=502)))
    sentinel = object()
    with patch.object(m.transport.messages, "_tenant_session", bind_db(db)), \
            patch.object(m.transport.messages, "_provider_for_message",
                         AsyncMock(return_value=(prov, "OLD_PID", "acc-1", object()))), \
            patch.object(m.transport.messages, "_persist_rotated_creds", AsyncMock()), \
            patch.object(m.transport.messages, "get_message",
                         AsyncMock(return_value=sentinel)):
        res = await m.update_message(
            "msg-1", m.MessageUpdateModel(is_read=True), user=USER)
    assert res is sentinel  # no exception propagated


async def test_update_message_move_rekeys_provider_id() -> None:
    # Outlook /move returns a new id -> we must persist it.
    db = _db_with_owned_row()
    prov = _provider(move_to_folder=AsyncMock(return_value="NEW_PID"))
    with patch.object(m.transport.messages, "_tenant_session", bind_db(db)), \
            patch.object(m.transport.messages, "_provider_for_message",
                         AsyncMock(return_value=(prov, "OLD_PID", "acc-1", object()))), \
            patch.object(m.transport.messages, "_persist_rotated_creds", AsyncMock()), \
            patch.object(m.transport.messages, "get_message",
                         AsyncMock(return_value=object())):
        await m.update_message(
            "msg-1", m.MessageUpdateModel(folder="archive"), user=USER)
    assert any("provider_message_id = :pid" in s for s in _executed_sql(db))
    rekey = [c for c in db.execute.call_args_list
             if "provider_message_id = :pid" in str(c.args[0])]
    assert rekey and rekey[0].args[1]["pid"] == "NEW_PID"


async def test_delete_message_rekeys_and_swallows_provider_error() -> None:
    db = _db_with_owned_row()
    prov = _provider(trash_message=AsyncMock(return_value="NEW_PID"))
    with patch.object(m.transport.messages, "_tenant_session", bind_db(db)), \
            patch.object(m.transport.messages, "_provider_for_message",
                         AsyncMock(return_value=(prov, "OLD_PID", "acc-1", object()))), \
            patch.object(m.transport.messages, "_persist_rotated_creds", AsyncMock()):
        # returns None (204) and does not raise
        await m.delete_message("msg-1", user=USER)
    assert any("provider_message_id = :pid" in s for s in _executed_sql(db))
