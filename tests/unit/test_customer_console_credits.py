"""Credit rating, balance and the spend gate.

Spec: ``project-docs/specs/customer_console.md`` §3.4/§4.4 · D19.2 (the
credit unit) · D32.6 (rollover) · D32.8 (per-member caps).

Three failure modes drive these tests, all of them expensive in different ways:

  1. **Double-billing cached tokens** — charges the customer most for the part
     of the prompt that cost us least, i.e. the exact opposite of what prompt
     caching was sold to them as.
  2. **Billing an unpriced model as free** — looks like revenue working while
     the margin leaks, and nobody notices until the month closes.
  3. **A hard stop at exactly zero** — lands mid-workflow and costs more in
     support than the overdraft ever will.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from customer_console.credits import (
    OverdraftPolicy,
    RateCard,
    TokenUsage,
    UnpricedModel,
    balance_of,
    decide_member_cap,
    decide_spend,
    rate_call,
)

CARD = RateCard(
    model="deepseek/deepseek-v4-pro",
    input_per_1k=Decimal("2.0"),
    output_per_1k=Decimal("6.0"),
    cached_input_per_1k=Decimal("0.5"),
)


class TestRating:
    def test_a_plain_call(self):
        # 1000 fresh input @2 + 500 output @6 = 2 + 3 = 5 credits
        cost = rate_call(CARD, TokenUsage(prompt_tokens=1000, completion_tokens=500))
        assert cost == Decimal("5.0")

    def test_cached_tokens_are_a_SUBSET_of_prompt_tokens(self):
        # The trap. Providers report cached tokens as part of prompt_tokens, so
        # billing both at full rate charges twice for the same text.
        #
        # 1000 prompt of which 800 cached →
        #   200 fresh @2/1k = 0.4  +  800 cached @0.5/1k = 0.4  +  0 output
        cost = rate_call(
            CARD, TokenUsage(prompt_tokens=1000, cached_tokens=800)
        )
        assert cost == Decimal("0.8")

        # And it must be strictly cheaper than the same call with no cache hit,
        # or the discount we advertise is not real.
        uncached = rate_call(CARD, TokenUsage(prompt_tokens=1000))
        assert cost < uncached

    def test_cached_exceeding_prompt_never_goes_negative(self):
        # Providers have shipped inconsistent counters before; a negative
        # fresh-token count would CREDIT the customer for using more cache.
        cost = rate_call(CARD, TokenUsage(prompt_tokens=100, cached_tokens=500))
        assert cost >= 0

    def test_an_unpriced_model_raises_rather_than_billing_zero(self):
        # 002_seed_catalog.sql seeds every model at zero on purpose, so this
        # fires until CP-6 sets real prices against measured burn.
        unpriced = RateCard("new/model", Decimal(0), Decimal(0), Decimal(0))
        assert unpriced.is_priced is False
        with pytest.raises(UnpricedModel):
            rate_call(unpriced, TokenUsage(prompt_tokens=1_000_000))

    def test_a_card_with_any_nonzero_rate_is_priced(self):
        assert RateCard("m", Decimal(0), Decimal("0.1")).is_priced is True

    def test_money_is_decimal_not_float(self):
        # Small rates x large token counts, summed thousands of times a month,
        # is exactly where binary floating point drifts.
        assert isinstance(rate_call(CARD, TokenUsage(prompt_tokens=1)), Decimal)


class TestBalance:
    def test_balance_is_the_sum_of_the_ledger(self):
        assert balance_of([Decimal("1000"), Decimal("-5.5"), Decimal("-4.5")]) == Decimal("990")

    def test_an_empty_ledger_is_zero_not_an_error(self):
        assert balance_of([]) == Decimal(0)

    def test_credits_roll_over_because_a_balance_is_just_a_sum(self):
        # D32.6: rollover is the DEFAULT — there is no per-period reset to
        # implement, which is exactly why expiry would be the thing needing
        # machinery, not rollover.
        last_month = [Decimal("1000"), Decimal("-300")]
        this_month = [Decimal("-200")]
        assert balance_of(last_month + this_month) == Decimal("500")


class TestTheSpendGate:
    def test_allows_a_call_within_balance(self):
        assert decide_spend(Decimal("100"), Decimal("5")).allowed is True

    def test_soft_blocks_into_grace_rather_than_at_zero(self):
        # Balance 0, cost 10, grace 100 → allowed, and flagged as overdraft so
        # the UI warns before the wall instead of at it.
        d = decide_spend(Decimal("0"), Decimal("10"))
        assert d.allowed is True
        assert d.in_overdraft is True

    def test_refuses_402_past_the_grace_floor(self):
        d = decide_spend(Decimal("-95"), Decimal("10"))
        assert d.allowed is False
        # 402, not 409: this IS the "not paid for" axis.
        assert d.status == 402
        assert d.reason == "insufficient_credits"

    def test_the_floor_is_checked_after_the_call_not_before(self):
        # Otherwise one very large call vaults past the floor in a single step
        # and the grace is whatever that call happened to cost.
        policy = OverdraftPolicy(grace_credits=Decimal("10"))
        assert decide_spend(Decimal("0"), Decimal("11"), policy=policy).allowed is False
        assert decide_spend(Decimal("0"), Decimal("10"), policy=policy).allowed is True

    def test_trials_get_no_grace_by_default(self):
        # An unpaid account is where overdraft becomes unrecoverable cost.
        assert decide_spend(Decimal("0"), Decimal("1"), is_trial=True).allowed is False
        assert decide_spend(Decimal("1"), Decimal("1"), is_trial=True).allowed is True

    def test_trial_grace_is_available_when_deliberately_enabled(self):
        policy = OverdraftPolicy(grace_credits=Decimal("50"), grace_for_trial=True)
        assert decide_spend(
            Decimal("0"), Decimal("10"), policy=policy, is_trial=True
        ).allowed is True


class TestMemberCaps:
    def test_no_cap_row_means_unlimited_within_the_org_pool(self):
        # Absence of a policy is not a policy of zero.
        assert decide_member_cap(Decimal("10000"), None) == ("allow", False)

    def test_degrade_is_the_default_at_exhaustion(self):
        # Dropping to tier-fast keeps the member working. A system exposing raw
        # model names could only stop them (D32.8).
        assert decide_member_cap(Decimal("100"), Decimal("100")) == ("degrade", True)

    def test_block_when_explicitly_configured(self):
        action, warn = decide_member_cap(
            Decimal("100"), Decimal("100"), on_exhaustion="block"
        )
        assert (action, warn) == ("block", True)

    def test_warns_at_80_percent_without_restricting(self):
        assert decide_member_cap(Decimal("80"), Decimal("100")) == ("allow", True)
        assert decide_member_cap(Decimal("79"), Decimal("100")) == ("allow", False)

    def test_a_zero_or_negative_cap_is_treated_as_no_cap(self):
        # A cap of zero almost certainly means "not configured"; reading it as
        # "this member may never use AI" would silently disable people.
        assert decide_member_cap(Decimal("5"), Decimal("0")) == ("allow", False)
