"""The Customer Console HTTP surface.

WS-31 CP-1/CP-3 · spec ``project-docs/specs/customer_console.md`` §6.

**Four authentication schemes, and which one an endpoint takes is a design
statement** (see :mod:`customer_console.auth`):

  * ``Operator`` — a staff token, for cross-organization surfaces: provisioning,
    seat writes, credit grants, key issuance.
  * ``Internal`` — the Router's token, for writing the meter.
  * ``KeyCaller`` — the customer's own ``cc_live_…`` key. **Read-only**, and it
    reaches exactly one endpoint (``/me``). **The key resolves the
    organization**; nothing under key auth takes an organization from request
    input, because that would make the caller the authority on which customer
    they are (``user_management_contract.md`` R11).
  * ``ResolveCaller`` / ``ProvisionCaller`` — the ``cc_depl_…`` deployment key
    at the two doors its **capabilities** open: ``resolve`` reaches
    ``POST /registry/resolve`` (CP-2b) and ``provision`` reaches the second arm
    of ``POST /orgs/provision`` (CP-2c slice 1). Both endpoints take **both**
    that key and ``Operator``: one endpoint, two schemes, and the credential —
    never the body — chooses the shape. A key holding only ``{resolve}``, the
    column default, is refused at provision with a logged 403.

⚠️ The customer key deliberately cannot write the meter. It briefly could, and
verification found that let a negative ``billed_credits`` mint credits — and,
more fundamentally, made the metered party the reporter of its own usage. See
:mod:`customer_console.auth` for the full note.

Both fail **closed**: the operator token has no default and an unconfigured
deployment 503s rather than admitting anyone. That is CP-0's lesson applied from
the first line rather than retrofitted — do not "temporarily" relax it, which is
precisely how the workbench came to serve every route to anyone (D33.1).

Still to come: the customer-admin surface is WS-30's, and the operator console is
CP-8 — a separate deployable app (D35), never a route tree in here.

Endpoints are ``def`` rather than ``async def`` so FastAPI runs them in its
threadpool alongside the sync engine (see :mod:`customer_console.db`). ⚠️ CP-4 first
shipped ``/v1/chat/completions`` as ``async def`` while still opening the sync
engine inside it, which blocked the event loop for two round trips on the
highest-QPS endpoint on the plane *and* contradicted this paragraph. It is
``def`` again; the provider coroutine is driven with ``asyncio.run`` inside the
threadpool worker.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from customer_console import payments, store
from customer_console.auth import (
    Caller,
    DeploymentCaller,
    Internal,
    KeyCaller,
    Operator,
    PayingCaller,
    ProvisionCaller,
    ResolveCaller,
    SignedWebhook,
)
from customer_console.credits import (
    CREDIT_QUANTUM,
    LEDGER_REASON_PURCHASE,
    LEDGER_REASONS,
    OverdraftPolicy,
    RunCeiling,
    TokenUsage,
    UnpricedModel,
    balance_of,
    decide_run_ceiling,
    decide_spend,
    quantize_credits,
    rate_call,
)
from customer_console.db import get_engine
from customer_console.keys import (
    ENV_DISCOUNT,
    is_discount_code,
    mint_key,
    split_key,
    verify_secret,
)
from customer_console.lifecycle import (
    TransitionRefused,
    assert_transition,
    capabilities_of,
)
from customer_console.router import (
    ExtractedUsage,
    TierUnknown,
    call_provider,
    provider_credential,
    resolve_rate_card,
    resolve_tier,
    usage_from_response,
)
from customer_console.seats import CORE_PLAN_SLUG, decide_assignment, seat_counts

_log = logging.getLogger("platform.router")

app = FastAPI(
    title="Metorite Customer Console",
    description=(
        "Organizations, seats, subscriptions and AI metering. Cross-tenant by "
        "design (saas_multitenancy.md §0.9.2) — never exposed to a tenant."
    ),
    version="0.1.0",
)


# ── Schemas ─────────────────────────────────────────────────────────────────

class ProvisionRequest(BaseModel):
    """One body, two schemes — and ``deployment_label`` is what tells them apart.

    ⚠️ It is **optional here and mandatory in neither direction by pydantic**,
    on purpose, exactly as :class:`ResolveRequest`'s ``org_slug`` is. Under the
    operator scheme it is required; under a deployment key it is refused with a
    **400**. Both rules are enforced in the handler rather than by the model,
    because a model can only express one of them, and the one it would express
    (``str``, required) is the shape the deployment-key arm exists to forbid
    for the caller it is about.

    A missing operator ``deployment_label`` therefore answers **400** and not
    pydantic's 422 — pinned by ``test_nothing_infers_the_deployment_from_there_
    being_exactly_one``, whose clause (a) was amended for exactly this reason
    (WS-31 CP-2c slice 1; D46.6 item 1 as amended in ``customer_console.md``,
    ruled in writing rather than by an implementer in silence). The 422 that
    clause used to assert was *pydantic refuses before the handler runs*; the
    property it asserts now is *the handler refuses without consulting a
    count*, which is weaker to start from and is why the ``count(*) = 1``
    mutation is still run against it.
    """

    slug: str
    name: str
    #: Which deployment this organization is placed on — **named by the
    #: operator, and named by NOBODY else** (WS-29 MT-1j slice 4 · D46.6 item
    #: 1). The `Operator` credential is cross-org by design and carries no
    #: deployment identity of its own, so the box has to be said out loud; it
    #: resolves against `deployment.label` (`001_customer_console.sql:82-94`,
    #: UNIQUE) — a name rather than a UUID, because an operator can say a name.
    #:
    #: ⚠️ **Absent under a deployment key. Present is 400, never ignored** —
    #: that caller IS a deployment, so naming one would be a credential
    #: asserting an identity it already has and might be asserting a different
    #: one (R11, the same rule as `ResolveRequest.org_slug`). An ignored field
    #: is a caller who believes it worked.
    #:
    #: ⚠️ **Nothing infers it in either arm.** A `count(deployment) = 1`
    #: fallback is forbidden by name (D46.6 item 3, `store.deployment_by_label`)
    #: — a missing operator label is a 400 and an unknown one a 404 even with
    #: exactly one deployment seeded.
    deployment_label: str | None = None
    #: Captured at SIGNUP, not at first invoice (saas_operations_doctrine.md
    #: §3.1) — chasing a GSTIN after invoices have gone out is a customer
    #: conversation, not a migration.
    gstin: str | None = None
    billing_state: str | None = None
    owner_email: str
    core_seats: int = Field(default=1, ge=1)


class LifecycleRequest(BaseModel):
    org_slug: str
    target: str
    reason: str | None = None
    #: Days of export window when moving to `cancelled`. Named here so the
    #: window is an explicit act rather than an implicit default nobody chose.
    export_window_days: int = Field(default=30, ge=1)


class ResolveRequest(BaseModel):
    """One body, two schemes — and ``org_slug`` is what tells them apart.

    ⚠️ It is **optional here and mandatory in neither direction by pydantic**,
    on purpose. Under the operator scheme it is required; under a deployment
    key it is refused with a **400**. Both rules are enforced in the handler
    rather than by the model, because a model can only express one of them, and
    the one it would express (``str``, required) is the shape CP-2b's clause 2
    exists to forbid for the caller it is about.

    A missing operator ``org_slug`` therefore answers **400** and not pydantic's
    422 — pinned by ``test_an_operator_without_an_org_slug_is_refused``, because
    relaxing a model is exactly how a required field silently becomes optional.
    """

    #: Absent under a deployment key. **Present is 400, never ignored** — an
    #: ignored field is a caller who believes it worked (clause 2).
    org_slug: str | None = None
    email: str
    display_name: str | None = None


class SeatWriteRequest(BaseModel):
    org_slug: str
    email: str
    plan_slug: str
    source: str = "alacarte"


class CreditGrantRequest(BaseModel):
    """An operator's ledger write.

    ⚠️ ``reason`` was free-form TEXT until CP-9, which is why *"a discounted
    purchase is distinguishable a year later"* was a hope rather than a fence
    (SC-4g (v)). It is now checked against :data:`credits.LEDGER_REASONS` — the
    **expand-phase** half of that clause, and deliberately the only half in
    this slice: a `CHECK` constraint on ``credit_ledger.reason`` would reject
    rows the running code can still write, which is R6's whole subject. The
    constraint is a later contract-phase migration.

    Validated rather than typed as a ``Literal`` so the vocabulary has exactly
    one definition — a Literal here would be a second copy, and a second copy
    of a vocabulary is how two writers come to disagree about one event.
    """

    org_slug: str
    credits: Decimal
    reason: str = LEDGER_REASON_PURCHASE
    ref: str | None = None

    @field_validator("reason")
    @classmethod
    def _known_reason(cls, value: str) -> str:
        if value not in LEDGER_REASONS:
            raise ValueError(
                f"{value!r} is not a ledger reason; expected one of "
                f"{sorted(LEDGER_REASONS)} (subscription_console.md SC-4g (v))"
            )
        return value


class IssueKeyRequest(BaseModel):
    org_slug: str
    label: str | None = None


class RevokeKeyRequest(BaseModel):
    org_slug: str
    prefix: str


class OrderLineRequest(BaseModel):
    """One basket line. The PRICE is not here — the catalog decides it."""

    plan_slug: str
    quantity: int = Field(ge=1)


class CreateOrderRequest(BaseModel):
    """A basket. Deliberately carries no amount, no currency and no code.

    A caller-supplied amount would be a caller-supplied price, and a checkout
    that trusts the browser about what something costs is the oldest bug in
    e-commerce. Every paisa comes from ``plan_catalog`` through
    ``payments.paise`` (§9.2).
    """

    lines: list[OrderLineRequest] = Field(min_length=1)

    model_config = {"extra": "forbid"}


class RedeemRequest(BaseModel):
    """The bearer code, whole. Only its PREFIX is ever stored or logged."""

    code: str

    model_config = {"extra": "forbid"}


class IssueDiscountRequest(BaseModel):
    """Operator-only. The pre-authorization a customer later presents."""

    label: str
    kind: str
    #: NULL = an OPEN code any organization may present. Naming an org binds it.
    org_slug: str | None = None
    percent_bp: int | None = Field(default=None, ge=1, le=10000)
    amount_paise: int | None = Field(default=None, ge=1)
    max_redemptions: int = Field(default=1, ge=1)
    expires_at: datetime | None = None

    model_config = {"extra": "forbid"}


class OrderLineView(BaseModel):
    plan_slug: str
    quantity: int
    unit_price_paise: int


class OrderDiscountView(BaseModel):
    #: The code's clear prefix — NEVER its secret (SC-4g (i)).
    code_prefix: str
    discount_paise: int


class OrderView(BaseModel):
    """What a customer may read back about their own order (§9.3a).

    ⚠️ **Deliberately absent: ``provider_order_id`` and any provider payload.**
    The customer's browser has no use for the provider's identifiers, and a
    field nothing reads is a field somebody eventually reads. Pinned
    structurally by ``test_the_order_read_carries_no_provider_identifiers``, so
    adding one has to argue with a red test rather than with a reviewer's
    attention.

    Integer paise on the wire, as everywhere else: the browser formats, it
    never arithmetics.
    """

    id: str
    status: str
    provider: str
    gross_paise: int
    discount_paise: int
    taxable_paise: int
    gst_paise: int
    total_paise: int
    gst_split: str | None
    expires_at: datetime
    created_at: datetime
    terminal_at: datetime | None
    lines: list[OrderLineView] | None = None
    discount: OrderDiscountView | None = None


class OrderPageView(BaseModel):
    """A page of orders. Same objects, without ``lines`` (§9.3a)."""

    orders: list[OrderView]
    #: The id to pass as ``cursor`` for the next page, or ``None`` at the end.
    next: str | None = None


class CatalogPlanView(BaseModel):
    """One sellable catalog row (§6 item (f)).

    ⚠️ **Five fields, and the money one is PAISE** — pinned exactly by
    ``test_the_catalog_read_carries_no_per_org_state_and_paise_only``, for
    ``OrderView``'s reason: a field nothing reads is a field somebody
    eventually reads.

    Two absences are the design. **No ``price_inr``:** a rupee field beside a
    paise-denominated order API is precisely the ambiguity §9.2 exists to
    prevent, and one denomination on the wire means the browser can only
    format. **No per-org state** — no entitlement, no seat count, no
    org-specific price. Prices are catalog data (MT-2/SC-1a own per-org
    pricing and neither is built), and a catalog that answers differently per
    customer is a pricing engine, which this is not.
    """

    slug: str
    name: str
    kind: str
    price_paise: int
    sort_order: int


class CatalogView(BaseModel):
    """The priced ladder, active rows only, in ``sort_order`` (§6 item (f))."""

    plans: list[CatalogPlanView]


class SeatPlanView(BaseModel):
    """One plan's seat counts for the calling organization (§6 item (g)).

    ⚠️ **Exactly five fields, and they ARE the one seat vocabulary** — pinned by
    ``test_the_seats_read_carries_the_seat_vocabulary_and_nothing_else``, on the
    model AND on the wire, for ``CatalogPlanView``'s reason: a field nothing reads
    is a field somebody eventually reads. The four counts are
    :class:`customer_console.seats.SeatCounts`'s — ``available`` zero-clamped,
    ``oversubscribed`` its companion — surfaced, never recomputed.

    Two absences are the design. **No organization id:** the organization is the
    credential's, so putting it on the wire would echo back the one thing a
    customer must never be able to NAME (R11). **No price:** a seat is a count,
    not a quote — pricing is ``GET /billing/catalog``'s (and MT-2/SC-1a's), and a
    seat grid that also quoted money would be a second denomination on a second
    wire, exactly the ambiguity ``CatalogPlanView`` keeps off this API.
    """

    plan_slug: str
    purchased: int
    assigned: int
    available: int
    oversubscribed: bool


class SeatsView(BaseModel):
    """The caller's seats, one row per plan it has touched (§6 item (g)).

    Plans with neither a grant nor a live assignment are **absent**, not zero
    rows — the same skip ``billing_summary`` makes, so "the org never bought this"
    and "the org bought zero of this" are not made to look alike.
    """

    plans: list[SeatPlanView]


class UsageRequest(BaseModel):
    """Written by the **Router**, which holds the internal token — never by the
    customer whose usage it describes.

    ``organization_id`` is named explicitly here and that is correct: the caller
    is trusted infrastructure serving every customer, so naming the subject is
    its job, exactly as it is for the operator surfaces. R11's prohibition is on
    letting an *untrusted* caller name its own tenant, which is what the
    organization-key version of this endpoint did and why it was withdrawn.

    ⚠️ Every quantity is floored at zero. Without ``ge=0`` a negative
    ``billed_credits`` became a *positive* ledger delta — verification minted
    100,000 credits through this endpoint on a live database. The schema carries
    the matching CHECK constraints (migration 003) so the floor survives a
    future caller that bypasses this model.
    """

    organization_id: str
    request_id: str
    billed_credits: Decimal = Field(default=Decimal(0), ge=0)
    user_email: str | None = None
    agent: str | None = None
    module_slug: str | None = None
    model: str | None = None
    tier: str | None = None
    #: D1's attribution four-tuple is (run, member, agent, instance). Without
    #: the run id a completion cannot be rolled up per agent run, which is the
    #: unit an operator actually debugs.
    run_id: str | None = None
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)


#: Parameters a caller may forward to the provider. **An allowlist, not a
#: blocklist**, and the difference was a live credential-exfiltration hole.
#:
#: CP-4 shipped `extra="allow"` and excluded two fields by name. `api_base` was
#: therefore forwarded from the request body, and was only overridden when the
#: credential row happened to carry one — which the platform's own row does not.
#: Verification measured the result: a customer sending
#: `{"api_base": "https://attacker.example/v1"}` received a 200 while OUR
#: platform provider key was sent to their host. One field, total compromise of
#: the credential `004_provider_keys.sql` exists to protect.
#:
#: A blocklist is only ever as complete as the author's imagination of the
#: provider's parameter surface — and litellm's surface grows without asking us.
_FORWARDABLE = frozenset({
    "messages", "temperature", "top_p", "n", "stop", "max_tokens",
    "presence_penalty", "frequency_penalty", "logit_bias", "user",
    "response_format", "seed", "tools", "tool_choice", "parallel_tool_calls",
    "reasoning_effort", "thinking",
})

#: Ceilings on the parameters that multiply what one request costs US.
#: Verification forwarded `num_retries=50`, `timeout=9999` and
#: `max_tokens=10_000_000` untouched; combined with meter suppression that is a
#: zero-balance trial org burning our provider account with nothing to show for
#: it. "No gate" (CP-4 is unpriced) was the decision; "no ceiling" was not.
_MAX_OUTPUT_TOKENS = 32_000


class CompletionRequest(BaseModel):
    """An OpenAI-shaped chat completion, addressed to a TIER.

    ⚠️ `extra="forbid"`. Everything the caller may forward is named in
    :data:`_FORWARDABLE`; anything else is rejected rather than passed through.
    See that constant for the hole this closes.
    """

    model: str = "tier-balanced"
    messages: list[dict[str, Any]]
    #: The caller's own correlation id. Stored as `client_ref`, trusted for
    #: NOTHING — it used to be the metering idempotency key, which let a caller
    #: suppress their own meter by reusing one value forever (verification F2).
    client_ref: str | None = None
    stream: bool = False

    temperature: float | None = None
    top_p: float | None = None
    n: int | None = None
    stop: Any | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logit_bias: dict | None = None
    user: str | None = None
    response_format: dict | None = None
    seed: int | None = None
    tools: list | None = None
    tool_choice: Any | None = None
    parallel_tool_calls: bool | None = None
    reasoning_effort: str | None = None
    thinking: dict | None = None

    model_config = {"extra": "forbid"}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _org_id(conn, slug: str) -> str:
    row = conn.execute(
        text("SELECT id FROM organization WHERE slug = :slug"), {"slug": slug}
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no organization {slug!r}")
    return str(row[0])


def _seat_grid(conn, org_id: str) -> list[SeatPlanView]:
    """The per-plan seat grid for one organization — the ONE seat-grid loop.

    Both the operator's ``GET /billing/summary`` (cross-org, by ``org_slug``) and
    the customer's ``GET /me/seats`` (own org, from the credential) render exactly
    this: every ACTIVE plan the org holds a grant or a live assignment on, in
    ``sort_order``, folded through the one seat vocabulary — ``store.seat_rows``
    through ``seat_counts`` (§3.3, D32.5). ``available``'s zero-clamp and
    ``oversubscribed`` are ``seat_counts``'s, surfaced not recomputed, and a plan
    the org never touched is skipped, not emitted as a zero row.

    Extracted so the two surfaces cannot drift into two loops: one enumerate →
    skip → fold, one SQL, computed once. ``billing_summary`` wraps the same grid
    in its ``organization_id``/``credit_balance`` envelope; ``my_seats`` returns
    it as a bare ``SeatsView``.
    """
    plans = [
        r[0] for r in conn.execute(
            text("SELECT slug FROM plan_catalog WHERE active ORDER BY sort_order")
        )
    ]
    grid = []
    for plan in plans:
        grants, assigned = store.seat_rows(conn, org_id=org_id, plan_slug=plan)
        if not grants and not assigned:
            continue  # never bought, never assigned — not worth a row
        c = seat_counts(plan, grants, assigned)
        grid.append(SeatPlanView(
            plan_slug=plan,
            purchased=c.purchased,
            assigned=c.assigned,
            available=c.available,
            oversubscribed=c.oversubscribed,
        ))
    return grid


def _audit(conn, org_id: str | None, action: str, detail: dict[str, Any],
           *, actor: str = "operator") -> None:
    """Write one audit row.

    ``actor`` defaults to ``operator`` because that was every writer until CP-9;
    the checkout is the first surface a CUSTOMER's own credential writes
    through, and an audit trail that called those acts "operator" would
    misattribute the one class of write we most need to tell apart later.
    """
    conn.execute(
        text(
            "INSERT INTO control_audit (organization_id, actor, action, detail) "
            "VALUES (:org, :actor, :action, CAST(:detail AS jsonb))"
        ),
        {"org": org_id, "actor": actor, "action": action,
         "detail": json.dumps(detail)},
    )


# ── CP-6: rating, the balance gate and the per-run breaker ──────────────────

#: Ship dark (CLAUDE.md §4). With this unset — the state of every environment —
#: the Router behaves exactly as CP-4 shipped it: it forwards, it counts, and it
#: refuses nothing. With it set, the two CP-6 refusals below become live.
#:
#: It is a flag rather than always-on for one measured reason: a newly
#: provisioned organization is `trial` with a zero balance, and **how many
#: credits a trial starts with is an open owner input** (spec §9.2). Enforcing
#: on that today would refuse the first AI call of every new customer, which is
#: a product outage dressed as a billing control. Flipping it for a real
#: customer is the owner's act, on the same footing as §8's gate 5.
_SPEND_GATE_ENV = "CUSTOMER_CONSOLE_SPEND_GATE"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _spend_gate_enabled() -> bool:
    return os.environ.get(_SPEND_GATE_ENV, "").strip().lower() in _TRUTHY


def _spend_refusal(conn, caller: Caller) -> HTTPException | None:
    """Pre-flight: may this organization spend on this call? (§4.4)

    Returns the refusal to raise, or ``None`` to proceed. Returned rather than
    raised so the caller can leave the transaction cleanly — nothing here
    writes, and an exception thrown through ``engine.begin()`` reads as if it
    might have.

    **The cost passed to the gate is one credit quantum, not an estimate.** The
    true cost of a completion is unknowable until the provider answers, and an
    estimate would either refuse calls the customer can afford (if we guessed
    from the 32k output ceiling) or wave through the one that empties the
    account. So the pre-flight asks the only question it can answer honestly —
    *is there any headroom left at all?* — and the real draw happens at metering
    against the tokens actually consumed. §4.4's soft-block does the rest: at
    zero a paying organization keeps working into the grace overdraft and only
    stops at the floor.
    """
    balance = balance_of(store.credit_deltas(conn, org_id=caller.organization_id))
    decision = decide_spend(
        balance,
        CREDIT_QUANTUM,
        policy=OverdraftPolicy(),
        is_trial=(caller.organization_status == "trial"),
    )
    if not decision.allowed:
        return HTTPException(
            status_code=decision.status,
            detail={"reason": decision.reason, "top_up": decision.top_up},
        )

    # The circuit breaker. Only a call that names a run can be part of a
    # runaway loop, and only that run is stopped — a second run for the same
    # organization is unaffected, because the ceiling is a tripwire on one
    # loop, not a budget on the customer.
    if caller.run_id:
        spent = store.run_spend(
            conn, org_id=caller.organization_id, run_id=caller.run_id
        )
        ceiling = RunCeiling()
        if not decide_run_ceiling(spent, ceiling=ceiling).allowed:
            _log.warning(
                "router.run_ceiling_tripped",
                extra={"cc_run_id": caller.run_id},
            )
            return HTTPException(
                status_code=403,
                detail={
                    "reason": "run_ceiling_exceeded",
                    "run_id": caller.run_id,
                    "spent_credits": str(spent),
                    "ceiling_credits": str(ceiling.max_credits),
                },
            )
    return None


def _rate_completion(conn, *, model: str, usage: ExtractedUsage) -> Decimal:
    """Credits drawn by one completion. **Never raises.**

    An unpriced model bills zero *loudly* rather than failing the call: the
    completion has already happened and the customer already has it, so the
    only choice left is whether we also lose the usage row. We do not — the row
    is the evidence, and CP-6 sets prices against exactly this data.
    ``002_seed_catalog.sql`` seeds the card at zero on purpose, so this warning
    is the expected state until the owner prices it (a commercial act, §8).

    ⚠️ **BYOK is not zero-rated here yet.** §3.4 says a BYOK organization is
    metered but not charged for tokens; today this function does not know which
    credential served the call, so a priced card would charge them. Harmless
    while every card is zero, and it must be closed before any real price is
    set — it is recorded in the spec's CP-6 note rather than left as a surprise.
    """
    try:
        card = resolve_rate_card(conn, model)
        return quantize_credits(
            rate_call(
                card,
                TokenUsage(
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cached_tokens=usage.cached_tokens,
                ),
            )
        )
    except UnpricedModel:
        # `router_model`, not `model`: a stdlib LogRecord already owns several
        # short names and a collision raises inside the logging call itself.
        _log.warning("router.unpriced_model", extra={"router_model": model})
        return Decimal(0)


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, str]:
    """Liveness. Deliberately unauthenticated and deliberately says nothing."""
    return {"status": "ok"}


@app.post("/orgs/provision")
def provision(req: ProvisionRequest, caller: ProvisionCaller) -> dict[str, Any]:
    """Create an organization, its owner and its Core seats. Idempotent.

    Idempotent on the org slug rather than on a request id, because the natural
    key is what a retrying signup form actually resends. Provisioning is a
    multi-step action that WILL fail halfway; re-running it must converge on one
    organization rather than produce a second (§2.1).

    **It also PLACES the organization** (WS-29 MT-1j slice 4). Until 2026-08-19
    nothing in the tree wrote ``org_placement``, and ``store.
    deployment_visible_orgs`` inner-joins it — so an organization created here
    could never be resolved by any deployment key, forever, failing closed in a
    way that reads as "the Console is down".

    **One endpoint, two schemes, and the CREDENTIAL says which deployment**
    (WS-31 CP-2c slice 1, D46.6 item 1 as amended). A second endpoint was
    refused for root ``CLAUDE.md`` §5's reason — it would be a second way to do
    an existing thing — which is the same call ``POST /registry/resolve`` made.

      * **Operator** — a staff act on a **named** box. The credential is
        cross-org and carries no deployment identity, so ``deployment_label``
        is required and its absence is a **400**.
      * **Deployment key** (capability ``provision``) — a box provisioning a
        customer onto **itself**. The deployment is ``caller.deployment_id``
        and the body may not name one: presenting ``deployment_label`` is a
        **400, never ignored** (R11, the same rule the resolve arm applies to
        ``org_slug``).

    **Nothing infers the deployment in either arm.** A ``count(deployment) = 1``
    fallback is forbidden by name (D46.6 item 3).

    Two refusals beyond that, and they are different questions:

    * **404** — no deployment carries that label. Operator arm only: a key's
      deployment is a foreign key, so it cannot fail to exist. Naming the label
      back is the operator idiom :func:`_org_id` already ships: this credential
      is cross-org by design, so telling it what it asked about is not an
      existence oracle (``customer_console.md`` CP-9 clause 7 — "the contrast,
      not the precedent").
    * **409** — the organization is already placed on a *different*
      deployment. Provisioning never MOVES a placement (D46.6 item 4); a move
      is a separate operator act with its own semantics and its own ticket.
      **Both arms**, and it matters more under the key: the caller there is a
      box rather than a human who might notice a 200 that changed nothing.

    Re-running against the SAME deployment is a no-op on the placement row —
    ``moved_at`` is untouched — exactly like every other step here.

    **The deployment-key arm CREATES an org, it never JOINS one** (WS-31 CP-2c
    slice 1 repair, R11). ``ensure_organization`` is idempotent on the slug, so
    a slug that already exists returns the existing org — and the operator arm's
    idempotent re-provision (cross-org staff, by design) is fine there. The
    deployment-key arm is not: driven in slice 2 by a USER-supplied slug from an
    unauthenticated signup form, an unchecked re-provision would write the
    request's ``owner_email`` an ``owner`` membership and a seat in *someone
    else's* org. So under the key, an existing org already owned by a DIFFERENT
    identity is refused, and — with the placed-elsewhere case — collapses to one
    **409 "slug unavailable"** that names nothing (no placement, no owner, no
    deployment, no "taken here" vs "taken elsewhere"): the old 409/200 split was
    a hijack AND an existence oracle over the global slug namespace. The
    idempotent same-owner retry and the crash-before-membership resume (org with
    NO owner yet) both still complete, because the guard keys on *owned by
    someone else*, not on *exists*.
    """
    with get_engine().begin() as conn:
        # Resolved FIRST, before anything is written: a refusal discovered
        # after five inserts is a rollback whose reason nobody can read in the
        # log. Which arm resolves it is the credential's answer, never the
        # body's.
        if caller is None:
            if req.deployment_label is None:
                # Refused HERE rather than by the model. `deployment_label`
                # became optional so the deployment-key arm could refuse it;
                # without this line that relaxation would silently make the
                # operator's own subject optional — and it would do it by
                # opening the one code path in which a sole-deployment guess
                # could be consulted.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "deployment_label is required under the operator "
                        "scheme"
                    ),
                )
            deployment_id = store.deployment_by_label(
                conn, label=req.deployment_label
            )
            if deployment_id is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"no deployment {req.deployment_label!r}",
                )
        else:
            if req.deployment_label is not None:
                # 400, never ignored — and refused on SHAPE, before the value
                # is looked at, so naming its own box and naming one that does
                # not exist are the same refusal. Consulting the value first
                # would make the status code answer "does this deployment
                # exist" for every label a caller cares to try.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "a deployment key may not name a deployment; the "
                        "deployment is the credential's own"
                    ),
                )
            deployment_id = caller.deployment_id

        org_id = store.ensure_organization(
            conn, slug=req.slug, name=req.name,
            gstin=req.gstin, billing_state=req.billing_state,
        )

        # Placement lands before seats and membership: where an organization
        # lives is the most fundamental fact about it, and an org that has
        # seats but no placement is the shape this slice exists to remove.
        placed_on = store.current_placement(conn, org_id=org_id)
        placed_elsewhere = placed_on is not None and placed_on != deployment_id

        if caller is None:
            # ── Operator arm — UNCHANGED from slice 4. ──────────────────────
            # Cross-org staff BY DESIGN, so it names the target back in the
            # operator's own vocabulary. Provisioning never MOVES a placement;
            # a move is a separate operator act with its own ticket (D46.6
            # item 4). The operator is a human who might otherwise believe a
            # 200 moved a customer, so the refusal is explicit and legible.
            if placed_elsewhere:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{req.slug!r} is already placed on another "
                        f"deployment; provisioning never moves a placement — "
                        f"moving it to {req.deployment_label!r} is a separate "
                        f"operator act (saas_multitenancy.md §11 MT-1j "
                        f"slice 4)"
                    ),
                )
        else:
            # ── Deployment-key arm — it CREATES an org, it never JOINS one. ──
            # Two causes, ONE refusal that reveals NOTHING an oracle could read:
            #   * the slug is placed on ANOTHER box (never move — inherited); or
            #   * the slug already has an owner who is not this request's
            #     `owner_email` (R11 — a per-box `provision` key is driven in
            #     slice 2 by a USER-supplied slug from an unauthenticated signup
            #     form, so making its caller a co-owner of a stranger's org must
            #     be refused at THIS door, not the gateway).
            # Both collapse to a bare 409 "slug unavailable": no placement, no
            # owner, no deployment id, no label, no distinction between "taken
            # here" / "taken elsewhere" / "owned by someone else". The old split
            # (409 naming the box when placed elsewhere, a 200 that JOINED
            # otherwise) was BOTH a hijack and an existence oracle over the
            # GLOBAL slug namespace — the exact thing `customer_console.md` §5
            # forbids ("never the existence of an organization this deployment
            # does not serve"). Refused BEFORE any write (placement, membership,
            # seat, audit), so the transaction rolls back having committed
            # nothing.
            #
            # ⚠️ Agent-proposed default (D16/D17 class), recorded for owner
            # ratification in `customer_console.md` CP-2c slice 1. Residual,
            # flagged honestly there: slug AVAILABILITY stays observable at any
            # signup-provision door because global-unique-slug is a hard
            # constraint a form must report — this narrows the oracle to "slug
            # taken: yes/no" and removes the placement/ownership discrimination;
            # the residual is inherent, not closed here. The no-owner-yet resume
            # case (org created, crash before membership) is NOT a conflict, so
            # the same `owner_email` may complete it.
            if placed_elsewhere or store.org_owned_by_other(
                conn, org_id=org_id, owner_email=req.owner_email
            ):
                raise HTTPException(
                    status_code=409, detail="slug unavailable"
                )
        store.place_organization(
            conn, org_id=org_id, deployment_id=deployment_id
        )

        identity_id = store.ensure_identity(conn, email=req.owner_email)

        # Only grant on FIRST provision — a retry must not keep buying seats.
        grants, _assigned = store.seat_rows(
            conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG
        )
        if not grants:
            store.grant_seats(
                conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG,
                quantity=req.core_seats, reason="provision",
            )

        conn.execute(
            text(
                """
                INSERT INTO org_membership
                    (organization_id, user_identity_id, role, status, joined_at)
                VALUES (:org, :identity, 'owner', 'active', now())
                ON CONFLICT (organization_id, user_identity_id) DO NOTHING
                """
            ),
            {"org": org_id, "identity": identity_id},
        )
        store.try_assign_seat(
            conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG,
            identity_id=identity_id, source="core",
        )
        # The trial subscription. Without this row an org has seats and no
        # commercial state at all, and every billing surface has to invent a
        # default — which is how two surfaces come to disagree about whether a
        # customer is in trial.
        conn.execute(
            text(
                """
                INSERT INTO org_subscription (organization_id, status,
                                              trial_ends_at)
                VALUES (:org, 'trial', now() + interval '14 days')
                ON CONFLICT (organization_id) DO NOTHING
                """
            ),
            {"org": org_id},
        )

        # Resumability, recorded rather than assumed. Provisioning is a
        # multi-step distributed action that WILL fail halfway; this row is what
        # lets a re-run tell "already done" from "never started" instead of
        # guessing from whichever side effect it happens to find first.
        conn.execute(
            text(
                """
                INSERT INTO provisioning_run
                    (idempotency_key, organization_id, steps_done, status)
                VALUES (:key, :org,
                        ARRAY['org','placement','identity','seats',
                              'membership','subscription'],
                        'complete')
                ON CONFLICT (idempotency_key) DO UPDATE
                    SET organization_id = EXCLUDED.organization_id,
                        steps_done = EXCLUDED.steps_done,
                        status = 'complete',
                        updated_at = now()
                """
            ),
            {"key": f"provision:{req.slug}", "org": org_id},
        )
        # ⚠️ The ACTOR distinguishes the two arms, and it is not decoration:
        # `_audit`'s own contract is that a trail calling every act "operator"
        # *"would misattribute the one class of write we most need to tell
        # apart later"*. A box provisioning a self-serve customer is exactly
        # that class — the one provisioning act with no human in it — and
        # recording it as staff would make the self-serve flow indistinguishable
        # from an operator's console session forever after.
        #
        # The deployment is recorded by ID in both arms — the operator's label
        # is a name for it, and a name can be re-pointed at a different row —
        # with the operator's own word kept beside it, and the key's PREFIX
        # (never its secret) so the trail names WHICH credential acted.
        # `owner_email` is recorded so the trail names WHO was made owner: a
        # deployment-key create and a hijack attempt are the same act shape
        # (`org.provision` under `actor="deployment"`), and only the owner
        # written tells a real self-serve create from an attempt to co-own a
        # stranger's org (the ownership guard above refuses the latter before it
        # can be audited, but the field is what makes the legitimate writes
        # legible — who owns what, by which credential).
        detail: dict[str, Any] = {
            "slug": req.slug,
            "owner_email": req.owner_email,
            "deployment": req.deployment_label,
            "deployment_id": deployment_id,
        }
        if caller is not None:
            detail["key_prefix"] = caller.key_prefix
        _audit(conn, org_id, "org.provision", detail,
               actor="operator" if caller is None else "deployment")

    return {"organization_id": org_id, "slug": req.slug}


@app.post("/orgs/lifecycle")
def set_lifecycle(req: LifecycleRequest, _: Operator) -> dict[str, Any]:
    """Move an organization through the lifecycle. Transitions only, never sets.

    A free-form status write would let an operator move a customer straight from
    `past_due` to `deleted`, destroying their data without the export window
    §2.1 requires. The graph in :mod:`customer_console.lifecycle` makes that
    unreachable rather than merely discouraged.
    """
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        current = conn.execute(
            text("SELECT status FROM organization WHERE id = :i"), {"i": org_id}
        ).scalar_one()

        try:
            assert_transition(current, req.target)
        except TransitionRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        # Entering `cancelled` opens the export window. Recorded as a date on
        # the row, so "how long do they have" is answerable by anyone rather
        # than living in whoever ran the cancellation's memory.
        export_until = (
            f"now() + interval '{int(req.export_window_days)} days'"
            if req.target == "cancelled" else "NULL"
        )
        conn.execute(
            text(
                f"UPDATE organization SET status = :s, updated_at = now(), "
                f"       export_until = {export_until} "
                f"WHERE id = :i"
            ),
            {"s": req.target, "i": org_id},
        )
        conn.execute(
            text("UPDATE org_subscription SET status = :s, updated_at = now() "
                 "WHERE organization_id = :i AND :s <> 'deleted'"),
            {"s": req.target, "i": org_id},
        )
        _audit(conn, org_id, "org.lifecycle",
               {"from": current, "to": req.target, "reason": req.reason})

        caps = capabilities_of(req.target)

    return {
        "slug": req.org_slug, "from": current, "to": req.target,
        "can_sign_in": caps.can_sign_in, "can_use_ai": caps.can_use_ai,
        "can_write_seats": caps.can_write_seats,
        "data_retained": caps.data_retained,
    }


@app.post("/registry/resolve")
def resolve(req: ResolveRequest, caller: ResolveCaller) -> dict[str, Any]:
    """Resolve a person against the registry at sign-in, consuming a Core seat.

    **This is what makes the seat cap real.** A person cannot become a user of
    an organization without the Customer Console allocating them a seat, because
    the deployment asks before admitting them (D32.4/D32.5).

    **One endpoint, two schemes, two response shapes chosen by the credential**
    (CP-2b clauses 3 and 12). A second endpoint was refused for root
    ``CLAUDE.md`` §5's reason: it would be a second way to do an existing thing.

      * **Operator** — a staff act on a **named** customer. Unchanged, down to
        the response keys; the operator credential has no tenant of its own, so
        naming one in the body is not R11's violation, it is the act itself.
      * **Deployment key** — a box asking about a person it has just
        authenticated. It names **no** org: the org is the ANSWER, not the
        assertion, which is R11 at its strongest available reading — the caller
        makes no tenant claim at all.

    Returns 409 with a buy-more payload when the organization is full — never an
    auto-upgrade, and never a silent admit.
    """
    if caller is not None:
        return _resolve_for_deployment(req, caller)
    return _resolve_for_operator(req)


def _resolve_for_operator(req: ResolveRequest) -> dict[str, Any]:
    """The shipped operator shape, unchanged (CP-2b clause 3's regression)."""
    if req.org_slug is None:
        # Refused HERE rather than by the model. `org_slug` became optional so
        # the deployment arm could refuse it; without this line that relaxation
        # would silently make the operator's own subject optional, and an
        # operator call with no org named is a request with no target.
        raise HTTPException(
            status_code=400,
            detail="org_slug is required under the operator scheme",
        )

    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        state = conn.execute(
            text("SELECT status FROM organization WHERE id = :i"), {"i": org_id}
        ).scalar_one()
        caps = capabilities_of(state)
        if not caps.can_sign_in:
            # Only `deleted` lands here. `suspended` and `cancelled` sign in
            # deliberately: a customer who cannot log in cannot pay you, cannot
            # update a card and cannot export.
            raise HTTPException(
                status_code=403, detail=f"organization is {state}")

        identity_id = store.ensure_identity(
            conn, email=req.email, display_name=req.display_name
        )

        # The SAME allocation path the deployment arm takes — one
        # implementation of "may this person have a Core seat", so the two
        # schemes cannot drift on the answer that costs money. It also carries
        # the advisory lock that makes the cap hold under concurrency.
        #
        # ⚠️ `seats_locked=False` unconditionally, and that is a **recorded
        # pre-existing gap, not a choice made here**: the shipped operator arm
        # allocates seats to a `suspended` organization, which
        # `POST /billing/seats` refuses with 403 (`capabilities_of(state).
        # can_write_seats`). Its current behaviour is pinned by
        # `test_customer_console_lifecycle.py:170` and changing it is a
        # behaviour change to a shipped surface, so CP-2b records it in the
        # spec rather than smuggling it into a refactor. The deployment arm
        # passes the real answer.
        _allocate_core_seat(
            conn, org_id=org_id, identity_id=identity_id, seats_locked=False,
        )

        role = conn.execute(
            text(
                "SELECT role, status FROM org_membership "
                "WHERE organization_id = :org AND user_identity_id = :i"
            ),
            {"org": org_id, "i": identity_id},
        ).first()
        seats = [
            r[0] for r in conn.execute(
                text(
                    "SELECT plan_slug FROM seat_assignment "
                    "WHERE organization_id = :org AND user_identity_id = :i "
                    "AND released_at IS NULL"
                ),
                {"org": org_id, "i": identity_id},
            )
        ]

    return {
        "identity_id": identity_id,
        "organization_id": org_id,
        "role": role[0] if role else "member",
        "status": role[1] if role else "active",
        "seats": seats,
    }


#: The seat outcome vocabulary of the deployment answer (CP-2b clause 12).
#: Three words, named once: a caller branches on them, and a fourth invented at
#: a call site would be a vocabulary nobody agreed to.
_SEAT_ALLOCATED = "allocated"
_SEAT_ALREADY_HELD = "already_held"
_SEAT_NOT_ALLOCATED = "not_allocated"


def _resolve_for_deployment(
    req: ResolveRequest, caller: DeploymentCaller
) -> dict[str, Any]:
    """A box asking about a person it has just authenticated (CP-2b).

    What this answer may carry is bounded to what sign-in needs: org id, slug,
    placement target, lifecycle status, and the seat outcome for the presented
    email. **Never a balance, never a credit figure, never an invoice, and
    never a `role`** — ``org_membership.role`` is registry/billing vocabulary,
    the tenant's permission vocabulary is ``org_role`` plus the ladder
    ``acb_auth/access.py`` resolves, and a second grant vocabulary is forbidden
    by name (D12, root ``CLAUDE.md`` §5).
    """
    if req.org_slug is not None:
        # 400, never ignored. An ignored field is a caller who believes it
        # worked — and what it believes worked here is naming its own tenant.
        raise HTTPException(
            status_code=400,
            detail=(
                "a deployment key may not name an organization; the org is "
                "derived from membership and placement"
            ),
        )

    with get_engine().begin() as conn:
        visible = store.deployment_visible_orgs(
            conn, deployment_id=caller.deployment_id, email=req.email
        )

        # Partitioned by the ONE state machine, never by a local frozenset of
        # state names: `deleted` is the only state with can_sign_in=False, and
        # `suspended`/`cancelled` stay open deliberately (a customer who cannot
        # log in cannot pay you, and `cancelled` IS the export window).
        admissible = [
            o for o in visible if capabilities_of(o["status"]).can_sign_in
        ]
        refused = [
            o for o in visible if not capabilities_of(o["status"]).can_sign_in
        ]

        if not admissible:
            if refused:
                # Named, not hidden. This deployment already serves that
                # customer, so the state reveals nothing it does not have —
                # and it needs the state to refuse correctly. Same shape the
                # operator arm has always returned.
                raise HTTPException(
                    status_code=403,
                    detail=f"organization is {refused[0]['status']}",
                )
            # The three invisible cases — no membership anywhere, membership
            # only on another deployment, no such organization — are ONE
            # answer, byte for byte. A distinguishable negative IS a cross-org
            # existence oracle (clause 5, CP-3's `recorded:false` lesson).
            # In particular: no `identity_id`, which would otherwise say
            # whether this email is known to the Console at all.
            return {"organizations": []}

        identity_id = store.ensure_identity(
            conn, email=req.email, display_name=req.display_name
        )

        seat_outcomes: dict[str, str] = {}
        if len(admissible) == 1:
            org = admissible[0]
            seat_outcomes[org["organization_id"]] = _allocate_core_seat(
                conn, org_id=org["organization_id"], identity_id=identity_id,
                # The lifecycle decides whether a seat may be WRITTEN, and it
                # is a different question from whether this person may sign in
                # — the state machine answers both, and this arm asks both.
                seats_locked=not capabilities_of(org["status"]).can_write_seats,
            )
        else:
            # More than one visible organization → allocate NOTHING. Allocating
            # a seat in every organization a person can see would bill an admin
            # for a login they did not make (clause 9). Choosing among them is
            # the chooser, which is a named non-goal.
            #
            # ⚠️ Allocating nothing is not the same as holding nothing. The
            # seat token answers *does this person hold a seat here*, never
            # *did this call allocate one* — a caller reading `not_allocated`
            # for an org where the person already sits would conclude they are
            # unseated and go buy a seat they already own.
            for org in admissible:
                seat_outcomes[org["organization_id"]] = (
                    _SEAT_ALREADY_HELD
                    if store.has_live_seat(
                        conn, org_id=org["organization_id"],
                        plan_slug=CORE_PLAN_SLUG, identity_id=identity_id,
                    )
                    else _SEAT_NOT_ALLOCATED
                )

        return {
            "identity_id": identity_id,
            "organizations": [
                {
                    "organization_id": o["organization_id"],
                    "slug": o["slug"],
                    "placement": o["placement"],
                    "status": o["status"],
                    "seat": seat_outcomes[o["organization_id"]],
                    "capabilities": _capability_block(o["status"]),
                }
                for o in admissible
            ],
        }


def _capability_block(status: str) -> dict[str, bool]:
    """The three booleans the DEPLOYMENT stores and applies (CP-2b §6(d)).

    **Computed here, where the state machine is, because the deployment must
    not import it.** ``capabilities_of`` lives in this package; a tenant box
    that depended on it would ship the Console's code, and a tenant box that
    re-implemented it would be a second copy of the state machine spelled as an
    ``if``. So the decision is made on this side and only its RESULT crosses
    the wire. A deployment that branches on ``sign_in`` cannot drift, because
    there is nothing to drift from. Fenced from the other side by
    ``tests/unit/test_console_dependency_boundary.py``.

    ⚠️ **In a 200 body ``sign_in`` is ALWAYS true**, and that is a property of
    the caller, not of this function: ``_resolve_for_deployment`` partitions on
    ``can_sign_in`` above, 403s when nothing is admissible, and builds the
    array from ``admissible`` alone. It rides the wire anyway — it is the box's
    durable record and MT-2's future input, and a field that is constant
    *today* because of a filter *upstream* is exactly the field to send
    explicitly rather than leave the reader to infer.

    Three names, not ``OrgCapabilities``' **five**: ``data_retained`` is a
    Console-side retention fact with no deployment behaviour behind it, and
    ``can_pay`` (CP-9) is a *Console-side door*, not a tenant one — a
    deployment has nothing to decide with either, and shipping a field nothing
    reads invites somebody to read it. *(Said "four" until CP-9 appended the
    fifth boolean; a count in a comment goes stale silently, which is why the
    dataclass is the authority and this sentence merely points at it.)*

    Added to the DEPLOYMENT arm only — adding it to the operator arm would
    change a shipped surface for no caller (clause 12).
    """
    caps = capabilities_of(status)
    return {
        "sign_in": caps.can_sign_in,
        "write_seats": caps.can_write_seats,
        "use_ai": caps.can_use_ai,
    }


def _allocate_core_seat(
    conn, *, org_id: str, identity_id: str, seats_locked: bool
) -> str:
    """Consume a Core seat for one person, idempotently. Raises 409 at the cap.

    **The one seat-allocation path both arms of resolve go through** — not two
    copies of the same four calls. Four surfaces recomputing "how many seats
    are free" is how they come to disagree, and the one that disagrees in the
    customer's favour is the one that costs money (``seats.py`` module note).

    Args:
        seats_locked: the organization's lifecycle forbids seat WRITES
            (``capabilities_of(state).can_write_seats`` is False, i.e.
            ``suspended`` or ``cancelled``). Deliberately a **required**
            keyword rather than a default: every caller has to state which
            answer it is choosing, because the version of this function that
            defaulted it to False is the version that allocated new seats to a
            suspended customer on every sign-in.

    Note what ``seats_locked`` does **not** do: it is not sign-in admission.
    ``suspended`` and ``cancelled`` keep ``can_sign_in`` True on purpose (a
    customer who cannot log in cannot pay you, and ``cancelled`` IS the export
    window) while ``can_write_seats`` goes False. So the door stays open, the
    person is told the truth about their seat, and **nothing is written**.
    """
    if seats_locked:
        # Report, never write. A member who already holds a seat keeps it —
        # suspension locks the seat WRITER, it does not repossess seats — and
        # a member who holds none is simply not given one, rather than being
        # refused a login they are entitled to.
        held = store.has_live_seat(
            conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG,
            identity_id=identity_id,
        )
        return _SEAT_ALREADY_HELD if held else _SEAT_NOT_ALLOCATED

    # BEFORE the count, not between the count and the insert — see the
    # function's own note in `store.lock_seat_capacity`. Everything from here
    # to the INSERT is one serialised critical section per (org, plan).
    store.lock_seat_capacity(conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG)

    held = store.has_live_seat(
        conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG, identity_id=identity_id
    )
    grants, assigned = store.seat_rows(
        conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG
    )
    decision = decide_assignment(
        seat_counts(CORE_PLAN_SLUG, grants, assigned),
        already_assigned=held,
        price_inr=store.plan_price(conn, plan_slug=CORE_PLAN_SLUG),
    )
    if not decision.allowed:
        # Byte-compatible with the shipped seats path, because a caller that
        # learned the payload from one scheme must not have to relearn it.
        raise HTTPException(
            status_code=decision.status,
            detail={"reason": decision.reason, "buy_more": decision.buy_more},
        )

    if held:
        return _SEAT_ALREADY_HELD
    store.try_assign_seat(
        conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG,
        identity_id=identity_id, source="core",
    )
    return _SEAT_ALLOCATED


@app.get("/billing/summary")
def billing_summary(org_slug: str, _: Operator) -> dict[str, Any]:
    """Seats and credits for one organization — the console's single read."""
    with get_engine().begin() as conn:
        org_id = _org_id(conn, org_slug)
        seats = _seat_grid(conn, org_id)
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))

    return {
        "organization_id": org_id,
        "seats": seats,
        "credit_balance": str(balance),
    }


