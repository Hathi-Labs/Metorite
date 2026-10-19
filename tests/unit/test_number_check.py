"""The ONE number check (WS-53 CRM-Q29, ``crm_platform.md`` §18.11.2 items 1-3).

``acb_common.number_check`` finds each number of a model's text and checks it
against the numbers of its sources. The chat check (CRM-Q29) and the digest
(CRM-Q28 item 9) share it. These tests pin the parse, the rule of a match, the
forms that are not figures, and the one formatter.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest
from acb_common.number_check import (
    MONEY,
    PERCENT,
    PLAIN,
    Sources,
    check,
    find_figures,
    format_figure,
    group_indian,
)

ROOT = Path(__file__).resolve().parents[2]

TOOL_RESULT = (
    "Deals, sum_amount by status:\n"
    "- Open: INR 1,234,567\n"
    "- Won: INR 500,000\n"
    "Total: INR 2,100,000.\n"
    "Since 2026-07-19: won 12, lost 9, win rate 57.1%."
)


def _values(text: str) -> list[tuple[str, Decimal, str]]:
    return [(f.text, f.value, f.kind) for f in find_figures(text)]


# ── The parse (item 2) ──────────────────────────────────────────────────────

class TestTheParse:
    @pytest.mark.parametrize(("text", "value", "kind"), [
        ("₹12.3 lakh", Decimal("1230000"), MONEY),
        ("INR 12,34,567", Decimal("1234567"), MONEY),
        ("Rs. 5,000", Decimal("5000"), MONEY),
        ("Rs 5,000", Decimal("5000"), MONEY),
        ("$5k", Decimal("5000"), MONEY),
        ("₹1.2 crore", Decimal("12000000"), MONEY),
        ("₹1.2Cr", Decimal("12000000"), MONEY),
        ("₹12.3L", Decimal("1230000"), MONEY),
        ("5,000 rupees", Decimal("5000"), MONEY),
        ("1,234,567", Decimal("1234567"), PLAIN),
        ("3 million", Decimal("3000000"), PLAIN),
        ("45%", Decimal("45"), PERCENT),
        ("12.5 per cent", Decimal("12.5"), PERCENT),
        ("14", Decimal("14"), PLAIN),
    ])
    def test_each_form_parses(self, text: str, value: Decimal, kind: str) -> None:
        figures = find_figures(f"The figure is {text} today")
        assert len(figures) == 1, figures
        assert figures[0].value == value
        assert figures[0].kind == kind

    def test_indian_and_western_grouping_give_one_value(self) -> None:
        assert _values("INR 12,34,567")[0][1] == _values("INR 1,234,567")[0][1]

    def test_the_precision_is_the_one_the_text_states(self) -> None:
        lakh = find_figures("₹12.3 lakh")[0]
        assert lakh.unit == Decimal("10000")
        assert not lakh.exact
        assert find_figures("₹12.35 lakh")[0].unit == Decimal("1000")
        assert find_figures("1,234")[0].unit == Decimal("1")

    def test_a_scaled_number_with_one_significant_digit_must_be_exact(self) -> None:
        assert find_figures("₹5 lakh")[0].exact
        assert not find_figures("₹5.0 lakh")[0].exact


# ── What is not a figure (item 3) ───────────────────────────────────────────

class TestNotAFigure:
    @pytest.mark.parametrize("text", [
        "On 2026-10-17 we met.",
        "At 2026-10-17T10:30:00Z it ran.",
        "On 17/10/2026 we met.",
        "On 17 October 2026 we met.",
        "On Oct 17, 2026 we met.",
        "In October 2026 we met.",
        "At 10:30 we met.",
        "In 2026 we met.",
        "In Q3 of FY26 we met.",
        "See H-236 and WS-53.",
        "The id=3f2a1c9e-8b7d-4e6f-9a1b-2c3d4e5f6a7b row.",
        "Open https://app.metorite.com/crm/deals/123 now.",
        "Run `page=2` again.",
        "The 2nd stage.",
        "Version 1.2.3 ships.",
    ])
    def test_the_form_holds_no_figure(self, text: str) -> None:
        assert find_figures(text) == []

    def test_a_list_marker_is_not_a_figure(self) -> None:
        text = "1. Open: INR 1,234,567\n2. Won: INR 500,000"
        assert [f.text for f in find_figures(text)] == ["INR 1,234,567", "INR 500,000"]

    def test_a_grouped_year_is_a_figure(self) -> None:
        assert _values("We closed 2,026 deals.") == [("2,026", Decimal("2026"), PLAIN)]


# ── The rule of a match (item 2) ────────────────────────────────────────────

class TestTheMatch:
    @pytest.mark.parametrize("claim", [
        "INR 1,234,567", "₹12,34,567", "₹12.3 lakh", "₹12.35 lakh", "₹12 lakh",
        "₹21 lakh", "₹0.21 crore", "57.1%", "57%", "12 won", "9 lost",
        "INR 2,100,000", "1,234,567",
    ])
    def test_a_true_figure_matches(self, claim: str) -> None:
        result = check(f"The answer: {claim}.", [TOOL_RESULT])
        assert result.ok, result.unmatched

    @pytest.mark.parametrize("claim", [
        "₹13.5 lakh",       # misquoted
        "₹18.9 lakh",       # a total that no pair of figures gives
        "INR 1,234,000",    # rounded at a precision the text does not state
        "₹1 crore",         # one significant digit, and not exact
        "58%",              # rounded wrongly
        "₹57.1",            # a percent read as money
        "57.1 crore",       # a value no source holds
        "13 won",           # invented
    ])
    def test_a_false_figure_fails(self, claim: str) -> None:
        result = check(f"The answer: {claim}.", [TOOL_RESULT])
        assert not result.ok
        assert [f.text for f in result.unmatched] == [claim.split(" won")[0]]

    def test_money_never_matches_a_percent(self) -> None:
        assert not check("INR 45", ["win rate 45%"]).ok
        assert not check("45%", ["INR 45"]).ok

    def test_a_plain_number_matches_either_kind(self) -> None:
        assert check("45 of them", ["win rate 45%"]).ok
        assert check("45 of them", ["INR 45"]).ok

    def test_the_sign_is_not_compared(self) -> None:
        assert check("down 5%", ["change -5%"]).ok

    def test_sources_take_a_value(self) -> None:
        book = Sources()
        book.add_value(Decimal("1234567"), MONEY)
        assert len(book) == 1
        assert check("₹12.3 lakh", book).ok
        assert not check("₹12.3%", book).ok

    def test_a_text_with_no_figure_passes(self) -> None:
        result = check("The pipeline grew this week.", [TOOL_RESULT])
        assert result.ok
        assert result.figures == ()


# ── The one formatter (CRM-Q28 item 9) ──────────────────────────────────────

class TestTheFormatter:
    def test_indian_grouping(self) -> None:
        assert group_indian(0) == "0"
        assert group_indian(100) == "100"
        assert group_indian(1234) == "1,234"
        assert group_indian(1234567) == "12,34,567"
        assert group_indian(123456789) == "12,34,56,789"
        assert group_indian(-1234567) == "-12,34,567"

    def test_each_kind(self) -> None:
        assert format_figure(1234567.5, MONEY) == "INR 12,34,568"
        assert format_figure(45.25, PERCENT, decimals=1) == "45.3%"
        assert format_figure(-0.44, decimals=1) == "-0.4"
        assert format_figure(14) == "14"

    def test_the_check_reads_back_each_formatted_value(self) -> None:
        values = [(Decimal("1234567.4"), MONEY, 0), (Decimal("45.25"), PERCENT, 1),
                  (Decimal("14"), PLAIN, 0)]
        prompt = " ".join(format_figure(v, k, decimals=d) for v, k, d in values)
        book = Sources()
        book.add_text(prompt)
        for text in ("INR 12,34,567", "45.3%", "14 deals", "₹12.3 lakh"):
            assert check(text, book).ok, text


# ── One number check (item 1) ───────────────────────────────────────────────

#: Each other module that parses a lakh or a crore, and why it is not a
#: second number check. Each reads one value. None checks a model's numbers
#: against sources.
_OTHER_PARSERS = {
    "apps/services/gateway/gateway/routes/email/automation/insights_extract.py":
        "parse_amount reads ONE amount from a quote, and refuses a scaled one (D-EM-41)",
    "apps/services/gateway/gateway/routes/notes/copilot_policy.py":
        "_NUMERIC says only whether a text holds a number",
}


def _regex_literals(tree: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def test_no_second_number_check() -> None:
    """A new module that parses lakh and crore is a second number check.

    ``evals/`` is in scope (CRM-Q30 fix round): an eval scores the numbers of
    an answer, so it must ask this check too. ``tests/`` is not, because its
    files hold lakh and crore as the INPUT of a test, not as a parser."""
    found: set[str] = set()
    for base in ("apps", "packages", "evals"):
        for path in (ROOT / base).rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            source = path.read_text(encoding="utf-8-sig", errors="replace")
            if "lakh" not in source.lower():
                continue
            literals = " ".join(_regex_literals(ast.parse(source))).lower()
            if "lakh" in literals and "cr" in literals and ("\\d" in literals):
                found.add(rel)
    assert found - set(_OTHER_PARSERS) == {
        "packages/acb_common/acb_common/number_check.py",
    }


def test_the_chat_check_imports_the_one_check() -> None:
    path = ROOT / "packages/acb_skills/acb_skills/answer_check.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        (node.module, alias.name)
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("acb_common.number_check", "check") in imports


# ── Fix round (2026-10-17): years, multipliers, phones, approximations ─────

class TestTheFixRound:
    @pytest.mark.parametrize("text", [
        "In 2026 we met.", "Since 2025 the pipeline grew.", "FY 2026 closed.",
        "March 2026 was strong.", "The 2025-26 plan.", "The 2025–26 plan.",  # noqa: RUF001
        "From 2025 to 2026.",
    ])
    def test_a_year_in_its_context_is_no_figure(self, text: str) -> None:
        assert find_figures(text) == []

    def test_a_four_digit_count_is_a_figure(self) -> None:
        assert _values("We have 2047 contacts.") == [("2047", Decimal("2047"), PLAIN)]
        assert not check("We have 2047 contacts.", [TOOL_RESULT]).ok

    def test_a_multiplier_is_a_figure(self) -> None:
        assert _values("Growth of 5x.") == [("5x", Decimal("5"), PLAIN)]
        assert _values("Growth of 3× this year.") == [("3×", Decimal("3"), PLAIN)]  # noqa: RUF001
        assert not check("A 5x jump.", [TOOL_RESULT]).ok

    @pytest.mark.parametrize("text", [
        "Call +91 98450 12345.", "Call 98450 12345.", "Call 9845012345.",
        "Call (080) 2345-6789.", "Call 080-2345-6789.",
    ])
    def test_a_phone_number_is_no_figure(self, text: str) -> None:
        assert find_figures(text) == []

    @pytest.mark.parametrize("claim", [
        "about ₹4.7 lakh", "around ₹5 lakh", "roughly INR 4,60,000", "~₹4.6 lakh",
        "nearly 12 lakh",
    ])
    def test_an_approximation_matches_within_ten_percent(self, claim: str) -> None:
        assert check(f"Won deals hold {claim}.", ["Won: INR 500,000", "Open: INR 1,234,567"]).ok

    @pytest.mark.parametrize("claim", ["about ₹9 lakh", "around ₹4.4 lakh"])
    def test_an_invented_approximation_still_fails(self, claim: str) -> None:
        assert not check(f"Won deals hold {claim}.", ["Won: INR 500,000"]).ok

    def test_the_same_number_with_no_approximation_word_is_strict(self) -> None:
        assert not check("Won deals hold ₹4.7 lakh.", ["Won: INR 500,000"]).ok


# ── Fix round 2 (2026-10-17): derived figures and inline list markers ──────

class TestDerivedFigures:
    """A sum, a difference, a percent change or a percent share of TWO tool
    figures is a true figure (CRM-Q30 Q21: net cash flow). Fix round 3: it
    passes only when the text also cites BOTH operands."""

    OPEN, WON, TOTAL = "INR 12,34,567", "INR 5,00,000", "INR 21,00,000"

    @pytest.mark.parametrize(("claim", "first", "second"), [
        ("₹17.3 lakh", OPEN, WON),       # Open + Won
        ("₹8.65 lakh", TOTAL, OPEN),     # Total - Open
        ("INR 7,34,567", OPEN, WON),     # Open - Won
        ("58.8%", OPEN, TOTAL),          # Open as a share of Total
        ("40.5%", WON, OPEN),            # Won as a share of Open
        ("70.1%", OPEN, TOTAL),          # the change from Open to Total
    ])
    def test_a_pair_figure_matches(self, claim: str, first: str, second: str) -> None:
        text = f"One holds {first}, one holds {second}, so the answer is {claim}."
        assert check(text, [TOOL_RESULT]).ok

    @pytest.mark.parametrize(("claim", "first", "second"), [
        ("₹17.3 lakh", OPEN, WON),
        ("58.8%", OPEN, TOTAL),
        ("70.1%", OPEN, TOTAL),
    ])
    def test_a_pair_figure_needs_both_operands(self, claim: str, first: str,
                                               second: str) -> None:
        """Rule 1 of fix round 3: no operand, or one, is not enough."""
        for text in (f"The answer is {claim}.",
                     f"One holds {first}, so the answer is {claim}.",
                     f"One holds {second}, so the answer is {claim}."):
            result = check(text, [TOOL_RESULT])
            assert [f.text for f in result.unmatched] == [claim], text

    def test_one_figure_cannot_cite_both_operands(self) -> None:
        """"₹12 lakh" rounds to both tool figures, but it is ONE figure."""
        tool = "A: INR 12,10,000\nB: INR 12,40,000"
        assert not check("Both are near ₹12 lakh, a gap of INR 30,000.", [tool]).ok
        assert check("A holds ₹12.1 lakh and B ₹12.4 lakh, a gap of INR 30,000.",
                     [tool]).ok

    def test_about_gives_no_room_to_a_derived_figure(self) -> None:
        text = f"Open holds {self.OPEN} and won {self.WON}, about ₹16.5 lakh in all."
        assert [f.text for f in check(text, [TOOL_RESULT]).unmatched] == ["₹16.5 lakh"]
        # "about" still gives room to a figure that a source holds.
        assert check("Won deals hold about ₹4.7 lakh.", [TOOL_RESULT]).ok

    def test_a_short_derived_figure_after_about_must_be_exact(self) -> None:
        tool = "Open: INR 1,234,567\nWon: INR 3,765,433"
        text = "Open holds INR 12,34,567 and won INR 37,65,433, about ₹5 lakh more."
        assert not check(text, [tool]).ok   # 25,30,866 is no 5 lakh
        assert check("Open holds INR 12,34,567 and won INR 37,65,433, ₹50 lakh in all.",
                     [tool]).ok

    def test_net_cash_flow_matches(self) -> None:
        tool = "Received: INR 2,50,00,000\nPaid: INR 1,45,22,392"
        assert check("Received INR 2,50,00,000, paid INR 1,45,22,392, so the net "
                     "cash flow is INR 1,04,77,608.", [tool]).ok
        assert not check("Net cash flow is INR 1,04,77,608.", [tool]).ok

    @pytest.mark.parametrize("claim", [
        "₹17.4 lakh",       # near the sum 17,34,567, but not at its precision
        "INR 17,34,600",    # near the sum, and the text states rupees
        "INR 7,00,000",     # near the difference, but not equal
        "59.9%",            # near the share 58.8, but not equal
    ])
    def test_a_number_near_a_pair_figure_fails(self, claim: str) -> None:
        text = f"Open {self.OPEN}, won {self.WON}, total {self.TOTAL}: {claim}."
        assert not check(text, [TOOL_RESULT]).ok

    def test_a_chain_of_three_fails(self) -> None:
        sources = ["100 open", "200 won", "400 lost"]
        # Round 6: a total with its parts after ":" is a sum of the parts, so
        # this chain is only a chain without that shape.
        assert not check("100 open, 200 won and 400 lost, so 700 deals in total.",
                         sources).ok
        assert check("100 open and 200 won, 300 deals in total.", sources).ok
        assert not check("In all, 300 deals.", sources).ok

    def test_the_member_s_words_take_no_part(self) -> None:
        book = Sources()
        book.add_text("Deals: 200", derive=True)
        book.add_text("Is it over 300?")
        assert not check("We have 200 and you said 300, so together 500 deals.", book).ok
        assert check("Over 300 deals?", book).ok

    def test_money_and_plain_never_pair(self) -> None:
        book = Sources()
        book.add_text("INR 1,000", derive=True)
        book.add_text("500 deals", derive=True)
        assert not check("INR 1,000 and 500 deals, INR 1,500 in all.", book).ok
        assert not check("INR 1,000 and 500 deals, 1,500 in all.", book).ok

    def test_money_never_meets_a_percent_of_a_pair(self) -> None:
        assert not check(f"Open {self.OPEN} of {self.TOTAL} is INR 58.8.", [TOOL_RESULT]).ok


class TestInlineListMarkers:
    def test_an_inline_list_has_no_figure_in_its_markers(self) -> None:
        assert find_figures("Top stages: 1. Open 2. Won 3. Lost.") == []

    def test_a_figure_in_an_inline_list_still_counts(self) -> None:
        text = "1. Open INR 12,34,567 2. Won INR 5,00,000"
        assert [f.text for f in find_figures(text)] == ["INR 12,34,567", "INR 5,00,000"]

    def test_a_lone_number_with_a_dot_still_counts(self) -> None:
        assert [f.text for f in find_figures("Revenue 2. crore was the goal.")] == ["2"]
        assert not check("Revenue 2. crore was the goal.", [TOOL_RESULT]).ok

    def test_a_lone_one_with_a_dot_still_counts(self) -> None:
        assert [f.text for f in find_figures("Only 1. Then nothing.")] == ["1"]

    def test_a_run_out_of_order_is_no_list(self) -> None:
        assert [f.text for f in find_figures("We had 3. Then 7. Then 9.")] == ["3", "7", "9"]


# ── Fix round 3 (2026-10-17): parse rules and the sources ─────────────────

class TestFixRound3Parse:
    @pytest.mark.parametrize(("text", "count"), [
        ("A total of 2047 contacts.", "2047"),
        ("Leads fell to 1985 this week.", "1985"),
        ("Out of 2047 contacts, few replied.", "2047"),
        ("Up to 1999 deals.", "1999"),
    ])
    def test_of_and_to_before_a_count(self, text: str, count: str) -> None:
        assert [f.text for f in find_figures(text)] == [count]

    @pytest.mark.parametrize("text", [
        "At the end of 2026 we met.", "In the first quarter of 2026.",
        "Q3 of 2026 was strong.", "From 2025 to 2026.", "In the second half of 2025.",
    ])
    def test_of_and_to_after_a_word_of_time_is_a_year(self, text: str) -> None:
        assert find_figures(text) == []

    @pytest.mark.parametrize("text", [
        "We have __2047__ contacts.", "We have _2047_ contacts.",
        "We have **2047** contacts.", "We have ~~2047~~ contacts.",
        "We have <b>2047</b> contacts.", "We have 2047[1] contacts.",
        "We have 2047[^2] contacts.", "We have 2047^3 contacts.",
        "We have 2047【source】 contacts.", "We have 2047† contacts.",
        "## 2047 contacts",
    ])
    def test_a_marker_neither_hides_nor_adds_a_count(self, text: str) -> None:
        assert [f.text for f in find_figures(text)] == ["2047"]

    def test_a_year_word_reads_through_bold(self) -> None:
        assert find_figures("In **2026** we met.") == []

    def test_a_bold_inline_list_is_a_run(self) -> None:
        assert find_figures("Top stages: **1.** Open **2.** Won **3.** Lost") == []

    def test_a_count_after_a_label_breaks_a_run(self) -> None:
        text = "Won deals: 1. Lost deals: 2. Open: 3."
        assert [f.text for f in find_figures(text)] == ["1", "2", "3"]

    def test_a_numbered_heading_keeps_its_marker(self) -> None:
        assert find_figures("## 2. Deals\n### 2.1 Open") == []

    def test_a_scale_letter_on_the_next_line_is_no_scale(self) -> None:
        figures = find_figures("A: INR 12,10,000\nB: INR 12,40,000")
        assert [f.value for f in figures] == [Decimal(1210000), Decimal(1240000)]
        assert find_figures("₹12.3L")[0].value == Decimal(1230000)


class TestFixRound3Sources:
    def test_a_full_pool_keeps_the_newest_values(self) -> None:
        from acb_common import number_check

        book = Sources()
        for i in range(number_check.MAX_DERIVE + 50):
            book.add_value(1000 + i, MONEY, derive=True)
        pool = book.pool(MONEY)
        assert len(pool) == number_check.MAX_DERIVE
        assert pool[-1] == Decimal(1000 + number_check.MAX_DERIVE + 49)
        assert Decimal(1000) not in pool
        # Each value is still a direct source.
        assert check("INR 1,000", book).ok

    def test_one_text_adds_a_capped_share_to_the_pool(self) -> None:
        from acb_common import number_check

        cap = number_check.MAX_DERIVE_PER_TEXT
        book = Sources()
        book.add_text(" ".join(f"INR {1000 + i}" for i in range(cap + 40)), derive=True)
        book.add_text("INR 77", derive=True)
        assert len(book.pool(MONEY)) == cap + 1
        assert Decimal(77) in book.pool(MONEY)

    def test_a_layer_reads_its_base_and_leaves_it_alone(self) -> None:
        base = Sources()
        base.add_text("Open: INR 12,34,567\nWon: INR 5,00,000", derive=True)
        top = base.layer()
        top.add_text("over ₹7 lakh")
        assert check("Over ₹7 lakh: INR 12,34,567 and INR 5,00,000, ₹17.3 lakh.", top).ok
        assert not check("Over ₹7 lakh.", base).ok


# ── Fix round 4 (2026-10-17): the adversarial review of round 3 ───────────

PIPE = (
    "CRM report pipeline:\n"
    "- Qualification: 14 deals · INR 23,450,000 · weighted INR 4,690,000\n"
    "- Proposal: 9 deals · INR 41,200,000 · weighted INR 20,600,000\n"
    "- Negotiation: 5 deals · INR 70,623,000 · weighted INR 52,967,250\n"
    "Total: 28 deals · INR 135,273,000 · weighted INR 78,257,250"
)
WIN_LOSS = (
    "CRM report win-loss:\n"
    "In the last 90 days, since 2026-07-19: won 12, lost 8, win rate 60.0%.\n"
    "Won INR 18,400,000, lost INR 9,750,000."
)


class TestFixRound4Scale:
    """Rule 2: a scale after a currency sign, a no-break space, and a glued
    figure that never disappears."""

    @pytest.mark.parametrize(("text", "value"), [
        ("₹19.2m", Decimal("19200000")), ("$4.5b", Decimal("4500000000")),
        ("$3mm", Decimal("3000000")), ("₹12\u00a0crore", Decimal("120000000")),
        ("₹12\u202flakh", Decimal("1200000")),
    ])
    def test_the_scale_reads(self, text: str, value: Decimal) -> None:
        figures = find_figures(f"The pipeline is {text} now.")
        assert [f.value for f in figures] == [value]

    def test_a_lower_case_scale_needs_a_currency_sign(self) -> None:
        assert [f.text for f in find_figures("Walk 5 m to the 12 b block.")] == ["5", "12"]

    @pytest.mark.parametrize("text", ["₹19.2xy", "INR 5,00,000abc", "$4.5bnz"])
    def test_a_glued_figure_is_kept_and_fails(self, text: str) -> None:
        result = check(f"The pipeline is {text} now.", [PIPE])
        assert [f.text for f in result.unmatched] == [text]
        assert result.unmatched[0].unreadable

    def test_a_glued_figure_is_no_source(self) -> None:
        assert not check("₹19.2 is the figure.", ["Total ₹19.2xy"]).ok


class TestFixRound4Derivation:
    """Rule 4: the operands stand next to the derived figure in its sentence,
    and a word of derivation stands near it. Rule 5: two percents."""

    def test_operands_in_another_block_fail(self) -> None:
        """Round 5: the window is the block (up to a blank line), not the
        sentence. Operands after a blank line take no part."""
        text = "Won INR 1,84,00,000 and lost INR 97,50,000.\n\nThe net is INR 86,50,000."
        assert not check(text, [WIN_LOSS]).ok
        assert check("Won INR 1,84,00,000 and lost INR 97,50,000. The net is INR 86,50,000.",
                     [WIN_LOSS]).ok

    def test_a_derived_figure_needs_a_word_of_derivation(self) -> None:
        assert not check("Won 12 and lost 8, 20 deals.", [WIN_LOSS]).ok
        assert check("We won 12 and lost 8, so 20 deals closed.", [WIN_LOSS]).ok

    def test_far_operands_of_a_long_list_take_no_part(self) -> None:
        # 14 + 5 = 19, but 9 stands between them and the figure.
        text = "Qualification has 14 deals, Negotiation 5 and Proposal 9, so 19 are stalled."
        assert not check(text, [PIPE]).ok
        text = "Qualification has 14 deals and Negotiation 5, so 19 are in the two stages."
        assert check(text, [PIPE]).ok

    def test_a_share_with_one_operand_after_it_passes(self) -> None:
        text = "Negotiation holds ₹7.06 crore, 52% of the ₹13.53 crore pipeline."
        assert check(text, [PIPE]).ok

    def test_a_gap_of_two_percents_passes(self) -> None:
        tool = "Win rate this quarter: 45%. Last quarter: 35.2%."
        assert check("The win rate is 45% against 35.2%, a gap of 9.8 points.", [tool]).ok
        assert check("The win rate is 45% against 35.2%, up 9.8% in all.", [tool]).ok
        assert not check("The win rate is 45% against 35.2%, a gap of 8.8 points.", [tool]).ok

    def test_a_percent_pool_gives_no_share(self) -> None:
        tool = "Win rate this quarter: 45%. Last quarter: 30%."
        # 45 / 30 is 150 percent, but a percent of a percent is no rule.
        assert not check("The win rate is 45% against 30%, a change of 150%.", [tool]).ok


class TestFixRound4Approx:
    def test_about_gives_a_percent_one_point_at_most(self) -> None:
        assert not check("nearly 66% win rate", [WIN_LOSS]).ok
        assert check("nearly 61% win rate", [WIN_LOSS]).ok
        assert check("about 59% win rate", [WIN_LOSS]).ok


class TestFixRound4Sources:
    def test_a_csv_row_is_a_source(self) -> None:
        assert check("Target is 1234567.", ["region,1234567,88"]).ok
        assert check("Target is 88.", ["region,1234567,88"]).ok
        assert check("Target is 1234567.", ["2026-10-01,Acme,1234567"]).ok

    def test_compact_json_is_a_source(self) -> None:
        assert check("Target is 1234567.", ['{"target":1234567}']).ok
        assert check("Target is 12.5.", ['{"a":{"b":12.5}}']).ok

    def test_a_grouped_number_in_prose_gives_no_parts(self) -> None:
        # "1,234,567" in prose is ONE value, never 234 and 567.
        assert not check("We had 234 deals.", ["Total: INR 1,234,567."]).ok
        assert not check("We had 234 deals.", ["Total,1,234,567 INR"]).ok

    def test_a_large_result_costs_one_sort(self) -> None:
        """A value waits in a list, and the book sorts once before its next
        match. An insert for each value cost 31 s for 100,000 figures."""
        text = "\n".join(f"- row: {1000 + i} and {i}.{i % 100:02d}" for i in range(50_000))
        book = Sources()
        book.add_text(text, derive=True)
        assert book._values == [] and len(book._pending) == 100_000
        book.settle()
        assert book._pending == [] and book._values == sorted(book._values)
        assert check("The row holds 41234.", book).ok
        assert len(book) == 100_000


class TestFixRound4Limits:
    """Rule 9: these forms are known limits of §18.11.2, not figures."""

    @pytest.mark.parametrize("text", [
        "Won to lost is 4:1.", "The 7th deal.", "We hold #9.",
    ])
    def test_the_form_is_a_known_limit(self, text: str) -> None:
        assert find_figures(text) == []

    def test_a_multiplier_is_read(self) -> None:
        assert [f.text for f in find_figures("Proposal is 3x Negotiation.")] == ["3x"]


# ── Fix round 5 (2026-10-18): true breakdowns, choices, ranges ────────────

INVOICES = (
    "Invoices (4):\n"
    "- INV-2026-0042 · Acme Robotics · INR 4,12,000 · due 2026-10-30\n"
    "- INV-2026-0043 · Bharat Tools · INR 2,75,500 · due 2026-11-05\n"
    "- INV-2026-0044 · Kaveri Labs · INR 9,80,000 · due 2026-11-12\n"
    "Total due: INR 17,82,750"
)
ROUND5_SOURCES = [PIPE, WIN_LOSS, INVOICES]


class TestFixRound5TrueShapes:
    """P1-a: the commonest true shapes of a breakdown pass."""

    @pytest.mark.parametrize("text", [
        # bullets with a bracket share under a total line
        "Pipeline by stage (total ₹13.53 crore):\n- Qualification: ₹2.35 crore (17%)\n"
        "- Proposal: ₹4.12 crore (30%)\n- Negotiation: ₹7.06 crore (52%)",
        # a markdown table with a Share column, and its total row
        "| Stage | Value | Share |\n|---|---|---|\n| Qualification | ₹2.35 crore | 17% |\n"
        "| Proposal | ₹4.12 crore | 30% |\n| Negotiation | ₹7.06 crore | 52% |\n"
        "| Total | ₹13.53 crore | 100% |",
        "Negotiation accounts for 52% of pipeline value (₹7.06 Cr of ₹13.53 Cr).",
        "Proposal and Negotiation hold ₹11.18 crore together (₹4.12 crore and ₹7.06 crore).",
        "Won ₹1.84 crore and lost ₹97.5 lakh. The net is ₹86.5 lakh.",
        "Qualification has 14 deals against 9 in Proposal, 56% more.",
        "Negotiation is your biggest stage. It holds ₹7.06 crore of the ₹13.53 crore "
        "pipeline. That is 52%.",
        "- Proposal: ₹4.12 crore\n- Negotiation: ₹7.06 crore\nTogether they hold ₹11.18 crore.",
        "Negotiation is 52% of the pipeline: ₹7.06 crore of ₹13.53 crore.",
        "We won 12 of 20 closed deals, a 60% win rate.",
        "The average won deal is ₹15.3 lakh (₹1.84 crore over 12 deals).",
        "Acme and Bharat owe ₹6.88 lakh between them: ₹4.12 lakh and ₹2.76 lakh.",
        "Negotiation is weighted at 75%: ₹5.3 crore of ₹7.06 crore.",
    ])
    def test_a_true_shape_passes(self, text: str) -> None:
        result = check(text, ROUND5_SOURCES)
        assert result.ok, [f.text for f in result.unmatched]

    @pytest.mark.parametrize("text", [
        "Pipeline by stage (total ₹13.53 crore):\n- Negotiation: ₹7.06 crore (58%)",
        "| Stage | Value | Share |\n|---|---|---|\n| Negotiation | ₹7.06 crore | 58% |\n"
        "| Total | ₹13.53 crore | 100% |",
        "Proposal and Negotiation hold ₹11.5 crore together (₹4.12 crore and ₹7.06 crore).",
        "The average won deal is ₹19.3 lakh (₹1.84 crore over 12 deals).",
        "We won 12 of 21 closed deals, a 60% win rate.",
        "Negotiation holds ₹7.06 crore.\n\nThat is 52% of the ₹13.53 crore pipeline... "
        "and 58% of value.",
    ])
    def test_the_same_shape_with_a_wrong_figure_fails(self, text: str) -> None:
        assert not check(text, ROUND5_SOURCES).ok

    def test_a_bracket_share_needs_its_host(self) -> None:
        # "(52%)" is the share of 7.06 crore, not of 4.12 crore, the figure
        # right before it. So it fails, though 7.06 crore is in the block.
        text = ("Pipeline (total ₹13.53 crore):\n- Negotiation: ₹7.06 crore\n"
                "- Proposal: ₹4.12 crore (52%)")
        assert not check(text, ROUND5_SOURCES).ok
        assert check(text.replace("(52%)", "(30%)"), ROUND5_SOURCES).ok


class TestFixRound5Parse:
    @pytest.mark.parametrize(("text", "values"), [
        ("Open invoices run ₹3-25 lakh.", [Decimal(300000), Decimal(2500000)]),
        ("Stages hold 5-17 deals.", [Decimal(5), Decimal(17)]),
        ("Invoices run ₹1.15\u20139.9 lakh.", [Decimal("115000"), Decimal("990000")]),
    ])
    def test_both_ends_of_a_range_are_read(self, text: str, values: list[Decimal]) -> None:
        assert [f.value for f in find_figures(text)] == values

    def test_a_range_end_is_checked(self) -> None:
        assert not check("Stages hold 5-17 deals.", [PIPE]).ok
        assert check("Stages hold 5-14 deals.", [PIPE]).ok
        assert find_figures("The 2025-26 plan.") == []

    def test_a_scale_word_on_the_next_line_joins(self) -> None:
        assert [f.value for f in find_figures("The pipeline is ₹14\ncrore.")] == [
            Decimal(140000000)]
        assert not check("The pipeline is ₹19\ncrore across stages.", [PIPE]).ok

    def test_a_percent_never_quotes_a_plain_count(self) -> None:
        assert not check("Lost deals are down 12%.", ["won 12"]).ok
        assert check("Lost deals are down 12%.", ["won 12%"]).ok
        assert check("12 deals.", ["win rate 12%"]).ok
        # A data value has no unit, so it may stand for a percent.
        assert check("The win rate is 60%.", ['{"win_rate":60}']).ok


# ── Fix round 6 (2026-10-18): parts, ratios, fractions, the lock ──────────

WIN_LOSS_6 = (
    "CRM report win-loss:\nSince 2026-07-20: won 20, lost 7, win rate 74.07%.\n"
    "Won INR 135,300,000, lost INR 22,450,000."
)
STAGES_6 = (
    "- Qualification: 12 records\n- Proposal: 8 records\n- Negotiation: 5 records\n"
    "- Won: 20 records · INR 135,300,000\n- Lost: 7 records · INR 22,450,000"
)


class TestFixRound6Parts:
    """P1 item 1: a total with its parts in brackets or as a list is a sum,
    with no derive word. Item 2: three or more parts."""

    @pytest.mark.parametrize(("source", "text"), [
        (WIN_LOSS_6, "Closed deals: 27 (20 won, 7 lost)."),
        (WIN_LOSS_6, "You closed 27 deals: 20 won and 7 lost."),
        (WIN_LOSS_6, "Won (20) and Lost (7): 27 closed deals."),
        (WIN_LOSS_6, "Won ₹13.53 crore and Lost ₹2.245 crore make ₹15.78 crore closed."),
        (STAGES_6, "Open: 25 deals (12 + 8 + 5)."),
        (STAGES_6, "Open deals: 25 (Qualification 12, Proposal 8, Negotiation 5)."),
    ])
    def test_a_total_with_its_parts_passes(self, source: str, text: str) -> None:
        result = check(text, [source])
        assert result.ok, [f.text for f in result.unmatched]

    @pytest.mark.parametrize(("source", "text"), [
        (WIN_LOSS_6, "Closed deals: 28 (20 won, 7 lost)."),
        (WIN_LOSS_6, "You closed 26 deals: 20 won and 7 lost."),
        (WIN_LOSS_6, "Won (20) and Lost (7): 28 closed deals."),
        (WIN_LOSS_6, "Won ₹13.53 crore and Lost ₹2.245 crore make ₹16.78 crore closed."),
        (STAGES_6, "Open: 26 deals (12 + 8 + 5)."),
    ])
    def test_a_wrong_total_still_fails(self, source: str, text: str) -> None:
        assert not check(text, [source]).ok

    def test_a_part_must_be_a_tool_figure(self) -> None:
        book = Sources()
        book.add_text("won 20", derive=True)
        book.add_text("Is it 7?")  # the member's words
        assert not check("Closed deals: 27 (20 won, 7 lost).", book).ok


class TestFixRound6Ratio:
    @pytest.mark.parametrize(("text", "ok"), [
        ("Won ₹13.53 crore is 6x Lost ₹2.245 crore.", True),
        ("Won ₹13.53 crore is 6 times Lost ₹2.245 crore.", True),
        ("Won ₹13.53 crore is 8x Lost ₹2.245 crore.", False),
        ("Won ₹13.53 crore is 4 times Lost ₹2.245 crore.", False),
    ])
    def test_a_ratio_of_two_cited_figures(self, text: str, ok: bool) -> None:
        assert check(text, [WIN_LOSS_6]).ok is ok

    def test_times_makes_a_ratio(self) -> None:
        assert find_figures("Won is 6 times Lost.")[0].ratio
        assert not find_figures("Won is 6 deals.")[0].ratio


class TestFixRound6Fraction:
    def test_both_numbers_of_a_fraction_are_read(self) -> None:
        assert [f.text for f in find_figures("We closed 17/29 deals.")] == ["17", "29"]
        assert find_figures("Since 07/2026 and FY 2025/26.") == []

    @pytest.mark.parametrize(("text", "ok"), [
        ("Won 20/27.", True), ("Won 25/40.", False), ("Won 20/40.", False),
        ("Won 20/26.", False),
    ])
    def test_a_fraction_is_checked(self, text: str, ok: bool) -> None:
        assert check(text, [WIN_LOSS_6]).ok is ok


class TestFixRound6Lock:
    def test_a_match_takes_the_lock_of_the_book(self) -> None:
        """Item 5: ``matches`` sorts the book under its lock, so a worker
        thread that adds a value never races the sort."""
        import threading

        book = Sources()
        book.add_text("won 20", derive=True)
        taken: list[bool] = []

        class _Spy:
            def __init__(self) -> None:
                self._real = threading.Lock()

            def __enter__(self) -> None:
                taken.append(True)
                self._real.acquire()

            def __exit__(self, *_a: object) -> None:
                self._real.release()

        book._lock = _Spy()  # type: ignore[assignment]
        taken.clear()
        assert check("We won 20 deals.", book).ok
        assert taken, "matches did not take the lock"


# ── Fix round 7 (2026-10-19): the review of round 6 ───────────────────────

WIN_LOSS_7 = (
    "CRM report win-loss:\nSince 2026-07-20: won 20, lost 7, win rate 74.07%.\n"
    "Closed with no date: 3.\n- Lost reason Price: 4 deals\n- Lost reason Timing: 3 deals"
)
PIPE_7 = (
    "- Qualification: 12 records · INR 1,84,00,000\n- Proposal: 8 records · INR 59,00,000\n"
    "- Negotiation: 5 records · INR 70,600,000\nTotal: 52 records"
)


class TestFixRound7Fractions:
    """Item 1: a date and a set phrase are no fraction. Item 3: the whole of a
    fraction needs the rest on the same line of a tool result."""

    @pytest.mark.parametrize("text", [
        "Follow up by 15/11.", "Call on 3/11.", "Support is 24/7.", "Split 50/50.",
    ])
    def test_a_date_or_a_set_phrase_is_no_fraction(self, text: str) -> None:
        assert find_figures(text) == []
        assert check(text, [PIPE_7]).ok

    @pytest.mark.parametrize(("text", "ok"), [
        ("Won 20/27.", True), ("Won 25/40.", False), ("Won 20/23.", False),
        ("Won 20/24.", False), ("Won 20/40.", False),
    ])
    def test_the_whole_of_a_fraction(self, text: str, ok: bool) -> None:
        assert check(text, [WIN_LOSS_7]).ok is ok


class TestFixRound7Parts:
    """Item 2: the total is the sum of EVERY listed part. Item 4: counts and
    money, interleaved, each add up on their own."""

    @pytest.mark.parametrize("text", [
        "Open value: ₹2.4 crore (₹1.84 crore, ₹0.59 crore, ₹7.06 crore).",
        "Open: 20 (12, 8, 5).",
        "Open: 25 deals, ₹9.6 crore (Qualification 12 · ₹1.84 Cr, Proposal 8 · "
        "₹0.59 Cr, Negotiation 5 · ₹7.06 Cr).",
        "Open: 26 deals, ₹9.49 crore (Qualification 12 · ₹1.84 Cr, Proposal 8 · "
        "₹0.59 Cr, Negotiation 5 · ₹7.06 Cr).",
    ])
    def test_a_total_of_only_some_parts_fails(self, text: str) -> None:
        assert not check(text, [PIPE_7]).ok

    @pytest.mark.parametrize("text", [
        "Open value: ₹9.49 crore (₹1.84 crore, ₹0.59 crore, ₹7.06 crore).",
        "Open: 25 (12, 8, 5).",
        "Open: 25 deals, ₹9.49 crore (Qualification 12 · ₹1.84 Cr, Proposal 8 · "
        "₹0.59 Cr, Negotiation 5 · ₹7.06 Cr).",
    ])
    def test_a_total_of_all_its_parts_passes(self, text: str) -> None:
        result = check(text, [PIPE_7])
        assert result.ok, [f.text for f in result.unmatched]

    def test_a_ratio_is_no_quote_of_a_count(self) -> None:
        # "5x" is not the count 5 of Negotiation: 13.53 / 2.245 is 6.
        tool = PIPE_7 + "\nWon: INR 135,300,000\nLost: INR 22,450,000"
        assert not check("Won ₹13.53 crore is 5x Lost ₹2.245 crore.", [tool]).ok
        assert check("Won ₹13.53 crore is 6x Lost ₹2.245 crore.", [tool]).ok
