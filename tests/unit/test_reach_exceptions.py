"""WS-BRIEF BR-5 slice 1 — the exception engine, with no database.

Code: ``gateway/reach/exceptions.py``, ``plan.interrupt_candidates(rules=)``,
``brief.flags.exceptions``. Spec: ``core_brief.md`` §6, §10 row 9.

**Mutations (R7).**

* M1. ``RULES``: drop the ``waited_hours`` test of ``approval_waiting``.
  ``test_a_young_approval_holds`` goes red.
* M2. ``plan.interrupt_candidates``: ignore ``rules``.
  ``test_the_switch_changes_who_may_interrupt`` goes red.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.env_guard import is_platform_env
from acb_common.settings import Settings
from gateway.brief import flags
from gateway.reach import classes, exceptions, plan

NOW = datetime(2099, 3, 10, 12, 0, tzinfo=UTC)
ORG = "11111111-1111-1111-1111-111111111111"


def card(kind: str, *, hours: float | None = None, facts: dict[str, Any] | None = None,
         folded: tuple[str, ...] = (), cid: str = "x") -> dict[str, Any]:
    at = (NOW - timedelta(hours=hours)).isoformat() if hours is not None else None
    return {"signal_id": f"{kind}:{cid}", "kind": kind, "app": f"app{cid}", "title": kind,
            "at": at, "facts": facts or {}, "folded": list(folded), "state": "new",
            "group": "needs_you", "score": 50, "fingerprint": "fp000001", "why": "Why."}


def test_the_four_rules_are_the_owner_decision() -> None:
    assert [r.id for r in exceptions.RULES] == [
        "approval_waiting", "agent_asks", "block_soon_asked", "source_sync_failed"]
    assert all(r.action == "interrupt" for r in exceptions.RULES)


def test_an_old_approval_interrupts_and_a_young_one_holds() -> None:
    assert exceptions.decide(card("approval", hours=2), NOW).rule_id == "approval_waiting"
    assert exceptions.decide(card("approval", hours=5), NOW).action == "interrupt"
    young = exceptions.decide(card("approval", hours=1.9), NOW)
    assert young == exceptions.HOLD


def test_a_young_approval_holds() -> None:
    assert exceptions.may_interrupt(card("approval", hours=0.5), NOW) is False
    assert exceptions.may_interrupt(card("approval"), NOW) is False  # no readable `at`


def test_agent_asks_and_a_failed_sync_interrupt() -> None:
    assert exceptions.may_interrupt(card("agent_asks"), NOW)
    assert exceptions.may_interrupt(card("source_sync_failed"), NOW)


def test_a_block_soon_interrupts_only_when_the_member_asked() -> None:
    assert exceptions.may_interrupt(card("block_soon"), NOW) is False
    assert exceptions.may_interrupt(
        card("block_soon", facts={"notify_me": True}), NOW) is True
    assert exceptions.may_interrupt(
        card("block_soon", facts={"notify_me": "yes"}), NOW) is False


def test_no_rule_means_hold_also_for_a_class_that_interrupts_today() -> None:
    for kind in ("needs_reply", "crm_followup_due", "deal_quiet", "lead_replied",
                 "decision", "reminder_check", "overdue"):
        assert exceptions.decide(card(kind), NOW) == exceptions.HOLD, kind


def test_a_folded_kind_matches_a_rule() -> None:
    assert exceptions.may_interrupt(card("overdue", folded=("agent_asks",)), NOW)


def test_the_members_own_tell_me_request_always_qualifies() -> None:
    assert exceptions.may_interrupt(card("notify_me"), NOW)


def test_a_muted_rule_holds() -> None:
    assert not exceptions.may_interrupt(
        card("agent_asks"), NOW, muted=frozenset({"agent_asks"}))
    assert exceptions.may_interrupt(
        card("source_sync_failed"), NOW, muted=frozenset({"agent_asks"}))


def test_a_rule_refuses_a_bad_operator_or_action() -> None:
    with pytest.raises(ValueError):
        exceptions.Rule(id="r", kind="k", when={"a": ("!=", 1)})
    with pytest.raises(ValueError):
        exceptions.Rule(id="r", kind="k", action="send")


def test_the_operators() -> None:
    r = exceptions.Rule(id="r", kind="k", when={"n": ("<=", 3), "s": ("in", ("a", "b"))})
    assert exceptions.decide(card("k", facts={"n": 3, "s": "a"}), NOW, rules=(r,)).action == \
        "interrupt"
    assert exceptions.decide(card("k", facts={"n": 4, "s": "a"}), NOW, rules=(r,)) == \
        exceptions.HOLD
    assert exceptions.decide(card("k", facts={"n": "x", "s": "z"}), NOW, rules=(r,)) == \
        exceptions.HOLD


def test_the_switch_changes_who_may_interrupt() -> None:
    cards = [card("needs_reply", hours=1, facts={"waiting_since": NOW.isoformat()}, cid="1"),
             card("agent_asks", cid="2"), card("approval", hours=3, cid="3"),
             card("approval", hours=1, cid="4")]
    assert classes.reach_class(cards[0]) == "interrupt"
    off = {c["signal_id"] for c in plan.interrupt_candidates(cards, set(), NOW)}
    on = {c["signal_id"] for c in plan.interrupt_candidates(cards, set(), NOW, rules=True)}
    assert "needs_reply:1" in off and "needs_reply:1" not in on
    assert on == {"agent_asks:2", "approval:3"}


def _settings(monkeypatch, **kw: Any) -> None:
    base = {"brief_enabled": True, "brief_orgs": ORG, "brief_exceptions": True}
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(**{**base, **kw}))


def test_the_switch_is_dark_by_default_and_needs_the_org(monkeypatch) -> None:
    assert Settings.model_fields["brief_exceptions"].default is False
    assert is_platform_env("BRIEF_EXCEPTIONS")
    _settings(monkeypatch)
    assert flags.exceptions(ORG) is True
    assert flags.exceptions("22222222-2222-2222-2222-222222222222") is False
    assert flags.exceptions(None) is False
    _settings(monkeypatch, brief_exceptions=False)
    assert flags.exceptions(ORG) is False
    _settings(monkeypatch, brief_enabled=False)
    assert flags.exceptions(ORG) is False
    _settings(monkeypatch, brief_exceptions="true")
    assert flags.exceptions(ORG) is False
