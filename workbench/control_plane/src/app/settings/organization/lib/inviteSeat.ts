/**
 * Invite-then-seat — the two-step outcome, and what to tell the admin.
 *
 * Owning spec: `project-docs/specs/launch_surface.md` §6.3 · LS-8. D49.
 *
 * ## Two systems, two transactions, and the rule that follows from it
 *
 * Inviting a member writes to the **gateway's** tenant database
 * (`POST /admin/members` → an `invited` row). Assigning a seat writes to the
 * **Console's** billing plane, through a different service, over a different
 * credential. There is no transaction spanning them and there cannot be.
 *
 * So: **the invite is never rolled back by a failed assign.** A member who
 * exists without a seat is exactly the *Unassigned* state the seat surface is
 * built to show and fix; un-inviting a colleague because billing was briefly
 * unreachable would destroy the durable half of the work to tidy up the
 * transient half. That is D49's fourth bullet, stated as code.
 *
 * What this module does is make the *reporting* honest. Four outcomes, and the
 * admin needs to be able to tell them apart, because their next action differs
 * in each:
 *
 *   - **invited and seated** — done, nothing to do.
 *   - **invited, no seat wanted** — done; the admin chose that.
 *   - **invited, seat refused at the cap** — buy seats, then assign. The member
 *     is real and waiting.
 *   - **invited, seat plane unreachable** — nothing to buy and nothing to fix;
 *     retry the assign later from the Seats tab.
 *
 * A single "invite failed / invite succeeded" boolean cannot express any of the
 * last three, which is why this is a type rather than a string.
 */

import type { SeatActionResult } from "@/app/settings/billing/lib/manage";
import { buyMoreMessage } from "@/app/settings/billing/lib/manage";

/** What happened, across both steps. */
export type InviteOutcome =
  /** The invite itself failed. Nothing exists; nothing was seated. */
  | { kind: "invite-failed"; message: string }
  /** Member created, no seat attempted (the admin unticked it). */
  | { kind: "invited" }
  /** Member created and seated. */
  | { kind: "invited-and-seated" }
  /**
   * Member created; the seat was refused or unreachable.
   *
   * `atCap` separates "buy more seats" from "try again later", because they are
   * different next actions and conflating them sends an admin to the wrong one.
   */
  | { kind: "invited-not-seated"; message: string; atCap: boolean };

/** Whether the flow left a usable member behind. */
export function memberExists(outcome: InviteOutcome): boolean {
  return outcome.kind !== "invite-failed";
}

/**
 * Fold a successful invite plus the seat attempt's verdict into one outcome.
 *
 * `seat` is `null` when no assign was attempted. Note the asymmetry with
 * {@link fromInviteFailure}: this function is only reachable once the invite
 * has succeeded, so every branch here reports a member that exists.
 */
export function afterInvite(seat: SeatActionResult | null): InviteOutcome {
  if (seat === null) return { kind: "invited" };
  switch (seat.kind) {
    case "ok":
      return { kind: "invited-and-seated" };
    case "cap":
      // The Console's own sentence, from the Console's own numbers — never a
      // count this surface derived (D32.5).
      return {
        kind: "invited-not-seated",
        message: buyMoreMessage(seat.buyMore),
        atCap: true,
      };
    case "error":
      return { kind: "invited-not-seated", message: seat.message, atCap: false };
  }
}

/** The invite itself failed. */
export function fromInviteFailure(message: string): InviteOutcome {
  return { kind: "invite-failed", message };
}

/**
 * The sentence shown to the admin.
 *
 * Every non-fatal outcome **names the member as existing** before it mentions
 * the seat. An admin who reads only the first clause of an error message must
 * not come away believing the invite did not land and re-send it — a duplicate
 * invite is harmless on the gateway (it reactivates) but the confusion is not,
 * and it is the predictable misreading of "Seat management is unavailable"
 * shown on its own.
 */
export function describe(outcome: InviteOutcome, name: string): string {
  const who = name.trim() || "The member";
  switch (outcome.kind) {
    case "invite-failed":
      return outcome.message;
    case "invited":
      return `${who} is now a member. They hold no seat — assign one from Seat assignments when you are ready.`;
    case "invited-and-seated":
      return `${who} is now a member and holds a seat.`;
    case "invited-not-seated":
      return (
        `${who} is now a member, but no seat was assigned. ` +
        outcome.message +
        (outcome.atCap
          ? " They are on the roster as unassigned in the meantime."
          : " You can assign one from Seat assignments once this clears.")
      );
  }
}

/**
 * Whether the dialog should stay open so the admin reads the message.
 *
 * A clean invite closes — the roster behind it is the confirmation. Anything
 * that needs a decision stays, because a message that appears as the dialog
 * closes is a message nobody reads.
 */
export function shouldStayOpen(outcome: InviteOutcome): boolean {
  return outcome.kind === "invite-failed" || outcome.kind === "invited-not-seated";
}
