/**
 * The seat roster view-model — who is seated, who is Unassigned, and nothing else.
 *
 * Owning spec: `project-docs/specs/launch_surface.md` §6 (the seat lifecycle a
 * customer admin drives) · LS-7. Decision D49.
 *
 * ## Two rosters, and why the gateway's is the one that decides who appears
 *
 * There are two lists of people:
 *
 * - the **gateway's** `/api/admin/members` — every member of the organization,
 *   with their status and roles. This is the org's directory.
 * - the **Console's**, carried by `/api/org/seats` — the registry plane's view,
 *   with each member's live seat slugs (LS-7). *(It reached this surface as the
 *   org-key `/api/billing/members` until CP-2h slice 1, 2026-08-24; the rows are
 *   the same `MemberView` either way, which is why only the fetch moved.)*
 *
 * A member who has **never** held a seat, or whose seat was **released**, is
 * absent from every seat query by construction. So if the surface were driven
 * by the Console's roster alone, releasing somebody's seat would make them
 * vanish from the screen that is supposed to offer the reassign. That is the
 * exact failure D49's second bullet names.
 *
 * Hence: **the gateway's roster decides who is listed; the Console's decides
 * who is seated.** A member the Console has never heard of is Unassigned, which
 * is true and actionable, rather than missing.
 *
 * ## What this module deliberately does not do
 *
 * No seat arithmetic, for the same reason `billing/lib/seats.ts` states: the
 * counts a customer sees are `GET /me/seats`'s, computed once in the Console's
 * one seat vocabulary (`customer_console/seats.py`, D32.5). Nothing here adds,
 * subtracts, or infers a count — it joins two lists and reports a state.
 */

import type { Member } from "@/app/settings/members/types";
import type { Member as BillingMember } from "@/app/settings/billing/lib/manage";
import type { SeatPlan } from "@/app/settings/billing/lib/seats";

/**
 * `GET /api/org/seats` — the Console's `SeatOverviewView`, as the wire sends it
 * (CP-2h slice 1, D-SEAT-4).
 *
 * ⚠️ **Two existing shapes, composed — not a third vocabulary.** `plans` is
 * exactly `SeatsPayload["plans"]` (`GET /me/seats`'s rows, the ONE seat
 * vocabulary) and `members` is exactly `MembersPayload["members"]`
 * (`GET /me/members`'s rows). Both fields are optional here for the reason
 * `readMembers` guards its own: a malformed 2xx must not white-screen the page.
 */
export interface SeatOverviewPayload {
  plans?: SeatPlan[];
  members?: BillingMember[];
}

/**
 * Read the overview payload into the two lists the surface renders.
 *
 * Array-guarded on both fields, `readMembers`'s guard applied to the second
 * half: the hop relays whatever the Console said, and a non-array `plans` would
 * crash the counts block on `.map`. Neither list is transformed — this is a
 * guard, not a view-model, and nothing here computes a seat count.
 */
export function readSeatOverview(payload: SeatOverviewPayload | null): {
  plans: SeatPlan[];
  members: BillingMember[];
} {
  return {
    plans: Array.isArray(payload?.plans) ? payload.plans : [],
    members: Array.isArray(payload?.members) ? payload.members : [],
  };
}

/**
 * How the seat plane answered a READ — the five outcomes the surface draws.
 *
 * ⚠️ **A refusal is not an outage, and collapsing the two costs an operator a
 * fake incident.** The first version of this surface branched on 503 and swept
 * every other non-2xx into "the seat plane did not answer" — but the Console
 * answers, precisely, three different things:
 *
 * - `ready` — 2xx. The payload is the org's grid and roster.
 * - `unconfigured` — 503. THIS deployment cannot reach the Console
 *   (`is_wired()` is false, or the gateway itself is unreachable). The
 *   customer's own state is unknown and unknowable; nobody did anything wrong.
 * - `restricted` — 403. `_admin_scheme_context` refused: the acting member is
 *   not an active `owner|admin` in the Console's registry for this org. That is
 *   **the ordinary case for every admin except the founder**, because the
 *   Console has no code path that writes `role='admin'` at all — its member-add
 *   door leaves `role` at the column default `member` on purpose (§6 CP-2f:
 *   mapping tenant roles onto registry roles would be the second grant
 *   vocabulary D12 forbids). So a second tenant-plane admin IS a Console
 *   `member` and IS 403 here. Drawing that as an outage told the largest group
 *   of real users that the platform was broken.
 * - `ambiguous` — 409. The acting email holds a membership in more than one
 *   organization placed on this deployment, and `_admin_scheme_for_deployment`
 *   will not guess between them (the chooser is a named non-goal). CP-2h slice
 *   2 threads the caller's SESSION org through and this outcome disappears.
 * - `error` — everything else non-2xx. A genuine "we do not know what that
 *   was", which is the only case that earns the red banner.
 */
export type SeatPlaneRead =
  | "ready"
  | "unconfigured"
  | "restricted"
  | "ambiguous"
  | "error";

