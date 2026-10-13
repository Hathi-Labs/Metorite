import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  applyMove,
  boardTotals,
  missingRequiredFields,
  moveRequest,
  needsLostReason,
  orderLanes,
  planMove,
  REQUIREABLE_FIELD_LABELS,
  REQUIREABLE_FIELDS,
  ROT_TONES,
  rotLevel,
  stageAccent,
  stageIcon,
  toBoardLanes,
  weightedDeal,
  weightedRows,
  WEIGHTED_TYPES,
  type BoardLane,
} from "./board";
import { accentForHue } from "@/lib/statusAccent";

import type { Deal, Pipeline, Status, StatusType } from "./types";

// ── Fixtures ──────────────────────────────────────────────────────────────
//
// Shaped exactly like GET /crm/pipeline's payload (routes/crm/pipeline.py's
// PipelineResponse), including the `organization_name` the gateway's LEFT
// JOIN projects onto every deal row — migration 144 has not been applied
// anywhere, so a fixture is the whole of what can be asserted here and the
// live render is owner-verified after it applies.

function status(over: Partial<Status> & { id: string; name: string }): Status {
  return {
    color: "gray",
    position: 10,
    type: "open",
    is_default: false,
    probability: 0,
    ...over,
  };
}

function deal(over: Partial<Deal> & { id: string }): Deal {
  return {
    name: "Printer order",
    currency: "INR",
    organization_name: null,
    ...over,
  };
}

const QUALIFICATION = status({
  id: "s1",
  name: "Qualification",
  position: 10,
  color: "blue",
  // 144's seed priors: a deal that has stated nothing inherits these.
  probability: 10,
});
const PROPOSAL = status({
  id: "s2",
  name: "Proposal",
  position: 30,
  type: "ongoing",
  color: "amber",
  probability: 50,
});
const LOST = status({
  id: "s3",
  name: "Closed Lost",
  position: 60,
  type: "lost",
  color: "red",
});

const PIPELINE: Pipeline = {
  lanes: [
    {
      // Deliberately out of order — the board must not depend on the payload's
      // ordering to be correct.
      status: PROPOSAL,
      rows: [
        deal({
          id: "d1",
          name: "Bosch printer",
          amount: 250000,
          probability: 50,
          status_id: "s2",
          organization_name: "Bosch India",
        }),
      ],
      count: 4,
      amount: 900000,
      // Whole-lane, from the gateway — deliberately NOT derivable from `rows`,
      // which is one page of the four deals the lane holds.
      weighted: 450000,
    },
    {
      status: QUALIFICATION,
      rows: [
        deal({ id: "d2", amount: 100000, probability: 10, status_id: "s1" }),
      ],
      count: 1,
      amount: 100000,
      weighted: 10000,
    },
    { status: LOST, rows: [], count: 0, amount: 0, weighted: 0 },
  ],
};

describe("toBoardLanes", () => {
  it("orders lanes by position and keeps the empty ones", () => {
    // A kanban that hides its empty columns is a list.
    const lanes = toBoardLanes(PIPELINE);
    expect(lanes.map((l) => l.status.name)).toEqual([
      "Qualification",
      "Proposal",
      "Closed Lost",
    ]);
    expect(lanes[2].rows).toEqual([]);
  });

  it("carries the organization name the gateway joined in", () => {
    // WS-26c dw 2: the card prints it, and the browser must not client-side
    // join a ≤100-row page of organizations to get it.
    const lanes = toBoardLanes(PIPELINE);
    const proposal = lanes.find((l) => l.status.name === "Proposal")!;
    expect(proposal.rows[0].organization_name).toBe("Bosch India");
  });

  it("survives an unloaded board", () => {
    expect(toBoardLanes(null)).toEqual([]);
  });
});

