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
    "DEFAULT_MIN_CHARGE",
    "MIN_CHARGE_ENV",
    "NO_MIN_CHARGE_TASKS",
    "LEDGER_REASONS",
    "LEDGER_REASON_ADJUSTMENT",
    "LEDGER_REASON_DISCOUNT_REDEMPTION",
    "LEDGER_REASON_GRANT",
    "LEDGER_REASON_HOLD",
    "LEDGER_REASON_MANUAL",
    "LEDGER_REASON_PURCHASE",
    "LEDGER_REASON_RELEASE",
    "LEDGER_REASON_SETTLE",
    "LEDGER_REASON_USAGE",
    "OverdraftPolicy",
    "HoldEstimate",
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
    "estimate_hold",
    "floor_charge",
    "min_charge",
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

# ── The hold cycle (migration 026, `credit_pricing.md` §5) ──────────────────
#
# 🔴 **Three rows for one call, and the third is what makes a crash safe.**
#
#     hold     -1792   reserved before the call, at the WORST case
#     settle    -423   the real charge, written with the usage row
#     release  +1792   the reservation given back
#
# Net: -423. A single mutable "reserved" column cannot survive a process that
# dies between the provider answering and the meter running — the credits stay
# reserved with nothing to reconcile them against. Three append-only rows leave
# the ledger consistent at every point.
#
# ⚠️ All three carry the REQUEST ID as `ref`, so
# `credit_ledger_reason_ref_unique` makes each one idempotent for free. A
# retried request re-inserts nothing and re-charges nothing.

#: Credits reserved before the provider is called. Always negative.
LEDGER_REASON_HOLD = "hold"
#: The real charge, written in the same transaction as the usage row.
LEDGER_REASON_SETTLE = "settle"
#: The reservation, given back. Always positive, always equal to its hold.
LEDGER_REASON_RELEASE = "release"

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
        LEDGER_REASON_HOLD,
        LEDGER_REASON_SETTLE,
        LEDGER_REASON_RELEASE,
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


@dataclass(frozen=True)
class HoldEstimate:
    """What to reserve before a call, and why that number.

    ``credits`` is what the hold takes. ``reason`` explains the shape it was
    computed for, so an operator reading a large hold can see whether it was a
    worst case or an exact figure.
    """

    credits: Decimal
    reason: str

    @property
    def is_exact(self) -> bool:
        """A deterministic job needs no release — the hold IS the charge."""
        return self.reason == "exact"


def estimate_hold(
    card: RateCard | TierRate,
    *,
    prompt_tokens: int,
    max_output_tokens: int,
    quantity: Decimal | None = None,
) -> HoldEstimate:
    """Credits to reserve before the provider is called.

    🔴 **Three cost shapes, and forcing them through one path is the mistake
    this function exists to avoid** (`credit_pricing.md` §5.2).

    * **Deterministic** — a transcription's duration is a property of the file,
      so the charge is knowable before the call. Reserve exactly it, and no
      release is owed. Audio is EASIER than text here, not harder.
    * **Variable** — a chat completion's output length is unknowable, so
      reserve the worst case on both sides.

    ⚠️ **The worst case rates at the UNCACHED rate, always.** Assuming a cache
    hit makes the hold too small, and a hold too small is an organization that
    goes negative on the settle — which is the whole failure this reserve
    exists to prevent. The cache discount reaches the customer at settle time,
    where it is measured rather than hoped for.

    ⚠️ **An unpriced card holds ZERO and does not raise.** The card ships
    unpriced (H-42), and a reserve that refused every call until somebody
    priced the slate would take the product down rather than protect it.
    Pricing is what turns this on.
    """
    if not card.is_priced:
        return HoldEstimate(Decimal(0), "unpriced")

    if card.unit != "tokens":
        if quantity is None:
            # Nothing measured the quantity, so nothing can reserve for it.
            # `rate_call` refuses this case at settle time and the metering
            # caller downgrades it to "bill zero, loudly" — holding zero keeps
            # those two answers consistent.
            return HoldEstimate(Decimal(0), "unmeasured")
        return HoldEstimate(
            quantize_credits(quantity * card.credits_per_unit), "exact"
        )

    million = Decimal(1_000_000)
    worst = (
        Decimal(max(prompt_tokens, 0)) / million * card.input_per_1m
        + Decimal(max(max_output_tokens, 0)) / million * card.output_per_1m
    )
    return HoldEstimate(quantize_credits(worst), "worst_case")


#: The environment variable the owner sets to arm the floor.
MIN_CHARGE_ENV = "CUSTOMER_CONSOLE_MIN_CHARGE_CREDITS"

