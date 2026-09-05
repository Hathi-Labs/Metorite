"""Operator analytics — the numbers we derive, and what each one triggers.

Spec: ``specs/ai_metering_and_analytics.md`` §6.

Every function here is PURE. The reads live in ``store.py`` and are verified
against a real Postgres (R8). What is verified *here* is the arithmetic and,
much more importantly, **when we refuse to answer** — because the expensive
failure in an analytics surface is never a wrong number, it is a confident one.

🔴 **THERE IS NO CREDIT PRICE IN THIS SYSTEM.** ``launch_surface.md`` §4 sells
"₹500/user/month + AI credits" and never says what a credit costs. So
``billed_credits`` and ``provider_cost_usd`` are measured in DIFFERENT UNITS
and cannot be subtracted. Anything here that looks like money would be an
invented exchange rate, and an invented margin is worse than no margin: it
reads as fact and it is arithmetic on a number nobody chose.

What we can honestly say is the RATIO — credits billed per dollar spent. It is
unitless, it is comparable between organizations and across time, and it turns
into money the day the owner prices a credit (H-42).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

#: A1. Below this many credits per dollar we are probably losing money — but
#: only "probably", because the threshold is a guess until a credit has a
#: price. The surface shows the ratio and marks the low end. It does not claim
#: a loss.
LOW_MARGIN_RATIO = Decimal("1")

#: A3. No AI call for this long, while holding credits, reads as churn.
SILENT_AFTER_DAYS = 14

#: A6. A day this many times the trailing mean is worth a look.
SPIKE_MULTIPLE = Decimal("5")

#: A2. The window whose burn rate predicts the runway. Seven days spans a full
#: working week, so a customer who only works Monday to Friday is not credited
#: with a weekend of zero burn.
BURN_WINDOW_DAYS = 7


def margin_ratio(credits: Decimal, cost_usd: Decimal) -> Decimal | None:
    """Credits billed per dollar of provider cost. ``None`` when unanswerable.

    ⚠️ **Returns None on zero cost, and that is not the same as a good ratio.**
    Zero provider cost means we have not measured what this traffic cost us —
    the meter has not run, or the provider reported nothing. Dividing by it
    would produce infinity, and rendering infinity as "excellent margin" is the
    exact confident-wrong-number this module exists to avoid.
    """
    if cost_usd is None or Decimal(cost_usd) <= 0:
        return None
    return (Decimal(credits) / Decimal(cost_usd)).quantize(Decimal("0.01"))


def realised_margin(
    credits: Decimal,
    cost_usd: Decimal,
    *,
    inr_per_credit: Decimal | None,
    usd_to_inr: Decimal | None,
) -> Decimal | None:
    """The margin actually earned, as a FRACTION. ``None`` when unanswerable.

    🔴 **This is the number :func:`margin_ratio` deliberately refused to
    compute, and migration 017 is what made it answerable.** The comment above
    that function still stands for its own return: credits and dollars are
    different units, and subtracting them invents an exchange rate. The
    difference here is that the operator has now SAVED one — `credit_price`
    carries both the rupee price of a credit and the planning rate for
    dollars — so the conversion is a fact somebody asserted rather than a
    number this module made up.

    ⚠️ **``None`` whenever any leg is missing, and None is NEUTRAL.** No saved
    credit price means no margin, not a bad one. The console draws a dash. A
    zero here would read as "we are selling at cost", which is a claim nobody
    made.

    ⚠️ **``None`` on zero revenue, for :func:`margin_ratio`'s reason.** A tier
    that billed nothing has no margin to report, and dividing by it would
    produce a number that renders as certainty.
    """
    if inr_per_credit is None or usd_to_inr is None:
        return None
    if Decimal(inr_per_credit) <= 0 or Decimal(usd_to_inr) <= 0:
        return None

    revenue_inr = Decimal(credits) * Decimal(inr_per_credit)
    if revenue_inr <= 0:
        return None
    cost_inr = Decimal(cost_usd or 0) * Decimal(usd_to_inr)
    return ((revenue_inr - cost_inr) / revenue_inr).quantize(Decimal("0.001"))


def margin_alarms(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The tiers sitting below their own floor. Empty when none are.

    ⚠️ **A tier with no floor never alarms**, and a tier with no measured
    margin never alarms either. Both are unanswered questions rather than
    failures, and an alarm on an unanswered question teaches an operator to
    ignore the alarm.

    ⚠️ **Each tier is judged against ITS OWN floor.** A single fleet threshold
    would alarm on Powerful constantly and never on Fast — the design document
    puts their floors at 0.22 and 0.45 precisely because the same margin means
    different things on a cheap tier and an expensive one.
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        floor = r.get("margin_floor")
        realised = r.get("realised_margin")
        if floor is None or realised is None:
            continue
        if Decimal(realised) < Decimal(floor):
            out.append(r)
    return out


def runway_days(balance: Decimal, credits_burned: Decimal,
                window_days: int = BURN_WINDOW_DAYS) -> int | None:
    """Whole days of credit left at the recent burn rate. ``None`` if unknown.

    ⚠️ **None means "no burn to extrapolate from", not "forever".** A customer
    who used nothing this week has no rate, and printing ∞ or a huge number
    would hide the more interesting fact, which A3 reports instead: they are
    silent.

    ⚠️ **Floors at zero rather than going negative.** A negative runway is an
    overdraft, and the overdraft policy already owns that vocabulary.
    """
    burn = Decimal(credits_burned)
    if burn <= 0 or window_days <= 0:
        return None
    per_day = burn / Decimal(window_days)
    if per_day <= 0:
        return None
    return max(0, int(Decimal(balance) / per_day))


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_silent(balance: Decimal, last_seen: Any, now: datetime,
              after_days: int = SILENT_AFTER_DAYS) -> bool:
    """A3 — holding credits and not using them.

    ⚠️ **Both halves are required.** A customer with no credits and no usage is
    not silent, they are simply not a customer yet, and flagging them would
    bury the real signal under every trial that never started.

    ⚠️ **Never used at all counts as silent**, provided they hold credits. That
    is the sharpest version of this signal: somebody paid and never arrived.
    """
    if Decimal(balance) <= 0:
        return False
    seen = _as_dt(last_seen)
    if seen is None:
        return True
    return (now - seen) > timedelta(days=after_days)


def spike_days(series: list[dict[str, Any]],
               multiple: Decimal = SPIKE_MULTIPLE) -> list[str]:
    """A6 — days whose spend stands far above the days before them.

    ⚠️ **Each day is compared to the mean of the days BEFORE it**, never to the
    mean of the whole series. A single huge day drags a whole-series mean up
    far enough to hide itself, which is precisely the day we wanted to catch.

    ⚠️ **A run of zeros is not a baseline.** The first non-zero day after an
    idle week would beat any multiple of zero, so a zero mean yields no spike —
    starting to use the product is not an anomaly.
    """
    out: list[str] = []
    seen: list[Decimal] = []
    for row in series:
        value = Decimal(row.get("credits") or 0)
        if seen:
            mean = sum(seen, Decimal(0)) / Decimal(len(seen))
            if mean > 0 and value > mean * multiple:
                out.append(str(row.get("day")))
        seen.append(value)
    return out


def annotate_orgs(rows: list[dict[str, Any]], balances: dict[str, Decimal],
                  burn: dict[str, Decimal], now: datetime) -> list[dict[str, Any]]:
    """Attach A1, A2 and A3 to each organization row.

    Kept as one pass so a surface cannot show a margin from one window and a
    runway from another — two windows on one row is how a page starts
    disagreeing with itself.
    """
    out = []
    for r in rows:
        slug = str(r.get("slug"))
        balance = Decimal(balances.get(slug, 0))
        calls = int(r.get("calls") or 0)
        costed_calls = int(r.get("costed_calls") or 0)
        out.append({
            **r,
            "balance": balance,
            # Judged over the COSTED calls only: credits from the same rows
            # the cost came from. The old all-calls numerator made 10
            # measured calls under 1,000 billed ones read as a 10x margin -
            # the confident wrong number this module's header forbids.
            "margin_ratio": margin_ratio(
                Decimal(r.get("costed_credits") or 0),
                Decimal(r.get("cost_usd") or 0),
            ),
            "costed_share": (
                None if calls == 0
                else (Decimal(costed_calls) / Decimal(calls)
                      ).quantize(Decimal("0.01"))
            ),
            "runway_days": runway_days(balance, Decimal(burn.get(slug, 0))),
            "silent": is_silent(balance, r.get("last_seen"), now),
        })
    return out
