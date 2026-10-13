// ── Pipeline settings: reordering and the rules a row must satisfy ────────
//
// The grids in components/PipelineSettings.tsx are composition; everything
// server-shaped is here and unit-tested, the same split the rest of this app
// keeps (specs/crm_app.md §5, WS-26f f2).
//
// Two things live here and nowhere else:
//
//   1. **What a drag-reorder actually sends.** The admin API has no bulk
//      reorder — it has a per-row PATCH — so a drop is renumbered here and
//      issued as one PATCH per row that MOVED. Sending every row would write
//      six statements to move one lane, and sending none would leave the grid
//      showing an order the server never heard about.
//   2. **The client half of D-CRM-10.** The gateway is the boundary and 422s a
//      contradiction (`admin.py::_validate_status`); this is the courtesy that
//      says so before the request, because "422" in a toast is not an answer
//      to "why can I not save this row".

import { REQUIREABLE_FIELDS } from "./board";
import type { StageMetadataReport, StatusKind, StatusType } from "./types";

/** Lanes are numbered in tens so a lane can be inserted between two by hand. */
export const POSITION_STEP = 10;

export type Reorderable = { id: string; position: number };

/** The PATCH a reordered row needs: `{position}` on `/statuses/{kind}/{id}`. */
export type PositionPatch = { id: string; position: number };

/**
 * Move one item, returning a new array. Out-of-range indices are no-ops rather
 * than errors: an HTML5 drop can report an index for a row that has since been
 * re-rendered, and dropping a card should never blank a grid.
 */
export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (
    from === to ||
    from < 0 ||
    to < 0 ||
    from >= items.length ||
    to >= items.length
  ) {
    return items;
  }
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** Positions for a whole list, in tens, from its current order. */
export function renumber<T extends Reorderable>(items: T[]): T[] {
  return items.map((item, index) => ({
    ...item,
    position: (index + 1) * POSITION_STEP,
  }));
}

/**
 * A drop: the list as it should look, and the PATCHes that make it true.
 *
 * Only rows whose position actually changed are patched. Moving the last lane
 * up by one renumbers everything below it — that is the point of renumbering —
 * but the two lanes above it are untouched and must not be written.
 */
export function reorder<T extends Reorderable>(
  items: T[],
  from: number,
  to: number
): { items: T[]; patches: PositionPatch[] } {
  const moved = moveItem(items, from, to);
  const renumbered = renumber(moved);
  const before = new Map(items.map((item) => [item.id, item.position]));
  return {
    items: renumbered,
    patches: renumbered
      .filter((item) => before.get(item.id) !== item.position)
      .map((item) => ({ id: item.id, position: item.position })),
  };
}

/**
 * Did the close-date backfill actually run for this report?
 *
 * Two independent conditions, deliberately ANDed. The gateway leaves
 * `closed_at` **absent** unless it opened the database (the D-CRM-11 stop and
 * every unavailable outcome return before it does), and the outcome says the
 * same thing a second way. Either alone would be enough today; together they
 * mean a future server that starts sending a zeroed section on a stop still
 * cannot make this UI print "0 close dates missing" about data nobody read.
 */
export function backfillRan(
  report: Pick<StageMetadataReport, "outcome" | "closed_at">
): boolean {
  if (report.closed_at === null || report.closed_at === undefined) return false;
  return report.outcome === "dry_run" || report.outcome === "applied";
}

/**
 * Issue every patch. Collect the failures. Never stop at the first one.
 *
 * A reorder is N writes to ONE column, and abandoning it partway is worse than
 * either the old order or the new one: the rows already written hold their new
 * numbers and the rest hold their old ones, so the grid ends up with duplicate
 * positions — a state the lanes on screen cannot show and the next drag
 * compounds. Every patch is therefore attempted and the failures are reported
 * together, with the re-read that follows showing whatever the server actually
 * holds.
 *
 * Serial rather than `Promise.all` for the same reason it always was: these are
 * N writes to the same table and a race is not a better failure mode.
 */
