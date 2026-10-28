// CRM-U11: the stage settings are the board (D137, crm_platform.md §20.15).
//
// The runner has no DOM. The order rule is called directly, the screen and the
// Edit dialog render to a string, the store runs on a fake `fetch`, and a
// wiring rule is a scan of the source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import * as board from "../lib/board";
import { orderLanes, toBoardLanes } from "../lib/board";
import { placesOf } from "../lib/pipelines";
import { mergePartOrder, orderAfterMove, orderWithNewStage } from "../lib/settings";
import { useCrmStore } from "../lib/store";
import type { DealPipeline, PipelineLane, Status, StatusType } from "../lib/types";
import PipelineSettings, { MirrorStageForm, NativeStageForm, StageList } from "./PipelineSettings";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

// ── The Fracktal shape, measured on production 2026-10-27 ─────────────────
//
// "Fracktal Sales Pipeline" lists twelve stages. Five more stages are outside
// it. The source position of each stage is its place in the Zoho picklist.

/** The twelve stages of the pipeline, in its order. */
const PIPELINE_ORDER = [
  "Re-Evaluate",
  "Confirmation and Advance",
  "New Deal",
  "Discovery Meeting",
  "Sample parts",
  "Proposal / Quote Sent",
  "Negotiation/Review",
  "Awaiting PO",
  "PO Received",
  "Closed Won",
  "Payment Complete",
  "Closed Lost",
];

/** The global Zoho picklist, in its order: the source positions 0 to 16. */
const PICKLIST = [
  "Confirmation and Advance",
  "New Deal",
  "Discovery Meeting",
  "Sample parts",
  "Proposal / Quote Sent",
  "Negotiation/Review",
  "Awaiting PO",
  "PO Received",
  "Closed Won",
  "Payment Complete",
  "Closed Lost",
  "Meeting Scheduled",
  "Re-Evaluate",
  "Delivered & Installed",
  "Discovery Meeting Done",
  "New Lead",
  "Negotiation / Tendering",
];

/** The five stages that the pipeline does not list, in picklist order. */
const OUTSIDE = PICKLIST.filter((name) => !PIPELINE_ORDER.includes(name));

/**
 * A type and a win chance that would have moved a stage under the old rule:
 * a lost stage first in the pipeline, won stages in the middle, an on-hold
 * stage, and win chances that do not rise with the pipeline.
 */
const TYPE: Record<string, [StatusType, number]> = {
  "Re-Evaluate": ["lost", 0],
  "Closed Won": ["won", 100],
  "Payment Complete": ["won", 100],
  "Closed Lost": ["lost", 0],
  "Delivered & Installed": ["won", 100],
  "Meeting Scheduled": ["on_hold", 5],
  "Confirmation and Advance": ["open", 90],
  "New Deal": ["open", 10],
};

const idOf = (name: string) => `id:${name}`;

function fracktalStatus(name: string, over: Partial<Status> = {}): Status {
  const source = PICKLIST.indexOf(name);
  const [type, probability] = TYPE[name] ?? ["open", 40];
  return {
    id: idOf(name),
    name,
    color: "gray",
    position: source,
    type,
    is_default: false,
    probability,
    source_position: source,
    ui_position: null,
    admin_order: false,
    hidden: false,
    source_gone_at: null,
    ...over,
  };
}

/** The board lanes, in a scrambled payload order. */
function fracktalLanes(
  over: (name: string) => Partial<Status> = () => ({}),
  place: (name: string) => number | null = () => null,
): PipelineLane[] {
  const scrambled = [...PICKLIST].reverse();
  return scrambled.map((name) => ({
    status: fracktalStatus(name, over(name)),
    rows: [],
    count: 1,
    amount: 0,
    weighted: 0,
    pipeline_position: place(name),
  }));
}

/** `GET /crm/pipelines` for Fracktal: one pipeline, its twelve stages. */
const FRACKTAL_PIPELINES: DealPipeline[] = [
  {
    id: "p-sales",
    name: "Fracktal Sales Pipeline",
    is_default: true,
    position: 0,
    stages: PIPELINE_ORDER.map((name, index) => ({
      id: idOf(name),
      name,
      position: index,
      type: (TYPE[name] ?? ["open"])[0],
    })),
  },
];

const names = (lanes: readonly { status: Status }[]) => lanes.map((l) => l.status.name);

// ── A1: no saved order — the pipeline order, then the picklist order ───────

