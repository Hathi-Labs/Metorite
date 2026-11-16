/**
 * The warning for a task that names a person who is not a member (task 3597).
 *
 * The server puts a `not in the directory` row on a card BEFORE Approve when
 * an assignee is an address that the `people` table does not know. The row drew
 * as one grey line among the others, and the line `gives access to` beside it
 * named the same address, which reads as the opposite. This file holds the words
 * of the warning, and the three pure rules that the card uses.
 *
 * 1. The warning draws first in the body, with the warning tone.
 * 2. `gives access to` does not name an address that the warning names.
 * 3. A tick row whose first address is a stranger carries a mark.
 *
 * Nothing here changes which rows are ticked, or what Approve sends.
 * Fence: `src/components/assigneeWarning.test.ts`.
 */

import { cardKey, parseFieldValue } from "@/lib/cardFields";

/** The key that the server writes for the addresses that `people` does not know. */
export const STRANGER_KEY = "not in the directory";
/** The row that says who gains access. It stays true for a member only. */
const ACCESS_KEY = "gives access to";

/** The words of the warning. The owner may change them here and nowhere else. */
export const ASSIGNEE_WARNING = {
  one: (address: string) =>
    `${address} is not a member of your organization. The task lists this address, but this person will not see the task or get a notice. Invite the person, or pick a member.`,
  many: (addresses: readonly string[]) =>
    `${addresses.join(", ")} are not members of your organization. The task lists these addresses, but these people will not see the task or get a notice. Invite them, or pick members.`,
} as const;

/** The mark on a tick row, and its accessible name (the text is the name). */
export const ASSIGNEE_ROW_MARK = "Assignee not a member";

/** The shape that this file needs from a card field. */
interface FieldLike {
  key: string;
  value: string;
  values?: string[];
}

const norm = (s: string) => s.trim().toLowerCase();

function itemsOf(value: string): string[] {
  return parseFieldValue(value, true).after.map((i) => i.text.trim()).filter(Boolean);
}

/** The addresses that the card says the directory does not know. */
export function strangerAddresses(fields: readonly FieldLike[]): string[] {
  const out: string[] = [];
  for (const f of fields) {
    if (cardKey(f.key) !== STRANGER_KEY) continue;
    for (const v of f.values ?? [f.value]) for (const a of itemsOf(v)) if (!out.includes(a)) out.push(a);
  }
  return out;
}

/** The sentence of the warning, or "" when no address is a stranger. */
export function assigneeWarningText(addresses: readonly string[]): string {
  if (addresses.length === 0) return "";
  return addresses.length === 1 ? ASSIGNEE_WARNING.one(addresses[0]) : ASSIGNEE_WARNING.many(addresses);
}

/**
 * The fields that draw as rows. The stranger row leaves them, because the
 * warning draws it. `gives access to` loses each stranger, and it leaves too
 * when no one else remains. With no stranger the fields come back as they are.
 */
export function fieldsWithoutStrangers<T extends FieldLike>(fields: readonly T[], strangers: readonly string[]): T[] {
  if (strangers.length === 0) return [...fields];
  const gone = new Set(strangers.map(norm));
  const out: T[] = [];
  for (const f of fields) {
    const key = cardKey(f.key);
    if (key === STRANGER_KEY) continue;
    if (key === ACCESS_KEY && f.values === undefined) {
      const items = itemsOf(f.value);
      const kept = items.filter((a) => !gone.has(norm(a)));
      if (kept.length < items.length) {
        if (kept.length === 0) continue;
        const fenced = f.value.includes("«");
        out.push({ ...f, value: kept.map((a) => (fenced ? `«${a}»` : a)).join(", ") });
        continue;
      }
    }
    out.push(f);
  }
  return out;
}

/** True when the first address of a row's hint is a stranger. */
export function rowIsStranger(hint: string | undefined, strangers: readonly string[]): boolean {
  if (!hint || strangers.length === 0) return false;
  const first = hint.split(" · ")[0].replace(/[«»]/g, "");
  const gone = new Set(strangers.map(norm));
  return first.split(/[,\s]+/).some((t) => t !== "" && gone.has(norm(t)));
}
