// ── The kanban board's geometry and its one mutation ──────────────────────
//
// Everything the board does that is not painting pixels lives here, so it can
// be tested against fixtures without a DOM: lane assembly, lane headers, and
// the request a drag issues.
//
// Spec: project-docs/specs/crm_app.md §5 surface 1.

import { statusAccent, type StatusAccent } from "@/lib/statusAccent";

import type { CrmMode } from "./connect";
import { isRetired, visibleLanes } from "./emptyStages";
import { stageAgeDays } from "./format";
import type { Deal, Lead, Pipeline, PipelineLane, Status, StatusType } from "./types";

/** A lane as the board renders it: the wire lane. Its colour is `stageAccent`. */
export type BoardLane = PipelineLane;

/**
 * The colour of one CRM stage or status (WS-53 CRM-C2).
 *
 * The CRM kept its own colour-name map here until CRM-C2. It drew `blue` with
 * the member's accent and `violet` as orange, and it was a second palette
 * beside `statusAccent.ts`, which `AGENTS.md` rule 4 forbids. Now this asks
 * the shared module, and it holds no class string.
 *
 * - **Native mode, and a mode not yet known.** The stored colour answers
 *   first. Then the stage type: won green, lost red, on hold amber, ongoing
 *   blue. Then the name, then the position.
 * - **Mirror mode.** The stored colour is not passed. The sync never writes
 *   it, so every mirrored row is `gray` (`crm_platform.md` §20.1 fact 6), and
 *   a gray that nobody chose must not hide a won or a lost lane. The
 *   Metorite colour of a Mirror stage (`ui_color`, CRM-C3 item 16) wins when
 *   an admin set one. Else the type rule applies.
 *
 * No index is passed. So an open stage with no keyword in its name is gray on
 * every surface, and a lane that the board hides repaints no other lane.
 */
export function stageAccent(
  status: Pick<Status, "color" | "type" | "name"> & Pick<Partial<Status>, "ui_color">,
  mode: CrmMode,
): StatusAccent {
  return statusAccent({
    color: mode === "mirror" ? (status.ui_color ?? null) : status.color,
    stageType: status.type,
    name: status.name,
  });
}

/**
 * The icon of a closed or a paused stage, drawn in the chip in the place of
 * its dot. Colour is never the only signal (`DESIGN_SYSTEM.md` §7). An open
 * and an ongoing stage have none.
 */
const STAGE_ICONS: Partial<Record<StatusType, string>> = {
  won: "CircleCheck",
  lost: "CircleX",
  on_hold: "CirclePause",
};

export function stageIcon(type: StatusType): string | null {
  return STAGE_ICONS[type] ?? null;
}

/** The group of a stage type on the board: open work, then won, then lost. */
const LANE_GROUP: Record<StatusType, number> = {
  open: 0,
  ongoing: 0,
  on_hold: 1,
  won: 2,
  lost: 3,
};

/** The board group of a stage type: open work, on hold, won, then lost. */
export function laneGroup(type: StatusType): number {
  return LANE_GROUP[type] ?? 0;
}

/** Two pipeline places. A missing place sorts after every stated place. */
function comparePlaces(
  a: number | null | undefined,
  b: number | null | undefined
): number {
  const left = typeof a === "number" ? a : null;
  const right = typeof b === "number" ? b : null;
  if (left === right) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return left - right;
}

/**
 * The admin order of a Mirror kind (WS-53 CRM-C3, §20.6 item 12). The key is
 * `(ui_position IS NULL, ui_position, source position)`: each ordered row by
 * its `ui_position`, then each row with none, in Zoho order. The order route
 * writes `ui_position` on every row, so only a stage that Zoho adds later has
 * none, and it goes to the end of its group.
 */
function compareAdminOrder(a: Status, b: Status): number {
  const left = typeof a.ui_position === "number" ? a.ui_position : null;
  const right = typeof b.ui_position === "number" ? b.ui_position : null;
  if (left === null && right !== null) return 1;
  if (left !== null && right === null) return -1;
  if (left !== null && right !== null && left !== right) return left - right;
  return (a.source_position ?? a.position) - (b.source_position ?? b.position);
}

