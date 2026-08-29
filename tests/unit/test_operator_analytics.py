"""Operator analytics — WS-31, `specs/ai_metering_and_analytics.md` §6.

⚠️ **The subject is when we REFUSE to answer.** The expensive failure in an
analytics surface is never a wrong number, it is a confident one: an operator
who reads "excellent margin" from a division by zero acts on it, and nothing in
the page suggests they should not.

So every function under test has a "we cannot say" branch, and most of the
cases below are that branch rather than the arithmetic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from customer_console.analytics import (
    annotate_orgs,
    is_silent,
    margin_ratio,
    runway_days,
    spike_days,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
D = Decimal


def _ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat()


class TestMarginRatio:
    def test_it_reports_credits_per_dollar(self):
        assert margin_ratio(D(200), D(100)) == D("2.00")

    def test_zero_cost_is_UNANSWERABLE_not_excellent(self):
        # 🔴 Zero provider cost means we have not measured what this traffic
        # cost us. Dividing by it yields infinity, and rendering infinity as a
        # great margin is the confident-wrong-number this module exists to stop.
        assert margin_ratio(D(500), D(0)) is None

    def test_a_negative_or_missing_cost_is_also_unanswerable(self):
        assert margin_ratio(D(500), D(-1)) is None
        assert margin_ratio(D(500), None) is None  # type: ignore[arg-type]

    def test_zero_credits_against_real_cost_is_ZERO_not_none(self):
        # ⚠️ This one IS answerable, and it is the worst case: we paid a
        # provider and billed the customer nothing. It must not be hidden
        # behind the same None as "no data".
        assert margin_ratio(D(0), D(10)) == D("0.00")


class TestRunway:
    def test_it_divides_the_balance_by_the_daily_burn(self):
        # 700 credits burned over 7 days = 100/day. 1000 balance = 10 days.
        assert runway_days(D(1000), D(700)) == 10

    def test_no_burn_means_UNKNOWN_not_forever(self):
        # ⚠️ Printing ∞ would hide the more interesting fact, which A3 reports
        # separately: this customer is silent.
        assert runway_days(D(1000), D(0)) is None

    def test_it_floors_at_zero_rather_than_going_negative(self):
        # A negative runway is an overdraft, and the overdraft policy already
        # owns that vocabulary. Two vocabularies for one state is the defect.
        assert runway_days(D(-50), D(700)) == 0

    def test_a_zero_window_cannot_produce_a_rate(self):
        assert runway_days(D(1000), D(700), window_days=0) is None


class TestSilent:
    def test_credits_held_and_never_used_is_the_sharpest_case(self):
        # 🔴 Somebody paid and never arrived.
        assert is_silent(D(1000), None, NOW) is True

    def test_credits_held_and_idle_past_the_window(self):
        assert is_silent(D(1000), _ago(20), NOW) is True

    def test_recent_use_is_not_silent(self):
        assert is_silent(D(1000), _ago(2), NOW) is False

    def test_no_credits_is_NOT_silent(self):
        # ⚠️ Both halves are required. A customer with no credits and no usage
        # is not churning, they have not started — and flagging them buries the
        # real signal under every trial that never began.
        assert is_silent(D(0), None, NOW) is False

    def test_an_unparseable_timestamp_reads_as_never_seen(self):
        # Fail toward showing the operator something, not toward silence.
        assert is_silent(D(1000), "not-a-date", NOW) is True

    def test_a_naive_timestamp_does_not_explode(self):
        # `usage_event.created_at` comes back tz-aware, but a caller passing a
        # naive datetime must not raise inside an analytics page.
        naive = (NOW - timedelta(days=30)).replace(tzinfo=None)
        assert is_silent(D(1000), naive, NOW) is True


class TestSpikes:
    def test_it_compares_each_day_to_the_days_BEFORE_it(self):
        # ⚠️ A whole-series mean is dragged up by the very day we want to
        # catch, which then fails to exceed its own inflated baseline.
        series = [
            {"day": "2026-08-01", "credits": D(10)},
            {"day": "2026-08-02", "credits": D(10)},
            {"day": "2026-08-03", "credits": D(500)},
        ]
        assert spike_days(series) == ["2026-08-03"]

    def test_a_steady_series_has_no_spike(self):
        series = [{"day": f"d{i}", "credits": D(10)} for i in range(7)]
        assert spike_days(series) == []

    def test_starting_to_use_the_product_is_not_a_spike(self):
        # A run of zeros is not a baseline — any non-zero day beats any
        # multiple of zero, and "the customer began" is not an anomaly.
        series = [
            {"day": "d1", "credits": D(0)},
            {"day": "d2", "credits": D(0)},
            {"day": "d3", "credits": D(50)},
        ]
        assert spike_days(series) == []

    def test_the_first_day_can_never_be_a_spike(self):
        assert spike_days([{"day": "d1", "credits": D(9999)}]) == []

    def test_it_tolerates_a_missing_credits_key(self):
        assert spike_days([{"day": "d1"}, {"day": "d2"}]) == []


class TestAnnotate:
    def test_it_attaches_all_three_from_one_window(self):
        # Kept as one pass so a surface cannot show a margin from one window
        # and a runway from another — that is how a page disagrees with itself.
        rows = [{"slug": "acme", "credits": D(200), "cost_usd": D(100),
                 "last_seen": _ago(1)}]
        out = annotate_orgs(rows, {"acme": D(1000)}, {"acme": D(700)}, NOW)
        assert out[0]["margin_ratio"] == D("2.00")
        assert out[0]["runway_days"] == 10
        assert out[0]["silent"] is False
        assert out[0]["balance"] == D(1000)

    def test_an_org_absent_from_the_balance_map_reads_as_zero(self):
        # ⚠️ A missing balance must not raise inside a page that renders every
        # organization. It reads as zero, which is also the truthful default.
        rows = [{"slug": "ghost", "credits": D(0), "cost_usd": D(0),
                 "last_seen": None}]
        out = annotate_orgs(rows, {}, {}, NOW)
        assert out[0]["balance"] == D(0)
        assert out[0]["runway_days"] is None
        assert out[0]["margin_ratio"] is None
        assert out[0]["silent"] is False

    def test_it_preserves_the_columns_the_read_returned(self):
        rows = [{"slug": "acme", "name": "Acme", "calls": 5,
                 "credits": D(1), "cost_usd": D(1), "last_seen": None}]
        out = annotate_orgs(rows, {"acme": D(1)}, {}, NOW)
        assert out[0]["name"] == "Acme"
        assert out[0]["calls"] == 5


@pytest.mark.parametrize("balance,burn,expected", [
    (D(0), D(70), 0),
    (D(10), D(70), 1),
    (D(69), D(70), 6),
])
def test_runway_rounds_down_because_a_partial_day_is_not_a_day(
    balance, burn, expected
):
    assert runway_days(balance, burn) == expected