@app.post("/billing/seats")
def assign_seat(req: SeatWriteRequest, _: Operator) -> dict[str, Any]:
    """Assign a seat on a plan. 409 at the cap, with a buy-more payload."""
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        state = conn.execute(
            text("SELECT status FROM organization WHERE id = :i"), {"i": org_id}
        ).scalar_one()
        if not capabilities_of(state).can_write_seats:
            raise HTTPException(
                status_code=403,
                detail=f"organization is {state}; seats are locked")

        identity_id = store.ensure_identity(conn, email=req.email)
        held = store.has_live_seat(
            conn, org_id=org_id, plan_slug=req.plan_slug, identity_id=identity_id
        )
        grants, assigned = store.seat_rows(
            conn, org_id=org_id, plan_slug=req.plan_slug
        )
        decision = decide_assignment(
            seat_counts(req.plan_slug, grants, assigned),
            already_assigned=held,
            price_inr=store.plan_price(conn, plan_slug=req.plan_slug),
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=decision.status,
                detail={"reason": decision.reason, "buy_more": decision.buy_more},
            )
        store.try_assign_seat(
            conn, org_id=org_id, plan_slug=req.plan_slug,
            identity_id=identity_id, source=req.source,
        )
        _audit(conn, org_id, "seat.assign",
               {"email": req.email, "plan": req.plan_slug})

    return {"assigned": True, "plan_slug": req.plan_slug}


