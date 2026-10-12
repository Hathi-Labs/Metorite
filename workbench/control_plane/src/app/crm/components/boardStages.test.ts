// The Deals board stages (owner report, 2026-10-12): the column order, the
// empty columns and their toggle, and the header totals.
//
// The runner has no DOM. The pure rules are called directly, the board is
// rendered to a string, and the page wiring is read as source.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { boardTotals, orderLanes, toBoardLanes, type BoardLane } from "../lib/board";
import {
  defaultShowEmpty,
  emptyStagesKey,
  readShowEmpty,
  showEmptyStages,
  visibleLanes,
  writeShowEmpty,
} from "../lib/emptyStages";
import type { PipelineLane, Status, StatusType } from "../lib/types";
import KanbanBoard from "./KanbanBoard";

let next = 0;

function lane(
  name: string,
  type: StatusType,
  probability: number,
  pipelinePosition: number | null,
  count = 0,
  position = 50
): PipelineLane {
  next += 1;
  const status: Status = {
    id: `s${next}`,
    name,
    color: "",
    position,
    type,
    is_default: false,
    probability,
  };
  return {
    status,
    rows: [],
    count,
    amount: count * 1000,
    weighted: type === "open" ? count * 10 : 0,
    pipeline_position: pipelinePosition,
  };
}

// The Fracktal shape, in a scrambled payload order: twelve pipeline stages,
// and four stages that left the pipeline but still hold deals.
const FRACKTAL: PipelineLane[] = [
  lane("Closed Lost", "lost", 0, 11, 40, 16),
  lane("Payment Complete", "won", 100, 9, 139, 14),
  lane("Negotiation / Tendering", "open", 75, null, 3, 9),
  lane("New Deal", "open", 10, 0, 0, 1),
  lane("Re-Evaluate", "lost", 0, 10, 40, 15),
  lane("Discovery Meeting", "open", 20, 1, 0, 2),
  lane("Delivered & Installed", "open", 90, null, 12, 12),
  lane("New Lead", "open", 10, null, 4, 0),
  lane("Qualified", "open", 20, 2, 7, 3),
  lane("Sample parts", "open", 40, 3, 0, 4),
  lane("Proposal", "open", 50, 4, 9, 5),
  lane("Negotiation/Review", "open", 75, 5, 0, 6),
  lane("Confirmation and Advance", "open", 90, 6, 0, 7),
  lane("Discovery Meeting Done", "open", 20, null, 4, 8),
  lane("Closed Won", "won", 100, 8, 0, 13),
  lane("Order Received", "open", 95, 7, 2, 10),
];

const names = (lanes: readonly { status: Status }[]) => lanes.map((l) => l.status.name);

describe("orderLanes — the one column order of the Deals board", () => {
  it("puts open stages by probability, then won, then lost at the far end", () => {
    expect(names(orderLanes(FRACKTAL))).toEqual([
      // Open, by probability. A tie goes to the pipeline place, and a stage
      // with no place comes after the listed ones.
      "New Deal",
      "New Lead",
      "Discovery Meeting",
      "Qualified",
      "Discovery Meeting Done",
      "Sample parts",
      "Proposal",
      "Negotiation/Review",
      "Negotiation / Tendering",
      "Confirmation and Advance",
      "Delivered & Installed",
      "Order Received",
      // Won, by place.
      "Closed Won",
      "Payment Complete",
      // Lost, by place. Closed Lost and Re-Evaluate close the board.
      "Re-Evaluate",
      "Closed Lost",
    ]);
  });

  it("breaks a tie with no pipeline place by the global position", () => {
    const lanes = [
      lane("Later", "open", 30, null, 0, 9),
      lane("Earlier", "open", 30, null, 0, 2),
      lane("Lost late", "lost", 0, null, 0, 8),
      lane("Lost early", "lost", 0, null, 0, 1),
    ];
    expect(names(orderLanes(lanes))).toEqual(["Earlier", "Later", "Lost early", "Lost late"]);
  });

  it("orders won stages by place and not by probability", () => {
    const lanes = [lane("Won B", "won", 100, 2), lane("Won A", "won", 50, 3)];
    expect(names(orderLanes(lanes))).toEqual(["Won B", "Won A"]);
  });

  it("is the order toBoardLanes draws, and it leaves the input alone", () => {
    const before = names(FRACKTAL);
    expect(names(toBoardLanes({ lanes: FRACKTAL }))).toEqual(names(orderLanes(FRACKTAL)));
    expect(names(FRACKTAL)).toEqual(before);
  });
});

