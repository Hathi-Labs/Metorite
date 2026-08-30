"""What each operator role may reach — the matrix, and nothing else.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §5 · §8.1
done-whens 13-15 · **D64.3**. Board: WS-31 **CP-12c**.

**One table, checked before the route body runs.** Until CP-12c anybody who
signed in could do anything, and that included destroying a customer's tenant
plane (spec §2, F4).

⚠️ **The matrix FAILS CLOSED.** A route that reaches
:func:`customer_console.auth.require_operator` and has no row here is refused.
That is deliberate and it is the fence done-when 14 asks for: a new operator
route is shut until somebody decides who may use it, rather than open until
somebody remembers to close it. ``test_operator_roles.py`` names the same rule
at source level so the failure arrives in CI rather than in production.

⚠️ **The role is checked BEFORE the organization is read.** That is what makes
done-when 15 true: a ``viewer`` probing ``POST /credits/grant`` gets the same
403 whether the company exists or not, so the refusal is not an oracle for
which customers we have.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal

from customer_console.operators import ADMIN, EDITOR, VIEWER

_log = logging.getLogger("platform.operators")

__all__ = [
    "DEFAULT_CREDIT_ELEVATION",
    "MATRIX",
    "RoleForbidden",
    "RouteRule",
    "check_route",
    "credit_elevation",
    "rank",
    "rule_for",
]

#: Narrowest first. A role admits a route when its rank is at least the
#: route's, which is the whole of the comparison — there is no second
#: vocabulary of per-route grants (D12 keeps ``group:<slug>`` for TENANTS).
_RANK = {VIEWER: 0, EDITOR: 1, ADMIN: 2}

#: ⚠️ **CREDITS, not paise — a deliberate correction to spec §5.**
#:
#: The spec named this `OPERATOR_CREDIT_ELEVATION_PAISE`. `POST /credits/grant`
#: grants a Decimal quantity of CREDITS, and there is no credit-to-rupee rate
#: in this system today: the rate card ships UNPRICED on purpose (D19.2, and
#: pricing it is H-42, an owner decision). A threshold in paise would imply a
#: conversion that does not exist, and whoever added one later would be
#: inventing a price. So the unit is the one the route actually moves.
#:
#: The NUMBER still echoes **D33.4b**'s ₹15,000 auto-top-up cap, so the two
#: ideas of "large enough to need a second thought" stay one idea.
#: ⚠️ Re-derive it against the rate card once H-42 prices one.
DEFAULT_CREDIT_ELEVATION = 15_000


class RoleForbidden(Exception):
    """This role may not reach this route. Callers map this to **403**.

    ⚠️ ONE message for every cause, like
    :class:`customer_console.operators.OperatorForbidden`. A refusal that named
    the missing role would tell a caller how far off they were.
    """


@dataclass(frozen=True)
class RouteRule:
    """What a route demands."""

    min_role: str
    #: True when CP-12e must also find an open elevation window. The flag is
    #: recorded NOW so the matrix is written once — CP-12e reads it and adds
    #: the window check. Today it means "admin only", which is already the
    #: strongest thing this slice can enforce.
    elevated: bool = False


_R = RouteRule

#: **(METHOD, PATH) → what it demands.** The path is the ROUTE TEMPLATE as
#: FastAPI records it, not the requested URL.
MATRIX: dict[tuple[str, str], RouteRule] = {
    # ── Reads. Every operator sees the commercial record (D64.5) ────────────
    ("GET", "/orgs"): _R(VIEWER),
    ("GET", "/billing/summary"): _R(VIEWER),
    ("GET", "/billing/catalog"): _R(VIEWER),
    ("GET", "/credits/balance"): _R(VIEWER),
    # Key PREFIXES only. `keys.py` states in its own docstring that a prefix is
    # "stored in the clear, indexed, and safe to show or log", so listing them
    # discloses nothing a viewer may not see. The SECRET never leaves `POST`.
    ("GET", "/keys"): _R(VIEWER),
    # Which providers we hold an account with, and when each was installed.
    # ⚠️ NO SECRET, and not a fragment of one — `store.provider_credential_
    # list` does not select the ciphertext column at all. Viewer for the same
    # reason `GET /keys` is: knowing which vendors serve our customers is
    # ordinary operational knowledge, and hiding it teaches people to ask.
    ("GET", "/providers/credentials"): _R(VIEWER),
    ("POST", "/registry/seats/overview"): _R(VIEWER),
    # The audit trail (CP-12f). VIEWER on purpose, and it is the same
    # argument `GET /operators` makes: a record of who did what to our
    # customers is worth nothing if seeing it needs a privilege. It
    # discloses no secret — `control_audit.detail` records key and
    # discount PREFIXES only, never a token, and a test pins that.
    ("GET", "/activity"): _R(VIEWER),
    ("GET", "/activity/actions"): _R(VIEWER),
    # Cross-org AI usage (WS-31). VIEWER, for the same reason the audit trail
    # is: what our customers spend on AI is a thing the team should see
    # without asking, and a number nobody may read is a number nobody
    # notices going wrong. It discloses no secret - totals and credit
    # balances only, never a prompt, a response or a key.
    ("GET", "/admin/usage/orgs"): _R(VIEWER),
    ("GET", "/admin/usage/daily"): _R(VIEWER),

    # ── Day-to-day writes. An editor runs the business ──────────────────────
    ("POST", "/orgs/provision"): _R(EDITOR),
    ("POST", "/billing/subscriptions/activate"): _R(EDITOR),
    ("POST", "/billing/seats"): _R(EDITOR),
    ("POST", "/billing/seats/release"): _R(EDITOR),
    ("POST", "/registry/seats"): _R(EDITOR),
    ("POST", "/registry/seats/release"): _R(EDITOR),
    ("POST", "/registry/members"): _R(EDITOR),
    # ⚠️ Editor covers a grant AT OR BELOW the threshold only. The amount is in
    # the BODY, which a dependency cannot see, so the route body raises the bar
    # to `admin` above `credit_elevation()`. Both halves are tested.
    ("POST", "/credits/grant"): _R(EDITOR),

    # ── The sharp edges. Admin, and CP-12e adds a window ────────────────────
    ("POST", "/orgs/lifecycle"): _R(ADMIN, elevated=True),
    ("POST", "/keys"): _R(ADMIN, elevated=True),
    ("POST", "/keys/revoke"): _R(ADMIN, elevated=True),
    ("POST", "/discounts"): _R(ADMIN, elevated=True),
    ("POST", "/orgs/purge"): _R(ADMIN, elevated=True),
    # OUR provider accounts (CP-10 slice 1). Installing one is the sharpest
    # act on this console after a purge: the key it stores is what every
    # customer's AI call is billed against, and a wrong one stops the
    # product for everybody. Admin AND a window.
    ("POST", "/providers/credentials"): _R(ADMIN, elevated=True),
    # ── The model catalog (CP-10 slice 3) ───────────────────────────────
    # Reading it is ordinary work: what we can call, what we use it for,
    # what it costs, and the two gaps between those.
    ("GET", "/catalog/models"): _R(VIEWER),
    # Declaring a capability is a FACT about a model, not a commercial
    # term. Nobody is billed against it and it is reversible, so `editor`
    # and no window. Getting it wrong fails loudly at the provider.
    ("POST", "/catalog/capabilities"): _R(EDITOR),
    # ⚠️ Re-pointing a tier decides what EVERY customer call runs on. A
    # wrong model here does not fail loudly — it answers, plausibly, at
    # the wrong price. Same severity as installing a provider key above.
    # ⚠️ EDITOR, and NO elevation — unlike every other catalog write. A
    # profile changes neither what runs nor what we charge: it records what a
    # model IS, so an operator can choose one. Gating a description edit behind
    # elevation teaches people to reach for the break-glass token for routine
    # work, which is the opposite of what this matrix is for.
    ("POST", "/catalog/profiles"): _R(EDITOR),
    # The vendor feed (014) is the same severity as a profile: reference
    # data, nothing billing reads, and a bad sync is one more sync away
    # from fixed. EDITOR, no window — gating "fetch the current prices"
    # behind elevation would teach people the break-glass token.
    ("POST", "/catalog/feed/sync"): _R(EDITOR),
    ("POST", "/catalog/bindings"): _R(ADMIN, elevated=True),
    # ⚠️ This is what customers are BILLED. Admin and a window, and the
    # number itself stays the owner's commercial act (H-42, §8).
    # The model-keyed route is RETIRED (D67) and answers 410 — the row stays
    # so the refusal is authenticated, not an anonymous probe's oracle.
    ("POST", "/catalog/rates"): _R(ADMIN, elevated=True),
    ("POST", "/catalog/tier-rates"): _R(ADMIN, elevated=True),
    ("POST", "/providers/credentials/revoke"): _R(ADMIN, elevated=True),
    # Operator administration — admin only (D64.3), and NOT `elevated`.
    # Adding a colleague is ordinary work an admin does often, and a
    # window on it would train people to keep one open. The four guards
    # in `operators.py` are what make it safe, not a time box.
    ("GET", "/operators"): _R(VIEWER),
    # Signing MYSELF out is not a privilege (CP-12f2). ⚠️ There is no row
    # for `POST /operators/session`, and there must not be: that route is
    # the front door and carries no `Operator` dependency, so it never
    # reaches this matrix. `test_operator_signin.py` names the ungated
    # routes out loud so a THIRD one cannot appear unnoticed.
    ("DELETE", "/operators/session"): _R(VIEWER),
    ("POST", "/operators"): _R(ADMIN),
    # ⚠️ Opening a window is ADMIN and NOT itself `elevated` — a rule that
    # demanded a window to open a window could never be satisfied.
    ("POST", "/operators/elevate"): _R(ADMIN),
    # Reading and closing MY OWN window is not a privilege. A `viewer`
    # who somehow held one must be able to see and drop it.
    ("GET", "/operators/elevate"): _R(VIEWER),
    ("DELETE", "/operators/elevate"): _R(VIEWER),
    ("PATCH", "/operators/{operator_id}"): _R(ADMIN),
    ("DELETE", "/operators/{operator_id}"): _R(ADMIN),
}


def rank(role: str | None) -> int:
    """Where *role* sits. An unknown role ranks BELOW viewer, never above.

    ⚠️ Returning ``-1`` rather than raising is deliberate. A role this module
    does not recognise must be refused everywhere, and a comparison is harder
    to get wrong at a call site than an exception somebody may catch.
    """
    return _RANK.get(role or "", -1)


def rule_for(method: str, path: str) -> RouteRule | None:
    """What this route demands, or ``None`` when the matrix does not name it."""
    return MATRIX.get((method.upper(), path))


def credit_elevation(env: dict[str, str] | None = None) -> Decimal:
    """Above this many CREDITS, a grant needs an admin rather than an editor."""
    source = os.environ if env is None else env
    raw = (source.get("OPERATOR_CREDIT_ELEVATION") or "").strip()
    if not raw:
        return Decimal(DEFAULT_CREDIT_ELEVATION)
    try:
        value = Decimal(raw)
    except (ArithmeticError, ValueError):
        _log.warning("operator.bad_threshold")
        return Decimal(DEFAULT_CREDIT_ELEVATION)
    # A zero or negative ceiling would send every grant to an admin, which
    # LOOKS safe and in practice trains everybody to run as admin.
    return value if value > 0 else Decimal(DEFAULT_CREDIT_ELEVATION)


def check_route(role: str | None, method: str, path: str) -> RouteRule:
    """Raise :class:`RoleForbidden` unless *role* may reach this route.

    Returns the rule so a caller can read ``elevated`` without a second lookup.
    """
    rule = rule_for(method, path)
    if rule is None:
        # FAIL CLOSED. Reaching here means an operator-gated route exists that
        # nobody has classified, and guessing on its behalf is how a purge-
        # shaped route ends up open to a viewer.
        _log.warning(
            "operator.role_refused",
            extra={"role_why": "unmapped", "role_route": f"{method} {path}"},
        )
        raise RoleForbidden("forbidden")

    if rank(role) < rank(rule.min_role):
        _log.warning(
            "operator.role_refused",
            extra={
                "role_why": "rank",
                "role_route": f"{method} {path}",
                "role_held": role or "<none>",
            },
        )
        raise RoleForbidden("forbidden")

    return rule


def check_credit_amount(role: str | None, credits: Decimal,
                        env: dict[str, str] | None = None) -> None:
    """Raise when a grant is larger than an editor may make on their own.

    ⚠️ Only a POSITIVE grant is measured. A negative delta is a correction,
    and holding corrections to the admin bar would push people to fix a
    mistake by granting MORE rather than by reversing it.
    """
    if credits > credit_elevation(env) and rank(role) < rank(ADMIN):
        _log.warning(
            "operator.role_refused",
            extra={"role_why": "credit_amount", "role_held": role or "<none>"},
        )
        raise RoleForbidden("forbidden")