describe("stageAccent (WS-53 CRM-C2)", () => {
  const stage = (color: string, type: StatusType, name = "Stage") => ({ color, type, name });

  it("maps the stored colour through the shared vocabulary", () => {
    // `color` is free text the owner (and the Zoho importer) writes. The
    // shared module turns it into token classes, so no class is built here.
    expect(stageAccent(stage("amber", "ongoing"), "native")).toBe(accentForHue("amber"));
    expect(stageAccent(stage("red", "lost"), "native")).toBe(accentForHue("red"));
  });

  it("draws the seeded Proposal lane violet, not the orange of the old map", () => {
    expect(stageAccent(stage("violet", "ongoing", "Proposal"), "native").dot).toBe("bg-violet");
  });

  it("never paints a blue lane with the member's accent", () => {
    expect(stageAccent(stage("sky", "open"), "native").dot).toBe("bg-info");
  });

  it("falls back by status TYPE, not to grey", () => {
    // An unrecognised colour on a won lane should still read as won.
    expect(stageAccent(stage("chartreuse", "won"), "native")).toBe(accentForHue("green"));
    expect(stageAccent(stage("", "lost"), null)).toBe(accentForHue("red"));
  });

  // Done-when 4: every mirrored row is `gray` (§20.1 fact 6), and Mirror mode
  // must still show what a lane means.
  it.each([
    ["won", "green"],
    ["lost", "red"],
    ["on_hold", "amber"],
    ["ongoing", "blue"],
    ["open", "gray"],
  ] as const)("a gray Mirror %s stage draws %s", (type, hue) => {
    expect(stageAccent(stage("gray", type), "mirror")).toBe(accentForHue(hue));
  });

  it("keeps a Native admin's gray, because a person chose it", () => {
    expect(stageAccent(stage("gray", "won"), "native")).toBe(accentForHue("gray"));
  });

  it("gives a closed or a paused stage an icon, so colour is not alone", () => {
    expect(stageIcon("won")).toBe("CircleCheck");
    expect(stageIcon("lost")).toBe("CircleX");
    expect(stageIcon("on_hold")).toBe("CirclePause");
    expect(stageIcon("open")).toBeNull();
    expect(stageIcon("ongoing")).toBeNull();
  });
});

describe("boardTotals", () => {
  it("sums the LANE totals, not the rows returned", () => {
    // Each lane returns one page of deals; its count and ₹ total cover the
    // whole lane. Summing the visible cards would understate a busy board.
    const totals = boardTotals(toBoardLanes(PIPELINE));
    expect(totals.count).toBe(5);
    expect(totals.amount).toBe(1000000);
  });

  it("rolls the weighted figures up the same way", () => {
    // WS-26f f3: the header's weighted number is the sum of the LANE weighted
    // figures the gateway computed, not a recomputation over the cards — the
    // Proposal lane holds four deals and returned one.
    const totals = boardTotals(toBoardLanes(PIPELINE));
    expect(totals.weighted).toBe(460000);
  });
});

// ── WS-26f f3 · the weighted formula (done-when 6) ────────────────────────

describe("weightedDeal", () => {
  it("uses the deal's own probability", () => {
    // D-CRM-10: forecast math reads the DEAL, never the stage. A rep who
    // knows the champion just left marks a Proposal deal at 20 without moving
    // it, and the forecast has to believe them.
    expect(weightedDeal({ amount: 200000, probability: 20 }, PROPOSAL)).toBe(
      40000
    );
  });

  it("inherits the stage default for a NULL probability", () => {
    // The inheritance materializes when a deal ENTERS a stage, so a NULL
    // survives only on rows that predate a move — i.e. every imported deal,
    // which is most of the board today.
    expect(weightedDeal({ amount: 200000, probability: null }, PROPOSAL)).toBe(
      100000
    );
    expect(
      weightedDeal({ amount: 200000, probability: undefined }, PROPOSAL)
    ).toBe(100000);
  });

  it("treats a stated 0 as a measurement, not as a missing value", () => {
    expect(weightedDeal({ amount: 200000, probability: 0 }, PROPOSAL)).toBe(0);
  });

  it("carries a 100 through unchanged", () => {
    expect(weightedDeal({ amount: 200000, probability: 100 }, PROPOSAL)).toBe(
      200000
    );
  });

  it("forecasts nothing for a deal with no amount", () => {
    expect(weightedDeal({ amount: null, probability: 80 }, PROPOSAL)).toBe(0);
  });

  it("falls back to zero when neither the deal nor the stage says", () => {
    expect(
      weightedDeal({ amount: 200000, probability: null }, { probability: null })
    ).toBe(0);
  });
});

