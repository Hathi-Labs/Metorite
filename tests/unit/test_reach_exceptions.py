"""WS-BRIEF BR-5 slice 1 — the exception engine, with no database.

Code: ``gateway/reach/exceptions.py``, ``plan.interrupt_candidates(rules=)``,
``brief.flags.exceptions``. Spec: ``core_brief.md`` §6, §10 row 9.

**Mutations (R7).**

* M1. ``RULES``: drop the ``waited_hours`` test of ``approval_waiting``.
  ``test_a_young_approval_holds`` goes red.
* M2. ``plan.interrupt_candidates``: ignore ``rules``.
  ``test_the_switch_changes_who_may_interrupt`` goes red.
* M3. ``exceptions.with_notify``: return *cards* unchanged.
  ``test_the_setting_on_lets_rule_3_fire`` goes red (row 13).
* M4. ``loop.block_notify``: drop the ``BRIEF_EXCEPTIONS`` gate.
  ``test_dark_reads_no_block_notify`` goes red.
* M5. ``plan.interrupt_candidates``: drop the rule id from the copy.
  ``test_a_candidate_carries_its_rule_id`` goes red (row 12).
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


def test_a_muted_rule_leaves_the_candidates() -> None:
    cards = [card("agent_asks", cid="2"), card("source_sync_failed", cid="3")]
    got = plan.interrupt_candidates(cards, set(), NOW, rules=True,
                                    muted=frozenset({"agent_asks"}))
    assert [c["signal_id"] for c in got] == ["source_sync_failed:3"]
    # Rules off, the mute has no effect: the class table decides as before.
    off = plan.interrupt_candidates(cards, set(), NOW, muted=frozenset({"agent_asks"}))
    assert off == plan.interrupt_candidates(cards, set(), NOW)


def test_the_mute_is_a_pref_of_the_rule_ids() -> None:
    from gateway.routes.shell import prefs

    assert prefs.rule_ids() == {r.id for r in exceptions.RULES}
    assert prefs._CLEAN["rule_mutes"](["agent_asks", "agent_asks"]) == ["agent_asks"]
    for bad in (["nope"], "agent_asks", [1], [r.id for r in exceptions.RULES] + ["x"], None):
        with pytest.raises(ValueError):
            prefs._CLEAN["rule_mutes"](bad)


async def test_dark_reads_no_mutes(monkeypatch) -> None:
    from gateway.reach import loop, outbox

    async def boom(org, member):
        raise AssertionError("read while dark")

    monkeypatch.setattr(outbox, "muted_rules", boom)
    _settings(monkeypatch, brief_exceptions=False)
    assert await loop.muted_rules(ORG, "a@b.test") == frozenset()


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


# ── BR-5 slice 2b-2: the ``block_notify`` setting (row 13, Q8 "One setting") ──


def test_the_setting_on_lets_rule_3_fire() -> None:
    cards = [card("block_soon", cid="1"), card("agent_asks", cid="2")]
    marked = exceptions.with_notify(cards, True)
    assert marked[0]["facts"]["notify_me"] is True
    assert "notify_me" not in marked[1]["facts"]
    assert exceptions.decide(marked[0], NOW).rule_id == "block_soon_asked"
    got = plan.interrupt_candidates(marked, set(), NOW, rules=True)
    assert {c["signal_id"] for c in got} == {"block_soon:1", "agent_asks:2"}
    # The feed's own card does not change.
    assert cards[0]["facts"] == {}


def test_the_setting_off_lets_rule_3_match_nothing() -> None:
    cards = [card("block_soon", cid="1")]
    assert exceptions.with_notify(cards, False) is cards
    assert exceptions.decide(cards[0], NOW) == exceptions.HOLD
    assert plan.interrupt_candidates(exceptions.with_notify(cards, False), set(), NOW,
                                     rules=True) == []


def test_the_setting_marks_a_folded_block_soon() -> None:
    folded = card("overdue", folded=("block_soon",))
    assert exceptions.decide(exceptions.with_notify([folded], True)[0], NOW).rule_id == \
        "block_soon_asked"


def test_a_muted_rule_3_holds_with_the_setting_on() -> None:
    marked = exceptions.with_notify([card("block_soon")], True)
    assert not exceptions.may_interrupt(marked[0], NOW,
                                        muted=frozenset({"block_soon_asked"}))


def test_block_notify_is_a_strict_boolean_pref() -> None:
    from gateway.routes.shell import prefs

    assert prefs._CLEAN["block_notify"](True) is True
    assert prefs._CLEAN["block_notify"](False) is False
    for bad in ("true", 1, None, []):
        with pytest.raises(ValueError):
            prefs._CLEAN["block_notify"](bad)
    assert prefs.stored_prefs({"block_notify": True}) == {"block_notify": True}
    assert prefs.stored_prefs({"block_notify": "yes"}) == {}


async def test_dark_reads_no_block_notify(monkeypatch) -> None:
    from gateway.reach import loop, outbox

    async def boom(org, member):
        raise AssertionError("read while dark")

    monkeypatch.setattr(outbox, "block_notify", boom)
    _settings(monkeypatch, brief_exceptions=False)
    assert await loop.block_notify(ORG, "a@b.test") is False


async def test_on_reads_block_notify(monkeypatch) -> None:
    from gateway.reach import loop, outbox

    async def yes(org, member):
        return True

    monkeypatch.setattr(outbox, "block_notify", yes)
    _settings(monkeypatch)
    assert await loop.block_notify(ORG, "a@b.test") is True


async def test_a_failed_read_of_block_notify_is_off(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from gateway.reach import outbox

    @asynccontextmanager
    async def broken(org):
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(outbox, "tenant_session", broken)
    assert await outbox.block_notify(ORG, "a@b.test") is False


# ── BR-5 slice 2b-1: the rule id of an alert (row 12, no database) ──────────


def test_a_candidate_carries_its_rule_id() -> None:
    cards = [card("agent_asks", cid="2"), card("notify_me", cid="3")]
    got = {c["signal_id"]: c for c in plan.interrupt_candidates(cards, set(), NOW, rules=True)}
    assert plan.entry(got["agent_asks:2"])[2] == "agent_asks"
    assert plan.entry(got["notify_me:3"])[2] is None
    assert plan.entry(got["agent_asks:2"])[:2] == plan.pair(cards[0])
    # Rules off, no card carries a rule id.
    off = plan.interrupt_candidates([card("agent_asks")], set(), NOW)
    assert all(plan.entry(c)[2] is None for c in off)


async def test_a_bad_rating_runs_no_sql(monkeypatch) -> None:
    from gateway.reach import outbox

    def boom(org):
        raise AssertionError("SQL ran")

    monkeypatch.setattr(outbox, "tenant_session", boom)
    for args in (("s", "agent_asks", "great"), ("s", "nope", "useful"),
                 ("", "agent_asks", "useful")):
        with pytest.raises(ValueError):
            await outbox.rate_alert(ORG, "a@b.test", *args)
