// ── The cutover preview and the switch (WS-53 CRM-Z10a and CRM-Z10b) ────────
//
// Spec crm_platform.md §3.4, D133. The preview is the read that comes first:
// what moves to Metorite, what does not, and what needs an answer before the
// switch. It writes nothing, and the gateway calls Zoho for none of it. The
// counts in Zoho are those of the last good sync.
//
// CRM-Z10b adds the three calls of the switch (start, status, switch). The
// steps of the dialog live in `cutoverSwitch.ts`.
//
// Pure: the words and the sums are here and have tests. The card draws them.

import { call } from "./api";

export type Split = { from_zoho: number; native: number };

export type ModuleState =
  | "complete"
  | "held"
  | "unavailable"
  | "importing"
  | "failed"
  | "short"
  | "refused"
  /** Phase 2 only: the final sync did not read the module (review P1). */
  | "not_read"
  /** Phase 2 only: deals still wait for their contacts from Zoho (round 4). */
  | "roles_waiting";

/** One module that is not complete (CRM-Z10b). The dialog offers "Leave out". */
export type IncompleteRow = {
  key: string;
  label: string;
  reason: Exclude<ModuleState, "complete">;
  /** `roles_waiting` only: how many deals wait for their contacts. */
  count?: number;
};

export type RecordRow = {
  key: string;
  label: string;
  zoho: number | null;
  /** The age of the run that gave `zoho`. Null with `zoho`. */
  zoho_age_seconds: number | null;
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
  /** CRM-Z10b. An older gateway sends none of the three. */
  incomplete?: IncompleteRow[];
  /** The org is on the cutover list. Only then does the card offer the switch. */
  can_switch?: boolean;
  /** Pipelines that list stages in another order. Metorite uses one order. */
  pipelines_in_another_order?: number;
};

/** Admin only, Mirror mode only. A 409 `not_mirror` means the card has nothing to say. */
export function getCutoverPreview(): Promise<CutoverPreview> {
  return call<CutoverPreview>("/cutover/preview");
}

/** The last line of the card when the switch is not on for the org. */
export const SWITCH_OFF =
  "The switch is not on for this organization yet. Nothing here changes your CRM.";

/** The line over the two switches of the card, when the switch is on (D133, amended 2026-10-29). */
export const SWITCH_READY =
  "When the counts look right, choose how to switch. Both make Metorite your CRM, and Zoho stops syncing.";

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
  importing: "Still importing from Zoho",
  failed: "Some records did not save",
  short: "Metorite could not read every record",
  refused: "Zoho refused some records",
  not_read:
    "The final sync did not read it, often because Zoho's daily API limit is almost used. Try again tomorrow, or leave it out",
  roles_waiting:
    "Some deals still wait for their contacts from Zoho. Wait for the next syncs to read them, or leave them out. A deal you leave out keeps no linked contacts from Zoho",
};

/**
 * The words of one row of the Leave out step. A `roles_waiting` row with a
 * count says how many deals wait (round 4, P2-3). Each other row says its
 * reason.
 */
export function reasonWords(row: IncompleteRow): string {
  if (row.reason !== "roles_waiting" || !row.count) return STATE_WORDS[row.reason];
  const deals = row.count === 1 ? "1 deal still waits for its" : `${formatNumber(row.count)} deals still wait for their`;
  return (
    `${deals} contacts from Zoho. Wait for the next syncs to read them, or leave them out. ` +
    "A deal you leave out keeps no linked contacts from Zoho"
  );
}

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

/** The records in Metorite that came from Zoho. Records that members made here are not in it. */
export function movingTotal(preview: CutoverPreview): number {
  return preview.records.reduce((sum, row) => sum + row.metorite.from_zoho, 0);
}

/** The most names of Zoho-only modules that the lead sentence shows. */
export const STAYS_SHOWN = 3;

/** "Attachments, Quotes, Leads and 4 more". The full list sits behind the fold. */
export function staysShort(names: string[]): string {
  if (names.length <= STAYS_SHOWN) return names.join(", ");
  return `${names.slice(0, STAYS_SHOWN).join(", ")} and ${names.length - STAYS_SHOWN} more`;
}

/**
 * The one sentence that opens the card: what is in Metorite from Zoho, what is
 * not complete, and what stays in Zoho.
 */
export function leadSentence(preview: CutoverPreview): string {
  if (preview.sync.last_good_at === null) {
    return "Zoho has not finished a sync yet, so there is nothing to compare. Finish a sync first.";
  }
  const moving = `${formatNumber(movingTotal(preview))} records from Zoho are in Metorite.`;
  const incomplete = preview.not_complete.length > 0
    ? ` Not complete: ${preview.not_complete.join(", ")}.`
    : "";
  const stays = preview.stays_in_zoho.length > 0
    ? ` ${staysShort(preview.stays_in_zoho)} stay in Zoho.`
    : "";
  return moving + incomplete + stays;
}

