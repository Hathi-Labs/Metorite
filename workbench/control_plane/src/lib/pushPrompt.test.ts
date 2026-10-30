import { describe, expect, it } from "vitest";

import {
  SHOWN_GAP_MS,
  SNOOZE_MS,
  mergePromptState,
  promptStateOf,
  shouldShowPushPrompt,
  snoozeUntil,
  type PromptInput,
} from "@/lib/pushPrompt";

const NOW = new Date("2026-10-30T09:00:00Z");
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();
const ahead = (ms: number) => new Date(NOW.getTime() + ms).toISOString();

const OPEN: PromptInput = {
  flag: true,
  phone: true,
  ownChat: true,
  support: "supported",
  permission: "default",
  registered: false,
  messageCount: 2,
  streaming: false,
  state: {},
};
const show = (patch: Partial<PromptInput>) => shouldShowPushPrompt({ ...OPEN, ...patch }, NOW);

describe("shouldShowPushPrompt", () => {
  it("shows the Turn on sheet on a phone with the flag on and push off", () => {
    expect(show({})).toBe("turn-on");
  });

  it("shows nothing with the flag off", () => {
    expect(show({ flag: false })).toBe("none");
  });

  it("shows nothing on a desktop", () => {
    expect(show({ phone: false })).toBe("none");
  });

  it("shows nothing outside the member's own assistant", () => {
    expect(show({ ownChat: false })).toBe("none");
  });

  it.each([
    ["supported", "turn-on"],
    ["ios-tab", "ios-tab"],
    ["unsupported", "none"],
  ] as const)("support %s gives %s", (support, want) => {
    expect(show({ support })).toBe(want);
  });

  describe("the permission", () => {
    it("granted and registered here: nothing", () => {
      expect(show({ permission: "granted", registered: true })).toBe("none");
    });
    it("granted and no row for this member: Turn on", () => {
      expect(show({ permission: "granted", registered: false })).toBe("turn-on");
    });
    it("denied: the settings steps, once", () => {
      expect(show({ permission: "denied" })).toBe("denied");
    });
    it("denied and seen: never again while it stays denied", () => {
      expect(show({ permission: "denied", state: { denied_seen_at: ago(1000) } })).toBe("none");
      expect(show({ permission: "denied", state: { denied_seen_at: ago(40 * SHOWN_GAP_MS) } })).toBe("none");
    });
    it("denied once, then granted and not registered: Turn on", () => {
      expect(show({ permission: "granted", state: { denied_seen_at: ago(1000) } })).toBe("turn-on");
    });
    it("no Notification API: nothing", () => {
      expect(show({ permission: "unsupported" })).toBe("none");
    });
  });

  describe("the chat", () => {
    it("an empty chat shows nothing, so the interview offer stands alone", () => {
      expect(show({ messageCount: 0 })).toBe("none");
    });
    it("one message is enough", () => {
      expect(show({ messageCount: 1 })).toBe("turn-on");
    });
    it("a run that streams shows nothing", () => {
      expect(show({ streaming: true })).toBe("none");
    });
  });

  describe("the stored state", () => {
    it("never: nothing", () => {
      expect(show({ state: { never: true } })).toBe("none");
    });
    it("a snooze in force: nothing", () => {
      expect(show({ state: { snoozed_until: ahead(1000) } })).toBe("none");
    });
    it("a snooze that ended: the sheet", () => {
      expect(show({ state: { snoozed_until: ago(1000) } })).toBe("turn-on");
    });
    it("shown in the last 24 hours: nothing", () => {
      expect(show({ state: { last_shown_at: ago(SHOWN_GAP_MS - 1000) } })).toBe("none");
    });
    it("shown more than 24 hours ago: the sheet", () => {
      expect(show({ state: { last_shown_at: ago(SHOWN_GAP_MS + 1000) } })).toBe("turn-on");
    });
    it("a time that does not parse holds nothing back", () => {
      expect(show({ state: { snoozed_until: "soon", last_shown_at: "x" } })).toBe("turn-on");
    });
    it("the 24-hour rule and a snooze hold the ios-tab sheet too", () => {
      expect(show({ support: "ios-tab", state: { last_shown_at: ago(1000) } })).toBe("none");
      expect(show({ support: "ios-tab", state: { never: true } })).toBe("none");
    });
  });
});

describe("the state helpers", () => {
  it("reads only the four known fields", () => {
    expect(promptStateOf({ push_prompt: { never: true, snoozed_until: "a", evil: 1, last_shown_at: 5 } })).toEqual({
      never: true,
      snoozed_until: "a",
    });
    expect(promptStateOf(null)).toEqual({});
    expect(promptStateOf({ push_prompt: [] })).toEqual({});
    expect(promptStateOf({ push_prompt: { never: "yes" } })).toEqual({});
  });
  it("a merge keeps the stored fields", () => {
    expect(mergePromptState({ last_shown_at: "a", never: true }, { snoozed_until: "b" })).toEqual({
      last_shown_at: "a",
      never: true,
      snoozed_until: "b",
    });
  });
  it("a snooze is seven days", () => {
    expect(Date.parse(snoozeUntil(NOW)) - NOW.getTime()).toBe(SNOOZE_MS);
    expect(SNOOZE_MS).toBe(7 * 24 * 3600 * 1000);
  });
});
