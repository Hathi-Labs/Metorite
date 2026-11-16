"""WS-PZ MM-1b — the card answer in the act record (hermetic).

Spec: ``personalization.md`` §19.4, ``proactive_loop.md`` §5.3. Code:
``gateway/card_act.py``, called by ``POST /agent/respond-input``. R8 twin:
``test_card_act_r8.py``.

a. The act: APPROVE and REJECT and a decline map. Any other shape records
   nothing.
b. The gates: no org, no flag, no member record nothing and open no session.
c. A failure never raises. The row holds no text (the writer's fence).
d. The route calls the seam after delivery and on a late decline only.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gateway import card_act

ORG = "11111111-1111-1111-1111-111111111111"
USER = SimpleNamespace(organization_id=ORG, email="ana@example.com")
_AGENT = Path(card_act.__file__).parent / "routes" / "agent.py"


@pytest.mark.parametrize("answer,declined,act", [
    ("APPROVE", False, "card_approve"), (" approve ", False, "card_approve"),
    ("REJECT", False, "card_reject"), ("anything", True, "card_reject"),
    ("", True, "card_reject"),
    ('{"kind":"send","body":"hi"}', False, None), ("YES", False, None), ("", False, None),
])
def test_the_act_of_an_answer(answer: str, declined: bool, act: str | None) -> None:
    assert card_act.act_of(answer, declined) == act


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "member_actions_orgs", value, raising=False)


def test_the_flag_off_opens_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    import acb_common.db as db

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("a session was opened")

    monkeypatch.setattr(db, "tenant_session", boom)
    assert asyncio.run(card_act.record_card_answer(USER, "r1", "APPROVE")) is False


def test_a_row_is_written_with_the_session_member_and_org(monkeypatch: pytest.MonkeyPatch) -> None:
    import acb_common.db as db
    from acb_common import member_actions

    _flag(monkeypatch, ORG)
    seen: dict[str, Any] = {}

    class _Sess:
        async def __aenter__(self) -> str:
            return "DB"

        async def __aexit__(self, *a: Any) -> bool:
            return False

    monkeypatch.setattr(db, "tenant_session", lambda org: _Sess())

    async def fake(dbs: Any, **kw: Any) -> bool:
        seen.update(kw, db=dbs)
        return True

    monkeypatch.setattr(member_actions, "record_act", fake)
    assert asyncio.run(card_act.record_card_answer(USER, "req-1", "APPROVE")) is True
    assert seen == {"db": "DB", "org": ORG, "member": "ana@example.com", "app": "chat",
                    "act": "card_approve", "via": "card", "item_kind": "card",
                    "item_id": "req-1"}
    assert asyncio.run(card_act.record_card_answer(USER, "req-2", "", True)) is True
    assert seen["act"] == "card_reject"


def test_a_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import acb_common.db as db

    _flag(monkeypatch, ORG)

    def boom(org: str) -> Any:
        raise RuntimeError("secret")

    monkeypatch.setattr(db, "tenant_session", boom)
    assert asyncio.run(card_act.record_card_answer(USER, "r", "APPROVE")) is False
    assert asyncio.run(card_act.record_card_answer(SimpleNamespace(), "r", "APPROVE")) is False


def test_the_route_calls_the_seam_after_delivery_and_on_a_late_decline() -> None:
    src = _AGENT.read_text(encoding="utf-8")
    assert src.count("record_card_answer(") == 2
    body = src.split("async def respond_user_input", 1)[1]
    first = body.index("record_card_answer(user, req.request_id, req.answer, req.declined)")
    assert body.index("delivered = await dispatch_control") < first
    assert first < body.index("record_reply_answer(")
    assert "record_card_answer(user, req.request_id, req.answer, True)" in body
