// ── The kanban board's geometry and its one mutation ──────────────────────
//
// Everything the board does that is not painting pixels lives here, so it can
// be tested against fixtures without a DOM: lane assembly, lane headers, and
// the request a drag issues.
//
// Spec: project-docs/specs/crm_app.md §5 surface 1.

import { stageAgeDays } from "./format";
import type { Deal, Pipeline, PipelineLane, Status, StatusType } from "./types";

/** A lane as the board renders it — the wire lane plus its display totals. */
export type BoardLane = PipelineLane & {
  /** Lane colour token (see statusTone) resolved once, not per card. */
  tone: string;
};

/**
 * `crm_*_statuses.color` is free text managed by the owner (and by the Zoho
 * importer, which copies real stage names in). So it is mapped to a semantic
 * token here rather than interpolated into a class name — Tailwind v4 cannot
 * see a class it never saw in source, and `bg-${color}-500` would render as
 * nothing for every lane.
 */
const TONES: Record<string, string> = {
  gray: "bg-muted-foreground",
  slate: "bg-muted-foreground",
  blue: "bg-primary",
  sky: "bg-primary",
  cyan: "bg-primary",
  green: "bg-success",
  emerald: "bg-success",
  teal: "bg-success",
  amber: "bg-warning",
  yellow: "bg-warning",
  orange: "bg-accent",
  red: "bg-destructive",
  rose: "bg-destructive",
  purple: "bg-accent",
  violet: "bg-accent",
  pink: "bg-accent",
};

/**
 * Fallback by status TYPE, not to grey: an unrecognised colour on a won lane
 * should still read as won. The type is a closed CHECK vocabulary; the colour
 * is not.
 */
const TYPE_TONES: Record<string, string> = {
  open: "bg-muted-foreground",
  ongoing: "bg-primary",
  on_hold: "bg-warning",
  won: "bg-success",
  lost: "bg-destructive",
};

/**
 * The colour names a lane can be set to.
 *
 * Derived from TONES rather than typed out again: the settings grid must not
 * offer a colour the board cannot draw, and a second hand-kept list is how it
 * would end up doing exactly that.
 */
export const TONE_COLORS: readonly string[] = Object.keys(TONES);

export function statusTone(status: Pick<Status, "color" | "type">): string {
  return (
    TONES[(status.color || "").toLowerCase()] ??
    TYPE_TONES[status.type] ??
    "bg-muted-foreground"
  );
}

/**
 * Lanes in `position` order, empty ones included.
 *
 * The gateway already orders them; re-sorting here is not distrust, it is
 * that the board must not depend on a payload's ordering to be correct — and
 * an empty lane is kept because a kanban that hides its empty columns is a
 * list (the gateway's own words).
 */
export function toBoardLanes(pipeline: Pipeline | null): BoardLane[] {
  if (!pipeline) return [];
  return [...pipeline.lanes]
    .sort((a, b) => a.status.position - b.status.position)
    .map((lane) => ({ ...lane, tone: statusTone(lane.status) }));
}

// ── Weighted ₹ (WS-26f f3, spec §5.1) ─────────────────────────────────────
//
// Weighted pipeline = Σ(amount × probability/100) over deals in open/ongoing
// stages. It appears wherever money already appears: under each lane's ₹
// total, in the board header, and in WS-26g's reports.

/**
 * The stage types a deal forecasts from.
 *
 * `won` is CLOSED revenue and never pipeline; `lost` is nothing; and
 * `on_hold` — the fifth type the spec's prose never mentions — is excluded
 * too, because a deal nobody is working is not a forecast. Mirrors
 * `routes/crm/core.py::WEIGHTED_TYPES`; WS-26g's report block owns the
 * cross-language parity test that holds the two together.
 */
export const WEIGHTED_TYPES: readonly StatusType[] = ["open", "ongoing"];

/**
 * One deal's weighted ₹.
 *
 * A NULL probability inherits the stage's default (§5.1). The inheritance
 * materializes when a deal ENTERS a stage, so a NULL survives only on rows
 * that predate a move — which is every imported deal, i.e. most of the board
 * today. Reading them as 0% would zero the forecast on exactly those rows.
 */
export function weightedDeal(
  deal: Pick<Deal, "amount" | "probability">,
  stage: Pick<Status, "probability">
): number {
  const amount = deal.amount ?? 0;
  const probability = deal.probability ?? stage.probability ?? 0;
  return (amount * probability) / 100;
}

