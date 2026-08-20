/**
 * Seats view-model — the SHAPE of `GET /me/seats`, and deliberately no arithmetic.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-1a (the launch seats
 * block) · `customer_console.md` §6 item (g) / done-when 19.
 *
 * ⚠️ **Nothing here recomputes a seat count, and nothing ever should.**
 * `purchased`, `assigned`, `available` and `oversubscribed` are computed ONCE,
 * server-side, by the Console's one seat vocabulary (`customer_console/seats.py`
 * `seat_counts`, WS-31 §3.3) and delivered by `GET /me/seats`. `available`'s
 * zero-clamp and `oversubscribed` are the server's; this surface renders them
 * verbatim. A second implementation in the browser is exactly how two surfaces
 * come to disagree about how many seats a customer has left — the same argument
 * `lib/billing.ts` makes about the credit balance.
 *
 * In particular `oversubscribed` is a FLAG the read carries, never a thing this
 * module derives from `available == 0`: a plan can be oversubscribed with
 * `available` clamped to zero, and a plan can have zero available without being
 * oversubscribed. Surfacing the server's flag is SC-1a's "rather than hiding
 * behind a clamped `available == 0`".
 */

/** One plan's seat row, exactly as `GET /me/seats` sends it (`SeatPlanView`). */
export interface SeatPlan {
  plan_slug: string;
  purchased: number;
  assigned: number;
  available: number;
  oversubscribed: boolean;
}

/** The whole read: one row per plan the caller has touched (`SeatsView`). */
export interface SeatsPayload {
  plans: SeatPlan[];
}

/** The three numeric fields, the only ones shown as counts. */
export type SeatCountKey = "purchased" | "assigned" | "available";

/**
 * The counts to render, in read order, as `{ label, key }` descriptors.
 *
 * A descriptor, not a computation: each cell pulls ONE field off the plan by
 * key, so the rendered number IS the payload's number and there is nowhere for
 * arithmetic to creep in.
 */
export const SEAT_COUNTS: ReadonlyArray<{ key: SeatCountKey; label: string }> = [
  { key: "purchased", label: "Purchased" },
  { key: "assigned", label: "Assigned" },
  { key: "available", label: "Available" },
];

/**
 * The count cells for one plan — each value read VERBATIM off the payload.
 *
 * Returns whatever the read holds, even an internally inconsistent row: it is
 * the server's job to make the numbers agree, and a client that "fixes" them is
 * the second source of truth this module exists to avoid.
 */
export function seatCells(plan: SeatPlan): { label: string; value: number }[] {
  return SEAT_COUNTS.map((c) => ({ label: c.label, value: plan[c.key] }));
}

/**
 * Whether a plan is oversubscribed — the server's FLAG, returned as-is.
 *
 * Deliberately not `plan.available === 0` and not `plan.assigned > plan.purchased`:
 * both would be a re-derivation, and both can disagree with the server (a
 * grace-granted plan, a clamp). The read decides; this reads.
 */
export function isOversubscribed(plan: SeatPlan): boolean {
  return plan.oversubscribed;
}
