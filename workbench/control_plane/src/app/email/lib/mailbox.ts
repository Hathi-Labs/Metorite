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
 *  `reply-all` adds the To and Cc of the original. A duplicate keeps its
 *  first place (To before Cc).
 *
 *  `sending` is the address of the mailbox that sends the reply. Three cases:
 *  - The sending mailbox sent the original. The reply answers its
 *    recipients, as Outlook and Gmail do, without the own addresses. When
 *    only own addresses are left (mail from A to B of one member), they stay,
 *    and only the sending address leaves.
 *  - Another mailbox of the member sent it (mail from B, read in A). The
 *    sender stays: the member answers their own other address on purpose.
 *  - Anyone else sent it. The sender stays.
 *  In each case reply-all copies no other own address (D-EM-27, MB-7). */
export function replyRecipients(
  src: { from: Party; to?: Party[] | null; cc?: Party[] | null },
  mode: "reply" | "reply-all",
  own: ReadonlySet<string>,
  sending?: string | null,
): { to: string[]; cc: string[] } {
  const sendKey = (sending || "").trim().toLowerCase();
  const isOwn = (key: string) => own.has(key) || (!!sendKey && key === sendKey);
  const seen = new Set<string>();
  const keep = (
    addrs: Array<string | null | undefined>,
    drop: (key: string) => boolean,
  ): string[] => {
    const out: string[] = [];
    for (const raw of addrs) {
      const addr = (raw || "").trim();
      const key = addr.toLowerCase();
      if (!addr || drop(key) || seen.has(key)) continue;
      seen.add(key);
      out.push(addr);
    }
    return out;
  };
  const fromKey = (src.from.email || "").trim().toLowerCase();
  // With no sending address, any own address counts as the sender.
  const fromSelf = sendKey ? fromKey === sendKey : own.has(fromKey);
  const toList = (src.to || []).map((t) => t.email);
  let to: string[];
  if (fromSelf) {
    to = keep(toList, isOwn);
    if (to.length === 0) to = keep(toList, (key) => !!sendKey && key === sendKey);
  } else {
    to = keep([src.from.email], () => false);
    if (mode === "reply-all") to = [...to, ...keep(toList, isOwn)];
  }
  const cc = mode === "reply-all" ? keep((src.cc || []).map((c) => c.email), isOwn) : [];
  return { to, cc };
}
