import { describe, expect, it } from "vitest";

import { PACK_SWITCH_EVENT, packSwitchesOf, packSwitchText } from "./packSwitch";

describe("packSwitch", () => {
  it("names the event as the server does", () => {
    expect(PACK_SWITCH_EVENT).toBe("chat_pack_switched");
  });

  it("reads the label of a switch event", () => {
    expect(packSwitchesOf([{ name: PACK_SWITCH_EVENT, value: { label: "Projects" } }])).toEqual([
      "Projects",
    ]);
  });

  it("writes the line", () => {
    expect(packSwitchText("Projects")).toBe("Switched to Projects");
  });

  it("ignores other events and a value with no label", () => {
    expect(
      packSwitchesOf([
        { name: "assistant_profile_used", value: { v: 1 } },
        { name: PACK_SWITCH_EVENT, value: {} },
        { name: PACK_SWITCH_EVENT, value: { label: 5 } },
        { name: PACK_SWITCH_EVENT, value: null },
      ]),
    ).toEqual([]);
    expect(packSwitchesOf(undefined)).toEqual([]);
  });

  it("cleans and caps a label", () => {
    const [label] = packSwitchesOf([
      { name: PACK_SWITCH_EVENT, value: { label: `  A\n  ${"b".repeat(200)}` } },
    ]);
    expect(label.startsWith("A b")).toBe(true);
    expect(label.length).toBe(80);
  });
});
