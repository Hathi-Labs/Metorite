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
import io
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, NoReturn

import anyio
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from customer_console import (
    analytics,
    catalog,
    feed,
    operator_activity,
    operator_elevation,
    operator_roles,
    operator_sessions,
    operator_signin,
    operators,
    payments,
    pricing_window,
    provider_keys,
    store,
)
from customer_console import router as router_mod
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
    StaffIdentity,
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
    UsagePartitionError,
    balance_of,
    decide_run_ceiling,
    decide_spend,
    estimate_hold,
    floor_charge,
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
    SSE_DONE,
    ExtractedUsage,
    ResolvedTier,
    TierUnknown,
    encrypt_secret,
    provider_credential,
    relay_stream,
    resolve_chain,
    resolve_tier_rate,
    usage_from_response,
)
from customer_console.seats import CORE_PLAN_SLUG, decide_assignment, seat_counts

_log = logging.getLogger("platform.router")


# ── The feed's clock (owner directive, 2026-08-30) ──────────────────────────
#
# "Updated regularly" is an env var, not a promise. Unset or non-positive
# means OFF (ship dark — flipping it is the owner's act, H-77); a positive
# float is the refresh period in hours. The loop must never die on a bad
# night: every failure is one warning line and one more sleep, and the
# packaged-snapshot fallback inside `feed.fetch_feed` already absorbs the
# network being down.
_FEED_SYNC_HOURS_VAR = "CUSTOMER_CONSOLE_FEED_SYNC_HOURS"


def _feed_sync_hours() -> float:
    try:
        hours = float(os.environ.get(_FEED_SYNC_HOURS_VAR, "0"))
    except ValueError:
        _log.warning("feed.autosync_off reason=unparseable %s", _FEED_SYNC_HOURS_VAR)
        return 0.0
    return hours if hours > 0 else 0.0


def _feed_sync_once() -> dict[str, Any]:
    started = datetime.now(UTC)
    raw, source = feed.fetch_feed()
    rows = feed.parse_feed(raw)
    with get_engine().begin() as conn:
        counts = feed.sync(conn, rows, source, started)
    return {"source": source, **counts}


async def _feed_autosync(hours: float) -> None:
    while True:
        try:
            result = await asyncio.to_thread(_feed_sync_once)
            _log.info("feed.autosync source=%s models=%d", result["source"], result["models_seen"])
        except Exception as exc:  # the loop outlives any night
            _log.warning("feed.autosync_failed error=%s", exc)
        await asyncio.sleep(hours * 3600)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    hours = _feed_sync_hours()
    task = asyncio.create_task(_feed_autosync(hours)) if hours else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()


app = FastAPI(
    title="Metorite Customer Console",
    description=(
        "Organizations, seats, subscriptions and AI metering. Cross-tenant by "
        "design (saas_multitenancy.md §0.9.2) — never exposed to a tenant."
    ),
    version="0.1.0",
    lifespan=_lifespan,
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
    #: le: ten years. Uncapped, a huge value passed pydantic and 500ed
    #: on `interval out of range` — bounds belong at the door.
    export_window_days: int = Field(default=30, ge=1, le=3650)


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


#: The full set seat_assignment.source's CHECK admits (001:193). The
#: self-serve door allows its own subset; the staff door allows all four.
_SEAT_SOURCES = frozenset({"core", "center", "plan", "alacarte"})


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
    #: Bounded to what `credit_ledger.delta` (NUMERIC(14,4)) can hold —
    #: beyond it the insert 500s. Negative stays legal: corrections.
    credits: Decimal = Field(ge=Decimal("-9999999999"), le=Decimal("9999999999"))
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
    #: ge=0: an "activation" that REMOVES credits is a correction wearing the
    #: wrong name — corrections are `/credits/grant`'s negative deltas, where
    #: the amount rules apply. le: `credit_ledger.delta` is NUMERIC(14,4).
    credits: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999999"))
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
    #: le mirrors what the table can hold (`payment_order_line.quantity` is
    #: INT) with room to spare — 3e9 used to pass pydantic and 500 on the
    #: insert. Nobody buys 100k seats in one basket by accident.
    quantity: int = Field(ge=1, le=100_000)


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


class SeatOverviewView(BaseModel):
    """The seat surface's whole answer, under the DEPLOYMENT-key door (CP-2h).

    ⚠️ **Two existing views, composed — never a third shape.** ``plans`` is
    exactly :class:`SeatsView`'s list (``_seat_grid`` → ``seat_counts``, the ONE
    seat vocabulary, §3.3 / D32.5) and ``members`` is exactly
    :class:`MembersView`'s (``store.org_members`` zipped with
    ``store.live_seats_by_email``). Nothing here adds a field, renames one, or
    computes a count: an admin reading this and an admin reading
    ``GET /me/seats`` + ``GET /me/members`` must never be shown two answers, and
    the way to guarantee that is to hand back the same two models.

    It exists because the two customer-key reads it mirrors are unreachable on a
    SHARED deployment (**D-SEAT-4**): a ``cc_live_`` organization key is
    per-organization, so a box hosting N tenants has no single correct one, and
    the seat surface fails closed to "not configured". The deployment key is the
    one customer credential that resolves a MEMBER, so it is the door that works
    for every tenant on the box. See :func:`seat_overview_admin`.
    """

    plans: list[SeatPlanView]
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
_FORWARDABLE = frozenset(
    {
        "messages",
        "temperature",
        "top_p",
        "n",
        "stop",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
        "response_format",
        "seed",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning_effort",
        "thinking",
    }
)

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
    #: G-3 (D61): **the CALLER declares the task. The Router never sniffs
    #: the payload.** `vision` uses the same verb as `chat` and differs
    #: only in which model is bound, so somebody has to say — and
    #: inference is what D32.7 is hostile to (*'a bare model id is
    #: rejected 400, not coerced'*).
    #:
    #: ⚠️ Defaulting to `chat` is what keeps every existing caller working
    #: unchanged. It is NOT a coercion: an explicit task that has no
    #: binding still 400s rather than falling back here.
    task: str = "chat"
    messages: list[dict[str, Any]]
    #: The caller's own correlation id. Stored as `client_ref`, trusted for
    #: NOTHING — it used to be the metering idempotency key, which let a caller
    #: suppress their own meter by reusing one value forever (verification F2).
    client_ref: str | None = None
    stream: bool = False

    temperature: float | None = None
    top_p: float | None = None
    #: Completions per request MULTIPLY what one request costs us
    #: (n x max_tokens on the output side), so the ceiling that caps
    #: max_tokens caps n too. Four covers best-of sampling; fifty would be
    #: a 50x draw on the provider account from one zero-balance trial call.
    n: int | None = Field(default=None, ge=1, le=4)
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
        r[0]
        for r in conn.execute(
            text("SELECT slug FROM plan_catalog WHERE active ORDER BY sort_order")
        )
    ]
    grid = []
    for plan in plans:
        grants, assigned = store.seat_rows(conn, org_id=org_id, plan_slug=plan)
        if not grants and not assigned:
            continue  # never bought, never assigned — not worth a row
        c = seat_counts(plan, grants, assigned)
        grid.append(
            SeatPlanView(
                plan_slug=plan,
                purchased=c.purchased,
                assigned=c.assigned,
                available=c.available,
                oversubscribed=c.oversubscribed,
            )
        )
    return grid


def _audit(
    conn, org_id: str | None, action: str, detail: dict[str, Any], *, actor: str = "operator"
) -> None:
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
        {"org": org_id, "actor": actor, "action": action, "detail": json.dumps(detail)},
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
        spent = store.run_spend(conn, org_id=caller.organization_id, run_id=caller.run_id)
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


def _rate_completion(
    conn,
    *,
    tier: str,
    model: str,
    usage: ExtractedUsage,
    task: str = "chat",
    quantity: Decimal | None = None,
) -> tuple[Decimal, str | None]:
    """Credits drawn by one completion — priced by the TIER (D67). **Never
    raises.**

    🔴 **The customer pays for the tier they PICKED, never for the model
    that served them.** Until 2026-08-30 this rated against
    ``model_rate_card``, which made a failover change the customer's price
    mid-day and made a premium tier impossible on a shared model. The tier
    card fixes both: a fallback moves OUR cost (recorded separately as
    ``provider_cost_usd``), and their price holds. ``model`` is kept here
    for the log line only, because "which model went unpriced" was never
    the question — "which PRODUCT is unpriced" is.

    An unpriced tier bills zero *loudly* rather than failing the call: the
    completion has already happened and the customer already has it, so the
    only choice left is whether we also lose the usage row. We do not — the
    row is the evidence, and pricing happens against exactly this data.
    Migration 015 seeds NO tier rates on purpose, so this warning is the
    expected state until the owner prices the slate (a commercial act, H-42).

    ⚠️ Returns the UNIT alongside the credits, so the usage row can record
    what the number was measured in. A row that says `0.4` without saying
    `minutes` is a number nobody can check afterwards.

    ``quantity`` is what the call consumed, in the card's own unit — minutes
    of audio for `transcribe` (§6A.10a clause 4). `rate_call` ignores it on a
    token-priced card, whose quantity is the token counters themselves, and
    it REFUSES a per-unit card that carries none rather than rating minutes
    at a token rate. That refusal arrives here as `UnpricedModel` and bills
    zero loudly, which is the same answer every other unrateable call gets.
    """
    # ⚠️ Resolving the CARD and RATING it are separate `try` blocks on
    # purpose. A card that exists but is not priced still knows what it
    # would be measured IN, and the usage row should say so — otherwise
    # every row written before the owner prices the card records a NULL
    # unit, and the day prices arrive the history cannot be read back.
    try:
        card = resolve_tier_rate(conn, tier, task)
    except UnpricedModel:
        _log.warning(
            "router.unpriced_tier",
            extra={"router_tier": tier, "router_task": task, "router_model": model},
        )
        return Decimal(0), None

    try:
        return (
            # 🔴 **The floor, applied AFTER rating and never inside it**
            # (`credit_pricing.md` §5.4). `rate_call` answers what the card
            # says this call is worth; the floor is a separate policy about
            # what an operation must cover, and folding the two together
            # would make the rate untestable without the policy.
            #
            # ⚠️ Keyed on the TASK, so `embed` keeps its exemption. A
            # five-credit floor per call charges 50000 credits to index
            # 10000 documents, against perhaps 200 credits of real value.
            floor_charge(
                quantize_credits(
                    rate_call(
                        card,
                        TokenUsage(
                            prompt_tokens=usage.prompt_tokens,
                            completion_tokens=usage.completion_tokens,
                            cached_tokens=usage.cached_tokens,
                        ),
                        quantity=quantity,
                    )
                ),
                task=card.task,
            ),
            card.unit,
        )
    except UnpricedModel:
        # `router_tier`, not `tier`: a stdlib LogRecord already owns several
        # short names and a collision raises inside the logging call itself.
        _log.warning(
            "router.unpriced_tier",
            extra={"router_tier": tier, "router_task": task, "router_model": model},
        )
        return Decimal(0), card.unit


# ── Routes ──────────────────────────────────────────────────────────────────

# ── Elevation (CP-12e) ──────────────────────────────────────────────────────
#
# Spec: operator_identity_and_access.md §6.3 · D64.4. An admin holds the RIGHT
# to elevate, not the privilege. The window is time-boxed and needs a reason.


class ElevateRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    #: SC-4g's `<reason>:<ref>` grammar. ONE reference vocabulary in this
    #: service, not a second invented here.
    reference: str | None = Field(default=None, max_length=200)


# ── CP-12f2: the FRONT DOOR (F8) ───────────────────────────────────────────
#
# ⚠️ Declared BEFORE `/operators/{operator_id}`, and that is not cosmetic.
# FastAPI matches in DECLARATION order, so the path parameter would otherwise
# swallow `/operators/session` and answer 404. CP-12e already shipped that bug
# once with `/operators/elevate`. A test pins both.


class SigninRequest(BaseModel):
    """The Supabase access token the operator's browser just obtained."""

    access_token: str = Field(min_length=1)


@app.post("/operators/session")
def operator_sign_in(req: SigninRequest, request: Request) -> dict[str, Any]:
    """Exchange a Supabase sign-in for an operator session. **Closes F8.**

    ⚠️ **This route is deliberately UNAUTHENTICATED.** It is the only door
    into the identity system, so it cannot require the identity it issues.
    What guards it is the token: `operator_signin.introspect` asks Supabase
    who the bearer is, and `operators.admit` then runs all three checks of
    §4.1 against the answer.

    Every refusal carries the same MINIMAL body. The status is split on
    purpose — 401 when the TOKEN was not trusted (fix the sign-in), 403
    when the person is not an admitted operator (fix the registry) — and
    the login callback tells a person which problem to fix from exactly
    that split. This docstring once claimed "the same 403 for everything",
    which the code, the tests and the UI never did; the split discloses
    only what the signed-in human is told anyway.
    """
    try:
        identity = operator_signin.introspect(req.access_token)
    except operator_signin.SigninUnconfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except operators.OperatorUnconfigured as exc:
        # `extract_identity` reads OPERATOR_SIGNIN_PROVIDER, which is staff-gate
        # configuration and raises this class. A misconfigured box is a 503
        # wherever the console notices it.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except operator_signin.SigninRejected as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    now = datetime.now(UTC)
    issued = operator_sessions.issue(now=now)
    try:
        with get_engine().begin() as conn:
            row = store.operator_by_email(conn, identity.email)

            # The one-time bootstrap, and ONLY for somebody already inside our
            # directory. Letting a stranger trigger it would consume the
            # one-time path before the owner reached it, which is a denial of
            # the bootstrap even though it grants the stranger nothing.
            #
            # ⚠️ **`directory_matches` decides, never a bare `==`** (spec §8.1
            # done-when 32). This line read `identity.tid ==
            # operators.staff_tenant_id()`. Two `None` values compare equal in
            # Python, so the day that getter returned `None` for an
            # unconfigured box, an identity carrying no directory claim would
            # consume the one-time path. The getter raises instead, and the
            # helper reads a missing claim as `False`. Both properties must
            # hold, because the hole needs only one of them to fail.
            #
            # ⚠️ **This CALL is a SINGLE point, and no row count watches it.**
            # The two properties above are guards inside the helper. The `and`
            # clause here is not. Delete it and the bootstrap fires for every
            # caller. The whole route runs in one transaction that rolls back
            # on the 403, so `count(*) FROM operator` still reads zero.
            # Measured 2026-09-01: that mutation left 148 tests green.
            # The fence watches the CALL, and it is
            # `test_operator_signin.py`
            # `::test_the_bootstrap_never_fires_on_a_missing_directory_claim`.
            # ⚠️ **D71.5 moved this gate, and the move is the single most
            # dangerous line in that decision.** It read
            # `operators.directory_matches(identity.tid)`. In `registry` mode
            # there is no directory claim to match, so that call is always
            # False and the bootstrap could never fire — and the obvious
            # "fix", deleting the clause, hands `admin` to the FIRST STRANGER
            # who signs in. `bootstrap_allowed` keeps the directory comparison
            # in `directory` mode and pins to `OPERATOR_BOOTSTRAP_EMAIL`
            # exactly in `registry` mode. Every doubt reads False.
            if row is None and operators.bootstrap_allowed(
                identity.email, identity.tid
            ):
                try:
                    operators.bootstrap(conn)
                except operators.BootstrapRefused:
                    # The registry already holds a row, so the normal path
                    # applies and `admit` below refuses on the registry check.
                    pass
                row = store.operator_by_email(conn, identity.email)

            operator = operators.admit(
                row,
                tid=identity.tid,
                email=identity.email,
                method=identity.method,
            )

            store.operator_session_insert(
                conn,
                operator_id=operator.id,
                prefix=issued.prefix,
                key_hash=issued.key_hash,
                expires_at=issued.expires_at,
                ip=operator_signin.safe_ip(request.client.host if request.client else None),
                user_agent=request.headers.get("user-agent"),
            )
            # Recorded on every sign-in, not only the first. A person whose
            # directory subject changes is a person whose account was
            # rebuilt, and the newest value is the one that matches.
            store.operator_set_directory_subject(
                conn, operator_id=operator.id, subject=identity.subject
            )
            _audit(conn, None, "operator.signin", {"role": operator.role}, actor=operator.email)
    except operators.OperatorUnconfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except operators.OperatorForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return {
        "token": issued.token,
        "expires_at": _iso(issued.expires_at),
        "operator": {
            "id": operator.id,
            "email": operator.email,
            "role": operator.role,
        },
    }


@app.delete("/operators/session")
def operator_sign_out(staff: Operator) -> dict[str, Any]:
    """Sign out. **Revokes the session server-side**, closing F5.

    The interim gate could only ask the browser to forget a cookie, because
    the cookie WAS the shared passphrase and nothing recorded it. This drops
    one row's `revoked_at`, so the next request with that token is refused
    with no restart and no cache wait.

    A break-glass caller holds no session row, so there is nothing to revoke
    and this answers 409 rather than pretending it worked.
    """
    if not staff.is_session or not staff.session_id:
        raise HTTPException(
            status_code=409,
            detail="the shared token holds no session to revoke",
        )
    with get_engine().begin() as conn:
        store.operator_session_revoke(conn, staff.session_id)
        # Sign-IN is audited; the revocation that ends the session was not.
        _audit(conn, None, "operator.signout", {}, actor=staff.actor)
    return {"revoked": True}


@app.get("/operators/session")
def read_operator_session(staff: Operator) -> dict[str, Any]:
    """Who is signed in here, and as what role?

    The sidebar's identity row shows the answer, so an operator always
    knows which name their next write is audited under (§5) and which
    matrix rank judges it. **The break-glass token names NOBODY** — it
    carries no person and no role, and inventing either here would teach
    the team to trust a name the audit log cannot back. The surface
    renders nothing for it, the elevation control's own discipline.
    """
    if not staff.is_session:
        return {"method": "breakglass", "actor": None, "role": None}
    return {"method": "session", "actor": staff.actor, "role": staff.role}


@app.post("/operators/elevate")
def open_elevation(req: ElevateRequest, staff: Operator) -> dict[str, Any]:
    """Open an elevation window for the CALLING operator.

    ⚠️ For themselves, always. There is no `operator_id` parameter, because
    elevating somebody ELSE would be a way to hand out a destructive privilege
    without them asking for it — and the person who did it would not be the
    person the audit row named.
    """
    if not staff.is_session or staff.operator_id is None:
        # The break-glass token is already past every gate. Letting it open a
        # window would be theatre, and it would create an elevation row with
        # no person attached to it.
        raise HTTPException(
            status_code=403,
            detail="only a signed-in operator can elevate",
        )
    try:
        operator_elevation.may_elevate(staff.role)
    except operator_elevation.ElevationRefused:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    try:
        reason = operator_elevation.check_reason(req.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    expires_at = datetime.now(UTC) + operator_elevation.ttl()
    with get_engine().begin() as conn:
        window_id = store.operator_elevation_open(
            conn,
            operator_id=staff.operator_id,
            reason=reason,
            reference=req.reference,
            expires_at=expires_at,
        )
        _audit(
            conn,
            None,
            "operator.elevate",
            {"reason": reason, "reference": req.reference, "expires_at": expires_at.isoformat()},
            actor=staff.actor,
        )
    return {"id": window_id, "expires_at": expires_at.isoformat()}


@app.get("/operators/elevate")
def read_elevation(staff: Operator) -> dict[str, Any]:
    """Is a window open for me, and until when?

    The surface needs this to show a countdown. A window whose end nobody can
    see is one people re-open out of habit.
    """
    if not staff.is_session or staff.operator_id is None:
        return {"elevated": False}
    with get_engine().begin() as conn:
        window = store.operator_elevation_live(conn, staff.operator_id)
    if window is None:
        return {"elevated": False}
    return {
        "elevated": True,
        "reason": window["reason"],
        "reference": window["reference"],
        "expires_at": window["expires_at"].isoformat(),
    }


@app.delete("/operators/elevate")
def close_elevation(staff: Operator) -> dict[str, Any]:
    """Close my window early. Finishing the job should end the privilege."""
    if not staff.is_session or staff.operator_id is None:
        return {"closed": 0}
    with get_engine().begin() as conn:
        closed = store.operator_elevation_close(conn, staff.operator_id)
        if closed:
            _audit(conn, None, "operator.elevate_close", {"closed": closed}, actor=staff.actor)
    return {"closed": closed}


# ── Operator administration (CP-12d) ────────────────────────────────────────
#
# Spec: operator_identity_and_access.md §6.1 · D64.3. The role matrix (§5)
# gates these at the door: reading is `viewer`, writing is `admin`.
#
# ⚠️ These routes administer PLATFORM STAFF, not a customer's members. The
# customer's own member admin is `POST /registry/members` and they are not the
# same surface, the same audience or the same table. Do not merge them.


class OperatorAddRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(default="viewer")


class OperatorPatchRequest(BaseModel):
    role: str | None = None
    status: str | None = None


def _admin_refusal(exc: Exception) -> HTTPException:
    """`AdminRefused` is a 409, never a 403 — see its docstring."""
    return HTTPException(status_code=409, detail=str(exc))


@app.get("/operators")
def list_operators(staff: Operator) -> dict[str, Any]:
    """Every platform operator, with role, status and live session count.

    Readable by a `viewer` on purpose. Who holds power over our customers is
    exactly the thing the team should be able to see without asking.
    """
    with get_engine().begin() as conn:
        rows = store.operator_list(conn)
    return {
        "operators": [
            {
                "id": str(r["id"]),
                "email": r["email"],
                "role": r["role"],
                "status": r["status"],
                "has_signed_in": bool(r["has_signed_in"]),
                "live_sessions": int(r["live_sessions"]),
            }
            for r in rows
        ]
    }


@app.post("/operators")
def add_operator(req: OperatorAddRequest, staff: Operator) -> dict[str, Any]:
    """Add a platform operator. Idempotent on the email.

    The person becomes real on their FIRST successful directory sign-in —
    this only records that they may. An email is the one identifier a human
    knows before that has ever happened, which is why the registry is keyed
    on it rather than on a directory subject.
    """
    email = operators.normalise_email(req.email)
    try:
        operators.guard_known_role(req.role)
    except operators.AdminRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    with get_engine().begin() as conn:
        operator_id = store.operator_insert(
            conn,
            email=email,
            role=req.role,
            added_by=staff.operator_id,
        )
        row = store.operator_by_email(conn, email)
        assert row is not None  # just inserted, or already there
        if row["role"] != req.role:
            # ON CONFLICT DO NOTHING fired: the person already exists with a
            # DIFFERENT role. Answering the requested role here audited a
            # demotion that never happened.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{email} already exists as {row['role']!r} - change "
                    "a role with PATCH /operators/{operator_id}, not a "
                    "second add"
                ),
            )
        _audit(conn, None, "operator.add", {"email": email, "role": row["role"]}, actor=staff.actor)
    return {"id": operator_id, "email": email, "role": row["role"]}


# ⚠️ THE ELEVATION ROUTES ARE DECLARED ABOVE THIS LINE ON PURPOSE.
# FastAPI matches in declaration order, so `/operators/{operator_id}`
# would otherwise swallow `/operators/elevate` with operator_id="elevate"
# and `DELETE /operators/elevate` would answer 404 instead of closing a
# window. Measured, not guessed. Fence:
# `test_operator_elevation.py::test_the_elevate_routes_are_not_swallowed`.
@app.patch("/operators/{operator_id}")
def patch_operator(operator_id: str, req: OperatorPatchRequest, staff: Operator) -> dict[str, Any]:
    """Change an operator's role, their status, or both.

    ⚠️ **One transaction.** The status change and the session revocation that
    must follow it are the same act. Two transactions would leave a window in
    which the row reads `deactivated` and a session still works.
    """
    if req.role is None and req.status is None:
        raise HTTPException(status_code=400, detail="nothing to change")

    with get_engine().begin() as conn:
        target = store.operator_by_id(conn, operator_id)
        if target is None:
            raise HTTPException(status_code=404, detail="no such operator")

        try:
            if staff.operator_id is not None:
                operators.guard_not_self(staff.operator_id, str(target["id"]))
            if req.role is not None:
                operators.guard_known_role(req.role)
            if req.status is not None:
                operators.guard_known_status(req.status)
            operators.guard_last_admin(
                active_admins=store.operator_active_admin_count(conn),
                target_role=str(target["role"]),
                target_status=str(target["status"]),
                new_role=req.role,
                new_status=req.status,
            )
        except operators.AdminRefused as exc:
            raise _admin_refusal(exc) from None

        if req.role is not None:
            store.operator_set_role(conn, operator_id=operator_id, role=req.role)
        revoked = 0
        if req.status is not None:
            store.operator_set_status(conn, operator_id=operator_id, status=req.status)
            if req.status != "active":
                # The same transaction. This is the fix for spec §2's F5.
                revoked = store.operator_sessions_revoke_all(conn, operator_id)

        _audit(
            conn,
            None,
            "operator.update",
            {
                "email": target["email"],
                "role": req.role,
                "status": req.status,
                "sessions_revoked": revoked,
            },
            actor=staff.actor,
        )

    return {"id": operator_id, "sessions_revoked": revoked}


@app.delete("/operators/{operator_id}")
def deactivate_operator(operator_id: str, staff: Operator) -> dict[str, Any]:
    """Deactivate an operator. **It never deletes the row.**

    D63 — deactivation SEALS. The row stays so the person's `control_audit`
    history stays readable, and a `DELETE` that removed it would orphan the
    audit trail that naming them was for.

    The HTTP verb is `DELETE` because that is what an operator means by it.
    What it does is written here so nobody has to guess.
    """
    return patch_operator(operator_id, OperatorPatchRequest(status="deactivated"), staff)


# ── CP-12f: the Activity surface (D64.5, done-whens 25-26) ─────────────────


@app.get("/activity/actions")
def list_activity_actions(staff: Operator) -> dict[str, Any]:
    """The distinct `action` values present, so a filter can offer real ones."""
    with get_engine().begin() as conn:
        return {"actions": store.activity_actions(conn)}


