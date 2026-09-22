/**
 * Projects · what the move card READS off a preview, in one pure place.
 *
 * `MoveTasksDialog` used to derive its rows inline, and every optional
 * array carried its own `?? []` — except the one S6c added. The bulk path's
 * e2e stub omits `required_missing` (a promote-only concern), so
 * `plan.required_missing.length` threw, the page went white, and the
 * confirm button never enabled. PR #395's browser suite caught it.
 *
 * So the derivations live here, every array is guarded once, and
 * `movePlan.test.ts` feeds this the exact shapes the two callers send: the
 * bulk stub with no promote fields, and the promote preview with them. A
 * preview key the card reads and this file does not guard cannot be added
 * without a test naming it.
 */

import type { MovePlan } from "./api";
import type { FieldDef } from "./customFields";

/** A preview as the WIRE may deliver it: any array may be absent. */
export type LoosePlan = Partial<MovePlan> &
  Pick<MovePlan, "source_project_id" | "destination_project_id">;

export interface PlanReading {
  statuses: NonNullable<MovePlan["statuses"]>;
  destStatuses: NonNullable<MovePlan["destination_statuses"]>;
  /** `[field_key, rows]` pairs — what the selection would actually lose. */
  drops: [string, NonNullable<MovePlan["drops"]>[string]][];
  lostTypes: NonNullable<MovePlan["types"]>;
  unregistered: string[];
  /** Every source field and where it lands, mapped ones first. */
  fieldRows: { from: string; to: string | null }[];
  /** Destination fields the selection does not satisfy (migration 192). */
  requiredMissing: string[];
  crossesStatusSet: boolean;
  crossesRoot: boolean;
  /** Nothing is remapped and nothing is lost. */
  clean: boolean;
}

export function readPlan(plan: LoosePlan | null): PlanReading | null {
  if (!plan) return null;
  const drops = Object.entries(plan.drops ?? {});
  // `plan.tags?.` — the guard has to be at BOTH levels. A preview that omits
  // `tags` must not throw one frame later.
  const unregistered = plan.tags?.unregistered ?? [];
  const fieldRows = [
    ...Object.entries(plan.field_map ?? {}).map(([from, to]) => ({ from, to })),
    ...(plan.orphan_fields ?? []).map((field) => ({
      from: field.name || field.field_key,
      to: null,
    })),
  ];
  const crossesStatusSet = Boolean(plan.crosses_status_set);
  const crossesRoot = Boolean(plan.crosses_root);
  return {
    statuses: plan.statuses ?? [],
    destStatuses: plan.destination_statuses ?? [],
    drops,
    lostTypes: (plan.types ?? []).filter((row) => !row.to),
    unregistered,
    fieldRows,
    requiredMissing: plan.required_missing ?? [],
    crossesStatusSet,
    crossesRoot,
    clean: !crossesStatusSet && !crossesRoot && drops.length === 0,
  };
}

/**
 * The promote door's required fields: the preview's, plus any a refusal
 * named. `null` while the preview's definitions are still on their way, so
 * the button cannot offer a move the server would refuse for a field it has
 * not been asked about yet. A refused field the preview did not list (a
 * definition added between the preview and the move) is drawn with an
 * empty control, not merely named.
 */
export function askedFields(
  fromPreview: FieldDef[] | null,
  fromRefusal: readonly FieldDef[] | undefined
): FieldDef[] | null {
  if (fromPreview === null) return null;
  const seen = new Set(fromPreview.map((def) => def.field_key));
  const extra = (fromRefusal ?? []).filter((def) => !seen.has(def.field_key));
  return extra.length ? [...fromPreview, ...extra] : fromPreview;
}
