"""EM-T5b-1: the email triage questions, rebuilt to the System One conventions.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.8 ("EM-T5b-1 —
the questions, rebuilt, in shadow"), and the "Question conventions" table of
``project-docs/specs/customer_console.md`` §6A.14. One test, or one
parametrised test, for each "Done when" line, plus the fences the audit asked
for: the persona strings, the key names in the instructions, the 16-question
split and the Console validator.

The rules these pin:

- The rule match asks ONE BOOLEAN for each candidate, a choice ``conv`` over
  the conversation rules, and a choice ``best`` that only ranks.
- A state is an object of FACTS. It holds no persona, no command and no
  question. The rubric is in the criteria and the instructions.
- No instruction names an option key, because the model never sees one.
- Every request passes the Console's own validator, because a refused
  request is a lost decision.
- The log holds keys, numbers and the message id, never tenant text.

Everything stays in SHADOW: the old LLM answer is the one acted on. Every
test uses a FAKE ``acb_llm.decide``. No test reaches a network.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import acb_llm as decide_mod
import pytest
import structlog
from acb_common import get_settings
from acb_common._log import clear_run_context, run_context_scope
from acb_common.db import bind_tenant, release_tenant
from gateway import decide_features as df
from gateway.routes.email.automation import cleanup as cln
from gateway.routes.email.automation import engine as eng
from gateway.routes.email.automation import learning as lrn
from gateway.routes.email.automation import replyzero as rz
from gateway.routes.email.automation import rules as rules_mod
from gateway.routes.email.automation import senders as snd

ORG = "11111111-1111-1111-1111-111111111111"
ACC = "acc-questions-1"
MID = "msg-777"

ROOT = Path(__file__).resolve().parents[2]
AUTOMATION = ROOT / "apps/services/gateway/gateway/routes/email/automation"

# Tenant text. None of it may reach a decide.* log record.
SECRET_SUBJECT = "SECRET-SUBJECT-quarterly-pricing"
SECRET_BODY = "SECRET-BODY-please-send-the-quote"
SECRET_SENDER = "secret.sender@vendor.example"
SECRET_RULE = "SECRET-RULE-Newsletter"
SECRET_ABOUT = "SECRET-ABOUT-head-of-procurement"
SECRET_OWNER = "secret.owner@acme.example"

#: Strings of the old prompts that make a state a command, not facts.
PERSONA = ("You are", "Respond", "Determine", "Choose", "acting on behalf")

#: What no instructions text may name (§10.4.8 "The instructions", A2).
KEYS = ("REPLY", "AWAITING_REPLY", "DONE", "FYI", "r0", "none")

EMAIL = {
    "from": SECRET_SENDER, "from_name": "Vendor", "to": SECRET_OWNER,
    "cc": "", "subject": SECRET_SUBJECT, "body": SECRET_BODY,
    "self": SECRET_OWNER, "self_name": "Owner", "about": SECRET_ABOUT,
    "date": "2026-10-02T09:00:00+00:00", "sender_scope": "external",
    "recipient_role": "direct", "attachments": "",
}


# ── Rule fixtures ───────────────────────────────────────────────────────────


def _presets() -> list[dict[str, Any]]:
    """The 10 preset rules, as `_load_rules` returns them, with the Outlook
    actions (a cleanup rule moves mail there)."""
    return [
        {"id": f"p{i}", "name": p["name"], "instructions": p["instructions"],
         "system_type": None, "enabled": True,
         "actions": rules_mod._actions_for_preset(p, "microsoft")}
        for i, p in enumerate(rules_mod._PRESET_RULES)
    ]


def _cleanup(n: int, text: str = "x") -> list[dict[str, Any]]:
    return [{"id": f"c{i}", "name": f"Cleanup {i}", "instructions": text,
             "system_type": None, "actions": [{"type": "LABEL"}]}
            for i in range(n)]


def _conversation() -> list[dict[str, Any]]:
    return [{"id": f"v{i}", "name": n, "instructions": "conversation",
             "system_type": k, "actions": [{"type": "LABEL"}]}
            for i, (n, k) in enumerate([
                ("Needs Reply", "REPLY"), ("Awaiting Reply", "AWAITING_REPLY"),
                ("Done", "DONE"), ("FYI", "FYI")])]


def _questions(requests: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for _state, questions in requests:
        out.update(questions)
    return out


# ── The fake `decide` ───────────────────────────────────────────────────────


class FakeDecide:
    """Records each call. A boolean answers ``p`` (or ``by_key[qid]``). A
    choice answers ``choices[qid]``, else ``none`` when it is an option."""

    def __init__(self, *, p: float = 0.0, by_key: dict[str, float] | None = None,
                 choices: dict[str, str] | None = None,
                 raises: BaseException | None = None,
                 barrier: int = 0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.p = p
        self.by_key = by_key or {}
        self.choices = choices or {}
        self.raises = raises
        self.barrier = barrier
        self._all_started = asyncio.Event()

    async def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": questions, **kwargs})
        number = len(self.calls)
        self.events.append("start")
        if self.barrier:
            if len(self.calls) >= self.barrier:
                self._all_started.set()
            # In sequence, the first call waits here for ever and the bound
            # turns it into a timeout. Only calls that run at the same time
            # all pass.
            await asyncio.wait_for(self._all_started.wait(), timeout=2.0)
        self.events.append("end")
        if self.raises is not None:
            raise self.raises
        answers: dict[str, Any] = {}
        for qid, q in questions.items():
            if q.type == "boolean":
                answers[qid] = decide_mod.BooleanAnswer(
                    probability=self.by_key.get(qid, self.p))
            else:
                keys = list(q.criteria)
                choice = self.choices.get(qid) or ("none" if "none" in keys else keys[-1])
                answers[qid] = decide_mod.ChoiceAnswer(
                    choice=choice,
                    probabilities=MappingProxyType(
                        {k: (0.8 if k == choice else 0.2 / max(1, len(keys) - 1))
                         for k in keys}),
                    confidence=0.6,
                )
        return decide_mod.Decision(
            answers=MappingProxyType(answers), request_id=f"req-{number}")


def _decision(by_key: dict[str, float] | None = None,
              choices: dict[str, str] | None = None) -> Any:
    """A merged `Decision` for `_read_rule_match`, built by hand."""
    answers: dict[str, Any] = {k: decide_mod.BooleanAnswer(probability=v)
                               for k, v in (by_key or {}).items()}
    for qid, key in (choices or {}).items():
        answers[qid] = decide_mod.ChoiceAnswer(
            choice=key, probabilities=MappingProxyType({key: 0.9}), confidence=0.7)
    return decide_mod.Decision(answers=MappingProxyType(answers), request_id="req-x")


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("DECIDE_FEATURE_MODES", "")
    monkeypatch.setenv("DECIDE_FEATURE_ORGS", "")
    _clear_caches()
    with run_context_scope():
        clear_run_context()
        yield
    monkeypatch.undo()
    _clear_caches()


def _clear_caches() -> None:
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


def _llm(monkeypatch, module, data: Any) -> None:
    async def fake(model, messages, **kw):
        return data, "{}", model

    monkeypatch.setattr(module, "_llm_json", fake)


def _records(caps, event: str) -> list[dict[str, Any]]:
    return [c for c in caps if c.get("event") == event]


ALL_SHADOW = ",".join(f"{f}=shadow" for f in df.FEATURES)

#: The thread as facts, the other party last.
THREAD = [
    {"side": "owner", "from": "Owner", "to": "", "cc": "", "owner_cc_only": False,
     "date": "", "subject": SECRET_SUBJECT, "attachments": "",
     "body": "Can you send the quote?"},
    {"side": "other_party", "from": SECRET_SENDER, "to": SECRET_OWNER, "cc": "",
     "owner_cc_only": False, "date": "", "subject": SECRET_SUBJECT,
     "attachments": "", "body": SECRET_BODY},
]


def _pin_rows(n: int = 4, subject: str = SECRET_SUBJECT,
              snippet: str = SECRET_BODY) -> list[Any]:
    return [SimpleNamespace(subject=subject, snippet=snippet)] * n


def _pin_db(rows: list[Any] | None = None) -> AsyncMock:
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchall=MagicMock(return_value=rows if rows is not None else _pin_rows()))
    return db


# The five call sites of the four features, each with an old LLM fake.


async def _cold(monkeypatch, message_id: str | None = MID):
    _llm(monkeypatch, snd, {"cold": True, "reason": "r"})
    return await snd._llm_is_cold(EMAIL, account_id=ACC, message_id=message_id)


async def _pin(monkeypatch, message_id: str | None = MID):
    _llm(monkeypatch, lrn, {"always": True, "why": "w"})
    rule = {"id": "1", "name": SECRET_RULE, "instructions": "newsletters"}
    return await lrn._ai_confirms_sender_pattern(
        _pin_db(), ACC, SECRET_SENDER, rule, message_id=message_id)


async def _status(monkeypatch, message_id: str | None = MID):
    _llm(monkeypatch, rz, {"status": "REPLY", "rationale": "x"})
    return await rz._llm_determine_thread_status(
        "thread", SECRET_OWNER, SECRET_ABOUT, user_sent_last=False,
        account_id=ACC, thread_messages=THREAD, message_id=message_id)


_RULES = [{"id": "1", "name": SECRET_RULE, "instructions": "newsletters",
           "actions": [{"type": "LABEL"}]},
          {"id": "2", "name": "Receipt", "instructions": "receipts",
           "actions": [{"type": "LABEL"}]}]


async def _rule(monkeypatch, message_id: str | None = MID):
    _llm(monkeypatch, eng, {"index": 0, "reason": "x"})
    return await eng._llm_pick_rule(
        EMAIL, _RULES, account_id=ACC, message_id=message_id,
        history=[{"rule": SECRET_RULE, "count": 2}])


async def _rules_multi(monkeypatch, message_id: str | None = MID):
    _llm(monkeypatch, eng, {"matches": [{"index": 0, "reason": "x", "primary": True}]})
    return await eng._llm_pick_rules(
        EMAIL, _RULES, account_id=ACC, message_id=message_id)


SITES = (_cold, _pin, _status, _rule, _rules_multi)


# ── Done when: every mode off → zero calls on all four features ─────────────


async def test_every_mode_off_makes_zero_calls_on_all_four_features(
    monkeypatch, tenant
) -> None:
    fake = _fake(monkeypatch, p=0.9)
    for site in SITES:
        await site(monkeypatch)
    assert fake.calls == []
    assert set(df.FEATURES) == {
        "email.cold_check", "email.sender_pin", "email.thread_status",
        "email.rule_match"}


# ── Done when: the old name resolves to off and is refused ──────────────────


async def test_the_old_rule_pick_name_resolves_to_off_and_is_refused(
    monkeypatch, tenant
) -> None:
    with structlog.testing.capture_logs() as caps:
        _modes(monkeypatch, "email.rule_pick=shadow", ORG)
        assert df.mode_for("email.rule_pick") == "off"
        assert df.mode_for("email.rule_match") == "off"
    fake = _fake(monkeypatch)
    await _rule(monkeypatch)
    assert fake.calls == []
    rec = _records(caps, "decide.mode_refused")
    assert rec and rec[0]["decide_feature"] == "email.rule_pick"
    assert rec[0]["decide_reason"] == "unknown"


# ── Done when: the 10 presets → 6 booleans, conv 5, best 11, one request ────


def test_the_ten_presets_ask_one_request_of_six_booleans_conv_and_best() -> None:
    requests = eng._rule_match_requests(EMAIL, _presets())
    assert len(requests) == 1
    (_state, questions), = requests
    booleans = [k for k, q in questions.items() if q.type == "boolean"]
    assert len(booleans) == 6
    assert list(questions["conv"].criteria) == ["r0", "r1", "r2", "r3", "none"]
    assert len(questions["best"].criteria) == 11
    assert list(questions["best"].criteria)[-1] == "none"
    assert questions["conv"].type == questions["best"].type == "choice"


# ── Done when: 20 cleanup rules → 2 requests, both at the same time ─────────


def test_twenty_cleanup_rules_split_into_two_requests_with_best_first() -> None:
    requests = eng._rule_match_requests(EMAIL, _cleanup(20))
    assert [len(q) for _s, q in requests] == [16, 5]
    assert "best" in requests[0][1]
    assert all("best" not in q for _s, q in requests[1:])


def test_conv_and_best_both_ride_in_the_first_request() -> None:
    requests = eng._rule_match_requests(EMAIL, _conversation() + _cleanup(20))
    assert [len(q) for _s, q in requests] == [16, 6]
    assert {"conv", "best"} <= set(requests[0][1])
    assert all(len(q) <= df.QUESTION_LIMIT for _s, q in requests)
    # One state for every request of the email.
    assert all(s is requests[0][0] for s, _q in requests)


async def test_the_two_requests_run_at_the_same_time(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    fake = _fake(monkeypatch, barrier=2)
    _llm(monkeypatch, eng, {"index": 0, "reason": "x"})
    with structlog.testing.capture_logs() as caps:
        await eng._llm_pick_rule(EMAIL, _cleanup(20), account_id=ACC, message_id=MID)
    assert len(fake.calls) == 2
    assert fake.events == ["start", "start", "end", "end"]
    rec = _records(caps, "decide.shadow")
    assert len(rec) == 1
    assert rec[0]["requests"] == 2 and rec[0]["questions"] == 21
    assert rec[0]["request_ids"] == ["req-1", "req-2"]


async def test_one_failed_request_means_no_decision(monkeypatch, tenant) -> None:
    """§10.4.8 item 7: a partial set of answers is not a decision."""
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    calls: list[int] = []

    async def half(state, questions, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise decide_mod.DecideUnavailable("HTTP 503")
        return decide_mod.Decision(answers=MappingProxyType({
            k: (decide_mod.BooleanAnswer(probability=0.9) if q.type == "boolean"
                else decide_mod.ChoiceAnswer(choice="none", probabilities={}, confidence=None))
            for k, q in questions.items()}), request_id="r")

    monkeypatch.setattr(decide_mod, "decide", half)
    _llm(monkeypatch, eng, {"index": 0, "reason": "x"})
    with structlog.testing.capture_logs() as caps:
        out = await eng._llm_pick_rule(EMAIL, _cleanup(20), account_id=ACC, message_id=MID)
    assert out == {"index": 0, "reason": "x"}
    assert _records(caps, "decide.shadow") == []
    rec = _records(caps, "decide.fallback")
    assert len(rec) == 1 and rec[0]["decide_reason"] == "HTTP 503"
    assert rec[0]["message_id"] == MID


# ── Done when: every request passes the Console's own validator ────────────


def _long_email() -> dict[str, str]:
    return {**EMAIL, "body": "b" * 20_000, "about": "a" * 5_000,
            "to": "t" * 10_000, "cc": "c" * 10_000, "subject": "s" * 3_000,
            "attachments": "f" * 3_000, "from_name": "n" * 1_000}


def _refusal(state: Any, questions: dict[str, Any]) -> str | None:
    from customer_console.decide import DecideRequest, decide_refusal

    req = DecideRequest(tier="tier-decide", state=state, questions={
        qid: {"type": q.type, "instructions": q.instructions,
              "criteria": dict(q.criteria)}
        for qid, q in questions.items()})
    return decide_refusal(req)


@pytest.mark.parametrize("n", [1, 15, 16, 17, 254, 255, 300])
def test_every_rule_match_request_passes_the_console_validator(n) -> None:
    rules = (_conversation() + _cleanup(n - 4, "y" * 2_000)) if n >= 4 else _cleanup(n, "y" * 2_000)
    guidance = {"": [f"account-wide correction {i} " + "g" * 200 for i in range(50)]}
    history = [{"rule": "r" * 500, "count": 9}] * 5
    requests = eng._rule_match_requests(_long_email(), rules, guidance, history)
    asked = _questions(requests)
    assert len([q for q in asked.values() if q.type == "boolean"]) + (
        len(asked["conv"].criteria) - 1 if "conv" in asked else 0) == n
    for state, questions in requests:
        assert len(questions) <= df.QUESTION_LIMIT
        assert _refusal(state, questions) is None


def test_a_long_rule_many_corrections_and_a_long_body_pass_the_validator() -> None:
    rules = [{"id": "1", "name": "Long", "instructions": "y" * 10_000,
              "actions": [{"type": "MOVE_FOLDER"}]}, *_cleanup(3)]
    guidance = {"": [f"note {i} " + "g" * 300 for i in range(50)],
                "1": [f"rule note {i} " + "h" * 300 for i in range(50)]}
    for state, questions in eng._rule_match_requests(_long_email(), rules, guidance):
        assert _refusal(state, questions) is None


def test_the_other_three_questions_pass_the_validator() -> None:
    long_thread = [{**THREAD[1], "body": "b" * 1_500, "to": "t" * 1_000}] * 50
    block = "\n\nCORRECTIONS:\n" + "\n".join(f"- note {i} " + "g" * 300 for i in range(50))
    requests = [
        snd._cold_question(_long_email()),
        lrn._sender_pin_question(
            "x" * 400 + "@gmail.com", {"name": "N" * 500, "instructions": "y" * 10_000},
            _pin_rows(10, "s" * 1_000, "p" * 1_000)),
        rz._status_question(long_thread, SECRET_OWNER, "a" * 5_000,
                            user_sent_last=False, corrections=block),
    ]
    for state, questions in requests:
        assert _refusal(state, questions) is None


# ── Done when: above 254 candidates, no `best` ──────────────────────────────


@pytest.mark.parametrize(("n", "has_best"), [(1, False), (2, True), (254, True),
                                             (255, False), (300, False)])
def test_best_runs_only_for_two_to_254_candidates(n, has_best) -> None:
    asked = _questions(eng._rule_match_requests(EMAIL, _cleanup(n)))
    assert ("best" in asked) is has_best
    if has_best:
        assert len(asked["best"].criteria) == n + 1


# ── Done when: each state is an object of facts, no persona ────────────────


def _all_states() -> dict[str, dict[str, Any]]:
    rule_state = eng._rule_match_requests(EMAIL, _presets())[0][0]
    cold_state = snd._cold_question(EMAIL)[0]
    status_state = rz._status_question(
        THREAD, SECRET_OWNER, SECRET_ABOUT, user_sent_last=False)[0]
    pin_state = lrn._sender_pin_question(
        SECRET_SENDER, {"name": SECRET_RULE, "instructions": "newsletters"},
        _pin_rows())[0]
    return {"rule": rule_state, "cold": cold_state, "status": status_state,
            "pin": pin_state}


def test_no_state_holds_a_persona_a_command_or_a_question() -> None:
    for name, state in _all_states().items():
        assert isinstance(state, dict), name
        text = json.dumps(state)
        for persona in PERSONA:
            assert persona not in text, (name, persona)


def test_each_state_has_the_listed_keys() -> None:
    states = _all_states()
    assert set(states["rule"]) == {"email", "direction", "mailbox_owner", "sender_history"}
    assert set(states["rule"]["email"]) == {
        "from", "to", "cc", "date", "subject", "attachments", "body"}
    assert set(states["rule"]["mailbox_owner"]) == {
        "address", "name", "recipient_role", "about"}
    assert set(states["cold"]) == {"email", "direction", "sender"}
    assert states["cold"]["sender"] == {"prior_contact": False}
    assert set(states["status"]) == {
        "thread", "earlier_messages_omitted", "last_message_side", "mailbox_owner"}
    assert set(states["status"]["thread"][0]) == {
        "side", "from", "to", "cc", "owner_cc_only", "date", "subject",
        "attachments", "body"}
    assert set(states["pin"]) == {"sender", "recent_messages"}
    assert set(states["pin"]["sender"]) == {
        "address", "domain", "public_mail_domain", "automated_local_part"}


@pytest.mark.parametrize(("scope", "direction"), [
    ("external", "received"), ("self", "sent_by_owner"),
    ("internal", "sent_by_owner_organisation"), ("", "received")])
def test_direction_maps_the_sender_scope(scope, direction) -> None:
    state = eng._rule_state({**EMAIL, "sender_scope": scope}, None)
    assert state["direction"] == direction


# ── Done when: the owner facts are in the state, corrections are not ───────


def test_the_rule_state_holds_the_owner_facts_and_no_correction() -> None:
    email = {**EMAIL, "recipient_role": "cc", "sender_scope": "internal"}
    guidance = {"": ["OLD NOTE outbound invoices", "NEW NOTE vendor digests"]}
    history = [{"rule": "Newsletter", "count": 4}, {"rule": "FYI", "count": 1}]
    requests = eng._rule_match_requests(email, _presets(), guidance, history)
    state = requests[0][0]
    assert state["mailbox_owner"]["about"] == SECRET_ABOUT
    assert state["mailbox_owner"]["recipient_role"] == "cc"
    assert state["direction"] == "sent_by_owner_organisation"
    assert state["sender_history"] == history
    text = json.dumps(state)
    assert "OLD NOTE" not in text and "NEW NOTE" not in text
    for qid, question in _questions(requests).items():
        # The account-wide corrections, newest first, in every question.
        assert "- NEW NOTE vendor digests\n- OLD NOTE outbound invoices" \
            in question.instructions, qid


@pytest.mark.parametrize(("role", "fact"), [("direct", "to"), ("cc", "cc"), ("", "other")])
def test_recipient_role_maps_to_a_fact(role, fact) -> None:
    state = eng._rule_state({**EMAIL, "recipient_role": role}, None)
    assert state["mailbox_owner"]["recipient_role"] == fact


def test_the_corrections_are_clipped_to_1500_characters() -> None:
    guidance = {"": ["n" * 400] * 20}
    (_s, questions), = eng._rule_match_requests(EMAIL, _cleanup(2), guidance)
    for question in questions.values():
        tail = question.instructions.split(
            "Corrections from the user. They override the guidance:\n", 1)[1]
        assert len(tail) == df.CORRECTIONS_CLIP


# ── Done when: the rubric of `_CLASSIFIER_GUIDELINES` is in the instructions ─


def test_the_classifier_rubric_reaches_the_instructions() -> None:
    """Most specific wins, excludes, DIRECTION MATTERS, reply only when
    needed, and the CC role: each one is a guidance line now."""
    asked = _questions(eng._rule_match_requests(EMAIL, _presets()))
    rule_q = asked["r4"].instructions
    for line in ("excludes some emails", "`direction` is not \"received\"",
                 "receipts, newsletters, marketing, cold outreach",
                 "asks the owner for a response",
                 "`mailbox_owner.recipient_role` is \"cc\"",
                 "`mailbox_owner.about`", "`sender_history` is a hint only"):
        assert line in rule_q, line
    best_q = asked["best"].instructions
    for line in ("most specific", "catch-all", "excludes some emails",
                 "`direction` is not \"received\"", "asks the owner for a response"):
        assert line in best_q, line
    conv_q = asked["conv"].instructions
    for line in ("part of an exchange", "one-way mail",
                 "`direction` is not \"received\"", "asks the owner for a response"):
        assert line in conv_q, line
    assert asked["r4"].criteria["false"] == eng._RULE_FALSE


def test_each_boolean_criterion_holds_the_rule_and_its_corrections() -> None:
    rules = _cleanup(2)
    guidance = {"c1": ["Vendor digests belong here."]}
    asked = _questions(eng._rule_match_requests(EMAIL, rules, guidance))
    assert asked["r1"].criteria["true"] == (
        "Cleanup 1: x\n- correction from the user: Vendor digests belong here.")
    assert asked["best"].criteria["r1"] == asked["r1"].criteria["true"]


# ── Done when: `_read_rule_match` ──────────────────────────────────────────


def test_probabilities_of_0_7_and_0_2_match_r0_only() -> None:
    match = eng._read_rule_match(_decision({"r0": 0.7, "r1": 0.2}, {"best": "none"}),
                                 _cleanup(2))
    assert match.matched == (0,)
    assert match.main == 0


def test_a_best_answer_that_did_not_match_falls_to_the_highest_probability() -> None:
    match = eng._read_rule_match(
        _decision({"r0": 0.6, "r1": 0.9, "r2": 0.1}, {"best": "r2"}), _cleanup(3))
    assert match.matched == (0, 1)
    assert match.main == 1


def test_best_ranks_the_matched_rules() -> None:
    match = eng._read_rule_match(
        _decision({"r0": 0.6, "r1": 0.9}, {"best": "r0"}), _cleanup(2))
    assert match.main == 0


def test_a_tie_goes_to_the_canonical_order() -> None:
    match = eng._read_rule_match(
        _decision({"r0": 0.8, "r1": 0.8, "r2": 0.8}, {"best": "none"}), _cleanup(3))
    assert match.main == 0


def test_all_under_the_threshold_is_no_match_in_both_modes() -> None:
    match = eng._read_rule_match(
        _decision({"r0": 0.49, "r1": 0.1}, {"best": "r0"}), _cleanup(2))
    assert match.matched == () and match.main is None
    assert match.as_pick() is None
    assert match.as_picks() == []


@pytest.mark.parametrize(("action", "p", "matched"), [
    ("MOVE_FOLDER", 0.6, False), ("MOVE_FOLDER", 0.7, True),
    ("ARCHIVE", 0.69, False), ("TRASH", 0.6, False), ("MARK_SPAM", 0.6, False),
    ("LABEL", 0.5, True), ("LABEL", 0.49, False)])
def test_a_rule_that_moves_mail_needs_0_7(action, p, matched) -> None:
    rules = [{"id": "1", "name": "R", "instructions": "x",
              "actions": [{"type": "LABEL"}, {"type": action}]}]
    match = eng._read_rule_match(_decision({"r0": p}), rules)
    assert (match.matched == (0,)) is matched


def test_conv_matches_its_answer_unless_none() -> None:
    rules = _conversation() + _cleanup(1)
    hit = eng._read_rule_match(
        _decision({"r4": 0.1}, {"conv": "r1", "best": "none"}), rules)
    assert hit.matched == (1,) and hit.main == 1
    miss = eng._read_rule_match(
        _decision({"r4": 0.1}, {"conv": "none", "best": "r1"}), rules)
    assert miss.matched == ()


def test_the_return_shapes_and_the_reason() -> None:
    match = eng._read_rule_match(
        _decision({"r0": 0.834, "r1": 0.6}, {"best": "r1"}), _cleanup(2))
    assert match.as_pick() == {"index": 1, "reason": "Matched by AI (probability 0.60)."}
    assert match.as_picks() == [
        {"index": 0, "reason": "Matched by AI (probability 0.83).", "primary": False},
        {"index": 1, "reason": "Matched by AI (probability 0.60).", "primary": True},
    ]


# ── Done when: multi-rule runs in shadow, and the LLM answer stands ────────


async def test_multi_rule_in_shadow_makes_one_request_and_returns_the_llm_answer(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    fake = _fake(monkeypatch, p=0.9, choices={"best": "r1"})
    with structlog.testing.capture_logs() as caps:
        out = await _rules_multi(monkeypatch)
    assert out == [{"index": 0, "reason": "x", "primary": True}]
    assert len(fake.calls) == 1
    rec = _records(caps, "decide.shadow")
    assert len(rec) == 1
    assert rec[0]["decide_feature"] == "email.rule_match"
    assert rec[0]["old_keys"] == ["r0"]
    assert rec[0]["matched"] == ["r0", "r1"]
    assert (rec[0]["old"], rec[0]["new"]) == ("r0", "r1")
    assert rec[0]["agree"] is False and rec[0]["agree_set"] is False


async def test_an_llm_outage_still_raises_in_multi_rule_shadow(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    _fake(monkeypatch, p=0.9)

    async def boom(*a, **kw):
        raise RuntimeError("gateway 502")

    monkeypatch.setattr(eng, "_llm_json", boom)
    with pytest.raises(eng.LLMUnavailable):
        await eng._llm_pick_rules(EMAIL, _RULES, account_id=ACC)


# ── Done when: no tenant text in a log line, and each holds message_id ─────


async def test_no_log_line_holds_tenant_text_and_each_holds_the_message_id(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, ALL_SHADOW, ORG)
    _fake(monkeypatch, p=0.9)
    with structlog.testing.capture_logs() as caps:
        for site in SITES:
            await site(monkeypatch)
    _fake(monkeypatch, raises=decide_mod.DecideUnavailable("HTTP 503"))
    with structlog.testing.capture_logs() as down:
        for site in SITES:
            await site(monkeypatch)
    lines = [c for c in caps + down if c["event"].startswith("decide.")]
    assert len(_records(caps, "decide.shadow")) == len(SITES)
    assert len(_records(down, "decide.fallback")) == len(SITES)
    for line in lines:
        assert line["message_id"] == MID, line["event"]
    text = repr(lines)
    for secret in (SECRET_SUBJECT, SECRET_BODY, SECRET_SENDER, SECRET_RULE,
                   SECRET_ABOUT, SECRET_OWNER):
        assert secret not in text, secret


async def test_the_rule_log_holds_each_probability_under_its_key(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    _fake(monkeypatch, by_key={"r4": 0.81, "r5": 0.12}, choices={"conv": "r0", "best": "r4"})
    _llm(monkeypatch, eng, {"index": 4, "reason": "x"})
    with structlog.testing.capture_logs() as caps:
        await eng._llm_pick_rule(EMAIL, _presets(), account_id=ACC, message_id=MID)
    (rec,) = _records(caps, "decide.shadow")
    assert rec["p_r4"] == 0.81 and rec["p_r5"] == 0.12
    assert rec["conv"] == "r0" and rec["best"] == "r4"
    assert rec["conv_confidence"] == 0.6 and rec["conv_margin"] is not None
    assert rec["best_confidence"] == 0.6 and rec["best_margin"] is not None
    assert rec["matched"] == ["r0", "r4"] and rec["main"] == "r4"
    assert rec["p_old"] == 0.81 and rec["agree_main"] is True
    assert rec["rules"] == 10


# ── Done when: the thread status criteria copy the old rubric ──────────────


def test_the_status_criteria_copy_the_old_rubric() -> None:
    _state, questions = rz._status_question(
        THREAD, SECRET_OWNER, "", user_sent_last=False)
    criteria = questions["status"].criteria
    assert "promised a follow-up" in criteria["REPLY"]
    assert "the OTHER person's court" in criteria["AWAITING_REPLY"]
    assert "Taking ownership" in criteria["DONE"]
    assert "only on Cc" in criteria["FYI"]


@pytest.mark.parametrize(("user_sent_last", "side", "has_fyi"), [
    (False, "other_party", True), (True, "owner", False),
    (True, "owner_organisation", False)])
def test_fyi_is_an_option_only_when_the_other_party_sent_last(
    user_sent_last, side, has_fyi
) -> None:
    thread = [*THREAD[:1], {**THREAD[1], "side": side}]
    state, questions = rz._status_question(
        thread, SECRET_OWNER, "", user_sent_last=user_sent_last)
    assert state["last_message_side"] == side
    assert ("FYI" in questions["status"].criteria) is has_fyi


def test_the_thread_keeps_its_newest_messages_within_the_budget() -> None:
    thread = [{**THREAD[1], "body": f"{i} " + "b" * 1_400} for i in range(20)]
    state = rz._status_state(thread, SECRET_OWNER, "", user_sent_last=False)
    kept = state["thread"]
    assert kept[-1]["body"].startswith("19 ")
    assert sum(len(json.dumps(m)) for m in kept) <= rz._THREAD_PROMPT_BUDGET
    assert state["earlier_messages_omitted"] == 20 - len(kept) > 0


def test_a_plain_corrections_text_falls_back_to_its_lines_newest_first() -> None:
    block = ("\n\nCORRECTIONS THE USER HAS MADE BEFORE (these override your "
             "default reading):\n- First note.\n- [Reply] Second note.")
    assert rz._status_correction_notes(block) == ["[Reply] Second note.", "First note."]
    assert rz._status_correction_notes("") == []


def _guidance_env(monkeypatch, guidance: dict[str, list[str]],
                  rules: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(eng, "_load_rule_guidance", AsyncMock(return_value=guidance))
    monkeypatch.setattr(rules_mod, "_load_rules", AsyncMock(return_value=rules))


_CONV_RULES = [
    {"id": "r-reply", "name": "Needs Reply", "system_type": None, "enabled": True},
    {"id": "r-fyi", "name": "FYI", "system_type": None, "enabled": True},
    {"id": "r-news", "name": "Newsletter", "system_type": None, "enabled": True},
]


async def test_the_old_block_text_does_not_change(monkeypatch) -> None:
    """The live prompt keeps its exact text: account-wide notes first, then
    each conversation-rule note with its `[<rule name>]` prefix."""
    _guidance_env(monkeypatch, {"": ["A1", "A2"], "r-fyi": ["F1"], "r-news": ["N1"]},
                  _CONV_RULES)
    block = await rz._status_corrections_block(AsyncMock(), ACC)
    assert block == ("\n\nCORRECTIONS THE USER HAS MADE BEFORE (these override "
                     "your default reading):\n- A1\n- A2\n- [FYI] F1")


async def test_the_status_notes_put_rule_notes_first_and_newest_first(monkeypatch) -> None:
    _guidance_env(monkeypatch, {"": ["A-old", "A-new"], "r-reply": ["R-old", "R-new"],
                                "r-fyi": ["F-only"]}, _CONV_RULES)
    block = await rz._status_corrections_block(AsyncMock(), ACC)
    assert rz._status_correction_notes(block) == [
        "R-new", "R-old", "F-only", "A-new", "A-old"]


async def test_the_newest_status_notes_survive_the_clip(monkeypatch) -> None:
    """The clip of 1500 characters cuts the end, so the oldest note goes."""
    notes = [f"NOTE-{i:02d} " + "x" * 290 for i in range(8)]  # NOTE-07 is newest
    _guidance_env(monkeypatch, {"": notes}, _CONV_RULES)
    block = await rz._status_corrections_block(AsyncMock(), ACC)
    _state, questions = rz._status_question(
        THREAD, SECRET_OWNER, "", user_sent_last=False, corrections=block)
    text = questions["status"].instructions
    assert "NOTE-07" in text and "NOTE-06" in text
    assert "NOTE-00" not in text


async def test_a_note_on_the_rule_named_fyi_names_no_key(monkeypatch) -> None:
    """The preset rule named "FYI" must not put that key into the decide
    instructions. The old prompt keeps its `[FYI]` prefix."""
    _guidance_env(monkeypatch, {"r-fyi": ["Supplier chasers want an answer."]},
                  _CONV_RULES)
    block = await rz._status_corrections_block(AsyncMock(), ACC)
    assert "[FYI] Supplier chasers want an answer." in block
    _state, questions = rz._status_question(
        THREAD, SECRET_OWNER, "", user_sent_last=False, corrections=block)
    text = questions["status"].instructions
    assert "Supplier chasers want an answer." in text
    for key in KEYS:
        assert key not in text, key


# ── Done when: no instructions text names an option key ────────────────────


def test_no_instructions_text_names_an_option_key() -> None:
    guidance = {"": ["Vendor digests count as newsletters."]}
    block = "\n\nCORRECTIONS:\n- Chasers from suppliers need an answer."
    questions: list[Any] = list(_questions(
        eng._rule_match_requests(EMAIL, _presets(), guidance)).values())
    questions += list(snd._cold_question(EMAIL)[1].values())
    questions += list(lrn._sender_pin_question(
        SECRET_SENDER, {"name": "Newsletter", "instructions": "x"}, _pin_rows())[1].values())
    for user_sent_last in (True, False):
        questions += list(rz._status_question(
            THREAD, SECRET_OWNER, "", user_sent_last=user_sent_last,
            corrections=block)[1].values())
    assert len(questions) >= 12
    for question in questions:
        for key in KEYS:
            assert key not in question.instructions, (key, question.instructions)


def test_each_instructions_text_is_a_question_then_guidance() -> None:
    asked = _questions(eng._rule_match_requests(EMAIL, _presets()))
    for qid, question in asked.items():
        first, second = question.instructions.split("\n")[:2]
        assert first.endswith("?"), qid
        assert second == "Guidance:", qid


# ── Done when: the pin state ───────────────────────────────────────────────


def test_the_pin_state_marks_a_public_mail_domain_from_the_shared_list() -> None:
    assert "gmail.com" in cln._SHARED_DOMAINS
    rule = {"name": SECRET_RULE, "instructions": "newsletters only"}
    state, questions = lrn._sender_pin_question("news@gmail.com", rule, _pin_rows())
    assert state["sender"] == {"address": "news@gmail.com", "domain": "gmail.com",
                               "public_mail_domain": True, "automated_local_part": True}
    other, _q = lrn._sender_pin_question("jo@acme-corp.example", rule, _pin_rows())
    assert other["sender"]["public_mail_domain"] is False
    assert other["sender"]["automated_local_part"] is False
    true = questions["always"].criteria["true"]
    assert SECRET_RULE in true and "newsletters only" in true
    assert true.startswith("Every message from this sender fits this rule.")
    text = json.dumps(state)
    assert SECRET_RULE not in text and "newsletters only" not in text


def test_the_pin_reads_the_lists_that_exist() -> None:
    """No fourth list of shared domains, and no second list of no-reply
    prefixes: the pin reads `cleanup._SHARED_DOMAINS` and
    `engine._NO_REPLY_PREFIXES` (§10.4.8 "State shapes", A9)."""
    src = (AUTOMATION / "learning.py").read_text(encoding="utf-8")
    assert "_SHARED_DOMAINS" in src and "_NO_REPLY_PREFIXES" in src
    assert '"gmail.com"' not in src.split("def _sender_pin_question", 1)[1]


# ── The message id reaches the log from each caller ────────────────────────


async def test_classify_matches_passes_the_row_id_and_the_sender_history(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    fake = _fake(monkeypatch, p=0.9)
    _llm(monkeypatch, eng, {"index": 0, "reason": "x"})
    history = [{"rule": "Receipt", "count": 2}]
    monkeypatch.setattr(eng, "_load_rules", AsyncMock(return_value=[
        {**r, "enabled": True} for r in _RULES]))
    monkeypatch.setattr(eng, "_load_rule_patterns", AsyncMock(return_value={}))
    monkeypatch.setattr(eng, "_is_reply_candidate", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(eng, "_load_rule_guidance", AsyncMock(return_value={}))
    monkeypatch.setattr(eng, "_fetch_sender_history", AsyncMock(return_value=history))
    monkeypatch.setattr(eng, "_account_models", AsyncMock(return_value={"rule": "m"}))
    row = SimpleNamespace(id="msg-42", thread_id=None)
    with structlog.testing.capture_logs() as caps:
        await eng.classify_matches(AsyncMock(), ACC, row, EMAIL, resolve=False)
        await eng.classify_matches(AsyncMock(), ACC, row, EMAIL, multi_rule=True,
                                   resolve=False)
    recs = _records(caps, "decide.shadow")
    assert [r["message_id"] for r in recs] == ["msg-42", "msg-42"]
    assert all(c["state"]["sender_history"] == history for c in fake.calls)


async def test_recompute_thread_status_passes_the_messages_and_the_last_id(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, "email.thread_status=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, rz, {"status": "REPLY"})
    ctx = rz.ThreadContext(
        thread_id="t1", last_message_id="m9", last_message_at=None,
        our_side_last=False, has_external=True, thread_text="thread", messages=THREAD)
    monkeypatch.setattr(rz, "build_thread_context", AsyncMock(return_value=ctx))
    monkeypatch.setattr(rz, "_status_corrections_block", AsyncMock(return_value=""))
    monkeypatch.setattr(rz, "_upsert_thread_status", AsyncMock())
    with structlog.testing.capture_logs() as caps:
        await rz.recompute_thread_status(AsyncMock(), ACC, "t1", trigger="inbound")
    assert fake.calls[0]["state"]["thread"] == THREAD
    assert _records(caps, "decide.shadow")[0]["message_id"] == "m9"


async def test_the_resolver_passes_the_messages_and_the_row_id(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.thread_status=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, rz, {"status": "DONE"})
    ctx = rz.ThreadContext(
        thread_id="t1", last_message_id="m9", last_message_at=None,
        our_side_last=False, has_external=True, thread_text="thread", messages=THREAD)
    monkeypatch.setattr(rz, "_thread_is_conversation", AsyncMock(return_value=True))
    monkeypatch.setattr(rz, "_load_assistant_about", AsyncMock(return_value=("", "")))
    monkeypatch.setattr(rz, "build_thread_context", AsyncMock(return_value=ctx))
    monkeypatch.setattr(rz, "_status_corrections_block", AsyncMock(return_value=""))
    monkeypatch.setattr(rz, "_conversation_rule_for_status", AsyncMock(return_value=None))
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(
        return_value=SimpleNamespace(email_address=SECRET_OWNER)))
    with structlog.testing.capture_logs() as caps:
        await rz.resolve_conversation_status_matches(
            db, ACC, SimpleNamespace(id="msg-55", thread_id="t1"), [])
    assert fake.calls[0]["state"]["thread"] == THREAD
    assert _records(caps, "decide.shadow")[0]["message_id"] == "msg-55"


def _context_rows() -> list[Any]:
    return [
        SimpleNamespace(id="m1", from_address={"name": None, "email": None},
                        to_addresses=[], cc_addresses=[], subject=None,
                        body_text=None, snippet="first", folder="inbox", received_at=None),
        SimpleNamespace(id="m2", from_address={}, to_addresses=[], cc_addresses=[],
                        subject="s2", body_text="second", snippet="", folder="inbox",
                        received_at=None),
        SimpleNamespace(id="m3", from_address={"name": 123, "email": "x@other.com"},
                        to_addresses=[], cc_addresses=[], subject="s3",
                        body_text="third", snippet="", folder="inbox", received_at=None),
    ]


async def _context(monkeypatch, rows: list[Any]) -> Any:
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=rows))
    monkeypatch.setattr(rz, "_attachment_summaries", AsyncMock(return_value={}))
    return await rz.build_thread_context(db, ACC, "t1", "me@acme.com",
                                         extra_domains=frozenset())


async def test_off_mode_builds_no_facts_and_the_same_context(monkeypatch, tenant) -> None:
    """In `off` the facts are not built at all, so the live context is the
    one the base built: the same text, and a name of None or no name at all
    raises nothing."""
    built: list[int] = []
    real = rz._thread_message

    def spy(*a, **kw):
        built.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(rz, "_thread_message", spy)
    rows = _context_rows()
    ctx = await _context(monkeypatch, rows)
    assert ctx.messages == [] and built == []
    assert ctx.thread_text == "\n\n---\n\n".join(
        rz._fmt_thread_msg(r, "me@acme.com", frozenset(), "") for r in rows)
    assert ctx.thread_text.startswith("From: ?\n")


async def test_shadow_facts_take_a_name_of_none_or_not_text(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.thread_status=shadow", ORG)
    ctx = await _context(monkeypatch, _context_rows())
    assert [m["from"] for m in ctx.messages] == ["?", "?", "123"]
    assert ctx.messages[0]["subject"] == "" and ctx.messages[0]["body"] == "first"


async def test_a_facts_failure_never_breaks_the_status(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.thread_status=shadow", ORG)

    def boom(*a, **kw):
        raise TypeError("bad row")

    monkeypatch.setattr(rz, "_thread_message", boom)
    with structlog.testing.capture_logs() as caps:
        ctx = await _context(monkeypatch, _context_rows())
    assert ctx.messages == []
    assert ctx.thread_text.count("---") == 2
    assert _records(caps, "email.thread_facts_failed")


async def test_build_thread_context_builds_the_messages_as_facts(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.thread_status=shadow", ORG)
    rows = [
        SimpleNamespace(id="m1", from_address={"name": "Cust", "email": "cust@other.com"},
                        to_addresses=[{"email": "x@other.com"}],
                        cc_addresses=[{"email": "me@acme.com"}], subject="s1",
                        body_text="Please quote.", snippet="", folder="inbox",
                        received_at=None),
        SimpleNamespace(id="m2", from_address={"email": "sales@acme.com"},
                        to_addresses=[], cc_addresses=[], subject="s2",
                        body_text="On it.", snippet="", folder="inbox", received_at=None),
    ]
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=rows))
    monkeypatch.setattr(rz, "_attachment_summaries", AsyncMock(return_value={}))
    ctx = await rz.build_thread_context(
        db, ACC, "t1", "me@acme.com", extra_domains=frozenset(),
        pending_reply=("Thanks, all set.", "Re: s2"))
    assert [m["side"] for m in ctx.messages] == ["other_party", "owner_organisation", "owner"]
    assert ctx.messages[0]["owner_cc_only"] is True
    assert ctx.messages[0]["cc"] == "me@acme.com"
    assert ctx.messages[1]["to"] == "" and ctx.messages[1]["owner_cc_only"] is False
    assert ctx.messages[2]["body"] == "Thanks, all set."
    assert ctx.our_side_last is True


async def test_the_cold_gate_passes_its_message_id(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.cold_check=shadow", ORG)
    fake = _fake(monkeypatch)
    _llm(monkeypatch, snd, {"cold": False})
    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=None))
    with structlog.testing.capture_logs() as caps:
        await snd._maybe_block_cold(db, object(), ACC, "msg-5", "pm-5", EMAIL, "LABEL")
    assert len(fake.calls) == 1
    assert _records(caps, "decide.shadow")[0]["message_id"] == "msg-5"


# ── Fix round 1: the shadow log is not biased ──────────────────────────────


def _multi_compare(picks: list[dict[str, Any]]) -> Any:
    rules = _cleanup(3)
    decision = _decision({"r0": 0.9, "r1": 0.9, "r2": 0.9}, {"best": "r0"})
    return eng._rule_match_compare(rules, multi=True)(picks, decision)


@pytest.mark.parametrize(("picks", "old"), [
    # No primary: the live path sorts by the canonical order.
    ([{"index": 2, "primary": False}, {"index": 0, "primary": False}], "r0"),
    # Two primaries: both lead, in the canonical order.
    ([{"index": 2, "primary": True}, {"index": 1, "primary": True},
      {"index": 0, "primary": False}], "r1"),
    ([{"index": 2, "primary": True}, {"index": 0, "primary": False}], "r2"),
    ([], "none"),
])
def test_the_multi_rule_old_main_is_the_rule_the_live_path_puts_first(picks, old) -> None:
    assert _multi_compare(picks).old == old


async def test_the_old_main_matches_the_live_sort(monkeypatch, tenant) -> None:
    """Tie the comparison to `_match_email_to_rules_multi` itself: the rule
    the live path puts first is the one the log names as `old`."""
    _modes(monkeypatch, "email.rule_match=shadow", ORG)
    _fake(monkeypatch, p=0.9, choices={"best": "r0"})
    rules = [{**r, "enabled": True} for r in _cleanup(3)]
    _llm(monkeypatch, eng, {"matches": [
        {"index": 2, "reason": "x", "primary": False},
        {"index": 0, "reason": "y", "primary": False}]})
    monkeypatch.setattr(eng, "_load_rules", AsyncMock(return_value=rules))
    monkeypatch.setattr(eng, "_load_rule_patterns", AsyncMock(return_value={}))
    monkeypatch.setattr(eng, "_is_reply_candidate", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(eng, "_load_rule_guidance", AsyncMock(return_value={}))
    monkeypatch.setattr(eng, "_fetch_sender_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(eng, "_account_models", AsyncMock(return_value={"rule": "m"}))
    with structlog.testing.capture_logs() as caps:
        matches = await eng._match_email_to_rules_multi(
            AsyncMock(), ACC, EMAIL, message_id=MID)
    first = rules.index(matches[0]["rule"])
    (rec,) = _records(caps, "decide.shadow")
    assert rec["old"] == f"r{first}" == "r0"


async def test_a_reply_that_is_not_a_decision_never_raises(monkeypatch, tenant) -> None:
    """`_ask_all` never raises, as its docstring says: a broken reply logs
    `decide.shadow_failed`, and the site keeps the old answer."""
    _modes(monkeypatch, "email.rule_match=shadow", ORG)

    async def not_a_decision(state, questions, **kw):
        return {"answers": "not a mapping"}

    monkeypatch.setattr(decide_mod, "decide", not_a_decision)
    with structlog.testing.capture_logs() as caps:
        out = await _rule(monkeypatch)
    assert out == {"index": 0, "reason": "x"}
    rec = _records(caps, "decide.shadow_failed")
    assert len(rec) == 1 and rec[0]["message_id"] == MID
    assert _records(caps, "decide.shadow") == []


async def test_a_facade_that_is_not_async_never_raises(monkeypatch, tenant) -> None:
    _modes(monkeypatch, "email.cold_check=shadow", ORG)
    monkeypatch.setattr(decide_mod, "decide", lambda state, questions, **kw: None)
    with structlog.testing.capture_logs() as caps:
        out = await _cold(monkeypatch)
    assert out == (True, "r")
    assert _records(caps, "decide.shadow_failed")


def test_clip_fact_bounds_the_escaped_form() -> None:
    fact = df.clip_fact
    assert fact(None, 10) == "" and fact(123, 10) == "123"
    assert fact("a" * 50, 10) == "a" * 10
    # Ordinary text keeps its full raw length: a newline escapes to 2.
    assert fact("\n" * 50, 10) == "\n" * 10
    hostile = fact("\x01" * 5000, 1500)
    assert len(json.dumps(hostile, ensure_ascii=False)) - 2 <= 3000
    assert len(hostile) == 500


def _hostile_email() -> dict[str, str]:
    ctrl = "\x01\x02\x1f"
    return {**EMAIL, **{k: ctrl * 7000 for k in (
        "body", "about", "to", "cc", "subject", "attachments", "from_name",
        "from", "self", "self_name", "date")}}


def test_control_characters_in_every_field_pass_the_validator() -> None:
    """The verifier's case: 254 rules, and control characters in every field.
    The Console measures the JSON-escaped state, where each one takes six
    characters. Before fix round 1 this measured about 36 000 tokens."""
    ctrl = "\x01\x02\x1f"
    rules = _conversation() + [
        {"id": f"c{i}", "name": ctrl * 300, "instructions": ctrl * 3000,
         "system_type": None, "actions": [{"type": "LABEL"}]} for i in range(250)]
    guidance = {"": [ctrl * 400] * 50}
    history = [{"rule": ctrl * 900, "count": 9}] * 5
    requests = eng._rule_match_requests(_hostile_email(), rules, guidance, history)
    assert "best" in requests[0][1]
    for state, questions in requests:
        assert _refusal(state, questions) is None

    # The thread goes through `_thread_message`, as `build_thread_context`
    # builds it in production.
    row = SimpleNamespace(
        id="m1", from_address={"name": ctrl * 5000, "email": "x@other.com"},
        to_addresses=[{"name": ctrl * 5000, "email": "me@acme.com"}],
        cc_addresses=[{"name": ctrl * 5000, "email": "c@other.com"}],
        subject=ctrl * 5000, body_text=ctrl * 5000, snippet="", folder="inbox",
        received_at=None)
    thread = [rz._thread_message(row, "me@acme.com", frozenset(), ctrl * 5000)] * 30
    others = [
        snd._cold_question(_hostile_email()),
        lrn._sender_pin_question(ctrl * 900 + "@gmail.com",
                                 {"name": ctrl * 900, "instructions": ctrl * 9000},
                                 _pin_rows(10, ctrl * 900, ctrl * 900)),
        rz._status_question(thread, ctrl * 900, ctrl * 9000, user_sent_last=False),
    ]
    for state, questions in others:
        assert _refusal(state, questions) is None


def _escaped(state: Any) -> int:
    return len(json.dumps(state, ensure_ascii=False))


def test_each_state_keeps_its_escaped_size_within_twice_its_clips() -> None:
    """A per-field fence. The window test above sees only the total, so one
    field that goes back to a raw clip would hide inside the margin. Each
    bound is two times the sum of the raw clips of the state, plus the JSON
    keys."""
    ctrl = "\x01\x02\x1f"
    history = [{"rule": ctrl * 900, "count": 9}] * 5
    rule_state = eng._rule_state(_hostile_email(), history)
    # 2 x (from 320 + 320, to 2000, cc 2000, date 64, subject 500,
    # attachments 1000, body 1500, owner 320 + 320, about 1200, 5 x 200).
    assert _escaped(rule_state) <= 2 * 10_544 + 600
    assert _escaped(snd._cold_question(_hostile_email())[0]) <= 2 * 7_704 + 400
    row = SimpleNamespace(
        id="m1", from_address={"name": ctrl * 5000, "email": "x@other.com"},
        to_addresses=[{"name": ctrl * 5000, "email": "me@acme.com"}],
        cc_addresses=[{"name": ctrl * 5000, "email": "c@other.com"}],
        subject=ctrl * 5000, body_text=ctrl * 5000, snippet="", folder="inbox",
        received_at=None)
    message = rz._thread_message(row, "me@acme.com", frozenset(), ctrl * 5000)
    # 2 x (from 320, to 1000, cc 1000, subject 500, attachments 1000, body 1500).
    assert _escaped(message) <= 2 * 5_320 + 300
    pin_state = lrn._sender_pin_question(
        "news@gmail.com", {"name": "N", "instructions": "x"},
        _pin_rows(10, ctrl * 900, ctrl * 900))[0]
    assert _escaped(pin_state) <= 2 * (320 + 255 + 10 * (120 + 160)) + 600


def test_the_rule_log_carries_our_keys_never_a_raw_vendor_choice() -> None:
    rules = _conversation() + _cleanup(2)
    weird = eng._read_rule_match(
        _decision({"r4": 0.9, "r5": 0.1}, {"conv": "r4", "best": "R0 <script>"}), rules)
    assert weird.fields["conv"] == "unknown"  # r4 is not a conversation rule
    assert weird.fields["best"] == "unknown"
    assert weird.matched == (4,)
    padded = eng._read_rule_match(
        _decision({"r4": 0.1, "r5": 0.1}, {"conv": "r01", "best": "r1"}), rules)
    assert padded.fields["conv"] == "unknown" and padded.matched == ()
    assert padded.fields["best"] == "r1"
    plain = eng._read_rule_match(
        _decision({"r4": 0.1, "r5": 0.1}, {"conv": "none", "best": "none"}), rules)
    assert (plain.fields["conv"], plain.fields["best"], plain.fields["main"]) == (
        "none", "none", "none")


async def test_the_status_log_carries_our_keys_never_a_raw_vendor_choice(
    monkeypatch, tenant
) -> None:
    _modes(monkeypatch, "email.thread_status=shadow", ORG)
    _fake(monkeypatch, choices={"status": "maybe, said the vendor"})
    with structlog.testing.capture_logs() as caps:
        await _status(monkeypatch)
    (rec,) = _records(caps, "decide.shadow")
    assert rec["new"] == "unknown" and rec["agree"] is False


def test_one_set_of_moving_actions() -> None:
    """The rule match and the undo read ONE set (`engine._MOVE_ACTIONS`)."""
    from gateway.routes.email.automation import runner as run_mod

    assert run_mod._MOVE_ACTIONS is eng._MOVE_ACTIONS
    src = (AUTOMATION / "runner.py").read_text(encoding="utf-8")
    assert '"ARCHIVE", "MOVE_FOLDER", "TRASH", "MARK_SPAM"' not in src


# ── A structural fence: each caller names the message ──────────────────────

#: (file, called function) pairs where every call must pass `message_id=`.
_MESSAGE_ID_CALLS = (
    ("runner.py", "_match_email_to_rule"),
    ("runner.py", "_match_email_to_rules_multi"),
    ("runner.py", "_ai_confirms_sender_pattern"),
    ("engine.py", "_match_email_to_rule"),
    ("engine.py", "_match_email_to_rules_multi"),
    ("engine.py", "_llm_pick_rule"),
    ("engine.py", "_llm_pick_rules"),
    ("replyzero.py", "_llm_determine_thread_status"),
    ("senders.py", "_llm_is_cold"),
)


def _calls_without_message_id(source: str, name: str) -> list[int]:
    """The line of each call to ``name`` that passes no ``message_id=``."""
    missing: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if called == name and not any(k.arg == "message_id" for k in node.keywords):
            missing.append(node.lineno)
    return missing


@pytest.mark.parametrize(("filename", "name"), _MESSAGE_ID_CALLS)
def test_every_decision_call_names_its_message(filename, name) -> None:
    source = (AUTOMATION / filename).read_text(encoding="utf-8")
    assert f"{name}(" in source, (filename, name)
    assert _calls_without_message_id(source, name) == [], (filename, name)


def test_the_message_id_fence_can_fail() -> None:
    source = (
        "async def f(db, aid, email):\n"
        "    await _match_email_to_rule(db, aid, email)\n"
        "    await _match_email_to_rule(db, aid, email, message_id='m')\n"
    )
    assert _calls_without_message_id(source, "_match_email_to_rule") == [2]
