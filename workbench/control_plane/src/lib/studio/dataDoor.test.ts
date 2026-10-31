/**
 * STU-D1 fence: data binding and typed actions in the one renderer.
 * The vitest env has no DOM, so effects never run. Rows come from a seeded
 * cache, and the loading case comes from an empty cache.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import GenerativeUINode from "@/components/GenerativeUINode";
import { cacheKey, clearAll, put } from "@/lib/dataCache";
import {
  type ActionResult, type Row, type StudioDataDoor, StudioDataContext,
  actionDisabled, boundView, bindsSource, gridData, parseAction, parseSource, pressAction,
} from "./dataDoor";

function mockDoor(): StudioDataDoor & { reads: unknown[]; runs: unknown[][] } {
  const reads: unknown[] = [];
  const runs: unknown[][] = [];
  return {
    reads,
    runs,
    read: async (source) => { reads.push(source); return [] as Row[]; },
    run: async (action, args) => { runs.push([action, args]); return { ok: true } as ActionResult; },
  };
}

const SOURCE = { action: "tasks.list", args: { status: "open" } };
const KEY = cacheKey("studio:tasks.list", { status: "open" });
const TABLE = {
  type: "table",
  props: { source: SOURCE, columns: ["Name", "Owner"] },
};
const GRID = {
  type: "template",
  props: { name: "dataGrid", source: SOURCE, data: { columns: ["Name", "Owner"] } },
};

const draw = (spec: unknown, door: StudioDataDoor | null) =>
  renderToStaticMarkup(
    createElement(StudioDataContext.Provider, { value: door },
      createElement(GenerativeUINode, { spec, onAction: () => {} })),
  );

beforeEach(() => clearAll());

describe("a node with source and a door", () => {
  it("draws the rows the door read (table)", () => {
    put<Row[]>(KEY, [{ Name: "Ship D1", Owner: "Asha" }]);
    const html = draw(TABLE, mockDoor());
    expect(html).toContain("Ship D1");
    expect(html).toContain("Asha");
  });

  it("draws the rows the door read (dataGrid)", () => {
    put<Row[]>(KEY, [{ Name: "Ship D1", Owner: "Asha" }]);
    const html = draw(GRID, mockDoor());
    expect(html).toContain("Ship D1");
  });

  it("draws the loading state while the cache is empty", () => {
    const html = draw(TABLE, mockDoor());
    expect(html).toContain('aria-busy="true"');
    expect(html).not.toContain("<table");
  });
});

describe("with no door (chat)", () => {
  it("ignores source and draws the own props of the node", () => {
    put<Row[]>(KEY, [{ Name: "From cache", Owner: "X" }]);
    const own = { type: "table", props: { source: SOURCE, columns: ["Name"], rows: [["Own row"]] } };
    const html = draw(own, null);
    expect(html).toContain("Own row");
    expect(html).not.toContain("From cache");
    expect(html).toBe(
      renderToStaticMarkup(createElement(GenerativeUINode, {
        spec: { type: "table", props: { columns: ["Name"], rows: [["Own row"]] } },
      })),
    );
  });
});

describe("boundView", () => {
  it("chooses the view from the state", () => {
    expect(boundView({ data: undefined, loading: true, error: null })).toBe("loading");
    expect(boundView({ data: undefined, loading: false, error: null })).toBe("loading");
    expect(boundView({ data: undefined, loading: false, error: "boom" })).toBe("error");
    expect(boundView({ data: [], loading: false, error: null })).toBe("empty");
    expect(boundView({ data: [{ a: 1 }], loading: false, error: null })).toBe("rows");
    // Rows already read stay on screen when a refresh fails.
    expect(boundView({ data: [{ a: 1 }], loading: false, error: "boom" })).toBe("rows");
  });
});

describe("gridData", () => {
  it("maps keyed rows to cells by column, and keeps rows that have cells", () => {
    const out = gridData({ columns: ["Name", "Owner"] }, [
      { id: 1, Owner: "Asha", Name: "A" },
      { cells: ["B", "Ravi"] },
    ]);
    expect(out.rows).toEqual([{ id: 1, cells: ["A", "Asha"] }, { cells: ["B", "Ravi"] }]);
  });

  it("names the columns from the first row when none are given", () => {
    const out = gridData({}, [{ id: 1, Name: "A", Owner: "X" }]);
    expect(out.columns).toEqual(["Name", "Owner"]);
  });
});

describe("source parsing", () => {
  it("binds only table and dataGrid", () => {
    expect(bindsSource("table", { source: SOURCE })).toBe(true);
    expect(bindsSource("template", { name: "dataGrid", source: SOURCE })).toBe(true);
    expect(bindsSource("template", { name: "barChart", source: SOURCE })).toBe(false);
    expect(bindsSource("list", { source: SOURCE })).toBe(false);
    expect(bindsSource("table", { source: { action: "" } })).toBe(false);
    expect(bindsSource("table", {})).toBe(false);
    expect(parseSource("tasks.list")).toBeNull();
  });
});

describe("typed actions", () => {
  it("a button sends its action id through the door", () => {
    const door = mockDoor();
    const action = parseAction({ id: "task.complete", args: { id: 7 }, message: "Complete task 7" });
    const onAction = vi.fn();
    pressAction(action, door, onAction);
    expect(door.runs).toEqual([["task.complete", { id: 7 }]]);
    expect(onAction).not.toHaveBeenCalled();
  });

  it("with no door, a typed action sends message, else the id", () => {
    const onAction = vi.fn();
    pressAction(parseAction({ id: "task.complete", message: "Complete task 7" }), null, onAction);
    pressAction(parseAction({ id: "task.complete" }), null, onAction);
    expect(onAction.mock.calls).toEqual([["Complete task 7"], ["task.complete"]]);
  });

  it("the string action is unchanged, door or not", () => {
    const door = mockDoor();
    const onAction = vi.fn();
    pressAction(parseAction("Show my tasks"), door, onAction);
    pressAction(parseAction("Show my tasks"), null, onAction);
    expect(onAction.mock.calls).toEqual([["Show my tasks"], ["Show my tasks"]]);
    expect(door.runs).toEqual([]);
  });

  it("enables a button only when it can act", () => {
    const door = mockDoor();
    const typed = parseAction({ id: "x" });
    expect(actionDisabled(typed, door, undefined)).toBe(false);
    expect(actionDisabled(typed, null, undefined)).toBe(true);
    expect(actionDisabled(typed, null, () => {})).toBe(false);
    expect(actionDisabled(parseAction("go"), door, undefined)).toBe(true);
    expect(actionDisabled(null, door, () => {})).toBe(true);
  });

  it("renders the button enabled on a page with a typed action", () => {
    const spec = { type: "button", props: { label: "Done it", action: { id: "task.complete" } } };
    const page = renderToStaticMarkup(
      createElement(StudioDataContext.Provider, { value: mockDoor() },
        createElement(GenerativeUINode, { spec })),
    );
    expect(page).toContain("Done it");
    expect(page).not.toContain('disabled=""');
    expect(renderToStaticMarkup(createElement(GenerativeUINode, { spec }))).toContain('disabled=""');
  });
});
