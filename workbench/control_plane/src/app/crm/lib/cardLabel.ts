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
  /** How long the deal has sat in its stage, as the card shows it. */
  stageAge?: string | null;
  /** The rot level of that time (`lib/board.ts` rotLevel). The card shows it
   *  as a colour only, so the name says it in words. */
  rot?: "fresh" | "warn" | "over";
};

/** The card's short age ("12d", "3w", "2mo", "today", "—") in words. */
function spokenAge(age: string | null | undefined): string | null {
  if (!age || age === "—") return null;
  if (age === "today") return "Entered this stage today";
  const m = /^(\d+)(d|w|mo)$/.exec(age);
  if (!m) return `${age} in this stage`;
  const n = Number(m[1]);
  const unit = { d: "day", w: "week", mo: "month" }[m[2] as "d" | "w" | "mo"];
  return `${n} ${unit}${n === 1 ? "" : "s"} in this stage`;
}

export function cardLabel(input: CardLabelInput): string {
  const nextLabel = input.next?.label?.trim() || null;
  const parts = [
    input.title.trim() || "Untitled",
    input.amount,
    input.detail,
    nextLabel,
    // The chip of an overdue step already starts with "Overdue". Say it once.
    input.overdue && !/^overdue\b/i.test(nextLabel ?? "") ? "Overdue" : null,
    spokenAge(input.stageAge),
    input.rot === "over" ? "Stalled" : input.rot === "warn" ? "Stalling" : null,
    input.needsReason ? "Needs a lost reason" : null,
  ];
  return parts.filter((part): part is string => Boolean(part && part.trim())).join(", ");
}
