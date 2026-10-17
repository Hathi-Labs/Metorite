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
    """A new module that parses lakh and crore is a second number check."""
    found: set[str] = set()
    for base in ("apps", "packages"):
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
    figures is a true figure (CRM-Q30 Q21: net cash flow)."""

    @pytest.mark.parametrize("claim", [
        "₹17.3 lakh",       # Open + Won
        "₹8.65 lakh",       # Total - Open
        "INR 7,34,567",     # Open - Won
        "58.8%",            # Open as a share of Total
        "40.5%",            # Won as a share of Open
        "70.1%",            # the change from Open to Total
    ])
    def test_a_pair_figure_matches(self, claim: str) -> None:
        assert check(f"The answer: {claim}.", [TOOL_RESULT]).ok

    def test_net_cash_flow_matches(self) -> None:
        tool = "Received: INR 2,50,00,000\nPaid: INR 1,45,22,392"
        assert check("Net cash flow is INR 1,04,77,608.", [tool]).ok

    @pytest.mark.parametrize("claim", [
        "₹17.4 lakh",       # near the sum 17,34,567, but not at its precision
        "INR 17,34,600",    # near the sum, and the text states rupees
        "INR 7,00,000",     # near the difference, but not equal
        "59.9%",            # near the share 58.8, but not equal
    ])
    def test_a_number_near_a_pair_figure_fails(self, claim: str) -> None:
        assert not check(f"The answer: {claim}.", [TOOL_RESULT]).ok

    def test_a_chain_of_three_fails(self) -> None:
        assert not check("In all, 700 deals.", ["100 open", "200 won", "400 lost"]).ok
        assert check("In all, 300 deals.", ["100 open", "200 won", "400 lost"]).ok

    def test_the_member_s_words_take_no_part(self) -> None:
        book = Sources()
        book.add_text("Deals: 200", derive=True)
        book.add_text("Is it over 300?")
        assert not check("Together, 500 deals.", book).ok
        assert check("Over 300 deals?", book).ok

    def test_money_and_plain_never_pair(self) -> None:
        book = Sources()
        book.add_text("INR 1,000", derive=True)
        book.add_text("500 deals", derive=True)
        assert not check("INR 1,500 in all.", book).ok
        assert not check("1,500 in all.", book).ok

    def test_money_never_meets_a_percent_of_a_pair(self) -> None:
        assert not check("INR 58.8", [TOOL_RESULT]).ok


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
