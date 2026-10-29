"""Deterministic bulk detection, and what the Cleaner counts as outstanding.

Two changes, both free of model calls:

* ``_shape_category`` reads the message's own shape — a ``List-Unsubscribe``
  header, or an unattended local part. The first three ``_decide`` steps all
  need the sender to have been labelled BEFORE, so none of them can touch a
  sender seen for the first time; measured live, they reached 265 of the
  uncategorized backlog. Three signals of this kind already existed in
  engine.py, wired only as negative gates: allowed to say "not a Reply", never
  allowed to say "this is a Notification".

* ``_CLEANUP_SCOPE`` stops counting the user's own colleagues as outstanding
  cleanup work. Measured live: 2,799 of 3,434 uncategorized messages were
  internal human mail whose correct cleanup category is *none*. The badge could
  never reach zero and the sweep re-read them on every cycle to conclude
  nothing.

Every assertion below about a specific address was checked against the real
sender list from the live account (130 senders, 635 messages): 10 senders
classified, zero false positives.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from gateway.routes.email.automation import cleanup as c


def _row(sender: str, *, unsub: str | None = None, subject: str = "") -> Any:
    return SimpleNamespace(from_address={"email": sender}, subject=subject,
                           unsubscribe_link=unsub)


# ── List-Unsubscribe ────────────────────────────────────────────────────────


def test_an_unsubscribe_header_means_bulk() -> None:
    """RFC 8058: transactional mail doesn't carry one."""
    assert c._shape_category("anything@example.com", True) == (
        "Newsletter", "carries a List-Unsubscribe header")


def test_the_header_outranks_an_ambiguous_local_part() -> None:
    """Live: info@wamplercapital.com — 51 messages, every one with an
    unsubscribe link, 0% ever opened. "info" could be anyone; the header can't."""
    assert c._shape_category("info@wamplercapital.com", True) is not None


def test_the_header_outranks_the_local_part_map() -> None:
    """A newsletter sent from alerts@ is still a newsletter."""
    cat, _ = c._shape_category("alerts@brand.com", True)
    assert cat == "Newsletter"


# ── unattended local parts ──────────────────────────────────────────────────


def test_separators_do_not_hide_the_prefix() -> None:
    for addr in ("noreply@x.com", "no-reply@x.com", "no_reply@x.com",
                 "do.not.reply@x.com"):
        assert c._shape_category(addr, False)[0] == "Notification", addr


def test_digits_do_not_hide_the_prefix() -> None:
    assert c._shape_category("noreply2@x.com", False)[0] == "Notification"
    assert c._shape_category("alerts_01@x.com", False)[0] == "Notification"


def test_run_together_locals_are_matched_by_substring() -> None:
    """No separator to split on, and unmistakably automated. Both live:
    21 and 11 messages respectively."""
    assert c._shape_category("customernotification@icici.bank.in", False)[0] \
        == "Notification"
    assert c._shape_category("hdfcbanksmartstatement@hdfcbank.net", False)[0] \
        == "Receipt"


def test_order_decides_a_mixed_local_part() -> None:
    """Live: news-noreply@news.pitchbook.com. It is a newsletter that happens to
    be unattended, not an alert — Newsletter is checked before Notification."""
    assert c._shape_category("news-noreply@news.pitchbook.com", False)[0] \
        == "Newsletter"


def test_each_category_has_a_representative_prefix() -> None:
    for addr, expected in (("billing@stripe.com", "Receipt"),
                           ("newsletter@brand.com", "Newsletter"),
                           ("promotions@shop.com", "Marketing"),
                           ("alerts@hdfcbank.net", "Notification")):
        assert c._shape_category(addr, False)[0] == expected, addr


# ── what it must NOT classify ───────────────────────────────────────────────


def test_ambiguous_role_addresses_are_left_alone() -> None:
    """A human very often answers these, and the Cleaner offers archive and
    unsubscribe on top of whatever gets stamped. Live: support@fracktal.in is
    the user's own helpdesk — 231 messages, answered by people."""
    for addr in ("info@x.com", "support@fracktal.in", "contact@x.com",
                 "hello@x.com", "team@x.com", "sales@x.com", "help@x.com"):
        assert c._shape_category(addr, False) is None, addr


def test_a_person_is_never_classified_by_shape() -> None:
    for addr in ("suresh@fracktal.in", "sachin@archmation.com",
                 "chaithra.bp@kalvium.com", "gowri.sajeesh@icicibank.com"):
        assert c._shape_category(addr, False) is None, addr


def test_substrings_do_not_swallow_names() -> None:
    """The failure mode substring matching invites. Only long, specific tokens
    are matched that way — "news" inside Newsom, "order" inside recorder and
    "promo" inside promontory must not classify anybody."""
    for addr in ("gavin.newsom@x.com", "recorder@x.com", "promontory@x.com",
                 "bordereau@x.com", "updateson@x.com"):
        assert c._shape_category(addr, False) is None, addr