describe("orderLanes on the Fracktal shape with no saved order (A1)", () => {
  const placed = (name: string) =>
    PIPELINE_ORDER.includes(name) ? PIPELINE_ORDER.indexOf(name) : null;

  it("gives the twelve pipeline stages in pipeline order, then the five others in picklist order", () => {
    expect(names(orderLanes(fracktalLanes(() => ({}), placed), "mirror"))).toEqual([
      ...PIPELINE_ORDER,
      ...OUTSIDE,
    ]);
    expect(OUTSIDE).toEqual([
      "Meeting Scheduled",
      "Delivered & Installed",
      "Discovery Meeting Done",
      "New Lead",
      "Negotiation / Tendering",
    ]);
  });

  it("never moves a stage for its type or its win chance", () => {
    const plain = fracktalLanes(() => ({ type: "open", probability: 0 }), placed);
    const flipped = fracktalLanes(
      (name) => ({ type: name.length % 2 ? "won" : "lost", probability: name.length * 3 }),
      placed,
    );
    const expected = [...PIPELINE_ORDER, ...OUTSIDE];
    expect(names(orderLanes(plain, "mirror"))).toEqual(expected);
    expect(names(orderLanes(flipped, "mirror"))).toEqual(expected);
  });
});

// ── A2: a saved order wins until "Same as Zoho" ────────────────────────────

describe("orderLanes on the Fracktal shape with a saved order (A2)", () => {
  /** Fracktal's saved order: the picklist position x 10 on every stage. */
  const saved = (name: string): Partial<Status> => ({
    admin_order: true,
    ui_position: PICKLIST.indexOf(name) * 10,
    position: PICKLIST.indexOf(name) * 10,
  });

  it("gives the picklist order, Confirmation and Advance first, whatever the places say", () => {
    const placed = (name: string) =>
      PIPELINE_ORDER.includes(name) ? PIPELINE_ORDER.indexOf(name) : null;
    const lanes = fracktalLanes(saved, placed);
    expect(names(orderLanes(lanes, "mirror"))).toEqual(PICKLIST);
    expect(names(orderLanes(lanes, "mirror"))[0]).toBe("Confirmation and Advance");
  });

  it("puts a row with no saved place after the others, by source position, then by name", () => {
    const lanes = fracktalLanes((name) =>
      name === "New Deal" || name === "Awaiting PO" ? { admin_order: true, ui_position: null } : saved(name),
    );
    const extra = (name: string, source: number): PipelineLane => ({
      status: { ...fracktalStatus("Closed Won"), id: idOf(name), name, source_position: source, admin_order: true, ui_position: null },
      rows: [],
      count: 0,
      amount: 0,
      weighted: 0,
      pipeline_position: null,
    });
    const order = names(orderLanes([...lanes, extra("Zeta", 40), extra("Alpha", 40)], "mirror"));
    expect(order.slice(-4)).toEqual(["New Deal", "Awaiting PO", "Alpha", "Zeta"]);
    expect(order.slice(0, 3)).toEqual(["Confirmation and Advance", "Discovery Meeting", "Sample parts"]);
  });
});

// ── A3: retired stages last, and the Native order ─────────────────────────

describe("retired stages and the Native order (A3)", () => {
  const lane = (name: string, over: Partial<Status> = {}, place: number | null = null): PipelineLane => ({
    status: {
      id: name,
      name,
      color: "gray",
      position: 0,
      type: "open",
      is_default: false,
      probability: 0,
      ...over,
    },
    rows: [],
    count: 0,
    amount: 0,
    weighted: 0,
    pipeline_position: place,
  });

  it("sends a hidden stage and a stage that left Zoho to the end of the board", () => {
    const lanes = [
      lane("Hidden", { hidden: true, position: 0, source_position: 0 }, 0),
      lane("Gone", { source_gone_at: "2026-10-13T00:00:00Z", position: 1, source_position: 1 }, 1),
      lane("Lost", { type: "lost", position: 5, source_position: 5 }, 5),
      lane("Open", { position: 2, source_position: 2 }, 2),
    ];
    expect(names(orderLanes(lanes, "mirror"))).toEqual(["Open", "Lost", "Hidden", "Gone"]);
    expect(names(orderLanes(lanes, "native"))).toEqual(["Open", "Lost", "Hidden", "Gone"]);
  });

  it("orders a Native board by position, then the name, with no type group", () => {
    const lanes = [
      lane("Won", { type: "won", position: 10 }),
      lane("Parked", { type: "on_hold", position: 20 }),
      lane("Beta", { position: 30 }),
      lane("Alpha", { position: 30 }),
      lane("Lost", { type: "lost", position: 0 }),
    ];
    for (const mode of ["native", null] as const) {
      expect(names(orderLanes(lanes, mode))).toEqual(["Lost", "Won", "Parked", "Alpha", "Beta"]);
    }
    // A Native board reads no place, so a map changes nothing.
    const places = new Map([["Beta", 0], ["Won", 9]]);
    expect(names(orderLanes(lanes, "native", places))).toEqual(["Lost", "Won", "Parked", "Alpha", "Beta"]);
  });

  it("keeps the store's Native order in toBoardLanes, with no places", () => {
    const lanes = [lane("B", { position: 20 }), lane("A", { position: 10 }), lane("Z", { type: "lost", position: 5 })];
    expect(names(toBoardLanes({ lanes }))).toEqual(["Z", "A", "B"]);
  });
});

