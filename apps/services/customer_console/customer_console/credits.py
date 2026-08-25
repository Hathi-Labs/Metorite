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
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "CREDIT_QUANTUM",
    "LEDGER_REASONS",
    "LEDGER_REASON_ADJUSTMENT",
    "LEDGER_REASON_DISCOUNT_REDEMPTION",
    "LEDGER_REASON_GRANT",
    "LEDGER_REASON_MANUAL",
    "LEDGER_REASON_PURCHASE",
    "LEDGER_REASON_USAGE",
    "OverdraftPolicy",
    "RateCard",
    "RunCeiling",
    "SpendDecision",
    "TokenUsage",
    "UnpricedModel",
    "balance_of",
    "decide_member_cap",
    "decide_run_ceiling",
    "decide_spend",
    "quantize_credits",
    "rate_call",
]

# ── The ledger vocabulary (SC-4g (v)) ───────────────────────────────────────
#
# `credit_ledger.reason` is bare TEXT and `POST /credits/grant` accepts any
# string, so *"distinguishable a year later"* was a hope rather than a fence.
# Defined ONCE here and imported by every writer, so the same event says the
# same word wherever it is written — and, critically, so a `seat_grant.reason`
# and a `credit_ledger.reason` describing one purchase agree on the day credit
# packs arrive and both tables carry a row for it.
#
# ⚠️ Deliberately NOT a CHECK constraint in this slice, and the reason is R6:
# `/credits/grant` accepts free-form reasons today, so an expand-phase
# migration must not reject rows the running code can still write. Narrowing
# `CreditGrantRequest.reason` to the enum comes first; the CHECK is a later
# contract-phase migration. Recorded so the omission reads as a decision.

#: The Router's meter — `ref` is the `request_id` (shipped, `store.record_usage`).
LEDGER_REASON_USAGE = "usage"
#: CP-9 fulfilment, PAID path — `ref` is `order:<uuid>`.
LEDGER_REASON_PURCHASE = "purchase"
#: CP-9 fulfilment, Rs 0 / partial path — `ref` is `redemption:<uuid>`.
LEDGER_REASON_DISCOUNT_REDEMPTION = "discount_redemption"
#: SC-4e goodwill — `ref` is the operator's note reference.
LEDGER_REASON_ADJUSTMENT = "adjustment"
#: `POST /credits/grant`, non-commercial grants — `ref` is operator-supplied.
LEDGER_REASON_GRANT = "grant"
#: Manual / bank-transfer activation — the OFFLINE twin of `purchase`. Written by
#: the Operator-only `POST /billing/subscriptions/activate` (§6 item (j)) when a
#: customer paid OUT OF BAND and there is no Razorpay order; `ref` is the
#: operator-supplied bank-transfer reference. It carries its own word rather than
#: `purchase` so a term settled by bank transfer stays distinguishable from a
#: processor capture a year later — the same "one event, one word" reason
#: `seat_grant.reason`, the `control_audit` row and `org_subscription.provider`
#: all say `'manual'` for exactly this activation.
LEDGER_REASON_MANUAL = "manual"

#: Every reason a ledger row may carry. Fenced structurally by
#: ``test_customer_console_payments.py`` over the CALL SITES of
#: ``store.add_credit`` — a real fence in this slice, because it reads code
#: rather than rows. (The *data* test that the three commercial reasons are
#: pairwise distinguishable is scoped to when packs land: the subscription path
#: writes zero ledger rows today, and a test over an empty table passes for the
#: wrong reason — the disarmed-gate shape CP-3 already cost us once.)
LEDGER_REASONS: frozenset[str] = frozenset({
    LEDGER_REASON_USAGE,
    LEDGER_REASON_PURCHASE,
    LEDGER_REASON_DISCOUNT_REDEMPTION,
    LEDGER_REASON_ADJUSTMENT,
    LEDGER_REASON_GRANT,
    LEDGER_REASON_MANUAL,
})

