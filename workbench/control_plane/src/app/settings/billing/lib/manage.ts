/**
 * Manage-seats view-model — the roster's SHAPE, the write bodies, and the
 * refusal partition. Deliberately no seat arithmetic.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2b (the assign/release
 * UI) · `customer_console.md` §6 item (i) (`MembersView`) / item (h) (the seat
 * WRITE) · SC-2a (the Next hops this drives) · R11.
 *
 * ⚠️ **Nothing here recomputes a seat count, and nothing ever should.** The
 * counts a customer sees are `GET /me/seats`'s (SC-1a) — `seats.ts`'s job — and
 * after every write the surface REFETCHES that read rather than adjusting a
 * number locally. This module only:
 *
 *   - names the roster's shape (`Member`, verbatim from the read);
 *   - builds the two write bodies (R11: the target, the plan, and — assign only
 *     — the seat `source`; never an org or an actor the browser could name);
 *   - reads the server's clamped `available` as a boolean GATE (`canAssign`),
 *     which is a comparison against the read's field, not a re-derivation of it;
 *   - relays the cap-409's `buy_more` payload VERBATIM into a sentence built
 *     from the server's own numbers (`additional_seats_required`, `assigned`,
 *     `purchased`) — never a count this surface computed.
 */

import type { SeatPlan } from "./seats";

/** One membership row, exactly as `GET /me/members` sends it (`MemberView`). */
export interface Member {
  email: string;
  role: string;
  status: string;
}

/** The whole roster read: one row per membership (`MembersView`). */
export interface MembersPayload {
  members: Member[];
}

/**
 * The roster the surface renders — the read's rows, VERBATIM, guarded against a
 * malformed 2xx (a Console `{}` relayed by `relayConsole`'s `body ?? {}`) so the
 * render never dereferences `.length` on a non-array and white-screens the page.
 */
export function readMembers(payload: MembersPayload | null): Member[] {
  return Array.isArray(payload?.members) ? payload.members : [];
}

/**
 * A self-serve seat's billing category — `alacarte`, never `core` (membership
 * IS the Core seat, D19.3). One of the Console's `_SELF_SERVE_SEAT_SOURCES`
 * (`{center, plan, alacarte}`, `customer_console/main.py`); a manage-UI assign
 * draws the generic à-la-carte category, and the Console refuses anything else
 * with a 400 rather than a CHECK-constraint 500.
 */
export const ASSIGN_SOURCE = "alacarte";

/**
 * The assign POST body for `/api/billing/seats/assign`.
 *
 * R11: names ONLY the target member, the plan and the seat source. The acting
 * admin is the SESSION (the gateway reads it from `X-User-Email`) and the org is
 * derived Console-side — a browser cannot name either here or at the hop.
 */
export function assignBody(
  memberEmail: string,
  planSlug: string,
): { member_email: string; plan_slug: string; source: string } {
  return { member_email: memberEmail, plan_slug: planSlug, source: ASSIGN_SOURCE };
}

/**
 * The release POST body for `/api/billing/seats/release`.
 *
 * The write twin, minus `source` — freeing a seat needs no billing category
 * (SC-2a). Same R11 shape otherwise.
 */
export function releaseBody(
  memberEmail: string,
  planSlug: string,
): { member_email: string; plan_slug: string } {
  return { member_email: memberEmail, plan_slug: planSlug };
}

/**
 * Whether a member may be assigned to this plan from the surface — the server's
 * clamped `available`, read as a boolean.
 *
 * Deliberately `> 0` and never `available === 0` / a re-derivation from
 * `purchased`/`assigned`: it reads the ONE field the Console already computed
 * and clamped (`seat_counts`, SC-1a). A cap hit that slips past this stale gate
 * (a race) is still caught — the Console answers 409 and {@link interpretSeatAction}
 * relays its `buy_more`.
 */
export function canAssign(plan: SeatPlan): boolean {
  return plan.available > 0;
}

/**
 * The cap-409's upsell payload, exactly as the Console sends it
 * (`seats.py` `AssignmentDecision.buy_more`). Every field is the server's; this
 * surface derives none of them.
 */
export interface BuyMore {
  plan_slug: string;
  purchased: number;
  assigned: number;
  additional_seats_required: number;
  price_inr: string | null;
}

/** The verdict of an assign/release, as the surface must act on it. */
export type SeatActionResult =
  | { kind: "ok" }
  | { kind: "cap"; buyMore: BuyMore }
  | { kind: "error"; message: string };

function isBuyMore(v: unknown): v is BuyMore {
  return (
    typeof v === "object" &&
    v !== null &&
    typeof (v as BuyMore).plan_slug === "string" &&
    typeof (v as BuyMore).additional_seats_required === "number"
  );
}

/**
 * Interpret an assign/release response.
 *
 *   - **2xx** → `ok`. The counts are NOT touched here; the surface refetches
 *     `GET /me/seats` (SC-1a), which is the one source of truth for them.
 *   - **409 with a `buy_more` body** → `cap`, carrying the server's payload
 *     VERBATIM. This is the cap the customer must buy past (no self-serve
 *     oversubscription, D19.3); the copy the surface shows is built from these
 *     numbers, never from arithmetic of its own.
 *   - **anything else** → `error`, surfacing the upstream `detail` when it is a
 *     string.
 */
export function interpretSeatAction(status: number, body: unknown): SeatActionResult {
  if (status >= 200 && status < 300) return { kind: "ok" };

  const detail = (body as { detail?: unknown } | null)?.detail;
  if (
    status === 409 &&
    typeof detail === "object" &&
    detail !== null &&
    isBuyMore((detail as { buy_more?: unknown }).buy_more)
  ) {
    return { kind: "cap", buyMore: (detail as { buy_more: BuyMore }).buy_more };
  }

  const message =
    typeof detail === "string" ? detail : `Could not update seats (${status})`;
  return { kind: "error", message };
}

/**
 * The sentence shown at the cap — assembled from the server's OWN figures.
 *
 * `additional_seats_required` is the Console's (it is `1` for a single assign),
 * `assigned`/`purchased` are the read's; nothing here subtracts or adds. Buying
 * more seats is the customer's next step (the cap never auto-upgrades, D32.5).
 */
export function buyMoreMessage(b: BuyMore): string {
  const price =
    typeof b.price_inr === "string" ? ` (₹${b.price_inr} each)` : "";
  const seats = b.additional_seats_required === 1 ? "seat" : "seats";
  return (
    `${b.plan_slug} is at capacity — ${b.assigned} of ${b.purchased} seats assigned. ` +
    `Buy ${b.additional_seats_required} more ${seats}${price} to assign another.`
  );
}
