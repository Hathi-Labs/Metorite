// The Assistant pane flag is a pure function of its value (WS-63 PZ-7).
import { afterEach, describe, expect, it, vi } from "vitest";
import { assistantProfileFlagValue, assistantProfileOn } from "./assistantProfileFlag";

describe("assistantProfileFlagValue", () => {
  it("is on only for the string 1", () => {
    expect(assistantProfileFlagValue("1", null)).toBe(true);
    expect(assistantProfileFlagValue(undefined, null)).toBe(false);
    expect(assistantProfileFlagValue("0", null)).toBe(false);
    expect(assistantProfileFlagValue("true", null)).toBe(false);
  });
  it("takes the dev override only when it is 1", () => {
    expect(assistantProfileFlagValue(undefined, "1")).toBe(true);
    expect(assistantProfileFlagValue(undefined, "0")).toBe(false);
  });
});

describe("assistantProfileOn", () => {
  afterEach(() => vi.unstubAllEnvs());
  it("is off with no env value", () => {
    vi.stubEnv("NEXT_PUBLIC_ASSISTANT_PROFILE", "");
    expect(assistantProfileOn()).toBe(false);
  });
  it("is on with the env value 1", () => {
    vi.stubEnv("NEXT_PUBLIC_ASSISTANT_PROFILE", "1");
    expect(assistantProfileOn()).toBe(true);
  });
});