// ── A4: the places, and where they come from ──────────────────────────────

describe("the places of the pipeline on screen (A4)", () => {
  it("lets a lane's own pipeline_position beat the map", () => {
    const lanes = fracktalLanes(
      () => ({}),
      (name) => (name === "Closed Lost" ? -1 : null),
    );
    // The map says Closed Lost is last of the pipeline. The lane says first.
    const order = names(orderLanes(lanes, "mirror", placesOf(FRACKTAL_PIPELINES, null)));
    expect(order[0]).toBe("Closed Lost");
    expect(order.slice(1, 12)).toEqual(PIPELINE_ORDER.filter((n) => n !== "Closed Lost"));
  });

  it("orders by the default pipeline with one pipeline and no pipeline_id, as A1 does", () => {
    const lanes = fracktalLanes();
    expect(lanes.every((l) => l.pipeline_position === null)).toBe(true);
    const places = placesOf(FRACKTAL_PIPELINES, null);
    expect(names(orderLanes(lanes, "mirror", places))).toEqual([...PIPELINE_ORDER, ...OUTSIDE]);
    // With no places the board falls back to the picklist, as it did before.
    expect(names(orderLanes(lanes, "mirror"))).toEqual(PICKLIST);
  });

  const two: DealPipeline[] = [
    {
      id: "p2",
      name: "Renewals",
      is_default: false,
      position: 1,
      stages: [
        { id: "b", name: "B", position: 0, type: "open" },
        { id: "a", name: "A", position: 1, type: "open" },
      ],
    },
    {
      id: "p1",
      name: "Sales",
      is_default: true,
      position: 0,
      stages: [
        { id: "a", name: "A", position: 0, type: "open" },
        { id: "c", name: "C", position: 1, type: "open" },
      ],
    },
  ];
  const entries = (map: Map<string, number>) => [...map.entries()];

  it("is empty with no pipeline list", () => {
    expect(placesOf(null, null).size).toBe(0);
    expect(placesOf(undefined, "p1").size).toBe(0);
    expect(placesOf([], null).size).toBe(0);
  });

  it("reads the one pipeline when there is one", () => {
    expect(placesOf(FRACKTAL_PIPELINES, null).get(idOf("Re-Evaluate"))).toBe(0);
    expect(placesOf(FRACKTAL_PIPELINES, null).get(idOf("Closed Lost"))).toBe(11);
    expect(placesOf(FRACKTAL_PIPELINES, null).has(idOf("New Lead"))).toBe(false);
  });

  it("reads the chosen pipeline of two", () => {
    expect(entries(placesOf(two, "p2"))).toEqual([["b", 0], ["a", 1]]);
    expect(entries(placesOf(two, "p1"))).toEqual([["a", 0], ["c", 1]]);
  });

  it("falls to the default pipeline for an id that names none, and to the first with no default", () => {
    expect(entries(placesOf(two, "gone"))).toEqual([["a", 0], ["c", 1]]);
    expect(entries(placesOf(two, null))).toEqual([["a", 0], ["c", 1]]);
    const noDefault = two.map((p) => ({ ...p, is_default: false }));
    expect(entries(placesOf(noDefault, null))).toEqual([["a", 0], ["c", 1]]);
  });

  it("keeps a tie of places, and orderLanes breaks it by source position, then the name", () => {
    const tied: DealPipeline[] = [
      {
        id: "p",
        name: "Tied",
        is_default: true,
        position: 0,
        stages: [
          { id: "y", name: "Y", position: 3, type: "open" },
          { id: "x", name: "X", position: 3, type: "open" },
          { id: "w", name: "W", position: 3, type: "open" },
        ],
      },
    ];
    const places = placesOf(tied, null);
    expect(entries(places)).toEqual([["y", 3], ["x", 3], ["w", 3]]);
    const lane = (id: string, source: number): PipelineLane => ({
      status: { id, name: id.toUpperCase(), color: "gray", position: source, type: "open", is_default: false, source_position: source },
      rows: [],
      count: 0,
      amount: 0,
      weighted: 0,
    });
    expect(names(orderLanes([lane("y", 1), lane("x", 1), lane("w", 0)], "mirror", places))).toEqual(["W", "X", "Y"]);
  });
});

