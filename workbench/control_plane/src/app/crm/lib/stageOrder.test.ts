// Task #838: the stage menu and the filter chips follow the board's lane order
// (D137). Both call `orderStageList`, which calls `orderLanes`.

import { describe, expect, it } from "vitest";

import { orderLanes, orderStageList } from "./board";
import { pickable } from "./emptyStages";
import { chipsFor } from "./filters";
import { placesOf } from "./pipelines";
import type { DealPipeline, Status } from "./types";
import { DEFAULT_VIEW } from "./urlState";

function stage(id: string, source: number, extra: Partial<Status> = {}): Status {
  return {
    id,
    name: id.toUpperCase(),
    color: "gray",
    position: source,
    source_position: source,
    type: "open",
    is_default: false,
    ...extra,
  };
}

// The Zoho picklist order is a, b, c, d. The pipeline lists them d, c, a, b.
const PICKLIST = [stage("a", 10), stage("b", 20), stage("c", 30), stage("d", 40)];
const PIPELINE: DealPipeline = {
  id: "p1",
  name: "Sales",
  is_default: true,
  position: 0,
  stages: ["d", "c", "a", "b"].map((id, i) => ({
    id,
    name: id,
    position: i + 1,
    type: "open" as const,
  })),
};
const places = placesOf([PIPELINE], "p1");
const ids = (rows: readonly { id: string }[]) => rows.map((r) => r.id);
const boardIds = (statuses: Status[], mode: "mirror" | "native", p = places) =>
  orderLanes(
    statuses.map((status) => ({ status, pipeline_position: undefined })),
    mode,
    p
  ).map((l) => l.status.id);

describe("stage menu and chips follow the board", () => {
  it("Mirror, no saved order: the pipeline order, not the picklist order", () => {
    const menu = ids(pickable(orderStageList("deals", PICKLIST, "mirror", places)));
    expect(menu).toEqual(["d", "c", "a", "b"]);
    expect(menu).toEqual(boardIds(PICKLIST, "mirror"));
    const chips = chipsFor("deals", PICKLIST, DEFAULT_VIEW, "mirror", places);
    expect(chips.map((c) => c.id)).toEqual(["all", "d", "c", "a", "b"]);
  });

  it("Mirror, a saved order: ui_position wins", () => {
    const saved = PICKLIST.map((s, i) =>
      stage(s.id, s.position, { ui_position: (3 - i) * 10, admin_order: true })
    );
    const menu = ids(orderStageList("deals", saved, "mirror", places));
    expect(menu).toEqual(["d", "c", "b", "a"]);
    expect(menu).toEqual(boardIds(saved, "mirror"));
    expect(
      chipsFor("deals", saved, DEFAULT_VIEW, "mirror", places).map((c) => c.id)
    ).toEqual(["all", ...menu]);
  });

  it("a deal with no pipeline uses the default pipeline's places", () => {
    const none = placesOf([PIPELINE], null);
    expect(ids(orderStageList("deals", PICKLIST, "mirror", none))).toEqual([
      "d",
      "c",
      "a",
      "b",
    ]);
  });

  it("no pipeline list: the source order, as the board keeps it", () => {
    const empty = placesOf(null, null);
    expect(ids(orderStageList("deals", PICKLIST, "mirror", empty))).toEqual([
      "a",
      "b",
      "c",
      "d",
    ]);
  });

  it("a hidden stage goes last, and shows only as the current value", () => {
    const withHidden = PICKLIST.map((s) => (s.id === "c" ? { ...s, hidden: true } : s));
    const ordered = orderStageList("deals", withHidden, "mirror", places);
    expect(ids(ordered)).toEqual(["d", "a", "b", "c"]);
    expect(ids(pickable(ordered))).toEqual(["d", "a", "b"]);
    expect(ids(pickable(ordered, "c"))).toEqual(["d", "a", "b", "c"]);
    expect(
      chipsFor("deals", withHidden, DEFAULT_VIEW, "mirror", places).map((c) => c.id)
    ).toEqual(["all", "d", "a", "b"]);
  });

  it("Native: the admin's position", () => {
    const native = [stage("a", 30), stage("b", 10), stage("c", 20)];
    expect(ids(orderStageList("deals", native, "native", places))).toEqual(["b", "c", "a"]);
  });

  it("leads keep the Leads board order", () => {
    const leads = [stage("x", 20), stage("y", 10, { hidden: true }), stage("z", 5)];
    expect(ids(orderStageList("leads", leads, "mirror", places))).toEqual(["z", "x", "y"]);
  });
});
