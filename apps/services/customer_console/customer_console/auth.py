"""Four authentication schemes, deliberately separate.

Spec: ``project-docs/specs/customer_console.md`` §4.3 (CP-3) · §6 CP-2b (the
fourth) · ``user_management_contract.md`` R11 ("never trust a tenant from
request input").

  * **Operator** — a shared staff token. Reaches cross-organization surfaces:
    provisioning, seat writes, credit grants, key issuance, the console.
  * **Internal** — the Router's own token. Writes the meter, for any
    organization, because the Router legitimately serves all of them.
  * **Organization key** — ``cc_live_<prefix>_<secret>``, one per customer.
    **Read-only.** Identifies the caller and reports their balance; it cannot
    write a ledger row.
  * **Deployment key** — ``cc_depl_<prefix>_<secret>``, one per *deployment*.
    Reaches ``POST /registry/resolve`` and nothing else, with a capability set
    of exactly ``{resolve}``. It is the credential that lets a box ask *"this
    person just signed in — do you know them, and may they have a seat?"*

⚠️ **Why the fourth scheme rather than reusing one of the three.** The operator
token is cross-organization and staff-held, and this service already argues in
its own words that a tenant deployment must not hold it. The internal token is
the Router's and is equally cross-organization. The organization key is
org-scoped, which is right for ``/me`` and wrong here: a **pooled** deployment
must resolve *any* email to *its* org, and the org is not known before the
answer. So the deployment key's subject is a ``deployment``, and what it may
learn is bounded by ``org_placement`` — never a balance, never an invoice, and
never the existence of an organization it does not serve (CP-2b clauses 4-5).

⚠️ **Why the customer key cannot write the meter, and why that was a real bug.**
CP-3 first shipped ``/usage/record`` under organization-key auth. Independent
verification found two consequences, both measured on a live database:

  1. A negative ``billed_credits`` became a *positive* ledger delta — a
     customer-reachable **credit-minting endpoint** (balance 989 → 100,989).
  2. More fundamentally, it made the metered party the reporter of its own
     usage, which contradicts the argument the entire workstream rests on
     (``customer_console.md`` §4.1: *"Metering anywhere else means either
     trusting a client's self-report… That is not a meter, it is a
     suggestion."*).

The organization key's job is to authenticate the customer's **LLM traffic to
the Router**; the Router — our infrastructure, holding the internal token —
counts the tokens it has just proxied and writes the row. Restoring that
separation is what :data:`Internal` is for. Do not merge the two "because the
Router has the org key anyway": whoever can present a customer key must not be
able to move that customer's balance.

**The load-bearing rule, unchanged: the KEY resolves the organization.**
Attribution headers (``X-CC-Member``, ``X-CC-Agent``, ``X-CC-Module``,
``X-CC-Run``) refine *within* the organization the key pinned. A forged header
can misattribute usage inside one customer — their own problem — but can never
move a call, a charge or a read to a different customer.

This is why :func:`organization_from_key` returns the org id and the caller must
use it, rather than accepting an ``org_slug`` in the request body. A body field
naming the tenant is the exact shape R11 forbids: it makes the caller the
authority on who they are.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from customer_console import store
from customer_console.db import get_engine
from customer_console.keys import is_deployment_key, split_key, verify_secret
from customer_console.lifecycle import capabilities_of

__all__ = [
    "AUTHENTICATING_DEPENDENCIES",
    "RESOLVE_CAPABILITY",
    "Caller",
    "DeploymentCaller",
    "Internal",
    "KeyCaller",
    "Operator",
    "ResolveCaller",
    "deployment_or_operator",
    "organization_from_key",
    "require_internal",
    "require_operator",
]

#: The one capability a deployment key carries today. Named once so the string
#: that gates sign-in is not retyped at the mint site, the check and the tests.
RESOLVE_CAPABILITY = "resolve"


@dataclass(frozen=True)
class Caller:
    """A verified organization key holder."""

    organization_id: str
    key_prefix: str
    #: The owning organization's lifecycle state, already resolved alongside the
    #: key (:func:`customer_console.store.resolve_key` returns it so callers do
    #: not each run a second query). CP-6's balance gate needs it: a **trial**
    #: organization gets no overdraft grace, because an unpaid account is where
    #: overdraft turns into unrecoverable cost.
    #:
    #: Defaults to ``"trial"`` rather than ``"active"`` deliberately — a path
    #: that forgets to set it then denies grace instead of granting it.
    organization_status: str = "trial"
    #: Attribution only. Never used to select rows, never used to authorise.
    member: str | None = None
    agent: str | None = None
    module_slug: str | None = None
    run_id: str | None = None


def require_operator(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Refuse anything that is not the operator. Fails CLOSED when unconfigured."""
    expected = os.environ.get("CUSTOMER_CONSOLE_OPERATOR_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="CUSTOMER_CONSOLE_OPERATOR_TOKEN is not configured",
        )
    presented = ""
    if authorization and authorization.startswith("Bearer "):
        presented = authorization.removeprefix("Bearer ").strip()
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_internal(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Refuse anything that is not the Router (or another internal component).

    A **distinct** secret from the operator token, not an alias for it. The
    operator token is held by staff and used from consoles and scripts; this one
    is held by a service and used on the metering path. Sharing them would mean
    a leaked console credential could rewrite ledgers — and on the running
    Metorite box, `GATEWAY_INTERNAL_TOKEN` being byte-identical to
    `LITELLM_MASTER_KEY` is exactly that mistake, already recorded as an
    owner-gated defect (work_plan.md §6). Do not repeat it here.
    """
    expected = os.environ.get("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="CUSTOMER_CONSOLE_INTERNAL_TOKEN is not configured",
        )
    presented = ""
    if authorization and authorization.startswith("Bearer "):
        presented = authorization.removeprefix("Bearer ").strip()
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


# Which states may spend AI is decided by ONE state machine, in
# `customer_console.lifecycle`, read by every surface. This used to be a local
# frozenset here — a second copy of the answer, and the copy that drifts
# permissively is the one that gives away product.


def organization_from_key(
    authorization: Annotated[str | None, Header()] = None,
    x_cc_member: Annotated[str | None, Header()] = None,
    x_cc_agent: Annotated[str | None, Header()] = None,
    x_cc_module: Annotated[str | None, Header()] = None,
    x_cc_run: Annotated[str | None, Header()] = None,
) -> Caller:
    """Resolve the calling organization from its API key.

    Note what is deliberately absent: there is no ``X-CC-Org`` parameter and no
    ``org_slug`` body field. The organization is a property of the credential,
    full stop. If a caller sends ``X-CC-Org``, FastAPI ignores it — it is not
    bound anywhere — which is the desired outcome and is pinned by a test rather
    than left to a reader's confidence.

    Raises 401 for a missing, malformed, unknown or revoked key. The four cases
    return the SAME message on purpose: distinguishing "no such key" from "wrong
    secret" tells an attacker which half of their guess was right.
    """
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()

    parsed = split_key(token) if token else None
    if parsed is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    prefix, secret = parsed

    with get_engine().begin() as conn:
        resolved = store.resolve_key(conn, prefix=prefix)

    # Verify even when the prefix is unknown, against a dummy hash, so the two
    # rejection paths execute the same work.
    #
    # ⚠️ Do NOT record this as a proven constant-time property. Measured during
    # verification: one hash costs ~0.0006 ms against a ~3.3 ms request, and the
    # observed unknown-vs-wrong-secret delta was noise (and negative). What it
    # does not equalise is the real discriminator — an index hit versus a miss
    # on llm_api_key.prefix. It is cheap defence in depth; the property that IS
    # tested is that both paths return an identical response body.
    _dummy = "0" * 64
    if resolved is None:
        verify_secret(secret, _dummy)
        raise HTTPException(status_code=401, detail="Invalid API key")

    organization_id, key_hash, org_status = resolved
    if not verify_secret(secret, key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    # A live key belonging to a dead organization is still a dead organization
    # (F4 — verification found a `cancelled` org metering happily). 403, not
    # 401: the credential is valid and the caller should be told the account is
    # the problem, not sent to re-authenticate in a loop.
    if not capabilities_of(org_status).can_use_ai:
        raise HTTPException(
            status_code=403,
            detail=f"organization is {org_status}",
        )

    return Caller(
        organization_id=organization_id,
        key_prefix=prefix,
        organization_status=org_status,
        member=x_cc_member,
        agent=x_cc_agent,
        module_slug=x_cc_module,
        run_id=x_cc_run,
    )


@dataclass(frozen=True)
class DeploymentCaller:
    """A verified deployment key holder — CP-2b's fourth scheme.

    Note what is **not** here: no ``organization_id``. A deployment is not a
    tenant (D15 — a deployment is a *placement*), and which organizations this
    caller may see is a live property of ``org_placement``, resolved per
    request. Caching an org list on the credential would make a re-placement
    take effect whenever the key was last minted.
    """

    deployment_id: str
    key_prefix: str
    #: Exactly ``{resolve}`` today. Checked in the dependency below, before the
    #: route body runs — never by an ``if`` inside an endpoint.
    capabilities: frozenset[str] = frozenset({RESOLVE_CAPABILITY})


def deployment_or_operator(capability: str):
    """Build the dependency for an endpoint **both** schemes may open.

    One endpoint, two schemes, and the *credential* chooses which
    (``customer_console.md`` §6 CP-2b clause 3, clause 12). Two endpoints would
    be a second way to do an existing thing, which root ``CLAUDE.md`` §5
    forbids by name.

    **The token's shape dispatches, and it is not a fallback ladder.** A token
    whose canonical prefix names the deployment env goes to the deployment arm
    and is refused there or nowhere — it is never retried as an operator token.
    Anything else goes to :func:`require_operator`, unchanged, including its
    **503 when the operator token is unconfigured**: this endpoint must not
    become the one door that opens on a box where the rest of the service fails
    closed.

    A consequence worth stating, because it is a property and not an accident:
    an operator token that happens to be *shaped* like a deployment key is
    refused. That is the same rule
    ``test_a_key_shaped_operator_token_is_still_not_a_key`` already pins for
    the organization key, and the reason is identical — a credential's scheme
    must be decided by what it is, not by trying each comparison until one
    passes.
    """

    def _dependency(
        authorization: Annotated[str | None, Header()] = None,
    ) -> DeploymentCaller | None:
        """Return the deployment caller, or ``None`` for the operator arm."""
        token = ""
        if authorization and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ").strip()

        parsed = split_key(token) if token else None
        if parsed is None or not is_deployment_key(parsed[0]):
            require_operator(authorization)
            return None

        prefix, secret = parsed
        with get_engine().begin() as conn:
            resolved = store.resolve_deployment_key(conn, prefix=prefix)

        # Same shape as `organization_from_key`: verify against a dummy hash on
        # the unknown-prefix path so both rejections do the same work, and
        # return the SAME body either way — "no such key" told apart from
        # "wrong secret" tells an attacker which half of a guess was right.
        if resolved is None:
            verify_secret(secret, "0" * 64)
            raise HTTPException(status_code=401, detail="Invalid API key")

        deployment_id, key_hash, capabilities = resolved
        if not verify_secret(secret, key_hash):
            raise HTTPException(status_code=401, detail="Invalid API key")

        if capability not in capabilities:
            # 403, not 401: the credential is valid and the caller should be
            # told what it lacks rather than sent to re-authenticate in a loop.
            raise HTTPException(
                status_code=403,
                detail=f"deployment key lacks the {capability!r} capability",
            )

        return DeploymentCaller(
            deployment_id=deployment_id,
            key_prefix=prefix,
            capabilities=frozenset(capabilities),
        )

    return _dependency


Operator = Annotated[None, Depends(require_operator)]
Internal = Annotated[None, Depends(require_internal)]
KeyCaller = Annotated[Caller, Depends(organization_from_key)]

#: Built once at module scope rather than inline in the ``Annotated`` alias, so
#: the callable has a stable identity that :data:`AUTHENTICATING_DEPENDENCIES`
#: — and therefore the clause-1 fence — can recognise.
_resolve_dependency = deployment_or_operator(RESOLVE_CAPABILITY)

#: ``None`` means *the operator arm*; a :class:`DeploymentCaller` means the
#: deployment arm. The route reads the credential's identity, never the header.
ResolveCaller = Annotated[DeploymentCaller | None, Depends(_resolve_dependency)]

#: Every dependency in this module that authenticates somebody.
#:
#: This exists so CP-2b clause 1's fence
#: (``test_the_unauthenticated_route_set_is_exactly_health``) can DERIVE which
#: routes authenticate nothing instead of comparing one hand-list against
#: another. A fifth scheme added here is covered on the day it lands; one that
#: forgets to register itself makes every route it guards look unauthenticated,
#: which fails loudly rather than passing quietly.
AUTHENTICATING_DEPENDENCIES: frozenset = frozenset({
    require_operator,
    require_internal,
    organization_from_key,
    _resolve_dependency,
})
