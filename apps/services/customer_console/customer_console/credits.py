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
    "TierRate",
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
LEDGER_REASONS: frozenset[str] = frozenset(
    {
        LEDGER_REASON_USAGE,
        LEDGER_REASON_PURCHASE,
        LEDGER_REASON_DISCOUNT_REDEMPTION,
        LEDGER_REASON_ADJUSTMENT,
        LEDGER_REASON_GRANT,
        LEDGER_REASON_MANUAL,
    }
)

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


class UsagePartitionError(Exception):
    """Raised when cached tokens cannot be a subset of the prompt total.

    🔴 **The billing code treats ``cached_tokens`` as a SUBSET of
    ``prompt_tokens``, and one of the two vendor conventions disagrees.**
    OpenAI-compatible providers report the cached count *inside*
    ``prompt_tokens``. Anthropic-style providers report it *beside* them. The
    subtraction in :attr:`TokenUsage.fresh_prompt_tokens` is right for the
    first convention and wrong for the second.

    ⚠️ **This used to clamp at zero, and that was a silent undercharge.**
    Measured 2026-09-04 against `tier-balanced`: the same real call billed
    251.60 credits read as a subset and 183.60 credits read as a sibling — 27 %
    less, with no error and no log line. `prompt=100 cached=99999` was accepted
    and billed 340 credits.

    We refuse rather than guess. Re-normalising on a hunch bills the customer
    wrong in the other direction, and a wrong bill nobody can explain is worse
    than a call we admit we could not meter. ``usage_event.cache_convention``
    records which field the count came from, so the fleet can be MEASURED
    before anybody decides to re-normalise.
    """


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
    #: Which task this card prices. A model can serve several, at different
    #: rates and in different units (D60).
    task: str = "chat"
    #: What the price is per. `tokens` uses the three per-1k rates above.
    #: Every other unit uses `credits_per_unit`.
    unit: str = "tokens"
    #: The rate for one `unit`, when `unit` is not `tokens`.
    credits_per_unit: Decimal = Decimal(0)
    #: `unpriced` | `absorbed` | `priced` (D61, G-4).
    pricing_mode: str = "unpriced"

    @property
    def is_priced(self) -> bool:
        """Whether this card may be billed against.

        ⚠️ **A ZERO CANNOT CARRY THREE MEANINGS**, which is why this reads
        ``pricing_mode`` and not the numbers (D61, G-4). Zero means all of:

        * *nobody has priced this yet* — an operational mistake, and billing it
          confidently as free looks like revenue working while margin leaks;
        * *deliberately absorbed into the seat price* — D19.2 says embeddings
          are, and that is not a mistake;
        * *deliberately free*.

        Reading the numbers cannot tell them apart. Reading the mode can.
        """
        return self.pricing_mode == "priced"

    @property
    def is_absorbed(self) -> bool:
        """Deliberately free, and NOT an error. D19.2's embeddings."""
        return self.pricing_mode == "absorbed"

    @property
    def subject(self) -> str:
        """What this card prices, for refusal messages. See TierRate's."""
        return self.model


@dataclass(frozen=True)
class TierRate:
    """Credits per unit for one (TIER, task) — what a CUSTOMER pays. D67.

    🔴 **The card the metering path bills against since 2026-08-30.** The
    tier is the product and the model is supply, so the customer's price is
    keyed on what they PICKED, not on what served them: a failover moves our
    cost and never their price, and two tiers sharing one model can still
    charge differently.

    Field names deliberately mirror :class:`RateCard` so
    :func:`rate_call` prices either card without knowing which it holds.
    """

    tier: str
    input_per_1k: Decimal
    output_per_1k: Decimal
    cached_input_per_1k: Decimal = Decimal(0)
    task: str = "chat"
    unit: str = "tokens"
    credits_per_unit: Decimal = Decimal(0)
    pricing_mode: str = "unpriced"

    @property
    def is_priced(self) -> bool:
        """`pricing_mode == 'priced'` — see RateCard.is_priced for why."""
        return self.pricing_mode == "priced"

    @property
    def is_absorbed(self) -> bool:
        """Deliberately free, and NOT an error. D19.2's embeddings."""
        return self.pricing_mode == "absorbed"

    @property
    def subject(self) -> str:
        """What this card prices, for refusal messages."""
        return f"tier {self.tier}"


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

    def __post_init__(self) -> None:
        """Assert the partition. See :class:`UsagePartitionError` for why.

        ⚠️ **On the dataclass, not in ``rate_call``.** Every path that meters —
        the completion route, the stream relay, the per-unit tasks — builds one
        of these, so the check placed here cannot be forgotten by a caller that
        arrives later. A check in the pricing function would miss the BYOK path,
        which zero-rates the bill and still records the counters.
        """
        if self.cached_tokens > self.prompt_tokens:
            raise UsagePartitionError(
                f"cached_tokens ({self.cached_tokens}) exceeds prompt_tokens "
                f"({self.prompt_tokens}); cached must be a subset of the "
                "prompt total, so this call cannot be metered"
            )

    @property
    def fresh_prompt_tokens(self) -> int:
        """Prompt tokens that were NOT served from cache.

        ⚠️ **No clamp.** ``__post_init__`` has already refused the case a clamp
        would have hidden, so a negative here is impossible by construction.
        """
        return self.prompt_tokens - self.cached_tokens


def rate_call(
    card: RateCard | TierRate,
    usage: TokenUsage,
    *,
    quantity: Decimal | None = None,
) -> Decimal:
    """Credits drawn by one call, in whatever unit its task is priced in.

    🔴 **This was tokens-only until CP-10 slice 2, and three of six tasks could
    not be priced at all.** `transcribe` is sold per minute of audio (D19.2
    says so in terms), `speak` per character, and `image` per image. None of
    them divides a token count by 1000. `tier-stt` ships in the production
    seed, so the hole was live before any multimodal work.

    ``quantity`` is what was consumed, in ``card.unit``. It is ignored for a
    token-priced card, whose quantity is the usage counters themselves.

    Raises:
        UnpricedModel: the card may not be billed against — either nobody has
            priced it, or it is priced per unit and nothing measured the
            quantity. See :class:`UnpricedModel`.
    """
    if card.is_absorbed:
        # Deliberately free (D19.2), and NOT the error below. An absorbed task
        # that raised would be indistinguishable from a misconfigured one, and
        # somebody would "fix" it by inventing a price.
        return Decimal(0)

    if not card.is_priced:
        raise UnpricedModel(
            f"{card.subject}/{card.task} has no rate-card price; refusing to bill it as free"
        )

    if card.unit == "tokens":
        thousand = Decimal(1000)
        return (
            Decimal(usage.fresh_prompt_tokens) / thousand * card.input_per_1k
            + Decimal(usage.cached_tokens) / thousand * card.cached_input_per_1k
            + Decimal(usage.completion_tokens) / thousand * card.output_per_1k
        )

    if quantity is None:
        # ⚠️ REFUSE rather than fall back to the token rates. A minute of audio
        # rated per 1k tokens is a number, and a plausible one, and wrong. The
        # metering caller downgrades this to "bill zero, loudly" — visibly,
        # where somebody can see it.
        raise UnpricedModel(
            f"{card.subject}/{card.task} is priced per {card.unit} and no quantity was measured"
        )

    return Decimal(quantity) * card.credits_per_unit


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
    grace = policy.grace_credits if (not is_trial or policy.grace_for_trial) else Decimal(0)
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
