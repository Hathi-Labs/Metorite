"""`vendor_cost_usd` — what a call cost US, and when we refuse to say.

No database. The function is pure on purpose: the READ of the prices is two
lines in `_vendor_prices`, and the arithmetic is where the money mistakes
live. Spec: `ai_metering_and_analytics.md` §6 A1 · migration 013.

🔴 **The subject is the refusals.** A margin is renegotiated on these numbers,
so a confidently wrong cost is worse than no cost. Every branch that would
have to GUESS must answer ``None`` instead.
"""
from __future__ import annotations

from decimal import Decimal

from customer_console.router import ExtractedUsage, vendor_cost_usd

IN = Decimal("3.0000")       # USD per 1M input tokens
OUT = Decimal("15.0000")
CACHED = Decimal("0.3000")


def _usage(prompt: int = 0, completion: int = 0, cached: int = 0):
    return ExtractedUsage(
        prompt_tokens=prompt, completion_tokens=completion,
        cached_tokens=cached,
    )


def test_the_plain_case_is_plain_arithmetic() -> None:
    # 1000 in at $3/1M + 500 out at $15/1M = 0.003 + 0.0075
    cost = vendor_cost_usd(
        _usage(prompt=1000, completion=500),
        input_per_1m=IN, output_per_1m=OUT, cached_per_1m=None,
    )
    assert cost == Decimal("0.01050000")


def test_cached_reads_are_priced_at_the_CACHED_rate() -> None:
    # 1000 prompt of which 600 cached: 400 at $3/1M + 600 at $0.3/1M.
    cost = vendor_cost_usd(
        _usage(prompt=1000, cached=600, completion=0),
        input_per_1m=IN, output_per_1m=OUT, cached_per_1m=CACHED,
    )
    assert cost == Decimal("0.00138000")


def test_a_cache_hitting_call_with_no_cached_price_is_UNKNOWN() -> None:
    """🔴 The refusal this slice exists for.

    Pricing cached reads at the full input rate OVERSTATES the cost and
    understates the margin — a wrong number in the safe direction is still a
    number somebody renegotiates a vendor contract on.
    """
    cost = vendor_cost_usd(
        _usage(prompt=1000, cached=600),
        input_per_1m=IN, output_per_1m=OUT, cached_per_1m=None,
    )
    assert cost is None


def test_a_missing_needed_price_is_UNKNOWN_never_partial() -> None:
    # Output tokens exist and the output price does not. A partial sum would
    # render as a small real cost, which is the worst of the three options.
    assert vendor_cost_usd(
        _usage(prompt=1000, completion=500),
        input_per_1m=IN, output_per_1m=None, cached_per_1m=None,
    ) is None
    assert vendor_cost_usd(
        _usage(prompt=1000, completion=500),
        input_per_1m=None, output_per_1m=OUT, cached_per_1m=None,
    ) is None


def test_a_price_missing_for_tokens_not_consumed_costs_nothing() -> None:
    # No cached tokens, so the missing cached price is not needed. An
    # all-tokens-must-be-priced rule would leave every vendor without a
    # cached rate permanently uncosted.
    cost = vendor_cost_usd(
        _usage(prompt=1000, completion=500, cached=0),
        input_per_1m=IN, output_per_1m=OUT, cached_per_1m=None,
    )
    assert cost is not None


def test_all_zero_counts_are_UNKNOWN_not_free() -> None:
    """Extraction is best-effort. All-zero usually means an unreadable shape,
    and recording $0 for a call we could not read is a measurement nobody
    made."""
    assert vendor_cost_usd(
        _usage(), input_per_1m=IN, output_per_1m=OUT, cached_per_1m=CACHED,
    ) is None


def test_cached_is_clamped_to_prompt_so_cost_never_goes_negative() -> None:
    # A provider that double-reports cached tokens must not produce a negative
    # uncached count. 500 prompt, 900 "cached": all 500 priced as cached.
    cost = vendor_cost_usd(
        _usage(prompt=500, cached=900),
        input_per_1m=IN, output_per_1m=OUT, cached_per_1m=CACHED,
    )
    assert cost == Decimal("0.00015000")
    assert cost >= 0


def test_output_only_needs_only_the_output_price() -> None:
    # Degenerate but legal: a provider that reported completion counts only.
    cost = vendor_cost_usd(
        _usage(completion=1000),
        input_per_1m=None, output_per_1m=OUT, cached_per_1m=None,
    )
    assert cost == Decimal("0.01500000")


def test_quantised_to_the_column_not_to_float() -> None:
    # NUMERIC(14,8). One input token at $3/1M is 0.000003 exactly — a float
    # path would carry 2.9999999e-06 into the ledger.
    cost = vendor_cost_usd(
        _usage(prompt=1, completion=0),
        input_per_1m=IN, output_per_1m=OUT, cached_per_1m=None,
    )
    assert cost == Decimal("0.00000300")
    assert isinstance(cost, Decimal)
