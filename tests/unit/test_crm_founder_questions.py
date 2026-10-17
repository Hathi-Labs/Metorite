"""The founder question set: the dataset, the questions and the checkers (WS-53 CRM-Q30).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30 items 1 to 10.

Hermetic: no database, no model and no network. The scripted run replays
``recorded_routes.json`` through the real tools of ``crm_tools.py``. The
mutations below change one thing of a passing run and prove that a bar
fails (R7). ``test_crm_founder_questions_r8.py`` holds the recorded answers
to the real routes.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
from datetime import UTC
from decimal import Decimal

import pytest

from evals.crm_founder_questions import checkers, dataset, numbers, run
from evals.crm_founder_questions.questions import CATEGORIES, by_id, facts_of, questions


def _scripted(answers=None, routes=None) -> checkers.Score:
    return asyncio.run(run.run_scripted(answers, routes))


def _failed(score: checkers.Score) -> dict[str, dict[str, list[str]]]:
    return {q.id: {b: p for b, p in q.problems.items() if p}
            for q in score.questions if not q.passed()}


# ── The dataset ─────────────────────────────────────────────────────────────


class TestTheDataset:
    def test_a_second_build_gives_the_same_rows(self) -> None:
        from evals.crm_founder_questions.seed import statements

        org = "00000000-0000-0000-0000-0000000000aa"
        first = json.dumps(statements(org, dataset.build.__wrapped__()), default=str)
        second = json.dumps(statements(org, dataset.build.__wrapped__()), default=str)
        assert first == second

    def test_it_has_the_shape_of_dewin(self) -> None:
        ds = dataset.build()
        names = {m.api_name for m in ds.modules}
        assert names == {"Company", "Clients", "Operator_Associates", "Invoices",
                         "Payment_Received", "Payment_Made", "Commercials"}
        assert ds.records["Commercials"] == []
        assert 300 <= ds.size() <= 1500
        prof = dataset.profile(ds)
        assert prof["org"]["time_zone"] == "Asia/Kolkata"
        assert dataset.CONTACTS_PROFILE["plural_label"] == "Existing Sites"
        assert dataset.CONTACTS_PROFILE in prof["modules"]

    def test_the_lookups_point_at_real_records(self) -> None:
        ds = dataset.build()
        ids = {m: set(ds.by_id(m)) for m in ds.records}
        for rec in ds.rows("Invoices"):
            assert rec.fields["Client"]["id"] in ids["Clients"]
        for rec in ds.rows("Payment_Received"):
            assert rec.fields["Invoice"]["id"] in ids["Invoices"]
            assert rec.fields["Client"]["id"] in ids["Clients"]
        for rec in ds.rows("Payment_Made"):
            assert rec.fields["Operator"]["id"] in ids["Operator_Associates"]
        for rec in ds.rows("Clients"):
            assert rec.fields["Company"]["id"] in ids["Company"]
        for note in ds.notes:
            assert note.record_ext_id in ids[note.module]

    def test_the_values_have_the_shapes_of_zoho(self) -> None:
        ds = dataset.build()
        totals = [r.fields["Grand_Total"] for r in ds.rows("Invoices")]
        assert any(isinstance(v, str) for v in totals), "a currency that arrives as text"
        received = [r.fields["Received_On"] for r in ds.rows("Payment_Received")]
        assert any(v.endswith("+05:30") for v in received)
        assert any(v.endswith("Z") for v in received), "a datetime in UTC"
        assert all("Annual_Revenue" not in r.fields for r in ds.rows("Company"))
        with_discount = [r for r in ds.rows("Invoices") if "Discount" in r.fields]
        assert 0 < len(with_discount) < len(ds.rows("Invoices"))

    def test_the_status_and_the_balance_agree_with_the_payments(self) -> None:
        ds = dataset.build()
        paid: dict[str, Decimal] = {}
        for p in ds.rows("Payment_Received"):
            paid[p.fields["Invoice"]["id"]] = (paid.get(p.fields["Invoice"]["id"], Decimal(0))
                                               + dataset.as_decimal(p.fields["Amount"]))
        for inv in ds.rows("Invoices"):
            total = dataset.as_decimal(inv.fields["Grand_Total"])
            got = paid.get(inv.ext_id, Decimal(0))
            assert dataset.as_decimal(inv.fields["Balance_Due"]) == total - got
            want = "Paid" if got == total else "Unpaid" if got == 0 else "Partially Paid"
            assert inv.fields["Status"] == want
            assert inv.fields["Due_Date"] != dataset.AS_OF.isoformat()

    def test_the_zone_changes_the_month_of_some_rows(self) -> None:
        ds = dataset.build()
        moved = [r for r in ds.rows("Invoices")
                 if r.created.astimezone(dataset.IST).month != r.created.astimezone(UTC).month]
        assert moved

    def test_no_row_names_a_real_person_or_domain(self) -> None:
        ds = dataset.build()
        owners = {r.owner for rows in ds.records.values() for r in rows}
        assert all(o.endswith("@founder-evals.test") for o in owners)
        sites = [r.fields.get("Website") for r in ds.rows("Company") if r.fields.get("Website")]
        assert all(s.endswith(".example.test") for s in sites)


# ── The questions ───────────────────────────────────────────────────────────


class TestTheQuestions:
    def test_about_thirty_questions_cover_each_category(self) -> None:
        qs = questions()
        assert 28 <= len(qs) <= 34
        assert len({q.id for q in qs}) == len(qs)
        assert {q.category for q in qs} == set(CATEGORIES)

    def test_the_note_search_question_is_marked(self) -> None:
        notes = [q for q in questions() if q.category == "note_search"]
        assert len(notes) == 1 and notes[0].route_check is False
        assert "CRM-Q27" in notes[0].note
        assert all(q.route_check for q in questions() if q.category != "note_search")

    def test_each_question_checks_something(self) -> None:
        for q in questions():
            assert q.needs, q.id
            assert q.numbers() or q.names() or q.flags, q.id

    def test_the_short_read_question_needs_the_tool_line(self) -> None:
        """CRM-Q30b: Q31 passes because the tool says what Zoho holds."""
        q = by_id()["Q31"]
        ds = dataset.build()
        facts = facts_of(q)
        assert facts["zoho"] == ds.zoho_counts["Payment_Made"] > facts["mirrored"]
        assert q.tool_lines and str(f"{facts['zoho']:,}") in q.tool_lines[0]
        assert not q.forbid_numbers, "the two counts come from the tool, so they may be quoted"

    def test_no_scripted_answer_writes_a_fact_by_hand(self) -> None:
        """Each number of an answer comes from a placeholder. The text around
        them may name a year, a day, a quarter or a count of the question."""
        allowed = {Decimal(n) for n in (1, 2, 3, 4, 5, 15, 26, 30, 31, 2025, 2026)}
        for qid, entry in run.load_answers().items():
            bare = re.sub(r"\{[^}]+\}", "", entry["answer"])
            stray = [w.text for w in numbers.parse(bare) if w.value not in allowed]
            assert not stray, (qid, stray)

    def test_each_question_has_a_scripted_entry(self) -> None:
        assert set(run.load_answers()) == {q.id for q in questions()}


# ── The number check ────────────────────────────────────────────────────────


class TestTheNumberCheck:
    @pytest.mark.parametrize("text", [
        "INR 1,23,45,678", "₹12,345,678", "Rs. 1,23,45,678.00", "₹1.23 crore", "₹123.46 lakh",
        "12,345.68 thousand", "12.35 million",
    ])
    def test_each_form_stands_for_the_value(self, text: str) -> None:
        assert numbers.matches(Decimal("12345678"), text)

    @pytest.mark.parametrize("text", ["₹1.2 crore", "₹12 million", "INR 1,23,45,000", "₹12.4 m"])
    def test_a_coarse_or_wrong_form_does_not(self, text: str) -> None:
        assert not numbers.matches(Decimal("12345678"), text)

    def test_a_count_must_be_exact(self) -> None:
        assert numbers.matches(14, "We raised 14 invoices.")
        assert not numbers.matches(14, "We raised 15 invoices.")

    def test_a_word_after_a_number_is_no_scale(self) -> None:
        assert numbers.parse("12 months")[0].value == 12
        assert numbers.parse("715 MLD")[0].value == 715


# ── The scripted run, and its mutations (R7) ────────────────────────────────


class TestTheEval:
    def test_the_scripted_run_passes_each_bar(self) -> None:
        score = _scripted()
        assert score.passed(), json.dumps(_failed(score), indent=2)
        assert len(score.questions) == len(questions())

    def test_the_real_tool_says_no_values_for_an_empty_field(self) -> None:
        _calls, texts = asyncio.run(run.run_tools(run.load_answers()["Q22"],
                                                  run.recorded(run.load_routes())))
        assert "no values recorded for Annual_Revenue" in texts[0]

    def test_the_real_tool_names_the_crm_zone_of_a_datetime_month(self) -> None:
        _calls, texts = asyncio.run(run.run_tools(run.load_answers()["Q04"],
                                                  run.recorded(run.load_routes())))
        assert "Months count in the CRM time zone, Asia/Kolkata." in texts[0]

    def test_a_wrong_measure_fails_the_tools_bar(self) -> None:
        answers = copy.deepcopy(run.load_answers())
        answers["Q01"]["calls"] = answers["Q29"]["calls"]  # min, not sum
        failed = _failed(_scripted(answers))
        assert "tools" in failed["Q01"]

    def test_a_missing_filter_fails_the_tools_bar(self) -> None:
        answers = copy.deepcopy(run.load_answers())
        answers["Q16"]["calls"] = answers["Q15"]["calls"]  # no due date range
        failed = _failed(_scripted(answers))
        assert "tools" in failed["Q16"]

    def test_a_second_call_that_is_missing_fails_the_tools_bar(self) -> None:
        answers = copy.deepcopy(run.load_answers())
        answers["Q21"]["calls"] = answers["Q21"]["calls"][:1]
        failed = _failed(_scripted(answers))
        assert "tools" in failed["Q21"]

    def test_a_wrong_number_fails_the_numbers_bar(self) -> None:
        answers = copy.deepcopy(run.load_answers())
        answers["Q09"]["answer"] = "We raised {n:count}1 invoices in September 2026."
        assert "numbers" in _failed(_scripted(answers))["Q09"]

    def test_a_rounded_total_fails_the_numbers_bar(self) -> None:
        answers = copy.deepcopy(run.load_answers())
        answers["Q01"]["answer"] = "We invoiced about ₹7 crore in FY 2025-26."
        assert "numbers" in _failed(_scripted(answers))["Q01"]

    def test_a_wrong_order_fails_the_names_bar(self) -> None:
        answers = copy.deepcopy(run.load_answers())
        text = answers["Q11"]["answer"]
        answers["Q11"]["answer"] = text.replace("{name:top.1.name}", "{X}").replace(
            "{name:top.2.name}", "{name:top.1.name}").replace("{X}", "{name:top.2.name}")
        assert "names" in _failed(_scripted(answers))["Q11"]

    def test_a_missing_flag_fails_the_flags_bar(self) -> None:
        answers = copy.deepcopy(run.load_answers())
        answers["Q31"]["answer"] = "We have {n:mirrored} Payment Made records."
        assert "flags" in _failed(_scripted(answers))["Q31"]

    def test_a_zero_for_an_empty_field_fails_the_forbid_bar(self) -> None:
        answers = copy.deepcopy(run.load_answers())
        answers["Q22"]["answer"] = "The combined annual revenue is INR 0, no value recorded."
        assert "forbid" in _failed(_scripted(answers))["Q22"]

    def test_a_tool_with_no_short_read_line_fails_q31(self, monkeypatch) -> None:
        """The mutation of CRM-Q30b: take the line out of the tools, and Q31
        fails on ``tool_text`` even though its answer still reads well. No
        other question fails."""
        from acb_skills import crm_tools

        async def no_line(_keys):
            return ""

        monkeypatch.setattr(crm_tools, "_short_note", no_line)
        failed = _failed(_scripted())
        assert set(failed) == {"Q31"}
        # With no line the tools also read no short read, so the Zoho count
        # has no route answer either.
        assert set(failed["Q31"]) == {"tool_text", "agree"}

    def test_a_short_read_that_disagrees_fails_the_agree_bar(self) -> None:
        routes = copy.deepcopy(run.load_routes())
        routes[checkers.SHORT_READS][0]["zoho_count"] += 1
        assert "agree" in _failed(_scripted(routes=routes))["Q31"]

    def test_a_route_that_disagrees_fails_the_agree_bar(self) -> None:
        routes = copy.deepcopy(run.load_routes())
        key = next(k for k in routes if "measure=max" in k)
        routes[key]["total"] += 1
        failed = _failed(_scripted(routes=routes))
        assert "agree" in failed["Q08"]


def test_live_without_an_org_is_no_go(capsys) -> None:
    assert run.main(["--live"]) == run.EXIT_NO_GO
    assert "NO-GO" in capsys.readouterr().out
