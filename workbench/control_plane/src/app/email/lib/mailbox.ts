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

// ── The From row (EM-T8c, §11.4 and §11.7.3) ───────────────────────────────

interface MailboxLike {
  id: string;
  emailAddress: string;
  displayLabel?: string;
  workDomain?: string | null;
  needsReconnect?: boolean;
}

/** The label to show for a mailbox: the server's display label, else the
 *  address. The one copy of this rule; the chip and the From row use it. */
export function mailboxLabel(account: Pick<MailboxLike, "emailAddress" | "displayLabel">): string {
  return (account.displayLabel || "").trim() || account.emailAddress;
}

/**
 * True when the mailbox cannot send until the member reconnects it: a live
 * call answered 401, or the server says the last sync failed on the sign-in.
 *
 * Narrower than the reconnect banner, which shows for any sync error. A 429
 * or a 503 during an import also marks the sync as failed, and a send works
 * then, so it must not block one (EM-T8c review).
 */
export function sendBlocked(
  m: Pick<MailboxLike, "id" | "needsReconnect"> | null | undefined,
  authErrors: Readonly<Record<string, string>>,
): boolean {
  if (!m) return false;
  return !!authErrors[m.id] || m.needsReconnect === true;
}

export interface FromWarning {
  kind: "conversation" | "usual" | "domain";
  text: string;
  /** The mailbox that the one-click switch selects. Absent when that mailbox
   *  cannot send, so the warning shows with no switch. */
  switchTo?: string;
  switchLabel?: string;
}

/**
 * The first warning that applies to the chosen From mailbox, or null.
 * In order:
 * 1. A reply leaves the mailbox of its conversation.
 * 2. A recipient last heard from another mailbox of the member.
 * 3. A recipient is at the work domain of another mailbox, and the From
 *    mailbox is not at that domain.
 * Each warning offers the mailbox that fits better. A mailbox that needs a
 * reconnect is never offered.
 */
export function fromWarning(args: {
  fromId: string | null;
  accounts: ReadonlyArray<MailboxLike>;
  conversationAccountId?: string | null;
  recipients: ReadonlyArray<string>;
  usualSender?: Readonly<Record<string, string>>;
  authErrors?: Readonly<Record<string, string>>;
}): FromWarning | null {
  const { fromId, accounts, conversationAccountId, recipients } = args;
  const authErrors = args.authErrors ?? {};
  if (!fromId || accounts.length < 2) return null;
  const from = accounts.find((a) => a.id === fromId);
  if (!from) return null;
  const usable = (id: string | null | undefined) => {
    const m = accounts.find((a) => a.id === id);
    return m && m.id !== fromId && !sendBlocked(m, authErrors) ? m : null;
  };
  // A reply that leaves its conversation always says so, even when the
  // mailbox of the conversation cannot send: then the warning offers no
  // switch back (D-EM-20).
  const convAccount = conversationAccountId && conversationAccountId !== fromId
    ? accounts.find((a) => a.id === conversationAccountId) : undefined;
  if (convAccount) {
    const back = usable(convAccount.id);
    return {
      kind: "conversation",
      text: `This conversation is in ${mailboxLabel(convAccount)}. The recipients will see a new address, and the reply starts a new conversation.`,
      ...(back ? { switchTo: back.id, switchLabel: mailboxLabel(back) } : {}),
    };
  }
  const addrs = recipients.map((r) => r.trim().toLowerCase()).filter((r) => r.includes("@"));
  for (const addr of addrs) {
    const usual = usable(args.usualSender?.[addr]);
    if (usual) {
      return {
        kind: "usual",
        text: `You usually write to ${addr} from ${mailboxLabel(usual)}.`,
        switchTo: usual.id,
        switchLabel: mailboxLabel(usual),
      };
    }
  }
  for (const addr of addrs) {
    const domain = addr.split("@")[1];
    if (!domain || from.workDomain === domain) continue;
    const owner = accounts.find(
      (a) => a.id !== fromId && a.workDomain === domain && !sendBlocked(a, authErrors),
    );
    if (owner) {
      return {
        kind: "domain",
        text: `You are writing to a ${domain} address from ${mailboxLabel(from)}.`,
        switchTo: owner.id,
        switchLabel: mailboxLabel(owner),
      };
    }
  }
  return null;
}

/** The body after a change of From: the signature of the old mailbox becomes
 *  the signature of the new one, only while the old one is still in the body
 *  unchanged. A body the member edited around it keeps the old text. */
export function swapSignature(body: string, oldSig: string, newSig: string): string {
  if (!oldSig || !body.includes(oldSig)) return body;
  if (!newSig) return body.replace(oldSig, "").replace(/\s+$/, "");
  return body.replace(oldSig, newSig);
}
