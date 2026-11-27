"""The email-to-Projects eval, in scripted mode: no model, no network (task 2976).

What this file holds:

1. **The scripted run passes all five cases** through the REAL orchestrator run,
   the real email tools, the real company search formatter and the real
   projects-assistant tools. Only the model wire and the gateway are fakes.
2. **The fixtures keep the shape of the failed chat of 2026-11-11.** A search
   for "Aroh Attero" finds nothing. A search for "Aroh" finds mail, by the
   domain only.
3. **Each checker fails a wrong run (R7).** A wrong evidence is made from a good
   one by one change, and the rule must fail.
4. **The live mode is off by default.** It refuses to start with no budget.

Run::

    uv run python -m pytest tests/unit/test_email_to_projects_eval.py -q
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills.company_search_tools import HEADER, format_answer

from evals.email_to_projects import checkers as ck
from evals.email_to_projects import fixtures as fx
from evals.email_to_projects import run as eval_run
from evals.email_to_projects.cases import CASES, select
from evals.email_to_projects.gateway import ChatGateway, mails_for


@pytest.fixture(scope="module")
def records() -> dict[str, dict[str, Any]]:
    """One scripted run of every case. It sends nothing."""
    return {c.id: eval_run.run_case(c) for c in CASES}


# ── 1. the scripted run ──────────────────────────────────────────────────────


@pytest.mark.parametrize("case_id", [c.id for c in CASES])
def test_the_scripted_run_passes_each_case(records: dict[str, dict[str, Any]], case_id: str) -> None:
    rec = records[case_id]
    failed = [r for r in rec["rules"] if not r["pass"]]
    assert rec["passed"], (failed, rec["error"], rec["answer"])
    assert rec["unexpected_sends"] == []
    assert rec["unknown_routes"] == []
    assert rec["model_calls"] > 0


def test_each_case_runs_the_real_tools(records: dict[str, dict[str, Any]]) -> None:
    ran = {c.id: [x["name"] for x in records[c.id]["calls"]] for c in CASES}
    assert ran["EP-1"].count("post_project_update") == 4
    assert ran["EP-2"].count("create_project") == 2
    assert "post_project_update" in ran["EP-4"] and records["EP-4"]["writes"] == []
    assert records["EP-1"]["cards"] == 4 and records["EP-2"]["cards"] == 2
    assert records["EP-4"]["cards"] == 0, "the refusal comes before a card"
    # The refusal text is the product's own text, not the stub's.
    assert any("not on for this organization" in r for r in records["EP-4"]["results"])


# ── 2. the fixtures ──────────────────────────────────────────────────────────


def test_aroh_is_found_only_as_a_domain() -> None:
    aroh = mails_for("Aroh")
    assert {m.id for m in aroh} == {fx.AROH_1.id, fx.AROH_2.id}
    for m in aroh:
        assert "aroh" not in (m.subject + " " + m.body + " " + m.sender).lower()
        assert m.address.endswith("@aroh.si")


def test_two_names_that_no_one_mail_holds_together() -> None:
    assert mails_for("Aroh Attero") == []
    assert [m.id for m in mails_for("Attero")] == [fx.ATTERO.id]
    assert mails_for("Zephyr") == []


def test_the_company_search_answer_is_the_real_formatter() -> None:
    gateway = ChatGateway()
    miss = format_answer(gateway._search_company("Aroh Attero"))
    assert miss == f"{HEADER}\n(no matching records)"
    hit = format_answer(gateway._search_company("Aroh"))
    assert hit.startswith(HEADER) and "full_id: email:" in hit
    assert gateway.searches == ["Aroh Attero", "Aroh"]


def test_the_data_holds_no_real_address() -> None:
    for m in fx.MAILS:
        assert m.address.endswith((".example", ".si")), m.address


# ── 3. each checker fails a wrong run ────────────────────────────────────────


def _evidence(records: dict[str, dict[str, Any]], case_id: str) -> ck.Evidence:
    """A good evidence of *case_id*, rebuilt from its record."""
    rec = records[case_id]
    case = select(case_id)[0]
    calls = [ck.Call(c["agent"], c["name"], c["args"], r)
             for c, r in zip(rec["calls"], rec["results"], strict=True)]
    gateway = ChatGateway(updates_on=case.updates_on)
    for q in rec["searches"]:
        gateway._search_company(q)
    writes = [("POST", w.split(" ", 1)[1], {}) for w in rec["writes"]]
    cards = [{"answer": "APPROVE"} for _ in range(rec["cards"])]
    names = {i: n["name"] for i, n in gateway.nodes.items()}
    if case_id == "EP-2":
        names = {fx.SPACE: "Hathi Labs", **{v: k for k, v in fx.PROJECT_IDS.items()},
                 "7a5e0000-0000-4000-9000-000000000901": fx.NEW_PRODUCT,
                 "7a5e0000-0000-4000-9000-000000000902": fx.NEW_CHILD}
    return ck.Evidence(case_id=case_id, names=case.names, calls=calls, answer=rec["answer"],
                       searches=list(gateway.searches), found=list(gateway.found),
                       writes=writes, cards=cards, node_names=names)


def _good(records: dict[str, dict[str, Any]], case_id: str) -> ck.Evidence:
    ev = _evidence(records, case_id)
    case = select(case_id)[0]
    assert ck.passed(eval_run.check(case, ev)), [r for r in eval_run.check(case, ev) if not r.passed]
    return ev


def _failed(rules: list[ck.Rule]) -> set[str]:
    return {r.name for r in rules if not r.passed}


def test_alone_search_fails_when_two_names_share_a_query(records) -> None:
    ev = _good(records, "EP-2")
    wrong = dataclasses.replace(ev, searches=["Aroh Attero"])
    assert "alone_search" in _failed(ck.common(wrong))


def test_alone_search_fails_when_a_name_is_never_searched(records) -> None:
    ev = _good(records, "EP-5")
    assert "alone_search" in _failed(ck.common(dataclasses.replace(ev, searches=[])))


def test_read_before_write_fails_with_no_read(records) -> None:
    ev = _good(records, "EP-1")
    wrong = dataclasses.replace(ev, calls=[c for c in ev.calls if c.name != "read_email"])
    assert "read_before_write" in _failed(ck.common(wrong))


def test_read_before_write_fails_when_the_read_comes_after(records) -> None:
    ev = _good(records, "EP-2")
    reads = [c for c in ev.calls if c.name == "read_email"]
    rest = [c for c in ev.calls if c.name != "read_email"]
    assert "read_before_write" in _failed(ck.common(dataclasses.replace(ev, calls=rest + reads)))


def test_read_before_write_fails_when_a_found_mail_stays_unread(records) -> None:
    """The failed chat: the search found the Aroh mail, and nobody read it."""
    ev = _good(records, "EP-2")
    skip = fx.AROH_2.id
    wrong = dataclasses.replace(ev, calls=[c for c in ev.calls
                                           if not (c.name == "read_email"
                                                   and c.args.get("email_id") == skip)])
    assert "read_before_write" in _failed(ck.common(wrong))


def test_update_names_source_fails_with_no_source(records) -> None:
    ev = _good(records, "EP-1")
    calls = [dataclasses.replace(c, args={**c.args, "body": "Things look fine."})
             if c.name == "post_project_update" else c for c in ev.calls]
    assert "update_names_source" in _failed(ck.common(dataclasses.replace(ev, calls=calls)))


def test_update_names_source_fails_with_the_wrong_mail(records) -> None:
    ev = _good(records, "EP-1")
    calls = [dataclasses.replace(c, args={**c.args, "body": f"See {fx.TATV.subject}"})
             if c.name == "post_project_update" and c.args["health"] == "at_risk" else c
             for c in ev.calls]
    assert "update_names_source" in _failed(ck.common(dataclasses.replace(ev, calls=calls)))


def test_card_per_write_fails_with_no_card(records) -> None:
    ev = _good(records, "EP-1")
    assert "card_per_write" in _failed(ck.common(dataclasses.replace(ev, cards=[])))


def test_card_per_write_fails_with_a_rejected_card(records) -> None:
    ev = _good(records, "EP-2")
    cards = [{"answer": "REJECT"}, {"answer": "APPROVE"}]
    assert "card_per_write" in _failed(ck.common(dataclasses.replace(ev, cards=cards)))


@pytest.mark.parametrize("claim", [
    "I found no emails about Aroh.",
    "There are no emails for Vozzo Creditvision.",
    "No emails turned up, so I changed nothing.",
    "I could not find any mail on TATV.",
    "Nothing came up for Aroh in the inbox. There are no results.",
    "The search for Aroh came back empty.",
    "My search returned nothing about Aroh.",
])
def test_no_false_none_fails_a_claim_of_no_mail(records, claim: str) -> None:
    ev = _good(records, "EP-3")
    assert "no_false_none" in _failed(ck.common(dataclasses.replace(ev, answer=claim)))


def test_no_false_none_allows_none_for_a_name_without_mail(records) -> None:
    ev = _good(records, "EP-5")
    assert "no_false_none" not in _failed(ck.common(ev))


def test_facts_in_write_fails_a_one_line_project(records) -> None:
    """The failed chat wrote a one-line Aroh project."""
    ev = _good(records, "EP-2")
    case = select("EP-2")[0]
    calls = [dataclasses.replace(c, args={**c.args, "description": "New product project."})
             if c.name == "create_project" else c for c in ev.calls]
    assert "facts_in_write:Aroh" in _failed(eval_run.check(case, dataclasses.replace(ev, calls=calls)))


def test_attero_must_be_under_aroh(records) -> None:
    ev = _good(records, "EP-2")
    case = select("EP-2")[0]
    calls = [dataclasses.replace(c, args={**c.args, "parent_project_id": fx.SPACE})
             if c.name == "create_project" and c.args["name"] == fx.NEW_CHILD else c
             for c in ev.calls]
    assert "attero_under_aroh" in _failed(eval_run.check(case, dataclasses.replace(ev, calls=calls)))


@pytest.mark.parametrize("answer", [
    "I updated all four projects from the mail.",
    "Done. Posted the updates.",
    "The projects are set to at risk.",
])
def test_refusal_honest_fails_a_false_claim(records, answer: str) -> None:
    ev = _good(records, "EP-4")
    assert "refusal_honest" in _failed(eval_run.check(select("EP-4")[0],
                                                      dataclasses.replace(ev, answer=answer)))


def test_refusal_honest_fails_when_the_refusal_is_not_said(records) -> None:
    ev = _good(records, "EP-4")
    wrong = dataclasses.replace(ev, answer="Something went wrong with the updates.")
    assert "refusal_honest" in _failed(eval_run.check(select("EP-4")[0], wrong))


def test_says_what_was_searched_fails_a_bare_none(records) -> None:
    ev = _good(records, "EP-5")
    wrong = dataclasses.replace(ev, answer="I found no mail about Zephyr.")
    assert "says_what_was_searched" in _failed(eval_run.check(select("EP-5")[0], wrong))


def test_a_check_again_that_reads_no_mail_fails(records) -> None:
    ev = _good(records, "EP-3")
    wrong = dataclasses.replace(ev, calls=[c for c in ev.calls if c.name != "read_email"])
    assert "read_every_aroh_mail" in _failed(eval_run.check(select("EP-3")[0], wrong))


def test_a_write_in_a_read_only_case_fails(records) -> None:
    ev = _good(records, "EP-5")
    wrong = dataclasses.replace(ev, writes=[("POST", "/projects/nodes", {})])
    assert "wrote_nothing" in _failed(eval_run.check(select("EP-5")[0], wrong))


# ── 4. the live mode is off by default ───────────────────────────────────────


def test_live_mode_refuses_with_no_budget(capsys: pytest.CaptureFixture[str]) -> None:
    assert eval_run.main(["--live"]) == eval_run.EXIT_NO_GO
    assert "--budget-usd" in capsys.readouterr().err


def test_the_default_mode_is_scripted() -> None:
    args = eval_run.build_parser().parse_args([])
    assert args.live is False and args.budget_usd == 0.0