@app.post("/billing/seats/release")
def release_seat(req: SeatWriteRequest, _: Operator) -> dict[str, Any]:
    """Release a seat. Frees capacity immediately (D19.3)."""
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        identity_id = store.ensure_identity(conn, email=req.email)
        released = store.release_seat(
            conn, org_id=org_id, plan_slug=req.plan_slug, identity_id=identity_id
        )
        _audit(conn, org_id, "seat.release",
               {"email": req.email, "plan": req.plan_slug, "released": released})

    return {"released": released}


@app.post("/credits/grant")
def grant_credits(req: CreditGrantRequest, _: Operator) -> dict[str, Any]:
    """Add credits. Append-only — a correction is another row, never an edit."""
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        store.add_credit(
            conn, org_id=org_id, delta=req.credits,
            reason=req.reason, ref=req.ref,
        )
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))
        _audit(conn, org_id, "credits.grant",
               {"delta": str(req.credits), "reason": req.reason})

    return {"balance": str(balance)}


@app.get("/credits/balance")
def credit_balance(org_slug: str, _: Operator) -> dict[str, Any]:
    with get_engine().begin() as conn:
        org_id = _org_id(conn, org_slug)
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))
        status = conn.execute(
            text("SELECT status FROM organization WHERE id = :i"), {"i": org_id}
        ).scalar_one()

    decision = decide_spend(
        balance, Decimal(0),
        policy=OverdraftPolicy(),
        is_trial=(status == "trial"),
    )
    return {
        "balance": str(balance),
        "in_overdraft": decision.in_overdraft,
        "org_status": status,
    }


