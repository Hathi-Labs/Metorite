"""WS-PZ MM-1c — more seams of the act record (hermetic).

Task 3615. Code: ``routes/email/act_features.py``, ``transport/forward.py``, ``transport/send.py``,
``automation/senders.py`` (bulk archive by ids), ``automation/replyzero.py``.

a. The sender domain is read from a ``from_address`` of any shape, and no
   address, subject or body reaches a feature.
b. The forward row carries the sender domain of a jsonb ``from_address``
   (the first slice read it as a string and got none).
c. A bulk archive by NAMED ids records, a sweep records nothing.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import member_actions
from gateway.routes.email import act_features

_G = Path(act_features.__file__).parents[2]
ORG = "11111111-1111-1111-1111-111111111111"


def test_the_sender_domain_of_any_shape() -> None:
    assert act_features.of_address({"email": "Bob@GE.com", "name": "Bob"}) == {
        "sender_domain": "ge.com"}
    assert act_features.of_address("Bob <bob@ge.com>") == {"sender_domain": "ge.com"}
    assert act_features.of_address("bob@ge.com") == {"sender_domain": "ge.com"}
    for none in (None, "", "nobody", {}, {"email": None}, 5, ["a@b.c"]):
        assert act_features.of_address(none) == {}
    for key in act_features.of_address({"email": "x@y.com", "name": "N"}):
        assert key in member_actions.FEATURE_KEYS


class _Db:
    def __init__(self, row: Any = None, boom: bool = False) -> None:
        self.row, self.boom, self.sql = row, boom, []

    def begin_nested(self) -> Any:
        class N:
            async def __aenter__(s) -> None: return None
            async def __aexit__(s, *a: Any) -> bool: return False
        return N()

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        if self.boom:
            raise RuntimeError("secret")
        self.sql.append(str(stmt))
        return SimpleNamespace(first=lambda: self.row)


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "member_actions_orgs", value, raising=False)


def test_a_message_and_a_thread_read_the_sender_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, ORG)
    db = _Db(("ana@ge.com",))
    assert asyncio.run(act_features.of_message(db, ORG, "m1")) == {"sender_domain": "ge.com"}
    assert asyncio.run(act_features.of_thread(db, ORG, "a1", "t1")) == {
        "sender_domain": "ge.com"}
    for sql in db.sql:
        assert "from_address" in sql and "subject" not in sql and "body" not in sql
    assert asyncio.run(act_features.of_message(_Db(None), ORG, "m")) == {}
    assert asyncio.run(act_features.of_message(_Db(boom=True), ORG, "m")) == {}


def test_the_flag_off_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _Db(("ana@ge.com",))
    assert asyncio.run(act_features.of_message(db, ORG, "m1")) == {}
    assert asyncio.run(act_features.of_thread(db, ORG, "a1", "t1")) == {}
    assert asyncio.run(act_features.of_message(db, None, "m1")) == {}
    assert db.sql == []


def test_the_forward_row_reads_a_jsonb_sender() -> None:
    from gateway.routes.email.transport import forward

    seen: dict[str, Any] = {}

    async def fake(db: Any, **kw: Any) -> bool:
        seen.update(kw)
        return True

    orig = member_actions.record_act
    member_actions.record_act = fake  # type: ignore[assignment]
    try:
        row = SimpleNamespace(id="m1", account_id="a1",
                              from_address={"email": "Bob@GE.com", "name": "Bob"})
        req = SimpleNamespace(to=["v@ge.com"], cc=["k@ge.com"], bcc=["no-address"])
        user = SimpleNamespace(organization_id=ORG, email="ana@x.com")
        asyncio.run(forward._record_forward("DB", user, row, req))
    finally:
        member_actions.record_act = orig  # type: ignore[assignment]
    assert seen["features"] == {"sender_domain": "ge.com"}
    assert seen["targets"] == ["v@ge.com", "k@ge.com"]


def test_the_seams_carry_the_features() -> None:
    em = _G / "routes" / "email"
    msgs = (em / "transport" / "messages.py").read_text(encoding="utf-8")
    assert "act_features.of_message(db, member_actions.org_of(user), message_id)" in msgs
    assert msgs.count("act_features.of_message(") == 2
    rz = (em / "automation" / "replyzero.py").read_text(encoding="utf-8")
    assert "db, member_actions.org_of(user), req.account_id, req.thread_id)" in rz
    send = (em / "transport" / "send.py").read_text(encoding="utf-8")
    assert "*(req.cc or [])" in send and "act_features.of_address(" in send


def test_a_bulk_archive_records_named_ids_only() -> None:
    src = (_G / "routes" / "email" / "automation" / "senders.py").read_text(encoding="utf-8")
    body = src.split("async def bulk_action", 1)[1].split("_RECONCILE_ATTEMPTS", 1)[0]
    assert 'if req.action == "archive" and req.message_ids:' in body
    assert "rows[:20]" in body
    assert "RETURNING id, provider_message_id, account_id, from_address" in body
