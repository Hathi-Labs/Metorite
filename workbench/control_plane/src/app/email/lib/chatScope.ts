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
 */
import { ALL_INBOXES, pickInitialView } from "./emailStore";
import { chatMailboxName, type PersonaAccount } from "./emailAssistantPersona";
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

/** The options of the picker. "All inboxes" comes first, and only for two or
 *  more mailboxes. Each mailbox shows as "label · address". */
export function chatMailboxOptions(
  accounts: ReadonlyArray<PersonaAccount & { id: string }>,
): { id: string; label: string }[] {
  const boxes = accounts.map((a) => ({ id: a.id, label: chatMailboxName(a) }));
  return accounts.length > 1 ? [{ id: ALL_INBOXES, label: "All inboxes" }, ...boxes] : boxes;
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