@app.post("/keys")
def issue_key(req: IssueKeyRequest, _: Operator) -> dict[str, Any]:
    """Mint an organization key. **The token is returned exactly once.**

    Only the hash is stored, so this response is the only moment the secret
    exists anywhere. It is not recoverable — a lost key is replaced, not looked
    up, and that is the property that makes a database disclosure survivable.
    """
    minted = mint_key()
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        store.issue_key(
            conn, org_id=org_id, prefix=minted.prefix,
            key_hash=minted.key_hash, label=req.label, created_by="operator",
        )
        # The audit row records the PREFIX, never the token.
        _audit(conn, org_id, "key.issue",
               {"prefix": minted.prefix, "label": req.label})

    return {"prefix": minted.prefix, "token": minted.token}


@app.post("/keys/revoke")
def revoke_key(req: RevokeKeyRequest, _: Operator) -> dict[str, Any]:
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        revoked = store.revoke_key(conn, org_id=org_id, prefix=req.prefix)
        _audit(conn, org_id, "key.revoke",
               {"prefix": req.prefix, "revoked": revoked})
    return {"revoked": revoked}


@app.get("/keys")
def list_keys(org_slug: str, _: Operator) -> dict[str, Any]:
    """Key metadata for one org. Never returns a hash or a token."""
    with get_engine().begin() as conn:
        org_id = _org_id(conn, org_slug)
        return {"keys": store.list_keys(conn, org_id=org_id)}


