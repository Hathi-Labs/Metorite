/**
 * The in-app link to one email: `/email?email=<id>&account=<account id>`.
 *
 * Owner report, 2026-10-09: the email assistant said there is "no shareable
 * link to the original email, only an internal id". The app has had the link
 * all along (`app/email/page.tsx` opens `?email=`), and nothing used it.
 *
 * Three callers build a link here, and none builds one by hand:
 *
 * - the "Open in inbox" act of a chat card (`EmailToolCards.tsx`), so the
 *   open survives a refresh;
 * - the page's deep-link reader (`components/EmailDeepLink.tsx`), which
 *   reads the same two names back;
 * - the email agent prints the same shape in its tool output as `link=`
 *   (`_email_link` in `apps/agents/agent-email-assistant/agents.py`), and
 *   `tests/unit/test_email_forward_tool.py` holds the two to one shape.
 *
 * The id must be a UUID. A link with anything else in it could name another
 * page, so the helper answers `null` for it. `account` is optional, because
 * the open moves to the mailbox that holds the mail by itself
 * (`openEmailById`, `mailboxToOpen`).
 *
 * Fence: `emailLink.test.ts`, which also checks that the chat renderer opens
 * the link in this tab (`isInAppPath`).
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The query names of the link. The page reads them back by these names. */
export const EMAIL_PARAM = "email";
export const ACCOUNT_PARAM = "account";

/** The in-app link to one email, or `null` when the id is not a UUID. */
export function emailLink(id: unknown, accountId?: unknown): string | null {
  const mail = typeof id === "string" ? id.trim() : "";
  if (!UUID.test(mail)) return null;
  const params = new URLSearchParams({ [EMAIL_PARAM]: mail.toLowerCase() });
  const account = typeof accountId === "string" ? accountId.trim() : "";
  if (UUID.test(account)) params.set(ACCOUNT_PARAM, account.toLowerCase());
  return `/email?${params.toString()}`;
}

/** The email id a query string names, or `null`. */
export function emailIdFromSearch(search: { get(name: string): string | null } | null): string | null {
  const raw = search?.get(EMAIL_PARAM)?.trim() ?? "";
  return UUID.test(raw) ? raw.toLowerCase() : null;
}