describe("weightedRows", () => {
  const ON_HOLD = status({
    id: "s4",
    name: "Parked",
    position: 40,
    type: "on_hold",
    probability: 40,
  });
  const WON = status({
    id: "s5",
    name: "Closed Won",
    position: 50,
    type: "won",
    probability: 100,
  });
  const rows = [
    deal({ id: "a", amount: 100000, probability: 25 }),
    deal({ id: "b", amount: 200000, probability: null }),
    deal({ id: "c", amount: 50000, probability: 0 }),
  ];

  it("sums a mixed lane", () => {
    // 25000 + (200000 × 50% inherited) + 0
    expect(weightedRows(rows, PROPOSAL)).toBe(125000);
  });

  it("counts only open and ongoing stages", () => {
    expect(WEIGHTED_TYPES).toEqual(["open", "ongoing"]);
    expect(weightedRows(rows, QUALIFICATION)).toBeGreaterThan(0);
  });

  it("forecasts nothing from a won lane — that is closed revenue", () => {
    expect(weightedRows(rows, WON)).toBe(0);
  });

  it("forecasts nothing from a lost lane", () => {
    expect(weightedRows(rows, LOST)).toBe(0);
  });

  it("excludes on_hold, the fifth type the spec's prose never mentions", () => {
    // A deal nobody is working is not a forecast. Counting it at its stage's
    // prior is how a pipeline number stays high while the quarter empties.
    expect(weightedRows(rows, ON_HOLD)).toBe(0);
  });

  it("is zero for an empty lane rather than NaN", () => {
    expect(weightedRows([], PROPOSAL)).toBe(0);
  });
});

// ── WS-26g · the cross-language weighted parity fixture ───────────────────
//
// ONE file, read by BOTH runners. `tests/unit/test_crm_reports.py` drives the
// same rows through the emitted SQL (`core.WEIGHTED_SQL`, read out of the
// statement text by `_crm_fakes._WEIGHTED_SUM_RE`); this drives them through
// `weightedDeal`/`weightedRows`. Two independently typed tables are not
// parity, which is the whole reason the fixture exists rather than a comment
// pointing one implementation at the other.

type ParityRow = {
  name: string;
  amount: number | null;
  deal_probability: number | null;
  stage_probability: number | null;
  /** CRM-C3: the Metorite overlay of the stage, or null. */
  ui_probability: number | null;
  stage_type: StatusType;
  expected_weighted: number;
};

/**
 * The stage probability the wire carries for a fixture row (CRM-C3, §20.6
 * item 5): the gateway sends the EFFECTIVE value, the overlay when it is set,
 * else the source. `weightedDeal` reads the wire with no change, so the board
 * side of the parity reads it the same way.
 */
const wireProbability = (row: ParityRow): number | null =>
  row.ui_probability ?? row.stage_probability;

const PARITY: ParityRow[] = JSON.parse(
  readFileSync(
    fileURLToPath(
      // …/crm/lib → …/crm → …/app → …/src → control_plane → workbench → repo
      new URL(
        "../../../../../../tests/fixtures/crm_weighted_parity.json",
        import.meta.url
      )
    ),
    "utf-8"
  )
).rows;

describe("the weighted parity fixture", () => {
  it("is present and non-trivial", () => {
    // A fixture that quietly disappeared would leave this file asserting
    // nothing while the pytest half stayed green — the exact failure a shared
    // fixture exists to remove, so its absence must be loud on both sides.
    expect(PARITY.length).toBeGreaterThanOrEqual(10);
  });

  it.each(PARITY)(
    "weightedRows agrees with the SQL: $name",
    (row: ParityRow) => {
      // `weightedRows` is the function the fixture pins because it applies the
      // open/ongoing filter, exactly as the gateway's `_lane_totals` does — so
      // the won/lost/on_hold rows assert the exclusion rather than skipping.
      expect(
        weightedRows(
          [{ amount: row.amount, probability: row.deal_probability }],
          { type: row.stage_type, probability: wireProbability(row) }
        )
      ).toBeCloseTo(row.expected_weighted, 6);
    }
  );

  it.each(PARITY.filter((row) => WEIGHTED_TYPES.includes(row.stage_type)))(
    "weightedDeal agrees for a forecastable stage: $name",
    (row: ParityRow) => {
      expect(
        weightedDeal(
          { amount: row.amount, probability: row.deal_probability },
          { probability: wireProbability(row) }
        )
      ).toBeCloseTo(row.expected_weighted, 6);
    }
  );
});

