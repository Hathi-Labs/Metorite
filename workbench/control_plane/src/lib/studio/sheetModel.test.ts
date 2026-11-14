/**
 * STU-D17a: the model of the sheet of line items. The buffer, new and changed rows, the body of the
 * one save, the refusal mapped to the BODY order, the strip of the parent-rule prefix, the viewer
 * and the cap. A fake door counts the calls: one save is ONE `door.run`.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { clearAll } from "@/lib/dataCache";
import type { ActionResult, StudioDataDoor } from "./dataDoor";
import type { FieldSpec } from "./fieldCheck";
import {
  EMPTY_BUFFER, MAX_SHEET_LINES, type SheetBuffer, addRow, buildSave, fromStored, isDirty, parentRuleMessage,
  readSheetRefusal, rollupTargets, rowState, saveCount, saveSheet, setCell, sheetColumns, syncStored, valueOf,
} from "./sheetModel";

const specs: FieldSpec[] = [
  { key: "name", type: "text", label: "Name" },
  { key: "pct", type: "number", label: "Pct" },
  { key: "cost", type: "money" },
  { key: "formulation", type: "relation", to: "formulation" },
  { key: "share", type: "number" },
];
const f = (key: string) => specs.find((s) => s.key === key) as FieldSpec;
const stored = [
  { id: "a1", name: "Water", pct: 60, share: 1 },
  { id: "a2", name: "Oil", pct: 40, share: 2 },
];

function fakeDoor(answer: ActionResult | ((a: string, args?: Record<string, unknown>) => ActionResult)) {
  const calls: { action: string; args?: Record<string, unknown> }[] = [];
  const door: StudioDataDoor = {
    scope: "pkg:live",
    read: async () => [],
    run: async (action, args) => {
      calls.push({ action, args });
      return typeof answer === "function" ? answer(action, args) : answer;
    },
  };
  return { door, calls };
}

beforeEach(() => clearAll());

describe("the columns", () => {
  it("leaves out the back-link, edits scalar types, and shows a money cell read only", () => {
    const cols = sheetColumns(specs, "formulation", new Set(["share"]), true);
    expect(cols.map((c) => c.field.key)).toEqual(["name", "pct", "cost", "share"]);
    expect(cols.map((c) => c.editable)).toEqual([true, true, false, false]);
  });

  it("gives a viewer no editable column", () => {
    expect(sheetColumns(specs, "formulation", new Set(), false).some((c) => c.editable)).toBe(false);
  });

  it("finds the roll-up targets of the lines child", () => {
    const rules = [
      { kind: "rollup", target: "total", source: "ingredient", via: "formulation", agg: "sum", field: "pct" },
      { kind: "rollup", target: "other", source: "ingredient", via: "elsewhere", agg: "sum", field: "pct" },
      { kind: "range", field: "total", min: 100, max: 100 },
    ];
    expect(rollupTargets(rules, "ingredient", "formulation")).toEqual(["total"]);
    expect(rollupTargets(undefined, "ingredient", "formulation")).toEqual([]);
  });
});

describe("the buffer", () => {
  it("holds the stored rows and no edit, so nothing is dirty", () => {
    const b = fromStored(stored);
    expect(b.rows.map((r) => r.id)).toEqual(["a1", "a2"]);
    expect(isDirty(b)).toBe(false);
    expect(valueOf(b.rows[0], "name")).toBe("Water");
  });

  it("edits a cell into the buffer, and an edit equal to the stored value is dropped", () => {
    let b = fromStored(stored);
    const uid = b.rows[0].uid;
    b = setCell(b, uid, f("pct"), "55").buf;
    expect(valueOf(b.rows[0], "pct")).toBe(55);
    expect(rowState(b.rows[0])).toBe("changed");
    b = setCell(b, uid, f("pct"), "60").buf;
    expect(rowState(b.rows[0])).toBe("");
    expect(isDirty(b)).toBe(false);
  });

  it("refuses a bad cell with its label and leaves the buffer", () => {
    const b = fromStored(stored);
    const out = setCell(b, b.rows[0].uid, f("pct"), "lots");
    expect(out.error).toMatch(/^Pct: /);
    expect(out.buf).toBe(b);
  });

  it("keeps an edit when fresh rows arrive, and drops it when the stored value now equals it", () => {
    let b = fromStored(stored);
    b = setCell(b, b.rows[0].uid, f("pct"), "55").buf;
    b = setCell(b, b.rows[1].uid, f("pct"), "45").buf;
    const fresh = [{ id: "a1", name: "Water", pct: 60 }, { id: "a2", name: "Oil", pct: 45 }, { id: "a3", name: "Salt", pct: 0 }];
    const next = syncStored(b, fresh);
    expect(next.rows.map((r) => r.id)).toEqual(["a1", "a2", "a3"]);
    expect(valueOf(next.rows[0], "pct")).toBe(55);
    expect(rowState(next.rows[1])).toBe("");
  });
});

describe("the body of the one save", () => {
  const opts = { recordId: "rec1", linesKey: "ingredients", canWrite: true };

  it("sends a new row as {data} and a changed row as {id, data} with the changed keys only", () => {
    let b = fromStored(stored);
    b = setCell(b, b.rows[1].uid, f("pct"), "45").buf;
    b = addRow(b);
    const fresh = b.rows[2].uid;
    b = setCell(b, fresh, f("name"), "Salt").buf;
    b = setCell(b, fresh, f("pct"), "5").buf;
    const out = buildSave(b, opts);
    expect(out.ok).toBe(true);
    if (!out.ok) return;
    expect(out.body.args).toEqual({
      id: "rec1",
      data: { ingredients: [{ id: "a2", data: { pct: 45 } }, { data: { name: "Salt", pct: 5 } }] },
    });
    // No key that the member left alone travels: not `name` of a2, not the computed `share`.
    expect(JSON.stringify(out.body.args)).not.toContain("share");
    expect(out.body.order).toEqual([b.rows[1].uid, fresh]);
  });

  it("does not send a blank new row or an untouched row", () => {
    let b = addRow(fromStored(stored));
    expect(buildSave(b, opts)).toEqual({ ok: false, message: "There is nothing to save." });
    b = setCell(b, b.rows[2].uid, f("name"), "Salt").buf;
    b = setCell(b, b.rows[2].uid, f("name"), "").buf;
    expect(saveCount(b)).toBe(0);
  });

  it("refuses a save of a viewer", () => {
    let b = fromStored(stored);
    b = setCell(b, b.rows[0].uid, f("pct"), "55").buf;
    const out = buildSave(b, { ...opts, canWrite: false });
    expect(out.ok).toBe(false);
    if (!out.ok) expect(out.message).toMatch(/Only the owner/);
  });

  it("refuses a save over 49 lines before any call, and allows exactly 49", () => {
    const rows = (n: number): SheetBuffer => {
      let b: SheetBuffer = EMPTY_BUFFER;
      for (let i = 0; i < n; i += 1) {
        b = addRow(b);
        b = setCell(b, b.rows[i].uid, f("pct"), String(i + 1)).buf;
      }
      return b;
    };
    expect(MAX_SHEET_LINES).toBe(49);
    expect(buildSave(rows(49), opts).ok).toBe(true);
    const over = buildSave(rows(50), opts);
    expect(over.ok).toBe(false);
    if (!over.ok) expect(over.message).toMatch(/at most 49 lines/);
  });
});

describe("the refusal", () => {
  const order = ["u1", "u2", "u3"];

  it("maps 'Line N' to the Nth row of the body that was sent, not to the screen order", () => {
    // The screen may sort the rows. The body order is the order of the buffer: here u2 is Line 2.
    const got = readSheetRefusal({ message: "Line 2 of `ingredients`: Field `pct` must be a number." }, "formulation", order);
    expect(got).toEqual({ alert: null, rows: { u2: "Field `pct` must be a number." } });
  });

  it("strips the place and the rule prefix of a rule of the parent and shows one alert", () => {
    const m = "Line 1 of `ingredients`: Rule `total_ok` of object `formulation`: The lines must sum to 100 %.";
    expect(parentRuleMessage(m, "formulation")).toBe("The lines must sum to 100 %.");
    expect(parentRuleMessage("Rule `total_ok` of object `formulation`: Plain.", "formulation")).toBe("Plain.");
    expect(parentRuleMessage(m, "ingredient")).toBeNull();
    expect(parentRuleMessage("Another sentence.", "formulation")).toBeNull();
    expect(readSheetRefusal({ message: m }, "formulation", order)).toEqual({ alert: "The lines must sum to 100 %.", rows: {} });
  });

  it("keeps a rule of the line object on its row, with the rule prefix stripped", () => {
    const got = readSheetRefusal(
      { message: "Line 3 of `ingredients`: Rule `pct_ok` of object `ingredient`: A share is at most 100." }, "formulation", order);
    expect(got.rows).toEqual({ u3: "A share is at most 100." });
  });

  it("shows any other sentence as the alert, also a place past the body", () => {
    expect(readSheetRefusal({ message: "This app is busy now." }, "formulation", order).alert).toBe("This app is busy now.");
    expect(readSheetRefusal({ message: "Line 9 of `ingredients`: Bad." }, "formulation", order).alert)
      .toBe("Line 9 of `ingredients`: Bad.");
    expect(readSheetRefusal(null, "formulation", order).alert).toBe("That did not work. Try again.");
  });
});

describe("one save is one door.run", () => {
  const opts = { parent: "formulation", recordId: "rec1", linesKey: "ingredients", canWrite: true };

  it("sends 20 new rows in ONE call, and shows the total of the answer", async () => {
    let b: SheetBuffer = EMPTY_BUFFER;
    for (let i = 0; i < 20; i += 1) {
      b = addRow(b);
      b = setCell(b, b.rows[i].uid, f("name"), `item ${i}`).buf;
      b = setCell(b, b.rows[i].uid, f("pct"), "5").buf;
    }
    const { door, calls } = fakeDoor({ ok: true, data: { record: { id: "rec1", data: { total: 100 } }, lines: {} } });
    const out = await saveSheet(door, b, opts);
    expect(calls).toHaveLength(1);
    expect(calls[0].action).toBe("object:formulation.update");
    const lines = (calls[0].args?.data as { ingredients: unknown[] }).ingredients;
    expect(lines).toHaveLength(20);
    expect(lines.every((l) => Object.keys(l as object).join() === "data")).toBe(true);
    expect(out.kind).toBe("saved");
    if (out.kind === "saved") {
      expect(out.answer.total).toBe(100);
      expect(isDirty(out.buf)).toBe(false);
      expect(out.buf.rows.every((r) => r.saved === true)).toBe(true);
      // The fresh read brings the ids, and the saved rows leave.
      expect(syncStored(out.buf, [{ id: "z1", name: "item 0", pct: 5 }]).rows.map((r) => r.id)).toEqual(["z1"]);
    }
  });

  it("makes no call over the cap, for a viewer or with no change", async () => {
    const { door, calls } = fakeDoor({ ok: true });
    expect((await saveSheet(door, fromStored(stored), opts)).kind).toBe("blocked");
    let b = fromStored(stored);
    b = setCell(b, b.rows[0].uid, f("pct"), "55").buf;
    expect((await saveSheet(door, b, { ...opts, canWrite: false })).kind).toBe("blocked");
    expect(calls).toHaveLength(0);
  });

  it("returns the refusal for a 99.5 total and leaves the edits to the caller", async () => {
    let b = fromStored(stored);
    b = setCell(b, b.rows[0].uid, f("pct"), "59.5").buf;
    const { door, calls } = fakeDoor({
      ok: false,
      message: "Line 1 of `ingredients`: Rule `total_ok` of object `formulation`: The lines must sum to 100 %.",
    });
    const out = await saveSheet(door, b, opts);
    expect(calls).toHaveLength(1);
    expect(out).toEqual({ kind: "refused", refusal: { alert: "The lines must sum to 100 %.", rows: {} } });
    expect(valueOf(b.rows[0], "pct")).toBe(59.5);
  });

  it("turns a thrown error into the plain sentence", async () => {
    let b = fromStored(stored);
    b = setCell(b, b.rows[0].uid, f("pct"), "55").buf;
    const door: StudioDataDoor = { scope: "p:live", read: async () => [], run: async () => { throw new Error("boom"); } };
    const out = await saveSheet(door, b, opts);
    expect(out).toEqual({ kind: "refused", refusal: { alert: "That did not work. Try again.", rows: {} } });
  });
});