// ── A5: the fences ────────────────────────────────────────────────────────

describe("no type group sits in orderLanes (A5)", () => {
  const source = read("../lib/board.ts");
  const body = source.slice(
    source.indexOf("export function orderLanes"),
    source.indexOf("export function toBoardLanes"),
  );

  it("has no group key in the body of orderLanes", () => {
    expect(body.length).toBeGreaterThan(200);
    expect(body).not.toMatch(/LANE_GROUP|laneGroup|group/i);
  });

  it("exports no laneGroup, and the file holds no group table", () => {
    expect("laneGroup" in board).toBe(false);
    expect(source).not.toContain("LANE_GROUP");
    expect(source).not.toContain("laneGroup");
  });

  it("is the one order of the Deals board, with the places of the pipeline on screen", () => {
    const page = read("../page.tsx");
    expect(page).toContain("orderLanes(store.lanes, ctx.mode, places)");
    expect(page).toContain("placesOf(pipelines, pipelineId)");
  });
});

// ── Commit 2: the Deal stages screen ──────────────────────────────────────

const noop = async () => true;

/** The status rows of `GET /crm/statuses/deal` for Fracktal, with no saved order. */
const FRACKTAL_STATUSES: Status[] = PICKLIST.map((name) =>
  fracktalStatus(name, {
    hidden: name === "Meeting Scheduled",
    source_gone_at: name === "Negotiation / Tendering" ? "2026-10-13T00:00:00Z" : null,
  }),
);

/** The rows of a rendered list, in order: the name each Edit button carries. */
const rowsOf = (html: string) =>
  [...html.matchAll(/aria-label="Edit ([^"]+)"/g)].map((m) => m[1].replace(/&amp;/g, "&"));

function stageList(over: Record<string, unknown>) {
  return renderToStaticMarkup(
    createElement(StageList, {
      kind: "deal",
      mode: "mirror",
      statuses: FRACKTAL_STATUSES,
      saving: false,
      onSave: noop,
      onOrder: noop,
      onResetOrder: noop,
      ...over,
    } as Parameters<typeof StageList>[0]),
  );
}

function settings(over: Record<string, unknown>) {
  return renderToStaticMarkup(
    createElement(PipelineSettings, {
      section: "stages",
      showAdminCards: true,
      readOnly: true,
      dealStatuses: FRACKTAL_STATUSES,
      leadStatuses: [],
      lostReasons: [],
      saving: false,
      onSaveStatus: noop,
      onRemoveStatus: noop,
      onSaveLostReason: noop,
      onRemoveLostReason: noop,
      onReorderLostReasons: async () => undefined,
      ...over,
    } as Parameters<typeof PipelineSettings>[0]),
  );
}

/** Two pipelines. "e" is in none, and "a" is in both. */
const PART_STATUSES: Status[] = ["a", "b", "c", "d", "e"].map((id, source) => ({
  id,
  name: id.toUpperCase(),
  color: "gray",
  position: source,
  type: "open",
  is_default: false,
  probability: 10,
  source_position: source,
  admin_order: false,
}));
const TWO: DealPipeline[] = [
  {
    id: "p1",
    name: "Sales",
    is_default: true,
    position: 0,
    stages: [
      { id: "c", name: "C", position: 0, type: "open" },
      { id: "a", name: "A", position: 1, type: "open" },
    ],
  },
  {
    id: "p2",
    name: "Renewals",
    is_default: false,
    position: 1,
    stages: [
      { id: "b", name: "B", position: 0, type: "open" },
      { id: "a", name: "A", position: 1, type: "open" },
      { id: "d", name: "D", position: 2, type: "open" },
    ],
  },
];

// ── A6: the list and the board read the same function ─────────────────────