@app.get("/activity")
def read_activity(
    staff: Operator,
    actor: str | None = None,
    action: str | None = None,
    org_slug: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """The audit trail across EVERY company, newest first.

    Done-when 25. Readable by a `viewer`, and cross-org by design — this is the
    one read that answers "who did what to which customer", which no
    single-tenant view can. It is the commercial record only: `control_audit`
    holds our own acts against a tenant, never the tenant's content (D64.5).

    Done-when 26. Keyset-paginated on `(created_at, id)`. ⚠️ The cursor is
    EPHEMERAL and must stay so — `operator_activity.CURSOR_IS_EPHEMERAL`
    carries the measured reason, and H-7 is what happens when a cursor like
    this one is persisted instead.

    An unknown `actor`, `action` or `org_slug` returns an EMPTY page, not a
    404. A refusal that told the caller a company exists would be the same
    oracle §5 spent CP-12c closing.
    """
    try:
        mark = operator_activity.decode_cursor(cursor)
    except operator_activity.CursorInvalid as exc:
        # 400, never 500 — see the exception's docstring.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    size = operator_activity.clamp_limit(limit)
    with get_engine().begin() as conn:
        rows = store.activity_page(
            conn,
            limit=size,
            cursor_created_at=mark.created_at if mark else None,
            cursor_id=mark.row_id if mark else None,
            actor=actor,
            action=action,
            org_slug=org_slug,
        )

    # A cursor is offered only on a FULL page. A short page is the end of the
    # trail, and handing back a cursor there sends every client one more
    # round-trip to discover nothing.
    next_cursor = (
        operator_activity.encode_cursor(rows[-1]["created_at"], str(rows[-1]["id"]))
        if len(rows) == size and rows
        else None
    )
    return {
        "activity": [
            {
                "id": str(r["id"]),
                "actor": r["actor"],
                "action": r["action"],
                "detail": r["detail"],
                "created_at": _iso(r["created_at"]),
                "org_slug": r["org_slug"],
                "org_name": r["org_name"],
            }
            for r in rows
        ],
        "next_cursor": next_cursor,
    }


# ── CP-10 slice 1: OUR provider credentials (H-40, D56.7) ──────────────────
#
# ⚠️ **This is the write path that did not exist.** `router.provider_credential`
# has always read this table, and nothing has ever written it — so on a fresh
# Console database the Router cannot call a provider at all. Everything else in
# CP-10 manages a thing that could not exist.


class ProviderCredentialRequest(BaseModel):
    """Install a provider credential.

    ⚠️ `secret` is write-ONLY. It appears in no response model in this file,
    and `store.provider_credential_list` does not even select the column it is
    stored in. Done-when 2 asks for a structural fence rather than an example
    test, and those two facts are it.
    """

    provider: str = Field(min_length=1)
    secret: str = Field(min_length=1)
    api_base: str | None = None
    label: str | None = None
    #: Present means BYOK — this organization insists on its own provider
    #: account (§3.4). Absent means the PLATFORM account, used for everyone
    #: else, which is the row the Router falls back to.
    org_slug: str | None = None


class ProviderCredentialRevokeRequest(BaseModel):
    provider: str = Field(min_length=1)
    org_slug: str | None = None


def _credential_refusal(exc: Exception) -> HTTPException:
    """400, and it SAYS WHY — see `CredentialRefused`."""
    return HTTPException(status_code=400, detail=str(exc))


# ── The operator's model catalog (CP-10 slice 3) ────────────────────────────
#
# ⚠️ **INSERT ONLY** (§6A.5). Re-pointing a tier and re-pricing a model are
# both appends with an `effective_from`, so a past invoice is never recomputed
# and the history of what a customer was charged against stays intact. There is
# deliberately no PATCH or DELETE on a binding or a rate, and adding one is not
# a refactor. `revoked_at` on a provider credential is the one exception, and
# it is already modelled.


class CapabilityRequest(BaseModel):
    model: str
    task: str
    invocation: str
    streams: bool = False


class BindingRequest(BaseModel):
    tier: str
    task: str
    #: The single model, for the one-step case every caller used before 011.
    model: str | None = None
    #: The ORDERED chain, primary first. Supersedes `model` when both arrive.
    #:
    #: ⚠️ A chain is written WHOLE, at one `effective_from`. Removing a step is
    #: "send the chain you want", never a delete — §6A.5 is insert-only so a
    #: past invoice stays readable against what it was actually charged on.
    models: list[str] | None = None
    #: Omit for "now". A future date STAGES a change without taking effect,
    #: which is the same shape `seat_grant` and `model_rate_card` already use.
    effective_from: datetime | None = None

    def chain(self) -> list[str]:
        """The steps to write, in order, de-duplicated.

        ⚠️ **A repeated model is dropped, not refused.** The primary key would
        reject the second row anyway, and failing the whole request over a
        duplicate would lose the four steps that were fine. The second try adds
        nothing either way — it is the same model on the same provider.
        """
        raw = self.models if self.models else ([self.model] if self.model else [])
        seen: set[str] = set()
        out: list[str] = []
        for m in raw:
            m = (m or "").strip()
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out


def _catalog_refusal(exc: catalog.CatalogRefused) -> HTTPException:
    """A refused catalog write is a 400 the operator can act on."""
    return HTTPException(status_code=400, detail=str(exc))


#: The ceiling `model_profile`'s per-unit columns impose, restated where the
#: read can enforce it. It mirrors `feed._UNIT_MAX` exactly, and it must:
#: NUMERIC(18, 10) keeps ten digits after the point, so eight remain in front
#: and 1E8 is the first value that will not fit.
#:
#: ⚠️ **`ProfileRequest` binds the same number**, so a hand-typed price is
#: refused at the door rather than at the database. Both ends of the seam
#: read one constant.
_PER_UNIT_MAX = Decimal("1E8")

#: The FLOOR the same columns impose — the smallest value scale 10 can carry.
#: A nonzero price below it quantizes to zero, and "0" on this table means
#: free. `feed._UNIT10` is the ingest half of the identical rule.
_PER_UNIT_SCALE = Decimal("0.0000000001")


def _fixed(v: Decimal | None) -> str | None:
    """A money value as a FIXED-POINT string, or None for unknown.

    🔴 **`str(Decimal)` goes scientific below 1e-6, and an operator cannot
    read `6.0E-9`.** These per-unit columns are NUMERIC(18, 10), so a cheap
    model really does land there — `0.0000000060` is a legal per-character
    price. The console copies this string straight into a form box and then
    POSTs it back, so E-notation would also be what we store. `format(v,
    "f")` keeps every value in plain digits.
    """
    return None if v is None else format(v, "f")


def _per_minute_wire(per_second: Decimal | None) -> str | None:
    """The ONE place a per-SECOND vendor price becomes a per-MINUTE one.

    🔴 **This is §6A.11a clause 5.** litellm prices transcription per second
    and `task_catalog` (010) prices `transcribe` per minute, so the feed and
    the profile hold the same price in different units on purpose. The
    conversion happens here, server-side, in `Decimal`. The browser copies
    the result and converts nothing, because a conversion in TypeScript is a
    float conversion and a float rewrites the number it copies.

    ⚠️ **A price at or above the column ceiling serves NULL, not a number.**
    The feed admits anything under 1E8 per second, so x60 reaches 6E9 — which
    `model_profile.vendor_per_minute_usd` cannot hold. Serving it anyway
    hands the console a value whose only use is the copy button, and the copy
    would answer 500 from an unhandled psycopg DataError. Unknown beats
    poisoned, which is the rule `feed._per_unit` already applies on the way
    in. A dash is honest. A 500 on a button is not.
    """
    if per_second is None:
        return None
    minutes = Decimal(per_second) * Decimal(60)
    if minutes >= _PER_UNIT_MAX:
        return None
    return _fixed(minutes)


@app.get("/catalog/models")
def catalog_models(staff: Operator) -> dict[str, Any]:
    """Everything the operator manages, and the two GAPS between the tables.

    ⚠️ **The gaps are the point.** `model_capability` says what a model CAN do.
    `tier_binding` says what we USE it for. Neither table shows the difference,
    and the difference is where the mistakes live:

    * ``unbound`` — capable and unused. *"This model can generate images and we
      have not bound it to anything."* Money left on the table.
    * ``unserved`` — bound and NOT capable. The Router resolves a model and
      then cannot decide which verb to call. That is a 500 waiting for the
      first request, and it is invisible in either table alone.
    """
    with get_engine().begin() as conn:
        tasks = [
            {"slug": r[0], "label": r[1], "natural_unit": r[2]}
            for r in conn.execute(
                text("SELECT slug, label, natural_unit FROM task_catalog ORDER BY sort_order")
            )
        ]
        caps = [
            {"model": r[0], "task": r[1], "invocation": r[2], "streams": r[3]}
            for r in conn.execute(
                text(
                    "SELECT model, task, invocation, streams FROM model_capability "
                    "ORDER BY model, task"
                )
            )
        ]
        # IN FORCE ONLY — the newest row per key whose date has passed. The
        # superseded rows stay in the table for the audit trail, and showing
        # them here would read as "these are all live".
        # IN FORCE ONLY, and the WHOLE chain (011).
        #
        # ⚠️ **Every row at the newest timestamp, not the newest row per rank.**
        # A chain is written whole at one `effective_from`, so taking the newest
        # row per rank would splice half of yesterday's chain onto half of
        # today's — a configuration nobody chose and nobody could reproduce
        # from the audit trail.
        bindings = [
            {"tier": r[0], "task": r[1], "model": r[2], "rank": r[3], "effective_from": _iso(r[4])}
            for r in conn.execute(
                text(
                    "SELECT b.tier, b.task, b.model, b.rank, b.effective_from "
                    "FROM tier_binding b "
                    "WHERE b.effective_from = ("
                    "    SELECT max(x.effective_from) FROM tier_binding x "
                    "    WHERE x.task = b.task AND x.tier = b.tier "
                    "      AND x.effective_from <= now()) "
                    "ORDER BY b.task, b.tier, b.rank"
                )
            )
        ]
        # What each model IS (012). LEFT of everything: a model with no profile
        # row is normal, and it renders as em dashes rather than vanishing.
        profiles = [
            {
                "model": r[0],
                "label": r[1],
                "context_window": r[2],
                "max_output": r[3],
                # ⚠️ Money as STRINGS. These are NUMERIC in the database, and a
                # parsed float re-formatted is how a number stops matching itself.
                "vendor_input_per_1m_usd": None if r[4] is None else str(r[4]),
                "vendor_output_per_1m_usd": None if r[5] is None else str(r[5]),
                "vendor_cached_input_per_1m_usd": None if r[9] is None else str(r[9]),
                # The three per-unit costs (019, H-78). Each one already speaks
                # the TASK's unit, so this projection converts nothing — the
                # feed read did the x60 before anything copied a price here.
                # ⚠️ Fixed-point, because NUMERIC(18, 10) reaches values that
                # `str(Decimal)` would render as `6.0E-9`.
                "vendor_per_minute_usd": _fixed(r[10]),
                "vendor_per_character_usd": _fixed(r[11]),
                "vendor_per_image_usd": _fixed(r[12]),
                # 023 — the off-peak rates and the window that selects them.
                # ⚠️ The three peak fields above keep their names, because R6
                # forbids a rename in place. They ARE the peak rate.
                "vendor_input_offpeak_per_1m_usd": None if r[13] is None else str(r[13]),
                "vendor_output_offpeak_per_1m_usd": None if r[14] is None else str(r[14]),
                "vendor_cached_input_offpeak_per_1m_usd": (
                    None if r[15] is None else str(r[15])
                ),
                # `HH:MM` on the wire. A `time` would serialise as `16:30:00`
                # and the operator typed `16:30`.
                "offpeak_start_utc": None if r[16] is None else r[16].strftime("%H:%M"),
                "offpeak_end_utc": None if r[17] is None else r[17].strftime("%H:%M"),
                # 023 — the long-context threshold and its rates.
                "context_tier_threshold": r[18],
                "vendor_input_long_per_1m_usd": None if r[19] is None else str(r[19]),
                "vendor_output_long_per_1m_usd": None if r[20] is None else str(r[20]),
                "vendor_cached_input_long_per_1m_usd": (
                    None if r[21] is None else str(r[21])
                ),
                "description": r[6],
                "reads_images": r[7],
                "thinks_first": r[8],
            }
            for r in conn.execute(
                text(
                    "SELECT model, label, context_window, max_output, "
                    "       vendor_input_per_1m_usd, vendor_output_per_1m_usd, "
                    "       description, reads_images, thinks_first, "
                    "       vendor_cached_input_per_1m_usd, "
                    "       vendor_per_minute_usd, vendor_per_character_usd, "
                    "       vendor_per_image_usd, "
                    # ⚠️ APPENDED, never inserted. This projection reads BY
                    # POSITION, so a column added in the middle silently
                    # renames every field after it.
                    "       vendor_input_offpeak_per_1m_usd, "
                    "       vendor_output_offpeak_per_1m_usd, "
                    "       vendor_cached_input_offpeak_per_1m_usd, "
                    "       offpeak_start_utc, offpeak_end_utc, "
                    "       context_tier_threshold, "
                    "       vendor_input_long_per_1m_usd, "
                    "       vendor_output_long_per_1m_usd, "
                    "       vendor_cached_input_long_per_1m_usd "
                    "FROM model_profile ORDER BY model"
                )
            )
        ]
        rates = [
            {
                "model": r[0],
                "task": r[1],
                "unit": r[2],
                "pricing_mode": r[3],
                "input_per_1k": str(r[4]),
                "output_per_1k": str(r[5]),
                "cached_input_per_1k": str(r[6]),
                "credits_per_unit": str(r[7]),
                "effective_from": _iso(r[8]),
            }
            for r in conn.execute(
                text(
                    "SELECT DISTINCT ON (model, task) model, task, unit, "
                    "       pricing_mode, input_credits_per_1k, "
                    "       output_credits_per_1k, cached_input_credits_per_1k, "
                    "       credits_per_unit, effective_from "
                    "FROM model_rate_card WHERE effective_from <= now() "
                    "ORDER BY model, task, effective_from DESC"
                )
            )
        ]
        # The tier registry (015) — the product slate. A row here is what
        # lets an EMPTY tier exist: bound to nothing yet, shown anyway,
        # because the board is the map of what we intend to sell.
        # ⚠️ `customer_visible` (021) rides along so the board can say which
        # tiers a customer can pick. It is the SAME column `GET /my/tiers`
        # filters on, read once — an operator who cannot see the flag would
        # have to guess why a tier is missing from the customer's picker.
        tier_registry = [
            {
                "slug": r[0],
                "label": r[1],
                "blurb": r[2],
                "sort_order": int(r[3]),
                "task": r[4],
                "customer_visible": bool(r[5]),
            }
            for r in conn.execute(
                text(
                    "SELECT slug, label, blurb, sort_order, task, "
                    "       customer_visible "
                    "FROM tier_catalog ORDER BY sort_order, slug"
                )
            )
        ]
        # What a CUSTOMER pays (D67): the tier card in force per (tier, task).
        tier_rates = [
            {
                "tier": r[0],
                "task": r[1],
                "unit": r[2],
                "pricing_mode": r[3],
                # ⚠️ BOTH scales on the wire (migration 025, release one).
                # The console and the Console deploy separately, so the wire
                # is an expand/contract surface too: a frontend that has not
                # shipped yet still reads `_per_1k` and still draws the right
                # number. A later release removes them here as well.
                "input_per_1k": str(r[4]),
                "output_per_1k": str(r[5]),
                "cached_input_per_1k": str(r[6]),
                # 🔴 The scale of record. NULL only for a row written before
                # 024 backfilled, so the fallback keeps the number honest.
                "input_per_1m": str(r[9] if r[9] is not None else r[4] * 1000),
                "output_per_1m": str(r[10] if r[10] is not None else r[5] * 1000),
                "cached_input_per_1m": str(
                    r[11] if r[11] is not None else r[6] * 1000
                ),
                "credits_per_unit": str(r[7]),
                "effective_from": _iso(r[8]),
            }
            for r in conn.execute(
                text(
                    "SELECT DISTINCT ON (tier, task) tier, task, unit, "
                    "       pricing_mode, input_credits_per_1k, "
                    "       output_credits_per_1k, cached_input_credits_per_1k, "
                    "       credits_per_unit, effective_from, "
                    # ⚠️ APPENDED. This projection reads by POSITION.
                    "       input_credits_per_1m, output_credits_per_1m, "
                    "       cached_input_credits_per_1m "
                    "FROM tier_rate_card WHERE effective_from <= now() "
                    "ORDER BY tier, task, effective_from DESC"
                )
            )
        ]
        # 🔴 What each tier ACTUALLY earned, against the floor it was given
        # (migration 029, `credit_pricing.md` §4.3). Grouped by TIER, because
        # the question is whether a PRODUCT is priced right and one customer's
        # mix says nothing about that.
        tier_margins = store.margin_by_tier(conn, days=MARGIN_WINDOW_DAYS)

        # The credit's own price (017) — what one credit SELLS for, in
        # rupees, plus the planning rate margins convert dollars with.
        # ⚠️ Billing never reads this: a call bills CREDITS and the tier
        # card owns how many. This row prices the credits themselves.
        price_row = conn.execute(
            text(
                "SELECT inr_per_credit, usd_to_inr, effective_from "
                "FROM credit_price WHERE effective_from <= now() "
                "ORDER BY effective_from DESC LIMIT 1"
            )
        ).fetchone()
        # Failovers that actually happened (013, slice 12's read half). A
        # served_rank above 1 is a customer request the primary did not
        # answer — the one durable proof a chain earns its keep. Aggregated
        # by day so a bad afternoon reads as one row, not four hundred.
        failovers = [
            {
                "day": _iso(r[0]),
                "tier": r[1],
                "task": r[2],
                "model": r[3],
                "rank": int(r[4]),
                "requests": int(r[5]),
            }
            for r in conn.execute(
                text(
                    "SELECT date_trunc('day', created_at) AS day, tier, task, "
                    "       model, served_rank, COUNT(*) "
                    "FROM usage_event "
                    "WHERE served_rank > 1 "
                    "  AND created_at >= now() - INTERVAL '14 days' "
                    "GROUP BY 1, tier, task, model, served_rank "
                    "ORDER BY 1 DESC, tier, task LIMIT 50"
                )
            )
        ]

        # The vendor feed (014): upstream facts, fetched instead of typed.
        # Three reads, one purpose — the operator clicks instead of copying
        # out of a vendor's HTML pricing page:
        #   meta      "how current is what you are looking at", from the
        #             sync ledger, because currency is a provable claim;
        #   rows      feed facts for models ALREADY declared or profiled, so
        #             the console can show DRIFT (the vendor moved a price
        #             under a profile somebody typed);
        #   available what a CONNECTED vendor offers that nobody declared —
        #             "latest models" as a list with an Add button, not a
        #             newsletter. Vendors without a live platform key are
        #             excluded: a model we hold no key for is not available,
        #             it is a brochure.
        meta_row = conn.execute(
            text("SELECT source, finished_at FROM feed_sync_log ORDER BY id DESC LIMIT 1")
        ).fetchone()
        feed_total = conn.execute(text("SELECT COUNT(*) FROM vendor_price_feed")).scalar() or 0

        def _feed_wire(r: Any) -> dict[str, Any]:
            # ⚠️ Money as STRINGS, same rule as profiles above.
            #
            # 🔴 **The per-unit costs go through `_per_minute_wire`, which is
            # the ONE place a price changes unit** (H-78, §6A.11a clause 5).
            # The wire carries `vendor_per_minute_usd` and never
            # `vendor_per_second_usd`, so the browser has nothing to convert.
            return {
                "model": r[0],
                "provider": r[1],
                "mode": r[2],
                "task": r[3],
                "invocation": r[4],
                "context_window": r[5],
                "max_output": r[6],
                "vendor_input_per_1m_usd": None if r[7] is None else str(r[7]),
                "vendor_output_per_1m_usd": None if r[8] is None else str(r[8]),
                "vendor_cached_input_per_1m_usd": None if r[9] is None else str(r[9]),
                "reads_images": r[10],
                "thinks_first": r[11],
                "deprecated_on": None if r[12] is None else r[12].isoformat(),
                "vendor_per_minute_usd": _per_minute_wire(r[13]),
                # The other two already speak the task's unit, so they cross
                # verbatim — fixed-point, never E-notation.
                "vendor_per_character_usd": _fixed(r[14]),
                "vendor_per_image_usd": _fixed(r[15]),
            }

        # ⚠️ `_feed_wire` reads BY POSITION, so a column inserted in the
        # middle renames three fields without a word of warning. Append.
        _FEED_COLS = (
            "model, provider, mode, task, invocation, context_window, "
            "max_output, vendor_input_per_1m_usd, vendor_output_per_1m_usd, "
            "vendor_cached_input_per_1m_usd, reads_images, thinks_first, "
            "deprecated_on, vendor_per_second_usd, vendor_per_character_usd, "
            "vendor_per_image_usd"
        )
        feed_rows = [
            _feed_wire(r)
            for r in conn.execute(
                text(
                    f"SELECT {_FEED_COLS} FROM vendor_price_feed "
                    "WHERE model IN (SELECT model FROM model_capability "
                    "                UNION SELECT model FROM model_profile) "
                    "ORDER BY model"
                )
            )
        ]
        feed_available = [
            _feed_wire(r)
            for r in conn.execute(
                text(
                    f"SELECT {_FEED_COLS} FROM vendor_price_feed "
                    "WHERE provider IN (SELECT provider FROM provider_credential "
                    "                   WHERE organization_id IS NULL "
                    "                     AND revoked_at IS NULL) "
                    "  AND model NOT IN (SELECT model FROM model_capability "
                    "                    UNION SELECT model FROM model_profile) "
                    "ORDER BY provider, mode, model LIMIT 1000"
                )
            )
        ]

    cap_pairs = [(c["model"], c["task"]) for c in caps]
    bind_pairs = [(b["model"], b["task"]) for b in bindings]
    return {
        "tasks": tasks,
        "capabilities": caps,
        "profiles": profiles,
        "bindings": bindings,
        "rates": rates,
        "tier_registry": tier_registry,
        "tier_rates": tier_rates,
        # ⚠️ `realised_margin` is NULL until the operator saves a credit
        # price. NULL is NEUTRAL and never zero: no saved price means no
        # margin, not a bad one, and a zero would read as "selling at cost".
        "tier_margins": [
            {
                "tier": m["tier"],
                "calls": m["calls"],
                "costed_calls": m["costed_calls"],
                "credits": str(m["credits"]),
                "cost_usd": str(m["cost_usd"]),
                "margin_multiplier": (
                    None if m["margin_multiplier"] is None
                    else str(m["margin_multiplier"])
                ),
                "margin_floor": (
                    None if m["margin_floor"] is None else str(m["margin_floor"])
                ),
                "realised_margin": (
                    None if _realised is None else str(_realised)
                ),
            }
            for m in tier_margins
            for _realised in [
                analytics.realised_margin(
                    m["credits"],
                    m["cost_usd"],
                    inr_per_credit=(None if price_row is None else price_row[0]),
                    usd_to_inr=(None if price_row is None else price_row[1]),
                )
            ]
        ],
        "credit_price": None
        if price_row is None
        else {
            # ⚠️ Money as STRINGS, same rule as every price on this wire.
            "inr_per_credit": str(price_row[0]),
            "usd_to_inr": str(price_row[1]),
            "effective_from": _iso(price_row[2]),
        },
        "failovers": failovers,
        "unbound": catalog.unbound_capabilities(cap_pairs, bind_pairs),
        "unserved": catalog.unserved_bindings(cap_pairs, bind_pairs),
        "feed": {
            "synced_at": None if meta_row is None else _iso(meta_row[1]),
            "source": None if meta_row is None else meta_row[0],
            "models": int(feed_total),
            "rows": feed_rows,
            "available": feed_available,
        },
    }


@app.post("/catalog/feed/sync")
def sync_vendor_feed(staff: Operator) -> dict[str, Any]:
    """Pull the vendor feed NOW and land it in ``vendor_price_feed``.

    The other half of "updated regularly" is the flag-gated autosync loop
    (``CUSTOMER_CONSOLE_FEED_SYNC_HOURS``); this endpoint is the button, and
    it works with the flag off. Reference data only — nothing here touches
    ``model_profile``, a rate card, or anything billing reads. The response
    repeats the evidence row (source + counts) so the caller can verify the
    sync happened rather than believe it did.
    """
    started = datetime.now(UTC)
    raw, source = feed.fetch_feed()
    rows = feed.parse_feed(raw)
    with get_engine().begin() as conn:
        counts = feed.sync(conn, rows, source, started)
        _audit(conn, None, "catalog.feed_sync", {"source": source, **counts}, actor=staff.actor)
    return {"source": source, **counts}


@app.post("/catalog/capabilities")
def declare_capability(req: CapabilityRequest, staff: Operator) -> dict[str, Any]:
    """Declare what a model can do, and which provider verb does it.

    Replaces `_STT_TIER_IDS`, a frozenset that could not grow a row (D60.2).
    This one is an UPSERT rather than an append, and the difference is
    deliberate: a capability is a FACT about a model, not a commercial term.
    Nobody is billed against it, so correcting it destroys no audit trail.
    """
    try:
        invocation = catalog.check_invocation(req.invocation)
        streams = catalog.check_streams(req.task, req.streams)
    except catalog.CatalogRefused as exc:
        raise _catalog_refusal(exc) from exc

    with get_engine().begin() as conn:
        if not _task_exists(conn, req.task):
            raise HTTPException(status_code=400, detail=f"unknown task {req.task!r}")
        conn.execute(
            text(
                "INSERT INTO model_capability (model, task, invocation, streams) "
                "VALUES (:m, :t, :i, :s) "
                "ON CONFLICT (model, task) DO UPDATE "
                "SET invocation = EXCLUDED.invocation, "
                "    streams = EXCLUDED.streams"
            ),
            {"m": req.model, "t": req.task, "i": invocation, "s": streams},
        )
        _audit(
            conn,
            None,
            "catalog.capability",
            {"model": req.model, "task": req.task, "invocation": invocation},
            actor=staff.actor,
        )
    return {"model": req.model, "task": req.task, "invocation": invocation, "streams": streams}


@app.post("/catalog/bindings")
def bind_tier(req: BindingRequest, staff: Operator) -> dict[str, Any]:
    """Point a `(task, tier)` pair at a model. **INSERT, never UPDATE.**

    ⚠️ **This decides what every customer's call actually runs on**, which is
    why the matrix asks for `admin` AND an elevation window. A wrong model here
    does not fail loudly — it answers, plausibly, at the wrong price.

    ⚠️ The binding is refused unless the model DECLARES the capability. Without
    that check the Router resolves a model and then cannot choose a verb, which
    is a 500 on the first request rather than an error anybody sees here.
    """
    chain = req.chain()
    if not chain:
        raise HTTPException(
            status_code=400,
            detail="give a model, or an ordered list of models",
        )

    with get_engine().begin() as conn:
        if not _task_exists(conn, req.task):
            raise HTTPException(status_code=400, detail=f"unknown task {req.task!r}")

        # D68: a tier serves ONE kind of job, and the registry says which.
        # tier-stt IS speech-to-text; binding chat onto it is a mis-click,
        # and this is where the mis-click stops. A NULL registry task (a
        # ghost or a pre-016 row) keeps the old freedom.
        _check_tier_task(conn, tier=req.tier, task=req.task)

        # ⚠️ **EVERY step is checked, not just the primary.** An unchecked
        # backup is worse than no backup: it is only reached after the primary
        # has already failed, so the 500 arrives during an outage, when nobody
        # has attention to spare for it.
        for model in chain:
            capable = conn.execute(
                text("SELECT 1 FROM model_capability WHERE model = :m AND task = :t"),
                {"m": model, "t": req.task},
            ).first()
            if capable is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{model!r} declares no capability for task {req.task!r}; declare it first"
                    ),
                )

        # 🔴 **One timestamp for the whole chain, computed ONCE.** Letting each
        # row default to `now()` would give the steps different microsecond
        # timestamps, and resolution takes every row at the newest one — so the
        # chain would resolve to its last step alone. The bug would be
        # invisible until the primary failed.
        eff = conn.execute(
            text("SELECT COALESCE(CAST(:eff AS TIMESTAMPTZ), now())"),
            {"eff": req.effective_from},
        ).scalar_one()

        try:
            for position, model in enumerate(chain, start=1):
                conn.execute(
                    text(
                        "INSERT INTO tier_binding "
                        "    (tier, task, model, rank, effective_from) "
                        "VALUES (:tier, :task, :model, :rank, :eff)"
                    ),
                    {
                        "tier": req.tier,
                        "task": req.task,
                        "model": model,
                        "rank": position,
                        "eff": eff,
                    },
                )
        except IntegrityError:
            # An explicit effective_from equal to a saved row's violates the
            # (tier, task, rank, effective_from) PK — a retried "chain from
            # the 1st" POST used to 500 here.
            raise HTTPException(
                status_code=409,
                detail=(
                    "a chain for this (tier, task) already exists at that "
                    "exact effective_from; omit it to save a new row dated "
                    "now, or pass a later timestamp"
                ),
            ) from None

        _audit(
            conn,
            None,
            "catalog.binding",
            {"tier": req.tier, "task": req.task, "chain": chain},
            actor=staff.actor,
        )
    return {"tier": req.tier, "task": req.task, "model": chain[0], "chain": chain}


