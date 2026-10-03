/**
 * Shared persona builder for the email-assistant chat.
 *
 * Used by BOTH entry points so the agent gets the SAME context regardless of
 * where it runs:
 *   • the email app (EmailAssistantChat) — passes the connected accounts, the
 *     currently-selected account, and the open email;
 *   • the main chat app (chat/page.tsx) — passes the connected accounts only
 *     (there is no "open email" concept there).
 *
 * Keeping one builder means "run the email assistant in the chat app" feels the
 * same as "run it in the email app" — it's account-aware in both, and only the
 * open-email context (inherently email-app-only) differs.
 *
 * The scope (WS-17 EM-T8e-3, D-EM-23). The email app can run the chat in All
 * inboxes. Then the persona names no default mailbox and carries the settings
 * of no mailbox, because each mailbox has its own (D-EM-24). The chat app
 * passes no scope and stays on one mailbox. Fence: `chatScope.test.ts`.
 */

import { mailboxLabel } from "./mailbox";

export interface PersonaAccount {
  id: string;
  /** The raw stored label. Never drawn: two Outlook mailboxes share "Outlook"
   *  (MB-15). The display label wins. */
  label?: string | null;
  /** Email-store shape (camelCase). */
  emailAddress?: string | null;
  /** Gateway/API shape (snake_case). */
  email_address?: string | null;
  /** The label to draw (EM-T8b), store shape. */
  displayLabel?: string | null;
  /** The label to draw (EM-T8b), gateway shape. */
  display_label?: string | null;
}

export interface PersonaOpenEmail {
  id: string;
  /** The mailbox that holds the mail. An act on the mail runs in it (§11.3). */
  accountId?: string | null;
  subject?: string | null;
  from?: { name?: string | null; email?: string | null } | null;
}

/**
 * The ACTIVE account's own assistant configuration (email_assistant_settings).
 *
 * Every one of these is stored per account, and the drafting pipeline already
 * loads them by account_id — so a draft written for the work mailbox already
 * differs from one written for the personal mailbox. The CHAT assistant was the
 * one surface that didn't see them: it knew which account_id to pass to tools,
 * but nothing about how the user wants that mailbox handled, so it conversed
 * identically on every account. Carrying them here is what makes switching
 * accounts in the UI actually change the assistant's behaviour.
 */
export interface PersonaAccountSettings {
  /** Who the user is, in this mailbox's context. */
  about?: string | null;
  /** Standing rules the user always wants followed on this account. */
  personal_instructions?: string | null;
  /** How replies from this account should read. */
  writing_style?: string | null;
  /** Auto-derived from how the user edits drafts on this account. */
  learned_writing_style?: string | null;
}

function addr(a: PersonaAccount): string {
  return a.emailAddress || a.email_address || "";
}

/**
 * A mailbox as "label · address" (MB-15, §11.4). The label comes from
 * `mailboxLabel()`, so it is the display label, else the address. When the two
 * are the same, the address shows once. The picker and the persona use it.
 */
export function chatMailboxName(a: PersonaAccount): string {
  const address = addr(a).trim();
  const label = mailboxLabel({
    emailAddress: address,
    displayLabel: a.displayLabel ?? a.display_label ?? undefined,
  }).trim();
  if (label && address && label.toLowerCase() !== address.toLowerCase()) {
    return `${label} · ${address}`;
  }
  return address || label || a.id;
}

