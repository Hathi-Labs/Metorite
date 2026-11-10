/**
 * What My Email tells the one assistant about its page (D99,
 * `navigation_shell.md` §7.1). Facts only: the mailbox in view and the open
 * message, each with its id, so the general agent can pass them to the email
 * specialist. A subject and a sender are quoted as data, because anybody can
 * send mail.
 *
 * Fence (R7): `assistantFacts.test.ts`.
 */

import { dataText, type AssistantFacts } from "@/lib/shell/assistant";
import type { Email, EmailAccount } from "./types";

/** The longest subject the chip shows. */
const CHIP_MAX = 40;

export function emailAssistantFacts(input: {
  viewAll: boolean;
  account: Pick<EmailAccount, "id" | "emailAddress"> | null;
  email: Pick<Email, "id" | "subject" | "accountId" | "from"> | null;
}): AssistantFacts {
  const facts: string[] = [];
  if (input.viewAll) facts.push("They are looking at all their inboxes together.");
  else if (input.account) {
    facts.push(`They are looking at the mailbox ${dataText(input.account.emailAddress)} (account_id: ${input.account.id}).`);
  }
  if (input.email) {
    const from = input.email.from?.email ? ` from ${dataText(input.email.from.email)}` : "";
    facts.push(
      `They have the email ${dataText(input.email.subject || "(no subject)")}${from} open ` +
        `(email_id: ${input.email.id}, account_id: ${input.email.accountId}). "This email" means it.`,
    );
  }
  const subject = input.email?.subject?.replace(/\s+/g, " ").trim();
  return {
    label: subject ? (subject.length > CHIP_MAX ? `${subject.slice(0, CHIP_MAX - 1)}…` : subject) : undefined,
    facts,
  };
}