@app.post("/catalog/rates")
def set_rate(staff: Operator) -> dict[str, Any]:
    """RETIRED (D67, 2026-08-30). Customer prices are keyed on the TIER.

    🔴 **410, not a silent no-op and not a working write.** A model-keyed
    price stopped driving billing when `_rate_completion` moved to the tier
    card, so accepting a write here would store a number nothing reads — a
    control that looks armed and is not, which is the worst kind. The rows
    the table already holds stay readable history (R6: the table is not
    dropped in the release that stops writing it).

    Authenticated BEFORE refusing, like every route: the 410 is not an
    anonymous probe's oracle.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "Customer prices are keyed on the tier since D67 (2026-08-30). "
            "POST /catalog/tier-rates prices a (tier, task); model_rate_card "
            "is read-only history."
        ),
    )


class TierRateRequest(BaseModel):
    """What a customer pays for one (tier, task) — D67, the H-42 mechanism."""

    tier: str
    task: str
    unit: str
    pricing_mode: str
    #: ⚠️ **The per-thousand fields are the OLD scale and they still work**
    #: (migration 025, release one of two). A caller that has not moved yet —
    #: an unrestarted console, a script — keeps sending these and keeps
    #: pricing correctly. A later release removes them.
    input_per_1k: Decimal = Decimal(0)
    output_per_1k: Decimal = Decimal(0)
    cached_input_per_1k: Decimal = Decimal(0)
    #: 🔴 **The scale of record from 2026-09-04** (owner directive). Every
    #: vendor quotes per million, so the card now speaks the same unit as the
    #: cost it is derived from.
    #:
    #: ⚠️ `None` means "not sent", which is different from `0` meaning "free".
    #: A default of `Decimal(0)` here would make every old caller's price
    #: silently zero, because the writer prefers this field when it is set.
    input_per_1m: Decimal | None = None
    output_per_1m: Decimal | None = None
    cached_input_per_1m: Decimal | None = None
    credits_per_unit: Decimal = Decimal(0)
    #: When the price takes effect. NULL means now. Future-dating a price
    #: change is the mechanism for "new rates from the 1st".
    effective_from: datetime | None = None


#: 1000, named once. A bare literal appearing at several sites is how two
#: copies of one conversion eventually disagree.
_PER_1K = Decimal(1000)


def _tier_rate_scales(req: TierRateRequest) -> dict[str, Decimal]:
    """The three rates in per-MILLION terms, whichever scale the caller sent.

    🔴 **The per-million field wins when the caller sent one.** A caller on the
    new scale is stating the price it means, and multiplying its per-thousand
    default of zero would price the tier at nothing.

    ⚠️ **`is None`, never a falsy test.** Zero is a legitimate price — an
    absorbed task is free on purpose (D19.2) — and `or` would read it as
    "not sent" and silently reach for the other field.
    """
    return {
        "input": (
            req.input_per_1m if req.input_per_1m is not None
            else req.input_per_1k * _PER_1K
        ),
        "output": (
            req.output_per_1m if req.output_per_1m is not None
            else req.output_per_1k * _PER_1K
        ),
        "cached": (
            req.cached_input_per_1m if req.cached_input_per_1m is not None
            else req.cached_input_per_1k * _PER_1K
        ),
    }


@app.post("/catalog/tier-rates")
def set_tier_rate(req: TierRateRequest, staff: Operator) -> dict[str, Any]:
    """Price one `(tier, task)`. **INSERT, never UPDATE.** (D67)

    🔴 **Setting a real price is the OWNER's commercial act** (§8, D19.2, and
    H-42). This route is the MECHANISM, and building it prices nothing:
    migration 015 seeds no tier rates at all.

    ⚠️ The unit must be the task's own. `transcribe` is sold per minute of
    audio, and pricing it per 1k tokens produces a plausible wrong number
    rather than an error — which is why `task_catalog` carries `natural_unit`.

    ⚠️ The tier must exist in `tier_catalog`. A price for a tier that is not
    on the slate would bill nothing and confuse everybody reading the board.
    """
    with get_engine().begin() as conn:
        known_tier = conn.execute(
            text("SELECT 1 FROM tier_catalog WHERE slug = :s"),
            {"s": req.tier},
        ).first()
        if known_tier is None:
            raise HTTPException(
                status_code=400, detail=f"unknown tier {req.tier!r}; it is not in tier_catalog"
            )
        # D68: a price for the wrong KIND of job on this tier is refused for
        # the same reason the binding is - it could never bill anything the
        # tier serves.
        _check_tier_task(conn, tier=req.tier, task=req.task)
        natural = _task_unit(conn, req.task)
        if natural is None:
            raise HTTPException(status_code=400, detail=f"unknown task {req.task!r}")

        # ⚠️ Computed BEFORE validation, and the validator reads the derived
        # numbers. A caller on the per-million scale leaves the per-thousand
        # fields at their zero default, and `all_rates_zero` would then refuse
        # a legitimately priced card as "you priced nothing".
        per_1m = _tier_rate_scales(req)
        try:
            catalog.check_rate(
                catalog.TierRateProposal(
                    tier=req.tier,
                    task=req.task,
                    unit=req.unit,
                    pricing_mode=req.pricing_mode,
                    input_per_1k=per_1m["input"] / _PER_1K,
                    output_per_1k=per_1m["output"] / _PER_1K,
                    cached_input_per_1k=per_1m["cached"] / _PER_1K,
                    credits_per_unit=req.credits_per_unit,
                ),
                natural_unit=natural,
            )
        except catalog.CatalogRefused as exc:
            raise _catalog_refusal(exc) from exc

        # 🔴 **BOTH scales are written, and that is what makes the rollout
        # safe** (migration 025, release one of two). Old code reading
        # `_per_1k` finds its number. New code reading `_per_1m` finds its
        # own. A later release drops the per-thousand columns and this
        # doubling with them.
        #
        # ⚠️ Whichever scale the CALLER sent is the authority, and the other
        # is derived from it. Deriving both from one field keeps them exactly
        # 1000 apart by construction, so the two columns cannot drift into
        # disagreeing about one price.
        try:
            conn.execute(
                text(
                    "INSERT INTO tier_rate_card (tier, task, unit, "
                    "    input_credits_per_1k, output_credits_per_1k, "
                    "    cached_input_credits_per_1k, credits_per_unit, "
                    "    pricing_mode, effective_from, "
                    "    input_credits_per_1m, output_credits_per_1m, "
                    "    cached_input_credits_per_1m) "
                    "VALUES (:tr, :t, :u, :i, :o, :c, :cpu, :pm, "
                    "        COALESCE(:eff, now()), :i1m, :o1m, :c1m)"
                ),
                {
                    "tr": req.tier,
                    "t": req.task,
                    "u": req.unit,
                    "i": per_1m["input"] / _PER_1K,
                    "o": per_1m["output"] / _PER_1K,
                    "c": per_1m["cached"] / _PER_1K,
                    "cpu": req.credits_per_unit,
                    "pm": req.pricing_mode,
                    "eff": req.effective_from,
                    "i1m": per_1m["input"],
                    "o1m": per_1m["output"],
                    "c1m": per_1m["cached"],
                },
            )
        except IntegrityError:
            # Explicit effective_from duplicating the PK — a retried POST
            # used to 500 here instead of answering.
            raise HTTPException(
                status_code=409,
                detail=(
                    "a price for this (tier, task) already exists at that "
                    "exact effective_from; omit it to save a new row dated "
                    "now, or pass a later timestamp"
                ),
            ) from None
        _audit(
            conn,
            None,
            "catalog.tier_rate",
            {
                "tier": req.tier,
                "task": req.task,
                "pricing_mode": req.pricing_mode,
                "unit": req.unit,
            },
            actor=staff.actor,
        )
    return {"tier": req.tier, "task": req.task, "pricing_mode": req.pricing_mode}


class CreditPriceRequest(BaseModel):
    """What one credit sells for — the other half of H-42 (migration 017)."""

    inr_per_credit: Decimal
    #: INR per USD — the PLANNING rate margins convert vendor bills with,
    #: not a live FX feed. Saved so every operator reads the same margins.
    usd_to_inr: Decimal
    #: When the price takes effect. NULL means now. Future-dating a price
    #: change is the mechanism for "new price from the 1st".
    effective_from: datetime | None = None


@app.post("/catalog/credit-price")
def set_credit_price(req: CreditPriceRequest, staff: Operator) -> dict[str, Any]:
    """Price the credit itself. **INSERT, never UPDATE** — history stays.

    🔴 **The NUMBER is the owner's commercial act (H-42)**; this route is the
    mechanism, and building it prices nothing — migration 017 seeds no row.

    🔴 **Billing never reads what this writes.** A call bills credits; the
    tier card (D67) owns how many. This price sells the credits themselves:
    a bank transfer of Rs N buys N / inr_per_credit credits, granted on the
    customer's page. The fence is test_customer_console_credit_price.py::
    test_billing_never_reads_the_credit_price.

    ⚠️ Bounds mirror the table's own CHECK so the operator reads a named
    refusal instead of an IntegrityError's stack trace.
    """
    for name, v in (("inr_per_credit", req.inr_per_credit), ("usd_to_inr", req.usd_to_inr)):
        if not v.is_finite() or v <= 0 or v > 100_000:
            raise HTTPException(
                status_code=400, detail=f"{name} must be a positive number up to 100000, got {v}"
            )
    with get_engine().begin() as conn:
        try:
            conn.execute(
                text(
                    "INSERT INTO credit_price (inr_per_credit, usd_to_inr, "
                    "effective_from) "
                    "VALUES (:p, :fx, COALESCE(:eff, now()))"
                ),
                {"p": req.inr_per_credit, "fx": req.usd_to_inr, "eff": req.effective_from},
            )
        except IntegrityError:
            raise HTTPException(
                status_code=409,
                detail=(
                    "a credit price already exists at that exact "
                    "effective_from; omit it to save a new row dated now, "
                    "or pass a later timestamp"
                ),
            ) from None
        _audit(
            conn,
            None,
            "catalog.credit_price",
            {"inr_per_credit": str(req.inr_per_credit), "usd_to_inr": str(req.usd_to_inr)},
            actor=staff.actor,
        )
    return {"inr_per_credit": str(req.inr_per_credit), "usd_to_inr": str(req.usd_to_inr)}


def _task_exists(conn, task: str) -> bool:
    return (
        conn.execute(text("SELECT 1 FROM task_catalog WHERE slug = :t"), {"t": task}).first()
        is not None
    )


def _task_unit(conn, task: str) -> str | None:
    """What this task is measured in (``task_catalog.natural_unit``).

    ``None`` for a task the catalog does not carry. A refusal on an unknown
    task therefore records no unit, which is honest — nobody ever measured it.
    """
    return conn.execute(
        text("SELECT natural_unit FROM task_catalog WHERE slug = :t"),
        {"t": task},
    ).scalar_one_or_none()


def _check_tier_task(conn, *, tier: str, task: str) -> None:
    """Refuse a write that puts the wrong KIND of job on a tier (D68).

    Fires only when the registry categorises the tier - a ghost tier has no
    row and keeps the old freedom, and hiding a thing that serves is worse
    than the mismatch.
    """
    registered = conn.execute(
        text("SELECT task FROM tier_catalog WHERE slug = :s"), {"s": tier}
    ).first()
    if registered is None or registered[0] is None:
        return
    if registered[0] != task:
        raise HTTPException(
            status_code=400,
            detail=(
                f"tier {tier!r} serves {registered[0]!r}, not {task!r} "
                "(D68). Use the tier made for this job, or register a "
                "new one."
            ),
        )


class ProfileRequest(BaseModel):
    """What a model IS — window, output cap, what the vendor charges us.

    ⚠️ **Every measurement is OPTIONAL and defaults to `None`.** `None` means
    "nobody has told us", which the console draws as an em dash. A default of
    zero would render as "0 tokens" and "free", and both read as facts.

    🔴 **`extra="forbid"`, and H-78 is why it had to be added.** Pydantic
    ignores an unknown key by default, so a POST carrying
    `vendor_per_second_usd` answered 200 and stored nothing. That is the
    exact confusion this feature invites — the feed table holds a per-SECOND
    column and the profile holds a per-MINUTE one — and a silent success is
    the worst shape it could take. A misnamed price now answers 422 and names
    the field. The idiom matches the five request models above.
    """

    model_config = {"extra": "forbid"}

    model: str
    label: str | None = None
    #: ge mirrors `model_profile_positive` (012) and `model_profile_cached_
    #: positive` (013): a negative window or price used to pass pydantic and
    #: surface as an IntegrityError 500 — the same class `set_credit_price`
    #: already converts to a legible refusal.
    context_window: int | None = Field(default=None, ge=1)
    max_output: int | None = Field(default=None, ge=1)
    #: 🔴 What the VENDOR charges US, per million tokens. NOT `model_rate_card`,
    #: which is what we charge a customer. Reading one as the other inverts a
    #: margin, which is why the name carries both the payer and the unit.
    vendor_input_per_1m_usd: Decimal | None = Field(default=None, ge=0)
    vendor_output_per_1m_usd: Decimal | None = Field(default=None, ge=0)
    #: The vendor's discounted CACHE-READ rate (013). Without it a
    #: cache-hitting call cannot be costed at all — the computation refuses
    #: rather than bill cached reads at the full input price.
    vendor_cached_input_per_1m_usd: Decimal | None = Field(default=None, ge=0)
    #: 🔴 The per-unit costs (019, H-78), for the jobs a token price cannot
    #: cost: `transcribe`, `speak` and `image`. Each one carries the unit
    #: `task_catalog` (010) names for that task, so **this route converts
    #: nothing**. The x60 that turns litellm's per-SECOND transcription price
    #: into a per-MINUTE one already happened, once, in the feed-read
    #: projection (`_feed_wire`). A second conversion here would multiply the
    #: price by 3600, and the operator would never see why.
    #: ⚠️ **`lt` mirrors what the COLUMN can hold**, and the feed read applies
    #: the same ceiling on the way out. Without it a hand-typed `100000000`
    #: reached Postgres and answered an unhandled psycopg 500 rather than a
    #: refusal the operator could read. `_PER_UNIT_MAX` is the one number.
    vendor_per_minute_usd: Decimal | None = Field(default=None, ge=0, lt=_PER_UNIT_MAX)
    vendor_per_character_usd: Decimal | None = Field(default=None, ge=0, lt=_PER_UNIT_MAX)
    vendor_per_image_usd: Decimal | None = Field(default=None, ge=0, lt=_PER_UNIT_MAX)
    #: 🔴 The OFF-PEAK rates (024, `credit_pricing.md` §4.1). DeepSeek charges
    #: less for part of the day, so a call that ran cheap and a call that ran
    #: dear were recorded as costing the same.
    #:
    #: ⚠️ **The three fields above hold the PEAK rate**, and they keep their
    #: names because R6 forbids a rename in place. The vendor feed already
    #: fills them with the peak number.
    #:
    #: ⚠️ These change what a call COST us and never what a customer pays. D67
    #: keys the charge on the tier, so a window moves our margin and not their
    #: bill — and a tier PRICE derives from the peak rate always (owner
    #: directive, 2026-09-04, `pricing_window.pricing_basis`).
    vendor_input_offpeak_per_1m_usd: Decimal | None = Field(default=None, ge=0)
    vendor_output_offpeak_per_1m_usd: Decimal | None = Field(default=None, ge=0)
    vendor_cached_input_offpeak_per_1m_usd: Decimal | None = Field(default=None, ge=0)
    #: When the off-peak window opens and closes, in UTC, as `HH:MM`. Both or
    #: neither — a half-configured range cannot say whether the operator meant
    #: all day or nothing, and `model_profile_offpeak_range_complete` refuses
    #: it. The range MAY wrap midnight, and DeepSeek's does.
    offpeak_start_utc: str | None = None
    offpeak_end_utc: str | None = None
    #: 🔴 The LONG-CONTEXT rates (024). A vendor that charges roughly double
    #: past a threshold under-bills by half without these, on exactly the
    #: calls that cost most.
    context_tier_threshold: int | None = Field(default=None, ge=1)
    vendor_input_long_per_1m_usd: Decimal | None = Field(default=None, ge=0)
    vendor_output_long_per_1m_usd: Decimal | None = Field(default=None, ge=0)
    vendor_cached_input_long_per_1m_usd: Decimal | None = Field(default=None, ge=0)
    description: str = ""
    reads_images: bool = False
    thinks_first: bool = False

    @field_validator("offpeak_start_utc", "offpeak_end_utc")
    @classmethod
    def _a_window_bound_is_HH_MM(cls, v: str | None) -> str | None:
        """⚠️ Refuse a bound Postgres would read as a different time.

        A blank string is `None` — the operator cleared the box. Anything else
        must be `HH:MM`, because a value this route waves through reaches a
        `TIME` column and lands as whatever Postgres decides it meant.
        """
        if v is None or not v.strip():
            return None
        raw = v.strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", raw):
            raise ValueError(
                f"{raw!r} is not a time of day. Write HH:MM in UTC, "
                "for example 16:30. Clear the box to remove the window."
            )
        return raw

    @model_validator(mode="after")
    def _both_window_bounds_or_neither(self) -> ProfileRequest:
        """The API refuses what `model_profile_offpeak_range_complete` refuses.

        Refusing here as well turns an IntegrityError 500 into a 422 that
        names the field — the same reason `ge=0` mirrors the column CHECKs.
        """
        if (self.offpeak_start_utc is None) != (self.offpeak_end_utc is None):
            raise ValueError(
                "an off-peak window needs BOTH offpeak_start_utc and "
                "offpeak_end_utc, or neither. One bound alone cannot say "
                "whether you meant all day or nothing."
            )
        return self

    @field_validator(
        "vendor_per_minute_usd",
        "vendor_per_character_usd",
        "vendor_per_image_usd",
    )
    @classmethod
    def _too_small_is_refused_not_rounded(cls, v: Decimal | None) -> Decimal | None:
        """🔴 A nonzero price that rounds to zero at scale 10 is REFUSED.

        `NUMERIC(18, 10)` quantizes on the way in, so `0.00000000001` stores
        as `0E-10` and the board then reports the model as "listed at $0".
        Free is a real and meaningful state, so a fabricated one is not a
        rounding detail — it is a wrong fact about a vendor on the screen
        an operator prices from.

        ⚠️ **This mirrors `feed._per_unit`'s rule, which answers NULL for the
        same input.** The two sides differ on the ANSWER and agree on the
        JUDGEMENT: a price below what the column can hold is not a price.
        The feed is a cache and may say "unknown". A person typing into a
        form gets told, because they can fix it.
        """
        if v is None or v == 0:
            return v
        if v.quantize(_PER_UNIT_SCALE) == 0:
            raise ValueError(
                "this price is smaller than the column can hold "
                "(10 decimal places), so it would store as 0 and read as "
                "free. Record 0 if the model really is free."
            )
        return v


@app.post("/catalog/profiles")
def set_model_profile(req: ProfileRequest, staff: Operator) -> dict[str, Any]:
    """Record what a model IS. **UPSERT, and that is deliberate.**

    🔴 **`Operator`, and it was `CatalogCaller` until 2026-08-30 - a live
    500 on every staff save.** `customer_or_operator` returns None on the
    OPERATOR arm by design (it exists for the plan-catalog read, which binds
    `_`), so `staff.actor` below crashed for every operator, session and
    break-glass alike - the owner hit it adding assemblyai/best from the
    feed, and no profile had ever been saved through the console. The same
    door also admitted a CUSTOMER key (`can_pay`) to write OUR reference
    data, which D66 forbids in spirit: the customer never brings a model.
    One dependency fixes both. The §5 matrix row (EDITOR) was already
    right; the door just did not match it.

    ⚠️ **The only catalog write that is not insert-only**, and the difference is
    the point. A tier binding and a rate card are commercial decisions, so a
    past invoice must stay readable against the one that produced it. A context
    window is a fact about the world — nobody is owed a history of what a
    vendor's documentation said last month.

    ⚠️ **`editor`, and NO elevation window.** Every other catalog write demands
    `admin` plus elevation because it changes what runs or what we charge. This
    changes neither: it is reference data an operator reads before choosing.
    Gating a description edit behind elevation would teach people to reach for
    the break-glass token for routine work, which is the opposite of what §5 is
    for.

    ⚠️ **This route is a PASS-THROUGH, and it does no arithmetic** (H-78).
    Every price arrives in the unit the column keeps. The one unit change in
    this feature — per second to per minute — happens in the feed-read
    projection, once. A second conversion here would be silent and wrong.

    ⚠️ **The model does not have to exist in `model_capability` yet.** Writing
    the profile first and declaring the capability second is a legitimate order
    — an operator researching models fills these in before deciding which to
    connect — and a foreign key would forbid it for no benefit.
    """
    model = (req.model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")

    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO model_profile (
                    model, label, context_window, max_output,
                    vendor_input_per_1m_usd, vendor_output_per_1m_usd,
                    vendor_cached_input_per_1m_usd,
                    vendor_per_minute_usd, vendor_per_character_usd,
                    vendor_per_image_usd,
                    vendor_input_offpeak_per_1m_usd,
                    vendor_output_offpeak_per_1m_usd,
                    vendor_cached_input_offpeak_per_1m_usd,
                    offpeak_start_utc, offpeak_end_utc,
                    context_tier_threshold,
                    vendor_input_long_per_1m_usd,
                    vendor_output_long_per_1m_usd,
                    vendor_cached_input_long_per_1m_usd,
                    description, reads_images, thinks_first, updated_at
                ) VALUES (
                    :model, :label, :ctx, :out, :vin, :vout, :vcached,
                    :vmin, :vchar, :vimg,
                    :vin_off, :vout_off, :vcached_off,
                    CAST(:off_start AS TIME), CAST(:off_end AS TIME),
                    :ctx_threshold, :vin_long, :vout_long, :vcached_long,
                    :descr, :imgs, :think, now()
                )
                ON CONFLICT (model) DO UPDATE SET
                    label = EXCLUDED.label,
                    context_window = EXCLUDED.context_window,
                    max_output = EXCLUDED.max_output,
                    vendor_input_per_1m_usd = EXCLUDED.vendor_input_per_1m_usd,
                    vendor_output_per_1m_usd = EXCLUDED.vendor_output_per_1m_usd,
                    vendor_cached_input_per_1m_usd =
                        EXCLUDED.vendor_cached_input_per_1m_usd,
                    vendor_per_minute_usd = EXCLUDED.vendor_per_minute_usd,
                    vendor_per_character_usd =
                        EXCLUDED.vendor_per_character_usd,
                    vendor_per_image_usd = EXCLUDED.vendor_per_image_usd,
                    vendor_input_offpeak_per_1m_usd =
                        EXCLUDED.vendor_input_offpeak_per_1m_usd,
                    vendor_output_offpeak_per_1m_usd =
                        EXCLUDED.vendor_output_offpeak_per_1m_usd,
                    vendor_cached_input_offpeak_per_1m_usd =
                        EXCLUDED.vendor_cached_input_offpeak_per_1m_usd,
                    offpeak_start_utc = EXCLUDED.offpeak_start_utc,
                    offpeak_end_utc = EXCLUDED.offpeak_end_utc,
                    context_tier_threshold = EXCLUDED.context_tier_threshold,
                    vendor_input_long_per_1m_usd =
                        EXCLUDED.vendor_input_long_per_1m_usd,
                    vendor_output_long_per_1m_usd =
                        EXCLUDED.vendor_output_long_per_1m_usd,
                    vendor_cached_input_long_per_1m_usd =
                        EXCLUDED.vendor_cached_input_long_per_1m_usd,
                    description = EXCLUDED.description,
                    reads_images = EXCLUDED.reads_images,
                    thinks_first = EXCLUDED.thinks_first,
                    updated_at = now()
                """
            ),
            {
                "model": model,
                "label": (req.label or "").strip() or None,
                "ctx": req.context_window,
                "out": req.max_output,
                "vin": req.vendor_input_per_1m_usd,
                "vout": req.vendor_output_per_1m_usd,
                "vcached": req.vendor_cached_input_per_1m_usd,
                # Straight through, unconverted. See ProfileRequest above.
                "vmin": req.vendor_per_minute_usd,
                "vchar": req.vendor_per_character_usd,
                "vimg": req.vendor_per_image_usd,
                # 023 — the window and the context tier. Straight through,
                # like every price above: the route does no arithmetic.
                "vin_off": req.vendor_input_offpeak_per_1m_usd,
                "vout_off": req.vendor_output_offpeak_per_1m_usd,
                "vcached_off": req.vendor_cached_input_offpeak_per_1m_usd,
                "off_start": req.offpeak_start_utc,
                "off_end": req.offpeak_end_utc,
                "ctx_threshold": req.context_tier_threshold,
                "vin_long": req.vendor_input_long_per_1m_usd,
                "vout_long": req.vendor_output_long_per_1m_usd,
                "vcached_long": req.vendor_cached_input_long_per_1m_usd,
                "descr": (req.description or "").strip(),
                "imgs": req.reads_images,
                "think": req.thinks_first,
            },
        )
        _audit(conn, None, "catalog.profile", {"model": model}, actor=staff.actor)
    return {"model": model}


