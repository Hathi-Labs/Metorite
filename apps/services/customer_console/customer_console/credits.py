"""AI credits — rating a call, the balance, and the spend gate.

Spec: ``project-docs/specs/customer_console.md`` §3.4/§4.4 · decision
D19.2 (the credit unit and the cost x 2 rule) and D32.6/D32.8 (rollover, and
per-member caps as a policy against the org pool).

Money is ``Decimal`` throughout and never ``float``. Not superstition: a rate
card multiplies small per-token rates by large token counts and then sums
thousands of those per month, and binary floating point drifts exactly there.
The ledger is the customer's evidence in a dispute; it has to add up.

**No LLM is involved in any of this, and none should be.** Metering is
deterministic bookkeeping — count tokens, multiply by a rate, decrement a
balance, write a row. The only judgement call is the rate card, which is a
business decision made once by a human (§3.5).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "OverdraftPolicy",
    "RateCard",
    "SpendDecision",
    "TokenUsage",
    "UnpricedModel",
    "balance_of",
    "decide_member_cap",
    "decide_spend",
    "rate_call",
]


class UnpricedModel(Exception):
    """Raised when a model has no usable rate-card entry.

    Deliberately an exception rather than "bill zero". A model the card does not
    price is an operational mistake — a provider added without a price, a tier
    repointed — and the two safe responses are to refuse or to bill nothing
    *loudly*. Silently billing zero is neither: it looks like revenue working
    while the margin leaks, and nobody notices until the month closes.

    ``002_seed_catalog.sql`` seeds the card at zero on purpose so this fires
    until CP-6 sets real prices against measured burn.
    """


@dataclass(frozen=True)
class RateCard:
    """Credits per 1,000 tokens for one model, as of one ``effective_from``.

    Versioned by ``effective_from`` in the table so a re-price never rewrites
    history: rating happens once, at write time, and a past invoice is never
    recomputed against today's card.
    """

    model: str
    input_per_1k: Decimal
    output_per_1k: Decimal
    cached_input_per_1k: Decimal = Decimal(0)

    @property
    def is_priced(self) -> bool:
        """False when every rate is zero — i.e. the model is not really priced."""
        return any(
            r != 0
            for r in (self.input_per_1k, self.output_per_1k, self.cached_input_per_1k)
        )


@dataclass(frozen=True)
class TokenUsage:
    """What one completion consumed, as the provider reported it."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: Cache-read tokens. ⚠️ **A SUBSET of ``prompt_tokens``, not an addition to
    #: it** — that is how LiteLLM and both major providers report it. Billing
    #: them at the full input rate *and* again at the cached rate double-charges
    #: the customer for the part of the prompt that cost us least, which is
    #: precisely the thing prompt caching was sold to them as saving.
    cached_tokens: int = 0

    @property
    def fresh_prompt_tokens(self) -> int:
        """Prompt tokens that were NOT served from cache. Clamped at zero."""
        return max(0, self.prompt_tokens - self.cached_tokens)


def rate_call(card: RateCard, usage: TokenUsage) -> Decimal:
    """Credits drawn by one completion.

    Raises:
        UnpricedModel: the card prices nothing. See :class:`UnpricedModel`.
    """
    if not card.is_priced:
        raise UnpricedModel(
            f"{card.model} has no rate-card price; refusing to bill it as free"
        )

    thousand = Decimal(1000)
    return (
        Decimal(usage.fresh_prompt_tokens) / thousand * card.input_per_1k
        + Decimal(usage.cached_tokens) / thousand * card.cached_input_per_1k
        + Decimal(usage.completion_tokens) / thousand * card.output_per_1k
    )


def balance_of(deltas: list[Decimal]) -> Decimal:
    """The org's credit balance: ``SUM(delta)`` over the append-only ledger.

    There is deliberately no balance *column* to read instead. A mutable balance
    destroys the audit trail at exactly the moment a customer disputes a charge,
    which is the only moment it matters. Redis caches this sum for the hot path;
    Redis is never the truth.
    """
    return sum(deltas, Decimal(0))


@dataclass(frozen=True)
class OverdraftPolicy:
    """How far below zero a paying organization may go before AI stops.

    §3.3 fixes the shape — soft-block with a grace overdraft — and says the
    number is "a business decision encoded in a config value: write it down."
    This is where it is written down.

    A hard cut-off at exactly zero generates a support ticket and a refund
    request that together cost more than the overdraft ever will, because it
    lands mid-workflow: the customer's agent stops halfway through a task they
    were watching.
    """

    #: Absolute credits of grace. Absolute rather than "10% of purchases"
    #: because purchases accumulate forever, so a percentage of lifetime
    #: spend silently grows the exposure on your largest accounts — the exact
    #: opposite of what a credit limit is for.
    grace_credits: Decimal = Decimal(100)
    #: Trial organizations get no grace: an unpaid account is where overdraft
    #: turns into unrecoverable cost.
    grace_for_trial: bool = False


@dataclass(frozen=True)
class SpendDecision:
    allowed: bool
    #: 402 Payment Required — the entitlement axis, distinct from the seat cap's
    #: 409. The UI renders it as "out of credits — top up".
    status: int = 200
    reason: str = ""
    #: True once the org is spending into grace, so the UI can warn before the
    #: wall rather than at it.
    in_overdraft: bool = False


def decide_spend(
    balance: Decimal,
    cost: Decimal,
    *,
    policy: OverdraftPolicy | None = None,
    is_trial: bool = False,
) -> SpendDecision:
    """Pre-flight gate: may this organization make this call?

    Checked against the balance *after* the call, not before, so a single large
    call cannot vault past the floor in one step.

    **Only AI is gated.** The non-AI parts of every module keep working at zero
    balance (§3.3) — a customer who cannot open their CRM because they ran out
    of AI credits will not top up, they will churn.
    """
    policy = policy or OverdraftPolicy()
    grace = (
        policy.grace_credits
        if (not is_trial or policy.grace_for_trial)
        else Decimal(0)
    )
    projected = balance - cost

    if projected < -grace:
        return SpendDecision(
            allowed=False,
            status=402,
            reason="insufficient_credits",
            in_overdraft=balance < 0,
        )
    return SpendDecision(allowed=True, in_overdraft=projected < 0)


def decide_member_cap(
    spent_this_period: Decimal,
    cap: Decimal | None,
    *,
    on_exhaustion: str = "degrade",
    warn_at: Decimal = Decimal("0.8"),
) -> tuple[str, bool]:
    """Apply a per-member cap. Returns ``(action, should_warn)``.

    ``action`` is one of ``"allow"``, ``"degrade"``, ``"block"``.

    A cap is a **policy against the organization's pool**, never a sub-wallet
    (D32.8). The difference matters: with sub-wallets, headroom allocated to a
    member who is on holiday is stranded and the org hits a wall while holding
    credits. With policies, the pool is always fully usable and the cap only
    decides who may draw on it.

    ``degrade`` is the default because dropping a member to ``tier-fast`` keeps
    them working, and the tier vocabulary is what makes that expressible at all
    — a system that exposed raw model names could only stop them.

    A member with no cap row is unlimited within the org pool: absence of a
    policy is not a policy of zero.
    """
    if cap is None or cap <= 0:
        return "allow", False

    if spent_this_period >= cap:
        return ("block" if on_exhaustion == "block" else "degrade"), True

    return "allow", spent_this_period >= cap * warn_at