describe("planMove", () => {
  it("describes a real move", () => {
    expect(planMove({ id: "d1", status_id: "s2" }, "s1")).toEqual({
      dealId: "d1",
      fromStatusId: "s2",
      toStatusId: "s1",
    });
  });

  it("refuses a drop back into the same lane", () => {
    // Sending it anyway would write a crm_status_changes row with a dwell of
    // zero and a timeline entry reading "Proposal → Proposal".
    expect(planMove({ id: "d1", status_id: "s2" }, "s2")).toBeNull();
  });

  it("treats a deal with no status as movable", () => {
    expect(planMove({ id: "d1", status_id: null }, "s1")?.fromStatusId).toBeNull();
  });
});

describe("moveRequest", () => {
  it("is the PATCH the drag issues", () => {
    // The status pill in the record sheet issues the same one, so the two
    // cannot disagree about what moving a deal means.
    expect(
      moveRequest({ dealId: "d1", fromStatusId: "s2", toStatusId: "s1" })
    ).toEqual({
      path: "/deals/d1",
      method: "PATCH",
      body: { status_id: "s1" },
    });
  });
});

describe("applyMove", () => {
  const lanes = toBoardLanes(PIPELINE);
  const move = { dealId: "d1", fromStatusId: "s2", toStatusId: "s1" };

  it("moves the card and re-tallies both lanes at once", () => {
    const next = applyMove(lanes, move);
    const from = next.find((l) => l.status.id === "s2")!;
    const to = next.find((l) => l.status.id === "s1")!;

    expect(from.rows.map((d) => d.id)).toEqual([]);
    expect(from.count).toBe(3);
    expect(from.amount).toBe(650000);
    expect(to.rows.map((d) => d.id)).toEqual(["d1", "d2"]);
    expect(to.count).toBe(2);
    expect(to.amount).toBe(350000);
  });

  it("re-tallies the weighted figures with them", () => {
    // Leaving weighted until the server re-read would show a lane whose ₹
    // total dropped and whose forecast did not — two numbers side by side
    // disagreeing about the same drag. d1 is ₹250,000 at 50%.
    const next = applyMove(lanes, move);
    const from = next.find((l) => l.status.id === "s2")!;
    const to = next.find((l) => l.status.id === "s1")!;

    expect(from.weighted).toBe(450000 - 125000);
    expect(to.weighted).toBe(10000 + 125000);
  });

  it("never lets a re-tally take a lane's forecast negative", () => {
    const next = applyMove(
      [
        { ...lanes[0], weighted: 0 },
        { ...lanes[1] },
        { ...lanes[2] },
      ],
      { dealId: "d2", fromStatusId: "s1", toStatusId: "s2" }
    );
    expect(next[0].weighted).toBe(0);
  });

  it("re-stamps the moved card's status so it does not re-render in limbo", () => {
    const next = applyMove(lanes, move);
    expect(next.find((l) => l.status.id === "s1")!.rows[0].status_id).toBe("s1");
  });

  it("leaves the input untouched so a refusal can put the card back", () => {
    applyMove(lanes, move);
    expect(lanes.find((l) => l.status.id === "s2")!.rows).toHaveLength(1);
  });

  it("does nothing for a card it cannot find", () => {
    expect(
      applyMove(lanes, { dealId: "ghost", fromStatusId: "s2", toStatusId: "s1" })
    ).toBe(lanes);
  });

  it("never counts a lane below zero", () => {
    const empty: BoardLane[] = [
      {
        status: QUALIFICATION,
        rows: [],
        count: 0,
        amount: 0,
        weighted: 0,
      },
      {
        status: PROPOSAL,
        rows: [deal({ id: "d9", status_id: "s2" })],
        count: 0,
        amount: 0,
        weighted: 0,
      },
    ];
    const next = applyMove(empty, {
      dealId: "d9",
      fromStatusId: "s2",
      toStatusId: "s1",
    });
    expect(next[1].count).toBe(0);
  });
});

