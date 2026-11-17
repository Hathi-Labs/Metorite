"""The fence of the seven chats of task 2874, their checks, the card answers and ``--mirror-prod``.

Each new check has one case that the behaviour of 2026-11-11 FAILS and one that the right
behaviour passes. No network and no model.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from evals.one_assistant_measure import checks as ck
from evals.one_assistant_measure import checks_2874, mirror, run, splits
from evals.one_assistant_measure import mailbox as mb
from evals.one_assistant_measure.scenarios import all_scenarios
from evals.one_assistant_measure.scenarios_2874 import (
    FLAGS,
    LAKSHMI,
    SCENARIOS_2874,
)
from evals.routing import dataset as ds
from evals.routing.harness import ORCHESTRATOR, ReplyModel, text_turn, tool_turn

PRIYA_ID = mb.uid(5)


def obs(answer: str = "", *calls: tuple[str, dict[str, Any], str]) -> ck.Observation:
    return ck.Observation(arm="pack", answer=answer, ordered=list(calls))


def run_check(kind: str, o: ck.Observation, arg: Any = None) -> bool | None:
    return ck.KINDS[kind](o, arg)


# ── the checks: 11-11 fails, the right behaviour passes ──────────────────────


def test_act_kept_fails_when_a_forward_becomes_a_new_mail() -> None:
    forward = ("forward_email", {"email_id": "e1", "to": ["k@x"]}, "Forwarded: To k@x.")
    new_mail = ("send_email", {"to": ["k@x"], "body": "note"}, "Sent: To k@x.")
    assert run_check("act_kept", obs("", forward), ("forward_email", "e1")) is True
    assert run_check("act_kept", obs("", new_mail), ("forward_email", "e1")) is False
    assert run_check("act_kept", obs("", forward, new_mail), ("forward_email", "e1")) is False


def test_no_promise_fails_on_a_promise_with_no_read() -> None:
    read = ("read_thread", {"email_id": "e1"}, "thread text")
    assert run_check("no_promise", obs("I'll check the thread before we rely on it.")) is False
    assert run_check("no_promise", obs("I will look at the thread.")) is False
    assert run_check("no_promise", obs("It costs Rs 36,000.", read)) is True
    assert run_check("no_promise", obs("I'll check the thread.", read)) is True  # a read ran now
    assert run_check("no_promise", obs("I can check the thread if you want. Shall I?")) is True


def test_no_doubt_fails_on_the_words_of_the_false_doubt() -> None:
    assert run_check("no_doubt", obs("I said it was sent, but I haven't seen a delivery "
                                     "confirmation.")) is False
    assert run_check("no_doubt", obs("I cannot confirm that it went.")) is False
    assert run_check("no_doubt", obs("Yes. It went to priya@boardworks.example.")) is True


def test_states_discard_fails_on_a_card_that_waits() -> None:
    wrong = ("The card is up for your decision. It has not been sent. I will treat it as "
             "declined until you press Send.")
    assert run_check("states_discard", obs(wrong)) is False
    assert run_check("states_discard", obs("The card is still waiting for you, "
                                           "and you discarded nothing yet.")) is False
    assert run_check("states_discard", obs("Nothing was sent.")) is False  # silent on Discard
    assert run_check("states_discard", obs("You discarded it, so nothing was sent.")) is True


def test_done_direct_fails_when_projects_are_searched_first() -> None:
    done = ("mark_thread_done", {"email_id": LAKSHMI.id, "account_id": "a"}, "Thread marked done.")
    find = ("find_tasks", {"query": "invoice"}, "No task.")
    assert run_check("done_direct", obs("", done), LAKSHMI.id) is True
    assert run_check("done_direct", obs("", find, done), LAKSHMI.id) is False
    assert run_check("done_direct", obs("", find), LAKSHMI.id) is False  # it never marked
    assert run_check("done_direct", obs("", ("query_inbox", {}, "x"), done), LAKSHMI.id) is True
    assert run_check("done_direct", obs("", ("search_company", {}, "x"), done),
                     LAKSHMI.id) is False


def test_tried_closest_needs_find_contact_with_the_typed_name() -> None:
    nobody = ("resolve_person", {"name": "Vineeta"},
              "Colleagues:\nNobody matches in the members. Ask for the full name.")
    retry = ("find_contact", {"name": "Vineeta"}, "No exact match. CLOSEST ...")
    other = ("find_contact", {"name": "Kiran"}, "One person matches.")
    assert run_check("tried_closest", obs("", nobody), r"vineeta") is False  # asks at once
    assert run_check("tried_closest", obs("", nobody, other), r"vineeta") is False
    assert run_check("tried_closest", obs("", nobody, retry), r"vineeta") is True


def test_reply_by_compose_fails_on_send_email_for_a_reply() -> None:
    compose = ("compose_reply", {"email_id": "e1", "account_id": "a"},
               "Saved in Drafts of Work. Nothing was sent.")
    wasted = ("send_email", {"reply_to_email_id": "e1", "body": "yes"},
              "Not sent. In this chat a reply goes through compose_reply.")
    assert run_check("reply_by_compose", obs("", compose), "e1") is True  # "Nothing was sent" is true
    assert run_check("reply_by_compose", obs("", wasted, compose), "e1") is False
    assert run_check("reply_by_compose", obs("", wasted), "e1") is False


def test_result_has_reads_a_tool_result() -> None:
    sent = ("compose_reply", {}, "Sent to priya@boardworks.example, from Work.")
    assert run_check("result_has", obs("", sent), r"Sent to priya@") is True
    assert run_check("result_has", obs("", ("compose_reply", {}, "Saved in Drafts.")),
                     r"Sent to") is False


# ── the chats ────────────────────────────────────────────────────────────────


def test_seven_chats_one_for_each_case() -> None:
    assert [s.id for s in SCENARIOS_2874] == [
        "t2874_forward_keeps_act", "t2874_no_empty_promise", "t2874_sent_is_confirmed",
        "t2874_discard_is_final", "t2874_mark_email_done", "t2874_first_name_miss",
        "t2874_reply_by_compose"]
    assert sorted(t for s in SCENARIOS_2874 for t in s.tags if t.startswith("case:")) == [
        f"case:{k}" for k in range(1, 8)]
    assert all("t2874" in s.tags and s.set == "email" for s in SCENARIOS_2874)
    ids = {s.id for s in all_scenarios()}
    assert {s.id for s in SCENARIOS_2874} <= ids
    assert all(splits.split_of(s) == "tune" for s in SCENARIOS_2874)


def test_the_chats_run_on_the_pack_arm_only() -> None:
    for sc in SCENARIOS_2874:
        assert run.arms_for(sc, run.ALL_ARMS) == ("pack",)
    assert all(max(len(t.words.split()) for t in s.turns) < 12 for s in SCENARIOS_2874)


def test_the_scripted_run_passes_every_chat() -> None:
    rows, problems = run.run_scripted(SCENARIOS_2874, ("pack",))
    assert problems == []
    assert len(rows) == 12 and all(r["passed"] for r in rows)
    # the real tools ran, so the proof checks read real answers
    tools = {t for r in rows for t in r["email_tools"]}
    assert {"forward_email", "mark_thread_done", "find_contact", "compose_reply"} <= tools


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[list[tuple[str, dict[str, Any], str]]]:
    """Keep the ordered calls of each turn, so a test reads the real tool results."""
    seen: list[list[tuple[str, dict[str, Any], str]]] = []
    real = run.ck_2874.ordered_calls_of

    def keep(events: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str]]:
        out = real(events)
        seen.append(out)
        return out

    monkeypatch.setattr(run.ck_2874, "ordered_calls_of", keep)
    return seen


def test_a_wrong_path_of_case_7_runs_and_the_product_refuses_it(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The 11-11 path: ``send_email`` for a reply. In a held chat the product refuses it with
    "use compose_reply". The harness runs the tool, and the check sees the wasted round."""
    from evals.one_assistant_measure.scenarios_2874 import REPLY

    seen = _capture(monkeypatch)
    first = [tool_turn("query_inbox", {"from_email": "priya"}, call_id="c1"),
             tool_turn("read_email", {"email_id": PRIYA_ID}, call_id="c2"),
             text_turn("Priya asks for 250 boards."),
             tool_turn("send_email", {"reply_to_email_id": PRIYA_ID, "body": "Yes.",
                                      "account_id": mb.WORK}, call_id="c3"),
             tool_turn("compose_reply", {"email_id": PRIYA_ID, "account_id": mb.WORK,
                                         "draft": "Yes."}, call_id="c4"),
             text_turn("Saved in Drafts.")]
    rows = run.run_chat(REPLY, "pack", models={ORCHESTRATOR: ReplyModel(first)})
    refusal = next(r for n, _, r in seen[1] if n == "send_email")
    assert "a reply goes through compose_reply" in refusal
    assert rows[1]["checks"]["compose_not_send"] is False


