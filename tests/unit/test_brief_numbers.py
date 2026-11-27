"""WS-BRIEF BR-4c — the ``numbers`` section, pure builder (R7).

Spec: ``project-docs/specs/core_brief.md`` §4.3. Code:
``gateway/brief/gather.py`` (``number_claims``). The real database is
``test_brief_numbers_r8.py``.

Each test names the mutation that it catches.
"""

from __future__ import annotations

from gateway.brief import fence, gather, store


def card(sid, kind="needs_reply", **kw):
    base = {"signal_id": sid, "app": "email", "kind": kind, "group": "needs_you", "score": 50,
            "state": "new",
            "href": f"/{sid.split(':')[0]}?x=1", "title": "T", "facts": {}}
    return {**base, **kw}


def test_three_waiting_threads_make_one_count_claim():
    """Catches a count with the wrong value, kind, section or ids."""
    cards = [card(f"email:acc:t{i}") for i in range(3)]
    got = gather.number_claims(cards, 4)
    assert len(got) == 1
    c = got[0]
    assert (c.section, c.source_kind, c.value, c.value_path, c.position) == (
        "numbers", "count", 3, "count", 4)
    assert sorted(c.source_ids) == [f"email:acc:t{i}" for i in range(3)]
    assert c.source_id in c.source_ids and c.source_app == "email"
    assert c.href.startswith("/email") and c.text == "3 threads wait for your reply"


def test_one_thread_reads_in_the_singular():
    got = gather.number_claims([card("email:acc:t1")], 0)
    assert got[0].text == "1 thread waits for your reply" and got[0].value == 1


def test_promises_count_apart_from_replies_and_at_most_three_claims():
    """Catches a count that mixes two kinds, and a cut above MAX_NUMBERS."""
    cards = [card("email:acc:t1"), card("tasks:9", "promise_due"),
             card("tasks:10", "promise_due")]
    got = gather.number_claims(cards, 0)
    assert [(c.source_kind, c.value, c.source_app) for c in got] == [
        ("count", 1, "email"), ("count", 2, "tasks")]
    assert [c.position for c in got] == [0, 1]
    assert len(got) <= gather.MAX_NUMBERS


def test_a_count_with_no_usable_card_is_skipped():
    """Catches a count that has no source id or no href."""
    cards = [card("email:acc:t1", href="//evil.example"), card("nobody:1"),
             card("email:acc:t2", href=""), card("email:acc:t3", state="dismissed"),
             card("email:acc:t4", group="later")]
    assert gather.number_claims(cards, 0) == []
    assert gather.number_claims([], 0) == []


def test_a_card_that_cannot_open_is_not_counted():
    """Catches a value that is higher than the ids it lists."""
    cards = [card("email:acc:t1"), card("email:acc:t2", href="/api/x")]
    got = gather.number_claims(cards, 0)
    assert got[0].value == 1 and got[0].source_ids == ("email:acc:t1",)


def test_the_ids_are_cut_at_fifty_and_the_value_is_not():
    cards = [card(f"email:acc:t{i}") for i in range(60)]
    c = gather.number_claims(cards, 0)[0]
    assert c.value == 60 and len(c.source_ids) == store.MAX_SOURCE_IDS


def test_the_fence_accepts_the_lead_number_of_a_count_and_refuses_another():
    """Catches a count claim that the fence cannot back."""
    c = gather.number_claims([card(f"email:acc:t{i}") for i in range(3)], 0)[0]
    held = {"c1": [c.text, str(c.value)]}
    ok = fence.check_phrase([{"text": "3 threads wait.", "claims": ["c1"]}], held)
    bad = fence.check_phrase([{"text": "4 threads wait.", "claims": ["c1"]}], held)
    assert ok.ok and bad.reason == "unbacked_number"