#: 🔴 **SHIPS AT ZERO, which means the floor is inert until the owner sets
#: it** (`credit_pricing.md` §5.4). A two-token classification call rates to a
#: fraction of a credit and does not cover the overhead of serving it — but
#: *how much* an operation must cover is a commercial number, and §2 of the
#: specification puts every commercial number behind H-42.
#:
#: ⚠️ **The first build of this slice shipped it at 5 and it changed real
#: bills.** Thirteen suites went red because their fixtures price in fractions
#: of a credit, and each one was the floor working correctly on a number
#: nobody had agreed to. The mechanism is an agent's to build. The figure is
#: the owner's to choose, exactly as the rate card ships unpriced and the
#: spend gate ships off.
DEFAULT_MIN_CHARGE = Decimal(0)


def min_charge() -> Decimal:
    """The floor in force, from the environment. Zero unless the owner set it.

    Read per call rather than at import, so a rotation takes effect without a
    restart — the idiom `_spend_gate_enabled` already uses.

    ⚠️ An unparseable or negative value reads as ZERO rather than raising. A
    misconfigured floor must not fail completions, and zero is the shipped
    state it falls back to.
    """
    import os

    raw = os.environ.get(MIN_CHARGE_ENV, "").strip()
    if not raw:
        return DEFAULT_MIN_CHARGE
    try:
        value = Decimal(raw)
    except (ArithmeticError, ValueError):
        return DEFAULT_MIN_CHARGE
    return value if value > 0 else DEFAULT_MIN_CHARGE

#: 🔴 **Tasks the floor must NEVER touch, and this is not a nicety.**
#:
#: One embedding costs a fraction of a credit. A five-credit floor per call
#: would charge **50000 credits to index 10000 documents** against perhaps 200
#: credits of real value — a 250x overcharge, on the one task a customer runs
#: in bulk by design. §3.4 of the design document names it in terms.
#:
#: ⚠️ **Keyed on the TASK and never on the tier.** D61 makes tiers free text
#: and tasks an allowlist, so a second embedding tier — `tier-embed-fast`, a
#: customer-specific slate — would silently lose the exemption if this read a
#: tier slug. The task is the durable axis.
#:
#: ⚠️ The exemption is not "embeddings are free". `tier-embed` still rates and
#: still bills what it rates. It is only the FLOOR that does not apply, because
#: the floor assumes one call is one human action and an index is not.
NO_MIN_CHARGE_TASKS: frozenset[str] = frozenset({"embed"})


def floor_charge(credits: Decimal, *, task: str) -> Decimal:
    """Apply the per-operation floor, or refuse to for a bulk task.

    ⚠️ **A ZERO stays ZERO.** An absorbed task (D19.2) and an unpriced card
    both rate to nothing on purpose, and lifting either to five credits would
    invent a charge nobody agreed to. The floor exists to round a real but tiny
    charge up to something that covers its overhead — not to mint one.
    """
    if credits <= 0:
        return credits
    if task in NO_MIN_CHARGE_TASKS:
        return credits
    return max(credits, min_charge())


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
    """Credits per MILLION tokens for one model, as of one ``effective_from``.

    Versioned by ``effective_from`` in the table so a re-price never rewrites
    history: rating happens once, at write time, and a past invoice is never
    recomputed against today's card.
    """

    model: str
    input_per_1m: Decimal
    output_per_1m: Decimal
    cached_input_per_1m: Decimal = Decimal(0)
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
    """Credits per MILLION tokens for one (TIER, task) — what a CUSTOMER pays.

    🔴 **The card the metering path bills against since 2026-08-30.** The
    tier is the product and the model is supply, so the customer's price is
    keyed on what they PICKED, not on what served them: a failover moves our
    cost and never their price, and two tiers sharing one model can still
    charge differently.

    Field names deliberately mirror :class:`RateCard` so
    :func:`rate_call` prices either card without knowing which it holds.
    """

    tier: str
    input_per_1m: Decimal
    output_per_1m: Decimal
    cached_input_per_1m: Decimal = Decimal(0)
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
        # 🔴 A MILLION, not a thousand (migration 024, owner directive
        # 2026-09-04). Every vendor quotes per million, so the card now speaks
        # the same unit as the cost it is derived from — and a reader comparing
        # the two no longer has to carry a factor of 1000 in their head.
        #
        # ⚠️ The divisor and the field name must move TOGETHER. A card holding
        # per-million numbers divided by a thousand overcharges by 1000, which
        # is a bill nobody could mistake for a rounding error and nobody could
        # defend. `test_customer_console_credits.py` pins one exact figure for
        # exactly this reason.
        million = Decimal(1_000_000)
        return (
            Decimal(usage.fresh_prompt_tokens) / million * card.input_per_1m
            + Decimal(usage.cached_tokens) / million * card.cached_input_per_1m
            + Decimal(usage.completion_tokens) / million * card.output_per_1m
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
