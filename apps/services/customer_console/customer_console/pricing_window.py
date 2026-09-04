"""Which vendor rate applied to one call — the window and the context tier.

Spec: ``project-docs/specs/credit_pricing.md`` §4.1 (slice 2).

🔴 **One model can carry four prices for the same token.** DeepSeek charges an
off-peak rate for part of the day and a peak rate otherwise. OpenAI charges
roughly double for input past a context threshold. ``model_profile`` held one
number per token kind until migration 023, so a call that ran cheap and a call
that ran dear were recorded as costing the same.

📌 **Two questions, and confusing them is the trap this module exists to stop.**

* *What did this call COST us?* The rate for the window it actually ran in.
  Recording peak for a call we paid off-peak overstates cost and understates
  margin, which defeats the margin monitor §4.3 exists to build.
* *What should we CHARGE for a tier?* The PEAK rate, always — owner directive,
  2026-09-04. A price that assumes the cheap window breaks the moment traffic
  moves to the dear one. :func:`pricing_basis` is the second question's answer
  and it never consults a clock.

⚠️ **The customer's charge is untouched by everything here.** D67 keys the
charge on the TIER, so a window change moves our cost and never their price.

⚠️ **No database and no clock of its own.** Every input arrives as an argument,
so the whole module is testable without either — and the caller cannot
accidentally resolve a window from "now" when it meant "when the call started".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal

__all__ = [
    "CONTEXT_LONG",
    "CONTEXT_SHORT",
    "WINDOW_OFFPEAK",
    "WINDOW_PEAK",
    "VendorRates",
    "context_tier_of",
    "pricing_basis",
    "resolve_rates",
    "window_of",
]

#: The two windows. `usage_event_window_known` (023) holds the same two words.
WINDOW_PEAK = "peak"
WINDOW_OFFPEAK = "offpeak"

#: The two context tiers. `usage_event_context_tier_known` (023) agrees.
CONTEXT_SHORT = "short"
CONTEXT_LONG = "long"


def window_of(
    started_at: datetime,
    *,
    offpeak_start: time | None,
    offpeak_end: time | None,
) -> str:
    """Which window ``started_at`` fell in.

    ⚠️ **Resolved from the call's START time, never from "now"** and never from
    when it finished. A long stream can begin off-peak and end at peak, and
    both halves must bill one way — the vendor charges the window the request
    entered on. `credit_pricing.md` §4.1 clause 6 pins this.

    ⚠️ **The range may wrap midnight, and DeepSeek's does** (16:30 to 00:30
    UTC). A naive ``start <= t < end`` answers `peak` for the whole night in
    that case, which is the expensive direction to be wrong in — we would
    record a dear rate for our cheapest eight hours and read our own margin as
    worse than it is.

    A model with no configured window is always ``peak``. That is not a
    default standing in for missing data: one rate all day IS the peak rate,
    and every model but DeepSeek's two works that way.
    """
    if offpeak_start is None or offpeak_end is None:
        return WINDOW_PEAK

    # Compare in UTC. A naive datetime is READ as UTC rather than refused,
    # because the metering path has always passed naive UTC timestamps and a
    # refusal here would fail a completion over a bookkeeping detail.
    moment = (
        started_at.astimezone(UTC) if started_at.tzinfo else started_at
    ).time()

    if offpeak_start <= offpeak_end:
        inside = offpeak_start <= moment < offpeak_end
    else:
        # Wraps midnight: off-peak is everything at or after the start OR
        # before the end.
        inside = moment >= offpeak_start or moment < offpeak_end
    return WINDOW_OFFPEAK if inside else WINDOW_PEAK


def context_tier_of(prompt_tokens: int, *, threshold: int | None) -> str:
    """Whether this call priced at the short or the long context rate.

    ⚠️ **``prompt_tokens`` must be what the PROVIDER REPORTED, never a
    pre-flight estimate.** Tokenizers differ between vendors, so an estimate
    computed with one vendor's tokenizer can miss a threshold the other vendor
    crossed. `credit_pricing.md` §4.1 clause 4 pins this, and the consequence
    of getting it wrong is a large document that under-bills by half.

    A model with no threshold is always ``short``. One rate at every size is
    what most models do.
    """
    if threshold is None or threshold <= 0:
        return CONTEXT_SHORT
    return CONTEXT_LONG if prompt_tokens > threshold else CONTEXT_SHORT


@dataclass(frozen=True)
class VendorRates:
    """The three token rates that applied to one call, plus why.

    ``window`` and ``context`` are recorded on the usage row so the cost can be
    explained a year later. A number nobody can explain is a number nobody can
    defend in a dispute.
    """

    input_per_1m: Decimal | None
    output_per_1m: Decimal | None
    cached_per_1m: Decimal | None
    window: str
    context: str


def resolve_rates(
    profile: dict[str, Decimal | int | time | None],
    *,
    prompt_tokens: int,
    started_at: datetime,
) -> VendorRates:
    """The rates that ACTUALLY applied — what this call cost us.

    ``profile`` carries the `model_profile` row as a plain mapping, so this
    function needs no database and no ORM.

    **Precedence: long context beats off-peak.** A long-context call in the
    off-peak window takes the long rates, because the context surcharge is the
    larger effect and no vendor publishes a long-and-off-peak cell. When the
    long column is missing the read falls back to the window rate rather than
    to nothing — a known rate in the right ballpark beats NULL, and the tier is
    recorded either way so a reader can see which happened.

    ⚠️ **A missing off-peak column falls back to the PEAK number, never to
    NULL.** The peak rate is what `vendor_price_feed` fills in, so a model
    nobody has given an off-peak price to still costs correctly at its one
    published rate.
    """
    window = window_of(
        started_at,
        offpeak_start=profile.get("offpeak_start_utc"),  # type: ignore[arg-type]
        offpeak_end=profile.get("offpeak_end_utc"),  # type: ignore[arg-type]
    )
    context = context_tier_of(
        prompt_tokens,
        threshold=profile.get("context_tier_threshold"),  # type: ignore[arg-type]
    )

    def pick(kind: str) -> Decimal | None:
        peak = profile.get(f"vendor_{kind}_per_1m_usd")
        if context == CONTEXT_LONG:
            long_rate = profile.get(f"vendor_{kind}_long_per_1m_usd")
            if long_rate is not None:
                return long_rate  # type: ignore[return-value]
        if window == WINDOW_OFFPEAK:
            off = profile.get(f"vendor_{kind}_offpeak_per_1m_usd")
            if off is not None:
                return off  # type: ignore[return-value]
        return peak  # type: ignore[return-value]

    return VendorRates(
        input_per_1m=pick("input"),
        output_per_1m=pick("output"),
        cached_per_1m=pick("cached_input"),
        window=window,
        context=context,
    )


def pricing_basis(
    profile: dict[str, Decimal | int | time | None],
) -> VendorRates:
    """The rates a TIER PRICE is derived from — always peak, always short.

    🔴 **Owner directive, 2026-09-04: the credit cost calculation always uses
    the peak value.** This is the second of the two questions in this module's
    header, and it deliberately takes no timestamp and no token count. A
    suggestion that moved with the clock would give an operator a different
    answer at breakfast than at midnight, for one unchanged product.

    The measured reason: `credit_pricing.md` §4.1 shows Fast at 30 credits
    earning 66.3 percent on the off-peak cost and 32.7 percent on the peak
    one, against a floor of 45 percent. Pricing from the cheap window sets a
    price that breaks as soon as traffic moves to the dear one.
    """
    return VendorRates(
        input_per_1m=profile.get("vendor_input_per_1m_usd"),  # type: ignore[arg-type]
        output_per_1m=profile.get("vendor_output_per_1m_usd"),  # type: ignore[arg-type]
        cached_per_1m=profile.get("vendor_cached_input_per_1m_usd"),  # type: ignore[arg-type]
        window=WINDOW_PEAK,
        context=CONTEXT_SHORT,
    )
