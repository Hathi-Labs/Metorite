"""Seat accounting: purchased, assigned, available, and the hard cap.

Spec: ``project-docs/specs/customer_console.md`` §3.3 · D19.3 (the cap,
carried verbatim) · D32.5 (the counts, defined once).

The cases that matter are the ones where money or access leaks:

  * re-assigning an existing member must not burn a second seat (the sign-in
    path calls assignment on EVERY sign-in);
  * the cap must refuse rather than auto-upgrade;
  * a reduced seat count must not report negative capacity.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from customer_console.seats import (
    CORE_PLAN_SLUG,
    decide_assignment,
    seat_counts,
)


def counts(purchased: list[int], assigned: int, plan: str = CORE_PLAN_SLUG):
    return seat_counts(plan, purchased, assigned)


class TestCounts:
    def test_the_three_numbers(self):
        c = counts([10], 4)
        assert (c.purchased, c.assigned, c.available) == (10, 4, 6)

    def test_grants_accumulate(self):
        # Buying five more seats is a second grant row, not an edit of the first.
        assert counts([10, 5], 0).purchased == 15

    def test_a_reduction_is_a_negative_grant(self):
        # Never an UPDATE: the history of what a customer bought is exactly what
        # you need on the day they dispute the invoice.
        assert counts([10, -3], 0).purchased == 7

    def test_available_never_goes_negative(self):
        # Reachable only by a manual edit or a seat count reduced under live
        # assignments. "0 free" is the honest answer; a negative number could be
        # added to by a caller and treated as capacity.
        c = counts([2], 5)
        assert c.available == 0
        assert c.oversubscribed is True

    def test_oversubscribed_is_false_when_exactly_full(self):
        assert counts([5], 5).oversubscribed is False


class TestTheCap:
    def test_allows_while_seats_remain(self):
        assert decide_assignment(counts([3], 1)).allowed is True

    def test_refuses_at_the_cap_with_409_and_never_auto_upgrades(self):
        d = decide_assignment(counts([3], 3), price_inr=Decimal("600.00"))

        assert d.allowed is False
        # 409, not 402: the subscription is coherent, the request is outside its
        # SIZE. 402 is "not paid for at all" and conflating them leaves an admin
        # unable to buy their way out of the error.
        assert d.status == 409
        assert d.reason == "seat_cap_exceeded"
        # The payload tells the UI what to sell, and the decision itself grants
        # nothing — auto-upgrade is how a customer discovers a charge they did
        # not authorise.
        assert d.buy_more == {
            "plan_slug": CORE_PLAN_SLUG,
            "purchased": 3,
            "assigned": 3,
            "additional_seats_required": 1,
            "price_inr": "600.00",
        }

    def test_reassigning_an_existing_member_is_a_no_op_that_succeeds(self):
        # THE drift bug. Sign-in resolves membership on every sign-in; if that
        # consumed a seat each time, a single member would exhaust an
        # organization's seats by logging in.
        d = decide_assignment(counts([3], 3), already_assigned=True)

        assert d.allowed is True
        assert d.reason == "already_assigned"

    def test_an_org_that_bought_nothing_cannot_assign(self):
        d = decide_assignment(counts([], 0))
        assert (d.allowed, d.status) == (False, 409)

    def test_price_is_optional_in_the_upsell(self):
        d = decide_assignment(counts([1], 1))
        assert d.buy_more is not None
        assert d.buy_more["price_inr"] is None


@pytest.mark.parametrize(
    ("purchased", "assigned", "expected"),
    [(0, 0, False), (1, 0, True), (1, 1, False), (100, 99, True)],
)
def test_boundary_of_the_cap(purchased: int, assigned: int, expected: bool):
    assert decide_assignment(counts([purchased], assigned)).allowed is expected
