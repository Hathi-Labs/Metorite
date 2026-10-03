"use client";

import { accentForSlot, categoricalAccent, type CategoricalAccent } from "@/lib/categorical";
import type { EmailAccount } from "../lib/types";
import { mailboxLabel } from "../lib/mailbox";

export { mailboxLabel };

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

/** The accent of a mailbox: its stored slot, else a stable hash of its id
 *  (a row that old code wrote after migration 227 has no slot yet). */
export function mailboxAccent(account: Pick<EmailAccount, "id" | "colorSlot">): CategoricalAccent {
  return account.colorSlot && account.colorSlot >= 1 && account.colorSlot <= 12
    ? accentForSlot(account.colorSlot - 1)
    : categoricalAccent(account.id);
}


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
