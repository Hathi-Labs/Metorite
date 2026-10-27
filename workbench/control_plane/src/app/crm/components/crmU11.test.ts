// CRM-U11: the stage settings are the board (D130, crm_platform.md §20.15).
//
// The runner has no DOM. The order rule is called directly, and a wiring rule
// is a scan of the source.
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import * as board from "../lib/board";
import { orderLanes, toBoardLanes } from "../lib/board";
import { placesOf } from "../lib/pipelines";
import type { DealPipeline, PipelineLane, Status, StatusType } from "../lib/types";

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