/**
 * THE order of the Deals board columns (owner decision, 2026-10-12). This is
 * the one place that decides it, and the mode is a parameter.
 *
 * The groups, in both modes: open and ongoing stages, then `on_hold`, then
 * won, then lost at the far end. A hidden stage, and one that Zoho no longer
 * lists, goes to the end of its group (CRM-C3).
 *
 * **The order never follows the probability** (owner rule, 2026-10-20). The
 * board order and the win chance are two separate settings. A stage at 90%
 * can sit before a stage at 10%. `board.test.ts` fences this.
 *
 * - **Mirror mode, after an admin order** (`admin_order`). The admin order
 *   key of `compareAdminOrder`, in each group. The pipeline place does not
 *   apply: a pipeline is an ordered subset of the one stage list.
 * - **Mirror mode, no admin order.** The source `position`, then the place in
 *   the chosen pipeline.
 * - **Native mode, and a mode not yet known.** The admin's stage order, the
 *   global `position`, then the pipeline place.
 *
 * A stage with no pipeline place (no pipeline chosen, or a stage that only
 * holds deals) sorts after the stated places of its tie. The stage editor in
 * CRM settings keeps its own order.
 */
export function orderLanes<T extends Pick<PipelineLane, "status" | "pipeline_position">>(
  lanes: readonly T[],
  mode: CrmMode
): T[] {
  const mirror = mode === "mirror";
  const adminOrder = mirror && lanes.some((lane) => lane.status.admin_order);
  return [...lanes].sort((a, b) => {
    const group = (LANE_GROUP[a.status.type] ?? 0) - (LANE_GROUP[b.status.type] ?? 0);
    if (group !== 0) return group;
    const retired = Number(isRetired(a.status)) - Number(isRetired(b.status));
    if (retired !== 0) return retired;
    if (adminOrder) {
      const key = compareAdminOrder(a.status, b.status);
      if (key !== 0) return key;
    } else if (mirror) {
      const source =
        (a.status.source_position ?? a.status.position) -
        (b.status.source_position ?? b.status.position);
      if (source !== 0) return source;
      const inPipeline = comparePlaces(a.pipeline_position, b.pipeline_position);
      if (inPipeline !== 0) return inPipeline;
    } else {
      const global = a.status.position - b.status.position;
      if (global !== 0) return global;
      const inPipeline = comparePlaces(a.pipeline_position, b.pipeline_position);
      if (inPipeline !== 0) return inPipeline;
    }
    return a.status.name.localeCompare(b.status.name);
  });
}

/**
 * Lanes in the Native order of `orderLanes`, empty ones included. The page
 * orders them again for its mode, and decides which empty lanes to hide
 * (`lib/emptyStages.ts`). The store does not know the mode.
 *
 * The board must not depend on a payload's ordering to be correct, so it
 * sorts here and does not trust the order of the payload.
 */
export function toBoardLanes(pipeline: Pipeline | null): BoardLane[] {
  if (!pipeline) return [];
  return orderLanes(pipeline.lanes, null);
}

// ── The Leads board (CRM-Q3) ──────────────────────────────────────────────
//
// The columns are the Lead Status values, in order. A lead has no amount and no
// forecast, so a lane carries a count and nothing else. The card and the drop
// are the same as on the deals board, so one `KanbanBoard` draws both.

/** A Leads board column. */
export type LeadLane = Omit<PipelineLane, "rows"> & {
  rows: Lead[];
};

/**
 * Lanes from the Lead Status list and one page of leads for each status.
 *
 * `total` is the count of the WHOLE status. `rows` is one page of it, as on the
 * deals board. A status with no page reads as an empty lane.
 *
 * CRM-C3: the Leads board keeps the order of the statuses, with no groups by
 * type, so "Junk Lead" stays where Zoho puts it. The effective `position`
 * decides, and the gateway already folds the admin order into it: the
 * `ui_position` after an admin order, else the source order. A hidden status,
 * and one that Zoho no longer lists, goes last and shows only with leads in it.
 */
