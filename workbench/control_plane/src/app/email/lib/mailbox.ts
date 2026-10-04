/**
 * Which mailbox acts — the rules of WS-17 §11 (multi-inbox) that the UI
 * applies. Spec: `project-docs/specs/email_app_master_plan.md` §11.
 *
 * A member can connect several mailboxes. Every act on a mail runs in the
 * mailbox that holds that mail, never in the mailbox the view has selected
 * (D-EM-19). Before EM-T8a the inline reply sent from the selected mailbox, so
 * a mail of mailbox B opened while A was selected went out from A (MB-2, MB-3).
 */

import { accentForSlot, categoricalAccent, type CategoricalAccent } from "@/lib/categorical";
import { atStorageLimit, storageNoticeKind } from "./storage";

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
 * The accent of a mailbox (EM-T8b, D-EM-21): its stored slot, else a stable
 * hash of its id. A row that old code wrote after migration 227 has no slot
 * yet. The hue comes from the categorical ramp, never from a hex value.
 *
 * It lives here, in `lib/`, so a decision in `lib/` can read it with no
 * import from `components/` (EM-T8f-3 review F8). `components/MailboxChip.tsx`
 * re-exports it for its callers.
 */
export function mailboxAccent(
  account: { id: string; colorSlot?: number | null },
): CategoricalAccent {
  return account.colorSlot && account.colorSlot >= 1 && account.colorSlot <= 12
    ? accentForSlot(account.colorSlot - 1)
    : categoricalAccent(account.id);
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

// ── Keep separate (EM-T8g-2, D-EM-28 and D-EM-30) ──────────────────────────

/** A mailbox with the flag of "Keep separate". Absent means "in All inboxes",
 *  so a gateway before EM-T8g-1 keeps each mailbox in All inboxes. Only this
 *  file, `api.ts` and `types.ts` name the flag. Each other file takes this
 *  type and the functions below (review F2). */
export interface PoolFlag {
  inAllInboxes?: boolean | null;
}

/** True when the member keeps this mailbox out of All inboxes (D-EM-28). */
export function isSeparate(account: PoolFlag): boolean {
  return account.inAllInboxes === false;
}

/** A copy of `account` with the flag set: in All inboxes, or separate. */
export function withPoolFlag<T extends PoolFlag>(account: T, pooled: boolean): T {
  return { ...account, inAllInboxes: pooled };
}

/**
 * The mailboxes in All inboxes: each mailbox that is not separate, in the
 * order of the list. This is the one rule of the pool (D-EM-30). The All
 * inboxes row, its unread sum, the header count, the folder sums,
 * `scopeBusy`, `syncScope`, `pickInitialView` and the chat read it. Do not
 * count `accounts` for an All-inboxes decision. Fence:
 * `email-all-skips-separate` in `allInboxes.test.ts`.
 */
export function pooledMailboxes<T extends PoolFlag>(accounts: ReadonlyArray<T>): T[] {
  return accounts.filter((a) => !isSeparate(a));
}

/** True when All inboxes shows: two or more pooled mailboxes (D-EM-30). */
export function hasAllInboxes(accounts: ReadonlyArray<PoolFlag>): boolean {
  return pooledMailboxes(accounts).length > 1;
}

/**
 * The mailbox that All inboxes keeps selected out of view: the default when
 * it is pooled, else the first pooled mailbox, else null. The folder tree,
 * the label colours and the labels of All inboxes come from it, so it is
 * never a separate mailbox (EM-T8g-2 review F1 and F5).
 */
export function poolHome<T extends { id: string; isDefault?: boolean } & PoolFlag>(
  accounts: ReadonlyArray<T>,
): T | null {
  const pooled = pooledMailboxes(accounts);
  return pooled.find((a) => a.isDefault) ?? pooled[0] ?? null;
}

/**
 * The mailbox that the reconnect banner names: the selected one, or in All
 * inboxes the first mailbox that needs it. A separate mailbox counts too,
 * because a failure must never hide (EM-T8d review, EM-T8g-2 item 6).
 */
export function attentionMailbox<T extends { id: string; syncStatus?: string }>(state: {
  viewAll: boolean;
  selectedAccountId: string | null;
  accounts: ReadonlyArray<T>;
  authErrors: Readonly<Record<string, string>>;
}): T | null {
  const scope = state.viewAll
    ? state.accounts
    : state.accounts.filter((a) => a.id === state.selectedAccountId);
  return scope.find((a) => a.syncStatus === "error" || !!state.authErrors[a.id]) ?? null;
}

/**
 * The mailbox that the storage notice names (EM-T6e, D3), or null.
 *
 * - One mailbox in view: that mailbox, when it earns a notice
 *   (`storageNoticeKind`: at the limit, or the gap of D2). A separate mailbox
 *   shows its notice here, in its own view.
 * - All inboxes: the first POOLED mailbox at the limit, in the order of the
 *   list. A separate mailbox is never named here. The gap line shows in the
 *   own view of its mailbox only.
 *
 * Two surfaces win over the notice, and their mailbox is skipped:
 * - `attentionId`, the mailbox of the reconnect banner (`attentionMailbox`).
 *   A failing sync comes first.
 * - `storageStepId`, the mailbox whose guided setup draws the storage step.
 *   The step names it already (D6).
 *
 * The dialog acts on the id of the mailbox this returns, never on the
 * selected mailbox or `poolHome` (D3). Fence: `email-storage-mailbox` in
 * `allInboxes.test.ts`.
 */
export function storageMailbox<
  T extends { id: string } & PoolFlag & Parameters<typeof storageNoticeKind>[0],
>(state: {
  viewAll: boolean;
  selectedAccountId: string | null;
  accounts: ReadonlyArray<T>;
  attentionId?: string | null;
  storageStepId?: string | null;
}): T | null {
  const free = (a: T) => a.id !== state.attentionId && a.id !== state.storageStepId;
  if (!state.viewAll) {
    const box = state.accounts.find((a) => a.id === state.selectedAccountId);
    return box && free(box) && storageNoticeKind(box) !== null ? box : null;
  }
  return pooledMailboxes(state.accounts).find((a) => free(a) && atStorageLimit(a)) ?? null;
}

/** The item of the mailbox menu that moves a mailbox in or out of All
 *  inboxes. `nextPooled` is the value that the `PATCH` sends. */
export interface SeparateToggle {
  label: "Keep separate" | "Show in All inboxes";
  icon: "EyeOff" | "Inbox";
  nextPooled: boolean;
}

/**
 * The menu item of "Keep separate" (EM-T8g-2 item 1). A separate mailbox
 * offers "Show in All inboxes", and each other mailbox offers "Keep
 * separate". With one mailbox there is no All inboxes, so the menu offers
 * neither (§11.0).
 */
export function separateToggle(
  account: PoolFlag,
  accounts: ReadonlyArray<unknown>,
): SeparateToggle | null {
  if (accounts.length < 2) return null;
  return isSeparate(account)
    ? { label: "Show in All inboxes", icon: "Inbox", nextPooled: true }
    : { label: "Keep separate", icon: "EyeOff", nextPooled: false };
}

/** The word that the switcher row of a separate mailbox shows beside its
 *  chip, or null. With one mailbox nothing shows (§11.0). */
export function separateMark(account: PoolFlag, accounts: ReadonlyArray<unknown>): "Separate" | null {
  return accounts.length > 1 && isSeparate(account) ? "Separate" : null;
}

/**
 * The mailbox that "Open in inbox" moves the view to, or null to stay.
 * - One mailbox in view: a mail of another mailbox opens in its own mailbox
 *   (EM-T8a, MB-3).
 * - All inboxes: a mail of a separate mailbox opens in that mailbox, never
 *   in All inboxes (EM-T8g-2 item 4). A mail of a pooled mailbox stays in
 *   All inboxes with its chip (EM-T8d).
 * A mailbox that is not in the list never moves the view.
 */
export function mailboxToOpen(
  state: {
    viewAll: boolean;
    selectedAccountId: string | null;
    accounts: ReadonlyArray<{ id: string } & PoolFlag>;
  },
  mailAccountId: string | null | undefined,
): string | null {
  const box = mailAccountId ? state.accounts.find((a) => a.id === mailAccountId) : undefined;
  if (!box) return null;
  if (state.viewAll) return isSeparate(box) ? box.id : null;
  return box.id !== state.selectedAccountId ? box.id : null;
}
