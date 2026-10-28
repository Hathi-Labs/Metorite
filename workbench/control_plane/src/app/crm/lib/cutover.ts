// ── The cutover preview (WS-53 CRM-Z10a, spec crm_platform.md §3.4, D133) ────
//
// "Make Metorite your CRM" comes in a later release. This file holds the read
// that comes first: what moves to Metorite, what does not, and what needs an
// answer before the switch. It writes nothing, and the gateway calls Zoho for
// none of it. The counts in Zoho are those of the last good sync.
//
// Pure: the words and the sums are here and have tests. The card draws them.

import { call } from "./api";

export type Split = { from_zoho: number; native: number };

export type ModuleState = "complete" | "held" | "unavailable" | "failed" | "refused";

export type RecordRow = {
  key: string;
  label: string;
  zoho: number | null;
  metorite: Split;
  parts: Array<{ label: string; metorite: Split }>;
  state: ModuleState;
  failed: number;
  refused: number;
};

export type CutoverPreview = {
  mode: "mirror";
  sync: { last_good_at: string | null; age_seconds: number | null; running: boolean };
  records: RecordRow[];
  other: Array<{ key: string; label: string; metorite: Split }>;
  unmatched_owners: Array<{ name: string; records: number }>;
  unmatched_owner_names: number;
  labels_kept: Array<{ key: string; label: string; singular: string | null }>;
  not_complete: string[];
  stays_in_zoho: string[];
};

/** Admin only, Mirror mode only. A 409 `not_mirror` means the card has nothing to say. */
export function getCutoverPreview(): Promise<CutoverPreview> {
  return call<CutoverPreview>("/cutover/preview");
}

/** The last line of the card. The switch is not in this release. */
export const SWITCH_LATER = "The switch itself comes in a later release. Nothing here changes your CRM.";

export const SHOW_COUNTS = "Show the counts";

export function total(split: Split): number {
  return split.from_zoho + split.native;
}

export function formatNumber(n: number): string {
  return n.toLocaleString("en-IN");
}

/** The reason a module is not complete, in a member's words. */
export const STATE_WORDS: Record<Exclude<ModuleState, "complete">, string> = {
  held: "Held back from the sync",
  unavailable: "Zoho does not let Metorite read it",
  failed: "Some records did not save",
  refused: "Zoho refused some records",
};

/** "3 hours ago", "2 days ago". Plain words for the age of the last good sync. */
export function ageWords(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} days ago`;
}

/** The records that move: every row of the core entities and the modules. */
export function movingTotal(preview: CutoverPreview): number {
  return preview.records.reduce((sum, row) => sum + total(row.metorite), 0);
}

/**
 * The one sentence that opens the card: what moves, what does not.
 * A module that is not complete is named after it, since the switch waits.
 */
export function leadSentence(preview: CutoverPreview): string {
  if (preview.sync.last_good_at === null) {
    return "Zoho has not finished a sync yet, so there is nothing to compare. Finish a sync first.";
  }
  const moving = `${formatNumber(movingTotal(preview))} records move to Metorite with their links and fields.`;
  const stays = preview.stays_in_zoho.length > 0
    ? ` ${preview.stays_in_zoho.join(", ")} stay in Zoho.`
    : "";
  return moving + stays;
}

/** A line for each thing that needs the admin's eye. Empty when all is clear. */
export function attention(preview: CutoverPreview): string[] {
  const lines: string[] = [];
  if (preview.sync.running) lines.push("A sync is running now. Wait for it to finish.");
  for (const row of preview.records) {
    if (row.state === "complete") continue;
    lines.push(`${row.label}: ${STATE_WORDS[row.state]}. The switch waits for it, unless you leave it out.`);
  }
  if (preview.unmatched_owners.length > 0) {
    lines.push(
      `${formatNumber(preview.unmatched_owner_names)} Zoho owner${preview.unmatched_owner_names === 1 ? "" : "s"} ` +
        "with no Metorite member. Their records keep the name and stay without an owner.",
    );
  }
  return lines;
}

/** The count of a row in Zoho, or a dash when the last sync did not give one. */
export function zohoCell(row: { zoho: number | null }): string {
  return row.zoho === null ? "—" : formatNumber(row.zoho);
}
