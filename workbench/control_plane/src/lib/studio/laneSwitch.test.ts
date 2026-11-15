/**
 * STU-LY-4b fence: the lane switcher picks one lane, labels each tab with its count, and asks only through the node.
 *
 * Mutation 1: let `activeLane` return a lane that does not exist. "falls back to the first lane" turns red.
 * Mutation 2: let `phoneSwitch` change its input. "never changes its input" turns red.
 */
import { describe, expect, it } from "vitest";

import { activeLane, laneTabs, phoneSwitch } from "./laneSwitch";
import { parseBoard } from "./boardModel";

describe("activeLane", () => {
  it("keeps the wanted lane, and falls back to the first lane for a lane that is gone", () => {
    expect(activeLane(["a", "b", "c"], "b")).toBe("b");
    expect(activeLane(["a", "b", "c"], "zzz")).toBe("a");
    expect(activeLane(["a", "b"], null)).toBe("a");
    expect(activeLane(["a", "b"], undefined)).toBe("a");
  });
  it("is null for a board with no lane", () => {
    expect(activeLane([], "a")).toBeNull();
  });
});

describe("laneTabs", () => {
  it("labels each tab with the title and the count of its lane", () => {
    expect(laneTabs([{ id: "x", title: "Open", total: 3 }, { id: "y", title: "Done", total: 0 }])).toEqual([
      { id: "x", label: "Open (3)" }, { id: "y", label: "Done (0)" },
    ]);
    expect(laneTabs([])).toEqual([]);
  });
});

describe("phoneSwitch", () => {
  const node = Object.freeze({ type: "board", props: Object.freeze({ data: Object.freeze({ groupBy: "s" }), source: { action: "a" } }) });
  it("sets phone: switch in the data of the node and never changes its input", () => {
    const out = phoneSwitch(node);
    expect((out.props.data as Record<string, unknown>).phone).toBe("switch");
    expect((out.props.data as Record<string, unknown>).groupBy).toBe("s");
    expect(out.props.source).toBe(node.props.source);
    expect((node.props.data as Record<string, unknown>).phone).toBeUndefined();
  });
  it("returns a node with no data as it is", () => {
    const a = { props: {} };
    const b = { props: { data: [1] } };
    expect(phoneSwitch(a)).toBe(a);
    expect(phoneSwitch(b)).toBe(b);
  });
});

describe("parseBoard reads phone", () => {
  const fields = [{ key: "s", type: "select", options: ["a", "b"] }, { key: "title", type: "text" }];
  it("keeps phone: switch, and leaves the config unchanged for any other value", () => {
    const sw = parseBoard({ fields, groupBy: "s", phone: "switch" });
    expect(sw.ok && sw.config.phone).toBe("switch");
    const plain = parseBoard({ fields, groupBy: "s" });
    expect(plain.ok && "phone" in plain.config).toBe(false);
    const odd = parseBoard({ fields, groupBy: "s", phone: "grid" });
    expect(odd.ok && "phone" in odd.config).toBe(false);
  });
});
