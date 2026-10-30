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
    "₹500" is 500. A comma is no separator, so "1,200" is 1 and 200 and
    "1200" is another number: the fence fails closed on it."""
    assert check_phrase([s("Pay ₹500.", "c2")], CLAIMS).ok
    assert check_phrase([s("Pay 500.", "c2")], CLAIMS).ok
    assert check_phrase([s("Total 1,200.", "c4")], CLAIMS).ok
    assert check_phrase([s("Total 1200.", "c4")], CLAIMS).reason == "unbacked_number"


def test_the_brief_imports_the_one_public_helper():
    """Catches a second number regex in the brief package."""
    root = Path(fence.__file__).parent
    assert fence.numbers_in.__module__ == "acb_common.assistant_profile"
    for py in root.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert not re.search(r"re\.compile\([^)]*\\d", src), py.name
    src = Path(fence.__file__).read_text(encoding="utf-8")
    assert "from acb_common.assistant_profile import numbers_in" in src