#: The smallest amount the ledger can represent: ``credit_ledger.delta`` and
#: ``usage_event.billed_credits`` are both ``NUMERIC(14, 4)``.
#:
#: Two jobs, both load-bearing. It is what a rated call is rounded to before it
#: is written, so Python and Postgres agree on the number rather than Postgres
#: silently rounding ours; and it is the probe the pre-flight gate spends
#: (:func:`decide_spend`), because the true cost of a completion is unknowable
#: before the provider answers and the only question we can honestly ask up
#: front is *"is there any headroom left at all?"*.
CREDIT_QUANTUM = Decimal("0.0001")


def quantize_credits(credits: Decimal) -> Decimal:
    """Round a rated cost to what the ledger stores.

    Done here rather than left to Postgres so the number we bill is the number
    we computed. A sub-quantum call rounds to zero and writes no ledger row at
    all, which is right: a zero-delta row is noise in the one table a customer
    reads during a dispute.
    """
    return credits.quantize(CREDIT_QUANTUM, rounding=ROUND_HALF_UP)


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
class RunCeiling:
    """The per-run spend ceiling — the circuit breaker of §4.4.

    *"An agent in a tool loop can burn a large amount in minutes, and this
    codebase has retry loops and a 32k default output ceiling. A per-run spend
    ceiling is not optional."*

    It is a **tripwire, not a budget**. Budgets are per member and per period
    and belong to CP-7; this exists so that one runaway loop cannot spend a
    month's credits in an afternoon while nobody is watching. That is why the
    number is far above any legitimate agent run and far below a monthly
    balance: a ceiling low enough to argue about is a ceiling that stops real
    work, and then somebody raises it to infinity.
    """

    #: Absolute credits, per ``(organization, run)``. 500 credits is ₹5,000 of
    #: customer-facing spend on one agent run at D19.2's ₹10 credit — an order
    #: of magnitude above a long legitimate run and an order of magnitude below
    #: a monthly balance.
    max_credits: Decimal = Decimal(500)


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
    #: What the caller has to do about a 402, in the same shape the seat cap's
    #: ``buy_more`` uses. Built here rather than at the HTTP surface so the
    #: policy and the sentence it produces cannot drift apart.
    top_up: dict | None = None


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
            top_up={
                # Credits, never rupees. What a credit costs is a commercial
                # question with an owner's answer (D19.2), and a price quoted
                # from two places is a price that eventually disagrees.
                "balance_credits": str(balance),
                "grace_credits": str(grace),
                # Enough to get back to zero, which is the number an admin can
                # act on. Below zero the balance is already the debt.
                "credits_required": str(max(Decimal(0), -projected)),
                "is_trial": is_trial,
            },
        )
    return SpendDecision(allowed=True, in_overdraft=projected < 0)


def decide_run_ceiling(
    spent_this_run: Decimal,
    *,
    ceiling: RunCeiling | None = None,
) -> SpendDecision:
    """Per-run circuit breaker: has this agent run spent enough to be stopped?

    Called with ``SUM(usage_event.billed_credits)`` for one
    ``(organization_id, run_id)`` — the run is the unit an operator debugs
    (D1's attribution four-tuple), so it is also the unit a loop is broken at.

    **403, not 402.** A 402 tells the UI "out of credits — top up", and topping
    up does not help here: the organization may have a large balance and one
    misbehaving run. **And not 429**, which invites the caller to retry with
    backoff — a breaker that a retry loop can wait out is not a breaker.

    Honest limit, stated so nobody mistakes this for a security control: the run
    id is an attribution header the caller sets, so a caller that rotates it
    escapes the ceiling. The balance gate is the backstop that does not depend
    on anything the caller says; this stops the *accident*, which is what a
    runaway loop is.
    """
    ceiling = ceiling or RunCeiling()
    if spent_this_run >= ceiling.max_credits:
        return SpendDecision(
            allowed=False,
            status=403,
            reason="run_ceiling_exceeded",
        )
    return SpendDecision(allowed=True)


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
