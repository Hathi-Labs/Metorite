"""The files rule of a held send (``email_app_master_plan.md`` §21.1 item 3).

A file row resolves only from data of the member's own mailbox: the model
names ``attachment:<id>``, and the server reads the file. A workspace path and
a Metorite file are refused in a held chat. This is the hermetic half. The R8
half, ``test_held_send_files_r8.py``, refuses a file of another member and of
another tenant on a real database.

R7, each with the mutation that turns it red:

* ``test_a_workspace_path_is_refused`` — accept a path in ``parse_file_ref``.
* ``test_a_metorite_file_is_refused`` — accept ``file:<id>``.
* ``test_a_file_that_the_query_does_not_find_sends_nothing`` — drop the
  missing check of ``resolve_file_refs``.
* ``test_only_a_ticked_recorded_file_goes`` — read a file id from anywhere
  but the record.
* ``test_the_bytes_come_through_the_one_owned_fetch`` — read a file outside
  ``_fetch_owned_attachment``.
"""
from __future__ import annotations

import types
from typing import Any

import pytest
from acb_skills import held_card as H
from fastapi import HTTPException
from gateway.routes.email.automation import held_card as hc

from tests.unit import _held_card_fakes as F

FILE_A = "0f0f0f0f-0000-4000-8000-00000000000f"
FILE_B = "0e0e0e0e-0000-4000-8000-00000000000e"


@pytest.mark.parametrize("ref", ["outputs/quote.pdf", "inputs/x/../../.env", "/etc/passwd",
                                 "email-assistant:outputs/a.pdf", "attachment:not-a-uuid", ""])
def test_a_workspace_path_is_refused(ref: str) -> None:
    with pytest.raises(ValueError) as e:
        H.parse_file_ref(ref)
    assert str(e.value) == H.WORKSPACE_FILE_REFUSED


def test_a_metorite_file_is_refused() -> None:
    with pytest.raises(ValueError) as e:
        H.parse_file_ref("file:1234")
    assert str(e.value) == H.METORITE_FILE_REFUSED


def test_an_attachment_ref_reads_as_its_canonical_id() -> None:
    assert H.parse_file_ref(f"attachment:{FILE_A.upper()}") == ("attachment", FILE_A)


class _Db:
    def __init__(self, owned: list[str]) -> None:
        self.owned = owned
        self.queries: list[dict[str, Any]] = []

    async def execute(self, sql: Any, params: dict[str, Any]) -> Any:
        assert str(sql) == hc.OWNED_FILES_SQL
        self.queries.append(params)
        rows = [types.SimpleNamespace(id=i, filename="quote.pdf", size_bytes=2048,
                                      message_id="m", subject="Quote")
                for i in params["ids"] if i in self.owned]
        return types.SimpleNamespace(fetchall=lambda: rows)


async def test_a_refused_ref_asks_no_query() -> None:
    db = _Db([FILE_A])
    with pytest.raises(HTTPException) as e:
        await hc.resolve_file_refs(db, F.MEMBER, F.BOX, ["outputs/x.pdf"])
    assert e.value.status_code == 422 and db.queries == []


async def test_a_file_that_the_query_does_not_find_sends_nothing() -> None:
    """The owner join found one of the two: the whole send is refused."""
    db = _Db([FILE_A])
    with pytest.raises(HTTPException) as e:
        await hc.resolve_file_refs(db, F.MEMBER, F.BOX,
                                   [f"attachment:{FILE_A}", f"attachment:{FILE_B}"])
    assert e.value.status_code == 404
    assert db.queries[0]["uid"] == F.MEMBER and db.queries[0]["aid"] == F.BOX


async def test_a_found_file_is_a_ticked_row_with_its_mail() -> None:
    db = _Db([FILE_A])
    files = await hc.resolve_file_refs(db, F.MEMBER, F.BOX, [f"attachment:{FILE_A}"] * 2)
    rows = hc.file_rows(files)
    assert len(rows) == 1
    assert rows[0] == {"id": "file-0", "kind": "file", "attachment_id": FILE_A,
                       "label": 'File: "quote.pdf" (2 KB)',
                       "hint": 'From the mail "Quote" of your own mailbox.', "checked": True}


def test_too_many_bytes_send_nothing() -> None:
    big = [types.SimpleNamespace(id=FILE_A, filename="a", size_bytes=hc.MAX_FILES_BYTES,
                                 subject="s"),
           types.SimpleNamespace(id=FILE_B, filename="b", size_bytes=1, subject="s")]
    with pytest.raises(HTTPException) as e:
        hc.file_rows(big)
    assert e.value.status_code == 422


def test_only_a_ticked_recorded_file_goes() -> None:
    rows = [
        {"id": "to-0", "kind": "to", "address": F.KNOWN},
        {"id": "file-0", "kind": "file", "attachment_id": FILE_A},
        {"id": "file-1", "kind": "file", "attachment_id": FILE_B},
    ]
    assert hc.ticked_file_ids(rows, frozenset({"to-0", "file-1"})) == [FILE_B]
    assert hc.ticked_file_ids(rows, frozenset({"to-0"})) == []


async def test_the_bytes_come_through_the_one_owned_fetch(monkeypatch) -> None:
    import contextlib

    from gateway.routes.email.transport import attachments as att

    fetched: list[str] = []

    async def owned(db: Any, aid: str, user: Any, *, max_bytes: int | None = None) -> Any:
        fetched.append(aid)
        assert max_bytes == hc.MAX_FILES_BYTES
        return types.SimpleNamespace(
            row=types.SimpleNamespace(filename="quote.pdf", mime_type="application/pdf"),
            content=b"%PDF")

    @contextlib.asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr(att, "_fetch_owned_attachment", owned)
    monkeypatch.setattr(hc, "_tenant_session", session)
    out = await hc.file_payloads(F.user(), [FILE_A])
    assert fetched == [FILE_A]
    assert out == [{"filename": "quote.pdf", "mime_type": "application/pdf",
                    "content_b64": "JVBERg=="}]