describe("the settings list is the board (A6)", () => {
  it("draws the rows in the order of orderLanes, with the places of the one pipeline", () => {
    const lanes = FRACKTAL_STATUSES.map((status) => ({ status, pipeline_position: null }));
    const board = names(orderLanes(lanes, "mirror", placesOf(FRACKTAL_PIPELINES, null)));
    const rows = rowsOf(settings({ pipelines: FRACKTAL_PIPELINES }));
    expect(rows).toEqual(board);
    // The pipeline first, then the others. The hidden and the gone stage last.
    expect(rows.slice(0, 12)).toEqual(PIPELINE_ORDER);
    expect(rows.slice(-2)).toEqual(["Meeting Scheduled", "Negotiation / Tendering"]);
  });

  it("draws the part of each picked pipeline in the order of orderLanes for it", () => {
    const cases: [string, string[]][] = [
      ["p1", ["c", "a", "e"]],
      ["p2", ["b", "a", "d", "e"]],
    ];
    for (const [pipelineId, part] of cases) {
      const lanes = PART_STATUSES.filter((s) => part.includes(s.id)).map((status) => ({
        status,
        pipeline_position: null,
      }));
      const expected = names(orderLanes(lanes, "mirror", placesOf(TWO, pipelineId)));
      const html = stageList({ statuses: PART_STATUSES, pipelines: TWO, pipelineId });
      expect(rowsOf(html), pipelineId).toEqual(expected);
      expect(rowsOf(html), pipelineId).toEqual(part.map((id) => id.toUpperCase()));
    }
  });

  it("calls orderLanes and placesOf in the list, as the page does for the board", () => {
    const source = read("./PipelineSettings.tsx");
    const list = source.slice(source.indexOf("export function StageList"), source.indexOf("function StageRow"));
    expect(list).toContain("orderLanes(statuses.map(laneOf), mode, places)");
    expect(list).toContain("placesOf(pipelines,");
    expect(list).toContain("orderAfterMove(fullIds,");
    expect(list).toContain("onOrder(kind, ids)");
  });
});

// ── A7: a part order merged into the full order ────────────────────────────

describe("mergePartOrder (A7)", () => {
  const full = ["a", "b", "c", "d", "e"];

  it("moves inside the part, and the other ids keep their slots", () => {
    expect(mergePartOrder(full, ["d", "b"])).toEqual(["a", "d", "c", "b", "e"]);
  });

  it("takes a part at the start", () => {
    expect(mergePartOrder(full, ["b", "a"])).toEqual(["b", "a", "c", "d", "e"]);
  });

  it("takes a part at the end", () => {
    expect(mergePartOrder(full, ["e", "d"])).toEqual(["a", "b", "c", "e", "d"]);
  });

  it("takes a part with gaps", () => {
    expect(mergePartOrder(["a", "x", "b", "y", "c"], ["c", "a", "b"])).toEqual(["c", "x", "a", "y", "b"]);
  });

  it("leaves the order alone for a one-item part and an empty part", () => {
    expect(mergePartOrder(full, ["c"])).toEqual(full);
    expect(mergePartOrder(full, [])).toEqual(full);
  });

  it("keeps every id once, and drops a repeated or an unknown id of the part", () => {
    const merged = mergePartOrder(full, ["e", "zz", "a", "e", "c"]);
    expect(merged).toEqual(["e", "b", "a", "d", "c"]);
    expect([...merged].sort()).toEqual([...full].sort());
    expect(new Set(merged).size).toBe(full.length);
  });

  it("gives the full order after one move of the list on screen, or null", () => {
    expect(orderAfterMove(full, ["b", "d", "e"], 2, 0)).toEqual(["a", "e", "c", "b", "d"]);
    expect(orderAfterMove(full, full, 0, 0)).toBeNull();
    expect(orderAfterMove(full, full, 4, 3)).toEqual(["a", "b", "c", "e", "d"]);
  });
});

// ── A8: what the screen draws ──────────────────────────────────────────────