export async function applyPatches(
  patches: PositionPatch[],
  send: (patch: PositionPatch) => Promise<unknown>
): Promise<string[]> {
  const failures: string[] = [];
  for (const patch of patches) {
    try {
      await send(patch);
    } catch (err) {
      failures.push(err instanceof Error ? err.message : String(err));
    }
  }
  return failures;
}

/**
 * What a partly-applied reorder says.
 *
 * It names the arithmetic (how many of how many) and then the one thing the
 * reader needs to act on: the order below is the server's, not theirs. A bare
 * "request failed" next to a grid that re-read into a third order is how
 * somebody drags again and makes it worse.
 */
export function reorderFailureMessage(
  failures: string[],
  total: number
): string {
  return (
    `${failures.length} of ${total} lanes could not be reordered — the order ` +
    `shown is the server's, not the one you dropped. ${failures[0]}`
  );
}

// ── D-CRM-10, client side ─────────────────────────────────────────────────

/** Mirrors `routes/crm/admin.py::CLAMPED_PROBABILITY`. */
export const CLAMPED_PROBABILITY: Partial<Record<StatusType, number>> = {
  won: 100,
  lost: 0,
};

export type StatusDraft = {
  name: string;
  type: StatusType;
  color?: string;
  /** Deal statuses only — a lead status has no win probability. */
  probability?: number | null;
  /** Deal statuses only (WS-26h) — entry requirements. */
  required_fields?: string[];
  /** Deal statuses only (WS-26h) — rot threshold; null/undefined = never. */
  max_dwell_days?: number | null;
};

/**
 * Why this row cannot be saved, or null.
 *
 * Deliberately returns the SAME rule the gateway enforces rather than a
 * looser one: a client check that permitted something the server refuses
 * turns a rule into an intermittent bug, and one that refused something the
 * server allows is a feature nobody can reach. It is not a boundary — the
 * gateway re-checks every PATCH — it is the sentence that explains the 422
 * before it happens.
 */
export function validateStatusDraft(
  draft: StatusDraft,
  kind: "deal" | "lead"
): string | null {
  if (!draft.name.trim()) return "A status needs a name.";
  if (kind !== "deal") return null;

  // An emptied probability box means "take the column default", which is 0 —
  // the same value the server would land on, so it is judged as 0 here too.
  const probability = draft.probability ?? 0;
  if (!Number.isFinite(probability) || probability < 0 || probability > 100) {
    // Word-for-word the gateway's own refusal (admin.py::_validate_status), so
    // hitting the server anyway never produces a second, differently-worded
    // version of the same rule.
    return `A stage probability is a percentage: ${probability} is outside 0-100.`;
  }
  const pinned = CLAMPED_PROBABILITY[draft.type];
  if (pinned !== undefined && probability !== pinned) {
    return (
      `A '${draft.type}' stage always forecasts ${pinned}% — this one would be ` +
      `left at ${probability}%. Set probability to ${pinned}, or give the ` +
      `stage a different type.`
    );
  }

  // WS-26h. Same division of labour as the clamp above: the gateway is the
  // boundary and re-checks every PATCH, and this is the sentence that explains
  // the 422 before it happens.
  const unknown = (draft.required_fields ?? []).filter(
    (field) => !(REQUIREABLE_FIELDS as readonly string[]).includes(field)
  );
  if (unknown.length > 0) {
    return `${JSON.stringify(unknown)} cannot be required on a stage.`;
  }

  const dwell = draft.max_dwell_days;
  if (dwell !== null && dwell !== undefined) {
    if (!Number.isInteger(dwell) || dwell < 1 || dwell > 32767) {
      // Word-for-word the gateway's refusal (admin.py::_validate_max_dwell_days),
      // so hitting the server anyway never produces a second, differently
      // worded version of the same rule.
      return (
        "'max_dwell_days' is a whole number of days from 1 to 32767, or null " +
        `for a stage that never rots: ${dwell} is neither.`
      );
    }
  }
  return null;
}

