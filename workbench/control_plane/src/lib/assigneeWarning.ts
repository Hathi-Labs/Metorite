/**
 * The warning for a task that names a person who is not in the people list (task 3597).
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

import { cardKey } from "@/lib/cardFields";

/** The key that the server writes for the addresses that `people` does not know. */
export const STRANGER_KEY = "not in the directory";
/** The row that says who gains access. It stays true for a member only. */
const ACCESS_KEY = "gives access to";

/** The words of the warning. The owner may change them here and nowhere else. */
export const ASSIGNEE_WARNING = {
  one: (address: string) =>
    `${address} is not in your organization's people list. The task lists this address, but this person cannot see the task or get a notice until they are invited. Invite the person, or pick someone from the list.`,
  many: (addresses: readonly string[]) =>
    `${addresses.join(", ")} are not in your organization's people list. The task lists these addresses, but these people cannot see the task or get a notice until they are invited. Invite them, or pick people from the list.`,
} as const;

/** The warning names this many addresses. Each tick row keeps its own mark. */
export const MAX_SHOWN = 5;

/** The mark on a tick row, and its accessible name (the text is the name). */
export const ASSIGNEE_ROW_MARK = "Assignee not in the list";

/** The shape that this file needs from a card field. */
interface FieldLike {
  key: string;
  value: string;
  values?: string[];
}

const norm = (s: string) => s.trim().toLowerCase();
/** "and 4 more", which the server may add after the last address. */
const MORE = /\s+and\s+\d+\s+more\s*$/;

/**
 * The addresses of a value. The server writes ONE fence with a comma list
 * (`«a@x.com, b@x.com»`), and a list of fences (`«a», «b»`) gives the same
 * result. Each address comes once, in order, whatever its case.
 */
function addressesOf(value: string): string[] {
  const text = value.replace(/[«»]/g, "").replace(MORE, "");
  const out: string[] = [];
  for (const part of text.split(",")) {
    const a = part.trim();
    if (a && !out.some((o) => norm(o) === norm(a))) out.push(a);
  }
  return out;
}

/** The addresses that the card says the directory does not know. */
export function strangerAddresses(fields: readonly FieldLike[]): string[] {
  const out: string[] = [];
  for (const f of fields) {
    if (cardKey(f.key) !== STRANGER_KEY) continue;
    for (const v of f.values ?? [f.value]) for (const a of addressesOf(v)) if (!out.some((o) => norm(o) === norm(a))) out.push(a);
  }
  return out;
}

/** The sentence of the warning, or "" when no address is a stranger. */
export function assigneeWarningText(addresses: readonly string[]): string {
  if (addresses.length === 0) return "";
  if (addresses.length === 1) return ASSIGNEE_WARNING.one(addresses[0]);
  const more = addresses.length - MAX_SHOWN;
  const shown = addresses.slice(0, MAX_SHOWN).join(", ");
  const list = more > 0 ? `${shown} and ${more} more` : shown;
  return ASSIGNEE_WARNING.many([list]);
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
      const items = addressesOf(f.value);
      const kept = items.filter((a) => !gone.has(norm(a)));
      if (kept.length < items.length) {
        const tail = MORE.exec(f.value.replace(/[«»]/g, ""))?.[0] ?? "";
        // Nobody named is left. The people that the server only counted stay.
        if (kept.length === 0 && !tail) continue;
        const text = kept.length === 0 ? tail.trim().replace(/^and\s+/, "") : kept.join(", ") + tail;
        out.push({ ...f, value: f.value.includes("«") ? `«${text}»` : text });
        continue;
      }
    }
    out.push(f);
  }
  return out;
}

/** An address inside a hint segment: `Name (a@x.com)` or a bare `a@x.com`. */
const ADDRESS = /[^\s,()<>«»]+@[^\s,()<>«»]+/g;

/** True when ANY address in the first " · " segment of a hint is a stranger. */
export function rowIsStranger(hint: string | undefined, strangers: readonly string[]): boolean {
  if (!hint || strangers.length === 0) return false;
  const gone = new Set(strangers.map(norm));
  const first = hint.split(" · ")[0];
  return (first.match(ADDRESS) ?? []).some((a) => gone.has(norm(a.replace(/[.;:]+$/, ""))));
}
