// ── The spoken name of a CRM board card (WS-53 CRM-C2 review) ──────────────
//
// The card is `TaskCardShell`, a `role="button"`. A screen reader reads a
// button by its name, and it does not read the text inside the button. So the
// name must carry what the card shows: the title, the money or the company,
// the next step, and the two warnings. The red left edge marks an overdue
// card, and colour must never be the only signal (`DESIGN_SYSTEM.md` §7), so
// the name says "Overdue" too.

import type { MetaChip } from "@/lib/taskCard";

export type CardLabelInput = {
  title: string;
  /** The formatted amount of a deal, or null for a lead. */
  amount?: string | null;
  /** The company, or a lead's email when it has no company. */
  detail?: string | null;
  /** The next-activity chip (`lib/nextActivity.ts`), or null. */
  next?: MetaChip | null;
  /** The next step is late. The card wears the red edge. */
  overdue?: boolean;
  /** A move to Lost will ask for a reason. */
  needsReason?: boolean;
};

export function cardLabel(input: CardLabelInput): string {
  const nextLabel = input.next?.label?.trim() || null;
  const parts = [
    input.title.trim() || "Untitled",
    input.amount,
    input.detail,
    nextLabel,
    // The chip of an overdue step already starts with "Overdue". Say it once.
    input.overdue && !/^overdue\b/i.test(nextLabel ?? "") ? "Overdue" : null,
    input.needsReason ? "Needs a lost reason" : null,
  ];
  return parts.filter((part): part is string => Boolean(part && part.trim())).join(", ");
}
