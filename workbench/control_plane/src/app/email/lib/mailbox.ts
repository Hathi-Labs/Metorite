/**
 * Which mailbox acts — the rules of WS-17 §11 (multi-inbox) that the UI
 * applies. Spec: `project-docs/specs/email_app_master_plan.md` §11.
 *
 * A member can connect several mailboxes. Every act on a mail runs in the
 * mailbox that holds that mail, never in the mailbox the view has selected
 * (D-EM-19). Before EM-T8a the inline reply sent from the selected mailbox, so
 * a mail of mailbox B opened while A was selected went out from A (MB-2, MB-3).
 */

/** The mailbox of a mail. The selection is only the fallback for a mail that
 *  carries no account id (an optimistic row, an old cache). */
export function mailboxOf(
  email: { accountId?: string | null } | null | undefined,
  selectedAccountId: string | null | undefined,
): string | null {
  return email?.accountId || selectedAccountId || null;
}

/** Each address of a mailbox of the member, in lower case. "Self" covers every
 *  mailbox, so reply-all never copies the member to another of their own
 *  addresses (D-EM-27, MB-7). */
export function ownAddresses(
  accounts: ReadonlyArray<{ emailAddress?: string | null }>,
): Set<string> {
  const own = new Set<string>();
  for (const a of accounts) {
    const addr = (a.emailAddress || "").trim().toLowerCase();
    if (addr) own.add(addr);
  }
  return own;
}

interface Party {
  email?: string | null;
}

/** The To and Cc rows of a reply. `reply` answers the sender only.
 *  `reply-all` adds the To and Cc of the original. Each own address leaves,
 *  and a duplicate keeps its first place (To before Cc). */
export function replyRecipients(
  src: { from: Party; to?: Party[] | null; cc?: Party[] | null },
  mode: "reply" | "reply-all",
  own: ReadonlySet<string>,
): { to: string[]; cc: string[] } {
  const seen = new Set<string>();
  const keep = (addrs: Array<string | null | undefined>): string[] => {
    const out: string[] = [];
    for (const raw of addrs) {
      const addr = (raw || "").trim();
      const key = addr.toLowerCase();
      if (!addr || own.has(key) || seen.has(key)) continue;
      seen.add(key);
      out.push(addr);
    }
    return out;
  };
  // A reply to mail that the member sent answers its recipients, as Outlook
  // and Gmail do. Before, the own address left and the To row was empty.
  const fromSelf = own.has((src.from.email || "").trim().toLowerCase());
  const toRaw = fromSelf
    ? (src.to || []).map((t) => t.email)
    : mode === "reply-all"
      ? [src.from.email, ...(src.to || []).map((t) => t.email)]
      : [src.from.email];
  const to = keep(toRaw);
  const cc = mode === "reply-all" ? keep((src.cc || []).map((c) => c.email)) : [];
  return { to, cc };
}