@app.get("/me")
def whoami(caller: KeyCaller) -> dict[str, Any]:
    """Who this key belongs to, and what it has left. **Read-only.**

    This is the whole of the organization key's surface on the Customer Console.
    It resolves the tenant from the credential (CP-3's actual point) and reports
    the balance the caller needs to render an out-of-credits state — without
    handing the metered party any way to move its own ledger.
    """
    org_id = caller.organization_id
    with get_engine().begin() as conn:
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))
        status = conn.execute(
            text("SELECT slug, status FROM organization WHERE id = :i"),
            {"i": org_id},
        ).first()

    return {
        "organization_id": org_id,
        "slug": status[0],
        "status": status[1],
        "credit_balance": str(balance),
        "key_prefix": caller.key_prefix,
    }


@app.post("/v1/chat/completions")
def chat_completions(req: CompletionRequest, caller: KeyCaller) -> Any:
    """Proxy one completion, gate it, and charge it.

    The organization comes from the API key, the model comes from the tier
    binding, and the usage row is written here — on our infrastructure, from
    numbers we observed — rather than reported by the party being metered.

    **CP-6 added two refusals and one charge.** Before the provider call, the
    balance gate (402 with a top-up payload) and the per-run circuit breaker
    (403) may refuse; after it, the completion is rated against the rate card in
    force and the cost is drawn from the ledger. The refusals are behind
    ``CUSTOMER_CONSOLE_SPEND_GATE`` and ship OFF; the charge is always computed
    and is **zero until the rate card is priced**, which is the owner's
    commercial act (§8). The order is the point: a gate after the provider call
    would refuse a request we had already paid for.

    Declared ``def``, not ``async def``: the engine is synchronous, so an async
    route would block the event loop for two round trips on what is by design
    the highest-QPS endpoint on the plane. CP-4 shipped it async and contradicted
    both this module's and ``db.py``'s own docstrings; the provider call is
    driven through ``asyncio.run`` inside the threadpool worker instead.
    """
    org_id = caller.organization_id

    if req.stream:
        # 501, not a 500. CP-4 forwarded `stream` and returned litellm's
        # CustomStreamWrapper to FastAPI, which failed to serialise it — the
        # client got "Internal Server Error" AND a phantom zero-token usage row
        # was committed for a completion nobody received. Refusing explicitly is
        # honest; the streaming path is CP-4b and is the half that matters most,
        # since every agent runtime streams through this choke point.
        raise HTTPException(
            status_code=501,
            detail="streaming is not implemented on the Router yet (CP-4b)",
        )

    with get_engine().begin() as conn:
        try:
            resolved = resolve_tier(conn, req.model)
        except TierUnknown:
            # 400, not a silent coercion to a default. A misconfigured agent
            # must be visible rather than quietly billed (D32.7).
            raise HTTPException(
                status_code=400,
                detail=f"unknown tier {req.model!r}; name a tier, not a model",
            )
        provider = resolved.model.split("/", 1)[0]
        try:
            credential = provider_credential(conn, provider=provider, org_id=org_id)
        except Exception:
            # A missing or rotated encryption key must fail CLOSED with the same
            # 503 shape the other secrets use — not a 500 that reads as a bug.
            _log.exception("router.credential_unavailable")
            raise HTTPException(
                status_code=503, detail="provider credentials unavailable")

        # CP-6. BEFORE the provider call, which is the only place a refusal is
        # worth anything: after it we have already spent the money. Metering
        # afterwards stays best-effort and never fails a completion — the GATE
        # may refuse, the METER may not.
        refusal = _spend_refusal(conn, caller) if _spend_gate_enabled() else None

    if refusal is not None:
        raise refusal

    if credential is None:
        raise HTTPException(
            status_code=503,
            detail=f"no provider credential configured for {provider!r}",
        )
    secret, api_base = credential

    # ALLOWLIST. Only named parameters reach the provider, and the ones that
    # multiply our cost are clamped. `api_base` is ours alone — a caller can
    # neither set it nor see it.
    passthrough = {
        k: v for k, v in req.model_dump(exclude_none=True).items()
        if k in _FORWARDABLE
    }
    requested_max = passthrough.get("max_tokens")
    passthrough["max_tokens"] = min(
        int(requested_max) if requested_max else _MAX_OUTPUT_TOKENS,
        _MAX_OUTPUT_TOKENS,
    )
    call_kwargs: dict[str, Any] = {
        **passthrough,
        "model": resolved.model,
        "api_key": secret,
        # Bounded explicitly rather than left to litellm's defaults, so one
        # request cannot become fifty provider calls.
        "num_retries": 1,
        "timeout": 120,
    }
    if api_base:
        call_kwargs["api_base"] = api_base

    try:
        response = asyncio.run(call_provider(**call_kwargs))
    except HTTPException:
        raise
    except Exception as exc:
        # Map upstream failures to something a caller can branch on. Without
        # this a provider 429 is indistinguishable from a Router bug and every
        # client treats both as fatal (or both as retryable). The message is
        # deliberately NOT echoed: it can carry the request, and the request can
        # carry customer content.
        status = getattr(exc, "status_code", None)
        _log.warning("router.provider_error", extra={"upstream_status": status})
        if isinstance(status, int) and 400 <= status < 600:
            raise HTTPException(
                status_code=502 if status >= 500 else status,
                detail="upstream provider error",
            )
        raise HTTPException(status_code=502, detail="upstream provider error")

    # Metering is best-effort and NEVER fails the call: an unmetered completion
    # is a revenue problem, a failed completion is a product problem, and the
    # product problem is worse. It runs only AFTER a successful provider call,
    # so a failed request can no longer leave a phantom row behind.
    try:
        usage = usage_from_response(response)
        with get_engine().begin() as conn:
            # CP-6: the draw. `record_usage` negates this into `credit_ledger`
            # in the SAME transaction as the usage row, so a retried write that
            # inserts nothing also charges nothing. Zero while the card is
            # unpriced, which is the shipped state until the owner prices it.
            billed = _rate_completion(conn, model=resolved.model, usage=usage)
            store.record_usage(
                conn, org_id=org_id,
                # SERVER-generated. The caller's id is correlation only — see
                # migration 005 and CompletionRequest.client_ref.
                request_id=f"rtr-{uuid.uuid4().hex}",
                client_ref=req.client_ref,
                billed_credits=billed,
                user_email=caller.member, agent=caller.agent,
                module_slug=caller.module_slug, run_id=caller.run_id,
                model=resolved.model, tier=resolved.tier,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cached_tokens=usage.cached_tokens,
            )
    except Exception:
        _log.exception("router.metering_failed")

    return response