describe("needsLostReason", () => {
  it("asks before the drop rather than after the 422", () => {
    // The gateway refuses a move into a lost-type status with no reason. A
    // board that learns that from an error toast has already animated the
    // card into the lane.
    expect(needsLostReason({ lost_reason_id: null }, LOST)).toBe(true);
    expect(needsLostReason({ lost_reason_id: "r1" }, LOST)).toBe(false);
    expect(needsLostReason({ lost_reason_id: null }, PROPOSAL)).toBe(false);
  });
});

// ── WS-26h · stage discipline ─────────────────────────────────────────────

describe("missingRequiredFields", () => {
  const gated = { required_fields: ["amount", "owner_email"] };

  it("names everything the target stage demands and the deal has not got", () => {
    expect(
      missingRequiredFields({ amount: null, owner_email: null }, gated)
    ).toEqual(["amount", "owner_email"]);
  });

  it("stops asking for what the deal already carries", () => {
    expect(
      missingRequiredFields({ amount: 400000, owner_email: null }, gated)
    ).toEqual(["owner_email"]);
  });

  it("counts a zero amount as filled in", () => {
    // 0 is a number somebody typed. A falsiness test here would pop a modal
    // for a ₹0 deal the gateway would have accepted without one — the client
    // being STRICTER than the boundary, which is a feature nobody can reach.
    expect(missingRequiredFields({ amount: 0, owner_email: "a@b.c" }, gated))
      .toEqual([]);
  });

  it("counts a whitespace-only string as empty", () => {
    expect(missingRequiredFields({ amount: 1, owner_email: "  " }, gated))
      .toEqual(["owner_email"]);
  });

  it("lets the same PATCH's own edits satisfy a requirement", () => {
    // This is what makes fields + status ONE request: the modal collects a
    // value and the check stops asking for it, exactly as the gateway reads
    // `patch` before the row.
    expect(
      missingRequiredFields({ amount: null, owner_email: null }, gated, {
        amount: 400000,
        owner_email: "asha@fracktal.in",
      })
    ).toEqual([]);
  });

  it("requires nothing of an ungated stage", () => {
    expect(missingRequiredFields({ amount: null }, PROPOSAL)).toEqual([]);
    expect(missingRequiredFields({ amount: null }, { required_fields: [] }))
      .toEqual([]);
  });

  it("ignores a field name the client does not know", () => {
    // R6's window in reverse: an older bundle meeting a newer gateway that has
    // grown a fifth requirable column. Passing it through would render an
    // input for a field this build cannot label, so it is dropped and the
    // gateway's own 422 stays the boundary.
    expect(missingRequiredFields({}, { required_fields: ["not_a_column"] }))
      .toEqual([]);
  });

  it("every requirable field has a label", () => {
    // A missing label renders as `undefined` in the modal's own heading.
    for (const field of REQUIREABLE_FIELDS) {
      expect(REQUIREABLE_FIELD_LABELS[field]).toBeTruthy();
    }
  });
});

describe("rotLevel", () => {
  const NOW = new Date("2026-08-11T12:00:00Z");
  const daysAgo = (days: number) =>
    new Date(NOW.getTime() - days * 86_400_000).toISOString();

  it("says fresh when the stage has no threshold", () => {
    expect(rotLevel(daysAgo(400), null, NOW)).toBe("fresh");
    expect(rotLevel(daysAgo(400), undefined, NOW)).toBe("fresh");
  });

  it("says fresh when there is no stage-age timestamp", () => {
    // "We were never told" is not a measurement. Colouring a card on the
    // strength of a missing timestamp is how a badge stops meaning anything.
    expect(rotLevel(null, 14, NOW)).toBe("fresh");
    expect(rotLevel("not a date", 14, NOW)).toBe("fresh");
  });

  it("is fine ON the threshold and warns the day after", () => {
    expect(rotLevel(daysAgo(13), 14, NOW)).toBe("fresh");
    expect(rotLevel(daysAgo(14), 14, NOW)).toBe("fresh");
    expect(rotLevel(daysAgo(15), 14, NOW)).toBe("warn");
  });

  it("is fine ON twice the threshold and turns over the day after", () => {
    expect(rotLevel(daysAgo(27), 14, NOW)).toBe("warn");
    expect(rotLevel(daysAgo(28), 14, NOW)).toBe("warn");
    expect(rotLevel(daysAgo(29), 14, NOW)).toBe("over");
  });

  it("handles a one-day threshold at both boundaries", () => {
    expect(rotLevel(daysAgo(1), 1, NOW)).toBe("fresh");
    expect(rotLevel(daysAgo(2), 1, NOW)).toBe("warn");
    expect(rotLevel(daysAgo(3), 1, NOW)).toBe("over");
  });

  it("every level has a tone, and none of them is a raw colour", () => {
    for (const level of ["fresh", "warn", "over"] as const) {
      expect(ROT_TONES[level]).toMatch(/^text-[a-z-]+$/);
    }
    // Rot must be legible as a MEASUREMENT — the house's warning/destructive
    // tones — never as a hue somebody picked (DESIGN_SYSTEM rule 4).
    expect(ROT_TONES.warn).toBe("text-warning");
    expect(ROT_TONES.over).toBe("text-destructive");
  });
});

