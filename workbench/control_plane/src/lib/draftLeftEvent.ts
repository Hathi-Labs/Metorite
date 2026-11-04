/**
 * ── THE ONE "A DRAFT LEFT DRAFTS" SIGNAL ───────────────────────────────────
 *
 * Owner report, 2026-11-03 (task 1526). A chat reply card writes its reply to
 * Drafts at once. The member can then send or delete that draft in the Email
 * app while the card still waits. The card must learn it, or its Send mails
 * each person a second time.
 *
 * The Email app raises this event in the same tab after a successful send of
 * a draft (`sendDraft`) and after a successful delete of a message
 * (`deleteEmail`, which also deletes a draft). Both sit in
 * `app/email/lib/api.ts`, the one client of those two routes. A reply card
 * (`watchCardDraft` in `lib/replyCard.ts`) listens, and reads the state of
 * its own draft from the server. The event names a local id, and the card
 * trusts only the server's answer, never the event.
 *
 * Pure and DOM-guarded, so the node-env vitest holds it. Fence:
 * `src/lib/replyCardLeft.test.ts`.
 */

/** The window event name. Defined here once. */
export const DRAFT_LEFT_EVENT = "cc-email-draft-left";

/** Raise the event for one local message id. A non-browser render does nothing. */
export function announceDraftLeft(id: string): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new CustomEvent(DRAFT_LEFT_EVENT, { detail: { id } }));
  } catch {
    /* a browser with no CustomEvent: the card reads again on focus */
  }
}
