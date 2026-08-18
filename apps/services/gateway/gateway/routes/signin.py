"""POST /signin/resolve — the ONE place the product asks the registry.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-2b clause 11 · §6(e) ·
§6(g) hop 3 · ``user_management_contract.md`` R11 · D32.4.

A sign-in is COMPLETING for this address; may it? The BFF's ``signIn`` callback
calls this before a session exists and turns the answer into an admit or a
refusal with honest copy.

## Why this route exists at all, instead of hanging the call off the auth path

``acb_auth.deps._with_resolved_access`` runs on **every authenticated request**,
and the email it carries is an ``X-User-Email`` the internal Bearer is trusted
to assert. Hang a **seat-allocating** call there and the seat cap becomes
farmable: internal token ⇒ resolve any address you like ⇒ burn a paying
customer's cap to 409 ⇒ their staff cannot sign in, and the customer's remedy is
to buy seats they did not need. So the resolve fires from exactly one site — the
completion of a sign-in — and ``_with_resolved_access`` keeps doing precisely
what it does today and gains nothing.

🔓 **Named accepted risk, pre-existing and NOT inherited silently — and it is
ONE credential, not two.** This route sits behind the app-wide
``require_authenticated``, which accepts the internal Bearer plus an
``X-User-Email`` of the caller's choosing and trusts it by design (*"whoever
holds the internal token can already assert any X-User-Email, so a narrower set
would be theatre"*, ``deps.py``). So the honest statement is: **a holder of the
INTERNAL TOKEN ALONE can drive resolve for arbitrary addresses, using this
box's own deployment key.** The attacker never needs the ``cc_depl_`` key; the
box presents it for them. Clause 11 reduces the surface from *every
authenticated request* to *one call site*; it does not remove it, and nothing
here does. The residual is bounded by seat idempotence — a second resolve for
the same address allocates nothing — so the cost is one seat per DISTINCT
address. **The gate that NARROWS it is §4.3's ``GATEWAY_INTERNAL_TOKEN`` /
``LITELLM_MASTER_KEY`` split (work_plan §6, §8 gate 1); today one secret opens
both doors.** Accepted, named, and unchanged by this ticket.

## Not in PUBLIC_ROUTES, deliberately

Authentication here is by construction — ``FastAPI(dependencies=[…])`` covers
every route including tomorrow's — and adding an entry to ``PUBLIC_ROUTES`` to
"make it reachable" is forbidden by name (root ``AGENTS.md`` constraint 10).
The BFF reaches it with the internal bearer, which it already holds.
"""

from __future__ import annotations

from typing import Annotated, Any

from acb_auth import UserContext, get_current_user
from acb_auth.console_resolve import resolve_for_signin
from acb_common import get_logger
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

_log = get_logger("gateway.signin")

router = APIRouter(prefix="/signin", tags=["signin"])


class SignInResolveRequest(BaseModel):
    """What the caller may say, which is deliberately almost nothing.

    ⚠️ **No ``email`` and no org.** The address comes from the authenticated
    context (``X-User-Email``, established by the caller that verified it with
    the IdP), never from the body — R11, and the same reason the Customer
    Console refuses a deployment key that names an ``org_slug``. ``display_name``
    is a LABEL for the projection: not an identity, not a tenant, and nothing
    branches on it.
    """

    display_name: str = Field(default="", max_length=200)


@router.post("/resolve")
async def resolve_sign_in(
    req: SignInResolveRequest | None = None,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Answer *admit* or *refuse with a code* for the person signing in.

    Always 200: the refusal is the ANSWER, not an error. A 4xx here would make
    "the Console says no" indistinguishable from "the route is broken" at the
    one call site whose whole job is telling those two apart, and the caller
    would render the wrong copy for both.

    With the Console unwired this admits without a call, a query or a write, so
    an unconfigured deployment behaves exactly as it did before CP-2b.
    """
    decision = await resolve_for_signin(
        (user.email or "") if user else "",
        display_name=(req.display_name if req else ""),
    )
    if not decision.admit:
        # One line per refusal, because the person on the other side is about
        # to see a page that cannot say why in any more detail than the copy.
        _log.warning(
            "signin.resolve_refused",
            code=decision.code,
            source=decision.source,
        )
    return {
        "admit": decision.admit,
        "code": decision.code,
        "source": decision.source,
    }