/**
 * True when the cutover card shows both switches (D133 as amended
 * 2026-10-29): a preview that the card draws, for an org on the cutover list.
 * The hub then leaves its own start-empty switch out, so no action shows
 * twice. `SetupHub` and the card read the same cached preview.
 */
export function switchesInCard(x: unknown): boolean {
  return isPreview(x) && x.can_switch === true;
}

/** True when a body has the shape of a preview. Any other 200 body draws nothing. */
export function isPreview(x: unknown): x is CutoverPreview {
  const p = x as Partial<CutoverPreview> | null | undefined;
  if (!p || typeof p !== "object") return false;
  if (!p.sync || typeof p.sync !== "object") return false;
  return (
    Array.isArray(p.records) &&
    Array.isArray(p.other) &&
    Array.isArray(p.unmatched_owners) &&
    Array.isArray(p.labels_kept) &&
    Array.isArray(p.stays_in_zoho) &&
    Array.isArray(p.not_complete)
  );
}

/** A line for each thing that needs the admin's eye. Empty when all is clear. */
export function attention(preview: CutoverPreview): string[] {
  const lines: string[] = [];
  if (preview.sync.running) lines.push("A sync is running now. Wait for it to finish.");
  for (const row of incompleteOf(preview)) {
    lines.push(`${row.label}: ${STATE_WORDS[row.reason]}. Fix this, or leave it out when you switch.`);
  }
  const reordered = preview.pipelines_in_another_order ?? 0;
  if (reordered > 0) {
    lines.push(
      `${formatNumber(reordered)} pipeline${reordered === 1 ? " lists" : "s list"} stages in another order. ` +
        "Metorite uses one order.",
    );
  }
  if (preview.unmatched_owners.length > 0) {
    lines.push(
      `${formatNumber(preview.unmatched_owner_names)} Zoho owner${preview.unmatched_owner_names === 1 ? "" : "s"} ` +
        "with no Metorite member, on your companies, contacts, leads and deals. " +
        "Their records keep the name and stay without an owner.",
    );
  }
  return lines;
}

/** The count of a row in Zoho, or a dash when no good sync gave one. */
export function zohoCell(row: { zoho: number | null }): string {
  return row.zoho === null ? "—" : formatNumber(row.zoho);
}

/**
 * Each module that is not complete. The gateway's list when it sends one
 * (CRM-Z10b, Tasks too), else the rows of the table.
 */
export function incompleteOf(preview: CutoverPreview): IncompleteRow[] {
  if (preview.incomplete) return preview.incomplete;
  return preview.records
    .filter((row) => row.state !== "complete")
    .map((row) => ({
      key: row.key,
      label: row.label,
      reason: row.state as Exclude<ModuleState, "complete">,
    }));
}

// ── The switch (CRM-Z10b) ───────────────────────────────────────────────────

/**
 * The body of both POST routes: the admin's word and the modules left out.
 * The switch also sends `run_id`, the final run that the start began. A
 * second call after the switch needs none.
 */
export type SwitchBody = { confirm: string; leave_out: string[]; run_id?: string | null };

/** Phase 1 always starts its own final run (review P0). */
export type StartResult = { run_id: string };

export type CutoverStatus = {
  state: "idle" | "running" | "ready" | "failed" | "done";
  run_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** The safe code of a run that failed or stopped. Never free text. */
  error: string | null;
  age_seconds: number | null;
  /** The Zoho connection, or "none". */
  zoho_connection: string;
};

export type RevokeOutcome = "confirmed" | "not_confirmed" | "not_configured";

export type SwitchResult = {
  status: "switched" | "already_done";
  /** Null when this call closed nothing, or when the close failed. */
  revoked: RevokeOutcome | null;
  zoho_connection: string;
};

/** Phase 1: the checks, then the final sync. */
export function startCutover(body: SwitchBody): Promise<StartResult> {
  return call<StartResult>("/cutover/start", { method: "POST", body: JSON.stringify(body) });
}

/** The final sync, and whether the switch is done. The dialog reads it every 3 seconds. */
export function getCutoverStatus(): Promise<CutoverStatus> {
  return call<CutoverStatus>("/cutover/status");
}

/** Phases 2 and 3: the switch, then close Zoho. A second call closes Zoho only. */
export function switchToMetorite(body: SwitchBody): Promise<SwitchResult> {
  return call<SwitchResult>("/cutover", { method: "POST", body: JSON.stringify(body) });
}

/** True when the switch is done and Zoho still holds a live connection. */
export function closingPending(status: CutoverStatus | null | undefined): boolean {
  return (
    !!status &&
    status.state === "done" &&
    status.zoho_connection !== "disconnected" &&
    status.zoho_connection !== "none"
  );
}