def test_case_5_wrong_path_runs_only_with_the_production_flags(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The 11-11 path: a search of Projects tasks before the act. The email chat turns on the
    email pack alone, so the Projects tool is not in the request. With ``--mirror-prod`` it is."""
    from acb_skills import tool_packs

    from evals.one_assistant_measure.scenarios_2874 import DONE

    assert "find_tasks" in tool_packs.PACKS["projects"].tools

    def models() -> dict[str, ReplyModel]:
        return {ORCHESTRATOR: ReplyModel([
            tool_turn("query_inbox", {"from_email": "lakshmi"}, call_id="c1"),
            text_turn("Lakshmi says it is paid."),
            tool_turn(tool_packs.LOAD_TOOL, {"pack": "projects"}, call_id="c2"),
            tool_turn("find_tasks", {"query": "invoice TR-88"}, call_id="c3"),
            tool_turn("mark_thread_done", {"account_id": mb.WORK, "email_id": LAKSHMI.id},
                      call_id="c4"),
            text_turn("Done.")])}

    ran = {}
    for mirrored in (False, True):
        seen = _capture(monkeypatch)
        rows = run.run_chat(DONE, "pack", models=models(), mirror_prod=mirrored)
        find = next(r for n, _, r in seen[1] if n == "find_tasks")
        ran[mirrored] = not checks_2874._not_run(find)
        assert rows[1]["checks"]["mark_thread_done_first"] is False  # the wrong path fails it
    assert ran == {False: False, True: True}


# ── the card answers ─────────────────────────────────────────────────────────

FORM = {"reply": {"message_id": "m1", "reply_all": False, "body": "Hi", "draft_id": "d-1",
                  "ticked": {"reply": {"to": ["a@x"], "cc": []}}}}


def test_member_answers_follow_the_product_grammar() -> None:
    from acb_skills.reply_card import REPLY_ANSWER_PREFIX, parse_reply_answer

    member = run.Member()
    saved = member(None, FORM)
    assert saved.startswith(REPLY_ANSWER_PREFIX)
    assert parse_reply_answer(saved).action == "save"
    member.mode = "send"
    assert parse_reply_answer(member(None, FORM)).action == "send"
    trashed: list[str] = []
    member.on_discard = trashed.append
    member.mode = "discard"
    assert member(None, FORM) == "REJECT"
    assert trashed == ["d-1"]
    member.mode = "approve"
    rows = {"rows": [{"id": "r1"}, {"id": "r2"}]}
    assert json.loads(member(None, rows).split(" ", 1)[1]) == {"rows": ["r1", "r2"]}


# ── the fake gateway ─────────────────────────────────────────────────────────


def test_the_fake_gateway_answers_forward_done_and_closest() -> None:
    asyncio.run(_fake_gateway())


async def _fake_gateway() -> None:
    box = mb.Mailbox(SCENARIOS_2874[0].mails)
    near = await box.get("/email/contacts/suggest", {"q": "Vineeta"})
    assert [(r["name"], r["match"]) for r in near] == [("Vinita Rao", "closest")]
    exact = await box.get("/email/contacts/suggest", {"q": "Kiran"})
    assert exact[0]["match"] == "exact"
    card = await box.post("/email/held-card", {"kind": "forward", "message_id": mb.uid(7),
                                               "to": [mb.KIRAN], "note": "Price it."})
    assert card["title"] == "Forward this email?" and card["rows"][0]["label"] == mb.KIRAN
    done = await box.post("/email/held-card/act", {"card_id": card["card_id"], "answer": "x"})
    assert done["to"] == [mb.KIRAN] and box.forwards[0]["note"] == "Price it."
    await box.post("/email/reply-zero/resolve", {"thread_id": "t", "done": True})
    assert box.resolved == [{"thread_id": "t", "done": True}]
    assert (await box.get("/email/reply-card/draft-state", {"draft_id": "d"}))["in_drafts"] is True
    box.trash_draft("d")
    assert (await box.get("/email/reply-card/draft-state", {"draft_id": "d"}))["in_drafts"] is False


# ── --mirror-prod ────────────────────────────────────────────────────────────

NINE = ("email", "email-setup", "crm-records", "projects", "projects-plan",
        "projects-structure", "projects-bulk", "projects-reminders", "studio")


def test_mirror_prod_gives_an_email_chat_the_nine_packs_and_the_flags() -> None:
    sc = SCENARIOS_2874[0]
    plain = run.flag_for(sc, "pack")
    mirrored = run.flag_for(sc, "pack", True)
    assert tuple(mirrored["tool_packs"].split(",")) == NINE
    assert mirrored["tool_packs_orgs"] == ds.ORG
    assert mirrored["held_card_writes_orgs"] == ds.ORG
    assert mirrored["studio_orgs"] == ds.ORG
    assert mirrored["chat_tool_autoload"] is True
    assert mirrored["orchestrator_floor_trim"] is True
    assert mirrored["ai_turn_kind_parallel"] == "orchestrator"
    assert mirrored["tool_result_spill"] == "shadow"
    assert mirrored["maf_native_sessions"] is True
    assert mirrored["tool_search_orgs"] == ""
    # without the switch, a run is as before: the email owner's packs, and none of the others
    assert set(plain["tool_packs"].split(",")) < set(NINE)
    for name in ("held_card_writes_orgs", "studio_orgs", "chat_tool_autoload",
                 "ai_turn_kind_parallel", "tool_result_spill"):
        assert name not in plain
    assert run.flag_for(sc, "delegation", True) == {}  # the delegation arm holds no pack


def test_mirror_prod_names_are_real_settings_fields_and_the_header_has_the_date() -> None:
    from acb_common import get_settings

    fields = type(get_settings()).model_fields
    data = mirror.load()
    assert all(env.lower() in fields for env in data["flags"])
    assert mirror.header()["read"] == "2026-11-17 04:35 UTC"
    assert "production box" in mirror.header()["source"]
    assert set(mirror.settings_for("org-1")) == {env.lower() for env in data["flags"]}


def test_a_misspelt_key_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mirror, "load", lambda: {"flags": {"TOOL_PACKZ": "x"}, "org_lists": []})
    with pytest.raises(KeyError, match="TOOL_PACKZ"):
        mirror.settings_for("org-1")


def test_the_case_6_chat_turns_on_the_profile_for_that_chat_only() -> None:
    miss = next(s for s in SCENARIOS_2874 if s.id == "t2874_first_name_miss")
    assert run.flag_for(miss, "pack")["assistant_profile_enabled"] is True
    assert "assistant_profile_enabled" not in run.flag_for(SCENARIOS_2874[0], "pack")
    assert mirror.settings_for(ds.ORG)["assistant_profile_enabled"] is True  # part 4: production has it
    assert set(FLAGS) == {"t2874_first_name_miss"}


def test_every_mirrored_flag_has_a_reason_and_each_left_out_flag_is_absent() -> None:
    data = mirror.load()
    assert set(data["flags"]) <= set(data["why"])
    assert all(data["why"][k].strip() for k in data["flags"])
    settings = mirror.settings_for(ds.ORG)
    for env, why in data["left_out"].items():
        assert why.startswith("left out:")
        assert env.lower() not in settings
    for name in ("crm_enabled", "knowledge_enabled", "company_search_enabled", "decide_enabled",
                 "tool_arg_shapes", "chat_first_tools", "context_recency", "chat_durable_asks"):
        assert settings[name] is True
    assert settings["ai_tier_routing"] == "*" and settings["crm_orgs"] == ds.ORG
    assert settings["tool_search_orgs"] == ""