describe("the Deal stages screen (A8)", () => {
  it("draws Column order, the one line, and one plain line for each row in Mirror mode", () => {
    const html = settings({ pipelines: FRACKTAL_PIPELINES });
    expect(html).toContain(">Deal stages</h2>");
    expect(html).toContain(
      "These are the columns of your Deals board, left to right. Drag a stage, or use its arrows, to move its column.",
    );
    expect(html).toContain("Column order");
    expect(html).toContain(">Same as Zoho<");
    expect(html).toContain(">Custom<");
    expect(html).toContain(
      "Zoho owns the stage names. Your order, colours and win chances stay in Metorite. Nothing here changes Zoho.",
    );
    for (const gone of ["<input", ">Save<", ">Cancel<", "Reset to Zoho", ">Rules<", 'aria-label="Board order"']) {
      expect(html, gone).not.toContain(gone);
    }
    // The read-only "Pipelines" card is gone from the admin screen.
    expect(html).not.toContain(">Pipelines</h2>");
    const rows = rowsOf(html).length;
    expect(rows).toBe(17);
    expect(html.match(/>Edit</g)?.length).toBe(rows);
    expect(html.match(/aria-label="Move up"/g)?.length).toBe(rows);
    expect(html.match(/aria-label="Move down"/g)?.length).toBe(rows);
    expect(html.match(/draggable="true"/g)?.length).toBe(rows);
  });

  it("shows Custom while a saved order exists, and Same as Zoho with none", () => {
    const tab = (html: string, label: string) =>
      html.split("<button").find((part) => part.split("</button>")[0].endsWith(`>${label}`)) ?? "";
    const savedOrder = FRACKTAL_STATUSES.map((s) => ({ ...s, admin_order: true, ui_position: s.position * 10 }));
    expect(tab(settings({ dealStatuses: savedOrder }), "Custom")).toContain('aria-selected="true"');
    expect(tab(settings({}), "Same as Zoho")).toContain('aria-selected="true"');
    expect(tab(settings({}), "Custom")).toContain('aria-selected="false"');
  });

  it("tags a won, a lost, an on-hold, a hidden, a gone and a not-in-pipeline stage", () => {
    const statuses: Status[] = [
      fracktalStatus("Closed Won"),
      fracktalStatus("Closed Lost"),
      fracktalStatus("Meeting Scheduled"), // on hold in this fixture
      fracktalStatus("New Deal", { hidden: true }),
      fracktalStatus("Negotiation / Tendering", { source_gone_at: "2026-10-13T00:00:00Z" }),
      fracktalStatus("New Lead"),
    ];
    const html = stageList({ statuses, pipelines: FRACKTAL_PIPELINES });
    for (const tag of ["Won", "Lost", "On hold", "Hidden", "Removed in Zoho"]) {
      expect(html, tag).toContain(`>${tag}</span>`);
    }
    // Three stages are outside the pipeline: Meeting Scheduled, Negotiation /
    // Tendering and New Lead.
    expect(html.match(/>Not in the Zoho pipeline</g)?.length).toBe(3);
    // No pipeline list: no "Not in the Zoho pipeline" tag at all.
    expect(stageList({ statuses, pipelines: null })).not.toContain("Not in the Zoho pipeline");
    // The win chance is text: 100% for a won stage, 0% for a lost one.
    expect(html).toContain(">100%<");
    expect(html).toContain(">0%<");
  });

  it("draws Add stage and no Column order in Native mode, with no pipeline list", () => {
    const html = settings({ readOnly: false, pipelines: undefined });
    expect(html).toContain(">Add stage<");
    expect(html).not.toContain("Column order");
    expect(html).not.toContain('aria-label="Pipeline');
    expect(rowsOf(html)).toHaveLength(17);
    // Native mode orders by position, with no type group.
    expect(rowsOf(html).slice(0, 3)).toEqual(PICKLIST.slice(0, 3));
  });

  it("renders with no pipelines prop, and draws no picker for one pipeline", () => {
    expect(rowsOf(settings({}))).toHaveLength(17);
    expect(settings({})).not.toContain('aria-label="Pipeline');
    expect(settings({ pipelines: FRACKTAL_PIPELINES })).not.toContain('aria-label="Pipeline');
  });

  it("draws the picker in Mirror mode with two pipelines, and Native keeps its read-only note", () => {
    const mirror = settings({ dealStatuses: PART_STATUSES, pipelines: TWO });
    // The prefix joins the accessible name: "Pipeline: Sales", the default.
    expect(mirror).toContain('aria-label="Pipeline: Sales"');
    const native = settings({ readOnly: false, dealStatuses: PART_STATUSES, pipelines: TWO });
    expect(native).toContain("not available yet");
    expect(native).not.toContain(">Add stage<");
  });

  it("uses the same list for the lead statuses, with no picker", () => {
    const leads = [fracktalStatus("New Lead"), fracktalStatus("Closed Won")];
    const mirror = settings({ section: "lead-statuses", leadStatuses: leads, pipelines: TWO });
    expect(mirror).toContain(">Lead statuses</h2>");
    expect(mirror).toContain("Column order");
    expect(mirror).not.toContain('aria-label="Pipeline');
    expect(mirror).not.toContain("Not in the Zoho pipeline");
    const native = settings({ section: "lead-statuses", readOnly: false, leadStatuses: leads });
    expect(native).toContain(">Add status<");
  });
});

// ── A9: the requests of a move, of "Same as Zoho" and of "Add stage" ──────

type Answer = { status: number; body: unknown };

/** A fake `fetch` that records each request and answers by method and path. */
function fakeFetch(answer: (method: string, url: string) => Answer) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const url = String(input);
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const { status, body } = answer(method, url);
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  }) as typeof globalThis.fetch;
  return {
    writes: () => calls.filter((c) => c.method !== "GET"),
    restore: () => {
      globalThis.fetch = original;
    },
  };
}

