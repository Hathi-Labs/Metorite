"""The fence of the OA-5 measure: ``evals/one_assistant_measure`` (WS-67, R7).

* Each scenario's known-good plan passes every check on BOTH arms, through
  the real orchestrator run, the real email factory and the real cards. Only
  the model wire and the email gateway are fakes. Nothing leaves the box.
* Each checker fails on a bad answer, one mutation each.
* The pack arm holds the email tools and no email delegation, and a
  delegation to the email assistant fails its check.
* The harness's live mode with real specialists refuses a send that is not
  the Router, and the cost of a call matches the vendor's figures.

Run::

    uv run python -m pytest tests/unit/test_one_assistant_measure.py -q
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from evals.one_assistant_measure import checks as ck
from evals.one_assistant_measure import pack, run
from evals.one_assistant_measure.mailbox import DRAFT, HOME, KIRAN, MAIL, WORK, Mailbox
from evals.one_assistant_measure.scenarios import (
    EXACT_TEXT,
    NAVY_NAMES,
    OFFICE,
    SCENARIOS,
    Check,
    Plan,
    Turn,
    scenario,
)
from evals.routing.harness import ORCHESTRATOR, ReplyModel, RoutingHarness, text_turn, tool_turn


@pytest.fixture(scope="module")
def scripted() -> tuple[list[dict[str, Any]], list[str]]:
    return run.run_scripted()


# ── the scripted run ─────────────────────────────────────────────────────────


def test_every_plan_passes_on_both_arms(scripted) -> None:
    rows, problems = scripted
    assert problems == [], problems
    turns = sum(len(s.turns) for s in SCENARIOS)
    assert len(rows) == 2 * turns and len(SCENARIOS) >= 10, (len(rows), turns)


def test_the_set_holds_the_five_turns_and_the_navy_replay() -> None:
    five = scenario("five_turns")
    assert [t.words for t in five.turns][:3] == [
        "what needs a reply", "reply to Dr Sagar and cc Kiran", "send it"]
    replay = scenario("navy_replay")
    assert replay.history and any("CB6" in h["content"] for h in replay.history)


def test_the_tap_sees_each_agent_of_each_arm(scripted) -> None:
    rows, _ = scripted
    for r in rows:
        agents = {c["agent"] for c in r["model_calls"]}
        if r["arm"] == "pack":
            assert agents == {ORCHESTRATOR}, r
        elif r["email_tools"]:
            assert agents == {ORCHESTRATOR, pack.EA}, r
        assert r["first_text_s"] is not None and r["total_s"] >= r["first_text_s"], r


def test_the_pack_arm_holds_the_email_tools_and_no_email_delegation() -> None:
    sc = scenario("read_invoice")
    models = run.scripted_models(sc, "pack")
    run.run_chat(sc, "pack", models=models)
    offered = {t["function"]["name"] for t in models[ORCHESTRATOR].bodies[0]["tools"]}
    assert pack.DELEGATE_TOOL not in offered
    # ``narrow_and_read`` exists only with NARROWING_AGENTS on, and it is
    # off here, as for the delegated email assistant.
    own = pack.email_tool_names() - {"narrow_and_read"}
    assert own <= offered and "narrow_and_read" not in offered, sorted(own - offered)
    system = json.dumps(models[ORCHESTRATOR].bodies[0]["messages"][0], ensure_ascii=False)
    assert pack.RULES.splitlines()[0] in system and pack.DELEGATE_TOOL not in system


def test_a_delegation_in_the_pack_arm_fails_its_check() -> None:
    sc = scenario("read_invoice")
    models = {ORCHESTRATOR: ReplyModel([
        tool_turn("call_agent", {"agent_name": pack.EA, "message": "Read the invoice."}),
        text_turn("Delta Freight asks for Rs 48,500 by 5 November."),
    ])}
    (row,) = run.run_chat(sc, "pack", models=models)
    assert row["refused"] == 1 and row["checks"]["no_email_delegation"] is False, row


def test_the_delegation_arm_reaches_the_real_email_assistant(scripted) -> None:
    rows, _ = scripted
    row = next(r for r in rows if r["arm"] == "delegation" and r["scenario"] == "exact_reply")
    assert row["email_tools"] == ["compose_reply"] and row["tasks"], row
    assert row["checks"]["no_email_delegation"] is None


def test_a_saved_reply_sent_later_is_a_reply_to_its_own_people() -> None:
    """Turn 1 saves a reply that nobody asked for, and turn 2 sends it. The
    held card shows the draft's own people and text, never the five-turn
    chat's. The first live run of 2026-10-25 found the fake's fixed answer."""
    sc = scenario("reply_correction")
    anil = "7a5e0000-0000-4000-8000-000000000006"
    models = {
        ORCHESTRATOR: ReplyModel([
            tool_turn(pack.DELEGATE_TOOL, {"task": "Correct the date with Anil."}, "c1"),
            text_turn("Saved a reply in Drafts."),
            tool_turn(pack.DELEGATE_TOOL, {"task": "Send the draft to Anil."}, "c2"),
            text_turn("Sent to Anil."),
        ]),
        pack.EA: ReplyModel([
            tool_turn("compose_reply", {"email_id": anil, "account_id": WORK,
                                        "instruction": "They arrive on 14 November."}, "c3"),
            text_turn("Saved in Drafts."),
            tool_turn("send_draft", {"account_id": WORK, "draft_id": "d-000006"}, "c4"),
            text_turn("Draft sent."),
        ]),
    }
    first, second = run.run_chat(sc, "delegation", models=models)
    assert first["checks"]["no_write"] is False, first
    assert second["checks"]["replied"] is True and second["checks"]["the_corrected_date"], second

    async def held_send() -> dict[str, Any]:
        gw = Mailbox(OFFICE)
        await gw.post("/email/reply-card", {"message_id": anil, "instruction": "14 November"})
        await gw.post("/email/reply-card/act", {"message_id": anil, "answer": "APPROVE {}"})
        await gw.post("/email/held-card", {"kind": "draft", "account_id": WORK,
                                           "draft_id": "d-000006"})
        return await gw.post("/email/held-card/act", {})

    assert asyncio.run(held_send()) == {"to": ["anil@railfreight.example"], "cc": []}


# ── the checkers, one mutation each ──────────────────────────────────────────


def _obs(**kw: Any) -> ck.Observation:
    return ck.Observation(arm=kw.pop("arm", "delegation"), **kw)


def _one(kind: str, arg: Any, obs: ck.Observation) -> bool | None:
    return ck.check_turn(Turn("x", (Check(kind, arg),), Plan()), obs)[kind]


def test_a_missing_fact_fails() -> None:
    assert _one("answer_all", ("P-12", "P-31"), _obs(answer="P-12 and P-31.")) is True
    assert _one("answer_all", ("P-12", "P-31"), _obs(answer="P-12 only.")) is False
    assert _one("answer_some", (("a", "b", "c"), 2), _obs(answer="a and c")) is True
    assert _one("answer_some", (("a", "b", "c"), 2), _obs(answer="only a")) is False


@pytest.mark.parametrize(("answer", "expected"), [
    ("The order asks for P-12 and P-31. CB6 and CB7 are only the file names of the samples.", True),
    ("The order asks for P-12 and P-31.", True),
    ("The order names the parts CB6 and CB7.", False),
    ("It almost certainly asks for CB6 and CB7, as the drawings show.", False),
])
def test_a_file_name_stated_as_a_part_fails(answer: str, expected: bool) -> None:
    assert _one("no_name_claim", NAVY_NAMES, _obs(answer=answer)) is expected


@pytest.mark.parametrize(("answer", "expected"), [
    ("Meera wants 40 units of the X-200. The X-100 sheet is only a reference.", True),
    ("Meera wants a quote for 40 units of the X-100 bracket.", False),
])
def test_the_reference_file_as_the_item_fails(answer: str, expected: bool) -> None:
    assert _one("no_reference_subject", ("X-100",), _obs(answer=answer)) is expected


@pytest.mark.parametrize(("answer", "unreadable", "no_claim"), [
    ("I could not read the drawing: it is a scanned image. Its name is only a label.", True, True),
    ("The file name suggests that it shows the GB-40 gearbox.", False, True),
    ("The drawing shows the GB-40 gearbox, revision 3.", False, False),
])
def test_a_claim_about_an_unread_file_fails(answer: str, unreadable: bool, no_claim: bool) -> None:
    obs = _obs(answer=answer)
    assert _one("admits_unreadable", None, obs) is unreadable
    assert _one("no_content_claim", ("GB-40", "gearbox"), obs) is no_claim


def test_the_reply_checks_fail_without_their_card_or_text() -> None:
    card = {"reply": {"message_id": MAIL, "body": f"Dear Dr Sagar,\n\n{EXACT_TEXT}"}}
    act = ("POST", "/email/reply-card/act", {"message_id": MAIL})
    full = _obs(cards=[card], gateway=[act],
                email_calls=[("compose_reply", {"email_id": MAIL, "draft": EXACT_TEXT}, "")])
    assert _one("reply_card", MAIL, full) is True
    assert _one("reply_card", MAIL, _obs(cards=[card])) is False
    assert _one("verbatim", EXACT_TEXT, full) is True
    changed = _obs(email_calls=[("compose_reply", {"draft": EXACT_TEXT.replace("fine", "ok")}, "")])
    assert _one("verbatim", EXACT_TEXT, changed) is False
    assert _one("reply_text", (r"\b14\b",), _obs(
        email_calls=[("compose_reply", {"instruction": "Confirm 14 November."}, "")])) is True
    assert _one("reply_text", (r"\b14\b",), _obs(
        email_calls=[("compose_reply", {"instruction": "Confirm 12 November."}, "")])) is False


def test_the_write_checks_fail_on_the_wrong_write() -> None:
    put = _obs(gateway=[("PUT", "/email/drafts", {"cc": [KIRAN]})])
    assert _one("cc", KIRAN, put) is True
    assert _one("cc", KIRAN, _obs(gateway=[("PUT", "/email/drafts", {"cc": []})])) is False
    sent = _obs(gateway=[("POST", "/email/drafts/send", {"draft_id": DRAFT})])
    assert _one("sent_draft", DRAFT, sent) is True
    assert _one("sent_draft", DRAFT, _obs()) is False
    assert _one("rule_in", HOME, _obs(gateway=[("POST", "/email/rules", {"account_id": HOME})]))
    assert _one("rule_in", HOME, _obs(gateway=[("POST", "/email/rules", {"account_id": WORK})])) \
        is False
    assert _one("no_write", None, _obs(email_calls=[("compose_reply", {}, "")])) is False
    assert _one("no_write", None, _obs(email_calls=[("read_email", {}, "")])) is True


def test_a_named_mailbox_on_a_pooled_read_fails() -> None:
    assert _one("all_inboxes", None, _obs(email_calls=[("query_inbox", {}, "")])) is True
    assert _one("all_inboxes", None, _obs(
        email_calls=[("query_inbox", {"account_id": WORK}, "")])) is False
    assert _one("all_inboxes", None, _obs()) is None


def test_the_arm_checks_apply_to_their_arm_only() -> None:
    guess = _obs(tasks=["List the parts of the Navy order (e.g. CB6/CB7)."])
    assert _one("task_no_guess", NAVY_NAMES, guess) is False
    assert _one("task_no_guess", NAVY_NAMES, _obs(tasks=["List the parts."])) is True
    assert _one("task_no_guess", NAVY_NAMES, _obs(arm="pack")) is None
    assert ck.check_turn(Turn("x", (), Plan()), _obs(arm="pack", refused=["x"]))[
        "no_email_delegation"] is False


def test_the_arguments_of_a_call_are_read_once() -> None:
    """The start event holds the arguments, and the delta repeats them."""
    args = json.dumps({"email_id": MAIL})
    events = [
        {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "read_email", "args": args},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": args},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": "From: x"},
        {"type": "TOOL_CALL_START", "toolCallId": "c2", "toolCallName": "query_inbox", "args": ""},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "c2", "delta": '{"query": '},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "c2", "delta": '"invoice"}'},
    ]
    assert ck.email_calls_of(events) == [
        ("read_email", {"email_id": MAIL}, "From: x"), ("query_inbox", {"query": "invoice"}, "")]


# ── the pack fixture, the cost, the harness ──────────────────────────────────


def test_the_pack_persona_drops_each_email_assistant_bullet() -> None:
    # The package ``orchestrator.agents`` shadows ``agents.py`` and loads it
    # as ``_mod``. It re-exports no persona.
    from orchestrator import agents

    text = pack.persona_for_pack(agents._mod._PULL_INSTRUCTIONS)
    assert pack.DELEGATE_TOOL not in text and text.endswith(pack.RULES)
    assert "SPECIALIST AGENT TOOLS" in text and len(pack.RULES) < 3000
    with pytest.raises(RuntimeError):
        pack.persona_for_pack("- a persona with no email bullet")


def test_the_card_reproduces_each_probe_of_the_router() -> None:
    for probe in run.RATE_CARD["probes"]:
        usage = {"prompt_tokens": probe["prompt_tokens"],
                 "completion_tokens": probe["completion_tokens"],
                 "prompt_tokens_details": {"cached_tokens": probe["cached_tokens"]}}
        usd, source = run.cost_of(usage)
        assert source == "card" and abs(usd - probe["cost"]) <= 0.01 * probe["cost"], (usd, probe)
    assert run.cost_of({"cost": 0.25, "prompt_tokens": 9}) == (0.25, "vendor")


def test_a_live_run_with_real_specialists_refuses_a_send_that_is_not_the_router() -> None:
    import httpx

    async def send() -> None:
        async with httpx.AsyncClient() as client:
            await client.get("http://gateway.invalid/email/messages")

    with RoutingHarness(live=True, real_specialists=True) as harness:
        harness.allow = ("http://router.invalid/",)
        with pytest.raises(AssertionError):
            asyncio.run(send())
        assert harness.sent == ["GET http://gateway.invalid/email/messages"]
        assert harness.passed == []