describe("empty stages — hidden in Mirror mode, shown in Native mode", () => {
  const board = toBoardLanes({ lanes: FRACKTAL });

  it("hides every stage that holds no deal under the filters", () => {
    const shown = visibleLanes(board, false);
    expect(shown.every((l) => l.count > 0)).toBe(true);
    expect(names(shown)).not.toContain("Closed Won");
    expect(names(shown)).not.toContain("New Deal");
    expect(shown).toHaveLength(10);
    // The order survives the filter.
    expect(names(shown)).toEqual(names(board).filter((n) => shown.some((l) => l.status.name === n)));
  });

  it("no deal disappears when stages are hidden", () => {
    const all = board.reduce((n, l) => n + l.count, 0);
    expect(visibleLanes(board, false).reduce((n, l) => n + l.count, 0)).toBe(all);
  });

  it("shows every stage when the toggle is on", () => {
    expect(visibleLanes(board, true)).toHaveLength(FRACKTAL.length);
  });

  it("defaults by mode: hide in Mirror, show in Native and before a mode is known", () => {
    expect(defaultShowEmpty("mirror")).toBe(false);
    expect(defaultShowEmpty("native")).toBe(true);
    expect(defaultShowEmpty(null)).toBe(true);
    expect(showEmptyStages("mirror", null)).toBe(false);
    expect(showEmptyStages("native", null)).toBe(true);
    // The member's choice wins over the mode, in both directions.
    expect(showEmptyStages("mirror", true)).toBe(true);
    expect(showEmptyStages("native", false)).toBe(false);
  });
});

describe("the toggle's memory — per member, in local storage, never throws", () => {
  function memory() {
    const data = new Map<string, string>();
    return {
      data,
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    };
  }

  it("keeps one choice for each member and organization", () => {
    const store = memory();
    writeShowEmpty("ann@x.in|org-1", true, store);
    writeShowEmpty("bob@x.in|org-1", false, store);
    expect(readShowEmpty("ann@x.in|org-1", store)).toBe(true);
    expect(readShowEmpty("bob@x.in|org-1", store)).toBe(false);
    expect(readShowEmpty("ann@x.in|org-2", store)).toBeNull();
    expect(emptyStagesKey("ann@x.in|org-1")).toBe("metorite:crm:empty-stages:ann@x.in|org-1");
  });

  it("reads and writes nothing with no member to name", () => {
    const store = memory();
    writeShowEmpty(null, true, store);
    writeShowEmpty("", true, store);
    expect(store.data.size).toBe(0);
    expect(readShowEmpty(null, store)).toBeNull();
  });

  it("survives a store that throws, and ignores a value it did not write", () => {
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(() => writeShowEmpty("ann@x.in|org-1", true, broken)).not.toThrow();
    expect(readShowEmpty("ann@x.in|org-1", broken)).toBeNull();
    const odd = memory();
    odd.data.set("metorite:crm:empty-stages:ann@x.in|org-1", "maybe");
    expect(readShowEmpty("ann@x.in|org-1", odd)).toBeNull();
  });
});

describe("the board header counts open deals only", () => {
  it("leaves the won and the lost lanes out of the count and the amount", () => {
    const totals = boardTotals(toBoardLanes({ lanes: FRACKTAL }));
    const open = FRACKTAL.filter((l) => l.status.type === "open");
    expect(totals.count).toBe(open.reduce((n, l) => n + l.count, 0));
    expect(totals.amount).toBe(open.reduce((n, l) => n + l.amount, 0));
    expect(totals.count).toBe(41);
  });
});

describe("the board draws what the page hands it", () => {
  const props = { loading: false, onMove: () => {}, onOpen: () => {} };

  it("says that empty stages are hidden when every stage is empty", () => {
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, { ...props, lanes: [] as BoardLane[], hiddenEmpty: 3 })
    );
    expect(html).toContain("No deals match this view.");
    expect(html).toContain("3 empty stages are hidden");
    expect(html).not.toContain("No deal stages are configured yet.");
  });

  it("still says no stage exists when nothing was hidden", () => {
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, { ...props, lanes: [] as BoardLane[] })
    );
    expect(html).toContain("No deal stages are configured yet.");
  });
});

describe("the page wires the toggle through the shared Button", () => {
  const page = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");

  it("draws the toggle in the title-bar tools with Button and its selected state", () => {
    const tools = page.slice(page.indexOf("const tools = ("), page.indexOf("return (", page.indexOf("const tools = (")));
    expect(tools).toMatch(/<Button[^>]*?\n?[\s\S]*?aria-label="Show empty stages"/);
    expect(tools).toContain("selected={showEmpty}");
    expect(tools).toContain("writeShowEmpty(memberScope, next)");
  });

  it("hands the Deals board the visible lanes, and the Leads board its own", () => {
    expect(page).toContain("const dealLanes = visibleLanes(store.lanes, showEmpty)");
    expect(page).toContain('lanes={view.tab === "leads" ? store.leadLanes : dealLanes}');
  });
});
