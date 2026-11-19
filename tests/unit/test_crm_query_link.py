"""The link of a word to a stored field (WS-SEM SEM-2a, task 3733).

Database-free, so it never skips. ``crm_sync.terms.link_field`` is pure: the
caller passes the fields that the member may use, and the word of the model.

A wrong link is worse than the refusal of today. So the function links a
field only when the name is certain, and otherwise it only suggests.

1. The ladder: the exact key, the admin's answer of the term, the key in any
   case, then the folded label. Only these four steps link (``linked``).
   The ``near`` step fits the words of the word with ``terms._hit``, and it
   suggests only, for the caller to name in a refusal.
2. A close pair never picks: two or more fields at a link step give each key
   (at most three, and ``more`` counts the rest) and the step ``ambiguous``.
3. A key word (``@amount``) and a sum of keys (``a+b``) pass through, so the
   caller keeps its old path. A word over 80 characters, an empty word and a
   word of marks only give ``none``.
4. The result never holds the word. Only our stored keys go in it.
5. The purity fence: ``terms.py`` imports only the standard library and the
   pure ``crm_sync.catalogue``, and logs nothing.

Non-vacuity (each mutation was run once, and the named test went red):

* swap the ``exact`` and ``label`` steps: ``test_the_exact_key_of_another_
  field_beats_its_label`` fails;
* return the first of two fits: ``test_two_fits_give_both_keys_and_pick_none``
  fails;
* drop the pass-through: ``test_a_key_word_and_a_sum_pass_through`` fails;
* let the ``near`` step link: ``test_a_general_word_never_links_the_one_field_
  that_holds_it`` fails;
* fold ``%`` away: ``test_a_percent_label_folds_apart_from_its_amount`` fails;
* run the ``term`` step after ``case``: ``test_an_admin_answer_comes_before_
  the_key_in_another_case`` fails.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest
from gateway.crm_sync import terms
from gateway.crm_sync.terms import Link, link_field

REPO = Path(__file__).resolve().parents[2]
TERMS_PY = REPO / "apps/services/gateway/gateway/crm_sync/terms.py"


@dataclasses.dataclass(frozen=True)
class F:
    """A field as the callers hold it: a key, a label and a type."""

    key: str
    label: str = ""
    type: str = "text"
    visible: bool = True


def link(*keys: str, step: str, money: bool = False) -> Link:
    """A result that links: the caller may run the key."""
    return Link(keys=keys, step=step, money=money, linked=True)


def hint(*keys: str, step: str, money: bool = False, more: int = 0) -> Link:
    """A result that does not link: the caller may only name the keys."""
    return Link(keys=keys, step=step, money=money, linked=False, more=more)


FIELDS = (
    F("Deal_Stage", "Pipeline Step", "select"),
    F("Amt_Billed", "Billing Amount", "currency"),
    F("Closing_Date", "Close Date", "date"),
    F("Owner_Name", "", "text"),
)


# ── 1. The ladder ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", FIELDS, ids=lambda f: f.key)
def test_each_stored_key_links_to_itself(field: F) -> None:
    assert link_field(FIELDS, field.key) == link(
        field.key, step="exact", money=field.type in terms.MONEY_TYPES)


@pytest.mark.parametrize("word", ["deal_stage", "DEAL_STAGE", "dEaL_sTaGe"])
def test_a_key_in_another_case_gives_the_stored_key(word: str) -> None:
    assert link_field(FIELDS, word) == link("Deal_Stage", step="case")


@pytest.mark.parametrize("word", [
    "Billing Amount", "billing amount", "BILLING AMOUNT", "billing_amount",
    "billing-amount", "Billing  Amount", "billingamount", " Billing Amount ",
])
def test_a_label_in_any_case_or_spacing_gives_its_key(word: str) -> None:
    assert link_field(FIELDS, word) == link("Amt_Billed", step="label", money=True)


def test_a_field_with_no_label_reads_its_key_as_the_label() -> None:
    assert link_field(FIELDS, "owner name") == link("Owner_Name", step="label")


def test_the_exact_key_of_another_field_beats_its_label() -> None:
    # "Stage" is the label of one field and the key of another.
    fields = [F("Status", "Stage", "select"), F("Stage", "Pipeline", "select")]
    assert link_field(fields, "Stage") == link("Stage", step="exact")
    # The key in another case does NOT beat a label of equal certainty: two
    # fields name the word, so the function picks none.
    assert link_field(fields, "stage") == hint("Stage", "Status", step="ambiguous")


def test_a_near_fit_runs_only_when_no_label_fits_and_never_links() -> None:
    fields = [F("Rent_Paid", "Rent", "currency"), F("Rent_Due", "Rent Due", "currency")]
    assert link_field(fields, "rent") == link("Rent_Paid", step="label", money=True)
    assert link_field(fields, "rent due") == link("Rent_Due", step="label", money=True)
    assert link_field(fields, "due") == hint("Rent_Due", step="near", money=True)


def test_a_near_fit_takes_a_plural_by_the_hit_rule() -> None:
    fields = [F("Deposits_Held", "Deposits Held", "currency")]
    assert link_field(fields, "deposit held") == hint("Deposits_Held", step="near", money=True)


def test_a_near_fit_needs_every_word_of_the_word() -> None:
    # Stricter than terms.candidates: no general word drops, so "deal amount"
    # never suggests a text field that holds "deal" only.
    fields = [F("Deal_Name", "Deal Name", "text"), F("Amount", "Amount", "currency")]
    assert link_field(fields, "deal amount") == Link()


@pytest.mark.parametrize(("fields", "word", "near"), [
    # The amount of an invoice is the grand total, not the one field that
    # holds the word.
    ([F("Amount_Received", "Amount Received", "currency"),
      F("Grand_Total", "Grand Total", "currency")], "amount", "Amount_Received"),
    ([F("GSTIN", "GSTIN", "text"), F("Tax_Total", "Tax Total", "currency")], "gst", "GSTIN"),
    ([F("Customer_No", "Customer No", "text")], "customer", "Customer_No"),
    ([F("Paid_On", "Paid On", "date")], "paid", "Paid_On"),
])
def test_a_general_word_never_links_the_one_field_that_holds_it(
    fields: list[F], word: str, near: str,
) -> None:
    found = link_field(fields, word)
    assert found.step == "near" and found.linked is False
    assert found.keys == (near,)


@pytest.mark.parametrize("word", ["in", "id", "fe", "fa", "no", "do"])
def test_a_word_under_three_letters_is_no_near_fit(word: str) -> None:
    fields = [F("GSTIN", "GSTIN", "text"), F("Deal_Id", "Deal Id", "text"),
              F("Fees", "Fees", "currency"), F("Fax_No", "Fax No", "text"),
              F("Doc_Ref", "Doc Ref", "text")]
    assert link_field(fields, word) == Link()


def test_a_short_word_beside_a_longer_one_drops_from_the_near_fit() -> None:
    fields = [F("GSTIN", "GSTIN", "text"), F("Tax_Total", "Tax Total", "currency")]
    # "gst in" would fold to the label "GSTIN" and link it, so the test takes
    # "gst no": "no" drops, and "gst" alone only suggests.
    assert link_field(fields, "gst no") == hint("GSTIN", step="near")


# ── The percent mark of a label ─────────────────────────────────────────────

def test_a_percent_label_folds_apart_from_its_amount() -> None:
    fields = [F("GST_Pct", "GST %", "percent"), F("GST_Amount", "GST Amount", "currency")]
    # "gst" is the label of neither. Before the fix, "GST %" folded to "gst".
    found = link_field(fields, "gst")
    assert found.linked is False and found.step == "near"
    assert found.keys == ("GST_Pct", "GST_Amount")
    assert link_field(fields, "GST %") == link("GST_Pct", step="label")
    assert link_field(fields, "gst percent") == link("GST_Pct", step="label")
    assert link_field(fields, "gst amount") == link("GST_Amount", step="label", money=True)


def test_a_discount_word_does_not_link_the_discount_percent() -> None:
    fields = [F("Discount_Pct", "Discount %", "percent")]
    found = link_field(fields, "discount")
    assert found.linked is False and found.step == "near"
    assert link_field(fields, "Discount %") == link("Discount_Pct", step="label")


def test_the_fold_changes_only_the_percent_mark() -> None:
    assert terms._fold("Sales Orders") == terms._fold("sales_orders") == "salesorders"
    assert terms._fold("GST %") == "gstpercent"
    assert terms._fold("") == ""


# ── The admin's answer of the term ──────────────────────────────────────────

def _answers(module: str, word: str, key: str) -> dict[str, str]:
    return {terms.term_id(module, word): key}


TWO_FEES = [F("Fee_A", "Service Fee", "currency"), F("Fee_B", "Late Fee", "currency")]


def test_an_admin_answer_of_the_term_links_its_field() -> None:
    answers = _answers("Invoices", "fee", "Fee_B")
    assert link_field(TWO_FEES, "fee") == hint("Fee_A", "Fee_B", step="near", money=True)
    assert link_field(TWO_FEES, "fee", module="Invoices", answers=answers) == link(
        "Fee_B", step="term", money=True)


def test_an_admin_answer_comes_before_the_key_in_another_case() -> None:
    # As in terms.decide, the admin's answer of "fee" decides first. Only the
    # exact key comes before it.
    fields = [F("Fee", "Fee", "currency"), F("Late_Fee", "Late Fee", "currency")]
    answers = _answers("Invoices", "fee", "Late_Fee")
    assert link_field(fields, "fee", module="Invoices", answers=answers) == link(
        "Late_Fee", step="term", money=True)
    pick = terms.decide([terms.Candidate("Fee", "Fee"), terms.Candidate("Late_Fee", "Late Fee")],
                        answer=answers[terms.term_id("Invoices", "fee")])
    assert pick.field == "Late_Fee"
    assert link_field(fields, "Fee", module="Invoices", answers=answers) == link(
        "Fee", step="exact", money=True)


def test_an_answer_that_names_a_field_not_passed_is_ignored() -> None:
    answers = _answers("Invoices", "fee", "Gone_Field")
    assert link_field(TWO_FEES, "fee", module="Invoices", answers=answers).step == "near"


def test_the_term_step_needs_the_module_and_the_answers() -> None:
    answers = _answers("Invoices", "fee", "Fee_B")
    assert link_field(TWO_FEES, "fee", answers=answers).step == "near"
    assert link_field(TWO_FEES, "fee", module="Invoices").step == "near"
    assert link_field(TWO_FEES, "fee", module="Orders", answers=answers).step == "near"


def test_a_word_with_a_cut_slug_reads_no_answer() -> None:
    long_word = "service fee charged monthly for the account"
    assert len("-".join(terms.words(long_word))) >= terms.SLUG_MAX
    other = "service fee charged monthly for the account of last year"
    assert terms.term_id("Invoices", long_word) == terms.term_id("Invoices", other)
    answers = _answers("Invoices", long_word, "Fee_B")
    assert link_field(TWO_FEES, long_word, module="Invoices", answers=answers).step != "term"
    assert link_field(TWO_FEES, other, module="Invoices", answers=answers).step != "term"


def test_a_word_with_a_letter_of_another_script_reads_no_answer() -> None:
    answers = _answers("Invoices", "fee", "Fee_B")
    word = "राशि fee"
    assert terms.term_id("Invoices", word) == terms.term_id("Invoices", "fee")
    found = link_field(TWO_FEES, word, module="Invoices", answers=answers)
    assert found.step != "term" and found.linked is False


def test_a_percent_word_reads_no_answer_of_its_bare_word() -> None:
    fields = [F("GST_Pct", "GST %", "percent"), F("GST_Amount", "GST Amount", "currency")]
    answers = _answers("Invoices", "gst", "GST_Amount")
    assert terms.term_id("Invoices", "GST %") == terms.term_id("Invoices", "gst")
    found = link_field(fields, "GST %", module="Invoices", answers=answers)
    assert found.step == "label" and found.keys == ("GST_Pct",)


@pytest.mark.parametrize("label, word", [("दान", "दिन"), ("மூல", "மல"), ("कुल", "काल")])
def test_two_words_of_an_indic_script_never_fold_to_one(label: str, word: str) -> None:
    found = link_field([F("Donation", label, "currency")], word)
    assert found.linked is False and found.step != "label"


def test_the_case_step_sees_the_label_of_a_second_field() -> None:
    fields = [F("Amount", "Advance", "currency"), F("Amount1", "Amount", "currency")]
    assert link_field(fields, "amount") == hint("Amount", "Amount1", step="ambiguous",
                                                money=True)


# ── 2. A close pair never picks ─────────────────────────────────────────────

def test_two_fits_give_both_keys_and_pick_none() -> None:
    fields = [F("Net_A", "Net Total", "currency"), F("Net_B", "net-total", "currency")]
    assert link_field(fields, "net total") == hint("Net_A", "Net_B", step="ambiguous",
                                                   money=True)


def test_two_keys_that_differ_in_case_only_are_ambiguous() -> None:
    fields = [F("amount", "One", "currency"), F("Amount", "Two", "number")]
    assert link_field(fields, "Amount") == link("Amount", step="exact", money=True)
    assert link_field(fields, "AMOUNT") == hint("amount", "Amount", step="ambiguous",
                                                money=True)


def test_more_than_three_fits_give_three_keys_and_count_the_rest() -> None:
    fields = [F(f"Net_{n}", "Net Total", "currency") for n in range(5)]
    assert link_field(fields, "net total") == hint(
        "Net_0", "Net_1", "Net_2", step="ambiguous", money=True, more=2)
    near = [F(f"Rent_{n}", f"Rent {n}", "currency") for n in range(5)]
    assert link_field(near, "rent") == hint(
        "Rent_0", "Rent_1", "Rent_2", step="near", money=True, more=2)
    assert link_field(near[:3], "rent").more == 0


def test_money_is_a_hint_true_only_when_each_key_is_a_money_field() -> None:
    fields = [F("Rent_Paid", "Rent Paid", "currency"), F("Rent_Note", "Rent Note", "text")]
    mixed = link_field(fields, "rent")
    assert mixed.step == "near" and mixed.money is False
    assert link_field(fields, "Rent_Paid").money is True
    assert link_field(fields, "Rent_Note").money is False
    assert link_field(fields, "nothing here").money is False


@pytest.mark.parametrize("word", [
    "Deal_Stage", "deal_stage", "close date", "billing", "@amount", "", "x" * 81,
    "pipeline", "nothing",
])
def test_linked_is_true_only_for_a_link_step(word: str) -> None:
    found = link_field(FIELDS, word)
    assert found.linked == (found.step in terms.LINK_STEPS), found
    assert terms.LINK_STEPS == ("exact", "term", "case", "label")


# ── 3. Pass-through and the bounds ──────────────────────────────────────────

@pytest.mark.parametrize("word", [
    "@amount", "@date", "@status", "@parent", " @amount",
    "Rent_Paid+Rent_Due",
    # A legal sum of five keys is longer than TERM_MAX, and still passes.
    "+".join(f"Longer_Field_Key_{n}" for n in range(5)),
])
def test_a_key_word_and_a_sum_pass_through(word: str) -> None:
    fields = [F("Rent_Paid", "Rent Paid", "currency"), F("Rent_Due", "Rent Due", "currency")]
    assert link_field(fields, word) == Link(keys=(), step="passthrough")


@pytest.mark.parametrize("word", ["", "   ", "!!!", "-_-", "?", "x" * (terms.TERM_MAX + 1)])
def test_a_long_or_empty_or_marks_only_word_gives_none(word: str) -> None:
    fields = [F("x" * (terms.TERM_MAX + 1), "", "text"), F("Rent", "Rent", "currency")]
    assert link_field(fields, word) == Link(keys=(), step="none")


def test_a_word_of_the_longest_length_still_links() -> None:
    key = "K" * terms.TERM_MAX
    assert link_field([F(key)], key) == link(key, step="exact")


def test_a_word_with_no_ascii_word_and_no_label_gives_none() -> None:
    # terms.words() reads ASCII only. With no word, the near step must not
    # fit every field.
    fields = [F("Rent_Paid", "Rent Paid", "currency"), F("Rent_Due", "Rent Due", "currency")]
    assert link_field(fields, "राशि") == Link()


def test_a_label_in_another_script_links_by_the_fold() -> None:
    fields = [F("Size_1", "Größe", "number")]
    assert link_field(fields, "GRÖßE") == link("Size_1", step="label", money=True)


def test_a_word_that_is_not_text_gives_none() -> None:
    assert link_field(FIELDS, None) == Link()  # type: ignore[arg-type]


def test_the_term_max_is_the_one_of_the_route() -> None:
    from gateway.routes.crm import terms as route_terms

    assert terms.TERM_MAX == route_terms.TERM_MAX == 80


# ── 4. The word never goes into the result ──────────────────────────────────

CANARY = "Quokkacanary"


@pytest.mark.parametrize(("fields", "word", "step"), [
    ([F("F_1", "Rent")], CANARY, "none"),
    ([F("F_1", "Quokka Canary")], CANARY, "label"),
    ([F("F_1", "Quokka Canary"), F("F_2", "quokka-canary")], "quokka canary", "ambiguous"),
    ([F("F_1", "Quokka Canary Fee"), F("F_2", "Quokka Canary Tax")], "quokka canary", "near"),
    ([F("F_1", "Rent")], CANARY * 10, "none"),
])
def test_the_result_never_holds_the_word(fields: list[F], word: str, step: str) -> None:
    found = link_field(fields, word)
    assert found.step == step
    shown = repr(found).casefold()
    assert "quokka" not in shown and "canary" not in shown, shown
    assert set(found.keys) <= {f.key for f in fields}


def test_a_link_has_only_keys_a_step_and_three_marks() -> None:
    assert [f.name for f in dataclasses.fields(Link)] == [
        "keys", "step", "money", "linked", "more"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        Link().step = "exact"  # type: ignore[misc]


# ── The fields that the caller passes ───────────────────────────────────────

def test_a_field_the_caller_leaves_out_is_never_a_target() -> None:
    hidden = F("Margin", "Margin", "currency", visible=False)
    shown = [f for f in [*FIELDS, hidden] if f.visible]
    assert link_field(shown, "Margin") == Link()
    assert link_field(shown, "margin") == Link()
    # The function reads no visible flag: the caller decides.
    assert link_field([*FIELDS, hidden], "Margin") == link("Margin", step="exact", money=True)


def test_a_key_word_alias_and_a_sum_are_no_targets() -> None:
    from gateway.routes.crm.field_sql import FieldRef

    paid = FieldRef(key="Rent_Paid", type="currency", label="Rent Paid")
    due = FieldRef(key="Rent_Due", type="currency", label="Rent Due")
    total = FieldRef(key="Rent_Paid+Rent_Due", type="currency", label="Rent Paid + Rent Due",
                     parts=(paid, due))
    # The fields of a module scope: a key word holds the same ref again.
    scope = {"Rent_Paid": paid, "Rent_Due": due, "@amount": paid, "@total": total}
    assert link_field(scope.values(), "rent paid") == link("Rent_Paid", step="label",
                                                           money=True)
    assert link_field(scope.values(), "rent") == hint("Rent_Paid", "Rent_Due", step="near",
                                                      money=True)


def test_a_generator_of_fields_is_read_once_for_every_step() -> None:
    fields = (f for f in FIELDS)
    assert link_field(fields, "close date") == link("Closing_Date", step="label")


# ── 5. The purity fence ─────────────────────────────────────────────────────

#: Each module that terms.py may import. The catalogue is pure too.
ALLOWED_IMPORTS = frozenset({
    "__future__", "re", "collections.abc", "dataclasses", "typing",
    "gateway.crm_sync.catalogue",
})
#: The names of a log call or a print. terms.py holds none of them.
LOG_NAMES = frozenset({"print", "logging", "structlog", "get_logger", "getLogger", "_log"})


def test_terms_imports_no_route_no_database_and_no_network() -> None:
    tree = ast.parse(TERMS_PY.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    assert found <= ALLOWED_IMPORTS, sorted(found - ALLOWED_IMPORTS)
    assert "gateway.crm_sync.catalogue" in found  # the fence reads real imports


def test_terms_logs_nothing() -> None:
    tree = ast.parse(TERMS_PY.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & LOG_NAMES, sorted(names & LOG_NAMES)
    assert "link_field" in {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def test_the_catalogue_imports_nothing_at_the_top_but_the_standard_library() -> None:
    path = REPO / "apps/services/gateway/gateway/crm_sync/catalogue.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top = {node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)}
    top |= {a.name for node in tree.body if isinstance(node, ast.Import) for a in node.names}
    assert top <= {"__future__", "collections.abc", "dataclasses", "typing"}, sorted(top)
