"""Seat accounting — purchased, assigned, available — and the cap decision.

Spec: ``project-docs/specs/customer_console.md`` §3.3 · decisions D19.3
(the hard cap, carried verbatim) and D32.5 (the three counts, defined once).

**Why this module is pure.** Every surface wants these numbers: the customer's
seat grid, the operator console, the invoice input, and the sign-in path that
decides whether a new member may join at all. Four surfaces recomputing "how
many seats are free" from their own SQL is how they come to disagree, and the
one that disagrees in the customer's favour is the one that costs money. So the
arithmetic lives here, once, as functions over plain values — and the SQL in
``store.py`` only *fetches* rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: A seat reduction is a NEGATIVE grant, never an UPDATE of an earlier row, so
#: purchased is a sum over signed quantities. Kept as the module's stated
#: contract because a reader seeing ``sum(...)`` may reasonably wonder whether
#: negatives are a data error; they are the design.
__all__ = [
    "CORE_PLAN_SLUG",
    "AssignmentDecision",
    "SeatCounts",
    "decide_assignment",
    "seat_counts",
]

#: Membership IS the Core seat (D19.3): joining an organization consumes one.
#: Named here rather than spelled in each caller so "what does joining cost"
#: has one answer.
CORE_PLAN_SLUG = "core"


@dataclass(frozen=True)
class SeatCounts:
    """The three numbers the owner named, for one ``(org, plan)`` pair."""

    plan_slug: str
    purchased: int
    assigned: int

    @property
    def available(self) -> int:
        """Free seats. **Clamped at zero.**

        Over-assignment should be impossible (``decide_assignment`` plus the
        partial unique index), but if it ever happens — a manual database edit,
        a seat count reduced below what is already assigned — the honest report
        is "0 free", not a negative number that a caller might add to and
        cheerfully treat as capacity. The over-assignment is still visible as
        ``assigned > purchased``; see :attr:`oversubscribed`.
        """
        return max(0, self.purchased - self.assigned)

    @property
    def oversubscribed(self) -> bool:
        """True when more seats are assigned than were bought.

        Not reachable through the API, and precisely why it is worth surfacing:
        it means someone edited the database or reduced a seat count under live
        assignments, and the operator console should say so rather than let
        ``available == 0`` hide it.
        """
        return self.assigned > self.purchased


@dataclass(frozen=True)
class AssignmentDecision:
    """Whether a seat may be assigned, and what to tell the caller if not."""

    allowed: bool
    #: HTTP status the API should return. 409 — not 402 — because the customer
    #: has a coherent subscription and is asking for something outside its
    #: *size*; 402 is reserved for "not paid for at all" (the entitlement axis),
    #: and conflating the two is what makes an admin unable to buy their way out
    #: of an error. See saas_operations_doctrine.md §2.3.
    status: int = 200
    reason: str = ""
    #: Payload for the upsell. The cap NEVER auto-upgrades: auto-upgrading is
    #: how a customer discovers a charge they did not authorise, and it is the
    #: fastest way to lose a small-business account (D19.3, D32.5).
    buy_more: dict | None = None


def seat_counts(
    plan_slug: str,
    grants: list[int],
    live_assignments: int,
) -> SeatCounts:
    """Fold raw rows into the three counts.

    Args:
        plan_slug: the plan these counts describe.
        grants: signed ``quantity_purchased`` values whose ``effective_from``
            has already passed. Filtering by time is the caller's job — this
            function must not read a clock, or it could not be tested without
            one.
        live_assignments: count of assignments with ``released_at IS NULL``.
    """
    return SeatCounts(
        plan_slug=plan_slug,
        purchased=sum(grants),
        assigned=live_assignments,
    )


def decide_assignment(
    counts: SeatCounts,
    *,
    already_assigned: bool = False,
    price_inr: Decimal | None = None,
) -> AssignmentDecision:
    """Decide whether one more seat may be assigned on this plan.

    Args:
        counts: the current counts for the ``(org, plan)`` pair.
        already_assigned: the target member already holds a live seat on this
            plan. Re-assignment is then a **no-op that succeeds**, not a
            consumption — assignment has to be idempotent because the sign-in
            path calls it on every sign-in, and a member signing in twice must
            not burn two seats. This is the single most likely way a seat
            counter drifts upward in production.
        price_inr: unit price, echoed into the upsell payload so the UI can say
            what the next seat costs without a second lookup.
    """
    if already_assigned:
        return AssignmentDecision(allowed=True, reason="already_assigned")

    if counts.available > 0:
        return AssignmentDecision(allowed=True)

    return AssignmentDecision(
        allowed=False,
        status=409,
        reason="seat_cap_exceeded",
        buy_more={
            "plan_slug": counts.plan_slug,
            "purchased": counts.purchased,
            "assigned": counts.assigned,
            "additional_seats_required": 1,
            "price_inr": str(price_inr) if price_inr is not None else None,
        },
    )
