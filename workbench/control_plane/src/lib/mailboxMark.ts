/**
 * The mailbox mark of the one assistant (`navigation_shell.md` §7.1.1 rule 6).
 *
 * When the email work of a chat moves to another mailbox, the executor sends
 * a CUSTOM event `email_mailbox_mark` with the new mailbox's label. The chat
 * translator saves every custom event on the turn, so a reload draws the
 * same mark. The member sets nothing: there is no picker (D99).
 *
 * The server twin is `MAILBOX_MARK_EVENT` in
 * `packages/acb_skills/acb_skills/email_chat_context.py`.
 *
 * Pure. Fence (R7): `mailboxMark.test.ts`.
 */

export const MAILBOX_MARK_EVENT = "email_mailbox_mark";

/** The longest label a mark shows. */
const LABEL_MAX = 80;

/** The labels of the mailbox marks of one turn, in order. */
export function mailboxMarksOf(
  customEvents: ReadonlyArray<{ name: string; value: unknown }> | undefined,
): string[] {
  const out: string[] = [];
  for (const e of customEvents ?? []) {
    if (e.name !== MAILBOX_MARK_EVENT) continue;
    const value = (e.value ?? {}) as { label?: unknown };
    const label = String(value.label ?? "").replace(/\s+/g, " ").trim().slice(0, LABEL_MAX);
    if (label) out.push(label);
  }
  return out;
}
