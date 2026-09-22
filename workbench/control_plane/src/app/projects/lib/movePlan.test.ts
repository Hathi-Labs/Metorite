/**
 * S6c repair — the move card reads a BULK preview that carries no
 * promote-only field.
 *
 * `e2e/projects-stale-after-write.spec.ts` stubs the preview exactly as
 * `PREVIEW` below, and PR #395's browser suite went red because the card
 * read `plan.required_missing.length` off it. This file feeds `readPlan`
 * the same shape, so the two cannot drift again: a key the card reads and
 * this file does not guard fails here first.
 */
import { describe, expect, it } from "vitest";

import type { FieldDef } from "./customFields";
import { type LoosePlan, askedFields, readPlan } from "./movePlan";

/** Byte-for-byte the e2e stub — everything the bulk dialog reads. */
const PREVIEW = {
  source_project_id: "p2",
  destination_project_id: "p1",
  crosses_status_set: false,
  crosses_root: false,
  statuses: [],
  destination_statuses: [],
  status_map: {},
  drops: {},
  types: [],
  tags: { unregistered: [] },
  field_map: {},
  orphan_fields: [],
  moved: 1,
  dropped_fields: [],
} as unknown as LoosePlan;

describe("readPlan — the bulk shape, with no promote fields", () => {
  it("reads the e2e stub without throwing, and calls it clean", () => {
    const got = readPlan(PREVIEW);
    expect(got).not.toBeNull();
    expect(got?.requiredMissing).toEqual([]);
    expect(got?.clean).toBe(true);
    expect(got?.drops).toEqual([]);
    expect(got?.lostTypes).toEqual([]);
    expect(got?.unregistered).toEqual([]);
    expect(got?.fieldRows).toEqual([]);
    expect(got?.statuses).toEqual([]);
    expect(got?.destStatuses).toEqual([]);
  });

  it("survives a preview with NOTHING but the two ids", () => {
    // Every array absent at once. This is the shape an older gateway, or a
    // proxied error page, hands the card.
    const got = readPlan({ source_project_id: "a", destination_project_id: "b" });
    expect(got).toEqual({
      statuses: [],
      destStatuses: [],
      drops: [],
      lostTypes: [],
      unregistered: [],
      fieldRows: [],
      requiredMissing: [],
      crossesStatusSet: false,
      crossesRoot: false,
      clean: true,
    });
  });

  it("answers null for no plan", () => {
    expect(readPlan(null)).toBeNull();
  });
});

describe("readPlan — the promote shape", () => {
  it("carries the required names, the drops and the losses through", () => {
    const got = readPlan({
      ...PREVIEW,
      crosses_root: true,
      required_missing: ["Cost centre"],
      drops: { scratch: [{ task_id: "t1", value: "x" }] },
      types: [{ from: { id: "ty" }, to: null }],
      tags: { carried: ["ops"], unregistered: ["ops"] },
      field_map: { po: "customer_po" },
      orphan_fields: [{ field_key: "old", name: "", field_type: "text" }],
    });
    expect(got?.requiredMissing).toEqual(["Cost centre"]);
    expect(got?.clean).toBe(false);
    expect(got?.drops.map(([key]) => key)).toEqual(["scratch"]);
    expect(got?.lostTypes).toHaveLength(1);
    expect(got?.unregistered).toEqual(["ops"]);
    expect(got?.fieldRows).toEqual([
      { from: "po", to: "customer_po" },
      { from: "old", to: null },
    ]);
  });
});

describe("askedFields", () => {
  const def = (key: string): FieldDef => ({
    id: key, project_id: "p", field_key: key, name: key, field_type: "text",
    options: [], position: 0, required: true,
  });

  it("is null while the preview's definitions are on their way", () => {
    expect(askedFields(null, [def("a")])).toBeNull();
  });

  it("adds a refused field the preview did not list, once", () => {
    const got = askedFields([def("a")], [def("a"), def("b")]);
    expect(got?.map((d) => d.field_key)).toEqual(["a", "b"]);
  });

  it("returns the preview's own array when nothing is added", () => {
    const preview = [def("a")];
    expect(askedFields(preview, undefined)).toBe(preview);
    expect(askedFields(preview, [def("a")])).toBe(preview);
  });
});
