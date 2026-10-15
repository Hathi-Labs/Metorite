import { describe, expect, it } from "vitest";

import { ACTION_TYPES } from "../components/automation/ai-settings/actionFormat";
import { actionOptions, LINK_FIRST, readReachStatus, REACH_OFF } from "./reachStatus";

describe("Tell me on WhatsApp in the Rules UI (WS-59 RE-5, P1-4)", () => {
  it("leaves the action out when Reach is not available", () => {
    const got = actionOptions(ACTION_TYPES, REACH_OFF, "LABEL");
    expect(got.map((o) => o.type)).not.toContain("NOTIFY_ME");
    expect(got.length).toBe(ACTION_TYPES.length - 1);
  });

  it("shows the action disabled with the step when there is no link", () => {
    const got = actionOptions(ACTION_TYPES, { available: true, linked: false }, "LABEL");
    expect(got.find((o) => o.type === "NOTIFY_ME")).toEqual({
      type: "NOTIFY_ME", disabled: true, hint: LINK_FIRST,
    });
  });

  it("offers the action when Reach is available and linked", () => {
    const got = actionOptions(ACTION_TYPES, { available: true, linked: true }, "LABEL");
    expect(got.find((o) => o.type === "NOTIFY_ME")).toEqual({
      type: "NOTIFY_ME", disabled: false, hint: null,
    });
  });

  it("keeps the type an existing action already holds", () => {
    const got = actionOptions(ACTION_TYPES, REACH_OFF, "NOTIFY_ME");
    expect(got.find((o) => o.type === "NOTIFY_ME")?.disabled).toBe(false);
  });

  it("reads only a 200 with available true as available", () => {
    expect(readReachStatus(200, { available: true, linked: true })).toEqual({
      available: true, linked: true,
    });
    expect(readReachStatus(200, { available: false, linked: true })).toEqual(REACH_OFF);
    expect(readReachStatus(403, { available: true, linked: true })).toEqual(REACH_OFF);
    expect(readReachStatus(200, null)).toEqual(REACH_OFF);
  });
});
