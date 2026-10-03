"""EM-T5 (CP-13e): the four email triage sites ask ``decide`` in SHADOW only.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.4. One test (or
one parametrised test) for each "Done when" line.

The rules these pin:

- Every mode is ``off`` by default, and ``off`` makes no ``decide`` call.
- ``shadow`` needs BOTH the mode and the organization on the list.
- ``on`` and every unknown value resolve to ``off`` and log
  ``decide.mode_refused``.
- The site ALWAYS returns the old answer, also when ``decide`` disagrees,
  fails, refuses the request or is slow.
- The log lines carry no subject, body, sender, reason or rule name.

EM-T5b-1 (§10.4.8) changed two things here. ``email.rule_pick`` is now
``email.rule_match``. And the state is no longer the old user message: it is
an object of FACTS, and the corrections ride in the instructions. The old
"the state equals the old user message" fences (§10.4.4 item 10) are
replaced by the state-shape fences below and in
``test_email_decide_questions.py``.

Every test uses a FAKE ``acb_llm.decide.decide``. No test reaches a network.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import acb_llm as decide_mod
import pytest
import structlog
from acb_common import get_settings
from acb_common._log import clear_run_context, job_member_scope, run_context_scope
from acb_common.db import bind_tenant, release_tenant
from gateway import decide_features as df
from gateway.routes.email.automation import engine as eng
from gateway.routes.email.automation import learning as lrn
from gateway.routes.email.automation import replyzero as rz
from gateway.routes.email.automation import senders as snd

ORG = "11111111-1111-1111-1111-111111111111"
OTHER_ORG = "22222222-2222-2222-2222-222222222222"
ACC = "acc-shadow-1"

# Tenant text. None of it may reach a decide.* log record.
SECRET_SUBJECT = "SECRET-SUBJECT-quarterly-pricing"
SECRET_BODY = "SECRET-BODY-please-send-the-quote"
SECRET_SENDER = "secret.sender@vendor.example"
SECRET_REASON = "SECRET-REASON-model-said-so"
SECRET_RULE = "SECRET-RULE-Newsletter"

EMAIL = {
    "from": SECRET_SENDER,
    "from_name": "Vendor",
    "to": "owner@acme.com",
    "subject": SECRET_SUBJECT,
    "body": SECRET_BODY,
}
RULES = [
    {"id": "1", "name": SECRET_RULE, "instructions": "newsletters"},
    {"id": "2", "name": "Receipt", "instructions": "receipts"},
]

ALL_SHADOW = ",".join(f"{f}=shadow" for f in df.FEATURES)


# ── Fixtures ────────────────────────────────────────────────────────────────


class FakeDecide:
    """Records each call. Answers every question so that it DISAGREES with
    the old answer the site fakes below produce, unless told otherwise."""

    def __init__(
        self,
        *,
        probability: float = 0.0,
        choice: str | None = None,
        raises: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.probability = probability
        self.choice = choice
        self.raises = raises
        self.delay = delay

    async def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": questions, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        answers: dict[str, Any] = {}
        for qid, q in questions.items():
            if q.type == "boolean":
                answers[qid] = decide_mod.BooleanAnswer(probability=self.probability)
            else:
                keys = list(q.criteria)
                answers[qid] = decide_mod.ChoiceAnswer(
                    choice=self.choice or keys[-1],
                    probabilities=MappingProxyType({k: 0.0 for k in keys}),
                    confidence=0.42,
                )
        return decide_mod.Decision(
            answers=MappingProxyType(answers), request_id="req-123"
        )


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """No mode, no tenant, no run context, and fresh caches."""
    monkeypatch.setenv("DECIDE_FEATURE_MODES", "")
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", "")
    _clear_caches()
    with run_context_scope():
        clear_run_context()
        yield
    monkeypatch.undo()
    _clear_caches()


def _clear_caches() -> None:
    # `get_settings` is cached for the process, and so are the two parsers.
    get_settings.cache_clear()
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()


@pytest.fixture()
def tenant():
    token = bind_tenant(ORG)
    yield ORG
    release_tenant(token)


def _modes(monkeypatch, modes: str, orgs: str = ORG) -> None:
    monkeypatch.setenv("DECIDE_FEATURE_MODES", modes)
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", orgs)
    _clear_caches()


def _fake(monkeypatch, **kw) -> FakeDecide:
    fake = FakeDecide(**kw)
    monkeypatch.setattr(decide_mod, "decide", fake)
    return fake


def _records(caps, event: str) -> list[dict[str, Any]]:
    return [c for c in caps if c.get("event") == event]


# ── The four sites, each with a fake OLD call ───────────────────────────────
#
# Each old fake gives an answer the default FakeDecide disagrees with:
# cold True vs p=0.0, pin True vs p=0.0, DONE vs the last key, r0 vs none.


def _llm(monkeypatch, module, data: Any, delay: float = 0.0) -> list[Any]:
    sent: list[Any] = []

    async def fake(model, messages, **kw):
        sent.append(messages)
        if delay:
            await asyncio.sleep(delay)
        return data, "{}", model

    monkeypatch.setattr(module, "_llm_json", fake)
    return sent


async def _site_cold(monkeypatch, delay: float = 0.0):
    _llm(monkeypatch, snd, {"cold": True, "reason": SECRET_REASON}, delay)
    return (await snd._llm_is_cold(EMAIL, blocker="LABEL", account_id=ACC),
            (True, SECRET_REASON))


def _pin_db() -> AsyncMock:
    db = AsyncMock()
    rows = [SimpleNamespace(subject=SECRET_SUBJECT, snippet=SECRET_BODY)] * 4
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=rows))
    return db


async def _site_pin(monkeypatch, delay: float = 0.0):
    _llm(monkeypatch, lrn, {"always": True, "why": SECRET_REASON}, delay)
    out = await lrn._ai_confirms_sender_pattern(_pin_db(), ACC, SECRET_SENDER, RULES[0])
    return out, True


#: The thread as facts (`ThreadContext.messages`), the other party last.
THREAD = [{
    "side": "other_party", "from": SECRET_SENDER, "to": "owner@acme.com",
    "cc": "", "owner_cc_only": False, "date": "", "subject": SECRET_SUBJECT,
    "attachments": "", "body": SECRET_BODY,
}]


async def _site_status(monkeypatch, delay: float = 0.0):
    _llm(monkeypatch, rz, {"status": "DONE", "rationale": SECRET_REASON}, delay)
    out = await rz._llm_determine_thread_status(
        f"From: {SECRET_SENDER}\n{SECRET_BODY}", "owner@acme.com", "",
        user_sent_last=False, account_id=ACC, thread_messages=THREAD,
    )
    return out, ("DONE", True)


async def _site_rule(monkeypatch, delay: float = 0.0):
    _llm(monkeypatch, eng, {"index": 0, "reason": SECRET_REASON}, delay)
    out = await eng._llm_pick_rule(EMAIL, RULES, account_id=ACC)
    return out, {"index": 0, "reason": SECRET_REASON}


SITES = {
    "email.cold_check": _site_cold,
    "email.sender_pin": _site_pin,
    "email.thread_status": _site_status,
    "email.rule_match": _site_rule,
}


# ── Done when: every mode off → zero calls ──────────────────────────────────


async def test_every_mode_is_off_by_default() -> None:
    assert dict(df.DEFAULT_MODES) == {f: "off" for f in df.FEATURES}
    assert set(df.FEATURES) == set(SITES)


@pytest.mark.parametrize("feature", list(SITES))
async def test_all_off_makes_zero_calls(monkeypatch, tenant, feature) -> None:
    _modes(monkeypatch, "", ORG)
    fake = _fake(monkeypatch)
    got, old = await SITES[feature](monkeypatch)
    assert got == old
    assert fake.calls == []


async def test_off_builds_no_question(monkeypatch, tenant) -> None:
    built: list[int] = []

    def build():
        built.append(1)
        return "s", {}

    async def old():
        return "old"

    fake = _fake(monkeypatch)
    out = await df.shadow(
        "email.cold_check", old, account_id=ACC, build=build,
        compare=lambda r, d: None,  # type: ignore[arg-type,return-value]
    )
    assert out == "old"
    assert built == [] and fake.calls == []


# ── Done when: shadow, org NOT on the list → zero calls ─────────────────────


@pytest.mark.parametrize("feature", list(SITES))
async def test_shadow_for_an_unlisted_org_makes_zero_calls(
    monkeypatch, tenant, feature
) -> None:
    _modes(monkeypatch, ALL_SHADOW, OTHER_ORG)
    fake = _fake(monkeypatch)
    got, old = await SITES[feature](monkeypatch)
    assert got == old
    assert fake.calls == []


async def test_an_empty_org_list_allows_no_org(monkeypatch, tenant) -> None:
    _modes(monkeypatch, ALL_SHADOW, "")
    fake = _fake(monkeypatch)
    await _site_cold(monkeypatch)
    assert fake.calls == []


async def test_no_bound_tenant_makes_zero_calls(monkeypatch) -> None:
    _modes(monkeypatch, ALL_SHADOW, ORG)
    fake = _fake(monkeypatch)
    await _site_cold(monkeypatch)
    assert fake.calls == []


# ── Done when: shadow + listed org → one call, the OLD answer wins ──────────


@pytest.mark.parametrize("feature", list(SITES))
async def test_shadow_makes_one_call_and_returns_the_old_answer(
    monkeypatch, tenant, feature
) -> None:
    _modes(monkeypatch, f"{feature}=shadow", ORG)
    fake = _fake(monkeypatch)
    with structlog.testing.capture_logs() as caps:
        got, old = await SITES[feature](monkeypatch)
    assert len(fake.calls) == 1
    assert got == old
    rec = _records(caps, "decide.shadow")
    assert len(rec) == 1
    assert rec[0]["agree"] is False, "the fake disagrees, so the site must still act on old"


async def test_only_the_named_feature_is_in_shadow(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    fake = _fake(monkeypatch)
    await _site_cold(monkeypatch)
    await _site_pin(monkeypatch)
    await _site_status(monkeypatch)
    assert fake.calls == []
    await _site_rule(monkeypatch)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("feature", list(SITES))
async def test_the_state_is_an_object_of_facts_not_the_old_prompt(
    monkeypatch, tenant, feature
) -> None:
    """EM-T5b-1 replaced §10.4.4 item 10. The old call still sends its own
    text, but the ``decide`` state is a dict of named facts. It is not the old
    user message, and it holds no persona line of that message."""
    _modes(monkeypatch, f"{feature}=shadow", ORG)
    fake = _fake(monkeypatch)
    await SITES[feature](monkeypatch)
    state = fake.calls[0]["state"]
    assert isinstance(state, dict)
    text = json.dumps(state)
    for persona in ("You are", "acting on behalf", "Respond", "Determine"):
        assert persona not in text, persona


async def test_the_status_corrections_ride_in_the_instructions_not_the_state(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, "email.thread_status=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, rz, {"status": "DONE"})
    block = ("\n\nCORRECTIONS THE USER HAS MADE BEFORE (these override your "
             "default reading):\n- Vendors chasing a PO need an answer.")
    await rz._llm_determine_thread_status(
        "thread", "owner@acme.com", "About me", user_sent_last=False,
        corrections=block, account_id=ACC, thread_messages=THREAD,
    )
    (question,) = fake.calls[0]["questions"].values()
    assert "Vendors chasing a PO need an answer." in question.instructions
    assert "Vendors chasing" not in json.dumps(fake.calls[0]["state"])


async def test_the_rule_state_holds_no_rule_text_and_no_correction(
    monkeypatch, tenant
) -> None:
    """The rules and the corrections are questions and instructions. The
    state is the email, the direction, the owner and the sender history."""
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, eng, {"index": -1})
    await eng._llm_pick_rule(
        EMAIL, RULES, hints="Receipt (x3)", guidance={"": ["be strict"]},
        account_id=ACC, history=[{"rule": "Receipt", "count": 3}],
    )
    state = fake.calls[0]["state"]
    assert set(state) == {"email", "direction", "mailbox_owner", "sender_history"}
    assert state["sender_history"] == [{"rule": "Receipt", "count": 3}]
    text = json.dumps(state)
    assert "be strict" not in text and "newsletters" not in text


@pytest.mark.parametrize("feature", list(SITES))
async def test_decide_runs_at_the_same_time_as_the_old_call(
    monkeypatch, tenant, feature
) -> None:
    """Item 5. Both calls take 0.4 s. Concurrent is about 0.4 s and in
    sequence is about 0.8 s, so the bound sits between them with margin."""
    monkeypatch.setattr(df, "SHADOW_BOUND_S", 2.0)
    _modes(monkeypatch, f"{feature}=shadow", ORG)
    fake = _fake(monkeypatch, delay=0.4)
    started = time.monotonic()
    with structlog.testing.capture_logs() as caps:
        got, old = await SITES[feature](monkeypatch, delay=0.4)
    elapsed = time.monotonic() - started
    assert got == old
    assert len(fake.calls) == 1
    assert _records(caps, "decide.shadow"), "decide must finish inside the bound"
    assert elapsed < 0.7, f"old and decide ran in sequence ({elapsed:.2f}s)"


async def test_the_old_exception_still_propagates(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    _fake(monkeypatch, delay=10)

    async def boom(*a, **kw):
        raise RuntimeError("gateway 502")

    monkeypatch.setattr(eng, "_llm_json", boom)
    started = time.monotonic()
    with pytest.raises(eng.LLMUnavailable):
        await eng._llm_pick_rule(EMAIL, RULES, account_id=ACC)
    assert time.monotonic() - started < 1.0, "a failed old call must not wait for decide"


# ── Done when: DecideUnavailable → decide.fallback, old answer ──────────────


@pytest.mark.parametrize("feature", list(SITES))
async def test_unavailable_logs_fallback_and_keeps_the_old_answer(
    monkeypatch, tenant, feature
) -> None:
    _modes(monkeypatch, f"{feature}=shadow", ORG)
    _fake(monkeypatch, raises=decide_mod.DecideUnavailable("disabled"))
    with structlog.testing.capture_logs() as caps:
        got, old = await SITES[feature](monkeypatch)
    assert got == old
    rec = _records(caps, "decide.fallback")
    assert len(rec) == 1
    assert rec[0]["decide_reason"] == "disabled"
    assert rec[0]["decide_feature"] == feature
    assert _records(caps, "decide.shadow") == []


# ── Done when: DecideRequestInvalid → decide.shadow_invalid, old answer ─────


@pytest.mark.parametrize("feature", list(SITES))
async def test_request_invalid_logs_shadow_invalid_at_error(
    monkeypatch, tenant, feature
) -> None:
    _modes(monkeypatch, f"{feature}=shadow", ORG)
    detail = {"reason": "criterion_too_long", "error": f"bad {SECRET_RULE}"}
    _fake(monkeypatch, raises=decide_mod.DecideRequestInvalid(400, detail))
    with structlog.testing.capture_logs() as caps:
        got, old = await SITES[feature](monkeypatch)
    assert got == old
    rec = _records(caps, "decide.shadow_invalid")
    assert len(rec) == 1
    assert rec[0]["log_level"] == "error"
    assert rec[0]["decide_reason"] == "criterion_too_long"
    assert SECRET_RULE not in repr(rec)


async def test_any_other_decide_error_never_stops_triage(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.cold_check=shadow", ORG)
    _fake(monkeypatch, raises=RuntimeError(f"boom {SECRET_BODY}"))
    with structlog.testing.capture_logs() as caps:
        got, old = await _site_cold(monkeypatch)
    assert got == old
    assert _records(caps, "decide.shadow_failed")
    assert SECRET_BODY not in repr([c for c in caps if c["event"].startswith("decide.")])


# ── Done when: `on` or an unknown value → off + decide.mode_refused ─────────


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("email.cold_check=sometimes", "unknown"),
        ("email.nonsense=shadow", "unknown"),
        ("email.nonsense=on", "unknown"),
        ("email.cold_check", "unknown"),
        # F3: a refused value AFTER a valid pair still turns the feature off.
        ("email.cold_check=shadow,email.cold_check=bogus", "unknown"),
        # `on` for a feature outside ON_FEATURES. EM-T5b-2 in full put all
        # four email features in the set, so these cases narrow it.
        ("email.cold_check=on", "on_refused"),
        ("email.cold_check=ON", "on_refused"),
        ("email.cold_check=shadow,email.cold_check=on", "on_refused"),
    ],
)
async def test_on_and_unknown_values_resolve_to_off_and_are_refused(
    monkeypatch, tenant, raw, reason
) -> None:
    if reason == "on_refused":
        monkeypatch.setattr(df, "ON_FEATURES", frozenset({"email.rule_match"}))
    with structlog.testing.capture_logs() as caps:
        _modes(monkeypatch, raw, ORG)
        assert df.mode_for("email.cold_check") == "off"
        fake = _fake(monkeypatch)
        got, old = await _site_cold(monkeypatch)
    assert got == old
    assert fake.calls == []
    rec = _records(caps, "decide.mode_refused")
    assert rec and rec[0]["decide_reason"] == reason


async def test_a_refused_pair_does_not_disable_a_good_one(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.cold_check=bogus, email.rule_match=shadow", ORG)
    assert df.mode_for("email.cold_check") == "off"
    assert df.mode_for("email.rule_match") == "shadow"


async def test_an_unregistered_feature_name_is_off(monkeypatch, tenant) -> None:
    _modes(monkeypatch, ALL_SHADOW, ORG)
    assert df.mode_for("email.not_a_feature") == "off"


# ── Done when: the decide.shadow record holds the fields, no tenant text ────


async def test_the_shadow_record_holds_the_fields_and_no_tenant_text(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, ALL_SHADOW, ORG)
    _fake(monkeypatch)
    with structlog.testing.capture_logs() as caps:
        for site in SITES.values():
            await site(monkeypatch)
    recs = _records(caps, "decide.shadow")
    assert {r["decide_feature"] for r in recs} == set(SITES)
    for r in recs:
        for key in (
            "decide_feature", "account_id", "message_id", "old", "new", "agree",
            "confidence", "probability", "latency_ms", "options", "request_id",
            "request_ids", "questions", "requests",
        ):
            assert key in r, key
        assert r["account_id"] == ACC
        assert r["request_id"] == "req-123"
        assert r["request_ids"] == ["req-123"] * r["requests"]
        assert isinstance(r["latency_ms"], int)
        # A boolean logs its probability, a choice its confidence, and the
        # rule match the probability of each rule under its key.
        assert (r["confidence"] is not None) or (r["probability"] is not None) \
            or ("p_r0" in r)
    decide_lines = repr([c for c in caps if c["event"].startswith("decide.")])
    for secret in (SECRET_SUBJECT, SECRET_BODY, SECRET_SENDER, SECRET_REASON, SECRET_RULE):
        assert secret not in decide_lines, secret


async def test_the_thread_status_record_keeps_the_old_confident_flag(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, "email.thread_status=shadow", ORG)
    _fake(monkeypatch, choice="DONE")
    with structlog.testing.capture_logs() as caps:
        await _site_status(monkeypatch)
    rec = _records(caps, "decide.shadow")[0]
    assert rec["old"] == "DONE" and rec["new"] == "DONE" and rec["agree"] is True
    assert rec["old_confident"] is True
    assert rec["options"] == 4
    assert "margin" in rec  # every choice logs its top-two margin


async def test_the_sender_pin_agrees_at_the_ninety_percent_threshold(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, "email.sender_pin=shadow", ORG)
    _fake(monkeypatch, probability=0.89)
    with structlog.testing.capture_logs() as caps:
        await _site_pin(monkeypatch)
    assert _records(caps, "decide.shadow")[0]["agree"] is False
    _fake(monkeypatch, probability=0.9)
    with structlog.testing.capture_logs() as caps:
        await _site_pin(monkeypatch)
    assert _records(caps, "decide.shadow")[0]["agree"] is True


async def test_the_rule_record_logs_keys_never_names(monkeypatch, tenant) -> None:
    """Both rules pass 0.5 and `best` ranks r0 first, so the main rule agrees
    with the old pick. The old set is {r0} and the new set {r0, r1}."""
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    _fake(monkeypatch, probability=0.9, choice="r0")
    with structlog.testing.capture_logs() as caps:
        await _site_rule(monkeypatch)
    rec = _records(caps, "decide.shadow")[0]
    assert (rec["old"], rec["new"], rec["agree"]) == ("r0", "r0", True)
    assert rec["old_keys"] == ["r0"] and rec["matched"] == ["r0", "r1"]
    assert (rec["agree_main"], rec["agree_set"]) == (True, False)
    assert rec["p_r0"] == 0.9 and rec["p_old"] == 0.9
    assert rec["best"] == "r0" and rec["main"] == "r0"
    assert rec["options"] == len(RULES)
    assert SECRET_RULE not in repr(rec)


# ── Done when: the member comes from job_member_scope, proven ───────────────


async def test_the_member_comes_from_the_job_scope_and_is_proven(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, "email.cold_check=shadow", ORG)
    fake = _fake(monkeypatch)
    with job_member_scope("owner@acme.com", app="email"):
        await _site_cold(monkeypatch)
    call = fake.calls[0]
    assert call["member"] == "owner@acme.com"
    assert call["member_proven"] is True
    assert call["module_slug"] == "email"


async def test_with_no_scope_there_is_no_member(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.cold_check=shadow", ORG)
    fake = _fake(monkeypatch)
    await _site_cold(monkeypatch)
    call = fake.calls[0]
    assert call["member"] is None
    assert call["member_proven"] is False


async def test_the_member_is_never_read_from_the_mail(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.cold_check=shadow", ORG)
    fake = _fake(monkeypatch)
    with job_member_scope("owner@acme.com", app="email"):
        await _site_cold(monkeypatch)
    assert fake.calls[0]["member"] != SECRET_SENDER


# ── EM-T5b-1: one boolean for each rule, and no skip above 254 ──────────────


@pytest.mark.parametrize("n", [1, 2, 7, 254])
async def test_each_rule_asks_its_own_boolean(monkeypatch, tenant, n) -> None:
    """EM-T5 asked one choice of N + 1. EM-T5b-1 asks one boolean `r<i>` for
    each rule, and the choice `best` only ranks (§10.4.8 items 2 and 4)."""
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, eng, {"index": -1})
    rules = [{"id": str(i), "name": f"R{i}", "instructions": "x"} for i in range(n)]
    await eng._llm_pick_rule(EMAIL, rules, account_id=ACC)
    asked: dict[str, Any] = {}
    for call in fake.calls:
        asked.update(call["questions"])
    booleans = sorted(k for k, q in asked.items() if q.type == "boolean")
    assert booleans == sorted(f"r{i}" for i in range(n))
    assert ("best" in asked) is (n >= 2)


async def test_above_254_rules_decide_still_runs_without_best(monkeypatch, tenant) -> None:
    """EM-T5 skipped `decide` above 254 rules. Booleans have no option limit,
    so EM-T5b-1 asks, and only `best` is left out."""
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, eng, {"index": 3, "reason": "r"})
    rules = [{"id": str(i), "name": f"R{i}", "instructions": "x"} for i in range(255)]
    with structlog.testing.capture_logs() as caps:
        out = await eng._llm_pick_rule(EMAIL, rules, account_id=ACC)
    assert out == {"index": 3, "reason": "r"}
    assert fake.calls
    assert not any("best" in c["questions"] for c in fake.calls)
    assert _records(caps, "decide.shadow_skipped") == []


async def test_each_criterion_is_clipped_to_1000_characters(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, eng, {"index": -1})
    rules = [{"id": "1", "name": "Long", "instructions": "y" * 5000}]
    guidance = {"1": ["z" * 3000]}
    await eng._llm_pick_rule(EMAIL, rules, guidance=guidance, account_id=ACC)
    (question,) = fake.calls[0]["questions"].values()
    assert all(len(v) <= 1000 for v in question.criteria.values())
    assert len(question.criteria["true"]) == 1000


# ── Done when: FYI only when the user did not send last ─────────────────────


@pytest.mark.parametrize(("user_sent_last", "has_fyi"), [(True, False), (False, True)])
async def test_fyi_is_an_option_only_when_the_user_did_not_send_last(
    monkeypatch, tenant, user_sent_last, has_fyi
) -> None:
    _modes(monkeypatch, "email.thread_status=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, rz, {"status": "DONE"})
    side = "owner" if user_sent_last else "other_party"
    await rz._llm_determine_thread_status(
        "thread", "owner@acme.com", "", user_sent_last=user_sent_last,
        account_id=ACC, thread_messages=[{**THREAD[0], "side": side}],
    )
    (question,) = fake.calls[0]["questions"].values()
    assert ("FYI" in question.criteria) is has_fyi
    assert {"REPLY", "AWAITING_REPLY", "DONE"} <= set(question.criteria)
    state = fake.calls[0]["state"]
    assert (state["last_message_side"] == "other_party") is has_fyi


async def test_a_status_call_with_no_messages_is_skipped(monkeypatch, tenant) -> None:
    """The state is the thread as facts. A caller that has only the old text
    gets the old answer, and `decide` is not asked (EM-T5b-1)."""
    _modes(monkeypatch, "email.thread_status=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, rz, {"status": "DONE"})
    with structlog.testing.capture_logs() as caps:
        out = await rz._llm_determine_thread_status(
            "thread", "owner@acme.com", "", user_sent_last=False, account_id=ACC)
    assert out == ("DONE", True)
    assert fake.calls == []
    assert _records(caps, "decide.shadow_skipped")


# ── Done when: a slow decide delays the old answer by at most the bound ─────


async def test_the_default_bound_is_five_seconds() -> None:
    assert df.SHADOW_BOUND_S == 5.0


@pytest.mark.parametrize("feature", list(SITES))
async def test_a_slow_decide_does_not_hold_the_old_answer_past_the_bound(
    monkeypatch, tenant, feature
) -> None:
    monkeypatch.setattr(df, "SHADOW_BOUND_S", 0.2)
    _modes(monkeypatch, f"{feature}=shadow", ORG)
    _fake(monkeypatch, delay=10.0)
    started = time.monotonic()
    with structlog.testing.capture_logs() as caps:
        got, old = await SITES[feature](monkeypatch)
    elapsed = time.monotonic() - started
    assert got == old
    assert elapsed < 0.2 + 0.8, f"the old answer waited {elapsed:.2f}s"
    rec = _records(caps, "decide.fallback")
    assert rec and rec[0]["decide_reason"] == "timeout"


# ── The comparison helpers ──────────────────────────────────────────────────


def test_compare_boolean_reads_the_threshold() -> None:
    cmp = df.compare_boolean(lambda r: r, qid="q", threshold=0.9)
    d: Mapping[str, Any] = {"q": decide_mod.BooleanAnswer(probability=0.95)}
    out = cmp(True, d)
    assert (out.old, out.new, out.agree, out.options) == (True, True, True, 2)
    assert out.probability == 0.95 and out.confidence is None


# ── The limits match the Console's own ─────────────────────────────────────


def test_the_limits_match_the_console() -> None:
    """``decide_features`` copies the Console's option limit, because the
    gateway may not import ``customer_console``
    (``test_console_dependency_boundary.py``). A TEST may import it, so this
    pins the copy to the source of record."""
    from customer_console.decide import (
        MAX_CHOICE_OPTIONS,
        MAX_CRITERION_CHARS,
        MAX_INSTRUCTIONS_CHARS,
        MAX_QUESTIONS,
    )

    assert df.CHOICE_OPTION_LIMIT == MAX_CHOICE_OPTIONS
    assert df.QUESTION_LIMIT == MAX_QUESTIONS
    assert df.CRITERION_CLIP <= MAX_CRITERION_CHARS
    assert df.CORRECTIONS_CLIP < MAX_INSTRUCTIONS_CHARS