export function toLeadLanes(
  statuses: readonly Status[],
  pages: Readonly<Record<string, { rows: Lead[]; total: number } | undefined>>,
): LeadLane[] {
  const lanes = [...statuses]
    .sort(
      (a, b) =>
        Number(isRetired(a)) - Number(isRetired(b)) || a.position - b.position
    )
    .map((status) => {
      const page = pages[status.id];
      return {
        status,
        rows: page?.rows ?? [],
        count: page?.total ?? 0,
        amount: 0,
        weighted: 0,
      };
    });
  return visibleLanes(lanes, true);
}

/**
 * The leads a lane expects to convert: count x win chance / 100.
 *
 * A lead has no amount, so the lane header shows this in place of a weighted
 * total. The count is the whole lane, as the gateway counts it. The result
 * keeps its fraction, and the header rounds it to one decimal.
 */
export function expectedLeads(lane: Pick<LeadLane, "count" | "status">): number {
  const chance = Math.min(100, Math.max(0, lane.status.probability ?? 0));
  return (lane.count * chance) / 100;
}

/**
 * The expected leads as the lane header writes them: one decimal, and a whole
 * number with no ".0". `3.5` reads "3.5". `4` reads "4". `0` reads "0".
 */
export function formatExpected(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

/**
 * The second figure of a deal lane header. An open lane shows the weighted
 * total and its win chance. A closed or parked lane says why it has no
 * forecast, so a zero never reads as a bug.
 */
export function dealForecastLine(
  status: Pick<Status, "type" | "probability">,
  weightedText: string
): string {
  switch (status.type) {
    case "won":
      return "booked, not forecast";
    case "lost":
      return "no forecast";
    case "on_hold":
      return "on hold";
    default:
      return `${weightedText} weighted \u00b7 ${status.probability ?? 0}%`;
  }
}

/** The second line of a lead lane header: the count, then the expected wins. */
export function leadForecastLines(lane: Pick<LeadLane, "count" | "status">): {
  count: string;
  expected: string;
} {
  const chance = lane.status.probability ?? 0;
  return {
    count: `${lane.count} ${lane.count === 1 ? "lead" : "leads"}`,
    expected: `${chance === 0 ? "0" : `~${formatExpected(expectedLeads(lane))}`} expected \u00b7 ${chance}%`,
  };
}

/** The leads board as it should look the instant a card is dropped. */
export function applyLeadMove(lanes: LeadLane[], move: DealMove): LeadLane[] {
  const moving = lanes.flatMap((lane) => lane.rows).find((lead) => lead.id === move.dealId);
  if (!moving) return lanes;
  return lanes.map((lane) => {
    if (lane.status.id === move.fromStatusId) {
      return {
        ...lane,
        rows: lane.rows.filter((lead) => lead.id !== move.dealId),
        count: Math.max(0, lane.count - 1),
      };
    }
    if (lane.status.id === move.toStatusId) {
      return {
        ...lane,
        rows: [{ ...moving, status_id: move.toStatusId }, ...lane.rows],
        count: lane.count + 1,
      };
    }
    return lane;
  });
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
 *  the whole lane — not the rows returned, which are one page of it.
 *
 *  The header says "open deals" and "in the pipeline", so only the lanes in
 *  `WEIGHTED_TYPES` count. A won or a lost deal is closed, which is the rule
 *  of the owner leaderboard's `open_deals` in `routes/crm/reports.py`. */
export function boardTotals(lanes: BoardLane[]): {
  count: number;
  amount: number;
  weighted: number;
} {
  return lanes.reduce(
    (acc, lane) =>
      WEIGHTED_TYPES.includes(lane.status.type)
        ? {
            count: acc.count + lane.count,
            amount: acc.amount + (lane.amount || 0),
            weighted: acc.weighted + (lane.weighted || 0),
          }
        : acc,
    { count: 0, amount: 0, weighted: 0 }
  );
}

/**
 * The move a drop asks for, or null when the drop changes nothing.
 *
 * `dealId` names the record that moves. On the Leads board it holds a lead id.
 */
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