@app.get("/me/billing")
def my_billing(caller: KeyCaller) -> dict[str, Any]:
    """The calling organization's OWN billing summary. Read-only.

    Exists because the customer's billing page needs this data and the
    workbench must **not** hold the operator token — a tenant deployment
    holding a cross-organization credential is the whole thing D32/D35 are
    arranged to avoid. So the deployment presents its own key, and the key
    resolves the organization (CP-3). It can read its own billing and nothing
    else, by construction rather than by a `WHERE` clause somebody has to
    remember.

    Balance is `SUM(credit_ledger)` computed here, once. The browser renders it
    and never recomputes it.
    """
    org_id = caller.organization_id
    with get_engine().begin() as conn:
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))

        # Burn over a NAMED window, so the UI can say what it is measuring.
        # A figure whose window is unstated reads as authoritative and is not.
        window_days = 30
        burn = conn.execute(
            text(
                "SELECT COALESCE(SUM(billed_credits), 0) FROM usage_event "
                "WHERE organization_id = :o "
                "  AND created_at >= now() - make_interval(days => :d)"
            ),
            {"o": org_id, "d": window_days},
        ).scalar_one()

        # BYOK: metered, never charged for tokens (§3.4). Presence of the org's
        # own provider credential is what makes it true — not a flag somebody
        # sets separately and forgets to clear.
        is_byok = bool(conn.execute(
            text("SELECT 1 FROM provider_credential "
                 "WHERE organization_id = :o AND revoked_at IS NULL LIMIT 1"),
            {"o": org_id},
        ).first())

    return {
        "credits": {
            "balanceCredits": float(balance),
            "burnThisCycle": float(burn),
            "windowDays": window_days,
            "isByok": is_byok,
        },
        # SC-5 issues real documents; until an invoice exists there is nothing
        # to list, and an empty list is the honest answer rather than a stub.
        "invoices": [],
        # Self-serve checkout is SC-4a and deliberately sequenced after
        # metering (D37.1). The page renders a contact prompt, not a dead
        # button, while this is false.
        "purchaseEnabled": False,
    }


# ══ CP-9 · the checkout: orders, redemption, and the money guard ════════════
#
# **The auth answer, in one paragraph, because it is the question the audit
# stopped on.** There is NO fifth scheme. The organization key — read-only for
# every route that shipped before this one — gains exactly two writes, chosen
# because neither can move value by itself: creating a pending intent, and
# PRESENTING a discount code somebody holding `Operator` issued. Value moves on
# exactly two events, both carrying an authority the caller does not hold: a
# provider webhook whose signature verifies, or the redemption of a valid
# operator-issued code. The reads below (§9.3a) sit on a credential that
# already reads, so they mint nothing.

#: How long an unpaid order stays open. `abandoned` is written by EXPIRY, never
#: by the customer (§9.2) — and expiry is observed at the next write that
#: touches the order, because the operator-facing sweep belongs with CP-8's
#: console rather than with a scheduler that has nowhere to report.
_ORDER_TTL_MINUTES = 30

#: A NAMED page size, never an unbounded `SELECT *` on a table a customer can
#: grow without limit (9.3(6)'s named residual is exactly that).
_ORDER_PAGE_SIZE = 50

#: ONE refusal shape for "that order belongs to another organization" and "no
#: such order" — same status, same body bytes, naming nothing (§9.3(7)). A
#: 403-for-foreign / 404-for-unknown split is a membership oracle over other
#: tenants' order ids. Contrast `_org_id`'s 404, which names the slug on
#: purpose: the operator is cross-org BY DESIGN, and that is the contrast, not
#: the precedent.
_NO_SUCH_ORDER = "no such order"

#: The collapsed half of SC-4g done-when 4's partition: {unknown, wrong-org}.
#: A distinguishable "wrong org" answer confirms that a code exists and belongs
#: to somebody, which is a membership test over other tenants' data run from a
#: customer's own key.
_NO_SUCH_CODE = "no such discount code"


def _no_such_order() -> HTTPException:
    return HTTPException(status_code=404, detail=_NO_SUCH_ORDER)


def _no_such_code() -> HTTPException:
    return HTTPException(status_code=404, detail=_NO_SUCH_CODE)


@app.get("/billing/catalog")
def billing_catalog(_: PayingCaller) -> CatalogView:
    """The priced ladder a customer may buy from (§6 item (f)).

    **Why ``can_pay`` and not ``can_use_ai``**: this is the read a customer
    makes on the way to paying us, so gating it on the AI door would shut it on
    exactly the ``suspended`` organization who most needs it — §9.3(5)'s
    measured defect, one route along. A ``deleted`` organization is refused,
    like everywhere else.

    **The caller is authenticated and then deliberately unused.** The catalog
    is the same for every customer, so binding it to ``_`` is the structural
    statement that no per-org answer is computable here: there is no
    organization id in scope to compute one from. Per-org pricing is MT-2 /
    SC-1a's and neither is built.

    Rupees become paise through ``payments.paise`` — the ONE conversion (§9.2),
    the same call ``_priced_basket`` makes, so what the ladder quotes and what
    an order charges cannot drift into two denominations.
    """
    with get_engine().begin() as conn:
        return CatalogView(plans=[
            CatalogPlanView(
                slug=plan["slug"],
                name=plan["name"],
                kind=plan["kind"],
                price_paise=payments.paise(plan["price_inr"]),
                sort_order=plan["sort_order"],
            )
            for plan in store.active_plans(conn)
        ])


@app.get("/me/seats")
def my_seats(caller: PayingCaller) -> SeatsView:
    """The calling organization's OWN seats, per plan (§6 item (g)).

    The sibling of ``GET /billing/catalog`` and the same move: a customer-key read
    on the ``can_pay`` door. **Why ``can_pay`` and not the Operator door or
    ``KeyCaller``**: a ``suspended`` organization is the one deciding whether to
    buy more seats, so it must be able to SEE them — the §9.3(5) reasoning item
    (f) records one route along — and a ``deleted`` one is refused like
    everywhere else. ``GET /billing/summary`` computes these same numbers but is
    the **Operator**, cross-org door and takes an ``org_slug`` a customer must
    never name; ``GET /me/billing`` carries no seats at all. So the customer's own
    seat grid (``subscription_console.md`` SC-1a) had no data source until this.

    **The organization is a property of the credential** — ``caller.
    organization_id``, never an ``org_slug`` on the wire (R11) — so org A can
    never read org B's seats, by construction rather than by a ``WHERE`` clause a
    reader has to remember, exactly as ``my_billing`` relies on one route up.

    The four counts come from the ONE seat vocabulary: ``store.seat_rows`` folded
    through ``seat_counts`` (§3.3, D32.5), the SAME computation ``billing_summary``
    runs — literally the one shared ``_seat_grid`` loop, with the org id taken from
    the credential instead of ``_org_id(conn, org_slug)``. There is no second SQL
    and no recompute: ``available``'s zero-clamp and ``oversubscribed`` are
    ``seat_counts``'s, surfaced not reimplemented, and the frontend renders them
    verbatim. A plan the org never touched is skipped exactly as
    ``billing_summary`` skips it, not emitted as a zero row.
    """
    with get_engine().begin() as conn:
        return SeatsView(plans=_seat_grid(conn, caller.organization_id))


def _order_view(conn, order: dict[str, Any], *, with_lines: bool) -> OrderView:
    """Shape one order for the customer's wire. Provider ids never board it."""
    discount = store.redemption_for_order(conn, order_id=order["id"])
    return OrderView(
        id=order["id"],
        status=order["status"],
        provider=order["provider"],
        gross_paise=order["gross_paise"],
        discount_paise=order["discount_paise"],
        taxable_paise=order["taxable_paise"],
        gst_paise=order["gst_paise"],
        total_paise=order["total_paise"],
        gst_split=order["gst_split"],
        expires_at=order["expires_at"],
        created_at=order["created_at"],
        terminal_at=order["terminal_at"],
        lines=(
            [OrderLineView(**line) for line in _lines_for(conn, order["id"])]
            if with_lines else None
        ),
        discount=(
            OrderDiscountView(
                code_prefix=discount["code_prefix"],
                discount_paise=discount["discount_paise"],
            ) if discount else None
        ),
    )


def _lines_for(conn, order_id: str) -> list[dict[str, Any]]:
    return [
        {"plan_slug": line["plan_slug"], "quantity": line["quantity"],
         "unit_price_paise": line["unit_price_paise"]}
        for line in store.order_lines(conn, order_id=order_id)
    ]


def _priced_basket(conn, lines: list[OrderLineRequest]) -> tuple[list, int]:
    """Price a basket from the CATALOG. 400 for anything it will not sell.

    An order line whose ``plan_slug`` is not an **active** catalog row is
    refused, so the checkout cannot sell a thing the catalog does not price
    (§9.1) — `rnd` and `support` are seeded INACTIVE precisely because their
    Centers are not registered yet.
    """
    priced: list[dict[str, Any]] = []
    gross = 0
    for line in lines:
        plan = store.priced_plan(conn, plan_slug=line.plan_slug)
        if plan is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{line.plan_slug!r} is not an active plan; the checkout "
                    "sells only priced, active catalog rows"
                ),
            )
        unit = payments.paise(plan["price_inr"])
        gross += unit * line.quantity
        priced.append({
            "plan_slug": plan["slug"], "quantity": line.quantity,
            "unit_price_paise": unit,
        })
    return priced, gross


