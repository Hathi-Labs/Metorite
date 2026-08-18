"""Three authentication schemes, deliberately separate.

Spec: ``project-docs/specs/customer_console.md`` §4.3 (CP-3) ·
``user_management_contract.md`` R11 ("never trust a tenant from request input").

  * **Operator** — a shared staff token. Reaches cross-organization surfaces:
    provisioning, seat writes, credit grants, key issuance, the console.
  * **Internal** — the Router's own token. Writes the meter, for any
    organization, because the Router legitimately serves all of them.
  * **Organization key** — ``cc_live_<prefix>_<secret>``, one per customer.
    **Read-only.** Identifies the caller and reports their balance; it cannot
    write a ledger row.

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
from customer_console.keys import split_key, verify_secret
from customer_console.lifecycle import capabilities_of

__all__ = [
    "Caller",
    "Internal",
    "KeyCaller",
    "Operator",
    "organization_from_key",
    "require_internal",
    "require_operator",
]


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


Operator = Annotated[None, Depends(require_operator)]
Internal = Annotated[None, Depends(require_internal)]
KeyCaller = Annotated[Caller, Depends(organization_from_key)]