/**
 * Weighted ₹ over a set of deals in one stage — the record-sheet math, and the
 * formula the fixtures pin.
 *
 * ⚠️ NOT what a lane header renders: `lane.rows` is one page of the lane, so
 * this over rows would under-report the busy lane. The gateway sends
 * `lane.weighted` for the whole lane and that is what is displayed.
 */
export function weightedRows(
  rows: Pick<Deal, "amount" | "probability">[],
  stage: Pick<Status, "type" | "probability">
): number {
  if (!WEIGHTED_TYPES.includes(stage.type)) return 0;
  return rows.reduce((total, deal) => total + weightedDeal(deal, stage), 0);
}

/** The board's own totals, for the header. Sums the LANE figures, which cover
 *  the whole lane — not the rows returned, which are one page of it. */
export function boardTotals(lanes: BoardLane[]): {
  count: number;
  amount: number;
  weighted: number;
} {
  return lanes.reduce(
    (acc, lane) => ({
      count: acc.count + lane.count,
      amount: acc.amount + (lane.amount || 0),
      weighted: acc.weighted + (lane.weighted || 0),
    }),
    { count: 0, amount: 0, weighted: 0 }
  );
}

/** The move a drop asks for, or null when the drop changes nothing. */
export type DealMove = {
  dealId: string;
  fromStatusId: string | null;
  toStatusId: string;
};

export function planMove(
  deal: Pick<Deal, "id" | "status_id">,
  toStatusId: string
): DealMove | null {
  const from = deal.status_id ?? null;
  // Dropping a card back into the lane it came from is not a transition, and
  // issuing the PATCH anyway would write a crm_status_changes row with a
  // dwell of zero and a status_change activity saying "Proposal → Proposal".
  if (from === toStatusId) return null;
  return { dealId: deal.id, fromStatusId: from, toStatusId };
}

/** Fields that ride along with a move — today `lost_reason_id`/`lost_note`. */
export type MoveExtras = Record<string, unknown>;

export type MoveRequest = {
  path: string;
  method: "PATCH";
  body: { status_id: string } & MoveExtras;
};

/**
 * The request a move issues. `api.moveDeal` is its only caller, so the board
 * drag and the status pill in the record sheet cannot disagree about what
 * moving a deal means.
 *
 * `extras` is part of the shape rather than a second request: entering a
 * lost-type status without a reason is a 422 raised BEFORE any of the
 * transition's three effects, so the reason has to be in the same PATCH — two
 * requests could half-apply, and the half that lands is the one that moved
 * the deal.
 */
export function moveRequest(move: DealMove, extras: MoveExtras = {}): MoveRequest {
  return {
    path: `/deals/${move.dealId}`,
    method: "PATCH",
    body: { status_id: move.toStatusId, ...extras },
  };
}

/**
 * The board as it should look the instant a card is dropped, before the
 * server answers.
 *
 * Optimism is the point of a kanban — a card that snaps back for 300ms while
 * a request flies reads as a bug. The caller reverts to the previous lanes if
 * the PATCH fails, which is why this is pure and returns a new array.
 */
export function applyMove(lanes: BoardLane[], move: DealMove): BoardLane[] {
  const moving = lanes
    .flatMap((lane) => lane.rows)
    .find((deal) => deal.id === move.dealId);
  if (!moving) return lanes;
  return lanes.map((lane) => {
    // The weighted figure moves with the card too. Leaving it until the
    // re-read would show a lane whose ₹ total dropped and whose forecast did
    // not — two numbers side by side disagreeing about the same drag.
    const contribution = WEIGHTED_TYPES.includes(lane.status.type)
      ? weightedDeal(moving, lane.status)
      : 0;
    if (lane.status.id === move.fromStatusId) {
      const rows = lane.rows.filter((deal) => deal.id !== move.dealId);
      return {
        ...lane,
        rows,
        count: Math.max(0, lane.count - 1),
        amount: lane.amount - (moving.amount || 0),
        weighted: Math.max(0, lane.weighted - contribution),
      };
    }
    if (lane.status.id === move.toStatusId) {
      return {
        ...lane,
        rows: [{ ...moving, status_id: move.toStatusId }, ...lane.rows],
        count: lane.count + 1,
        amount: lane.amount + (moving.amount || 0),
        weighted: lane.weighted + contribution,
      };
    }
    return lane;
  });
}

