"""WS-PZ MM-1b — the card answer in the act record (hermetic).

Spec: ``personalization.md`` §19.4, ``proactive_loop.md`` §5.3. Code:
``orchestrator/card_act.py``, called by ``ask_tools.request_confirmation``.
R8 twin: ``test_card_act_r8.py``.

a. The act comes from ``ask_tools.CARD_OUTCOMES``: approved and declined map,
   each other outcome records nothing.
b. The flag off for the bound tenant reads no fact and opens no session.
c. Only the run's own member: a press of another member writes nothing. A
   late approve takes the run's own member.
d. A failure never raises.
e. The wiring: the live card and the late approve call the seam, and the
   gateway route records no card answer.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from acb_common import get_settings, member_actions
from acb_skills import ask_tools
from orchestrator import card_act

ORG = "11111111-1111-1111-1111-111111111111"
ANA = "ana@example.com"
_ROOT = Path(__file__).resolve().parents[2]
_AGENT_ROUTE = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "agent.py"


def test_the_acts_come_from_the_card_outcomes() -> None:
    """Mutation: map "timed out" or "another member answered" to an act."""
    assert card_act.act_of(ask_tools.CARD_APPROVED) == "card_approve"
    assert card_act.act_of(ask_tools.CARD_DECLINED) == "card_reject"
    assert set(card_act.ACT_OF_OUTCOME) <= set(ask_tools.CARD_OUTCOMES)
    assert set(card_act.ACT_OF_OUTCOME.values()) <= member_actions.ACTS["chat"]
    for outcome in ask_tools.CARD_OUTCOMES:
        if outcome not in (ask_tools.CARD_APPROVED, ask_tools.CARD_DECLINED):
            assert card_act.act_of(outcome) is None, outcome
    for text in ("APPROVE", "REJECT", "", None, "card_revise"):
        assert card_act.act_of(text) is None


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "member_actions_orgs", value, raising=False)


class _Sess:
    async def __aenter__(self) -> str:
        return "DB"

    async def __aexit__(self, *a: Any) -> bool:
        return False


def _wire(monkeypatch: pytest.MonkeyPatch, actor: str = ANA,
          org: str | None = ORG) -> tuple[list[dict[str, Any]], list[str]]:
    """Fake the run facts, the session and the writer. Gives the written
    rows and the fact reads."""
    import acb_common.db as db

    written: list[dict[str, Any]] = []
    looked: list[str] = []

    async def facts(tid: str) -> tuple[str | None, str]:
        looked.append(tid)
        return org, actor

    async def fake(dbs: Any, **kw: Any) -> bool:
        written.append({**kw, "db": dbs})
        return True

    monkeypatch.setattr(card_act, "_facts", facts)
    monkeypatch.setattr(db, "tenant_session", lambda org: _Sess())
    monkeypatch.setattr(db, "current_tenant", lambda: None)
    monkeypatch.setattr(member_actions, "record_act", fake)
    return written, looked


def test_an_approve_and_a_decline_of_the_member_write_card_rows(monkeypatch) -> None:
    """Mutation: drop via="card", or take the member from the answer text."""
    _flag(monkeypatch, ORG)
    written, _ = _wire(monkeypatch)
    rid = "a" * 32
    assert asyncio.run(card_act.record_card_act("t1", rid, "approved", "Ana@Example.com"))
    assert asyncio.run(card_act.record_card_act("t1", "b" * 32, "declined", ANA))
    assert written[0] == {"db": "DB", "org": ORG, "member": ANA, "app": "chat",
                          "act": "card_approve", "via": "card", "item_kind": "card",
                          "item_id": rid}
    assert written[1]["act"] == "card_reject" and written[1]["item_id"] == "b" * 32


@pytest.mark.parametrize("outcome", [
    "timed out", "closed by your message", "another member answered", "no card",
    "parked", "changed since"])
def test_each_other_outcome_writes_nothing_and_reads_no_fact(monkeypatch, outcome) -> None:
    """Mutation: record a card_revise for "closed by your message"."""
    _flag(monkeypatch, "*")
    written, looked = _wire(monkeypatch)
    assert asyncio.run(card_act.record_card_act("t1", "c" * 32, outcome, ANA)) is False
    assert written == [] and looked == []


def test_a_press_of_another_member_writes_nothing(monkeypatch) -> None:
    """PZ-32: in a shared room a colleague can press the card. Mutation: drop
    the check that the responder is the run's own member."""
    _flag(monkeypatch, "*")
    written, _ = _wire(monkeypatch, actor=ANA)
    for who in ("bob@example.com", "", None):
        assert asyncio.run(card_act.record_card_act("t1", "d" * 32, "approved", who)) is False
    written2, _ = _wire(monkeypatch, actor="")
    assert asyncio.run(card_act.record_card_act("t1", "d" * 32, "approved", "")) is False
    assert written == [] and written2 == []


