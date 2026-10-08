"use client";

import type { EmailAccount } from "../lib/types";
import { mailboxAccent, mailboxLabel } from "../lib/mailbox";

// Both rules live in `lib/mailbox.ts`. The re-export keeps the callers of
// this file working (EM-T8f-3 review F8).
export { mailboxAccent, mailboxLabel };

/**
 * The identity of a mailbox on screen — WS-17 EM-T8b, §11.4 of
 * `project-docs/specs/email_app_master_plan.md`, decision D-EM-21.
 *
 * A member can connect several mailboxes, and each surface that can show two
 * of them draws one of these. The hue comes from the categorical ramp through
 * the stored `colorSlot` (1 to 12), never from a hex value (DESIGN_SYSTEM
 * rule 1). The label always goes with the hue, because the ramp collapses to
 * about four hues under deuteranopia (DESIGN_SYSTEM §7).
 */

type MailboxIdentity = Pick<EmailAccount, "id" | "emailAddress" | "colorSlot" | "displayLabel">;

/** The first letter of the label, for a round avatar. */
export function mailboxInitial(account: Pick<EmailAccount, "emailAddress" | "displayLabel">): string {
  return (mailboxLabel(account).match(/[\p{L}\p{N}]/u)?.[0] ?? "?").toUpperCase();
}

/** A round avatar: a tinted fill and the initial in the hue of the mailbox.
 *  The caller draws the label next to it, or the title names it. */
export function MailboxAvatar({ account }: { account: MailboxIdentity }) {
  const accent = mailboxAccent(account);
  return (
    <span
      className={`inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold ${accent.chip}`}
      title={`${mailboxLabel(account)} · ${account.emailAddress}`}
      aria-hidden="true"
    >
      {mailboxInitial(account)}
    </span>
  );
}

/** A chip: the dot and the label. The address is in the title. */
export function MailboxChip({ account }: { account: MailboxIdentity }) {
  const accent = mailboxAccent(account);
  const label = mailboxLabel(account);
  return (
    <span
      className={`inline-flex max-w-[10rem] flex-shrink-0 items-center gap-1 rounded-full border px-1.5 py-px text-[10px] font-medium ${accent.chip}`}
      title={account.emailAddress}
      aria-label={`Mailbox ${label}, ${account.emailAddress}`}
    >
      <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${accent.dot}`} aria-hidden="true" />
      <span className="truncate">{label}</span>
    </span>
  );
}
