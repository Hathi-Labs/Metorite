/**
 * The recipients of a mail, rolled up to one line (owner, 2026-11-01: "the
 * email addresses are filling up the vertical space of the email too much,
 * it would be good to roll it up and only see it when needed").
 *
 * - A mail with more than `ROLLUP_AFTER` recipients shows one line of names:
 *   "to Fracktal Works, Bharat Mishra, you · cc Girish R, Ramesh +2". The
 *   member opens the full list, name and address, with "Details".
 * - The Cc stays in the line, by name. One cut line once hid the Cc (owner
 *   report, 2026-10-24), and the member must see who else got the mail.
 * - The member's own address reads "you", and leads its group.
 * - A mail with few recipients shows the full list, as before: there is
 *   nothing to roll up.
 *
 * Pure, so the node-env vitest holds it. `ContactCard.tsx` draws it.
 */

import type { EmailAddress } from "./types";

/** More recipients than this, across To, Cc and Bcc, roll up. */
export const ROLLUP_AFTER = 2;
/** The names one group shows in the line before "+N". */
export const NAMES_PER_GROUP = 2;

export type RecipientLabel = "To" | "Cc" | "Bcc";

export interface RecipientGroup {
  label: RecipientLabel;
  people: readonly EmailAddress[];
}

/** One name in the rolled-up line. */
export interface SummaryName {
  person: EmailAddress;
  /** "you", the display name, or the address when there is no name. */
  text: string;
}

export interface SummaryGroup {
  label: RecipientLabel;
  shown: SummaryName[];
  /** The people of this group the line leaves out. */
  more: number;
}

const key = (email: string) => email.trim().toLowerCase();

/** The member's own addresses, compared without case. */
export function addressSet(addresses: readonly string[]): ReadonlySet<string> {
  return new Set(addresses.filter(Boolean).map(key));
}

/** The groups that hold someone, in the order To, Cc, Bcc. */
export function recipientGroups(to?: readonly EmailAddress[], cc?: readonly EmailAddress[], bcc?: readonly EmailAddress[]): RecipientGroup[] {
  const all: RecipientGroup[] = [
    { label: "To", people: to ?? [] },
    { label: "Cc", people: cc ?? [] },
    { label: "Bcc", people: bcc ?? [] },
  ];
  return all.filter((g) => g.people.length > 0);
}

/** Whether the list rolls up to one line. */
export function rollsUp(groups: readonly RecipientGroup[]): boolean {
  return groups.reduce((n, g) => n + g.people.length, 0) > ROLLUP_AFTER;
}

/** A person's name in the line: "you", the name, or the address. */
export function shortName(p: EmailAddress, mine: ReadonlySet<string>): string {
  if (mine.has(key(p.email))) return "you";
  const name = p.name?.trim();
  if (!name || key(name) === key(p.email)) return p.email.trim();
  // A team suffix in brackets goes: "Girish R (TGSIN - Purchase)" reads
  // "Girish R". The full name shows on hover and in the details.
  return name.replace(/\s*[([][^)\]]*[)\]]\s*$/, "").trim() || name;
}

/** The rolled-up line: each group's first names, the member first, and the rest as a count. */
export function summarize(groups: readonly RecipientGroup[], mine: ReadonlySet<string>): SummaryGroup[] {
  return groups.map((g) => {
    const names = g.people.map((person) => ({ person, text: shortName(person, mine) }));
    // The member leads the group: "you" is what they look for first.
    const ordered = [...names.filter((n) => n.text === "you"), ...names.filter((n) => n.text !== "you")];
    const shown = ordered.slice(0, NAMES_PER_GROUP);
    return { label: g.label, shown, more: ordered.length - shown.length };
  });
}

/** The line in words, for its accessible name. */
export function summaryText(summary: readonly SummaryGroup[]): string {
  return summary
    .map((g) => `${g.label.toLowerCase()} ${g.shown.map((n) => n.text).join(", ")}${g.more > 0 ? ` and ${g.more} more` : ""}`)
    .join("; ");
}
