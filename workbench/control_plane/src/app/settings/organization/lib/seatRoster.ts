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
 * - the **Console's** `/api/billing/members` — the billing plane's view, now
 *   carrying each member's live seat slugs (LS-7).
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
 * The real authorization is the Console's `_seat_admin_for_deployment`, and the
 * real capacity check is `decide_assignment`'s 409. This only decides whether
 * to draw an enabled button.
 */
export function canOfferSeat(row: SeatRow): boolean {
  return row.status !== "removed";
}