const SEED: Status[] = ["New", "Qualified", "Proposal", "Negotiation", "Won", "Lost"].map((name, i) => ({
  id: `n${i}`,
  name,
  color: "gray",
  position: i * 10,
  type: name === "Won" ? "won" : name === "Lost" ? "lost" : "open",
  is_default: i === 0,
  probability: name === "Won" ? 100 : 0,
}));

describe("the requests of the screen (A9)", () => {
  afterEach(() => {
    useCrmStore.setState({ dealStatuses: [], leadStatuses: [], error: null, saving: false });
  });

  it("saves a move with every id of the kind, in one PUT", async () => {
    const fake = fakeFetch(() => ({ status: 200, body: [] }));
    try {
      const shown = rowsOf(settings({ pipelines: FRACKTAL_PIPELINES })).map(idOf);
      const ids = orderAfterMove(shown, shown, 3, 0)!;
      expect(await useCrmStore.getState().orderStatuses("deal", ids)).toBe(true);
      // With two pipelines the list is a part, and the merge still sends all.
      const merged = orderAfterMove(["a", "b", "c", "d", "e"], ["c", "a", "e"], 2, 0)!;
      expect(await useCrmStore.getState().orderStatuses("deal", merged)).toBe(true);
      const writes = fake.writes();
      expect(writes.map((w) => [w.method, w.url])).toEqual([
        ["PUT", "/api/crm/statuses/deal/order"],
        ["PUT", "/api/crm/statuses/deal/order"],
      ]);
      const sent = (writes[0].body as { ids: string[] }).ids;
      expect(sent).toHaveLength(17);
      expect([...sent].sort()).toEqual(FRACKTAL_STATUSES.map((s) => s.id).sort());
      expect(sent[0]).toBe(idOf("Discovery Meeting"));
      // The part [c, a, e] became [e, c, a], in the slots of a, c and e.
      expect((writes[1].body as { ids: string[] }).ids).toEqual(["e", "b", "c", "d", "a"]);
    } finally {
      fake.restore();
    }
  });

  it("calls the reset route for Same as Zoho", async () => {
    const fake = fakeFetch(() => ({ status: 200, body: [] }));
    try {
      expect(await useCrmStore.getState().resetStatusOrder("deal")).toBe(true);
      expect(fake.writes().map((w) => [w.method, w.url, w.body])).toEqual([
        ["POST", "/api/crm/statuses/deal/order/reset", null],
      ]);
    } finally {
      fake.restore();
    }
  });

  it("adds a Native stage with one create, then one order that puts it before Won", async () => {
    useCrmStore.setState({ dealStatuses: SEED });
    const fake = fakeFetch((method) =>
      method === "POST"
        ? { status: 201, body: { ...SEED[0], id: "new1", name: "Demo", position: 60, is_default: false } }
        : { status: 200, body: [] },
    );
    try {
      const id = await useCrmStore.getState().addStageBeforeClosed("deal", { name: "Demo", type: "open" });
      expect(id).toBe("new1");
      expect(fake.writes()).toEqual([
        { method: "POST", url: "/api/crm/statuses/deal", body: { name: "Demo", type: "open" } },
        {
          method: "PUT",
          url: "/api/crm/statuses/deal/order",
          body: { ids: ["n0", "n1", "n2", "n3", "new1", "n4", "n5"] },
        },
      ]);
      expect(useCrmStore.getState().error).toBeNull();
    } finally {
      fake.restore();
    }
  });

  it("keeps a new stage at the end and says so when the order fails, and returns null when the create fails", async () => {
    useCrmStore.setState({ dealStatuses: SEED });
    let fake = fakeFetch((method) =>
      method === "POST"
        ? { status: 201, body: { ...SEED[0], id: "new1", name: "Demo" } }
        : method === "PUT"
          ? { status: 422, body: { detail: "Send every deal status once." } }
          : { status: 200, body: [] },
    );
    try {
      expect(await useCrmStore.getState().addStageBeforeClosed("deal", { name: "Demo" })).toBe("new1");
      expect(useCrmStore.getState().error).toContain('"Demo" was added at the end');
      expect(useCrmStore.getState().error).toContain("Send every deal status once.");
    } finally {
      fake.restore();
    }
    useCrmStore.setState({ dealStatuses: SEED, error: null });
    fake = fakeFetch((method) =>
      method === "POST" ? { status: 409, body: { detail: "A stage with this name exists." } } : { status: 200, body: [] },
    );
    try {
      expect(await useCrmStore.getState().addStageBeforeClosed("deal", { name: "New" })).toBeNull();
      expect(fake.writes().map((w) => w.method)).toEqual(["POST"]);
      expect(useCrmStore.getState().error).toBe("A stage with this name exists.");
    } finally {
      fake.restore();
    }
  });

  it("returns false and sets the error when the order call fails", async () => {
    const fake = fakeFetch((method) =>
      method === "PUT" ? { status: 422, body: { detail: "Send every deal status once." } } : { status: 200, body: [] },
    );
    try {
      expect(await useCrmStore.getState().orderStatuses("deal", ["a"])).toBe(false);
      expect(useCrmStore.getState().error).toBe("Send every deal status once.");
    } finally {
      fake.restore();
    }
  });

  it("puts a new stage before the first won or lost stage, or nowhere new with none", () => {
    expect(orderWithNewStage(SEED, "x")).toEqual(["n0", "n1", "n2", "n3", "x", "n4", "n5"]);
    const lostFirst = SEED.map((s) => (s.id === "n5" ? { ...s, position: -10 } : s));
    expect(orderWithNewStage(lostFirst, "x")?.slice(0, 2)).toEqual(["x", "n5"]);
    expect(orderWithNewStage(SEED.slice(0, 4), "x")).toBeNull();
  });
});

