"""The number check explains, asks or says how (WS-53 CRM-Q29 round 8).

``crm_platform.md`` §18.11.2, CRM-Q29, fix round 8 (owner, 2026-10-20).

On 2026-10-19 a true answer about one module went out with the note "I could
not check these figures against the CRM data: ₹10.62 crore, ₹32.92 crore,
₹22.30 crore, 959.8, 592.6 and 41 more." The owner called it very confusing.
:data:`RESULTS` and :func:`owner_answer` rebuild that run with the same shape:
three sums of one module by one group field, the largest group empty, and an
answer that adds and subtracts them and shows a table in lakh with no unit.
Four rules of the check failed it:

* a figure that the text derived ("₹32.92 crore", a sum of three tool
  figures) was no operand, so "the other ₹10.62 crore (₹32.92 crore less
  ₹22.30 crore)" failed, and so did each figure that stated it again;
* a bare number of a table cell was a count, so 959.8 never met 9,59,80,000;
* a cell that adds up its row (a "Net" column) had no rule;
* "the 11 tagged regions" counts the lines of a list, and no source held 11.

The note then named raw figures. Now a figure that the answer works out
needs no note, many failed figures are a limit of the check, an ambiguity
becomes one question, and a few failed figures each get a reason.

The org of the run is not named here or in the product code
(``test_the_round_8_product_code_names_no_word_of_one_org``).
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acb_common.number_check import Sources, check, list_counts
from acb_skills import answer_check
from acb_skills.answer_check import (
    HOW_HEAD,
    HOW_TAIL,
    NOTE_HOW,
    NOTE_MISSING,
    NOTE_NAMED,
    NOTE_NEAR,
    NOTE_NONE,
    NOTE_PERCENT,
    NOTE_QUESTION,
    ON,
    QUESTION_OPEN,
    SHADOW,
    CheckAnswers,
    RunLedger,
    empty_group,
    note_text,
    read_line,
)
from agent_framework import ChatResponse, Content, Message

ROOT = Path(__file__).resolve().parents[2]

# ── The rebuilt run ──────────────────────────────────────────────────────────

GROUPS = ["(none)", "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
          "Golf", "Hotel", "India", "Juliet", "Kilo"]
#: Rupees by group for each of three amount fields. The empty group holds
#: the most, as in the run of 2026-10-19.
RENT = [95_980_000, 9_210_000, 7_340_000, 5_120_000, 4_460_000, 3_310_000,
        2_870_000, 2_150_000, 1_960_000, 1_440_000, 3_210_000, 2_862_345]
FEES = [59_260_000, 6_110_000, 5_020_000, 4_330_000, 3_210_000, 2_640_000,
        2_010_000, 1_880_000, 1_550_000, 1_220_000, 2_870_000, 1_834_000]
POWER = [67_770_000, 6_340_000, 4_910_000, 3_870_000, 2_990_000, 2_410_000,
         1_870_000, 1_660_000, 1_240_000, 1_010_000, 2_150_000, 1_311_000]
FIELDS = (("Rent Amount", RENT), ("Fee Amount", FEES), ("Power Amount", POWER))


def _plain(value: float) -> str:
    """A module total as ``crm_tools._number`` prints it: no currency sign."""
    text = f"{value:,.2f}"
    return text.rstrip("0").rstrip(".")


def aggregate(label: str, values: list[int]) -> str:
    """The text of ``crm_aggregate`` for a sum of one field by one group."""
    lines = [f"Collections, sum of {label} by Region:"]
    pairs = sorted(zip(GROUPS, values, strict=True), key=lambda pair: -pair[1])
    lines += [f"- {group}: {_plain(value)}" for group, value in pairs]
    lines.append(f"Total: {_plain(sum(values))}.")
    return "\n".join(lines)


RESULTS = [aggregate(label, values) for label, values in FIELDS]
ARGS = {"entity": "module:Collection", "measure": "sum", "group_field": "Region"}


def crore(value: float) -> str:
    return f"₹{value / 1e7:.2f} crore"


def lakh(value: float) -> str:
    return f"{value / 1e5:.1f}"


def owner_answer(caption: str = "Net amounts by region:", header_unit: str = "",
                 total_row: bool = False) -> str:
    """The answer of the run, with the generic names of this file."""
    totals = [sum(values) for _label, values in FIELDS]
    empty = [values[0] for _label, values in FIELDS]
    head = (
        f"Collections across the 8,681 collection records total {crore(sum(totals))}: "
        f"rent {crore(totals[0])}, fees {crore(totals[1])}, power {crore(totals[2])}. "
        f"Of that, {crore(sum(empty))} is untagged (rent {crore(empty[0])}, fees "
        f"{crore(empty[1])}, power {crore(empty[2])}); the 11 tagged regions hold the "
        f"other {crore(sum(totals) - sum(empty))} ({crore(sum(totals))} less "
        f"{crore(sum(empty))})."
    )
    unit = f" {header_unit}" if header_unit else ""
    rows = [f"| Region | Rent{unit} | Fees{unit} | Power{unit} | Net{unit} |",
            "|---|---|---|---|---|"]
    for i, group in enumerate(GROUPS):
        cells = [values[i] for _label, values in FIELDS]
        name = "Untagged" if group == "(none)" else group
        rows.append(f"| {name} | " + " | ".join(lakh(v) for v in cells)
                    + f" | {lakh(sum(cells))} |")
    if total_row:
        rows.append("| Total | " + " | ".join(lakh(t) for t in totals)
                    + f" | {lakh(sum(totals))} |")
    return f"{head}\n\n{caption}\n\n" + "\n".join(rows)


@pytest.fixture
def ledger() -> RunLedger:
    """The ledger of the rebuilt run: a list read, then three sums."""
    book = RunLedger()
    book.record("crm_list_module_records", "Collections: showing 25 of 8,681 records.")
    for text in RESULTS:
        book.record("crm_aggregate", text, arguments=ARGS)
    return book


def _failed(book: RunLedger, text: str) -> list[str]:
    return [f.text for f in book.check(text).unmatched]


# ── 1. The run of 2026-10-19 passes now ─────────────────────────────────────

@pytest.mark.parametrize(("caption", "header_unit"), [
    ("Net amounts by region:", ""),                 # no unit at all, as in the run
    ("Net amounts by region (₹ lakh):", ""),        # the caption names the unit
    ("Net amounts by region:", "(₹ lakh)"),         # each header names it
    ("Net amounts by region, in lakh:", ""),
])
def test_the_answer_of_the_run_passes_with_no_note(ledger: RunLedger, caption: str,
                                                    header_unit: str) -> None:
    text = owner_answer(caption, header_unit, total_row=True)
    result = ledger.check(text)
    assert len(result.figures) > 60
    assert result.ok, [f.text for f in result.unmatched]
    assert note_text(result, ledger) == (NOTE_NONE, "")


def test_the_check_of_round_7_failed_the_same_figures_as_the_run(ledger: RunLedger) -> None:
    """The rebuild is faithful: with the derive passes and the table units
    turned off, the check fails the figures that the note of the run named,
    in the same order."""
    import acb_common.number_check as nc

    original_passes, original_layout = nc._DERIVE_PASSES, nc._table_layout
    try:
        nc._DERIVE_PASSES = 1
        nc._table_layout = lambda text, figures, sources: (figures, None)
        failed = _failed(ledger, owner_answer())
    finally:
        nc._DERIVE_PASSES, nc._table_layout = original_passes, original_layout
    assert failed[:5] == ["₹10.64 crore", "₹32.94 crore", "₹22.30 crore", "959.8", "592.6"]


# ── 2. A sum with a colon list, a difference with "less" ──────────────────

def test_a_total_with_a_colon_list_of_tool_figures_passes(ledger: RunLedger) -> None:
    assert ledger.check("They total ₹32.94 crore: rent ₹13.99 crore, fees ₹9.19 crore, "
                        "power ₹9.75 crore.").ok


def test_a_colon_list_whose_parts_do_not_add_up_fails(ledger: RunLedger) -> None:
    assert _failed(ledger, "They total ₹33.94 crore: rent ₹13.99 crore, fees ₹9.19 crore, "
                           "power ₹9.75 crore.") == ["₹33.94 crore"]


def test_a_total_of_parts_that_are_sums_passes(ledger: RunLedger) -> None:
    """A part may be a sum that the answer showed first (round 8)."""
    text = ("Rent and fees total ₹23.18 crore: rent ₹13.99 crore and fees ₹9.19 crore. "
            "With power the total is ₹32.94 crore: ₹23.18 crore and power ₹9.75 crore.")
    assert ledger.check(text).ok


@pytest.mark.parametrize("words", [
    "the other {d} ({a} less {b})",
    "the other {d} ({a} minus {b})",
    "{d}, that is {a} less {b}",
])
def test_a_difference_of_two_derived_figures_passes(ledger: RunLedger, words: str) -> None:
    head = ("All three total ₹32.94 crore: rent ₹13.99 crore, fees ₹9.19 crore, power "
            "₹9.75 crore. Untagged records hold ₹22.30 crore (rent ₹9.60 crore, fees "
            "₹5.93 crore, power ₹6.78 crore), and the tagged regions hold ")
    tail = words.format(d="₹10.64 crore", a="₹32.94 crore", b="₹22.30 crore")
    assert ledger.check(head + tail + ".").ok


def test_a_wrong_difference_of_two_derived_figures_fails(ledger: RunLedger) -> None:
    text = ("All three total ₹32.94 crore: rent ₹13.99 crore, fees ₹9.19 crore, power "
            "₹9.75 crore. Untagged records hold ₹22.30 crore (rent ₹9.60 crore, fees "
            "₹5.93 crore, power ₹6.78 crore), and the tagged regions hold the other "
            "₹10.84 crore (₹32.94 crore less ₹22.30 crore).")
    assert _failed(ledger, text) == ["₹10.84 crore"]


def test_a_restated_figure_needs_three_digits() -> None:
    """Only a figure of three significant digits states a derived value
    again, so a table of short rates cannot pass itself by chance."""
    book = Sources()
    book.add_text("- A: 100,000\n- B: 50,000\n- C: 70,000", derive=True)
    head = "A and B total ₹1.50 lakh: A ₹1.00 lakh and B ₹0.50 lakh."
    assert check(head, book).ok
    assert check(head + " Again: ₹1.50 lakh.", book).ok
    assert [f.text for f in check(head + " Again: ₹1.5 lakh.", book).unmatched] == ["₹1.5 lakh"]


def test_a_difference_of_two_module_totals_passes(ledger: RunLedger) -> None:
    """A module total has no currency sign, so its value is plain. A money
    figure still derives from it (round 8)."""
    assert ledger.check("Rent is ₹13.99 crore and fees ₹9.19 crore, a gap of ₹4.80 crore.").ok
    assert not ledger.check("Rent is ₹13.99 crore and fees ₹9.19 crore, a gap of ₹4.90 crore.").ok


# ── 3. A table in lakh or crore ─────────────────────────────────────────────

def test_a_wrong_cell_of_a_lakh_column_still_fails(ledger: RunLedger) -> None:
    text = owner_answer().replace("| Alpha | 92.1 |", "| Alpha | 97.1 |")
    # The net of the row fails too: one of its parts is no CRM figure.
    assert _failed(ledger, text) == ["97.1", "216.6"]


def test_a_wrong_net_cell_fails(ledger: RunLedger) -> None:
    text = owner_answer().replace("| 216.6 |", "| 226.6 |")
    assert _failed(ledger, text) == ["226.6"]


def test_a_wrong_total_row_fails(ledger: RunLedger) -> None:
    text = owner_answer(total_row=True)
    right = lakh(sum(RENT))
    assert _failed(ledger, text.replace(f"| Total | {right} |", "| Total | 1500.0 |")) \
        == ["1500.0"]


def test_a_crore_caption_scales_the_table() -> None:
    book = Sources()
    book.add_text("Deals, sum of Amount by Stage:\n- Won: 135,300,000\n- Lost: 22,450,000\n"
                  "- Open: 59,000,000\nTotal: 216,750,000.", derive=True)
    text = ("Amounts by stage (in crore):\n\n| Stage | Amount |\n|---|---|\n"
            "| Won | 13.53 |\n| Lost | 2.25 |\n| Open | 5.90 |\n| Total | 21.68 |")
    assert check(text, book).ok
    assert not check(text.replace("13.53", "14.53"), book).ok


def test_a_count_column_keeps_its_counts() -> None:
    book = Sources()
    book.add_text("Deals by stage:\n- Won: 20 records · INR 135,300,000\n"
                  "- Lost: 7 records · INR 22,450,000\n- Open: 25 records · INR 59,000,000",
                  derive=True)
    text = ("Deals (₹ lakh):\n\n| Stage | Deals | Amount |\n|---|---|---|\n"
            "| Won | 20 | 1353.0 |\n| Lost | 7 | 224.5 |\n| Open | 25 | 590.0 |")
    assert check(text, book).ok
    assert not check(text.replace("| 7 |", "| 9 |"), book).ok


def test_a_figure_in_a_caption_names_no_unit() -> None:
    """"total ₹32.94 crore:" above a table is a figure, not the unit of the
    table, so the bare cells stay as they stand."""
    book = Sources()
    book.add_text("- A: 12\n- B: 30\n- C: 41", derive=True)
    text = "Pipeline total ₹32.94 crore:\n\n| Name | Deals |\n|---|---|\n| A | 12 |\n| B | 30 |"
    figures = check(text, book).figures
    assert [f.value for f in figures if f.text in ("12", "30")] == [Decimal(12), Decimal(30)]


# ── 4. The count of a list ──────────────────────────────────────────────────

def test_the_count_of_a_list_and_of_its_filled_groups_are_sources() -> None:
    assert list_counts(RESULTS[0]) == [12, 11]
    assert list_counts("- Open: 1\n- Won: 2") == []          # two lines are too few


def test_the_count_of_tagged_groups_passes(ledger: RunLedger) -> None:
    assert ledger.check("The 11 tagged regions and 12 groups in all.").ok
    assert not ledger.check("The 13 tagged regions.").ok


# ── 5. The note ─────────────────────────────────────────────────────────────

def test_a_sum_that_the_answer_does_not_explain_gets_one_sentence(ledger: RunLedger) -> None:
    result = ledger.check("Rent is ₹13.99 crore and fees are ₹9.19 crore. Plan for ₹4.80 crore.")
    assert note_text(result, ledger) == (
        "explained", "\n\n₹4.80 crore is ₹13.99 crore less ₹9.19 crore.")


def test_an_explanation_needs_operands_as_precise_as_the_figure() -> None:
    """With no list read, the record count fails. Two table cells in lakh
    ("32.1" each) differ by 0, and their room of ₹5,000 must not make
    "8,681 is 32.1 less 32.1" a reason."""
    book = RunLedger()
    for text in RESULTS:
        book.record("crm_aggregate", text, arguments=ARGS)
    result = book.check(owner_answer())
    assert [f.text for f in result.unmatched] == ["8,681"]
    assert note_text(result, book) == (NOTE_NAMED,
                                       "\n\n" + NOTE_MISSING.format(figure="8,681"))


def test_a_few_failed_figures_each_get_a_reason(ledger: RunLedger) -> None:
    result = ledger.check("Rent is ₹13.99 crore and power ₹9.75 crore. Alpha holds rent "
                          "₹92.1 lakh, fees ₹61.1 lakh and power ₹63.4 lakh, and Bravo rent "
                          "₹73.4 lakh and fees ₹50.2 lakh. Fees are ₹9.30 crore, a target of ₹4.10 lakh and 23% "
                          "of plan.")
    # Three failed figures of ten (30%) are few, so each gets its reason.
    assert (len(result.figures), len(result.unmatched)) == (10, 3)
    kind, note = note_text(result, ledger)
    assert kind == NOTE_NAMED
    assert note == "\n\n" + "\n".join([
        NOTE_NEAR.format(figure="₹9.30 crore", near="₹9.19 crore"),
        NOTE_MISSING.format(figure="₹4.10 lakh"),
        NOTE_PERCENT.format(figure="23%"),
    ])


def test_more_than_three_failed_figures_name_none(ledger: RunLedger) -> None:
    text = "Plans: ₹1.11 lakh, ₹2.22 lakh, ₹3.33 lakh, ₹4.44 lakh and ₹5.55 lakh."
    kind, note = note_text(ledger.check(text), ledger)
    assert kind == NOTE_HOW
    assert note == "\n\n" + "\n".join([
        HOW_HEAD,
        "- Collections, sum of Rent Amount by Region. No filter.",
        "- Collections, sum of Fee Amount by Region. No filter.",
        "- Collections, sum of Power Amount by Region. No filter.",
        HOW_TAIL,
    ])
    for figure in ("1.11", "2.22", "5.55"):
        assert figure not in note


def test_a_large_share_of_failed_figures_names_none(ledger: RunLedger) -> None:
    """Two failed figures of five (40%) are a limit of the check. One of
    three is a real doubt, and it gets its reason."""
    from acb_skills.answer_check import many_failed

    two = ledger.check("Rent ₹13.99 crore, fees ₹9.19 crore, power ₹9.75 crore. "
                       "Plans ₹1.11 lakh and ₹2.22 lakh.")
    assert (len(two.figures), len(two.unmatched)) == (5, 2)
    assert note_text(two, ledger)[0] == NOTE_HOW
    one = ledger.check("Rent ₹13.99 crore, fees ₹9.19 crore. Plan ₹1.11 lakh.")
    assert note_text(one, ledger)[0] == NOTE_NAMED
    assert [many_failed(f, n) for f, n in ((1, 2), (2, 7), (3, 9), (4, 100), (3, 10))] == [
        False, False, True, True, False]


def test_an_empty_group_that_the_answer_states_becomes_one_question(ledger: RunLedger) -> None:
    text = ("Alpha rent is ₹92.1 lakh. Untagged rent is ₹9.60 crore, so plan to tag "
            "₹1.11 lakh of it.")
    kind, note = note_text(ledger.check(text), ledger)
    assert kind == NOTE_QUESTION
    assert note == ('\n\nIn Collections, 69% of the sum of Rent Amount has no Region. '
                    'Should I show those records as their own group, "No Region", or '
                    'group them by another field?')


def test_an_empty_group_that_the_answer_does_not_state_asks_nothing(ledger: RunLedger) -> None:
    kind, _note = note_text(ledger.check("Rent is ₹13.99 crore, and a target of ₹4.10 lakh."),
                            ledger)
    assert kind == NOTE_NAMED


def test_an_empty_group_beside_an_unrelated_failure_asks_nothing(ledger: RunLedger) -> None:
    """The record count fails in its own sentence. The empty group is
    stated in another one, so it is no cause, and the count gets its
    reason."""
    text = "There are 8,682 records. Untagged rent is ₹9.60 crore and Alpha ₹92.1 lakh."
    kind, note = note_text(ledger.check(text), ledger)
    assert (kind, note) == (NOTE_NAMED, "\n\n" + NOTE_NEAR.format(figure="8,682", near="8,681"))


def test_an_open_crm_question_of_the_run_is_the_question() -> None:
    book = RunLedger()
    book.record("crm_aggregate", "CLARIFY\nNot clear: which field holds the amount.",
                arguments=ARGS)
    book.record("crm_aggregate", RESULTS[0], arguments=ARGS)
    kind, note = note_text(book.check("Rent is ₹13.99 crore, a target of ₹4.10 lakh."), book)
    assert (kind, note) == (NOTE_QUESTION, "\n\n" + QUESTION_OPEN)


def test_the_empty_group_needs_a_tenth_of_the_total() -> None:
    small = "Deals, sum of Amount by Owner:\n- (none): 5,000\n- Ann: 95,000\n- Bob: 100,000\n" \
            "Total: 200,000."
    assert empty_group(small) is None
    assert empty_group(RESULTS[0])[:4] == ("Collections", "sum of Rent Amount", "Region", 69)


def test_a_read_line_has_plain_words_and_its_filters() -> None:
    core = "Deals, sum_amount by status:\n- Open: INR 1,234,567\nTotal: INR 1,234,567."
    assert read_line(core, {"entity": "deals", "status": "open"}) == (
        "Deals, sum of the amount by status. Filters: status is open.")
    module = ("Site_Visits, count:  42 across 42 matching records.\n"
              "Filters: Region is North (counted North and NORTH).")
    assert read_line(module, {}) == (
        "Site Visits, count. Filters: Region is North (counted North and NORTH).")
    assert read_line("", {}) is None
    assert read_line("x", {"kind": "win-loss"}, "crm_report") == (
        "The win and loss report of deals. No filter.")


def test_the_note_has_no_internal_name(ledger: RunLedger) -> None:
    for text in ("Plans: ₹1.11 lakh, ₹2.22 lakh, ₹3.33 lakh, ₹4.44 lakh and ₹5.55 lakh.",
                 "Untagged rent is ₹9.60 crore. Plans ₹1.11 lakh.",
                 "Rent is ₹13.99 crore, and a target of ₹4.10 lakh."):
        _kind, note = note_text(ledger.check(text), ledger)
        for word in ("module:", "sum_amount", "crm_", "tool", "source", "answer_check",
                     "Please verify", "_"):
            assert word not in note, (word, note)


# ── 6. The middleware ───────────────────────────────────────────────────────

class _Context:
    messages: tuple[Any, ...] = ()


def test_the_middleware_adds_the_note_and_logs_its_kind(ledger: RunLedger,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[dict[str, Any]] = []

    class _Log:
        def info(self, event: str, **fields: Any) -> None:
            logged.append({"event": event, **fields})

        def warning(self, event: str, **fields: Any) -> None:
            logged.append({"event": event, **fields})

    monkeypatch.setattr(answer_check, "_log", _Log())
    ledger.retried = True
    text = "Plans: ₹1.11 lakh, ₹2.22 lakh, ₹3.33 lakh, ₹4.44 lakh and ₹5.55 lakh."
    response = ChatResponse(messages=[Message("assistant", [Content.from_text(text)])])
    out = asyncio.run(CheckAnswers(ledger, ON)._checked(_Context(), lambda: None, response))
    assert out.text.startswith(text + "\n\n" + HOW_HEAD)
    assert logged[-1]["outcome"] == "caveat" and logged[-1]["note"] == NOTE_HOW
    assert "₹1.11" not in json.dumps(logged, ensure_ascii=False)


def test_a_failed_answer_whose_figures_all_explain_goes_out_unchanged_but_one_line(
        ledger: RunLedger) -> None:
    ledger.retried = True
    text = "Rent is ₹13.99 crore and fees are ₹9.19 crore. Plan for ₹4.80 crore."
    response = ChatResponse(messages=[Message("assistant", [Content.from_text(text)])])
    out = asyncio.run(CheckAnswers(ledger, ON)._checked(_Context(), lambda: None, response))
    assert out.text == text + "\n\n₹4.80 crore is ₹13.99 crore less ₹9.19 crore."


def test_shadow_logs_the_kind_of_note_that_on_would_add(ledger: RunLedger,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[dict[str, Any]] = []

    class _Log:
        def info(self, event: str, **fields: Any) -> None:
            logged.append(fields)

        def warning(self, event: str, **fields: Any) -> None:
            logged.append(fields)

    monkeypatch.setattr(answer_check, "_log", _Log())
    text = "Rent is ₹13.99 crore, and a target of ₹4.10 lakh."

    class _ShadowContext:
        result = ChatResponse(messages=[Message("assistant", [Content.from_text(text)])])

    CheckAnswers(ledger, SHADOW)._shadow(_ShadowContext())
    assert logged == [{"mode": SHADOW, "numbers": 2, "mismatches": 1,
                       "outcome": "shadow_mismatch", "note": NOTE_NAMED}]


def test_a_note_that_raises_sends_the_answer_unchanged(ledger: RunLedger,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: Any) -> tuple[str, str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(answer_check, "note_text", _boom)
    ledger.retried = True
    text = "A target of ₹4.10 lakh."
    response = ChatResponse(messages=[Message("assistant", [Content.from_text(text)])])
    out = asyncio.run(CheckAnswers(ledger, ON)._checked(_Context(), lambda: None, response))
    assert out.text == text


# ── 7. The replay script ────────────────────────────────────────────────────

def test_the_replay_counts_the_last_check_of_each_run() -> None:
    from scripts.crm_answer_check_replay import journal_kinds, kind_from_counts, parse_journal

    lines = [
        "\x1b[2mT\x1b[0m [info] answer_check.checked mismatches=14 mode=on numbers=15 "
        "outcome=retry run_id=a",
        "answer_check.checked mismatches=49 mode=on numbers=65 outcome=caveat run_id=a",
        "answer_check.checked mismatches=0 mode=shadow numbers=4 outcome=shadow_pass run_id=b",
        "answer_check.checked mismatches=1 mode=shadow numbers=9 outcome=shadow_mismatch "
        "note=explained run_id=c",
        "executor.answer_check_on mode=on run_id=a",
    ]
    runs = parse_journal(lines)
    assert sorted(runs) == ["a", "b", "c"] and runs["a"]["numbers"] == "65"
    assert dict(journal_kinds(runs)) == {"how": 1, "none": 1, "explained": 1}
    assert [kind_from_counts(n, m) for n, m in ((5, 0), (9, 1), (9, 3), (12, 3))] == [
        "none", "named", "how", "named"]


def test_the_replay_runs_a_stored_answer_through_round_8() -> None:
    from scripts.crm_answer_check_replay import case_kind, cases_from_rows

    tools = [{"name": "crm_list_module_records", "args": {},
              "result": "Collections: showing 25 of 8,681 records."}]
    tools += [{"name": "crm_aggregate", "args": ARGS, "result": text} for text in RESULTS]
    rows = [
        {"session_id": "s", "role": "user", "content": "By region?", "tool_events": []},
        # The stored answer still holds the note of round 7, and the replay
        # takes it off before the check.
        {"session_id": "s", "role": "assistant", "tool_events": tools,
         "content": owner_answer() + "\n\nI could not check these figures against the "
                                     "CRM data: 959.8. Please verify them."},
        {"session_id": "t", "role": "assistant", "content": "Hi.", "tool_events": []},
    ]
    cases = cases_from_rows(rows)
    assert cases[0]["member_message"] == "By region?"
    assert [case_kind(case) for case in cases] == [NOTE_NONE, "not_armed"]


# ── 8. Generic for every org ────────────────────────────────────────────────

def test_the_round_8_product_code_names_no_word_of_one_org() -> None:
    from tests.unit.test_crm_answer_consistency import ORG_WORDS

    for rel in ("packages/acb_common/acb_common/number_check.py",
                "packages/acb_skills/acb_skills/answer_check.py",
                "scripts/crm_answer_check_replay.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not ORG_WORDS.search(text), (rel, ORG_WORDS.search(text))
        for word in ("Shaurr", "Fracktal", "Collection", "Rent", "EB "):
            assert word not in text, (rel, word)