@app.get("/providers/credentials")
def list_provider_credentials(staff: Operator, include_revoked: bool = False) -> dict[str, Any]:
    """Which providers we hold an account with. **Never the secret.**

    Done-when 2. The plaintext is not returned here and it cannot be: the
    query does not select `secret_enc`, so there is nothing to leak.
    """
    with get_engine().begin() as conn:
        rows = store.provider_credential_list(conn, include_revoked=include_revoked)
    return {
        "credentials": [
            {
                "id": str(r["id"]),
                "provider": r["provider"],
                "api_base": r["api_base"],
                "label": r["label"],
                # NULL org means the PLATFORM account, which is the common
                # case and the one the Router falls back to.
                "org_slug": r["org_slug"],
                "scope": "byok" if r["org_slug"] else "platform",
                "created_at": _iso(r["created_at"]),
                "revoked_at": _iso(r["revoked_at"]),
            }
            for r in rows
        ]
    }


@app.post("/providers/credentials")
def install_provider_credential(req: ProviderCredentialRequest, staff: Operator) -> dict[str, Any]:
    """Install a provider credential. Fernet at rest, INSERT only.

    Done-when 1: after this route runs, `router.provider_credential()` returns
    the key — on a database where nothing seeded it.

    ⚠️ **Rotation is this same route.** The partial unique index allows one
    LIVE credential per (provider, org), so installing over a live one revokes
    the old row first, in the SAME transaction. That is a rotation: the old
    key stops being used, its row survives for the record, and there is never
    a moment with two live keys or none.
    """
    try:
        provider = provider_keys.check_provider(req.provider)
        secret = provider_keys.check_secret(req.secret)
        api_base = provider_keys.check_api_base(req.api_base)
    except provider_keys.CredentialRefused as exc:
        raise _credential_refusal(exc) from exc

    # Encrypted BEFORE the transaction opens. An unset encryption key must
    # fail before anything is written, not half way through a rotation.
    try:
        secret_enc = encrypt_secret(secret)
    except RuntimeError as exc:
        # `CUSTOMER_CONSOLE_ENCRYPTION_KEY` is unset. 503 and not 500: the box
        # is unconfigured, which is a different incident from a bug.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug) if req.org_slug else None
        rotated = store.provider_credential_revoke(conn, provider=provider, organization_id=org_id)
        credential_id = store.provider_credential_insert(
            conn,
            provider=provider,
            secret_enc=secret_enc,
            api_base=api_base,
            label=req.label,
            organization_id=org_id,
        )
        # ⚠️ The audit row records the PROVIDER and the LABEL, never the key
        # and never a fragment of it. `key.issue` set that precedent and
        # `test_operator_activity.py` fences the trail as a whole.
        _audit(
            conn,
            org_id,
            "provider.credential.install",
            {"provider": provider, "label": req.label, "rotated": rotated, "api_base": api_base},
            actor=staff.actor,
        )

    return {
        "id": credential_id,
        "provider": provider,
        "scope": "byok" if org_id else "platform",
        # What an operator needs to know afterwards: did this replace a live
        # key, or add the first one?
        "rotated": rotated,
    }