@app.post("/billing/orders")
def create_order(req: CreateOrderRequest, caller: PayingCaller) -> OrderView:
    """Create a pending intent under the CUSTOMER's own key (§9.3(2)).

    **It writes ``payment_order`` and ``payment_order_line`` and nothing else.**
    No entitlement, no seat, no ledger row, no subscription change — which is
    what makes a customer-authenticated write safe on a service whose customer
    credential is read-only by design (CP-3's lesson). Done-when 3 asserts it
    by snapshotting all four tables around this call.

    Gated on ``can_pay``, not ``can_use_ai``: a **suspended** organization must
    be able to buy its way out, and `auth.organization_from_key` shuts the door
    on exactly that customer (§9.3(5)).

    With no Razorpay credentials this is **503 naming the missing variables**
    (done-when 10). Refusing early is honest — an order nobody could ever pay
    is not an order — and it is half of how CP-9 ships dark, the other half
    being ``purchaseEnabled: False`` on the surface the workbench renders.

    ⚠️ **The provider order is created HERE, for the amount owed at this
    moment**, and any later redemption that changes the amount replaces it
    (see :func:`redeem_discount_code`). A provider order created once and then
    discounted would collect the pre-discount amount — the customer would be
    overcharged and the capture would fail our own amount check.
    """
    try:
        provider = payments.provider()
    except payments.ProviderUnconfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    org_id = caller.organization_id
    with get_engine().begin() as conn:
        priced, gross = _priced_basket(conn, req.lines)
        billing = conn.execute(
            text("SELECT gstin, billing_state FROM organization WHERE id = :i"),
            {"i": org_id},
        ).first()
        gst_split = payments.gst_split_for(billing[1] if billing else None)
        gst = payments.gst_for(gross)
        total = gross + gst
        if total == 0:
            # An order that collects nothing is not a purchase. The Rs 0 path
            # is reached by REDEEMING a code against a priced order, never by
            # ordering only free rows (`company` is Rs 0 and is not sold).
            raise HTTPException(
                status_code=400,
                detail="an order with nothing to pay is not a purchase",
            )

        order_id = store.create_order(
            conn, org_id=org_id, provider="razorpay",
            gross_paise=gross, discount_paise=0, taxable_paise=gross,
            gst_paise=gst, total_paise=total, gst_split=gst_split,
            customer_gstin=billing[0] if billing else None,
            place_of_supply=billing[1] if billing else None,
            expires_in_minutes=_ORDER_TTL_MINUTES, lines=priced,
        )
        # Inside the transaction on purpose: if the provider refuses, the local
        # order rolls back with it. The orphan this can leave is the harmless
        # direction — a provider order nobody pays, which expires there — and
        # never the dangerous one, a local order the customer cannot pay.
        created = provider.create_order(
            amount_paise=total, receipt=order_id,
            notes={"organization_id": org_id},
        )
        store.set_provider_order_id(
            conn, order_id=order_id,
            provider_order_id=created.provider_order_id,
        )
        _audit(conn, org_id, "order.create",
               {"order_id": order_id, "total_paise": total,
                "lines": [line["plan_slug"] for line in priced]},
               actor="organization")
        order = store.order_for_update(conn, order_id=order_id, org_id=org_id)
        assert order is not None
        return _order_view(conn, order, with_lines=True)


@app.get("/billing/orders/{order_id}")
def read_order(order_id: str, caller: PayingCaller) -> OrderView:
    """One order — **own org only** (§9.3a, §9.3(7)'s predicate).

    The organization comes from the key's resolution, never from the path, the
    body or a header (R11). A foreign order and an unknown one answer one
    byte-identical 404.
    """
    with get_engine().begin() as conn:
        order = store.order_row(
            conn, order_id=_valid_uuid(order_id),
            org_id=caller.organization_id,
        )
        if order is None:
            raise _no_such_order()
        return _order_view(conn, order, with_lines=True)


@app.get("/billing/orders")
def list_orders(
    caller: PayingCaller, status: str | None = None, cursor: str | None = None,
) -> OrderPageView:
    """That organization's orders, newest first (§9.3a).

    ``status`` is validated against the state machine's own set — an unknown
    value is **400, not silently ignored**, because a filter that quietly
    matches everything reads as "there are no failed orders".
    """
    if status is not None and status not in payments.ORDER_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown status {status!r}; "
                   f"expected one of {sorted(payments.ORDER_STATES)}",
        )
    with get_engine().begin() as conn:
        rows = store.orders_page(
            conn, org_id=caller.organization_id, limit=_ORDER_PAGE_SIZE,
            status=status,
            cursor=_valid_uuid(cursor) if cursor else None,
        )
        return OrderPageView(
            orders=[_order_view(conn, r, with_lines=False) for r in rows],
            next=rows[-1]["id"] if len(rows) == _ORDER_PAGE_SIZE else None,
        )


def _valid_uuid(value: str) -> str:
    """A malformed id is *"no such order"*, never a 500 from the driver.

    Postgres rejects a non-UUID literal for a UUID column with an error the
    driver raises, which would surface as a 500 and — worse — would tell a
    prober that "malformed" and "not yours" are different answers.
    """
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise _no_such_order() from None


@app.post("/billing/orders/{order_id}/redeem")
def redeem_discount_code(
    order_id: str, req: RedeemRequest, caller: PayingCaller,
) -> OrderView:
    """Present a discount code against one's own order (SC-4g, §9.3(2)).

    **This route grants — and it is the only org-key route that may.** It is
    the one allow-listed edge in
    ``test_no_org_key_route_writes_an_entitlement_or_ledger_row``, written as
    the PAIR ``("redeem_discount_code", "payments.fulfil")`` rather than as a
    sentence like *"redeem may write seats"*: a sentence is a general licence,
    a pair is not. What makes it safe is that **the code is the
    pre-authorization** — the customer cannot mint one; somebody holding
    ``Operator`` issued it. The org key still cannot move value on its own
    authority.

    The refusals PARTITION (done-when 4):

    * ``expired`` / ``revoked`` / ``exhausted`` — three DISTINCT reasons, given
      only for a code this organization is entitled to see at all. The caller
      has already proven possession of the secret, so naming *why* it failed
      tells them nothing they could not learn by asking us — and an admin told
      "this code expired on the 3rd" does not file a support ticket.
    * ``unknown`` / ``wrong-org`` — ONE indistinguishable shape, because a
      distinguishable "wrong org" confirms a code exists and belongs to
      somebody: a membership test over other tenants' data, run from a
      customer's own key.

    Idempotent: re-presenting the same code against the same order redeems once
    (``UNIQUE (discount_code_id, order_id)``), and a concurrent double-redeem of
    a ``max_redemptions = 1`` code yields one success and one refusal under
    ``store.lock_discount_capacity``.
    """
    org_id = caller.organization_id
    order_id = _valid_uuid(order_id)

    # Expiry runs in its OWN transaction, ahead of the work: a refusal below
    # rolls its transaction back, and an `abandoned` written in that
    # transaction would roll back with it — leaving an order that reports
    # itself open forever while refusing every attempt.
    with get_engine().begin() as conn:
        stale = store.order_for_update(conn, order_id=order_id, org_id=org_id)
        if stale is not None:
            payments.abandon_if_expired(conn, order=stale)

    parsed = split_key(req.code.strip())

    with get_engine().begin() as conn:
        order = store.order_for_update(conn, order_id=order_id, org_id=org_id)
        if order is None:
            raise _no_such_order()
        if order["status"] in payments.ORDER_TERMINAL_STATES:
            # Not one of the five: this is a statement about the ORDER, not
            # about the code. Naming it separately is what lets a page say
            # "this order has expired, start again" rather than "bad code".
            raise HTTPException(
                status_code=409,
                detail={"reason": "order_not_open", "status": order["status"]},
            )

        # ⚠️ **Above the verification, never below it** (moved 2026-08-19,
        # review P2-1). `_verified_code` raises on all four refusal shapes, so
        # below it this line recorded only the attempts that SUCCEEDED — the
        # measured rate its docstring promises was zero under exactly the
        # traffic a rate limiter would be sized against.
        _log_redeem_attempt(order_id, parsed)
        code = _verified_code(conn, parsed=parsed, org_id=org_id)

        # BEFORE the count, never between the count and the insert — the seat
        # cap's lesson, one table along (`store.lock_seat_capacity`).
        store.lock_discount_capacity(conn, code_id=code["id"])

        existing = store.redemption_for_order(conn, order_id=order_id)
        if existing is not None:
            if existing["code_prefix"] != code["prefix"]:
                # Stacking discounts is a commercial decision nobody has taken,
                # so it is refused rather than invented here.
                raise HTTPException(
                    status_code=409,
                    detail={"reason": "already_discounted"},
                )
            return _order_view(conn, order, with_lines=True)

        if code["expired"]:
            raise HTTPException(409, detail={"reason": "expired"})
        if code["revoked"]:
            raise HTTPException(409, detail={"reason": "revoked"})
        if store.count_redemptions(conn, code_id=code["id"]) >= code[
                "max_redemptions"]:
            raise HTTPException(409, detail={"reason": "exhausted"})

        return _apply_redemption(conn, order=order, code=code, org_id=org_id)


def _verified_code(conn, *, parsed, org_id: str) -> dict[str, Any]:
    """Resolve a presented code, or raise the ONE collapsed refusal.

    Three cases collapse into one shape here — malformed, unknown prefix, wrong
    secret — plus the fourth, a code bound to a different organization. What
    the caller learns is identical in all four: nothing.
    """
    if parsed is None or not is_discount_code(parsed[0]):
        raise _no_such_code()
    prefix, secret = parsed
    code = store.resolve_discount_code(conn, prefix=prefix)
    if code is None:
        # Verify against a dummy hash so both paths do the same work — the
        # idiom `organization_from_key` uses, and with the same caveat: it is
        # cheap defence in depth, not a proven constant-time property.
        verify_secret(secret, "0" * 64)
        raise _no_such_code()
    if not verify_secret(secret, code["code_hash"]):
        raise _no_such_code()
    if code["organization_id"] not in (None, org_id):
        raise _no_such_code()
    return code


def _log_redeem_attempt(order_id: str, parsed) -> None:
    """Log the PREFIX only — never the secret (done-when 17).

    Rate limiting on redemption is deliberately deferred (9.3(6)): a redeem
    attempt is a guess at a 256-bit bearer secret answered by an
    indistinguishable refusal, so it is neither an oracle nor a feasible
    search. What would change that call is a MEASURED attempt rate, which is
    exactly what this line is for.

    ⚠️ **Therefore it must run BEFORE the code is verified**, and it did not
    until 2026-08-19: every failing attempt — malformed, unknown prefix, wrong
    secret, wrong organization — raised out of :func:`_verified_code` first, so
    the only attempts counted were the ones that worked. Fence:
    ``test_a_failing_redeem_attempt_is_logged_and_carries_no_secret``.
    """
    _log.info(
        "payments.redeem_attempt",
        extra={"order": order_id, "code_prefix": parsed[0] if parsed else None},
    )


def _apply_redemption(
    conn, *, order: dict[str, Any], code: dict[str, Any], org_id: str,
) -> OrderView:
    """Recompute the order's money, record the redemption, and — at zero — grant.

    GST is recomputed on the DISCOUNTED base (SC-4g (iii)): that is standard
    invoice practice and the only reading under which 100 percent off yields
    taxable 0 -> GST 0 -> total 0, which is what D42 requires. Discount after
    tax would leave GST payable on a zero-rupee sale.
    """
    discount = payments.discount_for(
        gross_paise=order["gross_paise"], kind=code["kind"],
        percent_bp=code["percent_bp"], amount_paise=code["amount_paise"],
    )
    taxable = order["gross_paise"] - discount
    gst = payments.gst_for(taxable)
    total = taxable + gst

    redemption_id = store.write_redemption(
        conn, code_id=code["id"], org_id=org_id, order_id=order["id"],
        gross_paise=order["gross_paise"], discount_paise=discount,
        net_paise=total,
    )
    if redemption_id is None:
        # Lost a race with an identical submission; the winner's row stands and
        # this call is the idempotent no-op done-when 5 requires.
        return _order_view(conn, order, with_lines=True)

    store.apply_discount_to_order(
        conn, order_id=order["id"], discount_paise=discount,
        taxable_paise=taxable, gst_paise=gst, total_paise=total,
    )
    _audit(conn, org_id, "discount.redeem",
           {"order_id": order["id"], "code_prefix": code["prefix"],
            "discount_paise": discount, "net_paise": total},
           actor="organization")

    if total == 0:
        # The Rs 0 path: `provider='none'`, no provider identifier, and ZERO
        # provider calls in this request. The order created earlier left an
        # unpaid provider order behind, which expires there — the harmless
        # orphan, named rather than discovered (SC-4g (iv)).
        store.detach_provider(conn, order_id=order["id"])
        payments.fulfil(
            conn, order_id=order["id"],
            reference=f"redemption:{redemption_id}",
        )
    else:
        # A PARTIAL code routes the remainder through the provider path — one
        # order, discount recorded, fulfilment on capture, no second flow. The
        # provider order is REPLACED because its amount is now wrong, and a
        # provider order that collects the pre-discount amount is a customer
        # overcharged by us.
        _replace_provider_order(conn, order_id=order["id"], total_paise=total,
                                org_id=org_id)

    fresh = store.order_row(conn, order_id=order["id"], org_id=org_id)
    assert fresh is not None
    return _order_view(conn, fresh, with_lines=True)