def test_a_late_approve_takes_the_runs_own_member_and_a_new_id(monkeypatch) -> None:
    _flag(monkeypatch, "*")
    written, _ = _wire(monkeypatch)
    assert asyncio.run(card_act.record_card_act("t1", None, "approved", late=True))
    assert written[0]["member"] == ANA and written[0]["act"] == "card_approve"
    assert re.fullmatch(r"[0-9a-f]{32}", written[0]["item_id"])


def test_the_flag_off_reads_no_fact_and_opens_no_session(monkeypatch) -> None:
    """Mutation: read the run facts before the flag."""
    import acb_common.db as db

    written, looked = _wire(monkeypatch)
    _flag(monkeypatch, "")
    monkeypatch.setattr(db, "current_tenant", lambda: ORG)
    assert asyncio.run(card_act.record_card_act("t1", "e" * 32, "approved", ANA)) is False
    assert looked == [] and written == []
    # No bound tenant and the flag empty (the default): no fact read either.
    monkeypatch.setattr(db, "current_tenant", lambda: None)
    assert asyncio.run(card_act.record_card_act("t1", "e" * 32, "approved", ANA)) is False
    assert looked == [] and written == []
    # No bound tenant: the org of the run decides, and another org writes nothing.
    _flag(monkeypatch, "other-org")
    assert asyncio.run(card_act.record_card_act("t1", "e" * 32, "approved", ANA)) is False
    assert looked == ["t1"] and written == []
    # No org at all writes nothing.
    _flag(monkeypatch, "*")
    written2, _ = _wire(monkeypatch, org=None)
    assert asyncio.run(card_act.record_card_act("t1", "e" * 32, "approved", ANA)) is False
    assert written2 == []


def test_a_failure_never_raises(monkeypatch) -> None:
    import acb_common.db as db

    _flag(monkeypatch, "*")
    _wire(monkeypatch)

    def boom(org: str) -> Any:
        raise RuntimeError("secret")

    monkeypatch.setattr(db, "tenant_session", boom)
    assert asyncio.run(card_act.record_card_act("t1", "f" * 32, "approved", ANA)) is False

    async def slow(tid: str) -> tuple[str, str]:
        raise TimeoutError

    monkeypatch.setattr(card_act, "_facts", slow)
    assert asyncio.run(card_act.record_card_act("t1", "f" * 32, "approved", ANA)) is False


def test_the_card_gate_calls_the_seam_after_the_outcome() -> None:
    """The live card passes the outcome, the card id and the gateway's
    responder. The late approve and the stored-call yes pass late=True.
    Mutation: move the call above the outcome, or pass a field of the card."""
    src = Path(ask_tools.__file__).read_text(encoding="utf-8")
    assert src.count("await _record_card_act(") == 2
    live = src.index("await _record_card_act(_LAST_OUTCOME.get()")
    assert src.rindex("_LAST_OUTCOME.set(\n", 0, live) < live
    assert re.search(r'await _record_card_act\(_LAST_OUTCOME\.get\(\) or "", _rid,\n'
                     r'\s+_responded\[-1\] if _responded else ""\)', src)
    late = src.index("await _record_card_act(CARD_APPROVED, None, late=True)")
    assert src.rindex("_late_approved(_card)", 0, late) < late
    body = src[src.index("async def _record_card_act"):src.index("async def note_sent_message")]
    code = re.sub(r'""".*?"""', "", body, flags=re.S)
    for forbidden in ("_title", "_detail", "_context", "title", "detail", "answer"):
        assert forbidden not in code


def test_the_gateway_route_records_no_card_answer() -> None:
    """One seam: the route sees the text of an answer, never its outcome."""
    src = _AGENT_ROUTE.read_text(encoding="utf-8")
    assert "card_act" not in src and "record_card" not in src
