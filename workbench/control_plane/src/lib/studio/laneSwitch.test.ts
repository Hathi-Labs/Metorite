/**
 * STU-LY-4b fence: the lane switcher picks one lane, labels each tab with its count, and asks only through the node.
 *
 * Mutation 1: let `activeLane` return a lane that does not exist. "falls back to the first lane" turns red.
 * Mutation 2: let `phoneSwitch` change its input. "never changes its input" turns red.
 * Mutation 3: let `shownLane` ignore the hold. "follows the lane under the held card" turns red.
 * Mutation 4: let `laneMark` say `off` for the lane of the held card. "keeps the lane of the held card" turns red.
 * Mutation 5: drop Tab from `keyCancelsHold`. "cancels on Tab and Escape" turns red.
 * Mutation 6: let `laneAfterCancel` ignore `at`. "stays on the lane that the member pressed" turns red.
 * Mutation 7: let the gate stay open after a press. "stays shut from a press to its click" turns red.
 */
import { describe, expect, it } from "vitest";

import {
  activeLane, createScrollGate, keyCancelsHold, laneAfterCancel, laneMark, laneTabs, phoneSwitch, shownLane,
} from "./laneSwitch";
import { keyStep, parseBoard, type Hold } from "./boardModel";

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

describe("a keyboard move on a phone", () => {
  const ids = ["a", "b", "c"];
  const card = { id: "1", lane: "a" };
  const press = (hold: Hold | null, key: string) => keyStep(hold, key, card, ids).hold;
  const marks = (wanted: string | null, hold: Hold | null) =>
    ids.map((id) => laneMark(id, shownLane(ids, wanted, hold), hold));

  it("follows the lane under the held card", () => {
    let hold = press(null, " ");
    expect(shownLane(ids, null, hold)).toBe("a");
    hold = press(hold, "ArrowRight");
    expect(shownLane(ids, null, hold)).toBe("b");
    hold = press(hold, "ArrowRight");
    expect(shownLane(ids, null, hold)).toBe("c");
    hold = press(hold, "ArrowLeft");
    expect(shownLane(ids, null, hold)).toBe("b");
  });

  it("keeps the lane of the held card in the page, so the card keeps focus", () => {
    const hold = press(press(null, " "), "ArrowRight");
    expect(marks(null, hold)).toEqual(["held", "shown", "off"]);
    expect(marks(null, press(hold, "ArrowLeft"))).toEqual(["shown", "off", "off"]);
  });

  it("goes back to the wanted lane when the hold ends, and marks no lane held", () => {
    expect(shownLane(ids, "c", null)).toBe("c");
    expect(marks("b", null)).toEqual(["off", "shown", "off"]);
  });
});

describe("the end of a hold", () => {
  const hold = { from: "a" };

  it("cancels on Tab and Escape, and on no key of the move", () => {
    expect(["Tab", "Escape"].map(keyCancelsHold)).toEqual([true, true]);
    expect([" ", "ArrowLeft", "ArrowRight", "Shift", "Enter"].filter(keyCancelsHold)).toEqual([]);
  });

  it("goes back to the lane of the card when no lane was pressed or focused", () => {
    expect(laneAfterCancel(hold, null)).toBe("a");
    expect(laneAfterCancel(hold, undefined)).toBe("a");
  });

  it("stays on the lane that the member pressed", () => {
    expect(laneAfterCancel(hold, "b")).toBe("b");
    // No lane is marked held after the cancel, so nothing out of sight can take focus.
    expect(["a", "b", "c"].map((id) => laneMark(id, shownLane(["a", "b", "c"], "b", null), null)))
      .toEqual(["off", "shown", "off"]);
  });
});

describe("createScrollGate", () => {
  it("stays shut from a press to its click, and opens again", () => {
    const gate = createScrollGate();
    expect(gate.open()).toBe(true);
    gate.press();
    expect(gate.open()).toBe(false);
    gate.release();
    expect(gate.open()).toBe(true);
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
