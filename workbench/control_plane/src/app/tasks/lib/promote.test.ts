/**
 * S6c — the promote door's pure half.
 *
 * Two things this fences. The blank rule agrees with the gateway's `_is_blank`
 * (`0` and `false` are answers). And the request `apiMoveTask` receives has
 * the shape `move_task` reads: `project_id`, the answered fields, and
 * `assignees` only when the member changed them.
 */

import { describe, expect, it } from "vitest";

import {
  type FieldDef,
  isBlank,
  missingSentence,
  requiredBlanks,
} from "@/app/projects/lib/customFields";

import { promotePlan } from "./promote";

const def = (over: Partial<FieldDef>): FieldDef => ({
  id: over.field_key ?? "f",
  project_id: "root",
  field_key: "f",
  name: "Field",
  field_type: "text",
  options: [],
  position: 0,
  required: true,
  ...over,
});

const COST = def({ field_key: "cost", name: "Cost", field_type: "number" });
const SIGNED = def({ field_key: "signed", name: "Signed off", field_type: "boolean" });
const OWNER = def({ field_key: "owner", name: "Owner", field_type: "text" });
const TAGS = def({
  field_key: "tags",
  name: "Tags",
  field_type: "multi_select",
  options: ["a", "b"],
});

describe("isBlank mirrors the gateway's _is_blank", () => {
  it("calls nothing, empty text and whitespace blank", () => {
    expect(isBlank(null)).toBe(true);
    expect(isBlank(undefined)).toBe(true);
    expect(isBlank("")).toBe(true);
    expect(isBlank("   ")).toBe(true);
    expect(isBlank([])).toBe(true);
    expect(isBlank({})).toBe(true);
  });

  it("calls 0 and false ANSWERS, not blanks", () => {
    // The classic falsy bug. On a required-field check it refuses the move
    // and tells the member to fill in a field they can see is filled in.
    expect(isBlank(0)).toBe(false);
    expect(isBlank(false)).toBe(false);
    expect(isBlank("0")).toBe(false);
    expect(isBlank(["a"])).toBe(false);
  });
});

describe("requiredBlanks", () => {
  it("names only the required definitions with no answer, in form order", () => {
    const optional = def({ field_key: "note", name: "Note", required: false, position: -1 });
    const got = requiredBlanks(
      [OWNER, optional, { ...COST, position: -2 }],
      { owner: null, note: null, cost: null },
    );
    expect(got.map((d) => d.name)).toEqual(["Cost", "Owner"]);
  });
});

describe("promotePlan", () => {
  it("blocks on a blank required field and names it", () => {
    const plan = promotePlan({
      destinationId: "dest",
      fields: [COST, OWNER],
      draft: { cost: "", owner: "   " },
      assignees: [],
      initialAssignees: [],
    });
    expect(plan).toEqual({ ok: false, missing: ["Cost", "Owner"] });
  });

  it("accepts 0 for a number and an unticked checkbox as answers", () => {
    const plan = promotePlan({
      destinationId: "dest",
      fields: [COST, SIGNED],
      draft: { cost: "0", signed: false },
      assignees: [],
      initialAssignees: [],
    });
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.request).toEqual({
      projectId: "dest",
      customFields: { cost: 0, signed: false },
    });
  });

  it("treats an empty multi-select as blank", () => {
    const plan = promotePlan({
      destinationId: "dest",
      fields: [TAGS],
      draft: { tags: [] },
      assignees: [],
      initialAssignees: [],
    });
    expect(plan).toEqual({ ok: false, missing: ["Tags"] });
  });

  it("sends no customFields when the destination asked nothing", () => {
    const plan = promotePlan({
      destinationId: "dest",
      fields: [],
      draft: {},
      assignees: ["me@x.io"],
      initialAssignees: ["me@x.io"],
    });
    expect(plan).toEqual({ ok: true, request: { projectId: "dest" } });
  });

  it("sends assignees only when the member changed them", () => {
    const untouched = promotePlan({
      destinationId: "dest",
      fields: [],
      draft: {},
      assignees: ["b@x.io", "a@x.io"],
      initialAssignees: ["a@x.io", "b@x.io"],
    });
    expect(untouched.ok && "assignees" in untouched.request).toBe(false);

    const added = promotePlan({
      destinationId: "dest",
      fields: [],
      draft: {},
      assignees: ["a@x.io", "c@x.io"],
      initialAssignees: ["a@x.io"],
    });
    expect(added.ok && added.request.assignees).toEqual(["a@x.io", "c@x.io"]);

    // `[]` clears; it must survive as an array, never fold into undefined.
    const cleared = promotePlan({
      destinationId: "dest",
      fields: [],
      draft: {},
      assignees: [],
      initialAssignees: ["a@x.io"],
    });
    expect(cleared.ok && cleared.request.assignees).toEqual([]);
  });
});

describe("missingSentence", () => {
  it("names one, two and three fields", () => {
    expect(missingSentence([])).toBe("");
    expect(missingSentence(["Cost"])).toBe("Fill in Cost first.");
    expect(missingSentence(["Cost", "Owner"])).toBe("Fill in Cost and Owner first.");
    expect(missingSentence(["A", "B", "C"])).toBe("Fill in A, B and C first.");
  });
});