/**
 * Classify `GET /api/org/seats`'s response — status first, payload as the check.
 *
 * Extracted from `SeatsTab` so the branch that decides "refusal or outage" is
 * reachable by a test at all; inline in a `useCallback` it was not, which is how
 * it stayed wrong.
 *
 * **Why `payload` is a parameter rather than decoration.** The status alone is
 * ambiguous on exactly one code. A 409 from the seat plane is the multi-org
 * verdict *on this door* — the overview makes no capacity decision — but the
 * SIBLING doors (`/seats/assign`) answer 409 with the cap payload
 * `{detail: {buy_more: …}}`, and both relay through the same gateway hop. If a
 * capacity 409 ever reached this read, telling the admin "your email is in two
 * organizations" would be a confident lie; an unrecognised 409 degrades to
 * `error` instead, which says only that we do not know. The 409 shape is
 * re-derived from the Console (`main.py` `_admin_scheme_for_deployment`, a bare
 * `{"detail": "<sentence>"}`) and pinned by the gateway relay's own fence,
 * `tests/unit/test_seat_admin_proxy_route.py::test_a_multi_org_409_surfaces_as_itself`.
 */
export function interpretOverviewRead(
  status: number,
  payload?: unknown,
): SeatPlaneRead {
  if (status >= 200 && status < 300) return "ready";
  if (status === 503) return "unconfigured";
  if (status === 403) return "restricted";
  if (status === 409) return hasCapDetail(payload) ? "error" : "ambiguous";
  return "error";
}

/** Whether a 409 body is the WRITE doors' cap payload rather than a verdict. */
function hasCapDetail(payload: unknown): boolean {
  const detail = (payload as { detail?: unknown } | null | undefined)?.detail;
  return (
    typeof detail === "object" &&
    detail !== null &&
    "buy_more" in (detail as Record<string, unknown>)
  );
}

/** One row on the seat surface: a person, and whether they hold a seat. */
export interface SeatRow {
  email: string;
  displayName: string;
  /** The org membership status — `active`, `invited`, `suspended`. */
  status: Member["status"];
  roles: string[];
  /**
   * Live seat plan slugs this member holds, from the Console.
   *
   * **Empty means Unassigned**, and that covers three situations the surface
   * treats identically because the admin's next action is identical: never
   * seated, seat released, and "the Console has no row for this person at all".
   */
  seats: string[];
  /**
   * True when the billing plane has never heard of this member.
   *
   * Distinguished from a plain empty `seats` for the ONE thing it changes: it
   * is normal for a freshly invited member and abnormal for a long-standing
   * one, so the surface can say "not in billing yet" rather than implying a
   * seat was taken away. It does not change what the admin can do.
   */
  unknownToBilling: boolean;
}

/** Whether this row holds any live seat. The whole of "Seated vs Unassigned". */
export function isSeated(row: SeatRow): boolean {
  return row.seats.length > 0;
}

/**
 * Join the two rosters into the list the surface renders.
 *
 * Ordering is the gateway roster's, untouched — the seat tab and the members
 * tab show the same people in the same order, and an admin moving between them
 * should not have to re-find anybody.
 *
 * `billing` may be `null` (the Console is unwired, or its read failed). Every
 * row is then Unassigned and `unknownToBilling`, which is honest: we genuinely
 * do not know, and the surface's job in that case is to say the seat plane is
 * unreachable rather than to draw an empty grid that looks like an answer.
 *
 * Emails are matched case-insensitively. The Console stores them as `CITEXT`
 * and the gateway does not, so the two can differ in case for one person — and
 * a case-sensitive join would silently report a seated member as Unassigned,
 * which is the kind of bug that only shows up for the one colleague who typed
 * their address with a capital.
 */
export function buildSeatRows(
  members: Member[],
  billing: BillingMember[] | null,
): SeatRow[] {
  const known = new Map<string, string[]>();
  if (billing) {
    for (const b of billing) {
      known.set(b.email.toLowerCase(), Array.isArray(b.seats) ? b.seats : []);
    }
  }
  return members.map((m) => {
    const key = m.email.toLowerCase();
    const seats = known.get(key);
    return {
      email: m.email,
      displayName: m.display_name || m.email,
      status: m.status,
      roles: m.roles ?? [],
      seats: seats ?? [],
      unknownToBilling: seats === undefined,
    };
  });
}

/** The counts shown above the list. Row states, never seat counts. */
export interface RosterTally {
  total: number;
  seated: number;
  unassigned: number;
}

/**
 * Tally the ROWS.
 *
 * ⚠️ These are not the seat counts, and must never be shown as them.
 * `seated` counts *people holding at least one seat*; `purchased` /
 * `assigned` / `available` come from `GET /me/seats` and are the Console's.
 * On an org where somebody holds two plans the two numbers legitimately differ,
 * and a surface that presented this tally as the seat count would be quietly
 * wrong exactly where it matters.
 */
export function tally(rows: SeatRow[]): RosterTally {
  const seated = rows.filter(isSeated).length;
  return { total: rows.length, seated, unassigned: rows.length - seated };
}

/**
 * Whether a seat may be offered to this member — a courtesy gate, not a rule.
 *
 * `removed` members are excluded because assigning a seat to somebody who is no
 * longer in the organization spends capacity on nobody. Everyone else is
 * offered it, **`invited` included**: seating a colleague before their first
 * sign-in is the normal onboarding order (D49's first bullet), and the whole
 * point of the invite→assign flow.
 *
 * `suspended` is deliberately still offerable. A suspended member holding a
 * seat is a real, chosen state — the org is paying to keep their place — and
 * the admin's remedy for the other case is Release, which is right there.
 *
 * The real authorization is the Console's `_admin_scheme_for_deployment` (the
 * name `_seat_admin_for_deployment` carried until it was split into
 * `_admin_scheme_context` + the per-door registry gate), and the real capacity
 * check is `decide_assignment`'s 409. This only decides whether to draw an
 * enabled button.
 */
export function canOfferSeat(row: SeatRow): boolean {
  return row.status !== "removed";
}
