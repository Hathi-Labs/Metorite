"""The email assistant's reads say when they cut, and how to read more.

Owner-approved fix, 2026-10-22. ``read_email`` cut a body at 4,000 characters
(12,000 with ``full=true``) and ``read_thread`` cut each message at 1,500,
with no word that text was missing. ``read_thread`` paged a long thread
OLDEST first, so the newest mail of a thread over 25 messages was out of
sight. ``read_email_attachment`` cut at 20,000 characters with no way on.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
MAIL_ID = "44444444-4444-4444-8444-444444444444"
PDF_ID = "55555555-5555-4555-8555-555555555555"


@pytest.fixture(scope="module")
def ea():
    path = REPO / "apps" / "agents" / "agent-email-assistant" / "agents.py"
    spec = importlib.util.spec_from_file_location("ea_read_cuts", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_get(monkeypatch, mod, answer) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def fake_get(path, params=None):
        seen.append((path, dict(params or {})))
        return answer(path, dict(params or {}))

    monkeypatch.setattr(mod, "_get", fake_get)
    return seen


# ── read_email ──────────────────────────────────────────────────────────────


def test_a_normal_read_marks_a_cut_body(monkeypatch, ea) -> None:
    """Mutation: cut ``body_text[:4000]`` with no marker (the old read).

    The marker line is then absent and this test fails.
    """
    body = "b" * 9000
    _fake_get(monkeypatch, ea, lambda p, q: {"id": MAIL_ID, "body_text": body})
    out = asyncio.run(ea.read_email(MAIL_ID))
    assert "[Cut at characters 0 to 4000 of 9000. Call read_email with full=true for more.]" in out
    assert "b" * 4001 not in out


def test_a_short_body_has_no_marker(monkeypatch, ea) -> None:
    """Mutation: write the marker on every read. This test then fails."""
    _fake_get(monkeypatch, ea, lambda p, q: {"id": MAIL_ID, "body_text": "short"})
    assert "[Cut at" not in asyncio.run(ea.read_email(MAIL_ID))


def test_a_full_read_pages_a_long_body(monkeypatch, ea) -> None:
    """``full=true`` shows 30,000 characters, and ``offset`` reads on.

    Mutation: cut the full body at 12,000 with no marker (the old read), or
    ignore ``offset``. The tail ``QZTAIL`` is then out of reach.
    """
    body = "x" * 45_000 + "QZTAIL"
    _fake_get(monkeypatch, ea, lambda p, q: {"subject": "S", "from": "f", "body_text": body})
    first = asyncio.run(ea.read_email(MAIL_ID, full=True))
    assert "x" * 30_000 in first and "QZTAIL" not in first
    assert ("[Cut at characters 0 to 30000 of 45006. Call read_email with full=true "
            "and offset=30000 for more.]") in first
    rest = asyncio.run(ea.read_email(MAIL_ID, full=True, offset=30_000))
    assert "QZTAIL" in rest
    assert "This is the end of the body." in rest
    past = asyncio.run(ea.read_email(MAIL_ID, full=True, offset=90_000))
    assert "Nothing at offset=90000. The body has 45006 characters." in past


# ── read_thread ─────────────────────────────────────────────────────────────


def _thread(n: int, body: str = "hello") -> list[dict[str, Any]]:
    return [{"id": f"m{k}", "subject": "s", "thread_id": "t1", "account_id": "a1",
             "from_address": {"email": f"x{k}@y.io"}, "received_at": f"2026-10-{k:02d}",
             "body_text": body}
            for k in range(1, n + 1)]


def _route(rows):
    def answer(path, params):
        size = int(params.get("page_size") or 50)
        page = int(params.get("page") or 1)
        return {"emails": rows[(page - 1) * size: page * size], "total": len(rows)}
    return answer


def test_a_long_thread_shows_the_newest_page_first(monkeypatch, ea) -> None:
    """A 30-message thread shows messages 6 to 30, oldest first in the page.

    Mutation: page from the oldest end (the old read). Message 30, the
    newest mail, is then missing from the first page.
    """
    seen = _fake_get(monkeypatch, ea, _route(_thread(30)))
    out = asyncio.run(ea.read_thread(thread_id="t1"))
    assert out.startswith("Thread: 30 message(s), oldest first:")
    assert "x30@y.io" in out and "x6@y.io" in out and "x5@y.io" not in out
    assert out.index("[6]") < out.index("[30]"), "oldest first within the page"
    assert [q["page"] for _p, q in seen] == ["1", "2"]
    assert ("Showing messages 6-30 of 30. Call again with offset=25 for the 5 "
            "messages before these.") in out
    older = asyncio.run(ea.read_thread(thread_id="t1", offset=25))
    assert "x1@y.io" in older and "x5@y.io" in older and "x6@y.io" not in older
    assert "The 25 newer messages are at offset=0." in older
    assert "These are the oldest messages." in older


def test_a_short_thread_takes_one_request_and_no_paging_line(monkeypatch, ea) -> None:
    """Mutation: always fetch a second page. The request count then fails."""
    seen = _fake_get(monkeypatch, ea, _route(_thread(3)))
    out = asyncio.run(ea.read_thread(thread_id="t1"))
    assert len(seen) == 1
    assert "Showing" not in out and "[1]" in out and "[3]" in out


def test_an_offset_past_the_thread_says_where_it_ends(monkeypatch, ea) -> None:
    _fake_get(monkeypatch, ea, _route(_thread(30)))
    out = asyncio.run(ea.read_thread(thread_id="t1", offset=40))
    assert out == ("Nothing at offset=40. The thread has 30 messages, "
                   "so start below offset=30.")


def test_a_cut_message_in_a_thread_says_how_to_read_it(monkeypatch, ea) -> None:
    """Mutation: cut ``body[:1500]`` with no marker (the old read)."""
    _fake_get(monkeypatch, ea, _route(_thread(2, body="w" * 4000)))
    out = asyncio.run(ea.read_thread(thread_id="t1"))
    assert ("[Cut at characters 0 to 1500 of 4000. Call read_email on m2 with "
            "full=true for more.]") in out
    assert "id=" not in out, "the chat cards read id= as the id of a mail"


# ── read_email_attachment ───────────────────────────────────────────────────


class _Gateway:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return {"attachments": [{"id": PDF_ID, "filename": "Q.pdf"}]}

    async def request(self, method: str, path: str, **kw: Any) -> Any:
        self.requests.append((path, dict(kw.get("params") or {})))
        return SimpleNamespace(json=lambda: self.answer)


def test_the_attachment_marker_names_the_next_offset(monkeypatch, ea) -> None:
    """Mutation: drop the ``next_offset`` branch of ``_frame_attachment_text``.

    The answer then says only that the read stopped, with no way on.
    """
    gw = _Gateway({"filename": "Q.pdf", "kind": "pdf", "text": "t" * 20,
                   "truncated": True, "chars": 50_000, "offset": 0,
                   "next_offset": 20_000})
    monkeypatch.setattr(ea, "_get", gw.get)
    monkeypatch.setattr(ea, "_request", gw.request)
    out = asyncio.run(ea.read_email_attachment(MAIL_ID, PDF_ID))
    assert ("[Cut at characters 0 to 20000 of 50000. Call read_email_attachment "
            "with offset=20000 for the next part.]") in out
    assert gw.requests == [(f"/email/attachments/{PDF_ID}/text", {})]


def test_the_attachment_offset_reaches_the_route(monkeypatch, ea) -> None:
    """Mutation: drop the ``offset`` query parameter. The route then reads
    the first part again."""
    gw = _Gateway({"filename": "Q.pdf", "kind": "pdf", "text": "end",
                   "truncated": False, "chars": 20_003, "offset": 20_000,
                   "next_offset": None})
    monkeypatch.setattr(ea, "_get", gw.get)
    monkeypatch.setattr(ea, "_request", gw.request)
    out = asyncio.run(ea.read_email_attachment(MAIL_ID, PDF_ID, offset=20_000))
    assert gw.requests == [(f"/email/attachments/{PDF_ID}/text", {"offset": "20000"})]
    assert "[Cut at" not in out and "stopped at a limit" not in out
