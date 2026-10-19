import { describe, expect, it } from "vitest";

import {
  canEditView,
  chipList,
  dirtyState,
  orderViews,
  stableJson,
  viewById,
  type SavedView,
} from "./index";

describe("chipList", () => {
  it("starts with the default view and marks the one in effect", () => {
    const list: SavedView[] = [
      { id: "a", name: "Mine", shared: false, mine: true, position: 0 },
      { id: "b", name: "Team", shared: true, mine: true, position: 0 },
    ];
    const chips = chipList("All deals", list, "b");
    expect(chips.map((c) => c.label)).toEqual(["All deals", "Mine", "Team"]);
    expect(chips.map((c) => c.active)).toEqual([false, false, true]);
    expect(chips[2].shared).toBe(true);
  });
  it("keeps the default chip active for an unknown id", () => {
    const one: SavedView[] = [{ id: "a", name: "A", shared: false, mine: true, position: 0 }];
    expect(chipList("All deals", one, "gone")[0].active).toBe(true);
    expect(chipList("All deals", [], null).length).toBe(1);
  });
});

const v = (id: string, over: Partial<SavedView> = {}): SavedView => ({
  id,
  name: id,
  shared: false,
  mine: true,
  position: 0,
  ...over,
});

describe("orderViews", () => {
  it("puts private views first, then my shared ones, then the shared ones of others", () => {
    const list = [
      v("theirs", { shared: true, mine: false, position: 0 }),
      v("mine-shared", { shared: true, position: 0 }),
      v("private-b", { position: 2 }),
      v("private-a", { position: 1 }),
    ];
    expect(orderViews(list).map((x) => x.id)).toEqual([
      "private-a",
      "private-b",
      "mine-shared",
      "theirs",
    ]);
  });

  it("keeps the order of the list on a tie, and reads the owner when `mine` is false", () => {
    const list = [
      v("b", { shared: true, mine: false, owner_email: "Ann@x.test" }),
      v("a", { shared: true, mine: false, owner_email: "bob@x.test" }),
    ];
    expect(orderViews(list, "ann@x.test").map((x) => x.id)).toEqual(["b", "a"]);
    expect(orderViews(list, "bob@x.test").map((x) => x.id)).toEqual(["a", "b"]);
  });

  it("does not change its input, and takes null", () => {
    const list = [v("b", { position: 1 }), v("a", { position: 0 })];
    orderViews(list);
    expect(list.map((x) => x.id)).toEqual(["b", "a"]);
    expect(orderViews(null)).toEqual([]);
  });
});

describe("dirtyState", () => {
  const saved: { state: Record<string, unknown> } = {
    state: { filters: { owner: "me" }, sort: null },
  };
  it("is blank or unsaved on the default view", () => {
    expect(dirtyState(null, { state: {}, blank: true })).toBe("blank");
    expect(dirtyState(null, { state: {}, blank: false })).toBe("unsaved");
  });
  it("is clean when the screen equals the saved state, whatever the key order", () => {
    expect(
      dirtyState(saved, { state: { sort: null, filters: { owner: "me" } }, blank: false }),
    ).toBe("clean");
  });
  it("is changed when the screen differs from the saved view", () => {
    expect(
      dirtyState(saved, { state: { filters: { owner: "x" }, sort: null }, blank: false }),
    ).toBe("changed");
    // A saved view with nothing set is still a saved view.
    expect(dirtyState(saved, { state: {}, blank: true })).toBe("changed");
  });
});

describe("small rules", () => {
  it("stableJson ignores key order and undefined", () => {
    expect(stableJson({ a: 1, b: undefined, c: [{ y: 1, x: 2 }] })).toBe(
      stableJson({ c: [{ x: 2, y: 1 }], a: 1 }),
    );
  });
  it("viewById and canEditView", () => {
    expect(viewById([v("a")], "a")?.id).toBe("a");
    expect(viewById([v("a")], "gone")).toBeNull();
    expect(canEditView(v("a", { mine: false }), false)).toBe(false);
    expect(canEditView(v("a", { mine: false }), true)).toBe(true);
  });
});
