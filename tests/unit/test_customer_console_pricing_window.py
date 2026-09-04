"""Which vendor rate applied — the window and the context tier.

Spec: ``project-docs/specs/credit_pricing.md`` §4.1 (slice 2). Migration 023.

🔴 **One model can carry four prices for the same token, and until slice 2 we
recorded one.** DeepSeek charges an off-peak rate for part of the day.
OpenAI charges roughly double for input past a context threshold. A call that
ran cheap and a call that ran dear were recorded as costing the same, so every
margin read was an average of two numbers nobody could separate.

Three failure modes drive these tests:

  1. **A wrapping off-peak window read as peak.** DeepSeek's runs 16:30 to
     00:30 UTC. A naive ``start <= t < end`` answers `peak` for the whole
     night, so we would record our DEAREST rate for our CHEAPEST eight hours
     and read our own margin as worse than it is.
  2. **A long document billed at the short rate.** The vendor charges double
     and we record half. That is a direct, silent margin loss on exactly the
     calls that cost most.
  3. **Pricing a tier off the cheap window.** The price then breaks the moment
     traffic moves to the dear one, which is what the owner directive of
     2026-09-04 exists to prevent.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, time
from decimal import Decimal

import pytest
from customer_console.pricing_window import (
    CONTEXT_LONG,
    CONTEXT_SHORT,
    WINDOW_OFFPEAK,
    WINDOW_PEAK,
    context_tier_of,
    pricing_basis,
    resolve_rates,
    window_of,
)

#: DeepSeek's window, and it WRAPS midnight. That is the whole point of it
#: being the example here.
DS_START = time(16, 30)
DS_END = time(0, 30)

#: A DeepSeek-shaped profile: peak in the named columns (which is what the
#: vendor feed fills), off-peak beside them.
DS_PROFILE: dict = {
    "vendor_input_per_1m_usd": Decimal("0.44"),
    "vendor_output_per_1m_usd": Decimal("1.32"),
    "vendor_cached_input_per_1m_usd": Decimal("0.014"),
    "vendor_input_offpeak_per_1m_usd": Decimal("0.22"),
    "vendor_output_offpeak_per_1m_usd": Decimal("0.66"),
    "vendor_cached_input_offpeak_per_1m_usd": Decimal("0.007"),
    "vendor_input_long_per_1m_usd": None,
    "vendor_output_long_per_1m_usd": None,
    "vendor_cached_input_long_per_1m_usd": None,
    "offpeak_start_utc": DS_START,
    "offpeak_end_utc": DS_END,
    "context_tier_threshold": None,
}


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 5, hour, minute, tzinfo=UTC)


class TestTheWindow:
    def test_a_wrapping_window_covers_the_night(self):
        """🔴 Failure mode 1. Every one of these is inside 16:30 to 00:30."""
        for moment in (_at(16, 30), _at(20), _at(23, 59), _at(0, 0), _at(0, 29)):
            assert (
                window_of(moment, offpeak_start=DS_START, offpeak_end=DS_END)
                == WINDOW_OFFPEAK
            ), moment

    def test_a_wrapping_window_excludes_the_day(self):
        for moment in (_at(0, 30), _at(9), _at(12), _at(16, 29)):
            assert (
                window_of(moment, offpeak_start=DS_START, offpeak_end=DS_END)
                == WINDOW_PEAK
            ), moment

    def test_a_plain_window_does_not_wrap(self):
        """A non-wrapping range must still read normally."""
        start, end = time(2), time(6)
        assert window_of(_at(3), offpeak_start=start, offpeak_end=end) == WINDOW_OFFPEAK
        assert window_of(_at(7), offpeak_start=start, offpeak_end=end) == WINDOW_PEAK

    def test_no_window_configured_is_always_peak(self):
        """⚠️ Not a default standing in for missing data.

        One rate all day IS the peak rate. Every model but DeepSeek's two
        works that way, and the named columns already hold that number.
        """
        assert (
            window_of(_at(3), offpeak_start=None, offpeak_end=None) == WINDOW_PEAK
        )

    def test_the_boundary_belongs_to_offpeak_at_the_start(self):
        """Exactly at the open is inside. Exactly at the close is outside."""
        assert (
            window_of(_at(16, 30), offpeak_start=DS_START, offpeak_end=DS_END)
            == WINDOW_OFFPEAK
        )
        assert (
            window_of(_at(0, 30), offpeak_start=DS_START, offpeak_end=DS_END)
            == WINDOW_PEAK
        )


class TestTheContextTier:
    def test_above_the_threshold_is_long(self):
        """🔴 Failure mode 2."""
        assert context_tier_of(300_000, threshold=272_000) == CONTEXT_LONG

    def test_at_or_below_the_threshold_is_short(self):
        assert context_tier_of(272_000, threshold=272_000) == CONTEXT_SHORT
        assert context_tier_of(8_000, threshold=272_000) == CONTEXT_SHORT

    def test_no_threshold_is_always_short(self):
        assert context_tier_of(10_000_000, threshold=None) == CONTEXT_SHORT


class TestResolvingTheRates:
    def test_an_offpeak_call_costs_the_offpeak_rate(self):
        """What this call COST us follows the window it actually ran in.

        Recording peak here would overstate cost and understate margin, which
        defeats the margin monitor §4.3 exists to build.
        """
        r = resolve_rates(DS_PROFILE, prompt_tokens=8_000, started_at=_at(20))
        assert r.window == WINDOW_OFFPEAK
        assert r.input_per_1m == Decimal("0.22")
        assert r.output_per_1m == Decimal("0.66")

    def test_a_peak_call_costs_the_peak_rate(self):
        r = resolve_rates(DS_PROFILE, prompt_tokens=8_000, started_at=_at(12))
        assert r.window == WINDOW_PEAK
        assert r.input_per_1m == Decimal("0.44")

    def test_a_missing_offpeak_column_falls_back_to_peak_not_to_null(self):
        """⚠️ NULL would lose the cost of a model with one published rate.

        The feed fills the peak columns. A model nobody has given an off-peak
        price to must still cost correctly at the rate it does have.
        """
        profile = dict(DS_PROFILE, vendor_input_offpeak_per_1m_usd=None)
        r = resolve_rates(profile, prompt_tokens=8_000, started_at=_at(20))
        assert r.window == WINDOW_OFFPEAK
        assert r.input_per_1m == Decimal("0.44")

    def test_long_context_beats_offpeak(self):
        """The surcharge is the larger effect, and no vendor prices the pair."""
        profile = dict(
            DS_PROFILE,
            context_tier_threshold=272_000,
            vendor_input_long_per_1m_usd=Decimal("0.88"),
        )
        r = resolve_rates(profile, prompt_tokens=300_000, started_at=_at(20))
        assert r.context == CONTEXT_LONG
        assert r.window == WINDOW_OFFPEAK
        assert r.input_per_1m == Decimal("0.88")

    def test_a_long_call_with_no_long_column_uses_the_window_rate(self):
        """A known rate in the right ballpark beats NULL, and the row says which."""
        profile = dict(DS_PROFILE, context_tier_threshold=272_000)
        r = resolve_rates(profile, prompt_tokens=300_000, started_at=_at(20))
        assert r.context == CONTEXT_LONG
        assert r.input_per_1m == Decimal("0.22")


class TestThePricingBasis:
    """🔴 Failure mode 3 — the owner directive of 2026-09-04."""

    def test_a_tier_price_derives_from_the_PEAK_rate(self):
        """Even at an hour when we are actually paying off-peak.

        Fast at 30 credits earns 66.3 percent on the off-peak cost and 32.7
        percent on the peak one, against its own floor of 45. Pricing from the
        cheap window sets a price that breaks when traffic moves.
        """
        basis = pricing_basis(DS_PROFILE)
        assert basis.window == WINDOW_PEAK
        assert basis.input_per_1m == Decimal("0.44")
        assert basis.output_per_1m == Decimal("1.32")

    def test_the_basis_takes_no_clock_at_all(self):
        """⚠️ A suggestion that moved with the hour would give an operator a
        different answer at breakfast than at midnight, for one product."""
        import inspect

        params = inspect.signature(pricing_basis).parameters
        assert list(params) == ["profile"], (
            "pricing_basis must not accept a timestamp or a token count — "
            "taking either would let the suggestion drift with the clock"
        )


# ── R8: the same judgements, against a real database ───────────────────────

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "")
pytestmark_db = pytest.mark.skipif(
    not _URL, reason="R8 requires a REAL Postgres; set CUSTOMER_CONSOLE_DATABASE_URL"
)


@pytestmark_db
class TestTheSchemaAgrees:
    """The CHECK vocabularies and the writer must name the same words."""

    def test_the_column_vocabularies_match_the_module(self):
        from sqlalchemy import create_engine, text

        eng = create_engine(_URL, future=True)
        with eng.begin() as conn:
            for constraint, allowed in (
                ("usage_event_window_known", {WINDOW_PEAK, WINDOW_OFFPEAK}),
                ("usage_event_context_tier_known", {CONTEXT_SHORT, CONTEXT_LONG}),
            ):
                src = conn.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = :n"
                    ),
                    {"n": constraint},
                ).scalar_one()
                for word in allowed:
                    assert f"'{word}'" in src, (
                        f"{constraint} does not allow '{word}', but "
                        "pricing_window.py writes it"
                    )

    def test_a_half_configured_offpeak_window_is_refused(self):
        """⚠️ One bound set is worse than none.

        The reader cannot tell whether the operator meant all day or nothing,
        so the database refuses the shape rather than letting it mean either.
        """
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import IntegrityError

        eng = create_engine(_URL, future=True)
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO model_profile (model) VALUES ('test/window-guard') "
                    "ON CONFLICT (model) DO NOTHING"
                )
            )
        with pytest.raises(IntegrityError), eng.begin() as conn:
            conn.execute(
                text(
                    "UPDATE model_profile SET offpeak_start_utc = '16:30' "
                    "WHERE model = 'test/window-guard'"
                )
            )
        with eng.begin() as conn:
            conn.execute(
                text("DELETE FROM model_profile WHERE model = 'test/window-guard'")
            )
