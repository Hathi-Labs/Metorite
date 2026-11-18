"""The link of a word to a stored field (WS-SEM SEM-2a, task 3733).

Database-free, so it never skips. ``crm_sync.terms.link_field`` is pure: the
caller passes the fields that the member may use, and the word of the model.

1. The ladder: the exact key, the key in any case, the folded label, the
   admin's answer of the term, then the words of ``terms._hit``. The first
   step that finds a field decides.
2. A close pair never picks: two or more fields at one step give each key
   (at most three) and the step ``ambiguous``.
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
* drop the pass-through: ``test_a_key_word_and_a_sum_pass_through`` fails.
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


FIELDS = (
    F("Deal_Stage", "Pipeline Step", "select"),
    F("Amt_Billed", "Billing Amount", "currency"),
    F("Closing_Date", "Close Date", "date"),
    F("Owner_Name", "", "text"),
)


# ── 1. The ladder ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", FIELDS, ids=lambda f: f.key)
def test_each_stored_key_links_to_itself(field: F) -> None:
    assert link_field(FIELDS, field.key) == Link(
        keys=(field.key,), step="exact", money=field.type in terms.MONEY_TYPES)


@pytest.mark.parametrize("word", ["deal_stage", "DEAL_STAGE", "dEaL_sTaGe"])
def test_a_key_in_another_case_gives_the_stored_key(word: str) -> None:
    assert link_field(FIELDS, word) == Link(keys=("Deal_Stage",), step="case")


@pytest.mark.parametrize("word", [
    "Billing Amount", "billing amount", "BILLING AMOUNT", "billing_amount",
    "billing-amount", "Billing  Amount", "billingamount", " Billing Amount ",
])
def test_a_label_in_any_case_or_spacing_gives_its_key(word: str) -> None:
    assert link_field(FIELDS, word) == Link(keys=("Amt_Billed",), step="label", money=True)


def test_a_field_with_no_label_reads_its_key_as_the_label() -> None:
    assert link_field(FIELDS, "owner name") == Link(keys=("Owner_Name",), step="label")


def test_the_exact_key_of_another_field_beats_its_label() -> None:
    # "Stage" is the label of one field and the key of another.
    fields = [F("Status", "Stage", "select"), F("Stage", "Pipeline", "select")]
    assert link_field(fields, "Stage") == Link(keys=("Stage",), step="exact")
    # The key in another case beats the label too.
    assert link_field(fields, "stage") == Link(keys=("Stage",), step="case")


def test_the_word_step_runs_only_when_no_label_fits() -> None:
    fields = [F("Rent_Paid", "Rent", "currency"), F("Rent_Due", "Rent Due", "currency")]
    assert link_field(fields, "rent") == Link(keys=("Rent_Paid",), step="label", money=True)
    assert link_field(fields, "rent due") == Link(keys=("Rent_Due",), step="label", money=True)
    assert link_field(fields, "due") == Link(keys=("Rent_Due",), step="word", money=True)


def test_the_word_step_takes_a_plural_by_the_hit_rule() -> None:
    fields = [F("Deposits_Held", "Deposits Held", "currency")]
    assert link_field(fields, "deposit held") == Link(
        keys=("Deposits_Held",), step="word", money=True)


def test_the_word_step_needs_every_word_of_the_word() -> None:
    # Stricter than terms.candidates: no general word drops, so "deal amount"
    # never picks a text field that holds "deal" only.
    fields = [F("Deal_Name", "Deal Name", "text"), F("Amount", "Amount", "currency")]
    assert link_field(fields, "deal amount") == Link()


def _answers(module: str, word: str, key: str) -> dict[str, str]:
    return {terms.term_id(module, word): key}


def test_an_admin_answer_of_the_term_links_its_field() -> None:
    fields = [F("Fee_A", "Service Fee", "currency"), F("Fee_B", "Late Fee", "currency")]
    answers = _answers("Invoices", "fee", "Fee_B")
    assert link_field(fields, "fee") == Link(keys=("Fee_A", "Fee_B"), step="ambiguous",
                                             money=True)
    assert link_field(fields, "fee", module="Invoices", answers=answers) == Link(
        keys=("Fee_B",), step="term", money=True)


def test_an_answer_that_names_a_field_not_passed_is_ignored() -> None:
    fields = [F("Fee_A", "Service Fee", "currency"), F("Fee_B", "Late Fee", "currency")]
    answers = _answers("Invoices", "fee", "Gone_Field")
    assert link_field(fields, "fee", module="Invoices", answers=answers).step == "ambiguous"


def test_the_term_step_needs_the_module_and_the_answers() -> None:
    fields = [F("Fee_A", "Service Fee", "currency"), F("Fee_B", "Late Fee", "currency")]
    answers = _answers("Invoices", "fee", "Fee_B")
    assert link_field(fields, "fee", answers=answers).step == "ambiguous"
    assert link_field(fields, "fee", module="Invoices").step == "ambiguous"
    assert link_field(fields, "fee", module="Orders", answers=answers).step == "ambiguous"


def test_a_label_beats_an_answer_of_the_term() -> None:
    fields = [F("Fee_A", "Fee", "currency"), F("Fee_B", "Late Fee", "currency")]
    answers = _answers("Invoices", "fee", "Fee_B")
    assert link_field(fields, "fee", module="Invoices", answers=answers) == Link(
        keys=("Fee_A",), step="label", money=True)


# ── 2. A close pair never picks ─────────────────────────────────────────────

def test_two_fits_give_both_keys_and_pick_none() -> None:
    fields = [F("Net_A", "Net Total", "currency"), F("Net_B", "net-total", "currency")]
    assert link_field(fields, "net total") == Link(keys=("Net_A", "Net_B"), step="ambiguous",
                                                   money=True)


def test_two_keys_that_differ_in_case_only_are_ambiguous() -> None:
    fields = [F("amount", "One", "currency"), F("Amount", "Two", "number")]
    assert link_field(fields, "Amount") == Link(keys=("Amount",), step="exact", money=True)
    assert link_field(fields, "AMOUNT") == Link(keys=("amount", "Amount"), step="ambiguous",
                                                money=True)


def test_more_than_three_fits_give_the_first_three_keys() -> None:
    fields = [F(f"Rent_{n}", f"Rent {n}", "currency") for n in range(5)]
    found = link_field(fields, "rent")
    assert found.step == "ambiguous"
    assert found.keys == ("Rent_0", "Rent_1", "Rent_2")
    assert len(found.keys) == terms.MAX_CANDIDATES


def test_money_is_true_only_when_each_key_is_a_money_field() -> None:
    fields = [F("Rent_Paid", "Rent Paid", "currency"), F("Rent_Note", "Rent Note", "text")]
    mixed = link_field(fields, "rent")
    assert mixed.step == "ambiguous" and mixed.money is False
    assert link_field(fields, "Rent_Paid").money is True
    assert link_field(fields, "Rent_Note").money is False
    assert link_field(fields, "nothing here").money is False


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
    assert link_field([F(key)], key) == Link(keys=(key,), step="exact")


def test_a_word_with_no_ascii_word_and_no_label_gives_none() -> None:
    # terms.words() reads ASCII only. With no word, the word step must not
    # match every field.
    fields = [F("Rent_Paid", "Rent Paid", "currency"), F("Rent_Due", "Rent Due", "currency")]
    assert link_field(fields, "राशि") == Link()


def test_a_label_in_another_script_links_by_the_fold() -> None:
    fields = [F("Size_1", "Größe", "number")]
    assert link_field(fields, "GRÖßE") == Link(keys=("Size_1",), step="label",
                                                         money=True)


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
    ([F("F_1", "Quokka Canary Fee"), F("F_2", "Quokka Canary Tax")], "quokka canary",
     "ambiguous"),
    ([F("F_1", "Quokka Canary Fee")], "quokka canary", "word"),
    ([F("F_1", "Rent")], CANARY * 10, "none"),
])
def test_the_result_never_holds_the_word(fields: list[F], word: str, step: str) -> None:
    found = link_field(fields, word)
    assert found.step == step
    shown = repr(found).casefold()
    assert "quokka" not in shown and "canary" not in shown, shown
    assert set(found.keys) <= {f.key for f in fields}


def test_a_link_has_only_keys_a_step_and_the_money_mark() -> None:
    assert [f.name for f in dataclasses.fields(Link)] == ["keys", "step", "money"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        Link().step = "exact"  # type: ignore[misc]


# ── The fields that the caller passes ───────────────────────────────────────

def test_a_field_the_caller_leaves_out_is_never_a_target() -> None:
    hidden = F("Margin", "Margin", "currency", visible=False)
    shown = [f for f in [*FIELDS, hidden] if f.visible]
    assert link_field(shown, "Margin") == Link()
    assert link_field(shown, "margin") == Link()
    # The function reads no visible flag: the caller decides.
    assert link_field([*FIELDS, hidden], "Margin") == Link(keys=("Margin",), step="exact",
                                                           money=True)


def test_a_key_word_alias_and_a_sum_are_no_targets() -> None:
    from gateway.routes.crm.field_sql import FieldRef

    paid = FieldRef(key="Rent_Paid", type="currency", label="Rent Paid")
    due = FieldRef(key="Rent_Due", type="currency", label="Rent Due")
    total = FieldRef(key="Rent_Paid+Rent_Due", type="currency", label="Rent Paid + Rent Due",
                     parts=(paid, due))
    # The fields of a module scope: a key word holds the same ref again.
    scope = {"Rent_Paid": paid, "Rent_Due": due, "@amount": paid, "@total": total}
    assert link_field(scope.values(), "rent paid") == Link(
        keys=("Rent_Paid",), step="label", money=True)
    assert link_field(scope.values(), "rent") == Link(
        keys=("Rent_Paid", "Rent_Due"), step="ambiguous", money=True)


def test_a_generator_of_fields_is_read_once_for_every_step() -> None:
    fields = (f for f in FIELDS)
    assert link_field(fields, "close date") == Link(keys=("Closing_Date",), step="label")


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