export function buildEmailAssistantPersona(opts: {
  accounts?: PersonaAccount[];
  selectedAccountId?: string | null;
  /** All inboxes (EM-T8e-3): the chat reads each mailbox and names none as
   *  the default. `selectedAccountId` and `settings` are then ignored. */
  allInboxes?: boolean;
  openEmail?: PersonaOpenEmail | null;
  /** The ACTIVE account's assistant settings. Omit where they aren't loaded —
   *  the persona degrades to account-awareness without the standing orders. */
  settings?: PersonaAccountSettings | null;
}): string {
  const accounts = opts.accounts ?? [];
  const allInboxes = opts.allInboxes === true;
  const parts: string[] = [
    "You are the Email Assistant, embedded in the user's email client. You can " +
      "read, search, query, categorize, draft, send, automate (rules), and " +
      "manage the inbox entirely by chat using your tools.",
  ];

  if (accounts.length > 0) {
    parts.push(
      "Connected accounts:\n" +
        accounts
          .map((a) => `• ${chatMailboxName(a)} (account_id ${a.id})`)
          .join("\n"),
    );
  }

  const active = allInboxes
    ? undefined
    : accounts.find((a) => a.id === opts.selectedAccountId);
  if (allInboxes) {
    // No default mailbox (D-EM-23). The tools bind a write act to one mailbox,
    // or ask, by the order of §11.3. The model must not guess one.
    parts.push(
      "Scope: All inboxes. The user works across every mailbox in the list " +
        "above, and no mailbox is the default. Leave account_id out of a " +
        "search or a list to read every mailbox, and name the mailbox of each " +
        "result. A read tool that needs an account_id reads one mailbox: call " +
        "it once for each mailbox, and never answer for all from one. Leave " +
        "account_id out of a write act (send, draft, rule, " +
        "setting): the tool takes the mailbox from the email, or asks the user " +
        "which mailbox. Give an account_id only when the user names a mailbox.",
    );
  } else if (active) {
    parts.push(
      `Active account: "${chatMailboxName(active)}" (account_id: ` +
        `${active.id}). Use this account_id for account-scoped tools unless the ` +
        "user names a different account.",
    );
  } else if (opts.selectedAccountId) {
    parts.push(
      `Active account_id: ${opts.selectedAccountId}. Use it for account-scoped ` +
        "tools unless the user names a different account.",
    );
  } else if (accounts.length === 1) {
    parts.push(
      `Use account_id ${accounts[0].id} for account-scoped tools unless the ` +
        "user names a different account.",
    );
  } else if (accounts.length > 1) {
    parts.push(
      "No specific account is selected — use the relevant account_id from the " +
        "list above (ask the user if it's ambiguous) before account-scoped " +
        "actions.",
    );
  } else {
    parts.push(
      "No accounts are loaded here — call list_accounts before any " +
        "account-scoped action.",
    );
  }

  // How the ACTIVE account wants to be handled. Scoped to that account, so
  // switching mailboxes in the UI switches the assistant's standing orders with
  // it. Trimmed and bounded — this rides in the system context on every turn.
  // All inboxes has no active account, so it carries no settings (D-EM-24).
  const cfg = allInboxes ? null : opts.settings;
  if (cfg) {
    const clip = (s: string, n: number) =>
      s.length > n ? `${s.slice(0, n)}…` : s;
    const block = (label: string, v?: string | null, n = 1200) => {
      const t = (v || "").trim();
      return t ? `${label}\n${clip(t, n)}` : "";
    };
    const cfgParts = [
      block("### About the user (this account)", cfg.about),
      // Standing instructions outrank the assistant's defaults — say so, or the
      // model treats them as background colour.
      block(
        "### Standing instructions for this account (follow these)",
        cfg.personal_instructions,
      ),
      block("### How the user writes from this account", cfg.writing_style),
      // Advisory: derived from edits, not stated by the user, so an explicit
      // writing_style must win where the two disagree.
      block(
        "### Observed writing style (auto-derived, advisory)",
        cfg.learned_writing_style,
        600,
      ),
    ].filter(Boolean);
    if (cfgParts.length) {
      parts.push(
        "## This account's assistant configuration\n" +
          "These are configured for the ACTIVE account only — if the user " +
          "switches accounts, they no longer apply.\n\n" +
          cfgParts.join("\n\n"),
      );
    }
  }

  const email = opts.openEmail;
  if (email) {
    const from = email.from?.name
      ? `${email.from.name} <${email.from.email}>`
      : email.from?.email || "";
    // The mailbox of the open mail, in both scopes (EM-T8e-3). An act on the
    // mail runs in that mailbox, never in the scope (§11.3 rule 1).
    const box = email.accountId
      ? accounts.find((a) => a.id === email.accountId)
      : undefined;
    const mailbox = email.accountId
      ? `  • mailbox: ${box ? `${chatMailboxName(box)} ` : ""}` +
        `(account_id ${email.accountId}). An act on this email runs in this ` +
        "mailbox.\n"
      : "";
    parts.push(
      'The user currently has this email open. When they say "this email", ' +
        '"this thread", "reply", or "summarize this", they mean it:\n' +
        `  • email_id: ${email.id}\n` +
        mailbox +
        `  • subject: ${email.subject || "(no subject)"}\n` +
        `  • from: ${from}\n` +
        `Call read_email(email_id="${email.id}") to read its full body before ` +
        "acting.",
    );
  }

  return parts.join("\n\n");
}
