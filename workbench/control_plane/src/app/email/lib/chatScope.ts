/**
 * The scope of the email chat: one mailbox, or All inboxes (WS-17 EM-T8e-3,
 * D-EM-23, §11.7.5 of `project-docs/specs/email_app_master_plan.md`).
 *
 * The chat starts on the scope of the page. The member can pick another scope
 * in the composer, and that pick holds for the chat only: it never calls
 * `selectAccount` or `selectAll`. In All inboxes the chat names no mailbox, so
 * no hidden `selectedAccountId` reaches the persona, the tool cards or the
 * settings read.
 *
 * The decisions live here, not in `EmailAssistantChat.tsx`, because vitest in
 * this tree runs in node and cannot render that component. Fence:
 * `chatScope.test.ts`.
 *
 * EM-T8f-3 adds two decisions. Each mailbox option of the picker carries the
 * dot of its mailbox (`chatMailboxOptions`). When the mailbox of the scope
 * leaves, the chat shows one note that names it and the new scope
 * (`rememberChatScope`, §11.6 case 17).
 */
import { ALL_INBOXES, pickInitialView } from "./emailStore";
import { chatMailboxName, type PersonaAccount } from "./emailAssistantPersona";
import { mailboxAccent } from "./mailbox";
import type { EmailAccount } from "./types";

/** A pick in the chat, held against the page scope it was made on. When the
 *  page scope moves, the pick drops, and the chat follows the page again. */
export interface ChatScopePick {
  against: string | null;
  scope: string;
}

export interface ChatScope {
  /** True in All inboxes: the chat reads each mailbox and names none. */
  allInboxes: boolean;
  /** The one mailbox of the chat. Null in All inboxes, or with no mailbox. */
  accountId: string | null;
  /** The option that the picker marks: `ALL_INBOXES` or a mailbox id. */
  pickerId: string | null;
}

/**
 * The scope of the chat. A pick wins while the page scope it was made on
 * stands. A scope on a mailbox that is gone falls back by the rule of
 * `pickInitialView`: All inboxes for two or more mailboxes, else the only
 * mailbox (§11.6 case 17).
 */
export function chatScope(
  accounts: ReadonlyArray<Pick<EmailAccount, "id" | "isDefault">>,
  pageScope: string | null,
  pick: ChatScopePick | null,
): ChatScope {
  const preferred = (pick && pick.against === pageScope ? pick.scope : null) ?? pageScope;
  const view = pickInitialView(accounts, preferred);
  if (view.viewAll) return { allInboxes: true, accountId: null, pickerId: ALL_INBOXES };
  return { allInboxes: false, accountId: view.accountId, pickerId: view.accountId };
}

/** One option of the picker. `accent` is the class of the colour dot of a
 *  mailbox, from the categorical ramp. All inboxes has none. */
export interface ChatMailboxOption {
  id: string;
  label: string;
  accent?: string;
}

/**
 * The options of the picker. "All inboxes" comes first, and only for two or
 * more mailboxes. Each mailbox shows as "label · address". With two or more
 * mailboxes, each mailbox option carries the dot of `mailboxAccent()`, never
 * a hex value (EM-T8f-3 item 3). One mailbox gets no dot, because the chips
 * show only for two or more mailboxes (§11.0).
 */
export function chatMailboxOptions(
  accounts: ReadonlyArray<PersonaAccount & { id: string; colorSlot?: number | null }>,
): ChatMailboxOption[] {
  if (accounts.length < 2) return accounts.map((a) => ({ id: a.id, label: chatMailboxName(a) }));
  const boxes = accounts.map((a) => ({
    id: a.id,
    label: chatMailboxName(a),
    accent: mailboxAccent(a).dot,
  }));
  return [{ id: ALL_INBOXES, label: "All inboxes" }, ...boxes];
}

/** What the chat holds of its scope from the render before (EM-T8f-3). */
export interface ChatScopeMemory {
  /** The option that the picker marked: `ALL_INBOXES`, a mailbox id, or null. */
  pickerId: string | null;
  /** The name of that scope: "All inboxes", or "label · address". */
  name: string | null;
  /** The note of §11.6 case 17, or null. */
  note: string | null;
}

/** The name of a scope: "All inboxes", "label · address", or null. */
export function chatScopeName(
  accounts: ReadonlyArray<PersonaAccount & { id: string }>,
  pickerId: string | null,
): string | null {
  if (!pickerId) return null;
  if (pickerId === ALL_INBOXES) return "All inboxes";
  const account = accounts.find((a) => a.id === pickerId);
  return account ? chatMailboxName(account) : null;
}

/** The note that names the mailbox that left and the new scope. */
export function removedScopeNote(removed: string, scope: string | null): string {
  return scope
    ? `${removed} is no longer connected. The chat now uses ${scope}.`
    : `${removed} is no longer connected. No mailbox is connected.`;
}

/**
 * One step of the scope memory of the chat. The scope itself is a pure
 * derivation (`chatScope`), so the mailbox that left is gone from it. The
 * memory holds the scope of the render before, with its name.
 *
 * - The same scope gives back `prev`, so a render loop stops. A rename
 *   keeps the note and takes the new name.
 * - A new scope, after a scope whose mailbox is no longer in `accounts`,
 *   gives one note that names that mailbox and the new scope (§11.6 case 17).
 * - Any other new scope, such as a pick, clears the note. All inboxes that
 *   ends because one mailbox is left is not a removed scope mailbox.
 */
export function rememberChatScope(
  prev: ChatScopeMemory | null,
  accounts: ReadonlyArray<PersonaAccount & { id: string }>,
  pickerId: string | null,
): ChatScopeMemory {
  const name = chatScopeName(accounts, pickerId);
  if (prev && prev.pickerId === pickerId) {
    return prev.name === name ? prev : { ...prev, name };
  }
  const left =
    prev?.pickerId && prev.pickerId !== ALL_INBOXES && !accounts.some((a) => a.id === prev.pickerId)
      ? prev
      : null;
  return {
    pickerId,
    name,
    note: left ? removedScopeNote(left.name ?? left.pickerId ?? "", name) : null,
  };
}

/** The read of the assistant settings of the chat mailbox, or null when no
 *  read runs. All inboxes reads none, because each mailbox has its own
 *  settings (D-EM-24). */
export function chatSettingsRead<T>(
  scope: Pick<ChatScope, "allInboxes" | "accountId">,
  read: (accountId: string) => Promise<T>,
): Promise<T> | null {
  if (scope.allInboxes || !scope.accountId) return null;
  return read(scope.accountId);
}