def _replace_provider_order(
    conn, *, order_id: str, total_paise: int, org_id: str
) -> None:
    """Re-create the provider order for the discounted amount."""
    try:
        provider = payments.provider()
    except payments.ProviderUnconfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    created = provider.create_order(
        amount_paise=total_paise, receipt=order_id,
        notes={"organization_id": org_id},
    )
    store.set_provider_order_id(
        conn, order_id=order_id, provider_order_id=created.provider_order_id,
    )


@app.post("/billing/webhooks/razorpay")
def razorpay_webhook(event: SignedWebhook) -> dict[str, Any]:
    """Capture, verified first and idempotent second (§9.5).

    The signature check is not in this body — it is
    :func:`auth.razorpay_webhook_event`, an **authenticating dependency**, so
    the route is covered by the same fence that covers every other door on this
    service and a refactor cannot quietly drop it.

    **Two guards, and they are NOT the same guard:**

    1. ``payment_event.provider_event_id`` (PRIMARY KEY) is **transport**
       dedup. It makes the same delivery, delivered twice, a no-op. That is the
       retry case and nothing more.
    2. The **terminal-state rule** is the **money** guard. Razorpay sends
       DIFFERENT event ids for one capture (``payment.captured`` and
       ``order.paid``), so the primary key never sees them as duplicates. What
       makes the second harmless is that ``captured`` is terminal:
       :func:`payments.fulfil` refuses, this route logs at info and answers
       200. Both events are recorded; exactly one fulfils.

       ⚠️ **That no-op is scoped to `captured` and to nothing else** (repaired
       2026-08-19). A refusal against any *other* terminal state means a
       verified payment arrived and nothing was granted — an ERROR, not an
       info line; see :func:`_handle_webhook_event`.

    **A failed payment ATTEMPT does not close the ORDER.** One provider order
    accepts many attempts until one captures, so ``payment.failed`` is recorded
    and logged and the order stays open for the retry. Order-level failure is
    ``abandoned``, written by the clock (§9.2).

    A capture whose amount disagrees with ``total_paise`` is **refused and
    alerted**, never fulfilled: an amount mismatch is a bug or an attack, and
    it must not be resolved in the customer's favour silently. The refusal
    rolls its own receipt back deliberately, so a corrected re-delivery is
    evaluated afresh instead of being deduped into silence.
    """
    with get_engine().begin() as conn:
        order = (
            store.order_by_provider_id(
                conn, provider_order_id=event.provider_order_id)
            if event.provider_order_id else None
        )
        fresh = store.record_payment_event(
            conn, provider_event_id=event.event_id,
            order_id=order["id"] if order else None,
            kind=event.kind, body=json.dumps(event.body),
        )
        if not fresh:
            _log.info("payments.webhook_duplicate",
                      extra={"event": event.event_id})
            return {"recorded": False, "fulfilled": False}
        if order is None:
            # Recorded, not acted on, and 200 so the provider stops retrying a
            # delivery we will never be able to resolve.
            #
            # ⚠️ **ERROR, not warning** (2026-08-18, verification finding F2).
            # This arm is not only the benign "an event we never issued an
            # order for" case. A `payment.captured` whose `order_id` matches no
            # row here is **a customer charged with nothing granted** — and our
            # 200 is precisely what makes the provider stop retrying it. The
            # receipt row is kept with `order_id` NULL as the sole record that
            # the money arrived, and CP-8's reconciliation OWNS those rows.
            # The amount and both provider identifiers ride in the structured
            # fields so the payment can be found at the provider from the log
            # line alone. Fenced by
            # `test_a_capture_with_no_matching_order_is_kept_and_alerted_at_error`.
            _log.error(
                "payments.webhook_unknown_order",
                extra={"event": event.event_id, "event_kind": event.kind,
                       "provider_order_id": event.provider_order_id,
                       "provider_payment_id": event.provider_payment_id,
                       "amount_paise": event.amount_paise},
            )
            return {"recorded": True, "fulfilled": False}

        return _handle_webhook_event(conn, event=event, order=order)


def _handle_webhook_event(
    conn, *, event: payments.WebhookEvent, order: dict[str, Any]
) -> dict[str, Any]:
    """Decide what one verified, freshly recorded event does to its order."""
    if event.kind in _ATTEMPT_FAILURE_EVENTS:
        # ⚠️ **A failed ATTEMPT is not a failed ORDER** (repaired 2026-08-19,
        # review P0). One Razorpay order accepts MANY payment attempts until
        # one captures: a UPI collect that times out, a card the issuer
        # declines, a 3DS step the customer abandons. This arm used to
        # transition the order to `failed` — TERMINAL, no edge leaves it — so
        # the retry the customer made inside the same Checkout arrived at a
        # dead order, `fulfil` refused, and the money-received-nothing-granted
        # path opened with a 200 that stopped the provider retrying.
        #
        # The order stays OPEN until it is captured or the TTL abandons it.
        # Order-level failure is 9.2's `abandoned`, written by the clock. The
        # receipt is what makes the attempt visible — SC-4a's "a failed payment
        # says so" reads `payment_event`, not a closed order.
        _log.info(
            "payments.attempt_failed",
            extra={"order": order["id"], "event": event.event_id,
                   "event_kind": event.kind, "status": order["status"],
                   "provider_payment_id": event.provider_payment_id},
        )
        return {"recorded": True, "fulfilled": False}

    if event.kind not in _CAPTURE_EVENTS:
        # Recorded and ignored. Razorpay sends many event types; acting on one
        # we have not designed for is how a refund becomes a grant.
        return {"recorded": True, "fulfilled": False}

    if event.amount_paise != order["total_paise"]:
        # THE alert. Refused, never fulfilled, and never resolved in the
        # customer's favour: 409 rather than 200, so the provider's retries
        # keep the discrepancy visible instead of letting one silent 200 close
        # the incident.
        _log.error(
            "payments.amount_mismatch",
            extra={"order": order["id"], "event": event.event_id,
                   "expected_paise": order["total_paise"],
                   "presented_paise": event.amount_paise},
        )
        raise HTTPException(
            status_code=409,
            detail="captured amount does not match the order total",
        )

    try:
        payments.fulfil(
            conn, order_id=order["id"], reference=f"order:{order['id']}",
        )
    except TransitionRefused:
        # ⚠️ **TWO situations reach this arm and only ONE of them is benign**
        # (split 2026-08-19, review P0(b)). Branching on the order's status is
        # what tells them apart:
        if order["status"] == "captured":
            # The SECOND event of one capture (`payment.captured` and
            # `order.paid` carry different ids). Recorded, not fulfilled,
            # 200 — the money guard doing exactly its job.
            _log.info("payments.already_fulfilled",
                      extra={"order": order["id"], "event": event.event_id})
            return {"recorded": True, "fulfilled": False}
        # Any OTHER terminal state — `abandoned` today, reachable the moment a
        # capture lands after the TTL sweep ran — means a signature-verified
        # payment of the right amount arrived and we granted nothing. Same
        # class as `payments.webhook_unknown_order`, so the same severity and
        # the same three structured fields: the payment must be findable at the
        # provider from the log line alone. CP-8's reconciliation owns these
        # rows alongside the NULL-`order_id` receipts.
        _log.error(
            "payments.capture_after_terminal",
            extra={"order": order["id"], "event": event.event_id,
                   "status": order["status"],
                   "provider_order_id": event.provider_order_id,
                   "provider_payment_id": event.provider_payment_id,
                   "amount_paise": event.amount_paise},
        )
        return {"recorded": True, "fulfilled": False}
    return {"recorded": True, "fulfilled": True}


#: Event kinds that mean money arrived. TWO of them, for ONE payment — which is
#: why the terminal-state rule exists (§9.5, B8).
_CAPTURE_EVENTS = frozenset({"payment.captured", "order.paid"})

#: Event kinds that mean **one attempt** failed. ⚠️ Named `_ATTEMPT_` on
#: purpose: they say nothing about the ORDER, which stays open for the next
#: attempt. **Nothing in the tree drives an order to `failed`** — the state
#: stays on 9.2's graph for an explicit customer cancel the surface half may
#: add, and order-level failure today is `abandoned`, written by the clock.
_ATTEMPT_FAILURE_EVENTS = frozenset({"payment.failed"})


@app.post("/discounts")
def issue_discount(req: IssueDiscountRequest, _: Operator) -> dict[str, Any]:
    """Mint a discount code. **The token is returned exactly once** (SC-4g (i)).

    Only the hash is stored, so this response is the only moment the secret
    exists anywhere — the same property `POST /keys` has, and for the same
    reason: a recoverable discount code is a shared password in a database
    somebody exports. A code that must be re-read later is re-issued.

    🔴 Issuing a code against a **live** organization is OWNER-GATE
    (`work_plan.md` §6(g), the SC-4e adjustment gate class). Authoring codes
    against fixtures is agent-safe; this route is the API beneath the operator
    surface CP-8 will render.
    """
    if req.kind not in ("percent", "fixed"):
        raise HTTPException(status_code=400,
                            detail="kind must be 'percent' or 'fixed'")
    if (req.kind == "percent") != (req.percent_bp is not None):
        raise HTTPException(
            status_code=400,
            detail="a percent code needs percent_bp and nothing else")
    if (req.kind == "fixed") != (req.amount_paise is not None):
        raise HTTPException(
            status_code=400,
            detail="a fixed code needs amount_paise and nothing else")

    minted = mint_key(env=ENV_DISCOUNT)
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug) if req.org_slug else None
        store.issue_discount_code(
            conn, prefix=minted.prefix, code_hash=minted.key_hash,
            label=req.label, kind=req.kind, org_id=org_id,
            percent_bp=req.percent_bp, amount_paise=req.amount_paise,
            max_redemptions=req.max_redemptions, expires_at=req.expires_at,
            created_by="operator",
        )
        # The audit row records the PREFIX, never the token.
        _audit(conn, org_id, "discount.issue",
               {"prefix": minted.prefix, "label": req.label,
                "kind": req.kind, "max_redemptions": req.max_redemptions})

    return {"prefix": minted.prefix, "code": minted.token}


@app.post("/usage/record")
def record_usage(req: UsageRequest, _: Internal) -> dict[str, Any]:
    """Record one metered call. Idempotent on ``(organization_id, request_id)``.

    **Internal token only.** This endpoint briefly accepted the customer's own
    organization key, which was wrong twice over: it let a negative
    ``billed_credits`` mint credits, and more fundamentally it made the metered
    party the reporter of its own usage — the thing §4.1 says is "not a meter,
    it is a suggestion". The Router counts the tokens it proxied and writes this
    row; the customer's key is read-only (:func:`whoami`).

    Idempotency is scoped **per organization** (migration 003). Globally-unique
    request ids let one tenant's id silently suppress another's charge and
    turned ``recorded:false`` into a cross-tenant existence oracle.

    Returns ``recorded: false`` for a replay. Callers should treat that as
    success, not as an error to retry, or a reconnect storm becomes a retry
    storm.
    """
    org_id = req.organization_id
    with get_engine().begin() as conn:
        recorded = store.record_usage(
            conn,
            org_id=org_id,
            request_id=req.request_id,
            billed_credits=req.billed_credits,
            user_email=req.user_email,
            agent=req.agent,
            module_slug=req.module_slug,
            model=req.model,
            tier=req.tier,
            run_id=req.run_id,
            prompt_tokens=req.prompt_tokens,
            completion_tokens=req.completion_tokens,
            cached_tokens=req.cached_tokens,
        )
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))

    return {"recorded": recorded, "balance": str(balance)}