@app.post("/providers/credentials/revoke")
def revoke_provider_credential(
    req: ProviderCredentialRevokeRequest, staff: Operator
) -> dict[str, Any]:
    """Revoke the live credential for a provider. **The row survives.**

    ⚠️ Revoking the PLATFORM credential stops every AI call that is not BYOK.
    That is the intended behaviour of a revocation and it is why this route is
    admin-and-elevated, but it is worth saying out loud in the one place
    somebody will read before running it.
    """
    try:
        provider = provider_keys.check_provider(req.provider)
    except provider_keys.CredentialRefused as exc:
        raise _credential_refusal(exc) from exc

    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug) if req.org_slug else None
        revoked = store.provider_credential_revoke(conn, provider=provider, organization_id=org_id)
        _audit(
            conn,
            org_id,
            "provider.credential.revoke",
            {"provider": provider, "revoked": revoked},
            actor=staff.actor,
        )
    return {"provider": provider, "revoked": revoked}


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness. Deliberately unauthenticated and deliberately says nothing."""
    return {"status": "ok"}


@app.post("/orgs/provision")
def provision(req: ProvisionRequest, caller: ProvisionCaller, request: Request) -> dict[str, Any]:
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
                    detail=("deployment_label is required under the operator scheme"),
                )
            deployment_id = store.deployment_by_label(conn, label=req.deployment_label)
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
            conn,
            slug=req.slug,
            name=req.name,
            gstin=req.gstin,
            billing_state=req.billing_state,
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
                raise HTTPException(status_code=409, detail="slug unavailable")
        store.place_organization(conn, org_id=org_id, deployment_id=deployment_id)

        identity_id = store.ensure_identity(conn, email=req.owner_email)

        # Only grant on FIRST provision — a retry must not keep buying seats.
        grants, _assigned = store.seat_rows(conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG)
        if not grants:
            store.grant_seats(
                conn,
                org_id=org_id,
                plan_slug=CORE_PLAN_SLUG,
                quantity=req.core_seats,
                reason="provision",
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
            conn,
            org_id=org_id,
            plan_slug=CORE_PLAN_SLUG,
            identity_id=identity_id,
            source="core",
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
        _audit(
            conn,
            org_id,
            "org.provision",
            detail,
            # CP-12c CLOSES CP-12b's known gap. This is a DUAL-ARM
            # door, so `caller is None` means the operator arm ran —
            # and `auth._stash` put that arm's identity on the request.
            # `getattr` rather than a bare attribute because the
            # deployment arm never sets it.
            actor=(
                getattr(getattr(request, "state", None), "staff", None).actor
                if caller is None
                else "deployment"
            ),
        )

    return {"organization_id": org_id, "slug": req.slug}


@app.post("/orgs/lifecycle")
def set_lifecycle(req: LifecycleRequest, staff: Operator) -> dict[str, Any]:
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
            if req.target == "cancelled"
            else "NULL"
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
            text(
                "UPDATE org_subscription SET status = :s, updated_at = now() "
                "WHERE organization_id = :i AND :s <> 'deleted'"
            ),
            {"s": req.target, "i": org_id},
        )
        _audit(
            conn,
            org_id,
            "org.lifecycle",
            {"from": current, "to": req.target, "reason": req.reason},
            actor=staff.actor,
        )

        caps = capabilities_of(req.target)

    return {
        "slug": req.org_slug,
        "from": current,
        "to": req.target,
        "can_sign_in": caps.can_sign_in,
        "can_use_ai": caps.can_use_ai,
        "can_write_seats": caps.can_write_seats,
        "data_retained": caps.data_retained,
    }


#: What `/orgs/purge` deletes vs keeps vs scrubs, module-level so the receipt
#: and the suite pin the SAME lists (the N8 `_PURGE_DELETES`/`_PURGE_KEEPS`
#: idiom). Deleted = personal data (emails, identity links), live secrets,
#: and per-org operational state (`org_placement`, `provisioning_run` — the
#: latter also because its `provision:{slug}` idempotency key would otherwise
#: re-attribute the OLD org's provisioning history to a NEW org that takes
#: the freed slug). Kept = the financial record — a purge is entitled to take
#: the people, never the books. SCRUBBED = kept rows whose columns carry an
#: email: the row stays for the books, the address does not (review round 1,
#: P1 — `usage_event.user_email` and `control_audit.detail` survived the
#: first draft, contradicting this very comment).
#:
#: ⚠️ `user_identity` is KEPT and named: it is global and cross-org (three
#: FKs), and an identity with no memberships is D51's org-less sign-in. An
#: erasure request for a PERSON (as opposed to an org) is a different act and
#: not this door.
#:
#: `TestTheClassificationCannotGoStale` in `test_org_purge_console.py`
#: re-derives the full set of org-scoped Console tables from
#: information_schema and pins DELETES union KEEPS_TABLES equal to it, so a
#: new table cannot land in neither list silently.
_ORG_PURGE_DELETES: tuple[str, ...] = (
    "seat_assignment",
    "member_ai_cap",
    "org_membership",
    "llm_api_key",
    "provider_credential",
    "org_placement",
    "provisioning_run",
)
#: The org-scoped tables the purge keeps (the fence's other half). Prose for
#: the receipt lives in `_ORG_PURGE_KEEPS`; this is the machine-checkable set.
_ORG_PURGE_KEEPS_TABLES: tuple[str, ...] = (
    "organization",
    "org_subscription",
    "seat_grant",
    "credit_ledger",
    # 🔴 KEPT, and the foreign key makes it the only possible answer.
    # `credit_ledger.lot_id` references this table and the ledger is kept, so
    # deleting lots would either break that reference or force a cascade that
    # took the financial history with it.
    #
    # It is right on its own terms too: a lot records what credits COST and
    # when they lapsed, which is the evidence a refund argument needs after an
    # account is gone. It holds no personal data — a source, an amount, a
    # price and a date.
    "credit_lot",
    "payment_order",
    "usage_event",
    "usage_rollup",
    "control_audit",
    "discount_code",
    "discount_redemption",
)
_ORG_PURGE_KEEPS: tuple[str, ...] = (
    "organization (tombstone row, slug renamed)",
    "org_subscription",
    "seat_grant",
    "credit_ledger",
    "credit_lot (what the credits cost, for a later refund argument)",
    "payment_order",
    "usage_event (user_email scrubbed)",
    "usage_rollup",
    "control_audit (email keys scrubbed from detail)",
    "discount_code",
    "discount_redemption",
    "user_identity (global, cross-org — emails remain here)",
)
#: The jsonb keys under which Console audit details carry an address
#: (`member.add`, `seat.assign`/`release`, `org.provision`). Stripped, not
#: rewritten — an absent key reads as scrubbed, a fake value reads as data.
_AUDIT_EMAIL_KEYS: tuple[str, ...] = (
    "email",
    "owner_email",
    "member_email",
    "actor_email",
    "user_email",
)

#: The detail-strip expression, built ONCE from the module constant above —
#: the operands are this tuple, never request input, which is what makes the
#: interpolation below static SQL rather than construction from data.
_AUDIT_DETAIL_STRIP_SQL = (
    "UPDATE control_audit SET detail = detail - "
    + " - ".join(f"'{k}'" for k in _AUDIT_EMAIL_KEYS)
    + " WHERE organization_id = :i AND detail ?| :keys"
)

#: `control_audit.actor` is an EMAIL under the deployment-key scheme
#: (`_admin_scheme_for_deployment` returns the acting admin's address, and
#: `member.add`/`seat.assign`/`seat.release` write it straight into the
#: column) — repair round 2's blocking find: the first scrub covered `detail`
#: and left the address sitting one column over. The column is NOT NULL, so
#: email-shaped actors are OVERWRITTEN with this placeholder; `'operator'`
#: and other role-words carry no address and stay.
_ACTOR_PURGED = "[purged]"

_TOMBSTONE_RE = r"-purged-[0-9a-f]{6}$"


@app.post("/orgs/purge")
def purge_org_registry(req: OrgPurgeRequest, staff: Operator) -> dict[str, Any]:
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
    # A tombstone is the RESULT of a purge, never its subject — without this,
    # each press on a lingering `deleted` row appends another `-purged-` suffix
    # and mints another audit row (measured, review round 1).
    if re.search(_TOMBSTONE_RE, req.org_slug):
        raise HTTPException(
            status_code=409,
            detail="this organization is already purged (tombstone row)",
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
        # Scrub, don't delete: the books keep the usage and the audit trail,
        # the addresses go. Key-stripping (not rewriting) so an absent key
        # reads as scrubbed rather than as fake data.
        scrubbed: dict[str, int] = {}
        scrubbed["usage_event.user_email"] = conn.execute(
            text(
                "UPDATE usage_event SET user_email = NULL "
                "WHERE organization_id = :i AND user_email IS NOT NULL"
            ),
            {"i": org_id},
        ).rowcount
        scrubbed["control_audit.detail"] = conn.execute(
            text(_AUDIT_DETAIL_STRIP_SQL),
            {"i": org_id, "keys": list(_AUDIT_EMAIL_KEYS)},
        ).rowcount
        scrubbed["control_audit.actor"] = conn.execute(
            text(
                "UPDATE control_audit SET actor = :p "
                "WHERE organization_id = :i AND actor LIKE '%@%'"
            ),
            {"i": org_id, "p": _ACTOR_PURGED},
        ).rowcount
        tombstone = f"{req.org_slug}-purged-{uuid.uuid4().hex[:6]}"
        conn.execute(
            text("UPDATE organization SET slug = :t, updated_at = now() WHERE id = :i"),
            {"t": tombstone, "i": org_id},
        )
        _audit(
            conn,
            org_id,
            "org.purge",
            {
                "slug": req.org_slug,
                "tombstone": tombstone,
                "deleted": deleted,
                "scrubbed": scrubbed,
            },
            actor=staff.actor,
        )
    # ⚠️ THE AUDIT ROW IS A RECORD, NOT A NOTIFICATION. `_audit` writes to
    # `control_audit` and nothing else, so a purge reaches the other admins
    # only when somebody thinks to look. DEF-5 states the problem in its own
    # trigger: "Nobody reads a log until it alerts."
    #
    # This is the loudest thing this door can do TODAY. DEF-7 decided the
    # shape deliberately: the Resend seam lives in the GATEWAY, and reaching
    # across a service boundary for one message would put a second email seam
    # inside the Console. So the log line is the durable record and the thing
    # an alert rule fires on.
    #
    # ⚠️ HONEST LIMIT: nothing watches these logs yet, so this alerts NOBODY
    # today. It earns its place twice anyway — `journalctl -u
    # acb-customer-console` names the act during an incident, and purge is
    # already covered on the day log alerting arrives (DEF-7's own trigger).
    # It is not four-eyes and does not close H-93.
    #
    # Outside the transaction on purpose: the books are committed by here, so
    # a logging fault can never roll back a completed purge.
    _log.critical(
        "org.purge",
        extra={
            "purge_slug": req.org_slug,
            "purge_tombstone": tombstone,
            "purge_actor": staff.actor,
            "purge_deleted_rows": sum(deleted.values()),
            "purge_scrubbed_rows": sum(scrubbed.values()),
        },
    )
    return {
        "slug": req.org_slug,
        "tombstone": tombstone,
        "deleted": deleted,
        "scrubbed": scrubbed,
        "kept": list(_ORG_PURGE_KEEPS),
    }


@app.post("/registry/resolve")
def resolve(
    req: ResolveRequest,
    caller: ResolveCaller,
    request: Request,
) -> dict[str, Any]:
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
    return _resolve_for_operator(req, request)


def _resolve_for_operator(
    req: ResolveRequest,
    request: Request,
) -> dict[str, Any]:
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
            raise HTTPException(status_code=403, detail=f"organization is {state}")

        identity_id = store.ensure_identity(conn, email=req.email, display_name=req.display_name)

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
            conn,
            org_id=org_id,
            identity_id=identity_id,
            seats_locked=False,
        )
        # A staff act that mints an identity and can consume a paid Core
        # seat — audited like its /billing/seats twin (it never was). The
        # deployment arm stays unaudited on purpose: it runs on every
        # product sign-in and belongs to the meter, not the audit trail.
        staff = getattr(getattr(request, "state", None), "staff", None)
        _audit(
            conn,
            org_id,
            "registry.resolve",
            {"email": req.email},
            actor=getattr(staff, "actor", None) or "operator",
        )

        role = conn.execute(
            text(
                "SELECT role, status FROM org_membership "
                "WHERE organization_id = :org AND user_identity_id = :i"
            ),
            {"org": org_id, "i": identity_id},
        ).first()
        seats = [
            r[0]
            for r in conn.execute(
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


def _resolve_for_deployment(req: ResolveRequest, caller: DeploymentCaller) -> dict[str, Any]:
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
        admissible = [o for o in visible if capabilities_of(o["status"]).can_sign_in]
        refused = [o for o in visible if not capabilities_of(o["status"]).can_sign_in]

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

        identity_id = store.ensure_identity(conn, email=req.email, display_name=req.display_name)

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
                conn,
                org_id=org["organization_id"],
                identity_id=identity_id,
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
                        conn,
                        org_id=org["organization_id"],
                        plan_slug=CORE_PLAN_SLUG,
                        identity_id=identity_id,
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


def _allocate_core_seat(conn, *, org_id: str, identity_id: str, seats_locked: bool) -> str:
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
            conn,
            org_id=org_id,
            plan_slug=CORE_PLAN_SLUG,
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
    grants, assigned = store.seat_rows(conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG)
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
        conn,
        org_id=org_id,
        plan_slug=CORE_PLAN_SLUG,
        identity_id=identity_id,
        source="core",
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
        # 🔴 What the balance CANNOT say (`credit_pricing.md` §6): what these
        # credits cost, when they lapse, and whether anybody paid for them.
        lots = store.open_lots(conn, org_id=org_id)

    return {
        "organization_id": org_id,
        "seats": seats,
        "credit_balance": str(balance),
        # ⚠️ Money as STRINGS, the same rule every price on this wire follows.
        # ⚠️ `price_paid_inr` stays NULL where nobody paid — a grant and a
        # zero-priced promotion are different facts and the console draws them
        # differently.
        "credit_lots": [
            {
                "id": lot.id,
                "source": lot.source,
                "credits": str(lot.credits),
                "credits_used": str(lot.credits_used),
                "remaining": str(lot.remaining),
                "price_paid_inr": (
                    None if lot.price_paid_inr is None else str(lot.price_paid_inr)
                ),
                "expires_at": _iso(lot.expires_at),
            }
            # The order they will BURN in, not an arbitrary one. An operator
            # reading this list is reading the next thing to be spent.
            for lot in lots
        ],
        "members": [{**row, "seats": held.get(row["email"], [])} for row in roster],
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
            counts = seat_counts(line["plan_slug"], line["grants"], line["assigned"])
            seats.append(
                SeatPlanView(
                    plan_slug=counts.plan_slug,
                    purchased=counts.purchased,
                    assigned=counts.assigned,
                    available=counts.available,
                    oversubscribed=counts.oversubscribed,
                )
            )
            mrr_inr += counts.purchased * line["price_inr"]

        active = r["subscription_status"] == "active"
        organizations.append(
            OrgSummaryView(
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
            )
        )

    return OrgListView(organizations=organizations)


@app.post("/billing/subscriptions/activate")
def activate_subscription_manual(req: ManualActivationRequest, staff: Operator) -> dict[str, Any]:
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
            text("SELECT status FROM org_subscription WHERE organization_id = :i"),
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
            conn,
            org_id=org_id,
            term_months=term,
            provider="manual",
            provider_customer_id=None,
            provider_subscription_id=None,
        )
        store.grant_seats(
            conn,
            org_id=org_id,
            plan_slug=req.plan_slug,
            quantity=req.seats,
            reason="manual",
        )
        if req.credits is not None:
            # The OTHER door that writes the ledger — same amount rules as
            # /credits/grant (spec §5). Without this, an editor refused a
            # 15,001-credit grant attached 1,000,000 to an activation.
            # Raising here rolls the whole activation back: all or nothing.
            _require_credit_privilege(staff, req.credits)
            store.add_credit(
                conn,
                org_id=org_id,
                delta=req.credits,
                reason=LEDGER_REASON_MANUAL,
                ref=req.reference,
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
        _audit(conn, org_id, "subscription.activate_manual", detail, actor=staff.actor)

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
def assign_seat(req: SeatWriteRequest, staff: Operator) -> dict[str, Any]:
    """Assign a seat on a plan. 409 at the cap, with a buy-more payload."""
    if req.source not in _SEAT_SOURCES:
        # Mirrors seat_assignment.source's CHECK (001:193) so a typo answers
        # 400 here, not an IntegrityError 500 at the insert — the rule the
        # self-serve door already applies to its narrower set.
        raise HTTPException(
            status_code=400,
            detail=(
                f"{req.source!r} is not a seat source; expected one of {sorted(_SEAT_SOURCES)}"
            ),
        )
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        state = conn.execute(
            text("SELECT status FROM organization WHERE id = :i"), {"i": org_id}
        ).scalar_one()
        if not capabilities_of(state).can_write_seats:
            raise HTTPException(
                status_code=403, detail=f"organization is {state}; seats are locked"
            )

        identity_id = store.ensure_identity(conn, email=req.email)
        held = store.has_live_seat(
            conn, org_id=org_id, plan_slug=req.plan_slug, identity_id=identity_id
        )
        grants, assigned = store.seat_rows(conn, org_id=org_id, plan_slug=req.plan_slug)
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
            conn,
            org_id=org_id,
            plan_slug=req.plan_slug,
            identity_id=identity_id,
            source=req.source,
        )
        _audit(
            conn,
            org_id,
            "seat.assign",
            {"email": req.email, "plan": req.plan_slug},
            actor=staff.actor,
        )

    return {"assigned": True, "plan_slug": req.plan_slug}


@app.post("/billing/seats/release")
def release_seat(req: SeatWriteRequest, staff: Operator) -> dict[str, Any]:
    """Release a seat. Frees capacity immediately (D19.3)."""
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        identity_id = store.ensure_identity(conn, email=req.email)
        released = store.release_seat(
            conn, org_id=org_id, plan_slug=req.plan_slug, identity_id=identity_id
        )
        _audit(
            conn,
            org_id,
            "seat.release",
            {"email": req.email, "plan": req.plan_slug, "released": released},
            actor=staff.actor,
        )

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
    request: Request,
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
    return _admin_scheme_for_operator(conn, req, request)


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
    admissible = [o for o in visible if capabilities_of(o["status"]).can_sign_in]
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
    if membership is None or membership[0] not in roles or membership[1] != "active":
        raise HTTPException(
            status_code=403,
            detail="the acting member is not an active admin of this organization",
        )
    return org_id, req.actor_email


def _admin_scheme_for_operator(
    conn,
    req: AdminSchemeRequest,
    request: Request,
) -> tuple[str, str]:
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
    # CP-12c's fix, extended to these doors 2026-08-30: `auth._stash`
    # put the signed-in identity on the request, and the three /registry
    # writes discarded it — their audit rows read actor="operator",
    # indistinguishable from anybody. Break-glass still records "operator".
    staff = getattr(getattr(request, "state", None), "staff", None)
    return _org_id(conn, req.org_slug), (getattr(staff, "actor", None) or "operator")


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
    req: SeatAdminRequest,
    caller: SeatAdminCaller,
    request: Request,
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
            conn, req, caller, roles=_SEAT_ADMIN_ROLES, request=request
        )
        target_id = _seat_admin_target(conn, org_id=org_id, member_email=req.member_email)

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
        grants, assigned = store.seat_rows(conn, org_id=org_id, plan_slug=req.plan_slug)
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
            conn,
            org_id=org_id,
            plan_slug=req.plan_slug,
            identity_id=target_id,
            source=req.source,
        )
        _audit(
            conn,
            org_id,
            "seat.assign",
            {"email": req.member_email, "plan": req.plan_slug},
            actor=actor,
        )

    return {"assigned": True, "plan_slug": req.plan_slug}


@app.post("/registry/seats/release")
def release_seat_admin(
    req: SeatAdminRequest,
    caller: SeatAdminCaller,
    request: Request,
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
            conn, req, caller, roles=_SEAT_ADMIN_ROLES, request=request
        )
        target_id = _seat_admin_target(conn, org_id=org_id, member_email=req.member_email)
        released = store.release_seat(
            conn, org_id=org_id, plan_slug=req.plan_slug, identity_id=target_id
        )
        _audit(
            conn,
            org_id,
            "seat.release",
            {"email": req.member_email, "plan": req.plan_slug, "released": released},
            actor=actor,
        )

    return {"released": released}


# ── CP-2h · the customer-authenticated seat READ (D-SEAT-4) ─────────────────
#
# The read-side twin of the two writes above, on the SAME `seat_admin`
# capability: one door per plane. `GET /me/seats` + `GET /me/members` remain the
# ORGANIZATION-key reads for the per-org billing pages; this is the same picture
# under the credential a SHARED deployment can actually hold.


@app.post("/registry/seats/overview")
def seat_overview_admin(
    req: AdminSchemeRequest,
    caller: SeatAdminCaller,
    request: Request,
) -> SeatOverviewView:
    """The seat surface's READ under the customer's own admin credential (CP-2h).

    **The door split this closes — D-SEAT-4** (``customer_console.md`` §6 CP-2h).
    Until now the ``seat_admin`` capability opened two WRITES and no read, so the
    customer's Seats tab had to compose its own picture from the two
    ORGANIZATION-KEY reads ``GET /me/seats`` + ``GET /me/members``. That works on
    a box dedicated to one tenant and cannot work on a shared one: a ``cc_live_``
    key **is** an organization (CP-3), so a deployment hosting N tenants has no
    single correct key to hold, the per-org env var is unset, and the tab fails
    closed to "not configured for this deployment" — the state the owner
    photographed on 2026-08-24. **One plane, one door:** the writes already reach
    this service through the gateway's deployment-key hop, and after this so does
    the read. The org-key reads stay exactly as they are for the billing pages,
    which are per-org surfaces by construction.

    **Why POST for a read.** The actor travels in the body precisely as the two
    sibling writes send it (:class:`AdminSchemeRequest`), so R11's derivation is
    the SAME code on all three doors — ``_admin_scheme_context`` with
    ``_SEAT_ADMIN_ROLES``. A GET would have to carry the acting member in a query
    string or a header, i.e. a second way to say who is asking, on the one axis
    where a second way is how a derivation quietly becomes an assertion. Nothing
    here writes: no ledger row, no seat, no membership, no audit line.

    **Same gate as the writes, deliberately.** The Seats tab is an admin surface —
    it exists to move seats — so "may read the roster and the counts" is the same
    question as "may move a seat", and answering it with a second, looser role set
    would mint a policy nobody asked for. A non-admin member is refused here for
    the same reason they are refused at the write: 403, byte-identical, from the
    one shared derivation.

    **The org is the ANSWER, never the request** (R11): it comes from
    ``deployment_visible_orgs(deployment_id, actor_email)`` — placement ∩
    membership — so org A's admin cannot name org B, and a ``deleted``
    organization is inadmissible (``capabilities_of(...).can_sign_in``) exactly as
    it is at the writes. A ``suspended`` organization CAN read: it is the one
    deciding whether to buy more seats, so it must be able to see them — the
    ``my_seats`` / ``billing_catalog`` reasoning (§9.3(5)), and the reason no
    ``can_write_seats`` gate appears on a read.

    Both queries plus the grid run in ONE transaction, so the counts and the
    roster are a consistent snapshot — ``my_members``'s argument, extended to the
    grid: a seat released between two connections would otherwise surface as a
    member holding a seat the counts no longer show.
    """
    with get_engine().begin() as conn:
        org_id, _actor = _admin_scheme_context(
            conn, req, caller, roles=_SEAT_ADMIN_ROLES, request=request
        )
        rows = store.org_members(conn, org_id=org_id)
        seats = store.live_seats_by_email(conn, org_id=org_id)
        return SeatOverviewView(
            plans=_seat_grid(conn, org_id),
            members=[MemberView(**row, seats=seats.get(row["email"], [])) for row in rows],
        )


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
    req: MemberAdminRequest,
    caller: MemberAdminCaller,
    request: Request,
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
            conn, req, caller, roles=_MEMBER_ADMIN_ROLES, request=request
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
        created, status_now = store.add_invited_member(conn, org_id=org_id, identity_id=identity_id)
        # Audited on BOTH paths, and the payload says which — a re-invite that
        # changed nothing is a real event an operator may need to see beside the
        # one that did.
        _audit(
            conn,
            org_id,
            "member.add",
            {"email": req.member_email, "created": created, "status": status_now},
            actor=actor,
        )

    return {"created": created, "status": status_now}


def _require_credit_privilege(staff: StaffIdentity, credits: Decimal) -> None:
    """Spec §5's credit rows, in code: at or below the threshold an editor
    grants; above it the act is **elevated** — ``admin`` AND a live window.

    The rank half is :func:`operator_roles.check_credit_amount`. The WINDOW
    half was missing until 2026-08-30: a plain admin session could move any
    quantity while ``/orgs/lifecycle`` — the same "elevated" class in §5 —
    demanded the window. Only a POSITIVE amount above the threshold needs
    it, per check_credit_amount's own note on corrections. Break-glass
    bypasses the matrix by design and logs a WARNING on every use.
    """
    if not staff.is_session:
        return
    try:
        operator_roles.check_credit_amount(staff.role, credits)
    except operator_roles.RoleForbidden:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    if credits <= operator_roles.credit_elevation():
        return
    with get_engine().begin() as conn:
        window = store.operator_elevation_live(conn, staff.operator_id)
    try:
        operator_elevation.check_window(window, now=datetime.now(UTC))
    except operator_elevation.NotElevated:
        # The same 403 body as a rank refusal (auth._enforce_role's rule):
        # distinguishing them tells a stolen admin session what to do next.
        raise HTTPException(status_code=403, detail="Forbidden") from None


@app.post("/credits/grant")
def grant_credits(req: CreditGrantRequest, staff: Operator) -> dict[str, Any]:
    """Add credits. Append-only — a correction is another row, never an edit.

    ⚠️ **The role matrix admits an `editor` here, and the AMOUNT can raise the
    bar to `admin`** (CP-12c, spec §5). The size is in the body, which a
    FastAPI dependency cannot read, so this is the one rule the matrix cannot
    apply at the door and the route body applies instead. The check runs
    BEFORE the organization is read, so the refusal is identical whether the
    company exists or not.
    """
    _require_credit_privilege(staff, req.credits)
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        # 🔴 The manual-payment fence (owner ask, 2026-08-30): the same
        # (reason, ref) is refused with the FIRST row as evidence. A bank
        # transfer's reference typed twice is the same money credited twice,
        # and the operator finds out from a dispute unless it is refused
        # here. An `adjustment` citing the same ref passes - different
        # reason, and correcting a row is what it is for.
        ref = (req.ref or "").strip() or None
        if ref is not None:
            prior = store.credit_ref_row(conn, org_id=org_id, reason=req.reason, ref=ref)
            if prior is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"reference {ref!r} was already credited as "
                        f"{req.reason!r} on "
                        f"{prior.created_at.date().isoformat()} "
                        f"({prior.delta} credits). A correction is an "
                        "'adjustment' citing the same reference - never a "
                        "second grant."
                    ),
                )
        try:
            store.add_credit(
                conn,
                org_id=org_id,
                delta=req.credits,
                reason=req.reason,
                ref=ref,
            )
        except IntegrityError:
            # The SELECT above cannot hold under concurrency: two grants
            # both read "no prior row" and both insert (the exact race
            # `store.lock_org_activation` documents one plane over). The
            # 018 partial unique index makes the second INSERT fail
            # instead of crediting one transfer twice — answer it like
            # the sequential repeat.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"reference {ref!r} was already credited as "
                    f"{req.reason!r} (a concurrent grant landed first). "
                    "A correction is an 'adjustment' citing the same "
                    "reference - never a second grant."
                ),
            ) from None
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))
        _audit(
            conn,
            org_id,
            "credits.grant",
            {"delta": str(req.credits), "reason": req.reason},
            actor=staff.actor,
        )

    return {"balance": str(balance)}


@app.get("/credits/ledger")
def credit_ledger(org_slug: str, _: Operator, limit: int = 50) -> dict[str, Any]:
    """The newest ledger rows - the evidence an operator verifies against.

    ⚠️ VIEWER, the same argument as the balance and the audit trail: the
    ledger is what a customer reads in a dispute, and an operator checking
    "was this bank transfer already credited?" must not need a privilege to
    look. It discloses no secret - deltas, reasons, references and dates.
    """
    limit = max(1, min(int(limit), 200))
    with get_engine().begin() as conn:
        org_id = _org_id(conn, org_slug)
        rows = store.credit_ledger_rows(conn, org_id=org_id, limit=limit)
    return {
        "entries": [
            {
                # ⚠️ Money as STRINGS - NUMERIC(14,4) reformatted through a
                # float is how a ledger stops summing to its balance.
                "delta": str(r.delta),
                "reason": r.reason,
                "ref": r.ref,
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ],
    }


@app.get("/credits/balance")
def credit_balance(org_slug: str, _: Operator) -> dict[str, Any]:
    with get_engine().begin() as conn:
        org_id = _org_id(conn, org_slug)
        balance = balance_of(store.credit_deltas(conn, org_id=org_id))
        status = conn.execute(
            text("SELECT status FROM organization WHERE id = :i"), {"i": org_id}
        ).scalar_one()

    decision = decide_spend(
        balance,
        Decimal(0),
        policy=OverdraftPolicy(),
        is_trial=(status == "trial"),
    )
    return {
        "balance": str(balance),
        "in_overdraft": decision.in_overdraft,
        "org_status": status,
    }


@app.post("/keys")
def issue_key(req: IssueKeyRequest, staff: Operator) -> dict[str, Any]:
    """Mint an organization key. **The token is returned exactly once.**

    Only the hash is stored, so this response is the only moment the secret
    exists anywhere. It is not recoverable — a lost key is replaced, not looked
    up, and that is the property that makes a database disclosure survivable.
    """
    minted = mint_key()
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        store.issue_key(
            conn,
            org_id=org_id,
            prefix=minted.prefix,
            key_hash=minted.key_hash,
            label=req.label,
            created_by="operator",
        )
        # The audit row records the PREFIX, never the token.
        _audit(
            conn,
            org_id,
            "key.issue",
            {"prefix": minted.prefix, "label": req.label},
            actor=staff.actor,
        )

    return {"prefix": minted.prefix, "token": minted.token}


@app.post("/keys/revoke")
def revoke_key(req: RevokeKeyRequest, staff: Operator) -> dict[str, Any]:
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug)
        revoked = store.revoke_key(conn, org_id=org_id, prefix=req.prefix)
        _audit(
            conn,
            org_id,
            "key.revoke",
            {"prefix": req.prefix, "revoked": revoked},
            actor=staff.actor,
        )
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


#: Every `model_profile` column the window and context-tier read needs.
#: Named once, because a SELECT that drifts from `resolve_rates`'s key names
#: fails by answering NULL rather than by raising — the quiet shape.
_PROFILE_RATE_COLUMNS = (
    "vendor_input_per_1m_usd",
    "vendor_output_per_1m_usd",
    "vendor_cached_input_per_1m_usd",
    "vendor_input_offpeak_per_1m_usd",
    "vendor_output_offpeak_per_1m_usd",
    "vendor_cached_input_offpeak_per_1m_usd",
    "vendor_input_long_per_1m_usd",
    "vendor_output_long_per_1m_usd",
    "vendor_cached_input_long_per_1m_usd",
    "offpeak_start_utc",
    "offpeak_end_utc",
    "context_tier_threshold",
)


def _vendor_prices(
    conn,
    model: str,
    *,
    prompt_tokens: int = 0,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    """The vendor's prices for one model, from the operator's own record.

    Read at metering time and applied to THIS call only, so a later profile
    edit never rewrites what a past call cost (012's effective-dating
    argument, honoured by snapshotting instead of by history).

    🔴 **Which rate applies depends on WHEN the call ran and HOW BIG it was**
    (migration 024, `credit_pricing.md` §4.1). DeepSeek prices an off-peak
    window cheaper. OpenAI prices input past a context threshold dearer. Both
    arrive here as arguments rather than being read from a clock, so the caller
    cannot resolve "now" when it meant "when this call started".

    ⚠️ **``prompt_tokens`` must be the count the PROVIDER REPORTED.** A
    pre-flight estimate uses the wrong tokenizer for at least one vendor, and a
    threshold missed that way under-bills a large document by half.

    Returns the three rates plus the two labels the usage row records, so the
    number can be explained a year later.
    """
    row = conn.execute(
        text(
            f"SELECT {', '.join(_PROFILE_RATE_COLUMNS)} "  # noqa: S608 - a fixed tuple, never input
            "FROM model_profile WHERE model = :m"
        ),
        {"m": model},
    ).first()
    if row is None:
        # Unknown model: no rates, and no claim about a window we cannot see.
        return {
            "input": None, "output": None, "cached": None,
            "window": None, "context": None,
        }

    profile = dict(zip(_PROFILE_RATE_COLUMNS, row, strict=True))
    rates = pricing_window.resolve_rates(
        profile,
        prompt_tokens=prompt_tokens,
        started_at=started_at or datetime.now(UTC),
    )
    return {
        "input": rates.input_per_1m,
        "output": rates.output_per_1m,
        "cached": rates.cached_per_1m,
        "window": rates.window,
        "context": rates.context,
    }


#: Which `model_profile` column prices ONE unit of a task, keyed by the unit
#: `task_catalog.natural_unit` gives that task.
#:
#: 🔴 **The UNIT picks the column, and the presence of a quantity never
#: does** (§6A.10c clause 8). The branch in :func:`_record_completion` read
#: the per-MINUTE column for every call that carried a quantity, so an image
#: call would have taken its cost from a per-minute price. That was a live
#: mis-costing, and this map is the repair.
#:
#: `019_per_unit_vendor_costs.sql` built all three columns. H-46 shipped
#: before it, so the read below is GUARDED and an absent column answers NULL
#: rather than raising. A unit with no entry here — `tokens`, `seconds` —
#: also answers NULL, because nobody has told us what one costs.
_PER_UNIT_COLUMNS: dict[str, str] = {
    "minutes": "vendor_per_minute_usd",
    "images": "vendor_per_image_usd",
    "characters": "vendor_per_character_usd",
}


def _column_exists(conn, table: str, column: str) -> bool:
    """Does this table carry this column on the database we are talking to?

    ⚠️ **Asked, never caught.** A failed statement poisons the whole
    Postgres transaction, so a `try` around the real SELECT would take the
    metering write down with it. The catalog answers the question first
    instead, and the write goes on.
    """
    return (
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
        is not None
    )


def _vendor_per_unit(conn, model: str, *, unit: str | None) -> Decimal | None:
    """What ONE unit of this model costs us at the vendor, or ``None``.

    The per-unit half of :func:`_vendor_prices`, read at metering time for
    the same reason — a later profile edit must never rewrite what a past
    call cost.

    🔴 **``unit`` is the task's own** (`task_catalog.natural_unit`), so a
    minute of audio reads the per-minute column and a picture reads the
    per-image one. :data:`_PER_UNIT_COLUMNS` holds the whole mapping, and
    this function chooses nothing by itself.

    ``None`` covers all four ways nobody has told us: no column prices this
    unit, the column is not there yet, the model has no profile row, or the
    row holds NULL. D-AI-7 rule 3 says NULL means nobody told us and never
    means zero.
    """
    column = _PER_UNIT_COLUMNS.get(unit or "")
    if column is None:
        return None
    if not _column_exists(conn, "model_profile", column):
        return None
    return conn.execute(
        text(f"SELECT {column} FROM model_profile WHERE model = :m"),
        {"m": model},
    ).scalar_one_or_none()


#: What to assume a completion might return when the caller names no ceiling.
#:
#: ⚠️ **A reservation size, never a limit.** Nothing enforces this on the
#: provider — it exists so an uncapped request reserves something generous
#: rather than nothing. `credit_pricing.md` §7.3 records the cost of getting it
#: wrong in the other direction: a hold far larger than the real charge rejects
#: calls a customer could afford, and the fix there is a per-tier `max_tokens`
#: cap, which is NOT in this slice.
MAX_OUTPUT_FOR_HOLD = 4096

#: How far back the per-tier margin monitor looks (`credit_pricing.md` §4.3).
#:
#: ⚠️ Seven days, matching the design document's own query. Long enough that a
#: quiet tier reports something, short enough that a price change shows up
#: while somebody still remembers making it.
MARGIN_WINDOW_DAYS = 7


def _place_call_hold(
    conn,
    *,
    org_id: str,
    request_id: str,
    tier: str,
    task: str,
    messages: list[Any],
    max_tokens: int | None,
) -> HTTPException | None:
    """Reserve the worst case for one completion. Returns a refusal or None.

    🔴 **Never raises past its own body.** A reserve that failed on an
    unexpected error would fail a completion the customer is entitled to, and
    §5's whole argument is that a metering problem must never become a product
    problem. An error here logs and lets the call through unreserved, which is
    the same posture the meter takes.

    ⚠️ **The prompt estimate is TOKENS, and we do not have a tokenizer here.**
    Four characters per token is the usual rough figure, and it is an ESTIMATE
    used only to size a reservation that is released minutes later — never to
    bill. `credit_pricing.md` §10.1 records that tokenizers differ by vendor,
    which is exactly why this number may not reach a charge.
    """
    try:
        card = router_mod.resolve_tier_rate(conn, tier, task)
    except UnpricedModel:
        # The shipped state (H-42). Nothing to reserve against.
        return None

    # ⚠️ A rough character count, deliberately generous. Under-reserving is
    # the failure that matters: it lets a call through that the settle cannot
    # cover. Over-reserving costs a customer headroom for the length of one
    # request, and the release gives it straight back.
    chars = sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
    prompt_estimate = chars // 4 + 1

    estimate = estimate_hold(
        card,
        prompt_tokens=prompt_estimate,
        # ⚠️ No `max_tokens` means the provider's own ceiling, which we cannot
        # see from here. `MAX_OUTPUT_FOR_HOLD` stands in for it — a number
        # chosen to be larger than any real completion rather than accurate.
        max_output_tokens=max_tokens or MAX_OUTPUT_FOR_HOLD,
    )
    if estimate.credits <= 0:
        return None

    try:
        store.place_hold(
            conn, org_id=org_id, request_id=request_id, credits=estimate.credits
        )
    except store.HoldRefused as refused:
        return HTTPException(
            status_code=402,
            detail=(
                f"insufficient credits: this call reserves {refused.needed} "
                f"credits and the balance is {refused.balance}"
            ),
        )
    except Exception:
        # See the docstring. A broken reserve must not break a completion.
        _log.exception("router.hold_failed", extra={"router_org": org_id})
    return None


def _record_completion(
    usage: ExtractedUsage,
    *,
    org_id: str,
    caller: Any,
    resolved: Any,
    client_ref: str | None,
    byok: bool = False,
    quantity: Decimal | None = None,
    declared_task: str | None = None,
    started_at: datetime | None = None,
    request_id: str | None = None,
) -> None:
    """Write ONE usage row and draw the credits for it. Never raises.

    The ONE metering writer, shared by the buffered path and the streamed one.
    Two writers would drift, and the streamed one would be the one nobody
    checked. Metering is best-effort and never fails a completion: an unmetered
    completion is a revenue problem, a failed completion is a product problem,
    and the product problem is worse.

    ⚠️ **``resolved`` is the step that ANSWERED**, so both facts recorded here
    are about the call that actually ran: the cost is priced at the served
    model's vendor prices, and ``served_rank`` above 1 is the durable evidence
    a fallback earned its keep (migration 013).

    🔴 **``byok`` zero-rates the bill, not the meter.** §3.4: an organization
    on its own vendor account is metered but not charged for tokens. The unit
    is still recorded — the history must stay readable — and our provider
    cost is zero because we paid the vendor nothing.

    ⚠️ **``quantity`` is for the units that have NO column** — minutes,
    characters, images (§6A.10a clause 4). A token call passes none and the
    row keeps NULL, because the three token columns already carry that
    number and a second copy is a second thing to disagree with. A call that
    passes one is priced per unit at BOTH ends: the customer against
    ``credits_per_unit``, and our own cost against the vendor's price for
    ONE of that task's units.

    🔴 **The task's UNIT picks the vendor column, and the presence of a
    quantity never does** (§6A.10c clause 8). Until 2026-08-31 the branch
    below called the per-MINUTE reader for every call that carried a
    quantity, so the first image call would have multiplied two pictures by
    a price for one minute of audio. ``_task_unit`` answers what the task is
    measured in, and :data:`_PER_UNIT_COLUMNS` maps that unit onto the one
    column that prices it.

    🔴 **``declared_task`` is what the CUSTOMER ASKED FOR, and the ROW records
    it. The BILL follows ``resolved``** (§8.5 clause 4). D-AI-2 lets a
    ``vision`` call be served by the chat binding of the tier the customer
    picked, so the two names disagree on exactly that path: the row must read
    ``vision`` or analytics cannot answer *"how much image work is this
    customer doing"*, and the price must read (chosen tier, ``chat``) or we
    charge them for a second call nobody made. Every other caller passes
    ``None``, the two names agree, and nothing changes.
    """
    # 🔴 The partition, checked BEFORE anything prices it (`credit_pricing.md`
    # §3). `cached_tokens` must be a subset of `prompt_tokens`, and one of the
    # two vendor conventions reports it as a sibling instead. This used to
    # clamp at zero, which undercharged by 27 % in silence.
    #
    # ⚠️ Checked here rather than inside `_rate_completion`, whose contract is
    # "never raises" and whose one caller is this function. Validating first
    # keeps that contract true instead of merely documented.
    metering_fault: str | None = None
    # ⚠️ NULL unless a token-priced call resolved them. A per-unit job (an
    # image, a minute of audio) has no window and no context tier, and writing
    # a guess would put a fact in the row that nothing measured.
    window_at_call: str | None = None
    context_tier: str | None = None
    try:
        TokenUsage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
        )
    except UsagePartitionError as exc:
        metering_fault = "usage_partition"
        # ⚠️ Carries the organization AND the request id, so this alarm joins
        # to the row it belongs to. H-85 exists because a sibling alarm named
        # neither and could not be reconciled to anything.
        _log.error(
            "router.usage_partition_failed",
            extra={
                "router_org": org_id,
                "router_client_ref": client_ref,
                "router_model": resolved.model,
                "router_tier": resolved.tier,
                "router_prompt_tokens": usage.prompt_tokens,
                "router_cached_tokens": usage.cached_tokens,
                "router_cache_convention": usage.cache_convention,
                "router_error": str(exc),
            },
        )

    # 🔴 **The SECOND way a served call goes unbilled, and the quieter one**
    # (migration 026). `usage_from_response` never raises, so a body we do not
    # recognise returns three zeros. The partition assert passes — zero is not
    # greater than zero — `rate_call` multiplies zeros, and the row lands
    # looking exactly like a served call that happened to be free.
    #
    # ⚠️ **A per-unit job is NOT unreadable when it reports no tokens.** An
    # image, a minute of audio and a character of speech are measured by
    # `quantity`, and they carry no token counts at all. `quantity is None` is
    # what tells the two apart, so this check must sit beside that fact.
    #
    # ⚠️ A prompt of zero is the signal, not a completion of zero. A provider
    # can legitimately return an empty completion. Nobody sends an empty
    # prompt — every chat call carries at least a system message.
    if metering_fault is None and quantity is None and usage.prompt_tokens == 0:
        metering_fault = "usage_unreadable"
        _log.error(
            "router.usage_unreadable",
            extra={
                "router_org": org_id,
                "router_client_ref": client_ref,
                "router_model": resolved.model,
                "router_tier": resolved.tier,
                "router_task": resolved.task,
            },
        )

    try:
        with get_engine().begin() as conn:
            # CP-6: the draw. `record_usage` negates this into `credit_ledger`
            # in the SAME transaction as the usage row, so a retried write that
            # inserts nothing also charges nothing. Zero while the card is
            # unpriced, which is the shipped state until the owner prices it.
            #
            # A faulted call bills ZERO and still writes its row. The customer
            # already has their completion, so the row is the evidence — and it
            # still COUNTS as a served call, which is why the fault is not a
            # `refusal_reason` (migration 023).
            if metering_fault:
                billed, unit = Decimal(0), None
            else:
                billed, unit = _rate_completion(
                    conn,
                    tier=resolved.tier,
                    model=resolved.model,
                    usage=usage,
                    task=resolved.task,
                    quantity=quantity,
                )
            if metering_fault:
                # ⚠️ NULL, not zero. A broken partition breaks OUR cost sum by
                # the same arithmetic it breaks the charge with, and zero would
                # read as "this call cost us nothing" in every margin query.
                # NULL reads as "unknown", which is what it is.
                cost = None
            elif byok:
                if billed:
                    # Loud, because this is the difference between §3.4 and a
                    # mischarge: the card HAS a price and this call is not
                    # paying it, on purpose.
                    _log.info(
                        "router.byok_unbilled",
                        extra={"byok_credits_waived": str(billed)},
                    )
                billed = Decimal(0)
                cost = Decimal(0)
            elif quantity is not None:
                # A per-unit call. The vendor sells it by minute, by picture
                # or by character, so our cost comes off the column that
                # prices THAT unit — never off three token rates this call
                # did not consume, and never off a per-minute price for a
                # task nobody measured in minutes.
                cost = router_mod.vendor_cost_per_unit_usd(
                    quantity,
                    _vendor_per_unit(
                        conn,
                        resolved.model,
                        unit=_task_unit(conn, resolved.task),
                    ),
                )
            else:
                # ⚠️ The tokens the PROVIDER reported, and the moment the call
                # STARTED — never an estimate and never "now". Migration 024's
                # two dimensions both resolve from these two arguments.
                prices = _vendor_prices(
                    conn,
                    resolved.model,
                    prompt_tokens=usage.prompt_tokens,
                    started_at=started_at,
                )
                window_at_call = prices["window"]
                context_tier = prices["context"]
                cost = router_mod.vendor_cost_usd(
                    usage,
                    input_per_1m=prices["input"],
                    output_per_1m=prices["output"],
                    cached_per_1m=prices["cached"],
                )
            store.record_usage(
                conn,
                org_id=org_id,
                # 🔴 The id the HOLD used, so the release can find its
                # reservation (migration 027). Minted in the route beside
                # `started_at`. A fresh one here would orphan every hold.
                # Still SERVER-generated — the caller's `client_ref` is
                # correlation only (migration 005).
                request_id=request_id or f"rtr-{uuid.uuid4().hex}",
                client_ref=client_ref,
                billed_credits=billed,
                user_email=caller.member,
                agent=caller.agent,
                module_slug=caller.module_slug,
                run_id=caller.run_id,
                model=resolved.model,
                tier=resolved.tier,
                # The task the caller DECLARED, which is the served one for
                # every caller but D-AI-2's lift. See the docstring.
                task=declared_task or resolved.task,
                # ⚠️ `quantity` stays NULL for a token-priced call, because
                # that caller passes none. The three token columns already
                # carry the number, and a second copy of it is a second thing
                # to disagree with. A per-unit call passes its minutes here.
                quantity=quantity,
                unit=unit,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cached_tokens=usage.cached_tokens,
                provider_cost_usd=cost,
                served_rank=getattr(resolved, "rank", 1),
                byok_served=byok,
                # ⚠️ NOT `refusal_reason`. The call SERVED — the customer holds
                # their completion — and only the meter failed. Migration 023's
                # comment carries the full reason.
                metering_fault=metering_fault,
                cache_convention=usage.cache_convention,
                # Migration 024. Why the cost is the number it is, recorded
                # beside the number so nobody has to re-derive it from a
                # profile that may have been edited since.
                window_at_call=window_at_call,
                context_tier=context_tier,
            )
    except Exception:
        _log.exception("router.metering_failed")


def _upstream_refusal(failed: router_mod.UpstreamFailed) -> HTTPException:
    """Map an upstream failure onto something a caller can branch on.

    Without this a provider 429 is indistinguishable from a Router bug, and
    every client treats both as fatal or both as retryable.

    ⚠️ **The upstream message is deliberately NOT echoed.** It can quote the
    request, and the request can carry customer content.

    ⚠️ **401, 402 and 403 from the VENDOR become 502.** They are our
    credential or our account, never the customer's key. Relayed verbatim,
    every SDK tells the customer to rotate THEIR key, and a vendor 402
    collides with this API's own top-up 402.

    🔴 **ONE mapping for every serving route.** All four raise from here —
    `/v1/chat/completions`, `/v1/audio/transcriptions`,
    `/v1/images/generations` and `/v1/audio/speech` — so a second endpoint
    cannot grow a second opinion about what a vendor 500 means.
    """
    status = failed.status
    _log.warning("router.provider_error", extra={"upstream_status": status})
    if isinstance(status, int) and 400 <= status < 600:
        return HTTPException(
            status_code=(502 if status >= 500 or status in (401, 402, 403) else status),
            detail="upstream provider error",
        )
    return HTTPException(status_code=502, detail="upstream provider error")


def _chain_credentials(
    conn,
    chain: list[ResolvedTier],
    *,
    org_id: str,
) -> dict[str, router_mod.Credential | None]:
    """One credential per VENDOR named in the chain, read in the caller's
    transaction.

    A vendor appears once however many steps it holds — the second step on the
    same vendor reuses the first step's key rather than paying for a second
    lookup on the hottest path in the system.

    ⚠️ **The 503 raises from INSIDE the caller's transaction, on purpose.** A
    missing or rotated encryption key is OUR failure and not a customer wall,
    so it writes no `usage_event` row (§8.1). Only the three customer refusals
    leave the block before they are raised.
    """
    credentials: dict[str, router_mod.Credential | None] = {}
    for step in chain:
        vendor = step.model.split("/", 1)[0]
        if vendor in credentials:
            continue
        try:
            credentials[vendor] = provider_credential(conn, provider=vendor, org_id=org_id)
        except Exception:
            # A missing or rotated encryption key must fail CLOSED with the
            # same 503 shape the other secrets use — not a 500 that reads
            # as a bug.
            _log.exception("router.credential_unavailable")
            raise HTTPException(status_code=503, detail="provider credentials unavailable")
    return credentials


# ── A5: the meter records the WALL as well as the call (§8.1, migration 020) ─

#: The slug for the 400. Minted by §8.1, because this refusal carries a
#: sentence rather than a machine-readable reason.
REFUSAL_TIER_UNKNOWN = "tier_unknown"

#: Every slug migration 020's CHECK admits. Two of them are copied WORD FOR
#: WORD from the body the customer already reads — ``decide_spend`` writes
#: ``insufficient_credits`` and :func:`_spend_refusal` writes
#: ``run_ceiling_exceeded`` — so the meter and the refusal say one thing.
#:
#: ⚠️ Named here as well as in the database on purpose. The CHECK is the fence,
#: and this set makes the writer refuse a fourth spelling BEFORE it becomes an
#: IntegrityError on the hottest path in the system.
_REFUSAL_REASONS = frozenset(
    {
        "insufficient_credits",
        "run_ceiling_exceeded",
        REFUSAL_TIER_UNKNOWN,
    }
)

#: How much of a caller-supplied label a refusal row keeps.
#:
#: 🔴 **A refused request is FREE, and its `model` and `task` are whatever the
#: caller typed.** Nothing upstream bounds either one, so a five-megabyte
#: "model" string would persist once per 400, at no cost to the sender. These
#: two cells are OBSERVABILITY — "which tier did they ask for" — and never an
#: authority anything reads back, so a truncated value answers the question
#: just as well as a whole one.
_REFUSAL_LABEL_MAX = 200


def _clip(value: str | None) -> str | None:
    """Bound one caller-supplied label. ``None`` stays ``None``."""
    if value is None:
        return None
    return str(value)[:_REFUSAL_LABEL_MAX]


def _record_refusal(
    reason: str,
    *,
    org_id: str,
    caller: Any,
    tier: str,
    task: str,
    client_ref: str | None = None,
) -> None:
    """Write the one ``usage_event`` row that says we refused (A5).

    🔴 **It opens its OWN short transaction, and that is the design.** The 400
    raises from INSIDE the serving transaction, so a refusal row written on
    that connection rolls back with the raise and the meter records nothing.
    :func:`_spend_refusal` answers the same hazard the other way — it RETURNS
    the refusal so the caller leaves the transaction cleanly — and the route
    now carries both refusals out of the block before it calls this.

    ⚠️ **Best effort, exactly like :func:`_record_completion`.** An unmetered
    refusal is a reporting gap. A refusal the customer never receives because
    the meter fell over is an outage, and the outage is worse.

    ⚠️ **``tier`` is the tier the caller can ACT on, and never a served one.**
    A5 asks *"which wall did this customer hit"*, so the cell has to name the
    thing somebody goes and repairs. At three of the four walls that is the
    tier the caller ASKED for, because nothing resolved and the request itself
    is the fact. At D-AI-2's image wall it is ``tier-vision``: the tier the
    caller named binds a working chat model, the missing binding is the vision
    one, and reporting the caller's tier there would send an operator to look
    at a tier with nothing wrong with it. ``_resolve_serving_chain`` decides
    which of the two travels, and this function never resolves anything itself.
    *(This read "the tier the caller ASKED for, never a resolved one" until
    2026-08-31, when the image wall made that sentence false.)*

    ⚠️ **Only a CUSTOMER wall reaches here.** The two 503s and the 502 are OUR
    failures and write no usage row at all — one table that mixes a customer
    wall with a broken vendor answers neither question.
    """
    if reason not in _REFUSAL_REASONS:
        # A slug nobody declared would fail the CHECK. Say so in the log rather
        # than raise on a path whose whole job is to deliver a refusal.
        _log.warning("router.refusal_slug_unknown", extra={"router_refusal": reason})
        return
    # ⚠️ Clipped BEFORE the database sees them, so a five-megabyte label is
    # never sent as a bind parameter either.
    tier, task = _clip(tier) or "", _clip(task) or ""
    try:
        with get_engine().begin() as conn:
            store.record_usage(
                conn,
                org_id=org_id,
                # SERVER-generated, exactly as the served path mints it.
                # `request_id` is NOT NULL UNIQUE (001:271).
                request_id=f"rtr-{uuid.uuid4().hex}",
                # We served nothing, so we charge nothing and the row draws no
                # ledger line — `record_usage` skips the draw on a zero charge.
                billed_credits=Decimal(0),
                refusal_reason=reason,
                user_email=caller.member,
                agent=caller.agent,
                module_slug=caller.module_slug,
                # A `run_ceiling_exceeded` row without its run is not
                # actionable, and the breaker reads the same field.
                run_id=caller.run_id,
                # 📌 The customer's own correlation id, exactly as the served
                # path carries it. Support finds a refused call the same way
                # they find a served one, and without it the customer's "my
                # request rtr-… failed" has nothing to match.
                client_ref=_clip(client_ref),
                # Both clipped above, because both are caller-supplied and
                # unbounded and a refused request costs the sender nothing.
                tier=tier,
                task=task,
                # The call consumed nothing, and the task's own unit keeps the
                # row readable beside a served one.
                quantity=0,
                unit=_task_unit(conn, task),
                # No model answered, and no vendor billed us.
                model=None,
                provider_cost_usd=None,
            )
    except Exception:
        _log.exception("router.refusal_metering_failed")


def _raise_spend_refusal(
    refusal: HTTPException,
    *,
    org_id: str,
    caller: Any,
    tier: str,
    task: str,
    client_ref: str | None,
) -> NoReturn:
    """Record the wall the spend gate raised, then deliver it.

    🔴 **ONE shape for every serving route.** All four call this — the chat
    door, the transcribe door, the image door and the speak door — so the 402
    and the 403 cannot come to mean four different rows.

    The slug is the word already inside the body the customer reads —
    `insufficient_credits` or `run_ceiling_exceeded` — never a second
    spelling minted here. `_record_refusal` drops anything else.

    ⚠️ **A plain-string detail writes NO row, and that is deliberate.** It
    carries no slug, and minting one from the status code would be the second
    spelling W3 forbids. Both shipped gate refusals build a dict, so nothing
    in the tree takes that branch — it is pinned by
    `TestTheThreeBranchesTheRefusalWriterCanTake` so that a future refusal
    answering a bare sentence loses its row VISIBLY.

    ⚠️ The caller must already have LEFT the serving transaction. A row
    written on that connection rolls back with the raise below.
    """
    detail = refusal.detail
    if isinstance(detail, dict) and detail.get("reason"):
        _record_refusal(
            str(detail["reason"]),
            org_id=org_id,
            caller=caller,
            tier=tier,
            task=task,
            client_ref=client_ref,
        )
    raise refusal


# ── D-AI-2: which chain serves a `task: vision` call (§8.5, §3.2) ───────────


@dataclass(frozen=True)
class _TierWall:
    """The 400 a resolution refused with, and the pair the meter records.

    ⚠️ **The HTTP sentence and the refusal SLUG are two different things.**
    ``error`` carries the wording a person reads, and it differs between the
    two walls below. The slug is ``tier_unknown`` for both, because
    ``_REFUSAL_REASONS`` and `020_usage_refusal.sql`'s CHECK close the
    vocabulary at three and a fourth spelling of one wall is what that CHECK
    exists to stop.
    """

    error: HTTPException | None
    #: The tier a refusal row names — the one the caller can ACT on.
    #: :func:`_record_refusal` holds the rule and the reason. It is the tier
    #: the CALLER asked for at every wall but the image one, where the missing
    #: binding is `tier-vision` and the caller's own tier is not broken.
    tier: str
    #: The task a refusal row names, read the same way.
    task: str


def _resolve_serving_chain(
    conn,
    *,
    tier: str,
    task: str,
) -> tuple[list[ResolvedTier], _TierWall]:
    """The chain that serves this call, or the wall that refuses it.

    🔴 **The refusal is RETURNED, never raised** (§8.1 clause 3). The meter
    has to record the wall, and a row written on the serving connection rolls
    back with the raise. So the route carries the 400 out of the transaction
    and delivers it there.

    🔴 **An image follows the chat model when it can (D-AI-2, §3.2).** A
    caller that declares ``vision`` reaches :func:`router.resolve_vision_chain`,
    which reads ``model_profile.reads_images`` and answers with the chosen
    tier's own chat chain (one model, one call) or with the ``tier-vision``
    chain. **Nothing here reads ``messages``** — the caller DECLARES the task
    (G-3, D61), and inference is what D32.7 is hostile to.

    Three answers, and each one is a different repair:

    1. A chain. The call proceeds.
    2. The tier binds nothing for this task — §3.2 step 0, the wall this route
       already had, unchanged.
    3. The chat model reads no image and nothing binds ``tier-vision`` — §3.2
       step 4. The sentence names both halves, so an operator knows which one
       to fix.
    """
    try:
        chain = (
            router_mod.resolve_vision_chain(conn, tier)
            if task == router_mod.VISION_TASK
            else resolve_chain(conn, tier, task)
        )
    except router_mod.VisionUnbound as unbound:
        # A 200 here would answer about text the model cannot see, and that
        # answer looks correct — which is why the silent drop is the worse
        # outcome and this is a 400.
        return [], _TierWall(
            HTTPException(status_code=400, detail=str(unbound)),
            router_mod.VISION_TIER,
            router_mod.VISION_TASK,
        )
    except TierUnknown:
        # 400, not a silent coercion to a default. A misconfigured agent must
        # be visible rather than quietly billed (D32.7).
        return [], _TierWall(
            HTTPException(
                status_code=400,
                detail=(f"no binding for tier {tier!r} on task {task!r}; name a tier, not a model"),
            ),
            tier,
            task,
        )
    return chain, _TierWall(None, tier, task)


def _open_stream_chain(
    attempts: list[ResolvedTier],
    kwargs_for: Any,
    on_failover: Any,
) -> tuple[list[Any], Any, ResolvedTier]:
    """Walk the chain and open the winning stream ON THE SERVING LOOP.

    🔴 **``anyio.from_thread.run``, and NOT ``asyncio.run``.** This route is
    ``def``, so FastAPI runs it in an anyio worker thread. ``asyncio.run`` would
    create a private loop, and closing that loop calls
    ``shutdown_asyncgens()`` — which throws ``GeneratorExit`` into the provider
    stream we just opened. Measured 2026-08-31 on a three-frame source: the
    client received frame one and nothing else. ``from_thread.run`` runs the
    coroutine on the loop Starlette will iterate the body on, so the open, the
    first chunk and every later ``__anext__`` share one live loop.

    ⚠️ **The walk cannot move into the body generator.** Starlette sends the
    ``http.response.start`` message before it pulls the first item, so by the
    time a body iterator runs the 200 status line has gone out and no failover
    is expressible any more.
    """
    return anyio.from_thread.run(router_mod.open_stream_chain, attempts, kwargs_for, on_failover)


async def _stream_closed() -> AsyncIterator[bytes]:
    """Every step refused before a frame existed. Close cleanly, meter nothing.

    A client that never sees the sentinel waits for its own timeout, so it
    gets the sentinel and nothing else.

    ⚠️ **The 200 here is a CHOICE now, and it was a constraint before.** While
    the open lived inside the body generator the status line had already gone
    out, and a 502 could not be expressed at all. The walk moved into the
    route, so that 502 became reachable — and this slice still does not raise
    it, because changing what a streaming caller is answered WITH is not a
    failover change. ``test_a_stream_that_never_starts_writes_no_usage_row``
    pins the 200 and the sentinel.

    ⚠️ **No usage row, and no refusal row either.** The wall was the vendor's,
    not the customer's, and ``_REFUSAL_REASONS`` stays closed (§8.1).
    """
    yield SSE_DONE


async def _streamed_completion(
    head: list[Any],
    source: Any,
    *,
    org_id: str,
    caller: Any,
    resolved: Any,
    client_ref: str | None,
    byok: bool = False,
    declared_task: str | None = None,
    started_at: datetime | None = None,
    request_id: str | None = None,
) -> AsyncIterator[bytes]:
    """Replay the first chunk, relay the rest, and meter the result once.

    ⚠️ **The stream is ALREADY OPEN when this runs**, and its first chunk is
    already in hand. The route pulled it while walking the chain, because
    failover has to happen before the 200 status line goes out.

    ⚠️ **``head`` is replayed exactly once and is never re-fetched.** It left
    the provider and no retry can produce it again. A second failover from here
    would splice two completions into one response.

    A chain that failed at every step never reaches this generator, so it
    writes no usage row: ``relay_stream`` never starts and its ``finally``
    never runs. The row is impossible rather than merely unwritten. That is the
    phantom-row defect which produced the 501, closed by construction
    (done-when 4).

    🔴 **The ``finally`` CLOSES THE WINNER, and the walk moving earlier is why
    it has to.** Starlette 1.1.0 never calls ``aclose`` on a body iterator, and
    a client that goes away leaves this generator to the loop's async-generator
    finalizer. That raises ``GeneratorExit`` at the ``yield`` below, so the
    ``finally`` runs and the provider socket is released. Without it an
    abandoned stream held a connection until the process ended.

    ⚠️ **One window stays open, and no code here can close it.** If Starlette
    never pulls a single item — the client vanishes between the route returning
    and ``http.response.start`` — this generator never starts, so no
    ``finally`` of ours exists to run. §8.6 records it.
    """

    def _on_finish(usage: ExtractedUsage, started: bool) -> None:
        if not started:
            return
        _record_completion(
            usage,
            org_id=org_id,
            caller=caller,
            resolved=resolved,
            client_ref=client_ref,
            byok=byok,
            # A streamed `vision` call takes D-AI-2's lift exactly as a
            # buffered one does, so its row says `vision` too (§8.5 clause 4).
            declared_task=declared_task,
            request_id=request_id,
            # 🔴 The window follows the moment the request STARTED, not the
            # moment the last frame arrived. A stream is exactly the call that
            # can span a boundary, and the vendor charges the window it
            # entered on (`credit_pricing.md` §4.1 clause 6).
            started_at=started_at,
        )

    async def _replayed() -> AsyncIterator[Any]:
        for chunk in head:
            yield chunk
        async for chunk in source:
            yield chunk

    try:
        async for frame in relay_stream(_replayed(), on_finish=_on_finish):
            yield frame
    finally:
        # Every exit: the last frame, a client that left, a provider that
        # died. `aclose_quietly` is the ONE close, shared with the walk's
        # loser path, and it is safe on a stream that already finished.
        await router_mod.aclose_quietly(source)


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
    # 🔴 Taken ONCE, here, before anything talks to a vendor — and carried to
    # the meter on both the buffered and the streamed path. The vendor charges
    # the window a request ENTERED on, so a call that spans the boundary must
    # resolve from this moment and not from whenever metering happened to run
    # (`credit_pricing.md` §4.1 clause 6, migration 024).
    started_at = datetime.now(UTC)
    # 🔴 **Minted HERE, not at metering time** (migration 027). The hold and
    # the settle must carry the SAME `ref`, or the release cannot find the
    # reservation it closes and the sweeper sees an orphan on every call.
    # SERVER-generated, exactly as before — the caller's `client_ref` is
    # correlation only and trusting it let a caller suppress their own meter.
    request_id = f"rtr-{uuid.uuid4().hex}"

    # 🔴 **A CUSTOMER refusal leaves this block before it is raised** (§8.1
    # clause 3). The meter has to record the wall, and a row written on the
    # serving connection rolls back with the raise — so the 400 is CARRIED out
    # of the transaction rather than thrown through it. `_spend_refusal`
    # already works this way, and its docstring states the rule.
    refusal: HTTPException | None = None
    hold_refusal: HTTPException | None = None
    credentials: dict[str, router_mod.Credential | None] = {}

    with get_engine().begin() as conn:
        # 🔴 **The whole chain, not one model (D-AI-5).** Every step is
        # resolved and credentialled inside this one transaction, because the
        # provider call happens after the connection closes — looking a
        # credential up mid-failover would need a second connection on the
        # hottest path in the system.
        chain, wall = _resolve_serving_chain(conn, tier=req.model, task=req.task)
        unknown_tier = wall.error

        if unknown_tier is None:
            credentials = _chain_credentials(conn, chain, org_id=org_id)

            # CP-6. BEFORE the provider call, which is the only place a refusal
            # is worth anything: after it we have already spent the money.
            # Metering afterwards stays best-effort and never fails a
            # completion — the GATE may refuse, the METER may not.
            refusal = _spend_refusal(conn, caller) if _spend_gate_enabled() else None

            # 🔴 **The RESERVE** (migration 027, `credit_pricing.md` §5). The
            # gate above answers "is there any headroom at all"; this answers
            # "is there enough for THIS call", and takes it.
            #
            # ⚠️ Same transaction as the gate, and `place_hold` locks the
            # organization row inside it. Two calls arriving together are
            # serialised there — without it each reads the same balance, each
            # passes, and the organization goes negative by the second one.
            #
            # ⚠️ Only behind the spend gate. The reserve is a spend refusal by
            # another name, and arming it while the gate ships OFF would refuse
            # customers the gate deliberately does not (H-42's ordering).
            if refusal is None and _spend_gate_enabled():
                hold_refusal = _place_call_hold(
                    conn,
                    org_id=org_id,
                    request_id=request_id,
                    tier=req.model,
                    task=req.task,
                    messages=req.messages,
                    max_tokens=req.max_tokens,
                )

    if unknown_tier is not None:
        _record_refusal(
            REFUSAL_TIER_UNKNOWN,
            org_id=org_id,
            caller=caller,
            tier=wall.tier,
            task=wall.task,
            client_ref=req.client_ref,
        )
        raise unknown_tier

    # The reserve's refusal is a spend refusal, and it travels the same road.
    if refusal is None and hold_refusal is not None:
        refusal = hold_refusal

    if refusal is not None:
        # Carried OUT of the transaction above, then recorded and delivered.
        # `_raise_spend_refusal` holds the rules and the shared shape.
        _raise_spend_refusal(
            refusal,
            org_id=org_id,
            caller=caller,
            tier=req.model,
            task=req.task,
            client_ref=req.client_ref,
        )

    # ⚠️ **A step we hold no key for is not a step.** It cannot be tried at all,
    # so it is dropped here rather than attempted and counted as a failure —
    # otherwise one unconfigured vendor in the middle of a chain would burn an
    # attempt and a chunk of the latency budget on every single request.
    attempts = [step for step in chain if credentials.get(step.model.split("/", 1)[0]) is not None][
        : router_mod.MAX_CHAIN_ATTEMPTS
    ]

    if not attempts:
        # Unchanged shape: the first step names the vendor somebody has to go
        # and configure.
        provider = chain[0].model.split("/", 1)[0]
        raise HTTPException(
            status_code=503,
            detail=f"no provider credential configured for {provider!r}",
        )

    def _kwargs_for(step: ResolvedTier) -> dict[str, Any]:
        """Build the outgoing call for one step of the chain.

        ⚠️ ALLOWLIST. Only named parameters reach the provider, and the ones
        that multiply our cost are clamped. `api_base` is ours alone — a caller
        can neither set it nor see it.
        """
        cred = credentials[step.model.split("/", 1)[0]]
        assert cred is not None  # the attempts filter removed keyless steps
        passthrough = {
            k: v for k, v in req.model_dump(exclude_none=True).items() if k in _FORWARDABLE
        }
        requested_max = passthrough.get("max_tokens")
        passthrough["max_tokens"] = min(
            int(requested_max) if requested_max else _MAX_OUTPUT_TOKENS,
            _MAX_OUTPUT_TOKENS,
        )
        out: dict[str, Any] = {
            **passthrough,
            "model": step.model,
            "api_key": cred.secret,
            # Bounded explicitly rather than left to litellm's defaults, so one
            # request cannot become fifty provider calls.
            "num_retries": 1,
            "timeout": 120,
        }
        if cred.api_base:
            out["api_base"] = cred.api_base
        return out

    def _byok_served(step: ResolvedTier) -> bool:
        """Whose account ran this step. §3.4 turns on exactly this bit."""
        cred = credentials[step.model.split("/", 1)[0]]
        return bool(cred and cred.byok)

    resolved = attempts[0]

    # ── Walk the chain ──────────────────────────────────────────────────────
    #
    # ⚠️ **`resolved` is reassigned to the step that ANSWERED**, and that is not
    # bookkeeping. `_record_completion` prices the call from it, so billing an
    # Opus request that actually fell over to Haiku at Opus rates would
    # overcharge the customer for a model they did not get.
    #
    # The walk lives in `router.walk_chain` rather than here: a route needs
    # FastAPI, and a test of failover ORDER should not. Both shapes below walk
    # through that one function, so the stream carries no second policy.
    #
    # 📌 Declared ABOVE the stream branch on purpose. Both branches announce a
    # failover the same way, and a second copy of this callback is two places
    # to change the day the log line grows a field.
    def _note_failover(frm: ResolvedTier, to: ResolvedTier, status: int | None) -> None:
        # 📌 WHY a chain moved, which the row does not carry. `served_rank`
        # (migration 013) records WHICH step served. The reason lives here,
        # because it is read during an outage rather than during a bill.
        _log.warning(
            "router.failover",
            extra={
                "fo_from": frm.model,
                "fo_to": to.model,
                "fo_status": status,
                "fo_tier": frm.tier,
                "fo_task": frm.task,
            },
        )

    if req.stream:
        # CP-4b. Everything above this line has already run: the tier
        # resolved, the credential loaded, and CP-6's balance gate and
        # breaker either refused or did not. A refusal delivered inside an
        # SSE frame is one every client renders as CONTENT, so the gate has
        # to be behind us before the stream opens (done-when 5).
        #
        # 🔴 **THE BOUNDARY (§8.6). A stream fails over BEFORE its first
        # frame, and never after it.** The walk below opens each step and
        # pulls one chunk. Once that chunk is in hand the client is committed
        # to this step: a later retry would splice two different completions
        # into one response, which is worse than the error. The walk therefore
        # lives here and not in the body generator — Starlette sends the 200
        # status line before it pulls the first item, so a generator has no
        # failover left to express.
        def _stream_kwargs_for(step: ResolvedTier) -> dict[str, Any]:
            out = _kwargs_for(step)
            out["stream"] = True
            # Ask for the usage frame. Without it an OpenAI-compatible provider
            # reports no counts on a stream at all, and we would be metering a
            # guess. A provider that ignores the option costs us nothing here —
            # `relay_stream` keeps the last counts it saw, which stay zero.
            out["stream_options"] = {"include_usage": True}
            return out

        headers = {
            "Cache-Control": "no-cache",
            # Proxies that buffer turn a stream into one late blob.
            "X-Accel-Buffering": "no",
        }
        try:
            head, source, resolved = _open_stream_chain(
                attempts, _stream_kwargs_for, _note_failover
            )
        except router_mod.UpstreamFailed as failed:
            # 🔴 A 200 with a lone sentinel, NOT the 502 the buffered path
            # raises. `_stream_closed` holds the reason this stayed a 200 once
            # the walk moved out of the body generator and made a 502
            # reachable. The meter writes nothing, and `_REFUSAL_REASONS`
            # stays closed: an upstream outage is not a customer wall (§8.1).
            _log.warning("router.stream_open_failed", extra={"upstream_status": failed.status})
            return StreamingResponse(
                _stream_closed(), media_type="text/event-stream", headers=headers
            )

        return StreamingResponse(
            _streamed_completion(
                head,
                source,
                org_id=org_id,
                caller=caller,
                # The step that ANSWERED, so the row carries its `served_rank`
                # and its BYOK bit — exactly as the buffered path does.
                resolved=resolved,
                client_ref=req.client_ref,
                byok=_byok_served(resolved),
                # What the CUSTOMER asked for. The bill follows `resolved`.
                declared_task=req.task,
                started_at=started_at,
                request_id=request_id,
            ),
            media_type="text/event-stream",
            headers=headers,
        )

    try:
        response, resolved = asyncio.run(
            router_mod.call_chain(attempts, _kwargs_for, _note_failover)
        )
    except HTTPException:
        raise
    except router_mod.UpstreamFailed as failed:
        # ONE mapping, shared with the transcribe route. See
        # `_upstream_refusal` for what each status becomes and why.
        raise _upstream_refusal(failed) from failed

    # Metering is best-effort and NEVER fails the call: an unmetered completion
    # is a revenue problem, a failed completion is a product problem, and the
    # product problem is worse. It runs only AFTER a successful provider call,
    # so a failed request can no longer leave a phantom row behind.
    _record_completion(
        usage_from_response(response),
        org_id=org_id,
        caller=caller,
        resolved=resolved,
        client_ref=req.client_ref,
        # ⚠️ Judged on the step that ANSWERED, not the primary. A chain can
        # legally mix a BYOK vendor with a platform one, and §3.4's zero-rating
        # follows whichever account the tokens actually ran on.
        byok=_byok_served(resolved),
        # 📌 What the CUSTOMER asked for, which is what analytics must report.
        # The BILL follows `resolved`, so D-AI-2's lift records `vision` and
        # charges the (chosen tier, `chat`) pair (§8.5 clause 4).
        declared_task=req.task,
        started_at=started_at,
        request_id=request_id,
    )

    return response


# ── H-46: the transcribe endpoint (§6A.10a) ─────────────────────────────────
#
# 🔴 **`tier-stt` has been bound since migration 010 and nothing could call
# it.** The Router served one of D60's six tasks. This route serves the
# second, and it is the first caller `resolve_invocation` has ever had on the
# serving path.

#: The ONE task this endpoint serves, and the route names it rather than
#: reading it off the caller's alias.
#:
#: 🔴 **The Router never sniffs the payload to learn what the caller wants**
#: (§6A.10a clause 1). The DOOR declares the task, the `model` field names a
#: TIER, and `resolve_chain` refuses any alias that is not bound to this task.
#: So a bare model id and a chat tier both walk into the same 400, and audio
#: can never reach a chat model by accident (D60.2).
TRANSCRIBE_TASK = "transcribe"

#: Minutes are what `task_catalog.natural_unit` prices `transcribe` in.
_SECONDS_PER_MINUTE = Decimal(60)

#: `usage_event.quantity` is `NUMERIC(14, 4)` (migration 010).
#:
#: ⚠️ **Rounded HERE, before rating, and that is the point.** Postgres would
#: round the stored value on its own, and then the minutes we billed and the
#: minutes the row reports would differ in the last place. One number,
#: rounded once, bills and records the same thing.
_MINUTE_QUANTUM = Decimal("0.0001")

#: How big an upload we accept. *Agent default*, anchored on the vendor rather
#: than invented: the OpenAI Whisper API refuses above 25 MB, so a larger file
#: buys a round trip and a provider 413. The Router holds the body in memory
#: to replay it across a failover, so the ceiling is ours to state.
_MAX_AUDIO_BYTES = 25 * 1024 * 1024

#: How much of a caller-supplied filename we send upstream. The name only
#: tells the provider the audio format, and nothing reads it back.
_FILENAME_MAX = 200

#: Form values that mean yes. A form field is a STRING, so `"false"` is truthy
#: to Python and every one of these has to be named.
_TRUTHY_FORM = frozenset({"1", "true", "t", "yes", "y", "on"})


class _NamedAudio(io.BytesIO):
    """The uploaded audio, with the filename the provider reads it by.

    ⚠️ A subclass because `io.BytesIO` refuses attribute assignment, and an
    OpenAI-family provider infers the audio FORMAT from the name. A stream
    with no name is a stream the vendor cannot decode.
    """

    def __init__(self, data: bytes, name: str) -> None:
        super().__init__(data)
        self.name = name


def _upload_name(filename: str | None) -> str:
    """The bare filename we send upstream, bounded and stripped of any path.

    Caller-supplied and therefore never trusted with a directory: a multipart
    filename can carry one, and this value is handed to a client library.
    """
    raw = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    # No extension is invented for a caller who sent no name. Guessing the
    # format for them produces a decode failure that reads as our bug.
    return (raw or "audio")[:_FILENAME_MAX]


def _form_is_true(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY_FORM


def _chain_invocations(conn, chain: list[ResolvedTier], *, task: str) -> dict[str, str]:
    """The provider verb for each step of the chain. D60 step two.

    Read in the caller's transaction beside the credentials, and for the same
    reason: the provider call happens after the connection closes.

    ⚠️ **A step with no capability row is OMITTED, not raised on.** It is our
    configuration mistake rather than a customer wall, so it writes no
    refusal row — it simply stops being a step the Router can try. Refusing
    the whole request would let one unconfigured model take down a chain
    whose other steps serve perfectly well.
    """
    verbs: dict[str, str] = {}
    for step in chain:
        try:
            verbs[step.model] = router_mod.resolve_invocation(conn, step.model, task)
        except TierUnknown:
            _log.warning(
                "router.capability_missing",
                extra={"router_model": step.model, "router_task": task},
            )
    return verbs


def _nothing_to_try(
    primary: ResolvedTier, invocations: dict[str, str], *, task: str
) -> HTTPException:
    """The 503 for a chain no step of which can be attempted.

    Two causes, and the operator has to be told which. A missing credential
    names the vendor somebody has to go and configure. A missing capability
    row names the model and the task, because the credential is fine and the
    catalog is not.
    """
    if primary.model in invocations:
        provider = primary.model.split("/", 1)[0]
        detail = f"no provider credential configured for {provider!r}"
    else:
        detail = f"no capability declares how to serve {primary.model!r} for task {task!r}"
    return HTTPException(status_code=503, detail=detail)


def _transcript_of(response: Any) -> str:
    """The text of a transcription, whatever shape the response carries.

    litellm answers with a `TranscriptionResponse` whose `text` may be
    `None`, and the stub seam in the tests answers with a plain dict. Both
    reach the customer as a string, because a caller that asked for a
    transcript and got `null` cannot tell a silent file from our bug.
    """
    if isinstance(response, dict):
        text_value = response.get("text")
    else:
        text_value = getattr(response, "text", None)
    return text_value if isinstance(text_value, str) else ""


@app.post("/v1/audio/transcriptions")
def audio_transcriptions(
    caller: KeyCaller,
    file: Annotated[UploadFile, File()],
    model: Annotated[str, Form()],
    stream: Annotated[str | None, Form()] = None,
    client_ref: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Transcribe one audio file, gate it, and charge it per minute.

    The same shape as ``chat_completions`` and deliberately so: the
    organization comes from the API key, the model comes from the tier
    binding, the three customer walls stand BEFORE the provider call, and the
    usage row is written from numbers we observed rather than reported by the
    party being metered.

    Three things differ, and each is a clause of §6A.10a.

    🔴 **The body is multipart, so ``model`` arrives as a form field** — and
    it is a TIER ALIAS, never a model id (clause 1). ``016_tier_task.sql``
    binds ``tier-stt`` to this task.

    🔴 **The METER owns ``response_format``** (clause 3). The Router sends
    ``verbose_json`` upstream, because litellm's ``TranscriptionResponse``
    declares ``text`` and ``usage`` alone and a duration reaches it only
    under that format. A caller-supplied format is ignored, and the caller
    reads ``{"text": …}`` whatever the upstream body looked like — so the
    meter's choice never changes the customer-visible contract.

    🔴 **A truthy ``stream`` field is a 400** (clause 2). ``STREAMABLE_TASKS``
    cannot serve this refusal: its one reader guards the OPERATOR capability
    write and never sees a serving request. So the refusal is this endpoint's
    own contract, and it is NEW behaviour.

    ⚠️ **The stream 400 and the 413 write NO usage row.** Migration 020's
    CHECK holds three slugs and minting a fourth would be the second spelling
    §8.1 forbids. The three walls that DO write one are the ones clause 10
    names, and they are the same three the chat route carries.

    Declared ``def`` for the reason the module docstring gives — the engine is
    synchronous, and the provider coroutine is driven with ``asyncio.run``
    inside the threadpool worker.
    """
    org_id = caller.organization_id

    # Read once. Every failover step re-sends the same bytes, and a file
    # object is consumed by the first attempt that reads it.
    audio = file.file.read(_MAX_AUDIO_BYTES + 1)
    if len(audio) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"audio above {_MAX_AUDIO_BYTES} bytes is refused",
        )
    filename = _upload_name(file.filename)

    if _form_is_true(stream):
        # Refused BEFORE anything is resolved or spent. A stream this route
        # cannot deliver must cost the customer nothing.
        raise HTTPException(
            status_code=400,
            detail=(
                "this endpoint does not stream; omit the 'stream' field and "
                "read the transcript from the response body"
            ),
        )

    # 🔴 **A CUSTOMER refusal leaves this block before it is raised**, exactly
    # as the chat route carries it (§8.1 clause 3). A row written on the
    # serving connection rolls back with the raise, and the meter records
    # nothing.
    unknown_tier: HTTPException | None = None
    refusal: HTTPException | None = None
    chain: list[ResolvedTier] = []
    credentials: dict[str, router_mod.Credential | None] = {}
    invocations: dict[str, str] = {}

    with get_engine().begin() as conn:
        try:
            chain = resolve_chain(conn, model, TRANSCRIBE_TASK)
        except TierUnknown:
            unknown_tier = HTTPException(
                status_code=400,
                detail=(
                    f"no binding for tier {model!r} on task "
                    f"{TRANSCRIBE_TASK!r}; name a tier, not a model"
                ),
            )

        if unknown_tier is None:
            credentials = _chain_credentials(conn, chain, org_id=org_id)
            # D60 step two, on the serving path for the first time.
            invocations = _chain_invocations(conn, chain, task=TRANSCRIBE_TASK)
            refusal = _spend_refusal(conn, caller) if _spend_gate_enabled() else None

    if unknown_tier is not None:
        _record_refusal(
            REFUSAL_TIER_UNKNOWN,
            org_id=org_id,
            caller=caller,
            tier=model,
            task=TRANSCRIBE_TASK,
            client_ref=client_ref,
        )
        raise unknown_tier

    if refusal is not None:
        _raise_spend_refusal(
            refusal,
            org_id=org_id,
            caller=caller,
            tier=model,
            task=TRANSCRIBE_TASK,
            client_ref=client_ref,
        )

    # A step we hold no key for, or no capability row for, is not a step. It
    # cannot be tried at all, so it is dropped rather than attempted and
    # counted as a failure.
    attempts = [
        step
        for step in chain
        if credentials.get(step.model.split("/", 1)[0]) is not None and step.model in invocations
    ][: router_mod.MAX_CHAIN_ATTEMPTS]

    if not attempts:
        raise _nothing_to_try(chain[0], invocations, task=TRANSCRIBE_TASK)

    def _kwargs_for(step: ResolvedTier) -> dict[str, Any]:
        """Build the outgoing call for one step of the chain.

        ⚠️ ALLOWLIST, exactly as the chat route builds one. Nothing the
        caller sent reaches the provider except the audio itself and its
        name. `api_base` is ours alone.
        """
        cred = credentials[step.model.split("/", 1)[0]]
        assert cred is not None  # the attempts filter removed keyless steps
        out: dict[str, Any] = {
            "model": step.model,
            # A FRESH reader per attempt. The first attempt consumes it, and
            # a failover that re-sent an exhausted stream would transcribe
            # zero bytes and bill for it.
            "file": _NamedAudio(audio, filename),
            "api_key": cred.secret,
            # 🔴 The meter owns this field (clause 3). Without it the
            # provider reports no duration, the zero-bill arm is the only arm
            # that ever runs, and the meter records zero for every call.
            "response_format": router_mod.TRANSCRIPTION_RESPONSE_FORMAT,
            # D60 step two: the verb `model_capability` named for this pair.
            "invocation": invocations[step.model],
            "num_retries": 1,
            "timeout": 120,
        }
        if cred.api_base:
            out["api_base"] = cred.api_base
        return out

    def _byok_served(step: ResolvedTier) -> bool:
        """Whose account ran this step. §3.4 turns on exactly this bit."""
        cred = credentials[step.model.split("/", 1)[0]]
        return bool(cred and cred.byok)

    def _note_failover(frm: ResolvedTier, to: ResolvedTier, status: int | None) -> None:
        _log.warning(
            "router.failover",
            extra={
                "fo_from": frm.model,
                "fo_to": to.model,
                "fo_status": status,
                "fo_tier": frm.tier,
                "fo_task": TRANSCRIBE_TASK,
            },
        )

    try:
        response, resolved = asyncio.run(
            router_mod.call_chain(attempts, _kwargs_for, _note_failover)
        )
    except HTTPException:
        raise
    except router_mod.UpstreamFailed as failed:
        # ONE mapping, shared with the chat route. A second endpoint must not
        # grow a second opinion about what a vendor 500 means.
        raise _upstream_refusal(failed) from failed

    seconds = router_mod.duration_seconds(response)
    if seconds is None:
        # ⚠️ BILL ZERO, LOUDLY (clause 3). The customer already holds the
        # transcript, so the only choice left is whether we also lose the
        # row. We keep the row — it is the evidence — and this line is how
        # an unmeasured call becomes visible instead of merely cheap.
        _log.warning(
            "router.unmeasured_quantity",
            extra={
                "router_model": resolved.model,
                "router_tier": resolved.tier,
                "router_task": TRANSCRIBE_TASK,
            },
        )
        minutes = Decimal(0)
    else:
        minutes = (seconds / _SECONDS_PER_MINUTE).quantize(_MINUTE_QUANTUM)

    # Metering is best-effort and NEVER fails the call.
    _record_completion(
        ExtractedUsage(),
        org_id=org_id,
        caller=caller,
        resolved=resolved,
        client_ref=client_ref,
        byok=_byok_served(resolved),
        quantity=minutes,
    )

    # 📌 The caller reads a transcript, never the verbose body the meter
    # asked for. One field, so the meter's choice of `response_format` stays
    # invisible from outside (clause 3).
    return {"text": _transcript_of(response)}


