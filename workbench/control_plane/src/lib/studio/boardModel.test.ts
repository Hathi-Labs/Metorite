/**
 * STU-D3 fence: the pure model of the Board block.
 */
import { describe, expect, it } from "vitest";

import {
  DEFAULT_MAX_CARDS, MAX_CARD_FIELDS, buildLanes, keyStep, optionOf, parseBoard, recordsOf, type BoardConfig, type Hold,
} from "./boardModel";
import type { FieldSpec } from "./fieldCheck";

const FIELDS: FieldSpec[] = [
  { key: "title", type: "text", label: "Title", required: true },
  { key: "status", type: "select", label: "Status", options: ["Planned", "On site", "Closed"] },
  { key: "owner", type: "person" },
  { key: "qty", type: "number" },
  { key: "tags", type: "multiSelect", options: ["A"] },
  { key: "notes", type: "longText" },
];
const config = (extra: Record<string, unknown> = {}): BoardConfig => {
  const p = parseBoard({ fields: FIELDS, groupBy: "status", ...extra });
  if (!p.ok) throw new Error(p.reason);
  return p.config;
};
const row = (id: string, status?: unknown) => ({ id, title: "T" + id, status });

describe("parseBoard", () => {
  it("groups by a select field", () => {
    const c = config({ update: "object:visit.update", cardFields: ["owner", "qty"] });
    expect(c.group.key).toBe("status");
    expect(c.update).toBe("object:visit.update");
    expect(c.titleField?.key).toBe("title");
    expect(c.cardFields.map((f) => f.key)).toEqual(["owner", "qty"]);
    expect(c.maxCards).toBe(DEFAULT_MAX_CARDS);
  });
  it("refuses a groupBy that is not a select field", () => {
    for (const groupBy of ["title", "tags", "qty", "nope", undefined]) {
      const p = parseBoard({ fields: FIELDS, groupBy });
      expect(p.ok, String(groupBy)).toBe(false);
    }
  });
  it("keeps at most 3 card fields, and never the group or title field", () => {
    const c = config({ cardFields: ["owner", "qty", "tags", "notes", "status", "title"] });
    expect(c.cardFields).toHaveLength(MAX_CARD_FIELDS);
    expect(c.cardFields.map((f) => f.key)).toEqual(["owner", "qty", "tags"]);
  });
  it("reads maxCards when it is a whole number above 0", () => {
    expect(config({ maxCards: 5 }).maxCards).toBe(5);
    for (const bad of [0, -1, 2.5, "9"]) expect(config({ maxCards: bad }).maxCards).toBe(DEFAULT_MAX_CARDS);
  });
  it("a board with no update action is read-only", () => {
    expect(config().update).toBeNull();
  });
});

describe("buildLanes", () => {
  it("makes one lane for each option, in the option order", () => {
    const lanes = buildLanes(config(), [row("a", "Closed"), row("b", "Planned")]);
    expect(lanes.map((l) => l.title)).toEqual(["Planned", "On site", "Closed"]);
    expect(lanes.map((l) => l.total)).toEqual([1, 0, 1]);
  });
  it("keeps the option order when the data comes in another order", () => {
    const c = config();
    c.group.options = ["Z", "A", "M"];
    const lanes = buildLanes(c, [row("1", "A"), row("2", "M"), row("3", "Z")]);
    expect(lanes.map((l) => l.title)).toEqual(["Z", "A", "M"]);
  });
  it("adds the No value lane last, and only when a record needs it", () => {
    expect(buildLanes(config(), [row("a", "Planned")])).toHaveLength(3);
    const lanes = buildLanes(config(), [row("a"), row("b", "Gone"), row("c", null), row("d", "Planned")]);
    expect(lanes.map((l) => l.title)).toEqual(["Planned", "On site", "Closed", "No value"]);
    expect(lanes[3].total).toBe(3);
    expect(lanes[3].value).toBeNull();
  });
  it("shows 50 cards of 60, then Show more (10), and the true count", () => {
    const rows = Array.from({ length: 60 }, (_, i) => row("r" + i, "Planned"));
    const [lane] = buildLanes(config(), rows);
    expect(lane.rows).toHaveLength(50);
    expect(lane.hidden).toBe(10);
    expect(lane.total).toBe(60);
    const [open] = buildLanes(config(), rows, { expanded: new Set([lane.id]) });
    expect(open.rows).toHaveLength(60);
    expect(open.hidden).toBe(0);
  });
  it("honours a smaller maxCards", () => {
    const rows = Array.from({ length: 5 }, (_, i) => row("r" + i, "Planned"));
    const [lane] = buildLanes(config({ maxCards: 2 }), rows);
    expect([lane.rows.length, lane.hidden, lane.total]).toEqual([2, 3, 5]);
  });
  it("shows a moved card in its new lane, at the end", () => {
    const c = config();
    const rows = [row("a", "Planned"), row("b", "Planned"), row("c", "Closed")];
    const lanes = buildLanes(c, rows, { placed: { a: "Closed" } });
    expect(lanes[0].rows.map((r) => r.id)).toEqual(["b"]);
    expect(lanes[2].rows.map((r) => r.id)).toEqual(["c", "a"]);
    expect(optionOf(c, rows[0], { a: "Closed" })).toBe("Closed");
    expect(optionOf(c, rows[0], { a: null })).toBeNull();
  });
});

describe("keyStep", () => {
  const ids = ["a", "b", "c"];
  const card = { id: "r1", lane: "a" };
  it("Space picks up, the arrows choose a lane without wrap, Space drops", () => {
    const up = keyStep(null, " ", card, ids);
    expect(up.hold).toEqual({ id: "r1", from: "a", over: "a" });
    const left = keyStep(up.hold, "ArrowLeft", card, ids);
    expect(left.hold?.over).toBe("a");
    const right = keyStep(left.hold, "ArrowRight", card, ids);
    expect(right.hold?.over).toBe("b");
    const drop = keyStep(right.hold, " ", card, ids);
    expect(drop.drop).toEqual({ id: "r1", to: "b" });
    expect(drop.hold).toBeNull();
  });
  it("Escape cancels and drops nothing", () => {
    const hold: Hold = { id: "r1", from: "a", over: "c" };
    const out = keyStep(hold, "Escape", card, ids);
    expect(out.cancelled).toBe(true);
    expect(out.drop).toBeUndefined();
    expect(out.hold).toBeNull();
  });
  it("leaves the other keys to the browser", () => {
    expect(keyStep(null, "ArrowRight", card, ids).handled).toBe(false);
    expect(keyStep({ id: "r1", from: "a", over: "a" }, "x", card, ids).handled).toBe(false);
  });
});

describe("unique lanes", () => {
  it("a repeated option gives one lane, with its cards once", () => {
    const c = config();
    const dup: BoardConfig = { ...c, group: { ...c.group, options: ["Planned", "Planned", "Closed"] } };
    const lanes = buildLanes(dup, [row("a", "Planned")]);
    expect(lanes.map((l) => l.id)).toEqual([...new Set(lanes.map((l) => l.id))]);
    expect(lanes.map((l) => l.value)).toEqual(["Planned", "Closed"]);
    expect(lanes[0].rows).toHaveLength(1);
  });
});

describe("recordsOf", () => {
  it("drops a null and a non-object, in order", () => {
    const a = { id: "a" };
    expect(recordsOf([null, a, undefined, 3, "x"])).toEqual([a]);
  });
});