/**
 * Whether a move needs the lost-reason dialog before it can be sent.
 *
 * The gateway refuses a move into a lost-type status with no reason (422),
 * and a board that discovers that from an error toast has already animated
 * the card into the lane. Asking first is the same rule, read forward.
 */
export function needsLostReason(
  deal: Pick<Deal, "lost_reason_id">,
  target: Pick<Status, "type">
): boolean {
  return target.type === "lost" && !deal.lost_reason_id;
}

// ── WS-26h · stage discipline (spec §5.1 system 3) ────────────────────────
//
// Two mechanisms, deliberately unlike each other. Entry requirements are a
// GATE — the same 422 the lost reason raises, read forward so the modal asks
// before the card animates. Rot is PRESENTATION and nothing else: a badge
// changes colour, no move is blocked, nothing is closed or hidden. Pipedrive's
// lesson, adopted on purpose.

/**
 * The deal columns a stage may demand. Mirrors
 * `routes/crm/core.py::STAGE_REQUIREABLE_FIELDS`.
 *
 * ⚠️ Two hand-kept lists are not a vocabulary, so this one is FENCED rather
 * than trusted: `tests/unit/test_crm_stage_discipline_parity.py` reads this
 * array out of this file and asserts it against the Python tuple, the same
 * shape as the seed-colour fence in `test_projects_migration.py`. Extend both
 * sides or neither.
 */
export const REQUIREABLE_FIELDS = [
  "amount",
  "expected_close_date",
  "company_id",
  "owner_email",
] as const;

export type RequireableField = (typeof REQUIREABLE_FIELDS)[number];

/** What each requirable field is called where a human reads it. */
export const REQUIREABLE_FIELD_LABELS: Record<RequireableField, string> = {
  amount: "Amount",
  expected_close_date: "Expected close date",
  company_id: "Company",
  owner_email: "Owner",
};

/**
 * Is this value absent for the purposes of an entry requirement?
 *
 * Mirrors `pipeline.py::_is_blank`, including the part that matters: `0` is a
 * number somebody typed and an empty string is an unfilled box. A plain
 * falsiness test would refuse a genuine ₹0 deal, which a pipeline really does
 * hold — and, worse here than on the server, it would do so by popping a modal
 * the server would then have accepted without.
 */
function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  return typeof value === "string" && !value.trim();
}

/**
 * What the TARGET stage demands that this deal has not got — in the stage's
 * own order, so the modal's fields read the way the grid was written.
 *
 * `pending` is the same PATCH's own edits, so a modal that has just collected
 * `amount` stops asking for it. That mirrors the server reading `patch` before
 * the row, and it is what lets fields + status travel in ONE request.
 */
export function missingRequiredFields(
  deal: Partial<Record<RequireableField, unknown>>,
  target: Pick<Status, "required_fields">,
  pending: Partial<Record<string, unknown>> = {}
): RequireableField[] {
  return (target.required_fields ?? [])
    .filter((field): field is RequireableField =>
      (REQUIREABLE_FIELDS as readonly string[]).includes(field)
    )
    .filter((field) =>
      isBlank(field in pending ? pending[field] : deal[field])
    );
}

/**
 * How overdue a card is in its stage. Presentation only.
 *
 * `"fresh"` also covers "we cannot say": a stage with no threshold, and a deal
 * with no `status_changed_at`, are both answered as not-rotted rather than as
 * rotted — the same rule `stageAgeDays` follows in returning null instead of 0.
 * Colouring a card on the strength of a missing timestamp is how a badge stops
 * meaning anything.
 */
export type RotLevel = "fresh" | "warn" | "over";

export function rotLevel(
  since: string | null | undefined,
  maxDwellDays: number | null | undefined,
  now: Date = new Date()
): RotLevel {
  if (!maxDwellDays || maxDwellDays <= 0) return "fresh";
  const days = stageAgeDays(since, now);
  if (days === null) return "fresh";
  // Strictly past, not "at": a 14-day threshold means a card is fine ON day 14
  // and overdue on day 15. `>=` would flag a deal the moment it hit the number
  // the owner set as the allowance.
  if (days > maxDwellDays * 2) return "over";
  if (days > maxDwellDays) return "warn";
  return "fresh";
}

/** The tone a rotted card's age badge wears. House tokens, never a colour. */
export const ROT_TONES: Record<RotLevel, string> = {
  fresh: "text-muted-foreground",
  warn: "text-warning",
  over: "text-destructive",
};
