"""The card record (WS-CO CO-17, task 3516), the hermetic fences.

Spec: ``project-docs/specs/chat_organization.md`` §16.3. The SQL and RLS are
proved on a real database in ``test_card_record_r8.py`` (R8). Here: the pure
rules, the dark switch, the wiring of the card gate, and the migration text.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from acb_common import get_settings
from acb_skills import ask_tools
from orchestrator import card_record as cr

_ROOT = Path(__file__).resolve().parents[2]
_SQL_MARK = "CREATE TABLE IF NOT EXISTS chat_card_record ("


def _migration_text() -> str:
    hits = [p.read_text(encoding="utf-8")
            for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _SQL_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1
    return hits[0]


def test_the_switch_is_off_by_default_and_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "", raising=False)
    assert not cr.record_allowed("acme")
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "ACME, other", raising=False)
    assert cr.record_allowed("acme") and not cr.record_allowed("globex")
    assert not cr.record_allowed(None) and not cr.record_allowed("")
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "*", raising=False)
    assert cr.record_allowed("globex")


def test_the_act_kind_is_the_tools_own_name_never_text() -> None:
    def create_task() -> None: ...

    create_task.__module__ = "skill_projects.Client"
    assert cr.act_kind_of(create_task) == "client.create_task"
    assert cr.act_kind_of(None) == "card"
    anon = type("T", (), {"__module__": "m", "__name__": "Send it now!"})()
    assert re.fullmatch(r"[a-z0-9_.]+", cr.act_kind_of(anon))


@pytest.mark.parametrize("link,ok", [
    ("/projects?task=1", True), ("  /calendar ", True),
    ("https://x.test/a", False), ("//x.test", True), ("a b", False),
    ("/has space", False), ("", False), (None, False), ("/" + "a" * 300, False),
])
def test_a_link_is_an_app_path_or_nothing(link, ok) -> None:
    assert (cr.clean_link(link) is not None) is ok


def test_off_or_without_facts_nothing_is_written(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cr, "insert_row", lambda *a, **k: calls.append("insert") or True)

    async def facts(_tid):
        return "org-1", "alice@x.test"

    monkeypatch.setattr(cr, "_facts", facts)
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "", raising=False)
    assert asyncio.run(cr.record_approved("t", "a" * 32, "a.b")) is False
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "*", raising=False)
    assert asyncio.run(cr.record_approved("t", "a" * 32, "a.b")) is True
    assert calls == ["insert"]

    async def no_org(_tid):
        return None, "alice@x.test"

    monkeypatch.setattr(cr, "_facts", no_org)
    assert asyncio.run(cr.record_approved("t", "a" * 32, "a.b")) is False


def test_a_fault_never_reaches_the_card(monkeypatch) -> None:
    async def facts(_tid):
        return "org-1", "alice@x.test"

    def boom(*_a, **_k):
        raise RuntimeError("db down with a secret title")

    monkeypatch.setattr(cr, "_facts", facts)
    monkeypatch.setattr(cr, "insert_row", boom)
    monkeypatch.setattr(cr, "update_link", boom)
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "*", raising=False)
    assert asyncio.run(cr.record_approved("t", "a" * 32, "a.b")) is False
    assert asyncio.run(cr.set_link("t", "a" * 32, "/x")) is False


def test_the_live_card_records_on_approve_only_with_the_responder() -> None:
    """The live path sits behind ``_answer == "APPROVE"`` and passes the card
    id and the gateway's responder, and nothing of the card's text."""
    src = Path(ask_tools.__file__).read_text(encoding="utf-8")
    assert re.search(
        r'if _answer == "APPROVE":\n(?:\s+#[^\n]*\n)+\s+await _record_approved_card\('
        r'_rid, _responded\[-1\] if _responded else ""\)', src)
    body = src[src.index("async def _record_approved_card"):src.index("async def note_sent_message")]
    code = re.sub(r'""".*?"""', "", body, flags=re.S)
    for forbidden in ("_title", "_detail", "_context", "title", "detail"):
        assert forbidden not in code


def test_note_record_link_without_an_approved_card_is_false() -> None:
    ask_tools._LAST_APPROVED.set(None)
    assert asyncio.run(ask_tools.note_record_link("/x")) is False


def test_the_migration_holds_no_text_column_and_is_tenant_scoped() -> None:
    sql = _migration_text()
    assert "FORCE  ROW LEVEL SECURITY" in sql
    assert "REFERENCES organization (id) ON DELETE CASCADE\n                     DEFAULT" in sql
    assert "outcome_key = 'approved'" in sql
    for banned in ("title", "detail", "body", "summary", "content", "message", "payload"):
        assert not re.search(rf"^\s+{banned}\s", sql, re.M | re.I), banned


def test_the_purge_deletes_the_rows_of_a_leaver_before_the_chats() -> None:
    from gateway.routes.admin.members import _PURGE_DELETES
    from gateway.routes.tasks.settings import _BOUND_ORG

    [rows] = [r for r in _PURGE_DELETES if r.table == "chat_card_record"]
    assert rows.key == "chat_card_rows"
    assert rows.where == f"lower(member_email) = :email AND organization_id = {_BOUND_ORG}"
    keys = [r.key for r in _PURGE_DELETES]
    assert keys.index("chat_card_rows") < keys.index("private_chat_sessions")


def test_was_sent_reads_the_message_id_only() -> None:
    rows = [{"request_id": "a" * 32, "message_id": "<m@x.test>"},
            {"request_id": "b" * 32, "message_id": None}]
    assert cr.was_sent(rows) and cr.was_sent(rows, "a" * 32)
    assert not cr.was_sent(rows, "b" * 32) and not cr.was_sent(rows, "c" * 32)
    assert not cr.was_sent([])


def test_note_sent_message_without_an_approved_card_is_false() -> None:
    ask_tools._LAST_APPROVED.set(None)
    assert asyncio.run(ask_tools.note_sent_message("<m@x.test>")) is False


def test_the_row_names_who_approved_and_off_costs_no_lookup(monkeypatch) -> None:
    """In a room the responder can differ from the run's member, and the row
    names the responder. With the flag off for the bound tenant no Redis fact
    is read at all."""
    written: list[str] = []
    looked: list[str] = []
    monkeypatch.setattr(cr, "insert_row",
                        lambda org, **k: written.append(k["member"]) or True)

    async def facts(_tid):
        looked.append("facts")
        return "org-1", "alice@x.test"

    monkeypatch.setattr(cr, "_facts", facts)
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "*", raising=False)
    assert asyncio.run(cr.record_approved("t", "a" * 32, "a.b", responder="Bob@x.test"))
    assert asyncio.run(cr.record_approved("t", "b" * 32, "a.b"))
    assert written == ["bob@x.test", "alice@x.test"]

    from acb_common import db as acb_db

    looked.clear()
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "other", raising=False)
    monkeypatch.setattr(acb_db, "current_tenant", lambda: "org-1")
    assert asyncio.run(cr.record_approved("t", "c" * 32, "a.b")) is False
    assert looked == []


def test_every_approve_path_of_the_gate_writes_a_row() -> None:
    """The live card, the late approve and the stored-call yes (one branch), and the two
    WhatsApp reply-code paths each call the recorder once."""
    src = Path(ask_tools.__file__).read_text(encoding="utf-8")
    assert src.count("await _record_approved_card(") == 4