def test_every_substring_token_is_long_and_specific() -> None:
    """The guard on the guard: an entry short enough to appear inside an
    ordinary word turns this into a name classifier."""
    for needle, _cat in c._SHAPE_SUBSTRINGS:
        assert len(needle) >= 8, needle


def test_no_token_is_both_mapped_and_ambiguous() -> None:
    """A token in both sets would classify or not depending on evaluation
    order — the kind of contradiction that survives for years."""
    for _cat, prefixes in c._SHAPE_CATEGORY:
        assert not (prefixes & c._AMBIGUOUS_LOCALS), prefixes & c._AMBIGUOUS_LOCALS


# ── ordering inside _decide ─────────────────────────────────────────────────


def test_shape_is_the_last_resort() -> None:
    """Purely additive: it must not override anything the user or the rules
    decided, so no existing verdict can change because this now exists."""
    row = _row("noreply@stripe.com")
    # Sender history says Receipt; shape would say Notification. History wins.
    # Tally keys are canonical-cased — _label_tallies runs every label through
    # canonical_cleanup_category before counting it.
    verdict = c._decide(
        row, patterns={}, rule_labels={},
        by_sender={"noreply@stripe.com": {"Receipt": 5}}, by_domain={})
    assert verdict == ("Receipt", "sender history")


def test_shape_fills_in_where_there_is_no_history() -> None:
    verdict = c._decide(_row("alerts@hdfcbank.net"), patterns={}, rule_labels={},
                        by_sender={}, by_domain={})
    assert verdict is not None and verdict[0] == "Notification"


def test_a_message_with_no_signal_at_all_still_returns_none() -> None:
    """The module's contract: no evidence means say so, never guess."""
    assert c._decide(_row("sachin@archmation.com"), patterns={}, rule_labels={},
                     by_sender={}, by_domain={}) is None


def test_a_learned_exclude_beats_the_shape_guess() -> None:
    """Caught as a regression when this step was added: the local part is
    ``deals@``, which the shape map reads as Marketing, but the user had already
    taught via Fix that this exact sender is NOT Marketing. Step 1 honours an
    exclude by skipping the rule; the shape step has no rule to skip, so it has
    to ask separately — otherwise the exclude looks ignored, which is the
    difference between the cleaner learning and the cleaner nagging.

    A guess must always yield to an instruction.
    """
    verdict = c._decide(
        _row("deals@shop.com"),
        patterns={"r1": {"include": [("FROM", "shop.com")],
                         "exclude": [("FROM", "deals@shop.com")]}},
        rule_labels={"r1": "Marketing"},
        by_sender={}, by_domain={})
    assert verdict is None


def test_an_exclude_for_a_different_category_does_not_suppress() -> None:
    """"Not a Receipt" says nothing about whether it's a Notification. Blanket
    suppression would make one Fix silently mute the whole shape step."""
    verdict = c._decide(
        _row("alerts@bank.com"),
        patterns={"r1": {"include": [], "exclude": [("FROM", "alerts@bank.com")]}},
        rule_labels={"r1": "Receipt"},
        by_sender={}, by_domain={})
    assert verdict is not None and verdict[0] == "Notification"


# ── scope ───────────────────────────────────────────────────────────────────


def test_internal_mail_is_out_of_scope() -> None:
    assert "<> ALL(:internal)" in c._CLEANUP_SCOPE


def test_internal_bulk_is_the_documented_exception() -> None:
    """An all-staff campaign blasted through an ESP is internal mail worth
    cleaning; a colleague's email is not."""
    assert "OR em.unsubscribe_link IS NOT NULL" in c._CLEANUP_SCOPE


def test_sent_and_disposed_mail_stay_excluded() -> None:
    assert c.NOT_OUTBOUND_SQL in c._CLEANUP_SCOPE  # sent, drafts, outbox (#467)
    assert "trash" in c._CLEANUP_SCOPE and "junk" in c._CLEANUP_SCOPE


def test_the_scope_params_helper_binds_everything_the_clause_needs() -> None:
    """Binding the labels and forgetting the domains would silently widen the
    scope back to every colleague — and still run."""
    params = c._cleanup_scope_params("acc-1", frozenset({"fracktal.in"}))
    assert set(params) == {"aid", "labels", "internal"}
    assert params["internal"] == ["fracktal.in"]


def test_the_badge_and_the_sweep_read_the_same_definition() -> None:
    """A badge counting mail the sweep will never touch is a to-do list that
    cannot reach zero, which teaches the user to ignore it."""
    import inspect
    for fn in (c._uncategorized_inbox, c.uncategorized_overview):
        assert "_CLEANUP_SCOPE" in inspect.getsource(fn), fn.__name__
