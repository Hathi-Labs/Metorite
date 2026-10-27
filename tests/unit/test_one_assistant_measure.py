"""The fence of the OA-5 and OA-7h measure: ``evals/one_assistant_measure`` (WS-67, R7).

* Each scenario's known-good plan passes every check on BOTH arms, through
  the real orchestrator run, the real email factory and the real cards. Only
  the model wire and the email gateway are fakes. Nothing leaves the box.
* Each checker fails on a bad answer, one mutation each.
* The pack arm sets the REAL flag (``TOOL_PACKS``), and the real run loads
  the real ``email`` pack. A hand-off for setup work passes the check
  ``no_email_delegation``, and a hand-off of mail work fails it (OA-7h).
* The checks ``held_card`` and ``one_card`` fail on a plain send and on a
  second card. The measure installs the in-memory mark store.
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
    SCANNED_FACTS,
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
        if r["arm"] == "pack" and "setup" not in r["handoffs"]:
            assert agents == {ORCHESTRATOR}, r
        elif r["email_tools"]:
            assert agents == {ORCHESTRATOR, pack.EA}, r
        assert r["first_text_s"] is not None and r["total_s"] >= r["first_text_s"], r


def _names(body: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in body.get("tools") or []}


def test_the_pack_arm_sets_the_real_flag_and_loads_the_real_pack() -> None:
    """The pack arm sets ``TOOL_PACKS`` and ``TOOL_PACKS_ORGS`` in this process,
    and the real run loads the real pack. The model loads it first when the
    member's words do not (layer (b)). Mutation: ``pack_flag`` gives nothing →
    no pack tool in the second request → red."""
    from acb_common import get_settings
    from acb_skills import tool_packs

    sc = scenario("read_invoice")
    models = run.scripted_models(sc, "pack")
    (row,) = run.run_chat(sc, "pack", models=models)
    assert row["passed"], row
    first, second = (_names(b) for b in models[ORCHESTRATOR].bodies[:2])
    assert tool_packs.LOAD_TOOL in first and "query_inbox" not in first
    own = set(tool_packs.SKILL_FAMILIES["email"]["tools"])
    assert own <= second, sorted(own - second)
    # A load in the middle of a turn keeps the specialist tool for that turn.
    # The next turns of the chat start with the pack (sticky), and the pack
    # replaces the tool: see the five-turn chat in the test below.
    assert pack.DELEGATE_TOOL in second
    # A load by the tool brings the rules in its answer (layer (b)).
    seen = json.dumps(models[ORCHESTRATOR].bodies[1]["messages"], ensure_ascii=False)
    assert tool_packs.rules_for("email").splitlines()[0] in seen
    # The flag lives in the eval process only, for one chat.
    assert get_settings().tool_packs == "" and get_settings().tool_packs_orgs == ""
    assert run.pack_flag() == {"tool_packs": "email", "tool_packs_orgs": run.ds.ORG}


def test_a_chat_that_holds_the_pack_has_no_specialist_tool_for_email() -> None:
    sc = scenario("five_turns")
    models = run.scripted_models(sc, "pack")
    run.run_chat(sc, "pack", models=models)
    later = [_names(b) for b in models[ORCHESTRATOR].bodies[3:]]
    assert later and all("compose_reply" in n and pack.DELEGATE_TOOL not in n for n in later)


def test_the_delegation_arm_holds_no_pack() -> None:
    sc = scenario("read_invoice")
    models = run.scripted_models(sc, "delegation")
    run.run_chat(sc, "delegation", models=models)
    offered = _names(models[ORCHESTRATOR].bodies[0])
    assert pack.DELEGATE_TOOL in offered and "load_skill_tools" not in offered
    assert "query_inbox" not in offered


def test_both_arms_run_with_the_settings_of_production() -> None:
    """Both arms get the tier flags, the held card, native sessions and the
    narrowing agent. ``narrow_and_read`` is then a tool of the email pack."""
    from acb_common import get_settings

    seen: dict[str, Any] = {}
    sc = scenario("read_invoice")

    def on_turn(_row: dict[str, Any]) -> None:
        settings = get_settings()
        for name in run.PRODUCTION:
            seen[name] = getattr(settings, name)

    models = run.scripted_models(sc, "pack")
    run.run_chat(sc, "pack", models=models, on_turn=on_turn)
    assert seen == run.PRODUCTION, seen
    after = get_settings()
    assert after.ai_tier_routing == "" and after.email_held_card_send is False
    assert "narrow_and_read" in _names(models[ORCHESTRATOR].bodies[1])


def test_a_hand_off_of_mail_work_in_the_pack_arm_fails_its_check() -> None:
    sc = scenario("read_invoice")
    models = {
        ORCHESTRATOR: ReplyModel([
            tool_turn("call_agent", {"agent_name": pack.EA, "message": "Read the invoice."}),
            text_turn("Delta Freight asks for Rs 48,500 by 5 November."),
        ]),
        pack.EA: ReplyModel([text_turn("It asks for Rs 48,500.")]),
    }
    (row,) = run.run_chat(sc, "pack", models=models)
    assert row["handoffs"] == ["mail"] and row["checks"]["no_email_delegation"] is False, row


def test_a_hand_off_for_setup_work_in_the_pack_arm_passes_its_check() -> None:
    """With the ``email`` pack alone, the rule goes to the email assistant. The
    scripted plan of ``five_turns`` turn 4 does so, through the real run."""
    rows = run.run_chat(scenario("five_turns"), "pack")
    rule = rows[3]
    assert rule["handoffs"] == ["setup"] and rule["tasks"], rule
    assert rule["checks"]["no_email_delegation"] is True and rule["checks"]["rule_in"] is True
    assert all(r["checks"]["no_email_delegation"] is True for r in rows)


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
    # Fix round 1: a "not" counts only when it denies the name.
    ("It is for CB6 and CB7, not anything else.", False),
    ("The parts are P-12 and P-31, not CB6 and CB7.", True),
    ("CB6 and CB7 are not parts of the order.", True),
    ("CB6 and CB7 aren't the parts.", True),
    # The two live sentences that name CB6 or CB7 (repeat 1, the replay).
    ("The two attached PDFs (CB6_sample, CB7_sample) are sample drawings, for reference "
     "only, so they are not the parts ordered.", True),
    ("The two attached files (CB6 and CB7) are sample drawings for reference only, not the "
     "parts on order.", True),
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
    # Fix round 1, the two sentences of the live run that the old check got
    # wrong. A bare statement with no content verb is a claim.
    ("The drawing Ravi sent is a gearbox, model GB-40, revision 3.", False, False),
    # A pointer to the content claims none of it.
    ("To see what the drawing actually shows, open it here.", False, True),
    # Pack, repeat 2: the same claim as delegation, repeat 2.
    ("I can't read what the drawing shows. It's a revision-3 drawing of a gearbox, "
     "model GB-40, and he wants your comments by Monday.", True, False),
    # The file name itself is no claim.
    ("The attachment is Gearbox_GB-40_rev3.pdf, a gearbox drawing to review.", False, True),
    # A fact that the answer says it took from the file name is a guess.
    ("His drawing set is for model GB-40, revision 3. The subject and filename tell us "
     "it is a gearbox drawing, but not the content.", False, True),
])
def test_a_claim_about_an_unread_file_fails(answer: str, unreadable: bool, no_claim: bool) -> None:
    obs = _obs(answer=answer)
    assert _one("admits_unreadable", None, obs) is unreadable
    assert _one("no_content_claim", SCANNED_FACTS, obs) is no_claim


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


@pytest.mark.parametrize(("task", "kind"), [
    ("set a rule on my personal box: label mail with receipt in the subject as Receipts", "setup"),
    ("Change my writing style to be shorter.", "setup"),
    ("Save this in the knowledge: we quote in rupees.", "setup"),
    ("Turn off the signature setting for Work.", "setup"),
    ("Read the invoice from Delta Freight.", "mail"),
    ("Search my mail for the Navy order.", "mail"),
    ("Reply to Anil and confirm the date.", "mail"),
    ("Send the draft.", "mail"),
    # Mail work wins over a setup word: it errs to the failing side.
    ("Read the mail that sets the new rule.", "mail"),
    ("Summarise my unread mail.", "mail"),
    # Fix round 1: four probes of the verifier, and more.
    ("Check my unread emails against my settings", "mail"),
    ("Show the emails that match my rules", "mail"),
    ("Set up a rule to send newsletters to a label", "setup"),
    # A read of the knowledge is not a setup verb, so it counts as mail.
    ("Search the knowledge for my writing style", "mail"),
    ("Create a rule that labels mail from Delta as Invoices", "setup"),
    ("Update my writing style to be shorter.", "setup"),
    ("Add a signature setting for Work.", "setup"),
    ("Delete the rule Receipts.", "setup"),
    ("Delete the email about the rule.", "mail"),
    ("Respond to Anil about the date.", "mail"),
    ("List my drafts.", "mail"),
    ("Open the thread with Kumar.", "mail"),
    ("Get the replies from Priya.", "mail"),
    ("Look up the invoice and send it on.", "mail"),
    ("Tell me what you did.", "mail"),
])
def test_a_hand_off_is_setup_or_mail_work(task: str, kind: str) -> None:
    assert ck.handoff_kind(task) == kind
    got = ck.check_turn(Turn("x", (), Plan()), _obs(arm="pack", tasks=[task]))
    assert got["no_email_delegation"] is (kind == "setup")


def test_the_refusal_of_a_pack_still_fails_the_check() -> None:
    assert ck.check_turn(Turn("x", (), Plan()), _obs(arm="pack", refused=["x"]))[
        "no_email_delegation"] is False
    assert ck.check_turn(Turn("x", (), Plan()), _obs(arm="pack"))["no_email_delegation"] is True
    assert ck.check_turn(Turn("x", (), Plan()), _obs(arm="delegation", tasks=["Read x."]))[
        "no_email_delegation"] is None


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


# ── OA-7h: the held card, one card, the mark store ───────────────────────────


def _held(turn: int, gateway: list[tuple[str, str, Any]]) -> bool | None:
    return ck.check_turn(Turn("x", (), Plan()), _obs(turn=turn, gateway=gateway))["held_card"]


def test_from_turn_2_a_send_must_go_through_the_held_card() -> None:
    plain = [("POST", "/email/drafts/send", {"draft_id": DRAFT})]
    held = [("POST", "/email/held-card", {"draft_id": DRAFT}),
            ("POST", "/email/held-card/act", {"card_id": "c"})]
    assert _held(2, held) is True
    assert _held(2, plain) is False
    assert _held(3, [("POST", "/email/send", {})]) is False
    assert _held(3, [("POST", "/email/forward", {})]) is False
    # A plain send beside a held send in one turn fails: one send leaked.
    assert _held(2, held + plain) is False
    # Turn 1 is a clean chat, and a turn with no send has nothing to check.
    assert _held(1, plain) is None
    assert _held(2, [("GET", "/email/messages", {})]) is None


def test_no_act_shows_two_cards() -> None:
    one = _obs(email_calls=[("send_draft", {}, "")], cards=[{"title": "Send this draft?"}])
    two = _obs(email_calls=[("send_draft", {}, "")],
               cards=[{"title": "Send this draft?"}, {"title": "Send this draft?"}])
    assert ck.check_turn(Turn("x", (), Plan()), one)["one_card"] is True
    assert ck.check_turn(Turn("x", (), Plan()), two)["one_card"] is False
    # Two writes may show two cards. A read has nothing to check.
    both = _obs(email_calls=[("compose_reply", {}, ""), ("edit_draft", {}, "")],
                cards=[{"title": "a"}, {"title": "b"}])
    assert ck.check_turn(Turn("x", (), Plan()), both)["one_card"] is True
    read = _obs(email_calls=[("read_email", {}, "")])
    assert ck.check_turn(Turn("x", (), Plan()), read)["one_card"] is None


def test_a_card_less_write_gives_a_second_send_card_no_room() -> None:
    """Fix round 1 (P2-1). a turn with ``create_label`` and a send that shows the
    gate card and the held card has two send cards for one send. The old check counted every write, and it passed this turn. Mutation:
    count ``len(cards) <= len(writes)`` → this case passes → red."""
    gate = {"title": "Send this email?"}
    held = {"title": "Send this draft?", "rows": []}
    calls = [("create_label", {}, ""), ("send_draft", {}, "")]
    two = _obs(email_calls=calls, cards=[gate, held])
    assert ck.check_turn(Turn("x", (), Plan()), two)["one_card"] is False
    assert ck.check_turn(Turn("x", (), Plan()), _obs(email_calls=calls, cards=[held]))[
        "one_card"] is True
    # Two cards for one other write is a second card for that act.
    extra = _obs(email_calls=[("manage_inbox", {}, "")],
                 cards=[{"title": "Archive these?"}, {"title": "Archive them all?"}])
    assert ck.check_turn(Turn("x", (), Plan()), extra)["one_card"] is False
    # A reply and a draft edit show one card each, and a send adds one more.
    full = _obs(email_calls=[("compose_reply", {}, ""), ("edit_draft", {}, ""),
                             ("send_draft", {}, "")],
                cards=[{"title": "Reply to a?"}, {"title": "Save these changes?"}, held])
    assert ck.check_turn(Turn("x", (), Plan()), full)["one_card"] is True


@pytest.mark.parametrize("arm", ["delegation", "pack"])
def test_a_scripted_chat_sends_through_the_held_card_from_turn_2(arm: str) -> None:
    """Turn 1 reads mail, so turn 3 is held. The send runs through the real
    held card of the real tool, once, with one card. Mutation: set
    ``email_held_card_send`` False in ``PRODUCTION`` → the send is plain →
    ``held_card`` red."""
    rows = run.run_chat(scenario("five_turns"), arm)
    send = rows[2]
    assert send["checks"]["held_card"] is True and send["checks"]["one_card"] is True, send
    assert send["checks"]["sent_draft"] is True and send["cards"] == 1, send
    assert rows[0]["checks"]["held_card"] is None


def test_the_measure_installs_the_mark_store_and_the_chat_is_marked() -> None:
    """Reading mail writes the mark of the chat to the in-memory store, and the
    next turn reads it (duty 1 of H-417). Mutation: no store → the run stops
    at once, and the held-card check could prove nothing."""
    rows = run.run_chat(scenario("five_turns"), "pack")
    assert rows[0]["mark_writes"] >= 1, rows[0]
    built = [e for e in rows[1]["pack_events"] if e["event"] == "executor.pack_built"]
    assert built and built[0]["held"] is True, rows[1]["pack_events"]
    first = [e for e in rows[0]["pack_events"] if e["event"] == "executor.pack_built"]
    assert first and first[0]["held"] is False, rows[0]["pack_events"]


def test_a_missing_mark_store_stops_the_run(monkeypatch) -> None:
    monkeypatch.setattr(run, "install_memory_mark_store", lambda _mp: None)
    with pytest.raises(AssertionError, match="mark store"):
        run.run_chat(scenario("read_invoice"), "pack")


def test_the_summary_reports_the_cache_share_and_the_setup_gap() -> None:
    rows = run.run_chat(scenario("five_turns"), "pack")
    for r in rows:
        r["model_calls"] = [{"agent": ORCHESTRATOR, "prompt": 1000, "cached": 600,
                             "completion": 10, "cost_usd": 0.01, "dur_s": 1.0}]
        r["total_s"] = 2.0
    arm = run.summarise(rows)["pack"]
    assert arm["cache_share"] == 0.6
    assert arm["setup_handoff_turns"] == 1 and arm["mail_handoff_turns"] == 0
    assert arm["setup_handoff_cost_per_turn_usd"] == 0.01
    text = run.report({"summary": {"pack": arm, "delegation": arm}})
    assert "Cache share" in text and "`held_card`" in text and "`one_card`" in text


def test_the_tap_keeps_the_turn_kind_time_and_the_loads_of_a_pack() -> None:
    rows = run.run_chat(scenario("five_turns"), "pack")
    events = [e["event"] for r in rows for e in r["pack_events"]]
    assert "ai_route.turn_kind" in events and "tool_packs.loaded" in events
    assert any("calls_before" in e for r in rows for e in r["pack_events"])
    first = {"pack_events": [{"event": "ai_route.turn_kind", "latency_ms": 40, "calls_before": 0}]}
    assert run._turn_kind_ms(first) == 40.0 and run._turn_kind_ms({}) is None
    items = {"pack_events": [{"event": "executor.packs_turn_kind", "asked": "['email']",
                              "calls_before": 0}]}
    assert run._asked_pack_items(items) and not run._asked_pack_items(first)
    assert run._packs_before_first_call(items) and not run._packs_before_first_call(first)


# ── the pack fixture, the cost, the harness ──────────────────────────────────


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
        # Fix round 1: a sync client is refused too.
        with pytest.raises(AssertionError), httpx.Client() as client:
            client.get("http://gateway.invalid/crm/leads")
        assert harness.sent == ["GET http://gateway.invalid/email/messages",
                                "GET http://gateway.invalid/crm/leads"]
        assert harness.passed == []


def test_a_live_run_refuses_a_router_that_is_not_on_this_machine(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "https://router.example.com")
    monkeypatch.setenv("LITELLM_API_KEY", "not-a-key")
    assert run.main(["--live", "--scenarios", "read_invoice"]) == run.EXIT_NO_GO


def test_the_live_harness_installs_the_system_one_seam() -> None:
    """In a live run, ``system_one._ask`` reads ``routing_is_on`` as True, so the
    turn-kind request goes to the Router on ``tier-fast``. Every other caller
    reads the real answer, and the seam ends with the harness. Mutation: drop
    ``_system_one_seam`` from ``_use_router`` → ``_ask`` raises ``router_off``
    → red."""
    from acb_llm import routed
    from acb_skills import system_one

    real = routed.routing_is_on
    with RoutingHarness(live=True, real_specialists=True) as harness:
        run._use_router(harness, ("http://127.0.0.1:1", "k-test"))
        assert system_one._gateway() == ("http://127.0.0.1:1/v1", "k-test")
        assert system_one.SYSTEM_ONE_TIER == "tier-fast"

        async def ask() -> None:
            item = system_one.Item(id="x", question="q", kind="yes_no", options=system_one.YES_NO)
            await system_one.ask("c", [item], timeout_s=0.5)

        # The seam passes `router_off`. The request then fails on the closed
        # port or the blocked send, which is any reason but `router_off`.
        with pytest.raises(system_one.SystemOneUnavailable) as raised:
            asyncio.run(ask())
        assert raised.value.reason != "router_off", raised.value.reason
        # A caller that is not `_ask` reads the real answer.
        assert routed.routing_is_on() is real()
    assert routed.routing_is_on is real


def _stored_row(answer: str, claim: bool) -> dict[str, Any]:
    return {"scenario": "scanned_file", "turn": 1, "arm": "pack", "repeat": 2,
            "answer": answer, "tasks": [], "model_calls": [], "first_text_s": 1.0,
            "total_s": 2.0, "error": None, "passed": True,
            "checks": {"ran": True, "admits_unreadable": True, "no_content_claim": claim,
                       "tried_the_file": True, "no_email_delegation": True}}


def test_a_rescore_runs_the_answer_checks_again_and_keeps_the_rest() -> None:
    data = {"rows": [_stored_row(
        "I can't read what the drawing shows. It's a revision-3 drawing of a gearbox, "
        "model GB-40.", True)]}
    changes = run.rescore(data, when="2026-10-25")
    row = data["rows"][0]
    assert changes == ["pack scanned_file#1 r2: no_content_claim True -> False"], changes
    assert row["passed"] is False and row["checks"]["tried_the_file"] is True
    assert data["summary"]["pack"]["turns_passed"] == 0
    assert run.rescore(data) == []


def test_a_merge_keeps_the_sha_of_each_chunk(tmp_path) -> None:
    paths = []
    for name, sha in (("a", "1111111"), ("b", "2222222")):
        row = {**_stored_row("x", True), "scenario": f"s{name}"}
        path = tmp_path / f"chunk_{name}.json"
        path.write_text(json.dumps({"sha": sha, "rows": [row], "spent_usd": 0.1}),
                        encoding="utf-8")
        paths.append(path)
    data = run.merge(paths, tmp_path / "out.json")
    assert data["sha"] == "mixed"
    assert [c["sha"] for c in data["chunks_run"]] == ["1111111", "2222222"]
    assert [r["sha"] for r in data["rows"]] == ["1111111", "2222222"]