// ── CRM-C3 · the admin order key (crm_platform.md §20.6 item 12) ──────────
//
// In Mirror mode an admin order wins. The key is (ui_position IS NULL,
// ui_position, source position), in each of the four groups. Done when 9.

describe("orderLanes with an admin order", () => {
  /** A lane of a Mirror org: source position `source`, Metorite order `ui`. */
  const lane = (
    id: string,
    type: StatusType,
    source: number,
    ui: number | null,
    over: Partial<Status> = {}
  ) => ({
    pipeline_position: null,
    status: status({
      id,
      name: id,
      type,
      source_position: source,
      ui_position: ui,
      // The gateway folds the key into the effective position (core.py).
      position: ui ?? 1_000_000 + source,
      probability: 10 * source,
      admin_order: true,
      ...over,
    }),
  });
  const names = (lanes: { status: Status }[]) => lanes.map((l) => l.status.name);

  it("sorts by the admin order, and a new Zoho stage goes to the end of its group", () => {
    const lanes = [
      lane("Qualify", "open", 0, 20),
      lane("Propose", "open", 1, 10),
      lane("Discover", "open", 5, null), // Zoho added it after the admin order
      lane("Won", "won", 2, 0),
      lane("Lost", "lost", 3, 30),
    ];
    expect(names(orderLanes(lanes, "mirror"))).toEqual([
      "Propose",
      "Qualify",
      "Discover",
      "Won",
      "Lost",
    ]);
  });

  it("does not sort by probability once an admin ordered the stages", () => {
    const lanes = [lane("Low", "open", 0, 10, { probability: 5 }), lane("High", "open", 1, 0, { probability: 90 })];
    expect(names(orderLanes(lanes, "mirror"))).toEqual(["High", "Low"]);
  });

  it("sorts by the effective probability in Mirror mode with no admin order", () => {
    const lanes = [
      lane("High", "open", 0, null, { admin_order: false, probability: 90 }),
      lane("Low", "open", 1, null, { admin_order: false, probability: 5 }),
    ];
    expect(names(orderLanes(lanes, "mirror"))).toEqual(["Low", "High"]);
  });

  it("sorts a Native board by position, which the gateway already made effective", () => {
    const lanes = [
      lane("B", "open", 0, null, { admin_order: false, position: 20 }),
      lane("A", "open", 1, null, { admin_order: false, position: 10 }),
    ];
    expect(names(orderLanes(lanes, null))).toEqual(["A", "B"]);
    expect(names(orderLanes(lanes, "native"))).toEqual(["A", "B"]);
  });

  it("puts a hidden stage, and a stage that left Zoho, at the end of its group", () => {
    const lanes = [
      lane("Hidden", "open", 0, 0, { hidden: true }),
      lane("Gone", "open", 1, 5, { source_gone_at: "2026-10-13T00:00:00Z" }),
      lane("Open", "open", 2, 10),
      lane("Won", "won", 3, 20),
    ];
    expect(names(orderLanes(lanes, "mirror"))).toEqual(["Open", "Hidden", "Gone", "Won"]);
  });
});
