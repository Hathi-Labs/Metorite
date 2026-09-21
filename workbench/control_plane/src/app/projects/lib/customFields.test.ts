/**
 * Projects · custom fields in the browser (WS-27l).
 *
 * The gateway owns what a value may BE. This file owns its shape on the way
 * out of a form, and the awkward cases are the ones a form gets wrong: an
 * empty box, a deliberate `0`, an unticked checkbox, and a value whose
 * definition has since been deleted.
 */

import { describe, expect, it } from "vitest";

import {
  CHOICE_TYPES,
  FIELD_TYPES,
  type FieldDef,
  type FieldType,
  changeLabel,
  changedValues,
  displayValue,
  emptyValue,
  keyPreview,
  needsOptions,
  ordered,
  toInput,
  toWire,
} from "./customFields";

const def = (over: Partial<FieldDef> = {}): FieldDef => ({
  id: "f1",
  project_id: "p1",
  field_key: "customer",
  name: "Customer",
  field_type: "text",
  options: [],
  position: 0,
  ...over,
});

describe("keyPreview", () => {
  it("matches what the gateway will derive, so the form can show it first", () => {
    // The key is permanent — every stored value is filed under it — so somebody
    // should see it while they can still change their mind about the name.
    expect(keyPreview("Customer PO #")).toBe("customer_po");
    expect(keyPreview("  Due   Date  ")).toBe("due_date");
  });

  it("always starts with a letter, as the column's CHECK requires", () => {
    for (const name of ["2026 target", "#1 priority", "!!!", "___"]) {
      expect(keyPreview(name)).toMatch(/^[a-z][a-z0-9_]{0,62}$/);
    }
  });

  it("gives an empty name something rather than an invalid key", () => {
    expect(keyPreview("")).toBe("field");
  });
});

describe("toWire", () => {
  it("turns a typed number into a number, not the string it came as", () => {
    // Every <input> hands back a string. Without this a number field would be
    // refused for every value anybody types into it.
    expect(toWire("number", "2500")).toBe(2500);
    expect(toWire("number", "2.5")).toBe(2.5);
  });

  it("treats a blank number box as no value, never as zero", () => {
    // `Number("")` is 0, which is why the empty check has to come first.
    expect(toWire("number", "")).toBeNull();
    expect(toWire("number", "  ")).toBeNull();
  });

  it("keeps a deliberate zero", () => {
    expect(toWire("number", "0")).toBe(0);
  });

  it("sends something unparseable through so the server can name the problem", () => {
    // Refusing here would produce a second, differently-worded validation
    // message for the same value — and the server's is the one that is right.
    expect(toWire("number", "twelve")).toBe("twelve");
  });

  it("sends false rather than clearing an unticked checkbox", () => {
    // `false` IS the answer to "is this blocked". Clearing it would make
    // "answered no" and "never asked" the same state.
    expect(toWire("boolean", false)).toBe(false);
    expect(toWire("boolean", true)).toBe(true);
  });

  it("clears an empty multi-select rather than storing an empty list", () => {
    // A stored `[]` and a missing key both render as "nothing chosen" while
    // filtering differently, and only one of them can be right.
    expect(toWire("multi_select", [])).toBeNull();
    expect(toWire("multi_select", ["ops"])).toEqual(["ops"]);
  });

  it("trims text and clears what was only whitespace", () => {
    expect(toWire("text", "  PO-1  ")).toBe("PO-1");
    expect(toWire("text", "   ")).toBeNull();
  });

  it("clears an emptied select", () => {
    expect(toWire("select", "")).toBeNull();
  });
});

describe("toInput", () => {
  it("shows a stored zero rather than blanking it", () => {
    // `value || ""` would blank both `0` and `false` — the classic falsy bug,
    // and here it silently loses a real answer.
    expect(toInput("number", 0)).toBe("0");
  });

  it("shows a stored false as unticked, not as missing", () => {
    expect(toInput("boolean", false)).toBe(false);
    expect(toInput("boolean", true)).toBe(true);
  });

  it("gives a date input the yyyy-mm-dd it requires", () => {
    expect(toInput("date", "2026-08-31T00:00:00+00:00")).toBe("2026-08-31");
  });

  it("starts an unset field empty rather than as 'null'", () => {
    expect(toInput("text", null)).toBe("");
    expect(toInput("text", undefined)).toBe("");
    expect(toInput("multi_select", null)).toEqual([]);
  });
});

describe("emptyValue", () => {
  it("gives every type a control-shaped starting point", () => {
    expect(emptyValue("boolean")).toBe(false);
    expect(emptyValue("multi_select")).toEqual([]);
    expect(emptyValue("text")).toBe("");
  });
});

describe("displayValue", () => {
  it("renders an unset field as a dash, not as blank space", () => {
    expect(displayValue(def(), null)).toBe("—");
    expect(displayValue(def({ field_type: "multi_select" }), [])).toBe("—");
  });

  it("renders a zero, because it is a value", () => {
    expect(displayValue(def({ field_type: "number" }), 0)).toBe("0");
  });

  it("renders false as No rather than as unset", () => {
    expect(displayValue(def({ field_type: "boolean" }), false)).toBe("No");
    expect(displayValue(def({ field_type: "boolean" }), true)).toBe("Yes");
  });

  it("joins a multi-select", () => {
    expect(displayValue(def({ field_type: "multi_select" }), ["ops", "eng"])).toBe(
      "ops, eng"
    );
  });

  it("shows an unparseable date as stored rather than as 'Invalid Date'", () => {
    expect(displayValue(def({ field_type: "date" }), "soon")).toBe("soon");
  });
});