# ── H-46: the image endpoint and the speak endpoint (§6A.10c) ───────────────
#
# 🔴 **`015_tier_pricing.sql` registered `tier-image` and `tier-tts`, and
# `016_tier_task.sql` mapped them to `image` and `speak`. Neither tier had a
# door.** These two routes are that door. Both copy the transcribe route line
# for line: the DOOR declares the task, the `model` field names a TIER, the
# three customer walls stand before the provider call, and
# `_record_completion` writes the one usage row.
#
# ⚠️ **Neither tier is BOUND, so both routes answer 400 `tier_unknown` until
# the owner writes one `tier_binding` row each** (§6A.10c clause 4). Which
# vendor model we resell for pictures and for speech is a commercial
# decision, and an agent must not take it.

#: The ONE task the image endpoint serves. The route names it, exactly as
#: :data:`TRANSCRIBE_TASK` does, because the DOOR declares the task and the
#: Router never sniffs the payload (§6A.10c clause 1, D61 G-3).
IMAGE_TASK = "image"

#: The ONE task the speak endpoint serves (§6A.10c clause 2).
SPEAK_TASK = "speak"

#: How many pictures one request may ask for.
#:
#: 📌 **The chat route's own ceiling, not a second one.**
#: ``CompletionRequest.n`` caps completions at four for exactly this reason —
#: `n` multiplies what one request costs us, so fifty would be a 50x draw on
#: the provider account from one zero-balance trial call.
_MAX_IMAGES_PER_CALL = 4

