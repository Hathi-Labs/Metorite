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

/**
 * What the invite hop reported about the NOTIFICATION email (WS-30 SC-2c, D50).
 *
 * "disabled" is the shipped default (`MEMBER_INVITE_EMAIL_ENABLED` unset) and is
 * NOT a failure — the admin is told nothing, because there was nothing to
 * attempt. "failed" is the one state the admin must act on: the deployment DOES
 * send invite mail and this one did not go out, so the colleague will not hear
 * about the invite unless somebody tells them. A bare boolean cannot separate
 * those two, which is exactly the defect (review round 1, F2) this type closes.
 */
export type EmailChannel = "disabled" | "sent" | "failed";

/** What happened, across both steps. */
export type InviteOutcome =
  /** The invite itself failed. Nothing exists; nothing was seated. */
  | { kind: "invite-failed"; message: string }
  /** Member created, no seat attempted (the admin unticked it). */
  | { kind: "invited"; email: EmailChannel }
  /** Member created and seated. */
  | { kind: "invited-and-seated"; email: EmailChannel }
  /**
   * Member created; the seat was refused or unreachable.
   *
   * `atCap` separates "buy more seats" from "try again later", because they are
   * different next actions and conflating them sends an admin to the wrong one.
   */
  | { kind: "invited-not-seated"; message: string; atCap: boolean; email: EmailChannel };

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
export function afterInvite(
  seat: SeatActionResult | null,
  email: EmailChannel = "disabled",
): InviteOutcome {
  if (seat === null) return { kind: "invited", email };
  switch (seat.kind) {
    case "ok":
      return { kind: "invited-and-seated", email };
    case "cap":
      // The Console's own sentence, from the Console's own numbers — never a
      // count this surface derived (D32.5).
      return {
        kind: "invited-not-seated",
        message: buyMoreMessage(seat.buyMore),
        atCap: true,
        email,
      };
    case "error":
      return {
        kind: "invited-not-seated",
        message: seat.message,
        atCap: false,
        email,
      };
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
      return (
        `${who} is now a member. They hold no seat — assign one from Seat assignments when you are ready.` +
        emailSentence(outcome.email)
      );
    case "invited-and-seated":
      return `${who} is now a member and holds a seat.` + emailSentence(outcome.email);
    case "invited-not-seated":
      return (
        `${who} is now a member, but no seat was assigned. ` +
        outcome.message +
        (outcome.atCap
          ? " They are on the roster as unassigned in the meantime."
          : " You can assign one from Seat assignments once this clears.") +
        emailSentence(outcome.email)
      );
  }
}

/**
 * The email channel's clause — appended, never leading, because the MEMBER
 * existing is the fact the admin must not misread (the paragraph above), and
 * the mail is secondary. "disabled" says NOTHING: on a deployment that never
 * sends invite mail there is no news, and "no email went out" would read as a
 * failure on every single invite (the F2 defect, verbatim).
 */
function emailSentence(email: EmailChannel): string {
  switch (email) {
    case "sent":
      return " A notification email is on its way to them.";
    case "failed":
      return " The notification email did NOT go out — tell them to sign in with this address at app.metorite.com.";
    case "disabled":
      return "";
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
  if (outcome.kind === "invite-failed" || outcome.kind === "invited-not-seated") {
    return true;
  }
  // An armed deployment whose notification did not go out needs the admin to
  // read that and act (tell the colleague themselves) — a message that appears
  // as the dialog closes is a message nobody reads, same rule as above.
  return outcome.email === "failed";
}