describe("changeLabel", () => {
  const defs = [def({ field_key: "customer_po", name: "Customer PO" })];

  it("shows the field's label, not the database key", () => {
    expect(changeLabel("custom.customer_po", defs)).toBe("Customer PO");
  });

  it("still says something once the field has been deleted", () => {
    // A change survives the field it names, and the timeline still has to read.
    expect(changeLabel("custom.old_thing", defs)).toBe("old thing");
  });

  /**
   * ⚠️ These replaced a test that asserted the OPPOSITE — `changeLabel("title")`
   * used to return `"title"`, and "leaves an ordinary column name alone" was
   * the name it passed under.
   *
   * It was never a decision. Custom fields were the case the function was
   * written for, and the built-in columns fell through because nobody had
   * looked at what they rendered. The owner did, on 2026-09-21, and the
   * timeline said `Edited due_at, start_date`.
   */
  it("says Due date, not due_at", () => {
    expect(changeLabel("due_at", defs)).toBe("Due date");
    expect(changeLabel("start_date", defs)).toBe("Start date");
    expect(changeLabel("estimate_mins", defs)).toBe("Estimate");
  });

  it("uses the word the product uses, not the word the column uses", () => {
    // `importance` is the column. Every surface in Projects calls it
    // priority, and the timeline is the one place that said otherwise.
    expect(changeLabel("importance", defs)).toBe("Priority");
  });

  it("labels a project node's fields too", () => {
    // `tree.py::_TRACKED_PROJECT_FIELDS` writes these, through the same
    // `field_change` door and into the same renderer.
    expect(changeLabel("archive_after_months", defs)).toBe("Auto-archive after");
    expect(changeLabel("parent_project_id", defs)).toBe("Parent project");
  });

  it("degrades a column nobody listed into readable words", () => {
    // ⚠️ The rung that makes the map safe to keep. `FIELD_LABELS` mirrors two
    // tuples that live in Python; a column added there and forgotten here
    // must still not reach a member as a database key.
    expect(changeLabel("some_new_column", defs)).toBe("Some new column");
  });

  it("gives a single-word column a capital", () => {
    expect(changeLabel("lead", defs)).toBe("Lead");
  });
});

describe("ordered", () => {
  it("sorts by position, then by name for a tie", () => {
    const defs = [
      def({ id: "b", name: "Beta", position: 1 }),
      def({ id: "c", name: "Alpha", position: 1 }),
      def({ id: "a", name: "Zulu", position: 0 }),
    ];
    expect(ordered(defs).map((d) => d.id)).toEqual(["a", "c", "b"]);
  });

  it("does not reorder the array it was given", () => {
    const defs = [def({ id: "b", position: 1 }), def({ id: "a", position: 0 })];
    ordered(defs);
    expect(defs.map((d) => d.id)).toEqual(["b", "a"]);
  });
});

describe("changedValues", () => {
  const defs = [
    def({ field_key: "customer", field_type: "text" }),
    def({ field_key: "budget", field_type: "number" }),
    def({ field_key: "open", field_type: "boolean" }),
  ];

  it("sends only what actually moved", () => {
    // A PATCH carrying every field makes one edit read as three on the
    // timeline, burying the one that mattered.
    const patch = changedValues(
      defs,
      { customer: "Acme", budget: 10, open: true },
      { customer: "Acme", budget: "20", open: true }
    );
    expect(patch).toEqual({ budget: 20 });
  });

  it("does not treat an unanswered checkbox as an edit", () => {
    // A checkbox has no "unset" state to render, so an unticked box and a
    // field nobody has ever answered are the same pixels. Comparing against
    // `null` made EVERY save on such a task write `false` and post a timeline
    // entry for an edit nobody made.
    expect(changedValues(defs, {}, { open: false })).toEqual({});
  });

  it("still sends a checkbox somebody actually ticked", () => {
    expect(changedValues(defs, {}, { open: true })).toEqual({ open: true });
  });

  it("sends nothing when nothing was touched", () => {
    expect(
      changedValues(defs, { customer: "Acme" }, { customer: "Acme", open: false })
    ).toEqual({});
  });

  it("sends null for a field somebody emptied", () => {
    expect(changedValues(defs, { customer: "Acme" }, { customer: "" })).toEqual({
      customer: null,
    });
  });

  it("notices a boolean turned off, which a truthiness check would miss", () => {
    expect(changedValues(defs, { open: true }, { open: false })).toEqual({
      open: false,
    });
  });

  it("notices a number set to zero", () => {
    expect(changedValues(defs, { budget: 5 }, { budget: "0" })).toEqual({
      budget: 0,
    });
  });

  it("ignores a stored key whose definition is gone", () => {
    // Deleting a definition strips its values server-side, but a panel opened
    // before that happened still holds the old value in memory.
    expect(changedValues(defs, { retired: "x", customer: "Acme" }, {})).toEqual({
      customer: null,
    });
  });
});

describe("the type vocabulary", () => {
  it("matches the gateway's", () => {
    expect(FIELD_TYPES).toEqual([
      "text",
      "number",
      "date",
      "select",
      "multi_select",
      "boolean",
      "url",
    ] satisfies FieldType[]);
  });

  it("asks for options on exactly the choice types", () => {
    expect(FIELD_TYPES.filter(needsOptions)).toEqual(CHOICE_TYPES);
  });
});