#: How much text one speak request may carry.
#:
#: *Agent default*, anchored on the vendor rather than invented, exactly as
#: :data:`_MAX_AUDIO_BYTES` is: the OpenAI speech endpoint refuses an input
#: above 4096 characters. A longer one buys a round trip and a vendor
#: refusal, and the Router holds the text in memory to replay it across a
#: failover.
_MAX_SPEECH_CHARACTERS = 4096


class ImageRequest(BaseModel):
    """One picture request, addressed to a TIER.

    ⚠️ ``extra="forbid"``. Everything the caller may forward is named here,
    and anything else is refused rather than passed through — the same
    allowlist rule ``CompletionRequest`` states.
    """

    #: 🔴 A TIER ALIAS, never a model id (clause 1). `016_tier_task.sql` maps
    #: `tier-image` to the `image` task, so the alias declares the task.
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    #: ⚠️ **What the caller ASKS for, and never what we bill.** The meter
    #: counts the pictures the provider RETURNED (clause 5). It is CLAMPED
    #: because it multiplies what one request costs us.
    n: int | None = Field(default=None, ge=1, le=_MAX_IMAGES_PER_CALL)
    #: The caller's own correlation id, trusted for nothing.
    client_ref: str | None = None

    # 🔴 **THERE IS NO `size` FIELD, and the absence is the rule** (clause 1).
    # The vendor prices a picture BY SIZE. litellm 1.86.0 holds
    # `standard/1024-x-1024/dall-e-3` at 3.81469e-08 per pixel, which is
    # $0.040, and `standard/1024-x-1792/dall-e-3` at 4.359e-08, which is
    # $0.080. Our own price column carries NO size axis:
    # `feed.py` reads `output_cost_per_image` off the BARE model key, and
    # `019_per_unit_vendor_costs.sql` states `vendor_per_image_usd` as "USD
    # per generated image". So one number lands in the column, and a caller
    # who picked the size would pick which half of our bill we record. `n` is
    # clamped for exactly that reason, and `size` multiplied the same cost
    # with no ceiling at all. `extra="forbid"` turns a `size` field into a
    # 422. Offering sizes needs a size dimension on the vendor price first —
    # `HANDOFF.md` H-87 carries the shape.

    model_config = {"extra": "forbid"}


class SpeechRequest(BaseModel):
    """One speech request, addressed to a TIER. The answer is audio bytes."""

    #: 🔴 A TIER ALIAS, never a model id (clause 2). `016_tier_task.sql` maps
    #: `tier-tts` to the `speak` task.
    model: str = Field(min_length=1)
    #: 🔴 **The text we SEND is what the meter counts** (clause 6). A figure
    #: the caller reports is never read.
    #:
    #: ⚠️ **`min_length=1`, the same floor `ImageRequest.prompt` carries.**
    #: An empty string resolves the chain, opens the provider call and buys a
    #: vendor refusal for text the vendor will not read. The two bodies in
    #: one door pair must not disagree about that.
    input: str = Field(min_length=1, max_length=_MAX_SPEECH_CHARACTERS)
    voice: str = Field(min_length=1, max_length=100)
    response_format: str | None = Field(default=None, max_length=32)
    #: 📌 **Declared so the refusal can be a 400 rather than a 422.** Clause 3
    #: names "no streaming speech" as a non-goal, and this field is how a
    #: caller finds that out in the vocabulary the transcribe route already
    #: uses. `extra="forbid"` would answer 422 and say nothing.
    stream: bool | None = None
    client_ref: str | None = None

    model_config = {"extra": "forbid"}


def _serving_prelude(
    *,
    tier: str,
    task: str,
    org_id: str,
    caller: Any,
    client_ref: str | None,
) -> tuple[list[ResolvedTier], dict[str, router_mod.Credential | None], dict[str, str]]:
    """Resolve the chain, load its keys and verbs, and stand the three walls.

    🔴 **ONE prelude for the image door and the speak door.** The transcribe
    route wrote this shape first and the chat route wrote it before that. A
    third and a fourth copy is root ``CLAUDE.md`` §5's defect by name, so the
    two new routes share this function and the older two keep their own
    bodies until somebody moves them.

    🔴 **A CUSTOMER refusal leaves the transaction before it is raised**
    (§8.1 clause 3). A refusal row written on the serving connection rolls
    back with the raise, and then the meter records nothing.

    Raises:
        HTTPException: the 400 unknown tier, the 402 no credit, the 403 run
            ceiling — each after its refusal row is written — or the 503 for
            a chain with no step we can try.
    """
    unknown_tier: HTTPException | None = None
    refusal: HTTPException | None = None
    chain: list[ResolvedTier] = []
    credentials: dict[str, router_mod.Credential | None] = {}
    invocations: dict[str, str] = {}

    with get_engine().begin() as conn:
        try:
            chain = resolve_chain(conn, tier, task)
        except TierUnknown:
            unknown_tier = HTTPException(
                status_code=400,
                detail=(f"no binding for tier {tier!r} on task {task!r}; name a tier, not a model"),
            )

        if unknown_tier is None:
            credentials = _chain_credentials(conn, chain, org_id=org_id)
            invocations = _chain_invocations(conn, chain, task=task)
            refusal = _spend_refusal(conn, caller) if _spend_gate_enabled() else None

    if unknown_tier is not None:
        _record_refusal(
            REFUSAL_TIER_UNKNOWN,
            org_id=org_id,
            caller=caller,
            tier=tier,
            task=task,
            client_ref=client_ref,
        )
        raise unknown_tier

    if refusal is not None:
        _raise_spend_refusal(
            refusal, org_id=org_id, caller=caller, tier=tier, task=task, client_ref=client_ref
        )

    # A step we hold no key for, or no capability row for, is not a step. It
    # cannot be tried at all, so it is dropped rather than attempted and
    # counted as a failure.
    attempts = [
        step
        for step in chain
        if credentials.get(step.model.split("/", 1)[0]) is not None and step.model in invocations
    ][: router_mod.MAX_CHAIN_ATTEMPTS]

    if not attempts:
        raise _nothing_to_try(chain[0], invocations, task=task)

    return attempts, credentials, invocations


def _unmeasured(resolved: ResolvedTier, task: str) -> None:
    """Say out loud that nothing measured this call (clause 5, clause 6).

    The customer already holds the pictures or the audio, so the only choice
    left is whether we also lose the row. We keep the row and bill zero, and
    this line is how an unmeasured call becomes visible instead of merely
    cheap.
    """
    _log.warning(
        "router.unmeasured_quantity",
        extra={"router_model": resolved.model, "router_tier": resolved.tier, "router_task": task},
    )


@app.post("/v1/images/generations")
def images_generations(req: ImageRequest, caller: KeyCaller) -> Any:
    """Generate pictures, gate the call, and charge it per PICTURE.

    The same shape as ``audio_transcriptions`` and deliberately so: the
    organization comes from the API key, the model comes from the tier
    binding, the three customer walls stand BEFORE the provider call, and
    the usage row is written from numbers we observed.

    🔴 **``model`` is a TIER ALIAS, never a model id** (clause 1). A bare
    model id has no ``tier_binding`` row on this task, so it walks into the
    same 400 a chat tier does and pictures can never reach a chat model.

    🔴 **The quantity is the count of pictures the provider RETURNED**
    (clause 5), and never the request's ``n``. A provider that answers with
    two pictures for a request that asked for three bills two.

    ⚠️ **A response with no readable list of images bills ZERO, loudly.** It
    guesses at no count. 📌 That arm is STUB-ONLY today — litellm's
    ``ImageResponse`` always carries a list, so the ``None`` never arrives
    from a live vendor. ``router.image_count`` holds the measurement and the
    reason it stays.

    Declared ``def`` for the reason the module docstring gives — the engine
    is synchronous, and the provider coroutine is driven with ``asyncio.run``
    inside the threadpool worker.
    """
    org_id = caller.organization_id
    attempts, credentials, invocations = _serving_prelude(
        tier=req.model,
        task=IMAGE_TASK,
        org_id=org_id,
        caller=caller,
        client_ref=req.client_ref,
    )

    def _kwargs_for(step: ResolvedTier) -> dict[str, Any]:
        """Build the outgoing call for one step of the chain.

        ⚠️ ALLOWLIST, exactly as the chat and transcribe routes build one.
        ``api_base`` is ours alone, and nothing the caller sent reaches the
        provider except the prompt and the clamped ``n``.

        🔴 **NO ``size`` REACHES THE VENDOR.** The chat route states the house
        rule — the fields that multiply our cost are clamped. A size cannot be
        clamped, because every size the vendor sells has its own price and our
        profile holds one number. So the field is refused at the body instead.
        ``ImageRequest`` carries the whole argument.
        """
        cred = credentials[step.model.split("/", 1)[0]]
        assert cred is not None  # the attempts filter removed keyless steps
        out: dict[str, Any] = {
            "model": step.model,
            "prompt": req.prompt,
            "api_key": cred.secret,
            # D60 step two: the verb `model_capability` named for this pair.
            "invocation": invocations[step.model],
            "num_retries": 1,
            "timeout": 120,
        }
        if req.n is not None:
            out["n"] = req.n
        if cred.api_base:
            out["api_base"] = cred.api_base
        return out

    def _byok_served(step: ResolvedTier) -> bool:
        """Whose account ran this step. §3.4 turns on exactly this bit."""
        cred = credentials[step.model.split("/", 1)[0]]
        return bool(cred and cred.byok)

    def _note_failover(frm: ResolvedTier, to: ResolvedTier, status: int | None) -> None:
        _log.warning(
            "router.failover",
            extra={
                "fo_from": frm.model,
                "fo_to": to.model,
                "fo_status": status,
                "fo_tier": frm.tier,
                "fo_task": IMAGE_TASK,
            },
        )

    try:
        response, resolved = asyncio.run(
            router_mod.call_chain(attempts, _kwargs_for, _note_failover)
        )
    except HTTPException:
        raise
    except router_mod.UpstreamFailed as failed:
        # ONE mapping, shared with every serving route.
        raise _upstream_refusal(failed) from failed

    pictures = router_mod.image_count(response)
    if pictures is None:
        _unmeasured(resolved, IMAGE_TASK)
        pictures = Decimal(0)

    # Metering is best-effort and NEVER fails the call.
    _record_completion(
        ExtractedUsage(),
        org_id=org_id,
        caller=caller,
        resolved=resolved,
        client_ref=req.client_ref,
        byok=_byok_served(resolved),
        quantity=pictures,
    )

    return response


@app.post("/v1/audio/speech")
def audio_speech(req: SpeechRequest, caller: KeyCaller) -> Response:
    """Read text aloud, gate the call, and charge it per CHARACTER.

    🔴 **The answer is AUDIO BYTES, and not JSON** (clause 2). The route
    hands back the bytes the provider returned, under the provider's own
    content type, so a caller written against the OpenAI speech endpoint
    reads exactly what it expects.

    🔴 **The quantity is the count of characters we SEND upstream** (clause
    6), and never a figure the caller reports. A character count is a fact
    the REQUEST holds, while a picture count is a fact only the RESPONSE
    holds — the two routes measure at opposite ends on purpose.

    ⚠️ **A call that answered NO AUDIO bills zero, whatever we sent.** The
    request holds the count, and the response holds whether the count bought
    anything. ``speech_audio`` never raises, so a 200 with a body we cannot
    read is what an empty answer looks like from here. Billing the full text
    for it charges the customer for silence.

    🔴 **A truthy ``stream`` field is a 400** (clause 3, a D16 agent
    default). ``speak`` IS in ``STREAMABLE_TASKS``, and that membership
    changes nothing here: its one reader guards the OPERATOR capability
    write and never sees a serving request. The refusal is this endpoint's
    own contract, and it is the answer §6A.10a clause 2 already gives on the
    transcribe door. The reason is slice 11's failover walk — it is built
    for SSE, and its first-frame boundary has no meaning for an audio body.

    ⚠️ **The stream 400 writes NO usage row.** Migration 020's CHECK holds
    three slugs, and minting a fourth would be the second spelling §8.1
    forbids.
    """
    if req.stream:
        # Refused BEFORE anything is resolved or spent. A stream this route
        # cannot deliver must cost the customer nothing.
        raise HTTPException(
            status_code=400,
            detail=(
                "this endpoint does not stream; omit the 'stream' field and "
                "read the audio from the response body"
            ),
        )

    org_id = caller.organization_id
    attempts, credentials, invocations = _serving_prelude(
        tier=req.model,
        task=SPEAK_TASK,
        org_id=org_id,
        caller=caller,
        client_ref=req.client_ref,
    )

    #: What we SEND. The meter counts this string and nothing else.
    spoken = req.input

    def _kwargs_for(step: ResolvedTier) -> dict[str, Any]:
        """Build the outgoing call for one step of the chain. ALLOWLIST."""
        cred = credentials[step.model.split("/", 1)[0]]
        assert cred is not None  # the attempts filter removed keyless steps
        out: dict[str, Any] = {
            "model": step.model,
            "input": spoken,
            "voice": req.voice,
            "api_key": cred.secret,
            # D60 step two: the verb `model_capability` named for this pair.
            "invocation": invocations[step.model],
            "num_retries": 1,
            "timeout": 120,
        }
        if req.response_format is not None:
            out["response_format"] = req.response_format
        if cred.api_base:
            out["api_base"] = cred.api_base
        return out

    def _byok_served(step: ResolvedTier) -> bool:
        cred = credentials[step.model.split("/", 1)[0]]
        return bool(cred and cred.byok)

    def _note_failover(frm: ResolvedTier, to: ResolvedTier, status: int | None) -> None:
        _log.warning(
            "router.failover",
            extra={
                "fo_from": frm.model,
                "fo_to": to.model,
                "fo_status": status,
                "fo_tier": frm.tier,
                "fo_task": SPEAK_TASK,
            },
        )

    try:
        response, resolved = asyncio.run(
            router_mod.call_chain(attempts, _kwargs_for, _note_failover)
        )
    except HTTPException:
        raise
    except router_mod.UpstreamFailed as failed:
        raise _upstream_refusal(failed) from failed

    # 🔴 **READ THE ANSWER BEFORE THE METER RUNS.** `speech_audio` never
    # raises — a body it cannot read answers `(b"", "audio/mpeg")` by design.
    # So a provider that says 200 and sends nothing usable used to bill every
    # character we sent, while the customer got an empty 200. The image door
    # already bills what came BACK, and this door now agrees with it.
    audio, media_type = router_mod.speech_audio(response)

    if audio:
        characters = Decimal(len(spoken))
    else:
        # ⚠️ BILL ZERO, LOUDLY (clause 6). No audio came back, so nothing
        # measured this call and we guess at no length. The customer keeps
        # the 200 — an empty body their own player reports is better than a
        # 500 — and they keep their credits with it.
        _unmeasured(resolved, SPEAK_TASK)
        characters = Decimal(0)

    # Metering is best-effort and NEVER fails the call.
    _record_completion(
        ExtractedUsage(),
        org_id=org_id,
        caller=caller,
        resolved=resolved,
        client_ref=req.client_ref,
        byok=_byok_served(resolved),
        quantity=characters,
    )

    return Response(content=audio, media_type=media_type)