// ── A10: the Edit dialog ───────────────────────────────────────────────────

describe("the Edit dialog (A10)", () => {
  const native = (status: Status | null, kind: "deal" | "lead" = "deal") =>
    renderToStaticMarkup(
      createElement(NativeStageForm, {
        kind,
        status,
        saving: false,
        onSave: noop,
        onAdd: noop,
        onRemove: noop,
        onCancel: () => undefined,
      }),
    );
  const mirror = (status: Status, kind: "deal" | "lead" = "deal") =>
    renderToStaticMarkup(
      createElement(MirrorStageForm, { kind, status, saving: false, onSave: noop, onCancel: () => undefined }),
    );
  const open = SEED[1];

  it("holds the required fields and the warning days for a deal stage", () => {
    const html = native({ ...open, required_fields: ["amount"], max_dwell_days: 9 });
    expect(html).toContain("Fields a deal needs to enter this stage");
    for (const label of ["Amount", "Expected close date", "Company", "Owner"]) {
      expect(html, label).toContain(`>${label}</label>`);
    }
    expect(html).toMatch(/checked=""\/>Amount</);
    expect(html).toContain("Warn when a deal stays more than");
    expect(html).toContain('value="9"');
    expect(html).toContain("Show this column on the board");
    expect(html).toContain(">Save<");
    expect(html).toContain(">Cancel<");
    expect(html).toContain("Delete stage");
  });

  it("holds neither for a lead status", () => {
    for (const html of [native(open, "lead"), mirror(fracktalStatus("New Lead"), "lead")]) {
      expect(html).not.toContain("Fields a deal needs to enter this stage");
      expect(html).not.toContain("Warn when a deal stays more than");
      expect(html).toContain("Win chance");
    }
  });

  it("holds the warning days for a Mirror deal stage, and the Zoho value with its action", () => {
    const html = mirror(fracktalStatus("New Deal", { source_probability: 40 }));
    expect(html).toContain("Warn when a deal stays more than");
    expect(html).toContain("Zoho: 40%");
    expect(html).toContain("Use Zoho&#x27;s value");
    // Zoho owns the name and the type: no name box and no type picker.
    expect(html).not.toContain('aria-label="Type"');
    expect(html).not.toContain(">Name</label>");
    // The gateway takes no required fields in Mirror mode (MIRROR_OVERLAY_COLUMNS).
    expect(html).not.toContain("Fields a deal needs to enter this stage");
  });

  it("fixes the win chance at 100 for a won stage and at 0 for a lost one, with the reason", () => {
    const won = SEED[4];
    const lost = SEED[5];
    for (const html of [native(won), mirror({ ...won, source_probability: 100 })]) {
      expect(html).toMatch(/disabled=""[^>]*value="100"|value="100"[^>]*disabled=""/);
      expect(html).toContain("A won stage always forecasts 100%");
    }
    for (const html of [native(lost), mirror({ ...lost, source_probability: 0 })]) {
      expect(html).toMatch(/disabled=""[^>]*value="0"|value="0"[^>]*disabled=""/);
      expect(html).toContain("A lost stage always forecasts 0%");
    }
  });

  it("opens empty for Add stage, with no Delete", () => {
    const html = native(null);
    expect(html).toContain('value=""');
    expect(html).toContain(">Add stage<");
    expect(html).not.toContain("Delete stage");
  });
});
