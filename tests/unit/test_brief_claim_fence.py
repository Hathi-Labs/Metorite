"""The claim fence of the brief phrase step (WS-BRIEF BR-4b, R7).

Each test names the mutation that it catches.
"""

from __future__ import annotations

import re
from pathlib import Path

from gateway.brief import fence
from gateway.brief.fence import check_phrase

CLAIMS = {
    "c1": "3 tasks are due today",
    "c2": "Invoice for ₹500 is overdue",
    "c3": "Standup at 10:30",
    "c4": "Total 1,200 open",
}


def s(text, *ids):
    return {"text": text, "claims": list(ids)}


def test_a_backed_answer_passes():
    """Catches a fence that rejects a good answer."""
    r = check_phrase([s("3 tasks are due.", "c1"), s("Pay 500.", "c2")], CLAIMS)
    assert r.ok and r.reason is None


def test_more_than_three_sentences_fails():
    """Catches a removed MAX_SENTENCES check."""
    ans = [s("One.", "c1")] * 4
    assert check_phrase(ans, CLAIMS).reason == "too_many_sentences"


def test_a_sentence_with_no_claim_id_fails():
    """Catches a sentence that passes with an empty id list."""
    assert check_phrase([s("Good morning.")], CLAIMS).reason == "no_claim_id"


def test_an_unknown_claim_id_fails():
    """Catches an id check that skips ids outside the run."""
    assert check_phrase([s("Hi.", "c1", "c99")], CLAIMS).reason == "unknown_claim"


def test_an_unbacked_number_fails():
    """Catches a number check that is off."""
    assert check_phrase([s("7 tasks are due.", "c1")], CLAIMS).reason == "unbacked_number"


def test_a_number_held_only_by_an_uncited_claim_fails():
    """Catches a pool of all claims in place of the cited ones."""
    r = check_phrase([s("Pay 500 today.", "c1")], CLAIMS)
    assert r.reason == "unbacked_number"


def test_malformed_input_fails_closed_and_never_raises():
    """Catches a raise or a pass on a bad shape."""
    bad = [None, "x", {}, [], [None], [{}], [{"text": "", "claims": ["c1"]}],
           [{"text": "a", "claims": "c1"}], [{"text": "a", "claims": [1]}],
           [{"text": 5, "claims": ["c1"]}], [{"text": "a"}]]
    for b in bad:
        assert check_phrase(b, CLAIMS).reason == "malformed", b
    assert check_phrase([s("a", "c1")], None).reason == "malformed"


def test_number_forms_follow_the_shared_helper():
    """Catches a private number rule. The rupee sign is dropped, so
    "₹500" is 500. The fence reads a comma between digits as a group mark,
    so "1,200" and "1200" are the same number."""
    assert check_phrase([s("Pay ₹500.", "c2")], CLAIMS).ok
    assert check_phrase([s("Pay 500.", "c2")], CLAIMS).ok
    assert check_phrase([s("Total 1,200.", "c4")], CLAIMS).ok
    assert check_phrase([s("Total 1200.", "c4")], CLAIMS).ok


def test_a_fragment_of_a_grouped_number_is_not_backed():
    """Catches the fence without ``exact=True``: then "1,200" splits into
    "1" and "200", and a claim of 1,200 backs a sentence that says 200."""
    assert check_phrase([s("Total 200.", "c4")], CLAIMS).reason == "unbacked_number"
    assert check_phrase([s("Total 1.", "c4")], CLAIMS).reason == "unbacked_number"


def test_the_default_helper_keeps_its_old_shape():
    """Catches a change to the default, which the profile write guard uses,
    and a loose exact form."""
    from acb_common.assistant_profile import numbers_in
    assert numbers_in("1,200") == {"1", "200"}
    assert numbers_in("+1 41 55") == {"14155"}
    assert numbers_in("1,200", exact=True) == {"1200"}
    assert numbers_in("1,20,000 and 3", exact=True) == {"120000", "3"}
    assert numbers_in("1,2", exact=True) == {"1", "2"}
    assert numbers_in("due 2026-10-30", exact=True) == {"2026", "10", "30"}
    assert numbers_in("-5 and 12.5.", exact=True) == {"-5", "12.5"}


def test_one_item_with_many_sentences_counts_each():
    """Catches a count of list items in place of sentences."""
    assert check_phrase([s("A. B. C. D.", "c1")], CLAIMS).reason == "too_many_sentences"
    assert check_phrase([s("A! B? C.", "c1")], CLAIMS).ok
    assert check_phrase([s("A. B.", "c1"), s("C. D.", "c1")], CLAIMS).reason == "too_many_sentences"
    assert check_phrase([s("Total 1,200.", "c4")], CLAIMS).ok


def test_the_decimal_point_and_the_sign_stay():
    """Catches digits-only numbers in the fence: then 12.5 passes for 125,
    and -5 passes for 5."""
    claims = {"a": "125 tasks", "b": "5 tasks", "c": "12 tasks"}
    assert check_phrase([s("12.5 tasks.", "a")], claims).reason == "unbacked_number"
    assert check_phrase([s("-5 tasks.", "b")], claims).reason == "unbacked_number"
    assert check_phrase([s("\u22125 tasks.", "b")], claims).reason == "unbacked_number"
    assert check_phrase([s("1 2 tasks.", "c")], claims).reason == "unbacked_number"
    assert check_phrase([s("1,2 tasks.", "c")], claims).reason == "unbacked_number"
    assert check_phrase([s("50% done.", "b")], {"b": "5 0"}).reason == "unbacked_number"


def test_a_numeral_that_is_not_an_ascii_digit_fails():
    """Catches a fence that reads only ASCII digits: then "③" passes."""
    assert check_phrase([s("Three tasks and ③.", "c1")], CLAIMS).reason == "unbacked_number"
    assert check_phrase([s("Tasks: ٣.", "c1")], {"c1": "٣"}).reason == "unbacked_number"


def test_the_brief_imports_the_one_public_helper():
    """Catches a second number regex in the brief package."""
    root = Path(fence.__file__).parent
    assert fence.numbers_in.__module__ == "acb_common.assistant_profile"
    for py in root.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert not re.search(r"re\.compile\([^)]*\\d", src), py.name
    src = Path(fence.__file__).read_text(encoding="utf-8")
    assert "from acb_common.assistant_profile import numbers_in" in src