@app.get("/me/billing")
def my_billing(caller: PayingCaller) -> dict[str, Any]:
    """The calling organization's OWN billing summary. Read-only.

    Gated on ``can_pay``, not ``can_use_ai`` (moved 2026-08-30, §9.3(5)'s
    class): a **suspended** customer must be able to READ the bill they are
    being asked to pay. The old gate shut the billing page on exactly the
    customer who most needed it — the same defect the seats and members
    reads were moved off it for.

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
        is_byok = bool(
            conn.execute(
                text(
                    "SELECT 1 FROM provider_credential "
                    "WHERE organization_id = :o AND revoked_at IS NULL LIMIT 1"
                ),
                {"o": org_id},
            ).first()
        )

    return {
        "credits": {
            # ⚠️ floats — the one outlier from the strings-for-money rule.
            # Exact for NUMERIC(14,4) magnitudes (≤14 significant digits
            # round-trip float64), and the workbench billing page parses
            # numbers today, so the string flip is a two-release change:
            # consumer first (R6). Recorded as H-79.
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


# ══ CP-7 slice 1 · the spend reads (D66) ═══════════════════════════════════
#
# **What a customer may see about their AI spend: the activity, and the cost.**
# Not the model, not the provider, not the tier. D32.7 settled that customers
# never see a model, and D66 restated it for this surface specifically.
#
# ⚠️ **These two routes are READS, and CP-7's cap ENGINE is deliberately not
# here.** A cap decides whether a member may spend, so it must rest on an
# identity the member cannot choose. `X-CC-Member` is a request header the
# caller sets (`auth.py:497`, forwarded verbatim by `v1_compat.py:490`), so a
# cap enforced on it is evaded by omitting it. That is migration 005's defect
# class exactly — *the party being invoiced must not control whether it
# exists* — and H-73 carries it. Attribution is good enough to REPORT and not
# good enough to ENFORCE, and the gap between those two is this comment.
#
# **Authorisation note.** Both routes are organization-scoped by the key, like
# every other `/my/*` route. Neither can distinguish an admin from a member,
# because a `cc_live_` key IS the organization and reaches no role
# (`auth.py:155`). Deciding that *this viewer* may see *other people's* costs
# is the workbench's job — it holds the session, and the Console does not.


class ActivitySpendRow(BaseModel):
    activity: str
    calls: int
    credits: str


class ActivitySpendView(BaseModel):
    rows: list[ActivitySpendRow]
    windowDays: int
    #: Echoed back so the browser renders the scope it actually got rather than
    #: the one it asked for. They differ whenever the caller omits the filter.
    member: str | None = None


class MemberSpendRow(BaseModel):
    member: str
    calls: int
    credits: str


class MemberSpendView(BaseModel):
    rows: list[MemberSpendRow]
    windowDays: int


# ── The tier a customer picks (WS-31 slice 3) ───────────────────────────────
#
# Spec: `ai_metering_and_analytics.md` §8.4. D-AI-1 and D-AI-3 own the rules.
#
# 🔴 **Three fields leave, and the list is the contract.** D66 says a customer
# read names no model, no provider and no rate card, so this shape holds none
# of the three. Adding one is not a field, it is a decision.
#
# ⚠️ **The slug ships, and it never ships ALONE.** A picker has to send the
# tier back, and the slug is the wire name that every past usage row and every
# `tier_binding` row already carries. The customer sees the LABEL.


class CustomerTierRow(BaseModel):
    #: The wire name. Permanent, because a past invoice names it (D-AI-1).
    slug: str
    #: What the customer reads. The operator owns these words.
    label: str
    #: What picking this tier means, in the customer's own words.
    blurb: str


class CustomerTierView(BaseModel):
    rows: list[CustomerTierRow]


# ── Operator usage (WS-31, `specs/ai_metering_and_analytics.md` §5) ─────────
#
# 🔴 **THESE TWO CROSS TENANTS.** Every `/my/*` read above is scoped by the
# caller's own key. These are scoped by nothing but the `Operator` gate, which
# is therefore the whole of their security. Do not copy this shape onto a
# customer route.
#
# ⚠️ **Margin is a RATIO, never money.** `launch_surface.md` §4 sells
# "₹500/user/month + AI credits" and never says what a credit costs, so
# `billed_credits` and `provider_cost_usd` are different units. Subtracting
# them would invent an exchange rate, and an invented margin reads as fact.


class OrgUsageRow(BaseModel):
    slug: str
    name: str
    calls: int
    credits: str
    members: int
    costUsd: str
    balance: str
    lastSeen: str | None = None
    #: Credits billed per dollar of provider cost, judged over the calls
    #: whose cost was MEASURED — never all-calls credits over some-calls
    #: cost, which overstated margins 100x when coverage was thin. NULL
    #: when no cost is measured at all.
    marginRatio: str | None = None
    #: What fraction of this org's calls carry a measured cost (0..1, as a
    #: string). The context that stops marginRatio reading as authority it
    #: does not have. NULL when there are no calls.
    costedShare: str | None = None
    #: NULL means "no burn to extrapolate from", never "forever".
    runwayDays: int | None = None
    silent: bool = False
    #: How many times we REFUSED this organization over the same window
    #: (§8.1, A5). A plain int, because it is a count and not money.
    #:
    #: 🔴 **This is what makes a wall FINDABLE.** A refusal moves `last_seen`,
    #: so a walled customer stops reading as `silent` — and without this count
    #: the wall would make them HARDER to find than silence did. `refusals`
    #: above zero with `calls` at zero is an organization that got nothing
    #: through, which is the row support wants before the customer writes in.
    refusals: int = 0
    #: 🔴 **Calls this organization RECEIVED that we did not bill** (023, 025).
    #: The meter failed, so we absorbed the vendor's cost rather than send a
    #: number we could not defend.
    #:
    #: ⚠️ **The inverse of `refusals`, and reading them the same way is the
    #: mistake this comment exists to prevent.** A refusal is a customer we
    #: said NO to — they got nothing and owe nothing. This is a customer we
    #: said YES to and then did not charge: they hold their completion and we
    #: hold the vendor's bill. What is missing is our money, never their
    #: service.
    unbilledCalls: int = 0
    #: The tokens those calls consumed. ⚠️ Zero on an `usage_unreadable` row
    #: BY DEFINITION, so a large count beside a small token total is itself
    #: the signal that the provider's SHAPE broke and not our arithmetic.
    unbilledTokens: int = 0


class OrgUsageView(BaseModel):
    windowDays: int
    #: How many organizations exist, and how many this page carries.
    #:
    #: 🔴 The two differ once there are more than `SPEND_PAGE_SIZE` of them, and
    #: the rows that fall off are the QUIET ones — they sort last by spend, and
    #: they are exactly what the LEFT JOIN in `usage_by_org` exists to include.
    #: A page that did not say so would look complete while hiding the most
    #: actionable customers.
    total: int = 0
    shown: int = 0
    rows: list[OrgUsageRow]
    #: 🔴 Silent customers judged over EVERY organization, not the capped
    #: page (H-76). The page sorts by spend, so the funded-and-quiet customer
    #: A3 exists to find is the exact row the cap removes. Slugs only — the
    #: row data for the visible ones is already in `rows`.
    silentSlugs: list[str] = []
    #: 🔴 **Served and NOT billed, over every organization** (023, 025).
    #: Computed UNCAPPED, because a leak bills zero and a zero-credit row
    #: sorts off the spend-ordered page — so a total taken from `rows` would
    #: read zero exactly when it mattered most.
    unbilledOrgs: int = 0
    unbilledCallsTotal: int = 0
    unbilledTokensTotal: int = 0


class UsageDayRow(BaseModel):
    day: str
    calls: int
    credits: str


class UsageSeriesView(BaseModel):
    windowDays: int
    days: list[UsageDayRow]
    spikes: list[str]


@app.get("/admin/usage/orgs")
def admin_usage_by_org(
    _: Operator,
    days: int = store.SPEND_WINDOW_DAYS,
) -> OrgUsageView:
    """Every organization's AI usage, with margin, runway and the silent flag.

    Credits and costs are **strings**. They are money, the ledger stores
    `NUMERIC(14,4)`, and `float` is the standard way to make a total disagree
    with the sum of its rows.
    """
    days = max(1, min(int(days), store.USAGE_MAX_DAYS))
    with get_engine().begin() as conn:
        page = store.usage_by_org(conn, days=days)
        rows = page["rows"]
        balances = store.credit_balance_by_org(conn)
        # The burn window is its own read rather than a slice of the first —
        # a 30-day total cannot answer "what is the recent rate".
        # ⚠️ Uncapped on purpose: this dict is a FACT the page's rows are
        # judged from, and the 7-day spend ordering does not match the 30-day
        # one, so a page-sized burn read starves an edge of visible rows of
        # their runway — the same truncation class as H-76, one seam over.
        burn = {
            r["slug"]: r["credits"]
            for r in store.usage_by_org(
                conn,
                days=analytics.BURN_WINDOW_DAYS,
                limit=max(page["total"], 1),
            )["rows"]
        }
        last_seen = store.last_seen_by_org(conn)
        # 🔴 UNCAPPED, for the reason `last_seen_by_org` is. A leak bills zero
        # by definition, so the leaking organization sorts last and falls off
        # the page — the worse the leak, the more certainly it hides.
        unbilled = store.unbilled_fleet_total(conn, days=days)

    now = datetime.now(UTC)
    annotated = analytics.annotate_orgs(rows, balances, burn, now)
    # A3 over EVERYBODY. The per-row flag survives for the visible page;
    # this list is what stops the cap from hiding the quiet-but-funded.
    silent_slugs = sorted(
        slug for slug, bal in balances.items() if analytics.is_silent(bal, last_seen.get(slug), now)
    )
    return OrgUsageView(
        windowDays=days,
        # 🔴 Truncation is REPORTED, never silent. Rows sort by spend, so the
        # quiet customers the LEFT JOIN exists to include are the ones the cap
        # removes. The console says "100 of 563" rather than looking complete.
        total=page["total"],
        shown=page["shown"],
        silentSlugs=silent_slugs,
        unbilledOrgs=unbilled["orgs"],
        unbilledCallsTotal=unbilled["calls"],
        unbilledTokensTotal=unbilled["tokens"],
        rows=[
            OrgUsageRow(
                slug=r["slug"],
                name=r["name"],
                calls=r["calls"],
                credits=str(r["credits"]),
                members=r["members"],
                costUsd=str(r["cost_usd"]),
                balance=str(r["balance"]),
                lastSeen=r["last_seen"],
                marginRatio=(None if r["margin_ratio"] is None else str(r["margin_ratio"])),
                costedShare=(None if r["costed_share"] is None else str(r["costed_share"])),
                runwayDays=r["runway_days"],
                silent=r["silent"],
                refusals=r["refusals"],
                unbilledCalls=r["unbilled_calls"],
                unbilledTokens=r["unbilled_tokens"],
            )
            for r in annotated
        ],
    )


@app.get("/admin/usage/daily")
def admin_usage_daily(
    _: Operator,
    days: int = store.SPEND_WINDOW_DAYS,
    org_slug: str | None = None,
) -> UsageSeriesView:
    """AI usage per day, for the platform or for one organization.

    ⚠️ The series fills every gap. A client must not add a second gap fill —
    two of them disagree the first time one is changed.
    """
    days = max(1, min(int(days), store.USAGE_MAX_DAYS))
    with get_engine().begin() as conn:
        org_id = _org_id(conn, org_slug) if org_slug else None
        series = store.usage_daily(conn, days=days, org_id=org_id)

    return UsageSeriesView(
        windowDays=days,
        days=[
            UsageDayRow(day=r["day"], calls=r["calls"], credits=str(r["credits"])) for r in series
        ],
        spikes=analytics.spike_days(series),
    )


@app.get("/my/usage/activity")
def my_usage_by_activity(
    caller: KeyCaller,
    member: str | None = None,
) -> ActivitySpendView:
    """What this organization ran, and what it cost. **D66 (a).**

    Pass ``member`` to scope it to one person. The workbench fills that from
    the signed-in session, never from the browser — see the block comment
    above on why the Console cannot make that decision itself.

    Credits are returned as **strings**, not floats. They are money, the
    ledger stores `NUMERIC(14,4)`, and `float` is the standard way to make a
    total disagree with the sum of its rows.
    """
    with get_engine().begin() as conn:
        rows = store.usage_by_activity(
            conn,
            org_id=caller.organization_id,
            member=member,
        )
    return ActivitySpendView(
        rows=[
            ActivitySpendRow(
                activity=r["activity"],
                calls=r["calls"],
                credits=str(r["credits"]),
            )
            for r in rows
        ],
        windowDays=store.SPEND_WINDOW_DAYS,
        member=member,
    )


@app.get("/my/usage/members")
def my_usage_by_member(caller: KeyCaller) -> MemberSpendView:
    """Per-member cost inside this organization. **D66 (b).**

    ⚠️ **This does not read `member_ai_cap`, and it must not start.** Showing a
    cap beside a spend figure implies the cap is being enforced, and it is not
    (H-73). A number that looks like a control and is not one is worse than an
    absent number.
    """
    with get_engine().begin() as conn:
        rows = store.usage_by_member(conn, org_id=caller.organization_id)
    return MemberSpendView(
        rows=[
            MemberSpendRow(
                member=r["member"],
                calls=r["calls"],
                credits=str(r["credits"]),
            )
            for r in rows
        ],
        windowDays=store.SPEND_WINDOW_DAYS,
    )


@app.get("/my/tiers")
def my_tiers(_: KeyCaller) -> CustomerTierView:
    """The tiers this customer may pick, with the words they read.

    **`ai_metering_and_analytics.md` §8.4, D-AI-1 and D-AI-3.**

    🔴 **ONE source for a tier label.** `tier_catalog` holds the words and an
    operator edits them. A picker that carries its own list is a second source,
    and the two disagree the moment one of them is edited.

    ⚠️ **A hidden tier never reaches here.** `customer_visible` is FALSE on the
    six tiers the Router or the app selects, and `store.visible_tiers` drops
    them in SQL. A picker entry for one of those offers a choice no person can
    act on.

    **Authorisation.** The organization comes from the key, the same way
    `GET /my/usage/activity` takes it. The request body decides nothing, and
    there is no query parameter that names a tenant.

    ⚠️ **The answer holds no per-organization fact at all.** `tier_catalog` is
    the platform slate, so two organizations read the same rows. The key is
    still required, because the slate is our product surface and not a public
    page.
    """
    # ⚠️ The caller is named `_` on purpose, the way `billing_catalog` names
    # its own. The gate is the whole use of the credential here, because the
    # slate is the same product for every organization.
    with get_engine().begin() as conn:
        rows = store.visible_tiers(conn)
    return CustomerTierView(
        rows=[CustomerTierRow(slug=r["slug"], label=r["label"], blurb=r["blurb"]) for r in rows],
    )


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
        return CatalogView(
            plans=[
                CatalogPlanView(
                    slug=plan["slug"],
                    name=plan["name"],
                    kind=plan["kind"],
                    price_paise=payments.paise(plan["price_inr"]),
                    sort_order=plan["sort_order"],
                )
                for plan in store.active_plans(conn)
            ]
        )


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
        return MembersView(
            members=[MemberView(**row, seats=seats.get(row["email"], [])) for row in rows]
        )


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
            if with_lines
            else None
        ),
        discount=(
            OrderDiscountView(
                code_prefix=discount["code_prefix"],
                discount_paise=discount["discount_paise"],
            )
            if discount
            else None
        ),
    )


def _lines_for(conn, order_id: str) -> list[dict[str, Any]]:
    return [
        {
            "plan_slug": line["plan_slug"],
            "quantity": line["quantity"],
            "unit_price_paise": line["unit_price_paise"],
        }
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
        priced.append(
            {
                "plan_slug": plan["slug"],
                "quantity": line.quantity,
                "unit_price_paise": unit,
            }
        )
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
            conn,
            org_id=org_id,
            provider="razorpay",
            gross_paise=gross,
            discount_paise=0,
            taxable_paise=gross,
            gst_paise=gst,
            total_paise=total,
            gst_split=gst_split,
            customer_gstin=billing[0] if billing else None,
            place_of_supply=billing[1] if billing else None,
            expires_in_minutes=_ORDER_TTL_MINUTES,
            lines=priced,
        )
        # Inside the transaction on purpose: if the provider refuses, the local
        # order rolls back with it. The orphan this can leave is the harmless
        # direction — a provider order nobody pays, which expires there — and
        # never the dangerous one, a local order the customer cannot pay.
        created = provider.create_order(
            amount_paise=total,
            receipt=order_id,
            notes={"organization_id": org_id},
        )
        store.set_provider_order_id(
            conn,
            order_id=order_id,
            provider_order_id=created.provider_order_id,
        )
        _audit(
            conn,
            org_id,
            "order.create",
            {
                "order_id": order_id,
                "total_paise": total,
                "lines": [line["plan_slug"] for line in priced],
            },
            actor="organization",
        )
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
            conn,
            order_id=_valid_uuid(order_id),
            org_id=caller.organization_id,
        )
        if order is None:
            raise _no_such_order()
        return _order_view(conn, order, with_lines=True)


@app.get("/billing/orders")
def list_orders(
    caller: PayingCaller,
    status: str | None = None,
    cursor: str | None = None,
) -> OrderPageView:
    """That organization's orders, newest first (§9.3a).

    ``status`` is validated against the state machine's own set — an unknown
    value is **400, not silently ignored**, because a filter that quietly
    matches everything reads as "there are no failed orders".
    """
    if status is not None and status not in payments.ORDER_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown status {status!r}; expected one of {sorted(payments.ORDER_STATES)}",
        )
    with get_engine().begin() as conn:
        rows = store.orders_page(
            conn,
            org_id=caller.organization_id,
            limit=_ORDER_PAGE_SIZE,
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
    order_id: str,
    req: RedeemRequest,
    caller: PayingCaller,
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
        if store.count_redemptions(conn, code_id=code["id"]) >= code["max_redemptions"]:
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
    conn,
    *,
    order: dict[str, Any],
    code: dict[str, Any],
    org_id: str,
) -> OrderView:
    """Recompute the order's money, record the redemption, and — at zero — grant.

    GST is recomputed on the DISCOUNTED base (SC-4g (iii)): that is standard
    invoice practice and the only reading under which 100 percent off yields
    taxable 0 -> GST 0 -> total 0, which is what D42 requires. Discount after
    tax would leave GST payable on a zero-rupee sale.
    """
    discount = payments.discount_for(
        gross_paise=order["gross_paise"],
        kind=code["kind"],
        percent_bp=code["percent_bp"],
        amount_paise=code["amount_paise"],
    )
    taxable = order["gross_paise"] - discount
    gst = payments.gst_for(taxable)
    total = taxable + gst

    redemption_id = store.write_redemption(
        conn,
        code_id=code["id"],
        org_id=org_id,
        order_id=order["id"],
        gross_paise=order["gross_paise"],
        discount_paise=discount,
        net_paise=total,
    )
    if redemption_id is None:
        # Lost a race with an identical submission; the winner's row stands and
        # this call is the idempotent no-op done-when 5 requires.
        return _order_view(conn, order, with_lines=True)

    store.apply_discount_to_order(
        conn,
        order_id=order["id"],
        discount_paise=discount,
        taxable_paise=taxable,
        gst_paise=gst,
        total_paise=total,
    )
    _audit(
        conn,
        org_id,
        "discount.redeem",
        {
            "order_id": order["id"],
            "code_prefix": code["prefix"],
            "discount_paise": discount,
            "net_paise": total,
        },
        actor="organization",
    )

    if total == 0:
        # The Rs 0 path: `provider='none'`, no provider identifier, and ZERO
        # provider calls in this request. The order created earlier left an
        # unpaid provider order behind, which expires there — the harmless
        # orphan, named rather than discovered (SC-4g (iv)).
        store.detach_provider(conn, order_id=order["id"])
        payments.fulfil(
            conn,
            order_id=order["id"],
            reference=f"redemption:{redemption_id}",
        )
    else:
        # A PARTIAL code routes the remainder through the provider path — one
        # order, discount recorded, fulfilment on capture, no second flow. The
        # provider order is REPLACED because its amount is now wrong, and a
        # provider order that collects the pre-discount amount is a customer
        # overcharged by us.
        _replace_provider_order(conn, order_id=order["id"], total_paise=total, org_id=org_id)

    fresh = store.order_row(conn, order_id=order["id"], org_id=org_id)
    assert fresh is not None
    return _order_view(conn, fresh, with_lines=True)


def _replace_provider_order(conn, *, order_id: str, total_paise: int, org_id: str) -> None:
    """Re-create the provider order for the discounted amount."""
    try:
        provider = payments.provider()
    except payments.ProviderUnconfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    created = provider.create_order(
        amount_paise=total_paise,
        receipt=order_id,
        notes={"organization_id": org_id},
    )
    store.set_provider_order_id(
        conn,
        order_id=order_id,
        provider_order_id=created.provider_order_id,
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
            store.order_by_provider_id(conn, provider_order_id=event.provider_order_id)
            if event.provider_order_id
            else None
        )
        fresh = store.record_payment_event(
            conn,
            provider_event_id=event.event_id,
            order_id=order["id"] if order else None,
            kind=event.kind,
            body=json.dumps(event.body),
        )
        if not fresh:
            _log.info("payments.webhook_duplicate", extra={"event": event.event_id})
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
                extra={
                    "event": event.event_id,
                    "event_kind": event.kind,
                    "provider_order_id": event.provider_order_id,
                    "provider_payment_id": event.provider_payment_id,
                    "amount_paise": event.amount_paise,
                },
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
            extra={
                "order": order["id"],
                "event": event.event_id,
                "event_kind": event.kind,
                "status": order["status"],
                "provider_payment_id": event.provider_payment_id,
            },
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
            extra={
                "order": order["id"],
                "event": event.event_id,
                "expected_paise": order["total_paise"],
                "presented_paise": event.amount_paise,
            },
        )
        raise HTTPException(
            status_code=409,
            detail="captured amount does not match the order total",
        )

    try:
        payments.fulfil(
            conn,
            order_id=order["id"],
            reference=f"order:{order['id']}",
        )
        # The capture that grants seats and credits gets an audit row like
        # its manual twin — `GET /activity` showed a manual activation and
        # hid a paid one. Actor: the provider, whose verified signature
        # authorised the act.
        _audit(
            conn,
            order["organization_id"],
            "payment.captured",
            {"order_id": order["id"], "event": event.event_id, "amount_paise": event.amount_paise},
            actor="razorpay",
        )
    except TransitionRefused:
        # ⚠️ **TWO situations reach this arm and only ONE of them is benign**
        # (split 2026-08-19, review P0(b)). Branching on the order's status is
        # what tells them apart:
        if order["status"] == "captured":
            # The SECOND event of one capture (`payment.captured` and
            # `order.paid` carry different ids). Recorded, not fulfilled,
            # 200 — the money guard doing exactly its job.
            _log.info(
                "payments.already_fulfilled", extra={"order": order["id"], "event": event.event_id}
            )
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
            extra={
                "order": order["id"],
                "event": event.event_id,
                "status": order["status"],
                "provider_order_id": event.provider_order_id,
                "provider_payment_id": event.provider_payment_id,
                "amount_paise": event.amount_paise,
            },
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
def issue_discount(req: IssueDiscountRequest, staff: Operator) -> dict[str, Any]:
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
        raise HTTPException(status_code=400, detail="kind must be 'percent' or 'fixed'")
    if (req.kind == "percent") != (req.percent_bp is not None):
        raise HTTPException(
            status_code=400, detail="a percent code needs percent_bp and nothing else"
        )
    if (req.kind == "fixed") != (req.amount_paise is not None):
        raise HTTPException(
            status_code=400, detail="a fixed code needs amount_paise and nothing else"
        )

    minted = mint_key(env=ENV_DISCOUNT)
    with get_engine().begin() as conn:
        org_id = _org_id(conn, req.org_slug) if req.org_slug else None
        store.issue_discount_code(
            conn,
            prefix=minted.prefix,
            code_hash=minted.key_hash,
            label=req.label,
            kind=req.kind,
            org_id=org_id,
            percent_bp=req.percent_bp,
            amount_paise=req.amount_paise,
            max_redemptions=req.max_redemptions,
            expires_at=req.expires_at,
            created_by="operator",
        )
        # The audit row records the PREFIX, never the token.
        _audit(
            conn,
            org_id,
            "discount.issue",
            {
                "prefix": minted.prefix,
                "label": req.label,
                "kind": req.kind,
                "max_redemptions": req.max_redemptions,
            },
            actor=staff.actor,
        )

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
