"""The organization lifecycle — one state machine, read by every surface.

Spec: ``customer_console.md`` §4.1d · ``saas_operations_doctrine.md`` §2.1
· CP-2a.

    trial → active → past_due (grace: warnings, still working)
                   → suspended (login works · features locked · DATA RETAINED)
                   → cancelled (export window) → deleted

**Why this is a module and not an `if` in three places.** "Can this customer
sign in / use AI / add a seat" gets asked by the sign-in path, the Router, the
seat writer and the console. Four answers drift, and the one that drifts
permissively is the one that gives away product or money. So the state machine
is here, as data, and every surface reads it.

The distinction that matters most, and the one people get wrong:

    **`suspended` keeps LOGIN working while locking FEATURES.**

A suspended customer who cannot log in cannot pay you — they cannot see the
invoice, cannot update a card, cannot export. Locking them out converts a
recoverable billing problem into a churned account and a support ticket. The
same reasoning keeps sign-in open through `cancelled`: that is the export
window, and the export is the whole point of having one.

**Never delete customer data on non-payment without an export window.** It is a
trust matter, a DPDP matter, and the difference between a churned customer who
might come back and one who tells people not to buy from you.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "STATES",
    "OrgCapabilities",
    "TransitionRefused",
    "can_transition",
    "capabilities_of",
]


class TransitionRefused(Exception):
    """The requested lifecycle move is not on the graph."""


@dataclass(frozen=True)
class OrgCapabilities:
    """What an organization in a given state may do."""

    state: str
    #: Reach the product at all. TRUE for `suspended` — see the module note.
    can_sign_in: bool
    #: Spend AI credits. The first thing to go, because it is the thing that
    #: costs us money in real time.
    can_use_ai: bool
    #: Add seats or change entitlements.
    can_write_seats: bool
    #: Customer data still exists and is exportable.
    data_retained: bool
    #: Create an order, read your own orders, redeem a discount code (CP-9
    #: §9.3(5)). **True for every state except `deleted`**, and that breadth is
    #: the whole point: `can_use_ai` is the WRONG gate for paying, because it is
    #: false for exactly the `suspended` and `cancelled` customers who most need
    #: to pay — the same argument this module's own note makes about login.
    #:
    #: What it does NOT buy: capture never transitions `organization.status`
    #: (CP-9 done-when 16). A suspended org that pays is still suspended, and
    #: reinstatement stays an operator act through `POST /orgs/lifecycle` —
    #: letting an unattended webhook drive a state machine whose whole value is
    #: that a human is on every edge is how a replayed capture reinstates an
    #: account nobody decided to reinstate.
    #:
    #: ⚠️ **APPENDED LAST, and the next field must be too.** `STATES` below is
    #: built with KEYWORDS for this reason: when it was positional, a field
    #: inserted anywhere but last silently re-mapped every row's booleans —
    #: `suspended` would have become AI-enabled while every existing test kept
    #: passing. Keywords make that class of edit impossible rather than
    #: discouraged (CP-9 §9.3(5), repair-round nit 3). No default, for the same
    #: reason: a state row that forgets this field must fail to construct, not
    #: quietly inherit the permissive answer.
    can_pay: bool


#: The states, and what each permits. Ordered as the lifecycle runs.
#:
#: **Keyword construction, deliberately** — see `OrgCapabilities.can_pay`. The
#: six rows below were positional until CP-9 added a fifth boolean; the cost of
#: getting that wrong is silent and permissive, which is the worst pair.
STATES: dict[str, OrgCapabilities] = {
    "trial": OrgCapabilities(
        state="trial", can_sign_in=True, can_use_ai=True,
        can_write_seats=True, data_retained=True, can_pay=True,
    ),
    "active": OrgCapabilities(
        state="active", can_sign_in=True, can_use_ai=True,
        can_write_seats=True, data_retained=True, can_pay=True,
    ),
    # Grace. Everything still works — a customer cut off at the first missed
    # payment does not pay, they churn. Warnings are the product surface here,
    # not restrictions.
    "past_due": OrgCapabilities(
        state="past_due", can_sign_in=True, can_use_ai=True,
        can_write_seats=True, data_retained=True, can_pay=True,
    ),
    # Features locked, door open, data kept — and the checkout OPEN, which is
    # the state this whole distinction exists for.
    "suspended": OrgCapabilities(
        state="suspended", can_sign_in=True, can_use_ai=False,
        can_write_seats=False, data_retained=True, can_pay=True,
    ),
    # The export window. Sign-in deliberately still works, or the export the
    # window exists for is impossible.
    "cancelled": OrgCapabilities(
        state="cancelled", can_sign_in=True, can_use_ai=False,
        can_write_seats=False, data_retained=True, can_pay=True,
    ),
    # Terminal. Reached only from `cancelled`, i.e. only after a window.
    "deleted": OrgCapabilities(
        state="deleted", can_sign_in=False, can_use_ai=False,
        can_write_seats=False, data_retained=False, can_pay=False,
    ),
}

#: The transition graph. Anything not listed is refused — a state machine with
#: an "or whatever the caller asked for" branch is not a state machine.
#:
#: Note what is deliberately absent: **nothing reaches `deleted` except
#: `cancelled`.** There is no path from `suspended` or `past_due` straight to
#: deletion, so no sequence of operator actions can destroy a customer's data
#: without passing through the export window first. That is the rule enforced
#: by construction rather than by remembering.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "trial": frozenset({"active", "cancelled", "suspended"}),
    "active": frozenset({"past_due", "suspended", "cancelled"}),
    "past_due": frozenset({"active", "suspended", "cancelled"}),
    # Recoverable: a suspended customer who pays comes straight back.
    "suspended": frozenset({"active", "cancelled"}),
    # Reinstatement from cancelled is deliberate and allowed — a customer who
    # changes their mind inside the window should not need a new organization.
    "cancelled": frozenset({"active", "deleted"}),
    "deleted": frozenset(),
}


def capabilities_of(state: str) -> OrgCapabilities:
    """What this state permits. Unknown states fail CLOSED.

    An unrecognised value in the column — a hand-edit, a future state added to
    the schema but not here — must not read as "allowed". It reads as
    `deleted`: nothing permitted, loudly wrong, visibly broken.
    """
    return STATES.get(state, STATES["deleted"])


def can_transition(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    """Raise :class:`TransitionRefused` unless the move is on the graph."""
    if not can_transition(current, target):
        raise TransitionRefused(
            f"{current!r} → {target!r} is not a permitted transition"
            + ("; deletion is reachable only from 'cancelled', after the export "
               "window" if target == "deleted" else "")
        )