/** The lost-reason grid's one rule. */
export function validateLostReasonDraft(label: string): string | null {
  return label.trim() ? null : "A lost reason needs a label.";
}

/**
 * The fields a PATCH should carry — only what actually changed.
 *
 * A PATCH that resends every field is not merely wasteful here: `type` and
 * `probability` are judged TOGETHER by the clamp, so resending an unchanged
 * `probability` alongside a changed `type` is the difference between a 422
 * the editor caused and one the data did.
 *
 * ⚠️ Arrays are compared by CONTENT (WS-26h's `required_fields`). Identity
 * would report every re-render of a row as a change, because a checkbox that
 * toggles a field on and back off leaves a new array holding the old contents
 * — and a grid whose Save button never goes quiet is a grid nobody trusts.
 */
export function changedFields<T extends Record<string, unknown>>(
  before: T,
  after: T
): Partial<T> {
  const out: Partial<T> = {};
  for (const key of Object.keys(after) as (keyof T)[]) {
    if (!sameValue(before[key], after[key])) out[key] = after[key];
  }
  return out;
}

function sameValue(before: unknown, after: unknown): boolean {
  if (Array.isArray(before) && Array.isArray(after)) {
    // Order-sensitive on purpose: `required_fields` is stored as written and
    // the blocked-move 422 lists it in that order, so a reorder IS a change.
    return (
      before.length === after.length &&
      before.every((item, index) => item === after[index])
    );
  }
  return before === after;
}

// ── The Mirror editor (WS-53 CRM-C3, crm_platform.md §20.6 item 14) ────────
//
// In Mirror mode Zoho owns the source values of a stage, and Metorite keeps an
// overlay beside them that the sync never writes (D95). The gateway takes only
// the overlay fields there and answers 409 for any other field, so the editor
// sends only these.

/** The overlay fields a Mirror admin edits on one row, by kind. */
export const OVERLAY_FIELDS: Record<StatusKind, readonly string[]> = {
  deal: ["hidden", "ui_probability", "ui_color", "max_dwell_days"],
  lead: ["hidden", "ui_color"],
};

/** What changed in the overlay of one row. Only these fields. */
export function overlayPatch(
  kind: StatusKind,
  before: Record<string, unknown>,
  after: Record<string, unknown>
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of OVERLAY_FIELDS[kind]) {
    const was = before[key] ?? null;
    const now = after[key] ?? null;
    if (!sameValue(was, now)) out[key] = now;
  }
  return out;
}

/**
 * "Reset to Zoho" on a row: the Metorite probability and colour go, so the
 * Zoho values apply again. `hidden` and `max_dwell_days` stay. A lead status
 * has no probability, and the gateway refuses a field it has not got.
 */
export function resetToSource(kind: StatusKind): Record<string, null> {
  return kind === "deal" ? { ui_probability: null, ui_color: null } : { ui_color: null };
}

/** True when the row carries a Metorite value that "Reset to Zoho" clears. */
export function hasOverlay(kind: StatusKind, status: Record<string, unknown>): boolean {
  return Object.keys(resetToSource(kind)).some((key) => status[key] != null);
}

/** A Metorite probability: empty (the Zoho value applies) or a whole 0 to 100. */
export function validateOverlayProbability(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  if (!Number.isInteger(value) || value < 0 || value > 100) {
    return "A probability is a whole number from 0 to 100, or empty for the Zoho value.";
  }
  return null;
}

/** The ids the order route takes after one drag: every row, in the new order. */
export function orderAfterDrop<T extends { id: string }>(
  rows: T[],
  from: number,
  to: number
): string[] | null {
  const moved = moveItem(rows, from, to);
  return moved === rows ? null : moved.map((row) => row.id);
}
